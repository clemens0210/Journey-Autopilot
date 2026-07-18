"""Naive baseline — "just ask the model once".

Sends one natural-language user prompt directly to Claude Sonnet 4.6. There is
no agent orchestration, system instruction, tool access, read/write split, or
veto gate. This is the side-by-side baseline for the multi-agent demo.

Usage:
    python baseline/single_shot.py

AWS credentials are read by LiteLLM. ``BEDROCK_MODEL`` and ``AWS_REGION`` may
override the defaults used by the rest of the project.
"""

from __future__ import annotations

import os
import ssl as _ssl
import sys
from pathlib import Path


_ROOT = Path(__file__).resolve().parents[1]

try:
    from dotenv import load_dotenv

    # Load credentials before importing LiteLLM.
    load_dotenv(_ROOT / ".env")
    load_dotenv(_ROOT / "journey_autopilot" / ".env")
except ImportError:
    pass

os.environ.setdefault("LITELLM_LOG", "CRITICAL")

# Match the Windows SSL workaround used by the runnable scenarios. Some
# Windows certificate stores contain malformed entries that otherwise make
# aiohttp/LiteLLM fail while creating its default SSL context.
if sys.platform.startswith("win"):
    _original_load_default_certs = _ssl.SSLContext.load_default_certs

    def _patched_load_default_certs(
        self, purpose: _ssl.Purpose = _ssl.Purpose.SERVER_AUTH
    ) -> None:
        try:
            _original_load_default_certs(self, purpose)
        except _ssl.SSLError as exc:
            if "NOT_ENOUGH_DATA" not in str(exc):
                raise

    _ssl.SSLContext.load_default_certs = _patched_load_default_certs

from litellm import completion


PROMPT = (
    "My ICE 1006 from Munich to Berlin is heavily delayed, and I have an "
    "important on-site meeting in Berlin at 14:00. Please assess the "
    "situation, find the best alternative, and tell me what I should do."
)

MODEL_ID = os.getenv("BEDROCK_MODEL", "us.anthropic.claude-sonnet-4-6")
MODEL = f"bedrock/{MODEL_ID}"
AWS_REGION = os.getenv("AWS_REGION", "us-east-1")


def ask_once(prompt: str = PROMPT) -> str:
    """Send exactly one user message to Sonnet 4.6 and return its answer."""
    response = completion(
        model=MODEL,
        messages=[{"role": "user", "content": prompt}],
        aws_region_name=AWS_REGION,
    )
    content = response.choices[0].message.content
    return content if isinstance(content, str) else str(content)


def main() -> None:
    print(ask_once())


if __name__ == "__main__":
    main()
