import base64
import logging
from typing import Any

from aiohttp import ClientTimeout

from nephthys.utils.env import env

MAX_IMAGE_BYTES = 5 * 1024 * 1024
MAX_PDF_BYTES = 10 * 1024 * 1024
MAX_TEXT_BYTES = 200_000
MAX_TEXT_CHARS = 20_000
MAX_TOTAL_BYTES = 20 * 1024 * 1024

IMAGE_TYPES = {"image/png", "image/jpeg", "image/gif", "image/webp"}
PDF_TYPE = "application/pdf"
TEXT_TYPES = {"application/json", "application/xml", "application/x-yaml"}


def _is_text(mimetype: str) -> bool:
    return mimetype.startswith("text/") or mimetype in TEXT_TYPES


def _size_limit(mimetype: str) -> int | None:
    if mimetype in IMAGE_TYPES:
        return MAX_IMAGE_BYTES
    if mimetype == PDF_TYPE:
        return MAX_PDF_BYTES
    if _is_text(mimetype):
        return MAX_TEXT_BYTES
    return None


async def _download(file: dict[str, Any], limit: int) -> bytes | None:
    """Download a Slack file. The bot token has no files:read, so this uses the user token."""
    url = file.get("url_private_download") or file.get("url_private")
    if not url:
        return None
    async with env.session.get(
        url,
        headers={"Authorization": f"Bearer {env.slack_user_token}"},
        timeout=ClientTimeout(total=20),
    ) as res:
        # A bad token gets a 200 login page instead of the file
        if res.status != 200 or res.content_type == "text/html":
            logging.warning(
                f"Couldn't download attachment file_id={file.get('id')}: status={res.status} type={res.content_type}"
            )
            return None
        # read() only returns what's buffered, so it would truncate the file
        data = b""
        async for chunk in res.content.iter_chunked(64 * 1024):
            data += chunk
            if len(data) > limit:
                return None
    return data


async def attachment_parts(files: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Turn a Slack message's files into content parts for a chat completion.

    Images, PDFs and text files are included; anything else (or anything too
    big, or past 20 MB in total) is skipped.
    """
    parts: list[dict[str, Any]] = []
    total = 0
    for index, file in enumerate(files, start=1):
        if file.get("mode") not in (None, "hosted", "snippet"):
            continue
        mimetype = file.get("mimetype") or ""
        limit = _size_limit(mimetype)
        size = file.get("size", 0)
        if limit is None or size > limit or total + size > MAX_TOTAL_BYTES:
            continue
        data = await _download(file, limit)
        if data is None:
            continue
        total += len(data)
        label = {
            "type": "text",
            "text": f"[Attachment {index}: {file.get('name') or 'unnamed'} ({mimetype})]",
        }
        if mimetype in IMAGE_TYPES:
            encoded = base64.b64encode(data).decode()
            parts += [
                label,
                {
                    "type": "image_url",
                    "image_url": {"url": f"data:{mimetype};base64,{encoded}"},
                },
            ]
        elif mimetype == PDF_TYPE:
            encoded = base64.b64encode(data).decode()
            parts += [
                label,
                {
                    "type": "file",
                    "file": {
                        "filename": file.get("name") or "attachment.pdf",
                        "file_data": f"data:{mimetype};base64,{encoded}",
                    },
                },
            ]
        else:
            content = data.decode("utf-8", errors="replace")[:MAX_TEXT_CHARS]
            parts.append({"type": "text", "text": f"{label['text']}\n{content}"})
    return parts
