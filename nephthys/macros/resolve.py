import logging

from nephthys.actions.resolve import resolve
from nephthys.database.enums import TicketStatus
from nephthys.database.tables import Ticket
from nephthys.database.tables import User
from nephthys.macros.types import Macro
from nephthys.utils.env import env


class Resolve(Macro):
    name = "resolve"
    aliases = ["close"]

    async def run(self, ticket: Ticket, helper: User, **kwargs) -> None:
        """
        Resolve the ticket with the given arguments.
        """
        logging.info(
            f"Resolving ticket with ts {ticket.msg_ts} by <@{helper.slack_id}>. ticket_id={ticket.id} helper_id={helper.id}"
        )
        if not ticket.status == TicketStatus.CLOSED:
            await resolve(
                ts=ticket.msg_ts,
                resolver=helper.slack_id,
                client=env.slack_client,
            )
        else:
            logging.info(
                f"Ticket with ts {ticket.msg_ts} is already closed. No action taken. (Trying to resolve for <@{helper.slack_id}>). ticket_id={ticket.id} helper_id={helper.id}"
            )
            return
