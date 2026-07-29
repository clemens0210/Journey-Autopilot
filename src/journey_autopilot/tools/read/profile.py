"""Onboarding-profile access shared by the read tools.

Both the raw store lookup (the request's user, else the single stored profile)
and the profile tool the Planner calls live here, so ``calendar``, ``reroute``
and the agent all read the same blob the same way.
"""

from __future__ import annotations


def _profile_connections() -> dict:
    """Read ``profile.connections`` from the onboarding store ({} on any failure).

    Single accessor for the store's connections blob — shared by the
    Outlook-connected check and the self-organized contact resolution so the
    lookup pattern lives in one place.
    """
    try:
        from ...persistence import store
        from ...request_context import current_user_id

        user_id = current_user_id.get()
        profile = store.get_profile(user_id) if user_id else store.any_profile()
        return (profile or {}).get("connections", {}) or {}
    except Exception:
        return {}


def get_user_profile() -> dict:
    """Reads the user's personal preference profile from onboarding.

    Contains class, seat preferences, the speed-vs-comfort tradeoff (0 = maximum
    comfort, 100 = fastest arrival), maximum number of transfers, home station,
    latest return time, and the autonomy level. Reroute options should be
    evaluated against this profile.

    Returns:
        A dict with the profile, or with "error" if onboarding has not been
        completed yet.
    """
    try:
        # Lazy import: keeps the ADK package independent of the persistence
        # layer (SQLite store) as long as the tool is not called.
        from ...persistence import store
        from ...request_context import current_user_id

        user_id = current_user_id.get()
        profile = store.get_profile(user_id) if user_id else store.any_profile()
    except Exception as exc:  # persistence layer / DB not available
        return {"error": f"Profile not readable: {exc}"}
    if profile is None:
        return {"error": "No user profile available — onboarding has not been completed yet."}
    return profile
