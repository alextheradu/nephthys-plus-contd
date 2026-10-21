from nephthys.transcripts.transcript import Transcript


class Contd(Transcript):
    """Transcript for Cont.d YSWS."""

    program_name: str = "Cont.d"
    program_owner: str = "U0AFWJX9CP2"

    help_channel: str = "C0C5XMTDDHA"
    ticket_channel: str = "C0CA1ULHB7Z"
    team_channel: str = "C0CA1ULHB7Z"
    identity_help_channel: str = "C092833JXKK"

    faq_link: str = "https://arc.net/l/quote/amxwajkp"
    faq_canvas_id: str | None = None
    faq_events_question: str | None = None

    first_ticket_create: str = f"""
Hey (user), welcome to the Cont.d support channel! Someone from the team will be with you soon.
In the meantime, please check <{faq_link}|the Cont.d FAQ> for answers to common questions.
If your question has been answered, please use the button below to mark it as resolved.
"""
    ticket_create: str = f"""
Hi (user), your Cont.d support ticket is in the queue. Someone from the team will help you soon.
While you wait, please check <{faq_link}|the Cont.d FAQ> in case it already answers your question.
"""
    resolve_ticket_button: str = "Mark as resolved"
    ticket_resolve: str = f"""
This Cont.d support ticket was marked as resolved by <@{{user_id}}>.
If you need more help, please start a new thread in <#{help_channel}>.
"""
    ticket_resolve_stale: str = f"""
This Cont.d support ticket has been inactive, so it was closed.
If you still need help, please start a new thread in <#{help_channel}>.
"""
    ticket_reopen: str = "This Cont.d support ticket was reopened by <@{helper_slack_id}>. Someone will be with you shortly."
    not_allowed_channel: str = f"Please ask <@{program_owner}> if you need access to this Cont.d support channel."

    faq_macro: str = f"""
Hey (user), this question may already be answered in the <{faq_link}|Cont.d FAQ>.
Please check it out, and start a new thread if you still need help.
"""
    identity_macro: str = f"Please ask identity verification questions in <#{identity_help_channel}>."
    stale_tickets_macro: str = "This Cont.d support ticket has been inactive, so I am closing it. Please start a new thread if you still need help."

    wait_time_estimate: str = "_typical wait right now: about {wait}_"
    thread_broadcast_delete: str = "Please keep your messages in one thread so the Cont.d support team can follow along. I removed that message from the channel."
    ticket_feedback_text: str = "Feedback really helps the Cont.d support team. Thanks!"
