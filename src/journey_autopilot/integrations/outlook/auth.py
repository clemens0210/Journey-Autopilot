"""Azure Identity device-code authentication with persistent token cache.

Uses azure.identity.DeviceCodeCredential with TokenCachePersistenceOptions
so the MSAL token cache survives process restarts. On Windows, tokens are
encrypted via DPAPI; on macOS via Keychain; on Linux via libsecret.

The device-code prompt appears only on first run or after token expiry.
Subsequent runs acquire tokens silently from the persistent cache.
"""

from __future__ import annotations

import os
import sys

from azure.identity import DeviceCodeCredential, TokenCachePersistenceOptions

SCOPES = ["Calendars.Read"]

_CACHE_NAME = "journey_autopilot"


def acquire_credential() -> DeviceCodeCredential:
    """Return a DeviceCodeCredential backed by a persistent token cache.

    On first call the credential prints a device-code URL + code to stderr,
    then polls until the user completes sign-in in their browser. Tokens are
    cached to the OS credential store so subsequent calls (even across process
    restarts) are silent.

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
