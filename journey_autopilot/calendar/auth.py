"""MSAL device-code authentication flow with file-based token cache.

Uses the Microsoft Authentication Library (MSAL) to obtain an access token for
Microsoft Graph via the device-code flow (https://aka.ms/devicelogin). Tokens
are cached to disk so the user only interacts with the browser on first run or
after cache expiry.
"""

from __future__ import annotations

import atexit
import json
import os
import sys
from pathlib import Path

import msal

_CACHE_DIR = Path.home() / ".journey-autopilot"
_CACHE_FILE = _CACHE_DIR / "msal_cache.bin"


def _load_cache() -> msal.SerializableTokenCache:
    """Load the serialized token cache from disk, or return an empty one."""
    cache = msal.SerializableTokenCache()
    if _CACHE_FILE.exists():
        cache.deserialize(_CACHE_FILE.read_bytes())
    return cache


def _persist_cache(cache: msal.SerializableTokenCache) -> None:
    """Write the token cache to disk."""
    _CACHE_DIR.mkdir(parents=True, exist_ok=True)
    if cache.has_state_changed:
        _CACHE_FILE.write_bytes(cache.serialize())


def _build_app(cache: msal.SerializableTokenCache) -> msal.PublicClientApplication:
    """Create an MSAL PublicClientApplication from env configuration."""
    client_id = os.getenv("MS_ENTRA_CLIENT_ID", "")
    tenant = os.getenv("MS_ENTRA_TENANT_ID", "consumers")

    if not client_id:
        raise RuntimeError(
            "MS_ENTRA_CLIENT_ID is not set. "
            "Copy .env.example to .env and fill in your Entra App Registration values."
        )

    authority = f"https://login.microsoftonline.com/{tenant}"
    return msal.PublicClientApplication(
        client_id=client_id,
        authority=authority,
        token_cache=cache,
    )


SCOPES = ["Calendars.Read"]


def acquire_token() -> str:
    """Obtain an access token for Microsoft Graph.

    Tries silent cache-based acquisition first. If no valid token is cached,
    initiates the device-code flow: prints a URL and one-time code, then polls
    until the user completes authentication in their browser.

    Returns:
        An OAuth 2.0 access token string.

    Raises:
        RuntimeError: If MS_ENTRA_CLIENT_ID is missing or device-code flow
            times out / is cancelled.
    """
    cache = _load_cache()
    app = _build_app(cache)

    accounts = app.get_accounts()
    if accounts:
        result = app.acquire_token_silent(SCOPES, account=accounts[0])
        if result and "access_token" in result:
            _persist_cache(cache)
            return result["access_token"]

    flow = app.initiate_device_flow(scopes=SCOPES)
    if "user_code" not in flow:
        raise RuntimeError(
            f"Device-code flow initiation failed: {json.dumps(flow, indent=2)}"
        )

    print(
        f"\n  To sign in, use a web browser to open the page\n"
        f"  {flow['verification_uri']}\n"
        f"  and enter the code: {flow['user_code']}\n"
        f"  (This prompt will expire in {flow.get('expires_in', 900)} seconds)\n",
        file=sys.stderr,
        flush=True,
    )

    atexit.register(_persist_cache, cache)

    result = app.acquire_token_by_device_flow(flow)
    _persist_cache(cache)

    if "access_token" not in result:
        error_desc = result.get("error_description", json.dumps(result))
        raise RuntimeError(f"Authentication failed: {error_desc}")

    return result["access_token"]
