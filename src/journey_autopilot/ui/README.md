# Web App — Onboarding, Profile & Trip Chat

The user-facing web app in the **look & feel of the DB Navigator**. It covers
the **onboarding flow** (log in with a DB account, import trips, capture
preferences and hard constraints, set notifications and autonomy level), the
**dashboard** (monitored trips + profile), and a **per-trip chat** that runs
the ReAct orchestrator live. The **user profile** created during onboarding is
the knowledge base against which the Journey Autopilot evaluates replanning.

This package is the **presentation layer only**. The onboarding *logic* —
simulated DB accounts/trips and the SQLite profile store — lives next door in
[`journey_autopilot/onboarding/`](../onboarding/) and is imported here. The
chat endpoint runs [`journey_autopilot.agent.root_agent`](../agent.py), the
same orchestrator as [`scenarios/happy_path.py`](../../../scenarios/happy_path.py).

> **Why a standalone app instead of a real DB Navigator plugin?** DB does not
> offer third parties an official API/extension interface for account login or
> ticket import. We therefore recreate the UX as a standalone app in the
> DB Navigator design — the backends are simulated, but the API contracts
> match what a real integration would need to deliver. Background in
> [`CONTEXT_RECORD.md`](../../CONTEXT_RECORD.md) → *Onboarding & Profile*.

---

## Quick start

```bash
# from the project root directory, with the environment activated
pip install -r requirements.txt        # FastAPI, uvicorn, requests, pydantic
python run_onboarding.py               # -> http://127.0.0.1:8000
```

Open in browser: **http://127.0.0.1:8000** — ideally in mobile device view
(DevTools → Responsive), so the app looks like the DB Navigator on a phone.

**Demo login** (also shown directly on the login screen):

| Email                       | Password  | Account                                 |
| -------------------------- | --------- | --------------------------------------- |
| `lucas.wild@example.com`   | `demo123` | BahnCard 50, BahnBonus Gold · 3 trips   |
| `erika.muster@example.com` | `demo123` | BahnCard 25, BahnBonus Silver · 1 trip  |

Optionally start the **DB live data sidecar** beforehand, so the home station
search uses real DB station data instead of the fallback list:

```bash
cd db_service && npm install && npm start    # serves /locations on :3000
```

Configuration via environment variables:

| Variable          | Default                          | Purpose                                |
| ----------------- | --------------------------------- | -------------------------------------- |
| `ONBOARDING_HOST` | `127.0.0.1`                      | Bind host of the web app               |
| `ONBOARDING_PORT` | `8000`                           | Port of the web app                    |
| `DB_API_URL`      | `http://127.0.0.1:3000`          | Endpoint of the `db_service` sidecar   |
| `JA_DB_PATH`      | `data/journey_autopilot.db`      | SQLite file (covered by `.gitignore`)  |

---

## The onboarding flow

| # | Step               | Required? | Content                                                                       |
| - | ----------------- | -------- | ------------------------------------------------------------------------------ |
| 0 | **Welcome**       | –        | value proposition, privacy notice                                               |
| 1 | **DB account**    | Required | bahn.de login → imports trips + BahnCard/BahnBonus                              |
| 2 | **Trips**         | –        | overview of imported bookings (monitored from now on)                          |
| 3 | **Mobile number** | optional | SMS code verification for alerts via SMS/WhatsApp                              |
| 4 | **Outlook**       | optional | calendar consent (Microsoft) → protects hard appointments during replanning    |
| 5 | **Preferences**   | –        | class, seat, quiet zone, speed-vs-comfort, max. transfers                      |
| 6 | **Home**          | –        | home station (live autocomplete), latest return time, hotel/taxi OK            |
| 7 | **Notifications** | –        | channels, quiet hours, and **autonomy level** (notify only / approve / auto)   |
| 8 | **Summary**       | –        | completion → dashboard                                                          |

The DB login is the only requirement (without imported trips there's nothing to
monitor). All other steps are skippable or pre-filled with sensible
defaults — the wizard never blocks. Each step only saves the changed
part of the profile as a patch (`PUT /api/profile`); aborting and continuing
later is possible at any time.

After completion, you land on the **dashboard** (DB Navigator main view with
a mocked tab bar *Book / Explore / Trips / Profile*): monitored trips,
profile summary, connections, and the GDPR deletion option.

### Automation & veto (the policy layer)

From the dashboard or profile, **Automation & veto** (`renderers.policy`) lets
the user set the global autonomy level (conservative / balanced / automatic
within limits) and pin per-action overrides (auto / ask, plus a cost limit for
rebookings). The choices are stored in the profile's `policy` block via
`PUT /api/profile` and read by `journey_autopilot.policy.resolve()`, which the
Executor's write tools call before every side-effecting action — so a gated
action pauses for the user's veto in the chat. The onboarding autonomy choice
seeds the global level. See `docs/adr/0004-veto-gate.md`.

### Trip chat (the live agent demo)

**Tapping a monitored trip** on the dashboard opens a chat in DB Navigator
style. Every message is sent to `POST /api/chat`, which runs the ReAct
orchestrator (`journey_autopilot.agent.root_agent`) — the same flow as
[`scenarios/happy_path.py`](../../../scenarios/happy_path.py). The selected trip is added as context on the
first message, so the orchestrator monitors it, and (on elevated risk) calls the
planner for reroutes and checks the calendar. The reply is shown as a chat
bubble with a collapsible **agent trace** (which agent called which tool).

> Lucas' first trip (Munich → Berlin) is the canonical demo scenario — pinned to
> the same `trip_id`/date as `mock_data.DEMO_TRIP`, so it triggers the full
> disruption → reroute → calendar story. Requires a configured Uni-GPT backend
> in `.env` (`UNI_GPT_*`); without it, the chat shows the backend error inline.

---

## What's real, what's simulated?

| Feature                   | Status        | Note                                                                    |
| ------------------------- | ------------- | ---------------------------------------------------------------------- |
| Home station search       | **live**\*    | real DB station data via the `db_service` sidecar, otherwise fallback list |
| Profile persistence       | **real**      | SQLite (`../persistence/store.py`)                                      |
| DB account login & import | *simulated*   | `../onboarding/accounts.py` — no official DB API available             |
| SMS verification          | *simulated*   | no SMS gateway; the code is returned and displayed inline (delivered via Twilio WhatsApp when configured) |
| Outlook calendar (OAuth)  | **live**\*\*  | real MS Entra device-code flow when `MS_ENTRA_CLIENT_ID` is set; simulated consent dialog + sample events otherwise |

\* only if the sidecar is running. A green dot in front of a result marks
live data; without the sidecar, a static list of major stations is used.

\*\* a cached Microsoft login (from an earlier connect or
`python scripts/check_outlook.py --login`) is reused silently — the wizard's
Outlook step then connects instantly without a device-code round trip. For a
clean wizard run-through (e.g. before a demo), reset the stored profile with
`python scripts/reset_demo.py` — it keeps the Outlook login cached.

The simulations are deliberately kept behind realistic API contracts. A real
integration would ideally only need to swap out `../onboarding/accounts.py` and
the affected endpoints — the rest (UI, store, profile structure) remains untouched.

---

## Architecture

The presentation layer (`ui/`) is kept separate from the onboarding logic
(`onboarding/`) and the SQLite store (`persistence/`): the UI imports the logic,
never the other way around.

```
journey_autopilot/
├── ui/                  ← this package (everything user-facing)
│   ├── server.py        FastAPI app: JSON API, /api/chat, serving the static UI
│   ├── chat.py          runs the ReAct orchestrator (root_agent) per chat turn
│   ├── __init__.py      package docs
│   └── static/
│       ├── index.html   DB Navigator frame: status bar, header, tab bar, DB logo
│       ├── style.css    DB Navigator dark theme (DB red #EC0016, dark slate surfaces)
│       └── app.js       framework-free UI: render(step), wizard patches, trip chat
├── onboarding/          ← the logic (the "functions"), imported by ui/
│   ├── accounts.py      simulated DB accounts, bookings, Outlook events, station fallback
│   └── __init__.py
└── persistence/
    └── store.py         SQLite store: users, profile (JSON blob), imported trips
```

- **No build step, no JS framework.** The UI is vanilla JS; `render(step)`
  draws the respective screen, the bottom navigation is configured per
  step, and the chat reuses the same `render`/state mechanism.
- **Sessions** live in-memory (token → `user_id`). A restart simply means
  “log in again” — deliberately without persistence for the single-user prototype.
- **Chat sessions** are kept by ADK's in-memory runner (`chat.py`); ADK and the
  agent graph are imported lazily, so the onboarding flow runs even without the
  agent dependencies installed.
- **Profile** is stored as a single JSON blob per user (prototype-friendly,
  no migrations). The structure including defaults is in
  [`store.py`](../persistence/store.py) → `DEFAULT_PROFILE`.

### Connection to the agents

`journey_autopilot.tools` reads the same SQLite via
`persistence.store.any_profile()` (single-user: “the most recently maintained profile”).
The Planner agent ranks reroute options against this profile — without a FastAPI
dependency; the only shared touch point is the store. The chat goes the other
way: `ui/chat.py` drives `journey_autopilot.agent.root_agent` directly.

---

## API overview

| Method & path                     | Purpose                                                      |
| --------------------------------- | ----------------------------------------------------------- |
| `POST /api/auth/db-login`         | Simulated login → token + account + imported trips           |
| `GET  /api/me`                    | Account, profile, and trips of the current session           |
| `GET  /api/trips`                 | Imported trips                                                |
| `POST /api/verify/phone/start`    | Request SMS code (demo: code is returned in the response)    |
| `POST /api/verify/phone/confirm`  | Confirm code                                                  |
| `POST /api/chat`                  | Run one orchestrator turn for a trip → reply + agent trace    |
| `POST /api/connect/outlook`       | Grant Outlook consent → sample appointments                   |
| `DELETE /api/connect/outlook`     | Disconnect Outlook connection                                  |
| `GET  /api/profile`               | Read profile                                                  |
| `PUT  /api/profile`               | Merge a partial patch into the profile (per onboarding step) |
| `POST /api/onboarding/complete`   | Mark onboarding as completed                                   |
| `DELETE /api/profile`             | **GDPR deletion:** remove account, profile, and trips         |
| `GET  /api/stations?query=`       | Station search (live via sidecar, otherwise fallback)         |

Protected endpoints expect the `Authorization: Bearer <token>` header from the
login. Interactive API docs at **/docs** (FastAPI/Swagger).

---

## Design

The UI copies the appearance of the **DB Navigator (dark mode)**:

- **DB logo** as a red frame mark (SVG) in the header, trip cards, and on the
  welcome screen.
- **DB red `#EC0016`** as the brand and action color, dark slate surfaces,
  white text, green status dots.
- **Trip cards** in the Navigator layout: DB logo + train, start/destination
  markers with dot/pin icons, date/time, QR tile.
- **Mocked frame** (not part of onboarding, purely visual): iOS status
  bar at the top and the tab bar *Book / Explore / Trips / Profile* at the bottom.

The house font “DB Sans” requires a license; without a license, the
system font fallback is used.

---

## Privacy

- **Data minimization:** only the fields needed for onboarding are captured;
  everything is stored locally in the SQLite file.
- **Full control:** profile viewable and editable in the dashboard at any time.
- **Right to erasure (GDPR Art. 17):** one click on *Delete profile & data*
  irrevocably removes the account, profile, and imported trips
  (`DELETE /api/profile`).
