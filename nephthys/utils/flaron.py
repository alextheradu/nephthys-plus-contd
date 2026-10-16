import logging
from typing import Any

from aiohttp import ClientTimeout

from nephthys.utils.env import env

# Statuses meaning the person hasn't been identity verified (the verified_* statuses
# and "unknown" are deliberately not listed, so they never count as unverified).
NOT_VERIFIED_STATUSES = {"needs_submission", "pending", "rejected", "not_found"}


async def get_flaron_profile(slack_id: str) -> dict[str, Any] | None:
    """Everything Flaron knows about a user (profile, IDV, fraud and NDA info).

    Returns None if the lookup fails. Only show this to helpers.
    """
    try:
        async with env.session.get(
            f"{env.flaron_url}/user/{slack_id}", timeout=ClientTimeout(total=5)
        ) as res:
            if res.status != 200:
                logging.info(f"Flaron returned {res.status} for user_id={slack_id}")
                return None
            data = await res.json()
    except Exception as e:
        logging.info(f"Flaron lookup failed for user_id={slack_id}: {e}")
        return None
    inner = data.get("data") if isinstance(data, dict) else None
    return inner if isinstance(inner, dict) else None


async def is_not_identity_verified(slack_id: str) -> bool:
    """True only when Flaron says this user has not been identity verified.

    A failed lookup or an unclear status returns False, so AI steps just don't
    get the hint. The raw Flaron data is never passed to the AI.
    """
    profile = await get_flaron_profile(slack_id)
    return bool(profile) and profile.get("idv_status") in NOT_VERIFIED_STATUSES
