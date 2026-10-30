import asyncio
import importlib
import os
import unittest
from unittest.mock import AsyncMock
from unittest.mock import Mock
from unittest.mock import patch

from aiohttp import ClientConnectionError
from slack_sdk.errors import SlackApiError
from slack_sdk.web.async_client import AsyncWebClient
from slack_sdk.web.async_slack_response import AsyncSlackResponse

# Import the real handlers with isolated test configuration. No database or
# Slack requests are made; those boundaries are mocked in each test.
TEST_ENV = {
    "SLACK_BOT_TOKEN": "xoxb-test",
    "SLACK_USER_TOKEN": "xoxp-test",
    "SLACK_SIGNING_SECRET": "test",
    "SLACK_HELP_CHANNEL": "CHELP",
    "SLACK_TICKET_CHANNEL": "CTICKETS",
    "SLACK_BTS_CHANNEL": "CTEAM",
    "SLACK_MAINTAINER_ID": "UOWNER",
    "BASE_URL": "https://bot.example.test",
    "DATABASE_URL": "postgresql://test:test@localhost/test",
    "PROGRAM": "contd",
}
with patch.dict(os.environ, TEST_ENV, clear=True):
    config = importlib.import_module("nephthys.utils.env")
    joins = importlib.import_module("nephthys.events.channel_join")
    home = importlib.import_module("nephthys.events.app_home_opened")
    files = importlib.import_module("nephthys.utils.slack_files")
    attachments = importlib.import_module("nephthys.utils.attachments")


def slack_error(code, headers=None):
    return SlackApiError(
        code,
        AsyncSlackResponse(
            client=AsyncWebClient(token="test"),
            http_verb="POST",
            api_url="https://slack.com/api/test",
            req_args={},
            data={"ok": False, "error": code},
            headers=headers or {},
            status_code=200,
        ),
    )


class CommunityChannelTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.client = AsyncMock(spec=AsyncWebClient)
        self.ack = AsyncMock()
        self.channels = ("CMAIN", "CHELP", "CNEWS")
        self.env_patch = patch.object(
            config.env, "slack_community_channels", self.channels
        )
        self.env_patch.start()
        self.addCleanup(self.env_patch.stop)
        self.helpers = AsyncMock()
        self.helper_patch = patch.object(joins, "update_helpers", self.helpers)
        self.helper_patch.start()
        self.addCleanup(self.helper_patch.stop)

    async def deliver(self, channel, **event):
        await joins.channel_join(
            self.ack, {"channel": channel, "user": "UNEW", **event}, self.client, "UBOT"
        )
        self.ack.assert_awaited()

    async def test_every_channel_invites_to_the_other_channels(self):
        for channel in self.channels:
            self.client.reset_mock()
            await self.deliver(channel)
            self.assertEqual(
                {
                    call.kwargs["channel"]
                    for call in self.client.conversations_invite.await_args_list
                },
                set(self.channels) - {channel},
            )
            for call in self.client.conversations_invite.await_args_list:
                self.assertEqual(call.kwargs["users"], "UNEW")
        self.helpers.assert_not_awaited()

    async def test_existing_members_and_replayed_events_are_safe(self):
        self.client.conversations_invite.side_effect = slack_error("already_in_channel")
        with patch.object(joins.logging, "error") as errors:
            await self.deliver("CMAIN")
            await self.deliver("CMAIN")
            errors.assert_not_called()
        self.assertEqual(self.client.conversations_invite.await_count, 4)

    async def test_one_failed_invitation_does_not_block_the_other(self):
        for error in (slack_error("missing_scope"), ClientConnectionError("offline")):
            self.client.reset_mock()
            self.client.conversations_invite.side_effect = [error, {"ok": True}]
            with self.assertLogs(level="ERROR"):
                await self.deliver("CMAIN")
            self.assertEqual(self.client.conversations_invite.await_count, 2)

    async def test_unrelated_and_bot_generated_joins_do_not_sync(self):
        await self.deliver("COTHER")
        await self.deliver("CMAIN", user="UBOT")
        await self.deliver("CMAIN", inviter="UBOT")
        self.client.conversations_invite.assert_not_awaited()

    def test_join_request_phrasing(self):
        for text in (
            "add me to the contd slack channel",
            "ADD ME TO THE CONT.D SLACK CHANNEL!!!",
            "hey, can you add me to cont'd channels pls?",
            "Add-me to <#CMAIN|contd>",
            "why don't you add me to contd",
            "not sure if this is right but add me to contd",
        ):
            self.assertTrue(joins.is_join_request(text), text)
        for text in (
            "add me to the slack channel",
            "what is contd?",
            "address me",
            "DON'T add me to the contd slack channel",
            "dont add me to contd",
            "please do not ever add me to cont.d",
            "i dont want you to add me to contd",
            "never add me to contd",
            "stop trying to add me to contd!!",
        ):
            self.assertFalse(joins.is_join_request(text), text)

    async def test_join_request_invites_to_every_channel(self):
        event = {
            "channel": "CRANDOM",
            "user": "UNEW",
            "ts": "1.0",
            "text": "Add me to the contd slack channel!",
        }
        self.assertTrue(await joins.join_request(event, self.client))
        self.assertEqual(
            {
                call.kwargs["channel"]
                for call in self.client.conversations_invite.await_args_list
            },
            set(self.channels),
        )
        self.client.reactions_add.assert_awaited_once()

    async def test_bot_joins_listen_and_community_channels(self):
        self.client.conversations_join.side_effect = [
            {"ok": True},
            slack_error("method_not_supported_for_channel_type"),
            {"ok": True},
            {"ok": True},
        ]
        with patch.object(config.env, "slack_listen_channels", ("CLOUNGE", "CMAIN")):
            with self.assertLogs(level="WARNING"):
                await joins.join_listen_channels(self.client)
        self.assertEqual(
            [
                call.kwargs["channel"]
                for call in self.client.conversations_join.await_args_list
            ],
            [*self.channels, "CLOUNGE"],
        )

    async def test_join_request_ignores_bots_and_other_messages(self):
        base = {"channel": "CRANDOM", "user": "UNEW", "ts": "1.0"}
        for event in (
            {**base, "text": "hello"},
            {**base, "text": "add me to contd", "bot_id": "B1"},
            {**base, "text": "add me to contd", "subtype": "message_changed"},
        ):
            self.assertFalse(await joins.join_request(event, self.client))
        self.client.conversations_invite.assert_not_awaited()

    async def test_helper_updates_are_preserved_even_with_sync_disabled(self):
        with patch.object(config.env, "slack_community_channels", ()):
            await self.deliver("CMAIN")
            await self.deliver("CTEAM")
            await self.deliver("CTICKETS")
        self.client.conversations_invite.assert_not_awaited()
        self.assertEqual(self.helpers.await_count, 2)

    def test_portable_channel_configuration(self):
        for raw, expected in [
            ("", ()),
            (" CMAIN, CHELP,,CMAIN, CNEWS ", self.channels),
        ]:
            with patch.dict(
                os.environ, {**TEST_ENV, "SLACK_COMMUNITY_CHANNELS": raw}, clear=True
            ):
                self.assertEqual(
                    config.Environment().slack_community_channels, expected
                )


class AppHomeTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        home.last_requested_views.clear()
        self.client = AsyncMock(spec=AsyncWebClient)
        self.sleep_patch = patch.object(home.asyncio, "sleep", new=AsyncMock())
        self.sleep = self.sleep_patch.start()
        self.addCleanup(self.sleep_patch.stop)

    async def test_transient_internal_error_retries_the_same_view(self):
        self.client.views_publish.side_effect = [
            slack_error("internal_error"),
            {"ok": True},
        ]
        view = {"type": "home", "blocks": []}
        self.assertTrue(await home.publish_view(self.client, "U1", view))
        self.assertEqual(self.client.views_publish.await_count, 2)
        self.assertEqual(
            self.client.views_publish.await_args_list[0],
            self.client.views_publish.await_args_list[1],
        )

    async def test_permanent_and_exhausted_errors_do_not_cascade(self):
        for code, attempts in [("invalid_blocks", 1), ("internal_error", 3)]:
            self.client.reset_mock()
            self.client.views_publish.side_effect = slack_error(code)
            with self.assertLogs(level="ERROR"):
                self.assertFalse(await home.publish_view(self.client, "U1", {}))
            self.assertEqual(self.client.views_publish.await_count, attempts)

    async def test_rate_limit_respects_retry_after(self):
        self.client.views_publish.side_effect = [
            slack_error("ratelimited", {"Retry-After": "2"}),
            {},
        ]
        self.assertTrue(await home.publish_view(self.client, "U1", {}))
        self.sleep.assert_awaited_once_with(2)

    async def test_old_request_cannot_publish_after_new_request_even_for_same_tab(self):
        old = home.RequestedView(home.AppHomeView.DASHBOARD)
        new = home.RequestedView(home.AppHomeView.DASHBOARD)
        home.last_requested_views["U1"] = new
        self.assertFalse(await home.publish_view(self.client, "U1", {}, old))
        self.client.views_publish.assert_not_awaited()

    async def test_render_race_only_publishes_latest_result(self):
        started, release = asyncio.Event(), asyncio.Event()
        calls = 0

        async def render(**kwargs):
            nonlocal calls
            calls += 1
            number = calls
            if number == 1:
                started.set()
                await release.wait()
            return {"type": "home", "blocks": [], "private_metadata": str(number)}

        user_model = Mock()
        user_model.objects.return_value.where.return_value.first = AsyncMock(
            return_value=None
        )
        with (
            patch.object(home, "User", user_model),
            patch.object(home, "get_dashboard_view", side_effect=render),
        ):
            first = asyncio.create_task(
                home.open_app_home(home.AppHomeView.DASHBOARD, self.client, "U1")
            )
            await started.wait()
            await home.open_app_home(home.AppHomeView.DASHBOARD, self.client, "U1")
            release.set()
            await first
        rendered = [
            call.kwargs["view"].get("private_metadata")
            for call in self.client.views_publish.await_args_list
        ]
        self.assertIn("2", rendered)
        self.assertNotIn("1", rendered)
        self.assertEqual(home.last_requested_views, {})

    async def test_messages_tab_does_not_render_home(self):
        await home.on_app_home_opened({"user": "U1", "tab": "messages"}, self.client)
        self.client.views_publish.assert_not_awaited()

    async def test_failed_loading_publish_does_not_stop_the_real_view(self):
        self.client.views_publish.side_effect = [slack_error("internal_error")] * 3 + [
            {}
        ]
        user_model = Mock()
        user_model.objects.return_value.where.return_value.first = AsyncMock(
            return_value=None
        )
        final = {"type": "home", "blocks": [], "private_metadata": "ready"}
        with (
            patch.object(home, "User", user_model),
            patch.object(home, "get_dashboard_view", new=AsyncMock(return_value=final)),
            self.assertLogs(level="ERROR"),
        ):
            await home.open_app_home(home.AppHomeView.DASHBOARD, self.client, "U1")
        self.assertEqual(self.client.views_publish.await_args.kwargs["view"], final)
        self.assertEqual(home.last_requested_views, {})

    async def test_failed_final_view_has_a_bounded_error_fallback(self):
        self.client.views_publish.side_effect = [{}, slack_error("invalid_blocks"), {}]
        user_model = Mock()
        user_model.objects.return_value.where.return_value.first = AsyncMock(
            return_value=None
        )
        with (
            patch.object(home, "User", user_model),
            patch.object(home, "get_dashboard_view", new=AsyncMock(return_value={})),
            self.assertLogs(level="ERROR"),
        ):
            await home.open_app_home(home.AppHomeView.DASHBOARD, self.client, "U1")
        self.assertEqual(self.client.views_publish.await_count, 3)
        self.assertIn(
            "try again", str(self.client.views_publish.await_args.kwargs["view"])
        )

    def test_loading_view_needs_no_external_image_and_error_text_fits_slack(self):
        view = home.get_loading_view(home.AppHomeView.DASHBOARD)
        self.assertFalse(any(block["type"] == "image" for block in view["blocks"]))
        view = home.get_error_view("x" * 5000, "trace" * 5000)
        for block in view["blocks"]:
            self.assertLessEqual(len(block["text"]["text"]), 3000)


class DownloadResponse:
    def __init__(
        self, chunks=(), *, status=200, content_type="image/png", headers=None
    ):
        self.chunks = chunks
        self.status = status
        self.content_type = content_type
        self.headers = headers or {}
        self.content_length = None
        self.content = self

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        pass

    async def iter_chunked(self, size):
        for chunk in self.chunks:
            yield chunk


class SlackDownloadTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.file = {
            "id": "F1",
            "mimetype": "image/png",
            "mode": "hosted",
            "name": "photo.png",
        }
        self.url = "https://files.slack.com/files-pri/T-F1/photo.png"
        self.session = Mock()
        self.clients = {
            token: AsyncMock(spec=AsyncWebClient)
            for token in ("xoxb-test", "xoxp-test")
        }
        for client in self.clients.values():
            client.files_info.return_value = {
                "file": {**self.file, "url_private_download": self.url}
            }
        self.session_patch = patch.object(
            config.env, "session", self.session, create=True
        )
        self.session_patch.start()
        self.addCleanup(self.session_patch.stop)
        self.client_patch = patch.object(
            files,
            "AsyncWebClient",
            side_effect=lambda token, timeout: self.clients[token],
        )
        self.client_patch.start()
        self.addCleanup(self.client_patch.stop)

    async def test_200_login_page_falls_back_to_other_token_and_reads_all_chunks(self):
        self.session.get.side_effect = [
            DownloadResponse(content_type="text/html"),
            DownloadResponse([b"one", b"two"]),
        ]
        self.assertEqual(await files.download_slack_file(self.file, 10), b"onetwo")
        tokens = [
            call.kwargs["headers"]["Authorization"]
            for call in self.session.get.call_args_list
        ]
        self.assertEqual(tokens, ["Bearer xoxb-test", "Bearer xoxp-test"])

    async def test_missing_scope_on_bot_uses_authorized_user(self):
        self.clients["xoxb-test"].files_info.side_effect = slack_error("missing_scope")
        self.session.get.return_value = DownloadResponse([b"image"])
        self.assertEqual(await files.download_slack_file(self.file, 10), b"image")
        self.assertEqual(
            self.session.get.call_args.kwargs["headers"]["Authorization"],
            "Bearer xoxp-test",
        )

    async def test_partial_bot_metadata_does_not_hide_full_user_metadata(self):
        self.clients["xoxb-test"].files_info.return_value = {
            "file": {"id": "F1", "file_access": "check_file_info"}
        }
        hydrated = await files.slack_file_info({"id": "F1"})
        self.assertEqual(hydrated["mimetype"], "image/png")
        self.clients["xoxp-test"].files_info.assert_awaited_once()

    async def test_redirects_keep_authorization_only_on_slack_hosts(self):
        self.session.get.side_effect = [
            DownloadResponse(status=302, headers={"Location": "/new.png"}),
            DownloadResponse([b"ok"]),
        ]
        self.assertEqual(await files.download_slack_file(self.file, 10), b"ok")
        self.assertTrue(
            all(
                call.kwargs["allow_redirects"] is False
                for call in self.session.get.call_args_list
            )
        )
        self.session.get.reset_mock()
        self.session.get.side_effect = None
        self.session.get.return_value = DownloadResponse(
            status=302, headers={"Location": "https://example.org/leak"}
        )
        with self.assertLogs(level="WARNING"):
            self.assertIsNone(await files.download_slack_file(self.file, 10))
        self.assertTrue(
            all(
                call.args[0].startswith("https://files.slack.com/")
                for call in self.session.get.call_args_list
            )
        )

    async def test_oversized_and_unavailable_files_do_not_abort_attachment_processing(
        self,
    ):
        self.session.get.return_value = DownloadResponse([b"a" * 11])
        with self.assertLogs(level="WARNING"):
            self.assertIsNone(await files.download_slack_file(self.file, 10))
        self.session.get.side_effect = ClientConnectionError("offline")
        with self.assertLogs(level="WARNING"):
            self.assertEqual(await attachments.attachment_parts([self.file]), [])

    async def test_partial_file_events_are_hydrated_and_canvases_allow_html(self):
        self.session.get.return_value = DownloadResponse([b"png"])
        parts = await attachments.attachment_parts(
            [{"id": "F1", "file_access": "check_file_info"}]
        )
        self.assertEqual(parts[1]["image_url"]["url"], "data:image/png;base64,cG5n")
        self.session.get.return_value = DownloadResponse(
            [b"<h1>FAQ</h1>"], content_type="text/html"
        )
        self.assertEqual(
            await files.download_slack_file(self.file, 100, allow_html=True),
            b"<h1>FAQ</h1>",
        )

    async def test_remote_files_never_receive_slack_credentials(self):
        self.assertIsNone(
            await files.download_slack_file({**self.file, "is_external": True}, 100)
        )
        self.session.get.assert_not_called()
        for url in [
            "http://files.slack.com/test",
            "https://slack.com.example.org/test",
            "https://slack.com@evil.org/test",
        ]:
            self.assertFalse(files._slack_url(url))

    async def test_canvas_login_html_is_rejected_and_second_token_is_tried(self):
        self.session.get.side_effect = [
            DownloadResponse(
                [b'<form id="signin_form">login</form>'], content_type="text/html"
            ),
            DownloadResponse([b"<h1>Real FAQ</h1>"], content_type="text/html"),
        ]
        self.assertEqual(
            await files.download_slack_file(self.file, 100, allow_html=True),
            b"<h1>Real FAQ</h1>",
        )

    async def test_actual_download_sizes_enforce_total_attachment_budget(self):
        self.session.get.side_effect = [
            DownloadResponse([b"123456"]),
            DownloadResponse([b"12345"]),
            DownloadResponse([b"12345"]),
        ]
        with (
            patch.object(attachments, "MAX_TOTAL_BYTES", 10),
            self.assertLogs(level="WARNING"),
        ):
            parts = await attachments.attachment_parts([self.file, self.file])
        self.assertEqual(len(parts), 2)


if __name__ == "__main__":
    unittest.main()
