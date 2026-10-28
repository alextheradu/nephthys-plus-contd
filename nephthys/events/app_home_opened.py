import asyncio
import logging
import math
import traceback
from dataclasses import dataclass
from dataclasses import field
from typing import Any

from aiohttp import ClientError
from prometheus_client import Histogram
from slack_sdk.errors import SlackApiError
from slack_sdk.web.async_client import AsyncWebClient

from nephthys.database.tables import User
from nephthys.utils.performance import perf_timer
from nephthys.views.home import AppHomeView
from nephthys.views.home.assigned import get_assigned_tickets_view
from nephthys.views.home.category_tags import get_category_tags_view
from nephthys.views.home.dashboard import get_dashboard_view
from nephthys.views.home.error import get_error_view
from nephthys.views.home.loading import get_loading_view
from nephthys.views.home.macros import get_macros_view
from nephthys.views.home.stats import get_stats_view
from nephthys.views.home.team_tags import get_team_tags_view


@dataclass
class RequestedView:
    home_type: AppHomeView
    page: int | None = None
    publish_lock: asyncio.Lock = field(default_factory=asyncio.Lock)


DEFAULT_VIEW = AppHomeView.DASHBOARD


async def on_app_home_opened(event: dict[str, Any], client: AsyncWebClient):
    if event.get("tab", "home") != "home":
        return
    slack_id = event["user"]
    user = await User.objects().get(User.slack_id == slack_id)
    # Restore the the last view the user had open, if any
    if user and user.app_home_last_view:
        try:
            initial_view = AppHomeView(user.app_home_last_view)
            logging.info(
                f"Restoring saved app home view slack_id={slack_id} view={initial_view}"
            )
        except ValueError:
            logging.error(
                f"Invalid app_home_last_view in DB for user_id={user.id} last_view={user.app_home_last_view}"
            )
            initial_view = DEFAULT_VIEW
    else:
        initial_view = DEFAULT_VIEW
    await open_app_home(initial_view, client, slack_id)


APP_HOME_RENDER_DURATION = Histogram(
    "nephthys_app_home_render_duration_seconds",
    "How long it takes to load the app home screen",
    ["home_type"],
)


# Map of the last-requested view for each Slack user
# This prevents a view that took a while to render overwriting the view you want
# Entries are deleted once the view is published
last_requested_views: dict[str, RequestedView] = {}


async def open_app_home(
    home_type: AppHomeView,
    client: AsyncWebClient,
    user_id: str,
    page: int | None = None,
):
    requested = RequestedView(home_type, page)
    previous = last_requested_views.get(user_id)
    if previous:
        requested.publish_lock = previous.publish_lock
    last_requested_views[user_id] = requested
    try:
        await publish_view(client, user_id, get_loading_view(home_type), requested)
        if last_requested_views.get(user_id) is not requested:
            return
        # Generate the view (this is when DB queries are made)
        user = await User.objects().where(User.slack_id == user_id).first()
        logging.info(f"Opening {home_type} for {user_id}")

        async with perf_timer(
            f"Rendering app home (type={home_type})",
            APP_HOME_RENDER_DURATION,
            home_type=home_type,
        ):
            match home_type:
                case AppHomeView.DASHBOARD:
                    view = await get_dashboard_view(slack_user=user_id, db_user=user)
                case AppHomeView.ASSIGNED_TICKETS:
                    view = await get_assigned_tickets_view(user, page=page or 1)
                case AppHomeView.TEAM_TAGS:
                    view = await get_team_tags_view(user)
                case AppHomeView.CATEGORY_TAGS:
                    view = await get_category_tags_view(user)
                case AppHomeView.MY_STATS:
                    view = await get_stats_view(user)
                case AppHomeView.MACROS:
                    view = await get_macros_view(user)

        # Publish the view!
        published = await publish_view(client, user_id, view, requested)
        if not published and last_requested_views.get(user_id) is requested:
            await publish_view(
                client,
                user_id,
                get_error_view("Slack could not display this page. Please try again."),
                requested,
            )

        # Record the user's last-viewed page for future visits
        if published and user and last_requested_views.get(user_id) is requested:
            user.app_home_last_view = home_type.value
            await user.save()

    except Exception as e:
        if last_requested_views.get(user_id) is not requested:
            return
        logging.error(f"Error opening app home for <@{user_id}>: {e}", exc_info=True)
        tb = traceback.format_exception(e)

        tb_str = "".join(tb)

        view = get_error_view(
            f"An error occurred while opening the app home: {e}",
            traceback=tb_str,
        )
        await publish_view(client, user_id, view, requested)
    finally:
        if last_requested_views.get(user_id) is requested:
            del last_requested_views[user_id]


async def publish_view(
    client: AsyncWebClient,
    user_id: str,
    view: dict[str, Any],
    requested: RequestedView | None = None,
) -> bool:
    # Serialize publications for a user, including retries. A newer navigation
    # request must never be overwritten by an older render or its error screen.
    lock = requested.publish_lock if requested else asyncio.Lock()
    async with lock:
        for attempt in range(3):
            if requested and last_requested_views.get(user_id) is not requested:
                return False
            try:
                await client.views_publish(user_id=user_id, view=view)
                return True
            except SlackApiError as error:
                code = error.response.get("error")
                transient = (
                    code
                    in {
                        "internal_error",
                        "fatal_error",
                        "service_unavailable",
                        "request_timeout",
                        "ratelimited",
                    }
                    or error.response.status_code >= 500
                )
                if not transient or attempt == 2:
                    logging.error(
                        "Could not publish App Home user_id=%s error=%s", user_id, code
                    )
                    return False
                retry_after = error.response.headers.get(
                    "Retry-After"
                ) or error.response.headers.get("retry-after")
                try:
                    delay = float(retry_after) if retry_after else 0.5 * 2**attempt
                except (TypeError, ValueError):
                    delay = 0.5 * 2**attempt
                if not math.isfinite(delay) or delay < 0:
                    delay = 0.5 * 2**attempt
            except (ClientError, TimeoutError):
                if attempt == 2:
                    logging.exception("Could not publish App Home user_id=%s", user_id)
                    return False
                delay = 0.5 * 2**attempt
            await asyncio.sleep(max(0, delay))
    return False
