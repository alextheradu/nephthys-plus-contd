import logging

from slack_sdk.web.async_client import AsyncWebClient

from nephthys.utils import ai_help

DISCLAIMER = "_BETA: may be inaccurate, please check or wait for a helper to confirm._"


async def ask_ai(channel: str, thread_ts: str, user_id: str, client: AsyncWebClient):
    try:
        text = await ai_help.answer(channel, thread_ts)
    except Exception:
        logging.exception(f"AI help failed for thread_ts={thread_ts}")
        await client.chat_postEphemeral(
            channel=channel,
            user=user_id,
            thread_ts=thread_ts,
            text="Sorry, the AI couldn't answer that. A helper will be along soon!",
        )
        return

    await client.chat_postMessage(
        channel=channel,
        thread_ts=thread_ts,
        text=f"{text}\n\n{DISCLAIMER}",
        unfurl_links=False,
        unfurl_media=False,
    )
