"""Zentrale Modell-Konfiguration.

Ein Ort, an dem das Modell pro Agentenrolle gesetzt wird — praktisch auch für den
späteren Baseline-Vergleich. Aktuell teilen sich alle Rollen denselben Endpunkt;
sobald sinnvoll, kann hier pro Rolle ein anderes Modell gesetzt werden.

Backend: University of Cologne GPT, ein OpenAI-kompatibler Endpunkt. ADK spricht
ihn über die ``LiteLlm``-Modellklasse an (LiteLLM-Provider ``openai/``).
Voraussetzung: ``pip install -r requirements.txt`` (zieht google-adk[extensions]).
Der ReAct-/Agenten-Code bleibt vom konkreten Modell unberührt — er nutzt nur die
hier gelieferten ``*_MODEL``-Objekte.
"""

from __future__ import annotations
from pathlib import Path

import os

# University of Cologne GPT: OpenAI-kompatibler Endpunkt über LiteLLM.
# Braucht google-adk[extensions] (litellm); fehlt das, liefert ADK eine klare
# ImportError-Meldung mit Installationshinweis.
from google.adk.models.lite_llm import LiteLlm

_UNI_MODEL = os.getenv("UNI_GPT_MODEL", "gpt-oss")
_UNI_BASE_URL = os.getenv("UNI_GPT_BASE_URL")
_UNI_API_KEY = os.getenv("UNI_GPT_API_KEY")


def _uni_model() -> LiteLlm:
    """Frische LiteLlm-Instanz für den Uni-GPT-Endpunkt (eine pro Agent).

    Das Provider-Präfix ``openai/`` sagt LiteLLM: "sprich diesen Endpunkt über
    das OpenAI-Chat-Protokoll an". ``api_base``/``api_key`` kommen aus der .env.
    """
    return LiteLlm(
        model=f"openai/{_UNI_MODEL}",
        api_base=_UNI_BASE_URL,
        api_key=_UNI_API_KEY,
    )


# Ein gemeinsamer Endpunkt für alle Rollen. Sobald die Uni weitere Modelle
# anbietet, kann hier pro Rolle ein anderer Modellname gesetzt werden.
MONITORING_MODEL = _uni_model()
PLANNER_MODEL = _uni_model()
ORCHESTRATOR_MODEL = _uni_model()


BASE_DIR = Path(__file__).resolve().parent

CHROMA_PATH = BASE_DIR / "data" / "chromadb"