from nephthys.views.home import AppHomeView
from nephthys.views.home.components.header import get_header


def get_loading_view(home_type: AppHomeView):
    return {
        "type": "home",
        "blocks": [
            *get_header(user=None, current=home_type),
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": ":hourglass_flowing_sand: loading...",
                },
            },
        ],
    }
