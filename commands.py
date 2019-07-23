import logging
from textwrap import dedent

from markdown import markdown
from opsdroid.constraints import constrain_connectors
from opsdroid.matchers import match_regex
from opsdroid.events import (JoinRoom, Message, NewRoom, OpsdroidStarted,
                             RoomDescription, UserInvite)

from .constraints import ignore_appservice_users, admin_command

_LOGGER = logging.getLogger(__name__)


class PicardCommands:
    @match_regex("!help")
    @ignore_appservice_users
    async def on_help(self, message):
        help_text = dedent("""\
        Valid Commands are:

        * !createroom (name) "(topic)"

            Create a new room.

        Matrix user commands:

        * !inviteall

            Get invites to all matrix rooms.

        * !autoinvite [disable]

            Enable or disable invites to all new matrix rooms.

        """)

        if message.connector is self.matrix_connector:
            help_text = markdown(help_text)

        return await message.respond(help_text)

    @match_regex("!inviteall")
    @constrain_connectors("matrix")
    @ignore_appservice_users
    async def on_invite_all(self, message):
        rooms = await self.get_all_community_rooms()
        for r in rooms:
            await message.respond(UserInvite(user=message.raw_event['sender'],
                                             target=r,
                                             connector=self.matrix_connector))

    @match_regex("!autoinvite")
    @constrain_connectors("matrix")
    @ignore_appservice_users
    async def on_auto_invite(self, message):
        sender = message.raw_event['sender']
        users = await self.opsdroid.memory.get("autoinvite_users") or []
        if sender in users:
            return await message.respond("You already have autoinvite enabled.")
        users.append(sender)
        await self.opsdroid.memory.put("autoinvite_users", users)

        return await message.respond(
            "You will be invited to all future rooms. Use !inviteall to get invites to existing rooms.")

    @match_regex("!autoinvite disable")
    @constrain_connectors("matrix")
    @ignore_appservice_users
    async def on_disable_auto_invite(self, message):
        sender = message.raw_event['sender']
        users = await self.opsdroid.memory.get("autoinvite_users") or []
        if sender not in users:
            return await message.respond("You do not have autoinvite enabled.")
        users.remove(sender)
        await self.opsdroid.memory.put("autoinvite_users", users)

        return await message.respond("Autoinvite disabled.")

    @match_regex('!createroom (?P<name>.+?) "(?P<topic>.+?)"')
    @ignore_appservice_users
    async def on_create_room_command(self, message):
        # TODO: Ignore duplicates here, if a slack user sends this message in a
        # bridged room, we react to both the original slack message and the
        # matrix message.
        async with self._slack_channel_lock:
            await message.respond('Creating room please wait...')

            name, topic = (message.regex['name'],
                           message.regex['topic'])

            is_public = self.config.get("make_public", False)
            matrix_room_id = await self.create_new_matrix_room()

            await self.configure_new_matrix_room_pre_bridge(matrix_room_id, is_public)

            # Create the corresponding slack channel
            slack_channel_id = await self.create_slack_channel(name)

            # Link the two rooms
            await self.link_room(matrix_room_id, slack_channel_id)

            # Setup the matrix room
            matrix_room_alias = await self.configure_new_matrix_room_post_bridge(
                matrix_room_id, name, topic)

            # Set the description of the slack channel
            await self.set_slack_channel_description(slack_channel_id, topic)

            # Invite Command User
            if message.connector is self.matrix_connector:
                user = message.raw_event['sender']
                target = matrix_room_id
                command_room = message.target

                await self.opsdroid.send(UserInvite(target=target,
                                                    user=user,
                                                    connector=message.connector))

            elif message.connector is self.slack_connector:
                user = message.raw_event['user']
                target = slack_channel_id
                command_room = await self.matrix_room_id_from_slack_channel_name(message.target)

                await self.invite_user_to_slack_channel(slack_channel_id, user)

            # Inform users about the new room/channel
            pill = f'<a href="https://matrix.to/#/{matrix_room_alias}">{matrix_room_alias}</a>'
            await self.opsdroid.send(Message(f"Created a new room: {pill}",
                                             target=command_room,
                                             connector=self.matrix_connector))

            await self.announce_new_room(matrix_room_id, slack_channel_id)

            return matrix_room_id