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
from typing import Any

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

_BEDROCK_MODEL = os.getenv("BEDROCK_MODEL", "us.anthropic.claude-sonnet-4-6")
# Cross-region inference profile (note the `us.` prefix): the plain
# foundation-model ID is not invokable with on-demand throughput. Use the
# prefix matching AWS_REGION (`us.` / `eu.` / `apac.`).
_BEDROCK_HAIKU_MODEL = os.getenv(
    "BEDROCK_HAIKU_MODEL", "us.anthropic.claude-haiku-4-5-20251001-v1:0"
)
_BEDROCK_REGION = os.getenv("AWS_REGION", "us-east-1")

BASE_DIR = Path(__file__).resolve().parent
_SETTINGS_PATH = Path(
    os.getenv("JA_SETTINGS_PATH", str(BASE_DIR.parent.parent / "config" / "settings.yaml"))
)


def _uni_model(**params: Any) -> LiteLlm:
    """Fresh LiteLlm instance for the Uni-GPT endpoint (one per agent).

    The provider prefix ``openai/`` tells LiteLLM: "talk to this endpoint via
    the OpenAI chat protocol". ``api_base``/``api_key`` come from .env. Per-role
    ``model_params`` tuning is Bedrock-oriented and ignored here.
    """
    if params:
        logger.debug("Ignoring model_params %s for the Uni-GPT endpoint.", sorted(params))
    return LiteLlm(
        model=f"openai/{_UNI_MODEL}",
        api_base=_UNI_BASE_URL,
        api_key=_UNI_API_KEY,
    )


def _bedrock_model(model_id: str, **params: Any) -> LiteLlm:
    """Fresh LiteLlm instance for a Claude model on AWS Bedrock.

    Auth uses the ``AWS_BEARER_TOKEN_BEDROCK`` env var (read by LiteLLM
    automatically). Region comes from ``AWS_REGION`` (default: us-east-1).
    ``params`` are per-role tuning kwargs from settings.yaml's ``model_params``
    (e.g. ``reasoning_effort``, ``temperature``); ``drop_params`` lets LiteLLM
    silently ignore any a given Claude model doesn't support instead of erroring.
    """
    return LiteLlm(
        model=f"bedrock/{model_id}",
        aws_region_name=_BEDROCK_REGION,
        drop_params=True,
        **params,
    )


def _bedrock_claude_model(**params: Any) -> LiteLlm:
    """Claude Sonnet 4.6 on Bedrock — the stronger tier for demanding roles."""
    return _bedrock_model(_BEDROCK_MODEL, **params)


def _bedrock_haiku_model(**params: Any) -> LiteLlm:
    """Claude Haiku 4.5 on Bedrock — the fast/cheap tier (e.g. the monitoring loop)."""
    return _bedrock_model(_BEDROCK_HAIKU_MODEL, **params)


# Model alias -> builder. Add an entry here (plus the alias in settings.yaml)
# to introduce a new endpoint; agents stay untouched.
_MODEL_BUILDERS = {
    "uni_gpt": _uni_model,
    "bedrock_claude": _bedrock_claude_model,
    "bedrock_haiku": _bedrock_haiku_model,
}

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
    "model_params": {},
    "thresholds": {"at_risk_band": "MEDIUM"},
    "monitoring": {"poll_interval_seconds": 300},
    "reroute": {"max_options": 6, "max_added_delay_minutes": 120},
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
    """Build the LiteLlm for a role from its alias in settings.yaml.

    Optional per-role tuning under ``model_params:`` (e.g. ``reasoning_effort``,
    ``temperature``) is forwarded to the builder as LiteLLM completion kwargs.
    It only affects the Bedrock (Claude) aliases; ``uni_gpt`` ignores it.
    """
    alias = _SETTINGS.get("models", {}).get(role) or _DEFAULTS["models"][role]
    builder = _MODEL_BUILDERS.get(alias)
    if builder is None:
        logger.warning("Unknown model alias %r for role %r; using uni_gpt.", alias, role)
        builder = _MODEL_BUILDERS["uni_gpt"]
    # `model_params` may be absent or an all-comments YAML block (parses to None).
    params = (_SETTINGS.get("model_params") or {}).get(role) or {}
    return builder(**params)


# Per-role models, resolved from config/settings.yaml. (No RISK_MODEL: risk
# scoring is a deterministic statistic in tools/risk_model.py, never an LLM.)
MONITORING_MODEL = _model_for("monitoring")
PLANNER_MODEL = _model_for("planner")
ORCHESTRATOR_MODEL = _model_for("orchestrator")
DRAFTER_MODEL = _model_for("communicator")

# Runtime thresholds (also from settings.yaml) — read by the monitoring path.
AT_RISK_BAND: str = _SETTINGS.get("thresholds", {}).get("at_risk_band", "MEDIUM")
POLL_INTERVAL_SECONDS: int = _SETTINGS.get("monitoring", {}).get("poll_interval_seconds", 300)

# Reroute pre-filter bounds (read by tools/read_tools.find_reroute_options).
REROUTE_MAX_OPTIONS: int = int(_SETTINGS.get("reroute", {}).get("max_options", 5))
REROUTE_MAX_ADDED_DELAY_MINUTES: int = int(
    _SETTINGS.get("reroute", {}).get("max_added_delay_minutes", 120)
)
REROUTE_PROPOSAL_TTL_SECONDS: int = int(
    _SETTINGS.get("reroute", {}).get("proposal_ttl_seconds", 300)
)

CHROMA_PATH = Path(os.getenv("CHROMA_PATH", str(BASE_DIR / "data" / "chromadb")))
