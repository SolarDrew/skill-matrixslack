from matrix_client.errors import MatrixRequestError

from opsdroid.events import *
from opsdroid.connector.matrix.events import *


class MatrixMixin:
    """
    Matrix Operations for Picard.
    """

    @property
    def matrix_api(self):
        return self.matrix_connector.connection

    async def room_id_if_exists(self, room_alias):
        """
        Returns the room id if the room exists or `None` if it doesn't.
        """
        print(room_alias)
        if room_alias.startswith('!'):
            return room_alias
        try:
            room_id = await self.matrix_api.get_room_id(room_alias)
            return room_id
        except MatrixRequestError as e:
            if e.code != 404:
                raise e
        return None

    async def is_in_matrix_room(self, matrix_room_id):
        """
        Is the bot user in the matrix room.
        """
        respjson = await self.matrix_api._send("GET", "/joined_rooms")
        joined_rooms = respjson['joined_rooms']

        return matrix_room_id in joined_rooms

    async def join_or_create_matrix_room(self, matrix_room_alias):

        matrix_room_id = await self.room_id_if_exists(matrix_room_alias)

        if matrix_room_id is None:
            matrix_room_id = await self.create_new_matrix_channel()

        is_in_room = await self.is_in_matrix_room(matrix_room_id)

        if not is_in_room:
            opsdroid.send(JoinRoom(matrix_room_id,
                                    connector=self.matrix_connector))

        return matrix_room_id

    async def create_new_matrix_channel(self):
        """
        Create a new matrix channel with defaults from config.
        """
        # Create Room
        matrix_room_id = await self.opsdroid.send(NewRoom())

        return matrix_room_id

    async def configure_new_matrix_room_pre_bridge(self, matrix_room_id, is_public):
        if is_public:
            await self.opsdroid.send(MatrixJoinRules("public",
                                                     target=matrix_room_id,
                                                     connector=self.matrix_connector))
            await self.opsdroid.send(MatrixHistoryVisibility("world_readable",
                                                             target=matrix_room_id,
                                                             connector=self.matrix_connector))

    async def configure_new_matrix_room_post_bridge(self, matrix_room_id, name, topic):
        """
        Given Picard's config, setup the matrix side as appropriate.
        """
        # Set Aliases
        room_alias_templates = self.config.get('room_alias_template')
        if room_alias_templates:
            for alias_template in room_alias_templates:
                await self.opsdroid.send(RoomAddress(target=matrix_room_id,
                                                     address=alias_template.format(name=name),
                                                     connector=self.matrix_connector))

        # Set Room Name
        room_name_template = self.config.get('room_name_template')
        if room_name_template:
            await self.opsdroid.send(RoomName(target=matrix_room_id,
                                              name=room_name_template.format(name=name),
                                              connector=self.matrix_connector))

        # Set Room Image
        url = self.config.get("room_avatar_url")
        if url:
            await self.opsdroid.send(RoomImage(Image(url=url),
                                               target=matrix_room_id,
                                               connector=self.matrix_connector))

        # Set Room Description
        await self.opsdroid.send(RoomDescription(topic, target=matrix_room_id,
                                                 connector=self.matrix_connector))

        # Add to community
        # Enable flairs

        invite_users = (self.config.get("users_to_invite", []) +
                        self.config.get('users_as_admin', []))

        await self.invite_to_matrix_room(matrix_room_id, invite_users)

        await self.make_matrix_admin_from_config(matrix_room_id)

        if self.config.get("allow_at_room", False):
            await self.matrix_atroom_pl_0(matrix_room_id)

    async def invite_to_matrix_room(self, matrix_room_id, users):
        """
        Invite the listed users to the room.
        """
        for user in users:
            await self.opsdroid.send(UserInvite(target=matrix_room_id,
                                                user=user,
                                                connector=self.matrix_connector))

    async def make_matrix_admin_from_config(self, matrix_room_id):
        """
        Read the configuration file and make people in the 'users_as_admin'
        list admin in the room.
        """
        # Make config people admin
        for user in self.config.get("users_as_admin", []):
            await self.opsdroid.send(UserRole(target=matrix_room_id,
                                              user=user, role='admin',
                                              connector=self.matrix_connector))

    async def matrix_atroom_pl_0(self, matrix_room_id):
        power_levels = await self.matrix_api.get_power_levels(matrix_room_id)

        notifications = power_levels.get('notifications', {})
        notifications['room'] = 0
        power_levels['notifications'] = notifications

        return await self.opsdroid.send(MatrixPowerLevels(power_levels,
                                                          target=matrix_room_id,
                                                          connector=self.matrix_connector))

    async def archive_matrix_room(self, matrix_room_id):
        # Make sure the room isn't already archived first
        info = await self.matrix_api.get_state_event(matrix_room_id, 'm.picard.info')
        is_archived = info.get('is_archived')
        if is_archived == 'true':
            return

        # Change default speak power level so folks can't chat
        power_levels = await self.matrix_api.get_power_levels(matrix_room_id)
        power_levels['events_default'] = 50

        await self.opsdroid.send(MatrixPowerLevels(power_levels,
                                                   target=matrix_room_id,
                                                   connector=self.matrix_connector))

        # Edit name of room so we know it's archived
        old_name = await self.matrix_api.get_room_name(matrix_room_id)
        new_name = '[Archived] ' + old_name
        await self.opsdroid.send(RoomName(target=matrix_room_id,
                                          name=new_name,
                                          connector=self.matrix_connector))

        # Send an event so Picard knows it's archived
        return await opsdroid.send(MatrixStateEvent(key='m.picard.info',
                                                    content={'is_archived': 'true'}))