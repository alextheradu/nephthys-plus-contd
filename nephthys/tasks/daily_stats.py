import logging
from datetime import datetime
from datetime import timedelta
from datetime import timezone
from zoneinfo import ZoneInfo

from nephthys.database.enums import TicketStatus
from nephthys.database.tables import TagsOnTickets
from nephthys.database.tables import Ticket
from nephthys.utils.env import env
from nephthys.utils.stats import calculate_daily_stats
from nephthys.utils.stats import calculate_overall_stats
from nephthys.utils.stats import leaderboard_name
from nephthys.utils.ticket_methods import get_question_message_link
from nephthys.views.home.components.ticket_status_pie import (
    generate_ticket_status_pie_image,
)

BLOCK_TEXT_LIMIT = 2900
HIRE_MIN_CLOSED = 50
HIRE_MAX_SHOWN = 3


def slack_timestamp(dt: datetime, format: str = "date_short") -> str:
    fallback = dt.isoformat().replace("T", " ")
    return f"<!date^{int(dt.timestamp())}^{{{format}}}|{fallback}>"


def chunk_message(text: str, limit: int = BLOCK_TEXT_LIMIT) -> list[str]:
    text = text.strip("\n")
    chunks = []
    while text:
        if len(text) <= limit:
            chunks.append(text)
            break
        split_at = text.rfind("\n", 0, limit)
        if split_at <= 0:
            split_at = limit
        chunks.append(text[:split_at])
        text = text[split_at:].lstrip("\n")
    return chunks


async def ticket_lines(tickets: list[Ticket]) -> list[str]:
    MAX_TICKETS = env.daily_summary_max_tickets
    count = len(tickets)

    lines = []
    for i, ticket in enumerate(tickets[:MAX_TICKETS]):
        label = (
            ticket.title
            or ticket.description[:100]
            or f"Ticket #{ticket.id} (no description)"
        )
        last_reply = (
            slack_timestamp(ticket.last_msg_at, format="date_short")
            if ticket.last_msg_at
            else "unknown"
        )
        created_date = slack_timestamp(ticket.created_at, format="date_short")
        # FIXME: This is an n+1 query
        tag_links = await TagsOnTickets.objects(TagsOnTickets.tag).where(
            TagsOnTickets.ticket == ticket.id
        )
        tags_string = (
            " (" + ", ".join(f"*{t.tag.name}*" for t in tag_links if t.tag) + ")"
            if tag_links
            else ""
        )
        lines.append(
            f"{i + 1}. <{get_question_message_link(ticket)}|{label}>{tags_string} (created {created_date}, last reply *{last_reply}*)"
        )
    if count > MAX_TICKETS:
        lines.append(f"_(plus {count - MAX_TICKETS} more)_")

    return lines


async def send_daily_stats():
    """
    Calculates and sends statistics for the previous day to a Slack channel.
    This task is intended to be run at midnight.
    """
    london_tz = ZoneInfo("Europe/London")
    now_london = datetime.now(london_tz)
    today_midnight_london = now_london.replace(
        hour=0, minute=0, second=0, microsecond=0
    )

    # this gives us the 24-hour period of the previous day
    start_of_yesterday = today_midnight_london - timedelta(days=1)
    end_of_yesterday = today_midnight_london

    logging.info(
        f"Generating daily stats for the period: {start_of_yesterday} to {end_of_yesterday}"
    )

    try:
        stats = await calculate_daily_stats(start_of_yesterday, end_of_yesterday)

        daily_leaderboard_lines = [
            f"{i + 1}. {leaderboard_name(entry)} - {entry['count']} closed tickets"
            for i, entry in enumerate(stats.helpers_leaderboard[:3])
        ]
        if not daily_leaderboard_lines:
            daily_leaderboard_str = "_No tickets were closed yesterday!_"
        else:
            daily_leaderboard_str = "\n".join(daily_leaderboard_lines)

        overall = await calculate_overall_stats()
        hire_candidates = [
            e
            for e in overall.helpers_leaderboard
            if not e["user"].helper and e["count"] > HIRE_MIN_CLOSED
        ][:HIRE_MAX_SHOWN]
        hire_str = (
            "\n:rac_info: *a few helpers you may want to kidnap:*\n"
            + "\n".join(
                f"• <@{e['user'].slack_id}> - {e['count']} closed tickets"
                for e in hire_candidates
            )
            + "\n"
            if hire_candidates
            else ""
        )

        tickets_awaiting_response = (
            await Ticket.objects()
            .where(
                (Ticket.status != TicketStatus.CLOSED)
                & (Ticket.last_msg_at < today_midnight_london - timedelta(days=5))
            )
            .order_by(Ticket.last_msg_at)
        )

        pie_chart = await generate_ticket_status_pie_image(
            tz=timezone(now_london.utcoffset() or timedelta(0))
        )

        msg = f"""
um, um, hi there! hope i'm not disturbing you, but i just wanted to let you know that i've got some stats for you! :rac_cute:

*:mc-clock: in the last 24 hours...* _(that's a day, right? right? that's a day, yeah ok)_
:rac_woah: *{stats.new_tickets_total}* total tickets were opened and you managed to close *{stats.closed_today_from_today}* of them! congrats!! :D
:rac_info: *{stats.assigned_today_in_progress}* tickets have been assigned to users, and *{stats.new_tickets_still_open}* are still open
you managed to close a whopping *{stats.closed_today}* tickets in the last 24 hours, well done!

*:rac_info: today's leaderboard*
{daily_leaderboard_str}
{hire_str}"""

        if pie_chart:
            await env.slack_client.files_upload_v2(
                channel=env.slack_bts_channel,
                file=pie_chart,
                title="ticket status",
                initial_comment=msg,
            )
        else:
            await env.slack_client.chat_postMessage(
                channel=env.slack_bts_channel, text=msg
            )

        if not tickets_awaiting_response:
            await env.slack_client.chat_postMessage(
                channel=env.slack_bts_channel,
                text=":rac_woah: _btw, every ticket is closed. well done team!_",
            )
        else:
            header = await env.slack_client.chat_postMessage(
                channel=env.slack_bts_channel,
                text=(
                    ":rac_shy: *tickets that aren't closed yet*\n"
                    "i found some older tickets that might be waiting for a response from someone..."
                ),
                unfurl_links=False,
                unfurl_media=False,
            )
            lines = await ticket_lines(tickets_awaiting_response)
            for chunk in chunk_message("\n".join(lines)):
                await env.slack_client.chat_postMessage(
                    channel=env.slack_bts_channel,
                    thread_ts=header["ts"],
                    text=chunk,
                    unfurl_links=False,
                    unfurl_media=False,
                )

        logging.info("Daily stats message sent successfully.")

    except Exception as e:
        logging.error(f"Failed to send daily stats: {e}", exc_info=True)
