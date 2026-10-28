# Authenticated, bounded downloads of Slack-hosted files.
import logging
from typing import Any
from urllib.parse import urljoin
from urllib.parse import urlparse

from aiohttp import ClientError
from aiohttp import ClientTimeout
from slack_sdk.errors import SlackApiError
from slack_sdk.web.async_client import AsyncWebClient

from nephthys.utils.env import env


def _tokens() -> tuple[str, ...]:
    return tuple(
        dict.fromkeys(
            token
            for token in (env.slack_bot_token, env.slack_user_token)
            if token and token != "unset"
        )
    )


def _slack_url(url: str) -> bool:
    try:
        parsed = urlparse(url)
        host = parsed.hostname or ""
        return (
            parsed.scheme == "https"
            and (host == "slack.com" or host.endswith(".slack.com"))
            and parsed.port in (None, 443)
            and parsed.username is None
            and parsed.password is None
        )
    except ValueError:
        return False


async def slack_file_info(file: dict[str, Any]) -> dict[str, Any]:
    """Hydrate partial file events, including Slack Connect's check_file_info payloads."""
    if not file.get("id"):
        return file
    for token in _tokens():
        try:
            info = await AsyncWebClient(token=token, timeout=10).files_info(
                file=file["id"]
            )
            metadata = info.get("file") or {}
            if metadata.get("file_access") == "check_file_info" or not any(
                metadata.get(key)
                for key in (
                    "mimetype",
                    "url_private_download",
                    "url_private",
                    "content",
                )
            ):
                continue
            return {**file, **metadata}
        except (SlackApiError, ClientError, TimeoutError):
            continue
    return file


async def _fetch(url: str, token: str, limit: int, allow_html: bool) -> bytes:
    for _ in range(5):
        # Remote files and redirect targets can be third-party URLs. Never send
        # a Slack bearer token outside Slack's HTTPS hosts.
        if not _slack_url(url):
            raise ValueError("not a Slack HTTPS download URL")
        if urlparse(url).path.startswith(("/signin", "/sign_in", "/ssb/redirect")):
            raise ValueError("Slack returned a login page")
        async with env.session.get(
            url,
            headers={"Authorization": f"Bearer {token}"},
            allow_redirects=False,
            timeout=ClientTimeout(total=20),
        ) as response:
            if response.status in (301, 302, 303, 307, 308):
                location = response.headers.get("Location")
                if not location:
                    raise ValueError("download redirect has no location")
                url = urljoin(url, location)
                continue
            if response.status != 200:
                raise ValueError(f"download HTTP {response.status}")
            if response.content_type == "text/html" and not allow_html:
                raise ValueError(
                    "Slack returned HTML instead of the file (check files:read and channel access)"
                )
            if response.content_length is not None and response.content_length > limit:
                raise ValueError("file exceeds size limit")
            data = bytearray()
            async for chunk in response.content.iter_chunked(64 * 1024):
                data.extend(chunk)
                if len(data) > limit:
                    raise ValueError("file exceeds size limit")
            if response.content_type == "text/html":
                # Canvas HTML is valid, but Slack's sign-in document is not.
                html = bytes(data[: 64 * 1024]).lower()
                if any(
                    marker in html
                    for marker in (
                        b'id="signin_form"',
                        b'id="signin_flow"',
                        b"<title>sign in | slack</title>",
                        b"<title>sign in to slack</title>",
                    )
                ):
                    raise ValueError("Slack returned a login page")
            return bytes(data)
    raise ValueError("too many download redirects")


async def download_slack_file(
    file: dict[str, Any], limit: int, *, allow_html: bool = False
) -> bytes | None:
    """Refresh private URLs and try bot/user credentials independently.

    Slack can return a 200 login page for inaccessible downloads. That is a
    failed attempt, not file content; another authorized token may still work.
    """
    if file.get("is_external") or file.get("mode") == "external":
        return None
    failures = []
    for index, token in enumerate(_tokens()):
        metadata = file
        try:
            if file.get("id"):
                info = await AsyncWebClient(token=token, timeout=10).files_info(
                    file=file["id"]
                )
                metadata = {**file, **(info.get("file") or {})}
            if metadata.get("is_external") or metadata.get("mode") == "external":
                return None
            urls = dict.fromkeys(
                filter(
                    None,
                    (
                        metadata.get("url_private_download"),
                        metadata.get("url_private"),
                    ),
                )
            )
            for url in urls:
                try:
                    return await _fetch(url, token, limit, allow_html)
                except (ClientError, TimeoutError, ValueError) as error:
                    failures.append(
                        f"credential {index + 1}: {type(error).__name__}: {error}"
                    )
        except SlackApiError as error:
            failures.append(f"credential {index + 1}: {error.response.get('error')}")
        except (ClientError, TimeoutError) as error:
            failures.append(f"credential {index + 1}: {type(error).__name__}")
    logging.warning(
        "Could not download Slack file file_id=%s: %s. Ensure files:read is granted, reinstall the app after scope changes, and check channel access.",
        file.get("id"),
        "; ".join(failures) or "no download URL or credentials",
    )
    return None
