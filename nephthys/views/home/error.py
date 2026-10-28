def get_error_view(msg: str, traceback: str | None = None):
    if traceback:
        msg = f"{msg}\n\nTraceback:\n```{traceback}```"
    # Slack limits section text to 3,000 characters, including the label.
    detail = f"Error message\n{msg}"
    if len(detail) > 3000:
        detail = detail[:2999] + "…"
    return {
        "type": "home",
        "blocks": [
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": "Sorry, something went wrong. Please try again later.",
                },
            },
            {
                "type": "section",
                "text": {"type": "mrkdwn", "text": detail},
            },
        ],
    }
