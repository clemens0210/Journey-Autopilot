"""Best-effort English rendering of the German bahn.de legal-context chunk.

The passenger-rights knowledge base is crawled from bahn.de and is therefore
German, while the rest of the app speaks English. When a complaint draft is
persisted, the one retrieved clause is translated here via the same Uni-GPT
endpoint the agents use (``UNI_GPT_*`` env vars, LiteLLM ``openai/`` provider —
the direct-completion pattern from ``baseline/single_shot.py``, no ADK import).

Best-effort by design: on any failure — no credentials, endpoint down, empty
answer — the original German text is returned with ``translated=False`` so the
caller (and the UI label) never claims a German quote was translated. The
deterministic compensation logic never depends on this.
"""

from __future__ import annotations

import logging
import os

logger = logging.getLogger(__name__)

# The chunk is a fixed-size extract from a bahn.de page, so it can begin or end
# mid-sentence. The model — not a regex — drops those fragments: German legal
# text is full of abbreviations ("z.B.", "bzw.") and capitalised nouns
# ("(2. Klasse)") that make naive sentence-boundary splitting cut amounts in
# half, whereas the model reliably keeps only whole sentences.
_PROMPT = (
    "The following is an excerpt of German passenger-rights text from bahn.de. "
    "Because it was extracted from a larger page, it may begin or end in the "
    "middle of a sentence. Translate it faithfully and concisely into English, "
    "but OMIT any incomplete sentence fragment at the very start or end — "
    "output only complete sentences. Keep every euro amount and minute figure "
    "exactly as written. Return only the translation, with no preamble or "
    "commentary.\n\n"
)


def translate_to_english(text: str) -> tuple[str, bool]:
    """Translate a German legal-context chunk to English.

    Returns ``(english_text, True)`` on success, or ``(original_text, False)``
    if translation was skipped or failed — the German original is never lost.
    """
    text = (text or "").strip()
    if not text:
        return text, False

    base_url = os.getenv("UNI_GPT_BASE_URL")
    api_key = os.getenv("UNI_GPT_API_KEY")
    model = os.getenv("UNI_GPT_MODEL", "gpt-oss")
    if not base_url or not api_key:
        return text, False

    try:
        from litellm import completion

        response = completion(
            model=f"openai/{model}",
            api_base=base_url,
            api_key=api_key,
            messages=[{"role": "user", "content": _PROMPT + text}],
        )
        content = response.choices[0].message.content
        english = content.strip() if isinstance(content, str) else ""
        if not english:
            return text, False
        return english, True
    except Exception as exc:
        logger.warning(
            "legal-context translation failed (%s: %s) — keeping the German original",
            type(exc).__name__,
            exc,
        )
        return text, False
