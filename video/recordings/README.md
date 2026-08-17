# Demo recordings

Scripted screen recordings of the running app — **raw**: uncut, no music, no
titles. Each take drives the real UI in Chromium at the phone viewport, with a
synthetic pointer, and captures a CDP screencast that is assembled into an MP4.
The agent turns are real LLM calls, so what the file shows is what the system
actually did on that run.

Output lands in `out/`, three files per take:

| file | what it is |
|---|---|
| `<take>.mp4` | the recording, 430×932 @ 30 fps |
| `<take>.marks.json` | timestamped events (`tap:…`, `replied:…`, `CHANGED:…`) — the index for a later edit |
| `<take>.log.txt` | what the agent actually replied, plus any agent errors |

## The takes

| # | script | what it records |
|---|---|---|
| 1 | `01-onboarding.mjs` | The wizard start to finish: DB login → 3 trips imported → phone + SMS code → Outlook consent → preferences → home → notifications → summary → dashboard. Deliberate changes on the way: **1st class**, **quiet zone on**, **home station München Hbf** (live DB station search), **hotel stay on**, **autonomy → "Automatic within limits"**. |
| 2 | `02-passenger-claim.mjs` | The already-arrived trip DB-FRA-MUC (+128 min): monitor turn settles the rights lookup and seeds a draft → "Review complaint →" → claim detail (€ 39.95 = 50 % of € 79.90) → **Submit complaint** → Submitted. |
| 3 | `03-book-trip.mjs` | Book tab on **live DB data**: station autocomplete → connection search München → Hamburg → pick one → Add trip → open it → autopilot verdict (a genuine open result; this run returned Risk MEDIUM, pre-trip). |
| 4 | `04-demo-trip-reroute.mjs` | The canonical demo trip ICE 528 at +55 min: missed Nuremberg connection → Risk HIGH → reroute options R1/R2 → pick R1 → **veto gate** → approval → rebooking confirmed → agent trace. |

## Running them

```bash
# 1. app + sidecar, from the repo root
python run_onboarding.py                 # :8000
cd db_service && npm start               # :3000  (live DB data)

# 2. state
python scripts/reset_demo.py             # wipe profile/trips/complaints — needed for take 1
python scripts/reset_claims.py           # complaints only — needed to re-run take 2

# 3. record
cd video/recordings && npm install       # once
node 01-onboarding.mjs
node 02-passenger-claim.mjs
node 03-book-trip.mjs
node 04-demo-trip-reroute.mjs
```

Takes 2–4 need an onboarded user, so run take 1 (or any completed onboarding)
first. Take 2 is a no-op on repeat unless `reset_claims.py` runs in between —
`create_draft_complaint` refuses to add a second open draft for the same trip.

## Two things worth knowing

**Onboarding pre-fills the phone field from `DEMO_TRAVELER_NUMBER` in `.env`,
which is a real number.** Take 1 clears it and types a neutral demo number
instead, so no personal data is on camera. The same number also appears in a
"WhatsApp notice sent to …" toast after a HIGH-risk monitor turn — worth
avoiding when cutting any footage from takes 2–4.

**The calendar-clash / notice-email scenario is not recordable without a real
Microsoft login.** The fixture holds a clashing appointment (*Client meeting
Berlin*, confirmed + `hard_constraint`, 23:38, organiser Anna Client, with Sara
Klein and Tom Berger on it), and `classify_window_conflicts` flags it correctly
when handed the events. But no events reach it:

- `MS_ENTRA_CLIENT_ID` **unset** → `is_calendar_connected()` is false and the
  Planner's calendar steps are dropped from its prompt entirely. The fixture
  calendar *is* served on this path — to nobody who is asking.
- `MS_ENTRA_CLIENT_ID` **set** → `get_user_calendar` queries Graph for real and,
  with no cached token, returns `{"events": [], "source": "outlook"}`.

Both variables are read from the same env var
(`tools/read/calendar.py::_calendar_configured`, `auth.py::is_outlook_configured`),
so forcing the simulated Outlook consent in onboarding also switches the agents'
calendar off. The Communicator's email is gated on the Planner reporting a clash
(`orchestrator.py` §6: offer → draft → send), so it can never fire either.

To record it, either run `python scripts/check_outlook.py --login` once and keep
the token cache, or add a fixture fallback in `get_user_calendar` for the case
"configured but Graph returned nothing".

## Model backend

These takes were recorded with all five roles on `uni_gpt`, because
`AWS_BEARER_TOKEN_BEDROCK` was failing authentication at the time
(`BedrockException: Authentication failed`). `config/settings.yaml` has since
been restored to its Bedrock configuration. Re-recording on Claude will change
the wording and the latency of every agent reply — and the write-path protocols
(the veto gate, the three-step email) are long instruction chains that the
stronger models follow more reliably.
