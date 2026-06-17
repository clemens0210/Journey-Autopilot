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

### Historische Verspätungs-Referenz (Risk Agent)

Der Risk Agent stützt seine Baseline auf ein echtes Pünktlichkeits-**Archiv** über
Monate. Das committete `journey_autopilot/data/db_delay_reference.json` (~370 kB)
ist vorab aus dem Datensatz
[`piebro/deutsche-bahn-data`](https://github.com/piebro/deutsche-bahn-data)
(echte DB-Halte, **CC BY 4.0**) verdichtet — je Bahnhof und Zugtyp die
Ankunftsverspätungs-Kennzahlen. Zur Laufzeit wird nur diese JSON gelesen (keine
schweren Abhängigkeiten, offline nutzbar).

Neu bauen/aktualisieren (lädt Parquet von Hugging Face, braucht zusätzlich
`pyarrow` und `huggingface_hub`):

```bash
python scripts/build_db_delay_reference.py 2025-08 2025-09 2025-10
```

> **Lizenz/Attribution:** Daten © Deutsche Bahn, bereitgestellt von
> `piebro/deutsche-bahn-data` unter CC BY 4.0. Die `_meta`-Sektion der JSON hält
> Quelle, Lizenz und abgedeckte Monate fest.

## Onboarding & Profil (Web-App)

Das Onboarding läuft als eigene Web-App im **DB-Navigator-Look** (FastAPI +
SQLite, `onboarding/`):

```bash
python run_onboarding.py        # -> http://127.0.0.1:8000
```

Demo-Zugang: `lucas.wild@example.com` / `demo123` (steht auch auf dem
Login-Screen). Der Wizard führt durch: DB-Konto-Login mit Trip-Import →
Mobilnummer-Verifizierung (SMS-Code, simuliert) → Outlook-Kalender (simulierter
OAuth-Consent) → Reisepräferenzen (Klasse, Sitzplatz, Tempo-vs-Komfort) →
Zuhause-Constraints (Heimatbahnhof, späteste Heimkehr, Hotel/Taxi) →
Benachrichtigungen & Autonomie-Level → Zusammenfassung → Dashboard.

- **Pflicht** ist nur der DB-Login; Mobilnummer und Outlook sind überspringbar,
  alle Präferenzen haben Defaults.
- **Simuliert** sind DB-Login/Trip-Import, Microsoft-Consent und SMS-Versand
  (keine offiziellen APIs für ein Uni-Projekt) — die API-Verträge entsprechen
  aber dem, was eine echte Anbindung liefern müsste (Austauschpunkt:
  `onboarding/accounts.py`). Begründung im Context Record.
- **Echt** ist die Heimatbahnhof-Suche: Läuft der `db_service`-Sidecar, kommen
  die Stationsvorschläge live aus der DB-API (grüner Punkt), sonst greift eine
  statische Fallback-Liste.
- **Persistenz:** SQLite unter `data/journey_autopilot.db`. Die Agenten lesen
  das Profil über die Tools `get_user_profile` / `get_upcoming_trips` — der
  Planner gewichtet Reroute-Optionen damit. DSGVO-Löschung mit einem Klick im
  Dashboard.

## Ausführen

Drei Wege, derselbe `root_agent` (der Orchestrator):

```bash
python run_demo.py              # End-to-End-Demo im Terminal, streamt den Agenten-Verlauf
python run_risk_demo.py         # Fokus-Demo: Vorab-Risiko & ETA (Risk Agent)
adk web                         # Dev-UI im Browser — Agent auswählen & chatten
adk run journey_autopilot       # direkt im Terminal, interaktiv
```

`run_demo.py` ist der schnellste Weg, die Zusammenarbeit der Agenten zu
sehen: Es zeigt, wie der Orchestrator zuerst den Monitoring-Agent ruft und —
nur bei erhöhtem Risiko — den Planner nachzieht. `run_risk_demo.py` zeigt den
**Risk Agent** isoliert: Vorab-Verspätungsrisiko (Score 0–100) und prognostizierte
Ankunft (ETA), **bevor** die Reise begonnen hat.

---

## Aktueller Stand (Basis)

Implementiert ist eine erste lauffähige Grundlage mit **drei Spezialisten-Agenten
und einem Orchestrator nach dem ReAct-Muster**. Live-Daten kommen über den
db_service-Sidecar, mit Mock-Fallback wenn er nicht läuft.

- **Orchestrator** (`journey_autopilot/agent.py`, `root_agent`) — `LlmAgent`,
  der die Spezialisten als `AgentTool` einbindet und im ReAct-Loop entscheidet,
  wen er wann ruft. Vor der Reise: Risk Agent (Vorab-Risiko/ETA). Unterwegs:
  erst Monitoring, dann (bei Risiko) Planner.
- **Monitoring Agent** (`monitoring.py`) — liest gemockte Live-Daten und
  Störungslage, gibt ein Risiko-Level (NIEDRIG/MITTEL/HOCH) zurück.
- **Risk Agent** (`risk.py`) — bewertet **vor Reisebeginn** das Verspätungsrisiko
  einer Verbindung und prognostiziert die voraussichtliche Ankunft (Score 0–100 +
  ETA). Zwei Datenquellen: eine **historische Baseline über Monate** (echtes
  Pünktlichkeits-Archiv, [piebro/deutsche-bahn-data](https://github.com/piebro/deutsche-bahn-data),
  vorab verdichtet in `journey_autopilot/data/db_delay_reference.json`) und die
  **aktuelle Lage** der letzten Stunden (DB-Ankunftstafel via Sidecar). Kennzahlen
  rechnet `delay_stats.py` deterministisch; der Agent kombiniert Baseline + heutige
  Abweichung, bewertet und begründet.
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
  risk.py            # Risk Agent (Vorab-Risiko & ETA vor Reisebeginn)
  planner.py         # Planner Agent
  tools.py           # Function-Tools (Live via db_api mit Mock-Fallback)
  mock_data.py       # Fixtures (Demo-Reise München->Berlin, Verspätungs-Historie)
  config.py          # Modell pro Rolle
  db_api.py          # Python-Client für den db_service-Sidecar (einzige DB-Zugriffsstelle)
  delay_stats.py     # Verspätungs-Kennzahlen: Live-Tafel + historisches Archiv (Risk Agent)
  stations.py        # Stationsname -> EVA-Nummer (mit Cache)
  data/              # db_delay_reference.json — vorab gebautes Pünktlichkeits-Archiv
scripts/             # build_db_delay_reference.py — baut data/ aus piebro/deutsche-bahn-data
db_service/          # Node-Sidecar: db-vendo-client als lokale JSON-API
  index.mjs          # Fastify-Server, ein Endpunkt pro Client-Methode
  package.json       # gepinnte Deps (db-vendo-client, fastify)
  README.md          # Endpunkte & Beispiele
onboarding/          # Onboarding & Profil: FastAPI-App im DB-Navigator-Look
  server.py          # JSON-API (Login, Trips, Profil, Stationssuche) + statische UI
  store.py           # SQLite-Store (Profile, Constraints, importierte Reisen)
  accounts.py        # simulierte DB-Konten, Buchungen, Outlook-Termine
  static/            # UI: Wizard + Dashboard (index.html, style.css, app.js)
run_demo.py          # Standalone End-to-End-Demo (Orchestrator)
run_risk_demo.py     # Standalone-Demo des Risk Agent (Vorab-Risiko & ETA)
run_onboarding.py    # Startet die Onboarding-Web-App (Port 8000)
check_db.py          # Smoke-Test der DB-Anbindung
```

## Zielbild (noch offen)

Das System wächst modular entlang der Agentenrollen weiter (siehe
`journey_autopilot_projektgrundlage.md`):

- **Context Capture** — deterministische Function, friert Constraints ein
- **Risk Agent** ✅ — scort Verspätungsrisiko & ETA vor Reisebeginn (Historie)
- **Monitoring Agent** ✅ — pollt Live-Daten, scort Störungsrisiko unterwegs
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
