# Journey Autopilot

Agentisches System, das DB-Bahnreisen proaktiv überwacht, Störungen erkennt,
Umleitungen plant und den Nutzer transparent informiert — bei voller
Veto-Kontrolle. Uni-Projekt, gebaut auf **Google ADK 2.x** mit der University of
Cologne GPT als Modell-Backend (OpenAI-kompatibler Endpunkt, via LiteLLM).

---

## Voraussetzungen

- **Miniconda/Anaconda** oder `venv` — die Python-Version zieht ihr euch in die
  Umgebung, ein systemweites Python ist nicht nötig.
- Python **3.11+** (von ADK 2.0 verlangt).
- **University of Cologne GPT** (OpenAI-kompatibel) — Key, Endpunkt und
  Modellname vom GPT-Service der Uni. ADK spricht den Endpunkt über LiteLLM an,
  der Agenten-Code bleibt davon unberührt.

## Setup

```bash
# 1. Ins Projektverzeichnis
cd journey-autopilot

# 2. Umgebung anlegen & aktivieren (Conda)
conda create -n journey-autopilot python=3.11
conda activate journey-autopilot
#   ... oder venv:
#   python -m venv .venv
#   source .venv/bin/activate        # Windows: .venv\Scripts\activate

# 3. Abhängigkeiten (google-adk liegt nur auf PyPI → per pip)
pip install -r requirements.txt

# 4. Zugang hinterlegen
cp .env.example .env        # Windows (PowerShell): copy .env.example .env
# .env öffnen und die UNI_GPT_*-Werte ausfüllen (siehe unten)
```

### Backend konfigurieren (Uni-Köln-GPT)

In der `.env` werden Key, Endpunkt und Modellname hinterlegt:

```ini
UNI_GPT_API_KEY=dein_uni_key
UNI_GPT_BASE_URL=https://dein-uni-endpunkt/v1   # inkl. /v1
UNI_GPT_MODEL=dein_uni_modellname
```

`config.py` baut daraus `LiteLlm`-Modelle für alle drei Rollen — am ReAct-Code
(`agent.py`, `monitoring.py`, `planner.py`) ändert sich nichts. LiteLLM kommt
über das `extensions`-Extra in `requirements.txt` (`google-adk[extensions]`) mit.

> **Hinweis zur `.env`:** ADK lädt die `.env` aus dem jeweiligen
> **Agenten-Verzeichnis**. Am einfachsten ist es, die fertige `.env` in den/die
> Agenten-Ordner zu kopieren (`cp .env <agent>/.env`). Findet `adk` die Datei
> nicht, hilft ein Blick in die aktuelle ADK-Doku zur `.env`-Discovery.

## Ausführen

Drei Wege, derselbe `root_agent` (der Orchestrator):

```bash
python run_demo.py              # End-to-End-Demo im Terminal, streamt den Agenten-Verlauf
adk web                         # Dev-UI im Browser — Agent auswählen & chatten
adk run journey_autopilot       # direkt im Terminal, interaktiv
```

`run_demo.py` ist der schnellste Weg, die Zusammenarbeit der beiden Agenten zu
sehen: Es zeigt, wie der Orchestrator zuerst den Monitoring-Agent ruft und —
nur bei erhöhtem Risiko — den Planner nachzieht.

---

## Aktueller Stand (Basis)

Implementiert ist eine erste lauffähige Grundlage mit **zwei Spezialisten-Agenten
und einem Orchestrator nach dem ReAct-Muster**. Daten sind bewusst gemockt.

- **Orchestrator** (`journey_autopilot/agent.py`, `root_agent`) — `LlmAgent`,
  der die Spezialisten als `AgentTool` einbindet und im ReAct-Loop entscheidet,
  wen er wann ruft. Ruft immer erst Monitoring, dann (bei Risiko) Planner.
- **Monitoring Agent** (`monitoring.py`) — liest gemockte Live-Daten und
  Störungslage, gibt ein Risiko-Level (NIEDRIG/MITTEL/HOCH) zurück.
- **Planner Agent** (`planner.py`) — generiert Reroute-Optionen, prüft sie gegen
  harte Termine (Kalender) und nennt Fahrgastrechte. Schlägt vor, bucht nicht.
- **Tools & Mock-Daten** (`tools.py`, `mock_data.py`) — Function-Tools über
  Fixtures; die Einstecksstellen für echte DB-/Kalender-/RAG-Quellen.
- **Modell-Konfiguration** (`config.py`) — ein Ort, an dem das Modell pro Rolle
  gesetzt wird; spricht den Uni-Köln-GPT (OpenAI-kompatibel) via LiteLLM an.

### Datei-Layout

```
journey_autopilot/
  __init__.py        # macht das Paket für adk auffindbar (root_agent)
  agent.py           # Orchestrator (root_agent, ReAct)
  monitoring.py      # Monitoring Agent
  planner.py         # Planner Agent
  tools.py           # Function-Tools (gemockt)
  mock_data.py       # Fixtures (Demo-Reise München->Berlin)
  config.py          # Modell pro Rolle
run_demo.py          # Standalone End-to-End-Demo
```

## Zielbild (noch offen)

Das System wächst modular entlang der Agentenrollen weiter (siehe
`journey_autopilot_projektgrundlage.md`):

- **Context Capture** — deterministische Function, friert Constraints ein
- **Monitoring Agent** ✅ — pollt (gemockte) Live-Daten, scort Störungsrisiko
- **Planner Agent** ✅ — generiert Reroute-Optionen unter Constraints (RAG)
- **Negotiator Agent** — Multi-Stakeholder-Koordination
- **Veto-Gate** — Human-in-the-loop, Nutzer behält Veto
- **Communicator Agent** — Notifications (WhatsApp/Outlook)

State: ADK `SessionService` (flüchtig, innerhalb Run) + SQLite (persistente
Präferenzen, harte Constraints, Trip-Historie).

## Vorbehalte

- ADK **2.0** hat Breaking Changes ggü. 1.x (Agent-API, Event-Modell,
  Session-Schema). Viele Tutorials zeigen noch 1.x — auf die Version achten.
- Daten werden bewusst gemockt (kein echter DB-API-Zugang) — als ADR und im
  Context Record festhalten.
- Offizielle Doku: https://google.github.io/adk-docs/ und https://adk.dev/
