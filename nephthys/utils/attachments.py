import base64
from typing import Any

from nephthys.utils.slack_files import download_slack_file
from nephthys.utils.slack_files import slack_file_info

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


async def attachment_parts(files: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Turn a Slack message's files into content parts for a chat completion.

    Images, PDFs and text files are included; anything else (or anything too
    big, or past 20 MB in total) is skipped.
    """
    parts: list[dict[str, Any]] = []
    total = 0
    for index, file in enumerate(files, start=1):
        if not file.get("mimetype") or file.get("file_access") == "check_file_info":
            file = await slack_file_info(file)
        if file.get("mode") not in (None, "hosted", "snippet"):
            continue
        mimetype = file.get("mimetype") or ""
        limit = _size_limit(mimetype)
        size = file.get("size", 0)
        if limit is None or size > limit or total + size > MAX_TOTAL_BYTES:
            continue
        data = await download_slack_file(
            file,
            min(limit, MAX_TOTAL_BYTES - total),
            allow_html=file.get("mimetype") == "text/html",
        )
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
