import logging
import re

from slack_bolt.context.ack.async_ack import AsyncAck
from slack_sdk.errors import SlackApiError
from slack_sdk.web.async_client import AsyncWebClient

from nephthys.tasks.update_helpers import update_helpers
from nephthys.utils.env import env

# Matches "add me to the contd slack channel" and variations once normalised.
JOIN_REQUEST = re.compile(r"\badd me\b.*\bcont(?:d|inued)\b")
# "don't add me", "please do not ever add me", "i dont want you to add me", but
# not "why don't you add me" or "not sure but add me"
NEGATED = re.compile(
    r"(?:\b(?<!why )dont|\bdo not|\bnever|\bstop|\bshouldnt)\b"
    r"(?:\s+(?!but\b)\w+){0,3}\s+add me\b"
)


def is_join_request(text: str) -> bool:
    # "cont.d" and "cont'd" -> "contd", then punctuation -> spaces
    text = re.sub(r"(?<=\w)[.'’](?=\w)", "", text.lower())
    text = " ".join(re.sub(r"[^a-z0-9]+", " ", text).split())
    return bool(JOIN_REQUEST.search(text)) and not NEGATED.search(text)


async def invite_to_community_channels(
    client: AsyncWebClient, user_id: str, skip: str | None = None
):
    for target in env.slack_community_channels:
        if target == skip:
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


async def join_request(event: dict, client: AsyncWebClient) -> bool:
    """Invite the sender to every community channel if they asked to be added.

    Returns True if the message was a join request.
    """
    if event.get("subtype") not in {None, "file_share", "thread_broadcast"}:
        return False
    user_id = event.get("user")
    if not user_id or event.get("bot_id") or not env.slack_community_channels:
        return False
    if not is_join_request(event.get("text", "")):
        return False

    await invite_to_community_channels(client, user_id)
    try:
        await client.reactions_add(
            channel=event["channel"], timestamp=event["ts"], name="white_check_mark"
        )
    except SlackApiError as error:
        if error.response.get("error") != "already_reacted":
            logging.error(
                "Could not react to join request: %s", error.response.get("error")
            )
    return True


async def channel_join(
    ack: AsyncAck,
    event: dict,
    client: AsyncWebClient,
    bot_user_id: str | None = None,
):
    await ack()
    channel_id = event["channel"]

    user_id = event["user"]
    # Invites from this sync generate join events too; don't start another sync.
    from_bot = bot_user_id and (
        user_id == bot_user_id or event.get("inviter") == bot_user_id
    )
    if channel_id in env.slack_community_channels and not from_bot:
        await invite_to_community_channels(client, user_id, skip=channel_id)

    if channel_id in [env.slack_bts_channel, env.slack_ticket_channel]:
        await update_helpers()
