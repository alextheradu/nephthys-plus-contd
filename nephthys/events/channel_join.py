import logging

from slack_bolt.context.ack.async_ack import AsyncAck
from slack_sdk.errors import SlackApiError
from slack_sdk.web.async_client import AsyncWebClient

from nephthys.tasks.update_helpers import update_helpers
from nephthys.utils.env import env


async def channel_join(
    ack: AsyncAck,
    event: dict,
    client: AsyncWebClient,
    bot_user_id: str | None = None,
):
    await ack()
    channel_id = event["channel"]

    channels = env.slack_community_channels
    user_id = event["user"]
    # Invites from this sync generate join events too; don't start another sync.
    from_bot = bot_user_id and (
        user_id == bot_user_id or event.get("inviter") == bot_user_id
    )
    if channel_id in channels and not from_bot:
        for target in channels:
            if target == channel_id:
                continue
            try:
                await client.conversations_invite(channel=target, users=user_id)
            except SlackApiError as error:
                if error.response.get("error") not in {
                    "already_in_channel",
                    "cant_invite_self",
                }:
                    logging.error(
                        "Could not invite %s to community channel %s: %s",
                        user_id,
                        target,
                        error.response.get("error"),
                    )
            except Exception:
                logging.exception(
                    "Could not invite %s to community channel %s", user_id, target
                )

    if channel_id in [env.slack_bts_channel, env.slack_ticket_channel]:
        await update_helpers()
