"""Central model configuration.

A single place where the model is set per agent role — also handy for
later baseline comparison. Currently all roles share the same endpoint;
once useful, a different model can be set per role here.

Backend: University of Cologne GPT, an OpenAI-compatible endpoint. ADK talks
to it via the ``LiteLlm`` model class (LiteLLM provider ``openai/``).
Prerequisite: ``pip install -r requirements.txt`` (pulls in google-adk[extensions]).
The ReAct/agent code stays untouched by the concrete model — it only uses the
``*_MODEL`` objects supplied here.
"""

from __future__ import annotations
from pathlib import Path

import os

# LiteLLM's background logging/telemetry worker can emit timeout stack traces
# after otherwise successful demo runs. Keep it quiet by default; override with
# LITELLM_LOG=ERROR or LITELLM_LOG=DEBUG when diagnosing LiteLLM itself.
os.environ.setdefault("LITELLM_LOG", "CRITICAL")

# University of Cologne GPT: OpenAI-compatible endpoint via LiteLLM.
# Requires google-adk[extensions] (litellm); if missing, ADK gives a clear
# ImportError message with install instructions.
from google.adk.models.lite_llm import LiteLlm

_UNI_MODEL = os.getenv("UNI_GPT_MODEL", "gpt-oss")
_UNI_BASE_URL = os.getenv("UNI_GPT_BASE_URL")
_UNI_API_KEY = os.getenv("UNI_GPT_API_KEY")


def _uni_model() -> LiteLlm:
    """Fresh LiteLlm instance for the Uni-GPT endpoint (one per agent).

    The provider prefix ``openai/`` tells LiteLLM: "talk to this endpoint via
    the OpenAI chat protocol". ``api_base``/``api_key`` come from .env.
    """
    return LiteLlm(
        model=f"openai/{_UNI_MODEL}",
        api_base=_UNI_BASE_URL,
        api_key=_UNI_API_KEY,
    )


# A shared endpoint for all roles. Once the Uni offers additional models,
# a different model name can be set per role here.
MONITORING_MODEL = _uni_model()
PLANNER_MODEL = _uni_model()
ORCHESTRATOR_MODEL = _uni_model()
DRAFTER_MODEL = _uni_model()

BASE_DIR = Path(__file__).resolve().parent

CHROMA_PATH = Path(os.getenv("CHROMA_PATH", str(BASE_DIR / "data" / "chromadb")))
