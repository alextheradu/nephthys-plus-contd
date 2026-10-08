import asyncio
import ipaddress
import json
import logging
import re
from typing import Any
from typing import cast
from urllib.parse import urljoin
from urllib.parse import urlparse

import aiohttp
from openai import omit
from openai import OpenAIError
from openai.types.chat import ChatCompletionMessageParam
from openai.types.chat import ChatCompletionToolParam
from slack_sdk.errors import SlackApiError
from slack_sdk.web.async_client import AsyncWebClient

from nephthys.utils.ai import ai_client
from nephthys.utils.env import env

WORKSPACE_URL = "https://hackclub.slack.com"
MAX_TOOL_ROUNDS = 8
MAX_OUTPUT_TOKENS = 4000

HELP_CHANNELS = """\
General Hack Club questions / not sure where to ask: <#C07TM4C0AQ5>
Identity (ID verification, Hack Club Auth): <#C092833JXKK>
YSWS organizing (organizing or drafting events): <#C0A4GF56ZRD>
Hackatime (tracking coding time): <#C0AFG0XGGMP> (for fraud see Fraud Department)
HCB: <#CN523HLKW> (for grant support use your event's own help channel)
Nest (Hack Club run server): <#C097AL5AUH0>
Lapse (tracking time by recording your screen): <#C09NVLWU61E>
Hack Club AI (free AI/LLM models): <#C0BDLT68ENN>
Hackathon organization help (independent hackathons): <#C03QSGGCJN7>
Clubs (running your own clubs): <#C02PA5G01ND>
Hack Club CDN (file hosting): DM @nora
Fire Department (moderation): to report content message {{U07K4TS9HQE|fire department}}; general questions <#C0707TCFG7J>
Hack Club Minecraft server: <#CD1JSG9UK> (moderation concerns: ping the @minecraft-admins group)
Code related questions: <#C0EA9S0A0>
Hack Club Spaces (online IDE): <#C08D0UDBSLX>
Fraud Department (investigates Hackatime activity): if your account was flagged see https://fraud.land and message {{U091HC53CE8|fraud squad}}
YSWS / event help channels:
Resolution <#C0A80KVN6MA>, Sleepover <#C0A9UNRF96V>, Enclosure <#C0AQR65RE02>, Trailit <#C0AGG8J6PLL>, Fallout <#C0ACJ290090>, Remixed <#C0AK7L0B9A6>, Hackcraft <#C07NQ5QAYNQ>, Stasis <#C09JP51FHNE>, Coeur <#C0A6MCHFFEU>, Boot <#C09EWDU9ZQT>, Hack Club: The Game <#C0A9XULS1SL>, Boba Drops <#C06UJR8QW0M>, Horizons <#C0AFLAUT58A>, Sprig <#C02UN35M7LG>, Construct <#C09QSTUV88Y>, Flavortown <#C09MATKQM8C>, Campfire Flagship <#C0A6KLGRZQE>, Stardance Challenge <#C0AP0NMSP3P>, Alchemize <#C0ASVK0HHEX>"""

SYSTEM_PROMPT = """You are a friendly, concise assistant answering a question from a Hack Club member in a help-ticket thread.

Rules:
1. Keep the answer short and direct (about 4 sentences max). A little warmth is fine, no fluff.
2. Search first. Call search_messages (refine the query at least once if results are noisy) before answering. Do NOT answer from general knowledge; base everything on what the tools return.
3. Cite sources. For messages, name the author and channel and link the message: "as noted by <author> in <channel> (<{permalink}|view message>)". Copy the "author", "channel" and "permalink" fields from the result EXACTLY as given. For bookmarks, canvases and web pages cite the title and exact URL.
4. If nothing relevant is found, say you couldn't find it and a helper will follow up.
5. Mention channels ONLY as the proper channel token <#CHANNEL_ID>, copied exactly from the lists or tool results (never the plain channel name, never "#name"). Mention users ONLY as {{USER_ID|name}}, copied exactly as you saw them (never use <@...>).
6. If a more specific help channel fits the question, end with "Next time, ask in <#CHANNEL_ID>" using the list below. Only suggest channels from this list or from search results.
7. Never give instructions for fraud, abuse, or anything harmful.

Help channels:
{HELP_CHANNELS}

Helper macros (canned replies helpers send; these are configured for THIS program only, and some like ?shipwrights apply only to programs with project shipping or certification, so only reference a macro when it fits the question):
{MACROS}
"""

TOOLS: list[ChatCompletionToolParam] = [
    {
        "type": "function",
        "function": {
            "name": "search_messages",
            "description": "Search public Slack channels and threads by relevance. Supports operators like in:#channel, from:@handle, has:link, before:/after:YYYY-MM-DD.",
            "parameters": {
                "type": "object",
                "properties": {"query": {"type": "string"}},
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_channel_bookmarks",
            "description": "List the bookmarks (tabs) of a channel, e.g. its FAQ links.",
            "parameters": {
                "type": "object",
                "properties": {"channel_id": {"type": "string"}},
                "required": ["channel_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "read_canvas",
            "description": "Read a Slack canvas (e.g. an FAQ) by file ID.",
            "parameters": {
                "type": "object",
                "properties": {"file_id": {"type": "string"}},
                "required": ["file_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "read_web_page",
            "description": "Fetch the text of a public web page (e.g. an FAQ or docs site).",
            "parameters": {
                "type": "object",
                "properties": {"url": {"type": "string"}},
                "required": ["url"],
            },
        },
    },
]

USER_TOKEN = r"\{\{([UW][A-Z0-9]{8,11})(?:\|([^}]*))?\}\}"
_user_cache: dict[str, tuple[str, bool]] = {}


def _strip_html(raw: str, limit: int) -> str:
    raw = re.sub(r"<(script|style)[^>]*>[\s\S]*?</\1>", "", raw, flags=re.IGNORECASE)
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", raw)).strip()[:limit]


async def _is_public_host(host: str) -> bool:
    try:
        infos = await asyncio.get_running_loop().getaddrinfo(host, None)
        return all(ipaddress.ip_address(i[4][0]).is_global for i in infos)
    except (OSError, ValueError):
        return False


async def _fetch_text(url: str, headers: dict[str, str] | None = None) -> str:
    for _ in range(4):
        parsed = urlparse(url)
        if parsed.scheme not in ("http", "https") or not parsed.hostname:
            return "Error: only http(s) URLs are supported"
        if not await _is_public_host(parsed.hostname):
            return "Error: refusing to fetch a non-public host"
        async with env.session.get(
            url,
            headers=headers,
            allow_redirects=False,
            timeout=aiohttp.ClientTimeout(total=10),
        ) as res:
            if res.status in (301, 302, 303, 307, 308) and res.headers.get("Location"):
                url = urljoin(url, res.headers["Location"])
                continue
            if res.status != 200:
                return f"Error fetching page: status {res.status}"
            return _strip_html(await res.text(errors="ignore"), 12000)
    return "Error: too many redirects"


class _Run:
    def __init__(self):
        self.channels: dict[str, str] = {}
        self.user_client = AsyncWebClient(token=env.slack_user_token)

    async def call_tool(self, name: str, args: dict[str, Any]) -> Any:
        try:
            if name == "read_web_page":
                return {"content": await _fetch_text(args["url"])}
            if name == "search_messages":
                return await self.search_messages(args["query"])
            if name == "list_channel_bookmarks":
                res = await self.user_client.bookmarks_list(
                    channel_id=args["channel_id"]
                )
                return [
                    {"title": b.get("title"), "link": b.get("link")}
                    for b in res.get("bookmarks", [])
                ]
            if name == "read_canvas":
                info = await self.user_client.files_info(file=args["file_id"])
                f = info.get("file", {})
                url = f.get("url_private_download") or f.get("url_private")
                text = ""
                if url:
                    text = await _fetch_text(
                        url, {"Authorization": f"Bearer {env.slack_user_token}"}
                    )
                return {
                    "title": f.get("title"),
                    "content": text or str(f.get("content", ""))[:12000],
                }
        except (
            SlackApiError,
            aiohttp.ClientError,
            TimeoutError,
            KeyError,
        ) as e:
            logging.warning(f"AI help tool {name} failed: {e!r}")
            return {"error": f"{name} failed: {e}"}
        return {"error": f"unknown tool {name}"}

    async def search_messages(self, query: str) -> Any:
        res = await self.user_client.search_messages(
            query=query, count=20, sort="score"
        )
        results = []
        for m in res.get("messages", {}).get("matches", []):
            ch = m.get("channel") or {}
            if (
                not ch.get("is_channel")
                or ch.get("is_private")
                or ch.get("is_mpim")
                or ch.get("is_im")
            ):
                continue
            if ch.get("name") and ch.get("id"):
                self.channels[ch["name"]] = ch["id"]
            author = (
                f"{{{{{m['user']}|{m.get('username') or 'user'}}}}}"
                if m.get("user")
                else m.get("username", "unknown")
            )
            results.append(
                {
                    "author": author,
                    "channel": f"<#{ch['id']}>",
                    "text": (m.get("text") or "")[:700],
                    "permalink": m.get("permalink"),
                }
            )
        return results or {"info": "No public messages found for that query."}

    async def render(self, text: str) -> str:
        text = re.sub(r"\*\*(.+?)\*\*", r"*\1*", text)
        text = re.sub(
            r"\[([^\]]+)\]\((https?://[^)\s]+)\)",
            lambda m: f"<{m[2].replace('&', '&amp;')}|{m[1]}>",
            text,
        )

        text = re.sub(r"<@([UW][A-Z0-9]{8,11})(?:\|([^>]*))?>", r"{{\1|\2}}", text)
        text = re.sub(r"(?<![{\w])([UW][A-Z0-9]{8,11})\|([\w.\-]+)", r"{{\1|\2}}", text)
        text = re.sub(r"<@([^>|]+)>", r"@\1", text)

        for uid in {m[1] for m in re.finditer(USER_TOKEN, text)}:
            await self._load_user(uid)

        def user_sub(m: re.Match) -> str:
            uid = m[1]
            name, is_bot = _user_cache.get(uid, (m[2] or "user", False))
            if is_bot:
                return f"<@{uid}>"
            return f"<{WORKSPACE_URL}/team/{uid}|@{name}>"

        text = re.sub(USER_TOKEN, user_sub, text)
        text = re.sub(
            r"<(https?://[^|>]+)",
            lambda m: "<" + re.sub(r"&(?!amp;)", "&amp;", m[1]),
            text,
        )
        return re.sub(
            r"(?<![\w<&/#])#([a-z0-9][a-z0-9_-]*)",
            lambda m: f"<#{self.channels[m[1]]}>" if m[1] in self.channels else m[0],
            text,
        )

    async def tokenize_mentions(self, text: str) -> str:
        for uid in {m[1] for m in re.finditer(r"<@([UW][A-Z0-9]+)(?:\|[^>]*)?>", text)}:
            await self._load_user(uid)
        return re.sub(
            r"<@([UW][A-Z0-9]+)(?:\|[^>]*)?>",
            lambda m: f"{{{{{m[1]}|{_user_cache.get(m[1], ('user', False))[0]}}}}}",
            text,
        )

    async def _load_user(self, uid: str):
        if uid in _user_cache:
            return
        try:
            u = (await env.slack_client.users_info(user=uid)).get("user") or {}
            p = u.get("profile", {})
            name = (
                p.get("display_name") or p.get("real_name") or u.get("name") or "user"
            )
            _user_cache[uid] = (name, bool(u.get("is_bot")))
        except SlackApiError as e:
            logging.warning(f"AI help couldn't look up user {uid}: {e!r}")


async def macro_knowledge() -> str:
    from nephthys.database.tables import Macro as MacroTable
    from nephthys.macros import macros
    from nephthys.macros.types import ReplyMacro

    entries = [("hackatime", env.transcript.hackatime_macro)]
    entries += [
        (m.name, m.message)
        for m in macros
        if isinstance(m, ReplyMacro) and getattr(m, "message", None)
    ]
    custom = await MacroTable.select(MacroTable.name, MacroTable.message).where(
        MacroTable.program == env.program
    )
    entries += [(m["name"], m["message"]) for m in custom]
    lines = [
        f"?{name}: {message.replace('(user)', 'the user')}"
        for name, message in entries
        if message
    ]
    return "\n".join(lines)[:6000] or "(none)"


async def answer(channel: str, thread_ts: str) -> str:
    if not ai_client:
        raise RuntimeError("AI is not configured")
    from nephthys.events.message_creation import AI_TOKENS

    replies = await env.slack_client.conversations_replies(
        channel=channel, ts=thread_ts
    )
    thread = "\n".join(
        f"{'<@' + m['user'] + '>' if m.get('user') else 'Bot'}: {m.get('text', '')}"
        for m in replies.get("messages", [])
    )
    run = _Run()
    thread = await run.tokenize_mentions(thread)
    client = ai_client
    messages: list[ChatCompletionMessageParam] = [
        {
            "role": "system",
            "content": SYSTEM_PROMPT.replace("{HELP_CHANNELS}", HELP_CHANNELS).replace(
                "{MACROS}", await macro_knowledge()
            ),
        },
        {
            "role": "user",
            "content": f"Ticket thread (the question is the first message):\n{thread}\n\nAnswer the question.",
        },
    ]
    model = env.ai_help_model

    async def complete(use_tools: bool):
        try:
            res = await client.chat.completions.create(
                model=model,
                messages=messages,
                max_completion_tokens=MAX_OUTPUT_TOKENS,
                reasoning_effort="low",
                tools=TOOLS if use_tools else omit,
            )
        except OpenAIError:
            logging.exception("AI help request failed")
            raise
        if res.usage:
            AI_TOKENS.labels(task="ai_help", type="input", model=model).inc(
                res.usage.prompt_tokens
            )
            AI_TOKENS.labels(task="ai_help", type="output", model=model).inc(
                res.usage.completion_tokens
            )
        return res.choices[0].message

    final = None
    for _ in range(MAX_TOOL_ROUNDS):
        msg = await complete(True)
        if not msg.tool_calls:
            final = msg.content
            break
        messages.append(
            cast(ChatCompletionMessageParam, msg.model_dump(exclude_none=True))
        )
        for call in msg.tool_calls:
            if call.type != "function":
                continue
            try:
                args = json.loads(call.function.arguments)
            except json.JSONDecodeError:
                args = {}
            result = await run.call_tool(call.function.name, args)
            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": call.id,
                    "content": json.dumps(result)[:20000],
                }
            )
    if not final:
        final = (await complete(False)).content
    if not final:
        return "I couldn't find an answer to this, a helper will follow up."
    return await run.render(final.strip())
