"""Azure Identity device-code authentication with persistent token cache.

Uses azure.identity.DeviceCodeCredential with TokenCachePersistenceOptions
so the MSAL token cache survives process restarts. On Windows, tokens are
encrypted via DPAPI; on macOS via Keychain; on Linux via libsecret.

The device-code prompt appears only on first run or after token expiry.
Subsequent runs acquire tokens silently from the persistent cache.

Two entry points:

- ``acquire_credential()`` — for the agent (tools.py): returns a
  DeviceCodeCredential whose ``get_token()`` is silent when the cache has
  a valid token. Used only when ``profile.connections.outlook`` is True.
- ``create_device_credential(prompt_callback)`` — for the onboarding web
  flow: creates a DeviceCodeCredential with a ``prompt_callback`` so the
  server can capture the device code (URL + user code) and show it in the
  browser instead of printing to stderr.
"""

from __future__ import annotations

import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Optional

from azure.identity import DeviceCodeCredential, TokenCachePersistenceOptions

# Calendars.Read — reading events.  Calendars.ReadWrite would be needed to
# add/modify events (reschedule_outlook_event); left out for now so the
# consent screen matches the actual permission request.
SCOPES = ["Calendars.Read"]

_CACHE_NAME = "journey_autopilot"


def is_outlook_configured() -> bool:
    """Return True if MS Entra credentials are present in the environment."""
    return bool(os.getenv("MS_ENTRA_CLIENT_ID"))


def acquire_credential() -> DeviceCodeCredential:
    """Return a DeviceCodeCredential backed by a persistent token cache.

    On first call (per cache) the credential prints a device-code URL + code
    to stderr, then polls until the user completes sign-in in their browser.
    Tokens are cached to the OS credential store so subsequent calls (even
    across process restarts) are silent.

    Returns:
        A DeviceCodeCredential ready to pass to GraphServiceClient.

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

    print("  Authenticating with Microsoft Graph ...", file=sys.stderr, flush=True)

    return DeviceCodeCredential(
        tenant_id=tenant_id,
        client_id=client_id,
        cache_persistence_options=TokenCachePersistenceOptions(name=_CACHE_NAME),
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
    """Best-effort clear of the persistent MSAL token cache.

    Deletes the cache files that ``TokenCachePersistenceOptions(name=...)``
    creates under ``~/.IdentityService/`` (Linux/macOS) or
    ``%LOCALAPPDATA%\\.IdentityService`` (Windows).

    Returns:
        True if at least one cache file was removed, False otherwise.
    """
    from azure.identity._persistent_cache import CACHE_CAE_SUFFIX, CACHE_NON_CAE_SUFFIX

    removed = False
    candidates: list[str] = []

    # Windows: %LOCALAPPDATA%\.IdentityService\{name}.nocae / .cae
    if sys.platform.startswith("win") and "LOCALAPPDATA" in os.environ:
        base = Path(os.environ["LOCALAPPDATA"]) / ".IdentityService"
        candidates.append(str(base / (_CACHE_NAME + CACHE_NON_CAE_SUFFIX)))
        candidates.append(str(base / (_CACHE_NAME + CACHE_CAE_SUFFIX)))

    # Linux/macOS: ~/.IdentityService/{name}.nocae / .cae
    home_base = Path.home() / ".IdentityService"
    candidates.append(str(home_base / (_CACHE_NAME + CACHE_NON_CAE_SUFFIX)))
    candidates.append(str(home_base / (_CACHE_NAME + CACHE_CAE_SUFFIX)))

    for path_str in candidates:
        try:
            p = Path(path_str)
            if p.exists():
                p.unlink()
                removed = True
        except OSError:
            pass  # best-effort

    return removed
