"""Central model configuration.

A single place where the per-agent model and the runtime thresholds are
resolved. The values come from ``config/settings.yaml`` (the single source of
truth); ``config.py`` only maps the model *alias* named there to a concrete
``LiteLlm`` builder. All roles currently point at the same Uni-GPT alias, but
the per-role split is now a config edit, not a code change — that is what feeds
the cost/quality trade-off.

Backend: University of Cologne GPT, an OpenAI-compatible endpoint. ADK talks
to it via the ``LiteLlm`` model class (LiteLLM provider ``openai/``).
Prerequisite: ``pip install -r requirements.txt`` (pulls in google-adk[extensions]).
The ReAct/agent code stays untouched by the concrete model — it only uses the
``*_MODEL`` objects supplied here.
"""

from __future__ import annotations
from pathlib import Path

import logging
import os

logger = logging.getLogger(__name__)

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

BASE_DIR = Path(__file__).resolve().parent
_SETTINGS_PATH = Path(
    os.getenv("JA_SETTINGS_PATH", str(BASE_DIR.parent.parent / "config" / "settings.yaml"))
)


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


# Model alias -> builder. Add an entry here (plus the alias in settings.yaml)
# once the Uni offers additional endpoints; agents stay untouched.
_MODEL_BUILDERS = {"uni_gpt": _uni_model}

# Defaults mirror config/settings.yaml so the app still runs if the file is
# missing or PyYAML is unavailable (config.py must import cleanly for ADK
# discovery regardless).
_DEFAULTS: dict = {
    "models": {
        "orchestrator": "uni_gpt",
        "monitoring": "uni_gpt",
        "planner": "uni_gpt",
        "communicator": "uni_gpt",
    },
    "thresholds": {"at_risk_band": "MEDIUM"},
    "monitoring": {"poll_interval_seconds": 300},
}


def _load_settings() -> dict:
    """Read config/settings.yaml; fall back to ``_DEFAULTS`` on any problem."""
    try:
        import yaml  # transitive dep; optional at runtime

        loaded = yaml.safe_load(_SETTINGS_PATH.read_text(encoding="utf-8")) or {}
        # Shallow-merge top-level sections over the defaults so a partial file
        # (e.g. only `models:`) still yields complete settings.
        merged = {k: {**v} for k, v in _DEFAULTS.items()}
        for section, value in loaded.items():
            if isinstance(value, dict) and section in merged:
                merged[section].update(value)
            else:
                merged[section] = value
        return merged
    except FileNotFoundError:
        logger.info("settings.yaml not found at %s; using defaults.", _SETTINGS_PATH)
    except Exception as exc:  # missing PyYAML, parse error, ...
        logger.warning("Could not read settings.yaml (%s); using defaults.", exc)
    return {k: {**v} for k, v in _DEFAULTS.items()}


_SETTINGS = _load_settings()


def _model_for(role: str) -> LiteLlm:
    """Build the LiteLlm for a role from its alias in settings.yaml."""
    alias = _SETTINGS.get("models", {}).get(role) or _DEFAULTS["models"][role]
    builder = _MODEL_BUILDERS.get(alias)
    if builder is None:
        logger.warning("Unknown model alias %r for role %r; using uni_gpt.", alias, role)
        builder = _MODEL_BUILDERS["uni_gpt"]
    return builder()


# Per-role models, resolved from config/settings.yaml. (No RISK_MODEL: risk
# scoring is a deterministic statistic in tools/risk_model.py, never an LLM.)
MONITORING_MODEL = _model_for("monitoring")
PLANNER_MODEL = _model_for("planner")
ORCHESTRATOR_MODEL = _model_for("orchestrator")
DRAFTER_MODEL = _model_for("communicator")

# Runtime thresholds (also from settings.yaml) — read by the monitoring path.
AT_RISK_BAND: str = _SETTINGS.get("thresholds", {}).get("at_risk_band", "MEDIUM")
POLL_INTERVAL_SECONDS: int = _SETTINGS.get("monitoring", {}).get("poll_interval_seconds", 300)

CHROMA_PATH = Path(os.getenv("CHROMA_PATH", str(BASE_DIR / "data" / "chromadb")))
