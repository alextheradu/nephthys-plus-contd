import asyncio
import logging
from typing import Any

from slack_sdk.errors import SlackApiError

from nephthys.utils.env import env

# Users we've already confirmed are full members (or just promoted), so we
# don't hit Slack or Flaron again for them. Resets on restart, which is fine.
_full_members: set[str] = set()
_in_flight: set[str] = set()
_tasks: set[asyncio.Task] = set()


async def _promote(user_id: str, channel: str, thread_ts: str):
    try:
        info = await env.slack_client.users_info(user=user_id)
        user: dict[str, Any] = info.get("user") or {}
        if user.get("is_bot") or not (
            user.get("is_restricted") or user.get("is_ultra_restricted")
        ):
            _full_members.add(user_id)
            return

        async with env.session.get(f"{env.flaron_url}/promote/{user_id}") as res:
            if res.status != 200:
                logging.error(
                    f"Failed to promote user_id={user_id}: {res.status} - {await res.text()}"
                )
                return
        _full_members.add(user_id)
        await env.slack_client.chat_postMessage(
            channel=channel,
            thread_ts=thread_ts,
            text=f"promoted <@{user_id}> from mcg to full slack member :yayayayayay:",
        )
    except SlackApiError as e:
        logging.warning(f"Slack error while promoting user_id={user_id}: {e}")
    except Exception:
        logging.exception(f"Failed to promote user_id={user_id}")
    finally:
        _in_flight.discard(user_id)


def maybe_promote(event: dict[str, Any]):
    """Fire-and-forget: promote the sender from MCG to full member if they aren't one yet."""
    user_id = event.get("user")
    if not user_id or user_id in _full_members or user_id in _in_flight:
        return
    _in_flight.add(user_id)
    task = asyncio.create_task(
        _promote(user_id, event["channel"], event.get("thread_ts") or event["ts"])
    )
    _tasks.add(task)
    task.add_done_callback(_tasks.discard)
