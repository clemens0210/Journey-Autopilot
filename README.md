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
- **Node.js 18+** — nur für den DB-Live-Daten-Sidecar (`db_service/`). Wer ohne
  echte DB-Daten arbeitet (Mock-Modus), braucht Node nicht.

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

# 5. DB-Live-Daten-Sidecar vorbereiten (Node) — optional, nur für echte DB-Daten
cd db_service && npm install && cd ..
```

### Backend konfigurieren (Uni-Köln-GPT)

In der `.env` werden Key, Endpunkt und Modellname hinterlegt:

```ini
UNI_GPT_API_KEY=dein_uni_key
UNI_GPT_BASE_URL=https://dein-uni-endpunkt/v1   # inkl. /v1
UNI_GPT_MODEL=dein_uni_modellname # e.g Openai GPT OSS 120B
```

`config.py` baut daraus `LiteLlm`-Modelle für alle drei Rollen — am ReAct-Code
(`agent.py`, `monitoring.py`, `planner.py`) ändert sich nichts. LiteLLM kommt
über das `extensions`-Extra in `requirements.txt` (`google-adk[extensions]`) mit.

> **Hinweis zur `.env`:** ADK lädt die `.env` aus dem jeweiligen
> **Agenten-Verzeichnis**. Am einfachsten ist es, die fertige `.env` in den/die
> Agenten-Ordner zu kopieren (`cp .env <agent>/.env`). Findet `adk` die Datei
> nicht, hilft ein Blick in die aktuelle ADK-Doku zur `.env`-Discovery.

### DB-Live-Daten (db_service-Sidecar)

Echte DB-Live-Daten (Verspätungen, Routing, Preise) kommen über
[`db-vendo-client`](https://github.com/public-transport/db-vendo-client) — eine
Node-Bibliothek mit DB-Navigator-genauen Daten. Da unser Backend Python ist,
läuft sie als kleiner **Sidecar** (`db_service/`): ein lokaler JSON-Dienst, den
die Python-Seite über HTTP anspricht.

```
[ ADK-Agenten ] -> tools.py -> db_api.py --HTTP--> db_service (Node) -> DB
```

Sidecar starten (eigenes Terminal):

```bash
cd db_service && npm start      # läuft auf http://127.0.0.1:3000
```

Anbindung testen (Sidecar muss laufen):

```bash
python check_db.py              # Health + EVA-Auflösung + eine Verbindung
```

Endpunkte und Optionen stehen in `db_service/README.md`. Der Python-Client wird
über `DB_API_URL` / `DB_API_TIMEOUT` in der `.env` konfiguriert.

> **Stand:** Sidecar und Python-Client (`db_api.py`, `stations.py`) sind fertig
> und eigenständig testbar. Die Agenten laufen aktuell noch auf `mock_data` —
> `tools.py` wird im nächsten Schritt auf `db_api` umgestellt.

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
- **DB-Live-Daten-Sidecar** (`db_service/`, `db_api.py`, `stations.py`) — echte
  DB-Daten über `db-vendo-client`; eigenständig testbar via `check_db.py`. Noch
  nicht in die Tools verdrahtet (siehe Hinweis oben).

### Datei-Layout

```
journey_autopilot/
  __init__.py        # macht das Paket für adk auffindbar (root_agent)
  agent.py           # Orchestrator (root_agent, ReAct)
  monitoring.py      # Monitoring Agent
  planner.py         # Planner Agent
  tools.py           # Function-Tools (aktuell gemockt)
  mock_data.py       # Fixtures (Demo-Reise München->Berlin)
  config.py          # Modell pro Rolle
  db_api.py          # Python-Client für den db_service-Sidecar (einzige DB-Zugriffsstelle)
  stations.py        # Stationsname -> EVA-Nummer (mit Cache)
db_service/          # Node-Sidecar: db-vendo-client als lokale JSON-API
  index.mjs          # Fastify-Server, ein Endpunkt pro Client-Methode
  package.json       # gepinnte Deps (db-vendo-client, fastify)
  README.md          # Endpunkte & Beispiele
run_demo.py          # Standalone End-to-End-Demo
check_db.py          # Smoke-Test der DB-Anbindung
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
