# Journey Autopilot

Agentisches System, das DB-Bahnreisen proaktiv überwacht, Störungen erkennt,
Umleitungen plant und Betroffene per WhatsApp benachrichtigt — bei voller
Veto-Kontrolle des Reisenden. Uni-Projekt, gebaut auf **Google ADK 2.x** mit der
University of Cologne GPT als Modell-Backend (OpenAI-kompatibler Endpunkt, via
LiteLLM).

---

## Voraussetzungen

- **Miniconda/Anaconda** oder `venv` — die Python-Version zieht ihr euch in die
  Umgebung, ein systemweites Python ist nicht nötig.
- Python **3.11+** (von ADK 2.0 verlangt).
- **University of Cologne GPT** (OpenAI-kompatibel) — Key, Endpunkt und
  Modellname vom GPT-Service der Uni. ADK spricht den Endpunkt über LiteLLM an,
  der Agenten-Code bleibt davon unberührt.
- **Twilio-Account** (optional) — nur für den WhatsApp Communicator. Sandbox-Zugang
  genügt. Ohne Twilio-Konfiguration läuft `run_demo.py` im Trockenlauf und gibt
  die generierten Nachrichten nur auf der Konsole aus.

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
# .env öffnen und die Werte ausfüllen (UNI_GPT_* Pflicht, Twilio optional)

# 5. .env auch ins Agenten-Verzeichnis kopieren (für adk web / adk run)
cp .env journey_autopilot/.env
```

### LLM-Backend konfigurieren (Uni-Köln-GPT)

```ini
UNI_GPT_API_KEY=dein_uni_key
UNI_GPT_BASE_URL=https://dein-uni-endpunkt/v1   # inkl. /v1
UNI_GPT_MODEL=dein_uni_modellname
```

`config.py` baut daraus `LiteLlm`-Modelle für alle vier Rollen (Orchestrator,
Monitoring, Planner, Drafter). LiteLLM kommt über das `extensions`-Extra in
`requirements.txt` (`google-adk[extensions]`) mit.

### WhatsApp communicator konfigurieren (optional)

```ini
TWILIO_ACCOUNT_SID=ACxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
TWILIO_AUTH_TOKEN=your_auth_token
TWILIO_WHATSAPP_FROM=whatsapp:+14155238886

DEMO_TRAVELER_NUMBER=+49171xxxxxxx   # Muss in Twilio Sandbox registriert sein
DEMO_CLIENT_NUMBER=+49172xxxxxxx
DEMO_COLLEAGUE_NUMBER=+49173xxxxxxx
```

Für eingehende Antworten (YES/NO/EDIT) braucht Twilio eine öffentlich erreichbare
URL. Lokal am einfachsten mit [ngrok](https://ngrok.com):

```bash
ngrok http 8000
# Tunnel-URL in .env als WEBHOOK_BASE_URL eintragen
# In der Twilio Console: Webhook-URL auf https://<tunnel>/whatsapp/reply setzen
```

---

## Ausführen

### Agenten-Demo (Monitoring + Planner)

```bash
python run_demo.py        # streamt Agenten-Verlauf + WhatsApp-Demo im Terminal
adk web                   # Dev-UI im Browser — Agent auswählen & chatten
adk run journey_autopilot # direkt im Terminal, interaktiv
```

`run_demo.py` zeigt zuerst den Orchestrator-Durchlauf (Monitoring → Planner) und
danach — sofern `DEMO_TRAVELER_NUMBER` gesetzt ist — die WhatsApp-Communicator-Demo:
der Drafter-Agent entwirft Nachrichten für jeden konfigurierten Empfänger und
sendet sie (bei vollständiger Twilio-Konfiguration) zur Freigabe an den Reisenden.

### Webhook-Server (WhatsApp-Antworten empfangen)

```bash
uvicorn journey_autopilot.whatsapp_communicator.webhook:app --port 8000
```

Twilio schickt die Antworten des Reisenden (YES / NO / EDIT \<text\>) an
`POST /whatsapp/reply`. Der Server leitet sie an die Genehmigungslogik in
`queue.py` weiter und dispatcht die Nachricht bei Freigabe per Twilio an den
eigentlichen Empfänger.

---

## Aktueller Stand

Implementiert ist eine lauffähige Grundlage mit **drei Spezialisten-Agenten,
einem Orchestrator und einem WhatsApp-Kommunikations-Layer**.

| Komponente | Datei(en) | Beschreibung |
|---|---|---|
| Orchestrator | `agent.py` | `LlmAgent` (ReAct), koordiniert Spezialisten via `AgentTool` |
| Monitoring Agent | `monitoring.py` | Bewertet Störungsrisiko (NIEDRIG / MITTEL / HOCH) |
| Planner Agent | `planner.py` | Reroute-Optionen, Constraint-Check, Fahrgastrechte |
| Drafter Agent | `whatsapp_communicator/drafter.py` | Entwirft rollengerechte WhatsApp-Nachrichten (LlmAgent) |
| Communicator-Tools | `whatsapp_communicator/tools.py` | Sender (Twilio) + Genehmigungs-Queue (in-memory, 5-min-Timeout) |
| Webhook | `whatsapp_communicator/webhook.py` | FastAPI-Endpunkt für YES / NO / EDIT-Antworten |
| Tools & Mock-Daten | `tools.py`, `mock_data.py` | Function-Tools über Fixtures |
| Modell-Konfiguration | `config.py` | LiteLlm-Instanz pro Agentenrolle |

### Datei-Layout

```
journey_autopilot/
  __init__.py                      # macht das Paket für adk auffindbar (root_agent)
  agent.py                         # Orchestrator (root_agent, ReAct)
  monitoring.py                    # Monitoring Agent
  planner.py                       # Planner Agent
  tools.py                         # Function-Tools (gemockt)
  mock_data.py                     # Fixtures (Demo-Reise München→Berlin)
  config.py                        # Modell pro Rolle (UNI_GPT_*)
  whatsapp_communicator/
    __init__.py
    models.py                      # Recipient, DisruptionEvent
    drafter.py                     # Drafter Agent (LlmAgent)
    tools.py                       # Sender (Twilio) + Genehmigungs-Queue
    webhook.py                     # FastAPI-Webhook (YES/NO/EDIT)
run_demo.py                        # End-to-End-Demo (Agenten + WhatsApp)
```

---

## Zielbild (noch offen)

Das System wächst modular entlang der Agentenrollen weiter:

- **Context Capture** — deterministische Funktion, friert Constraints ein
- **Monitoring Agent** ✅ — pollt (gemockte) Live-Daten, scort Störungsrisiko
- **Planner Agent** ✅ — generiert Reroute-Optionen unter Constraints
- **Communicator Agent** ✅ — WhatsApp-Nachrichten mit Freigabe-Workflow (Twilio)
- **Negotiator Agent** — Multi-Stakeholder-Koordination
- **Veto-Gate** — Human-in-the-loop, konfigurierbare Autonomiestufen
- **Booking Agent** — Tickets, Hotels, Mobilitätsoptionen buchen (reversibel)
- **Memory & Learning** — Präferenzen persistent speichern (SQLite)

State: ADK `SessionService` (flüchtig, innerhalb Run) + SQLite (persistente
Präferenzen, harte Constraints, Trip-Historie).

## Vorbehalte

- ADK **2.0** hat Breaking Changes ggü. 1.x (Agent-API, Event-Modell,
  Session-Schema). Viele Tutorials zeigen noch 1.x — auf die Version achten.
- Alle Daten sind bewusst gemockt (kein echter DB-API-Zugang).
- Offizielle Doku: https://google.github.io/adk-docs/ und https://adk.dev/
