from collections.abc import Mapping
from typing import Any
from typing import Literal
from typing import NotRequired
from typing import TypedDict

import httpx
from openai import AsyncOpenAI

from nephthys.utils.env import get_environ

type StructuredGuidance = str | dict[str, Any] | list[Any]

DEFAULT_BASE_URL = "https://ai.hackclub.com/proxy/v1"

# HACK_CLUB_AI_* are the old names, still read so existing deployments keep working
api_key = get_environ("AI_API_KEY") or get_environ("HACK_CLUB_AI_API_KEY")
base_url = (
    get_environ("AI_BASE_URL")
    or get_environ("HACK_CLUB_AI_BASE_URL")
    or DEFAULT_BASE_URL
).rstrip("/")

ai_client: AsyncOpenAI | None = (
    AsyncOpenAI(api_key=api_key, base_url=base_url) if api_key else None
)


class ChoiceQuestion(TypedDict):
    type: Literal["choice"]
    instructions: StructuredGuidance
    criteria: Mapping[str, StructuredGuidance | None]


class ChoiceAnswer(TypedDict):
    type: Literal["choice"]
    choice: str
    confidence: NotRequired[float]
    probabilities: NotRequired[dict[str, float]]


class DecisionsUsage(TypedDict):
    input_tokens: int
    output_tokens: int


class DecisionsResponse(TypedDict):
    model: str
    answers: dict[str, ChoiceAnswer]
    usage: DecisionsUsage


_http_client = httpx.AsyncClient(timeout=60)


async def decisions(
    model: str,
    state: str | dict | list,
    questions: dict[str, ChoiceQuestion],
) -> DecisionsResponse:
    """Asks Typesafe's Jev model (System One) to answer multiple-choice questions.

    Hack Club AI proxies this at `<AI_BASE_URL>/jev/systemone`.
    Raises `httpx.HTTPStatusError` on API error."""
    if not api_key:
        raise RuntimeError("AI_API_KEY is not set")
    response = await _http_client.post(
        f"{base_url}/jev/systemone",
        headers={"Authorization": f"Bearer {api_key}"},
        json={"model": model, "state": state, "questions": questions},
    )
    response.raise_for_status()
    return response.json()
