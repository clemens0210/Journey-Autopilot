"""Azure Identity device-code authentication with persistent token cache.

Uses azure.identity.DeviceCodeCredential with TokenCachePersistenceOptions
so the MSAL token cache survives process restarts. On Windows, tokens are
encrypted via DPAPI; on macOS via Keychain; on Linux via libsecret.

The persistent cache alone is NOT enough for silent auth: a *new*
``DeviceCodeCredential`` instance has no idea which cached account to use and
raises ``AuthenticationRequiredError``. Silent cross-instance auth needs the
``AuthenticationRecord`` returned by ``credential.authenticate()`` — account
metadata (no secrets), persisted here as a JSON file next to the token cache.
The onboarding web flow saves it (``save_authentication_record``);
``acquire_credential()`` loads it and passes it back, so the agent tools get
tokens silently.

Two entry points:

- ``acquire_credential()`` — for the agent (tools/read_tools.py): returns a
  DeviceCodeCredential whose ``get_token()`` is silent when the cache has
  a valid token. Used only when ``profile.connections.outlook`` is True.
- ``create_device_credential(prompt_callback)`` — for the onboarding web
  flow: creates a DeviceCodeCredential with a ``prompt_callback`` so the
  server can capture the device code (URL + user code) and show it in the
  browser instead of printing to stderr.
"""

from __future__ import annotations

import logging
import os
import sys
from datetime import datetime
from pathlib import Path
from typing import Callable

from azure.identity import (
    AuthenticationRecord,
    DeviceCodeCredential,
    TokenCachePersistenceOptions,
)

logger = logging.getLogger(__name__)

# Calendars.Read — reading events.  Calendars.ReadWrite would be needed to
# add/modify events (reschedule_outlook_event); left out for now so the
# consent screen matches the actual permission request.
# User.Read — read the signed-in user's own profile (mail, display name) so the
# app shows/uses the ACTUAL connected Microsoft account instead of a hardcoded
# demo email.
SCOPES = ["Calendars.Read", "User.Read"]

# Mail.Send — sending the appointment-notice email (Communicator, after user
# approval). Kept in a SEPARATE list: calendar reads keep requesting only
# ``SCOPES`` so an existing cached login (consented before Mail.Send existed)
# stays silently usable. Interactive logins request ``MAIL_SCOPES`` so the
# consent covers sending too; until the user re-consents, the send path fails
# with ``AuthenticationRequiredError`` while calendar reads keep working.
MAIL_SCOPES = [*SCOPES, "Mail.Send"]

_CACHE_NAME = "journey_autopilot"


def is_outlook_configured() -> bool:
    """Return True if MS Entra credentials are present in the environment."""
    return bool(os.getenv("MS_ENTRA_CLIENT_ID"))


def _identity_service_dir() -> Path:
    """Directory holding the MSAL token cache — the auth record lives beside it."""
    if sys.platform.startswith("win") and "LOCALAPPDATA" in os.environ:
        return Path(os.environ["LOCALAPPDATA"]) / ".IdentityService"
    return Path.home() / ".IdentityService"


def _auth_record_path() -> Path:
    return _identity_service_dir() / f"{_CACHE_NAME}.authrecord.json"


def save_authentication_record(record: AuthenticationRecord) -> None:
    """Persist the AuthenticationRecord from an interactive login.

    The record carries account METADATA (authority, client id, home account id,
    username) — no tokens or secrets — so a plain JSON file is fine. It is the
    missing piece that lets a later ``acquire_credential()`` find the account
    in the persistent token cache and authenticate silently.
    """
    path = _auth_record_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(record.serialize(), encoding="utf-8")
    logger.info("authentication record saved to %s", path)


def load_authentication_record() -> AuthenticationRecord | None:
    """Load the persisted AuthenticationRecord, or None if absent/unreadable.

    Returning None keeps the old behavior: silent auth fails with
    ``AuthenticationRequiredError`` and the calling tool falls back to mock
    data — the user then reconnects Outlook via onboarding.
    """
    path = _auth_record_path()
    try:
        return AuthenticationRecord.deserialize(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except Exception as exc:  # corrupt/incompatible record — treat as absent
        logger.warning("could not load authentication record from %s: %s", path, exc)
        return None


def acquire_credential() -> DeviceCodeCredential:
    """Return a DeviceCodeCredential backed by a persistent token cache.

    Tokens are cached to the OS credential store so subsequent calls (even
    across process restarts) are silent — as long as a token was previously
    acquired interactively, e.g. through the onboarding web device-code flow.

    The persisted ``AuthenticationRecord`` from the onboarding login (see
    :func:`save_authentication_record`) is passed to the credential — without
    it a fresh instance cannot locate the cached account and silent auth
    always fails.

    Built with ``disable_automatic_authentication=True``: if silent
    authentication is not possible (no auth record, cache empty, refresh
    token revoked), ``get_token()`` raises ``AuthenticationRequiredError``
    instead of silently starting a device flow and blocking. This credential
    serves background paths (the agent tools, the post-connect Graph preview)
    where an interactive prompt would block the event loop or be invisible to
    the user. Interactive authentication is the web flow's job (see
    :func:`create_device_credential`).

    Raises:
        RuntimeError: If MS_ENTRA_CLIENT_ID is missing.
    """
    client_id = os.getenv("MS_ENTRA_CLIENT_ID", "")
    tenant_id = os.getenv("MS_ENTRA_TENANT_ID", "consumers")

    if not client_id:
        raise RuntimeError(
            "MS_ENTRA_CLIENT_ID is not set. "
            "Copy .env.example to .env and fill in your Entra App Registration values."
        )

    return DeviceCodeCredential(
        tenant_id=tenant_id,
        client_id=client_id,
        cache_persistence_options=TokenCachePersistenceOptions(name=_CACHE_NAME),
        authentication_record=load_authentication_record(),
        disable_automatic_authentication=True,
    )


def create_device_credential(
    prompt_callback: Callable[[str, str, datetime], None],
    timeout: int = 900,
) -> DeviceCodeCredential:
    """Create a DeviceCodeCredential with a prompt_callback for the web flow.

    Uses the **same** persistent OS token cache as ``acquire_credential()``,
    so a token acquired through the web device flow is silently re-used by
    the agent's ``acquire_credential()`` path — one cache, both paths.

    Args:
        prompt_callback: Called with ``(verification_uri, user_code,
            expires_on)`` when the device code is received. The server stores
            these values so the frontend can display them.
        timeout: Seconds to wait for the user to authenticate (default 15 min).

    Raises:
        RuntimeError: If MS_ENTRA_CLIENT_ID is missing.
    """
    client_id = os.getenv("MS_ENTRA_CLIENT_ID", "")
    tenant_id = os.getenv("MS_ENTRA_TENANT_ID", "consumers")

    if not client_id:
        raise RuntimeError("MS_ENTRA_CLIENT_ID is not set.")

    return DeviceCodeCredential(
        tenant_id=tenant_id,
        client_id=client_id,
        cache_persistence_options=TokenCachePersistenceOptions(name=_CACHE_NAME),
        prompt_callback=prompt_callback,
        timeout=timeout,
    )


def clear_token_cache() -> bool:
    """Best-effort clear of the persistent MSAL token cache and auth record.

    Deletes the cache files that ``TokenCachePersistenceOptions(name=...)``
    creates under ``~/.IdentityService/`` (Linux/macOS) or
    ``%LOCALAPPDATA%\\.IdentityService`` (Windows), plus the persisted
    ``AuthenticationRecord`` — otherwise a later reconnect would pair a stale
    account record with a fresh cache.

    Returns:
        True if at least one file was removed, False otherwise.
    """
    from azure.identity._persistent_cache import CACHE_CAE_SUFFIX, CACHE_NON_CAE_SUFFIX

    removed = False
    candidates: list[Path] = [_auth_record_path()]

    # Windows: %LOCALAPPDATA%\.IdentityService\{name}.nocae / .cae
    if sys.platform.startswith("win") and "LOCALAPPDATA" in os.environ:
        base = Path(os.environ["LOCALAPPDATA"]) / ".IdentityService"
        candidates.append(base / (_CACHE_NAME + CACHE_NON_CAE_SUFFIX))
        candidates.append(base / (_CACHE_NAME + CACHE_CAE_SUFFIX))

    # Linux/macOS: ~/.IdentityService/{name}.nocae / .cae
    home_base = Path.home() / ".IdentityService"
    candidates.append(home_base / (_CACHE_NAME + CACHE_NON_CAE_SUFFIX))
    candidates.append(home_base / (_CACHE_NAME + CACHE_CAE_SUFFIX))

    for p in candidates:
        try:
            if p.exists():
                p.unlink()
                removed = True
        except OSError:
            pass  # best-effort

    return removed
