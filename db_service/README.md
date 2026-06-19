# db-service — Deutsche Bahn live-data sidecar

A small Node service that wraps [`db-vendo-client`](https://github.com/public-transport/db-vendo-client) and exposes Deutsche Bahn live data (delays, routing, prices) as a local JSON API. The Python backend (`journey_autopilot/rerouting/db_api.py`) talks to it over HTTP.

**Why a sidecar?** `db-vendo-client` is Node-only, our backend is Python. Rather than depend on the (currently offline) public `v6.db.transport.rest`, we host the same engine ourselves. Data comes from DB's `dbnav` profile, so the numbers match the DB Navigator app.

## Run

```bash
cd db_service
npm install        # once
npm start          # serves on http://127.0.0.1:3000
```

`db-vendo-client` is pinned exactly in `package.json`; keep the generated
`package-lock.json` committed so everyone runs the same DB client version.

## Configuration (env vars)

| Variable | Default | Meaning |
|---|---|---|
| `DB_SERVICE_PORT` | `3000` | Port to listen on |
| `DB_SERVICE_HOST` | `127.0.0.1` | Bind address |
| `DB_USER_AGENT` | `journey-autopilot (...)` | Identifier sent to DB |

## Endpoints

| Method & path | db-vendo-client call | Notes |
|---|---|---|
| `GET /health` | — | Liveness probe |
| `GET /locations?query=` | `locations()` | Name → stations; `id` is the EVA number |
| `GET /departures/:id` | `departures()` | Live board; supports `when`, `duration`, `results` |
| `GET /arrivals/:id` | `arrivals()` | Live board |
| `GET /journeys?from=&to=` | `journeys()` | Routing; add `tickets=true` for prices |
| `GET /trips/:id` | `trip()` | Follow one train; `id` URL-encoded |
| `GET /nearby?latitude=&longitude=` | `nearby()` | Stations near a coordinate |

Query params are coerced automatically: `when/departure/arrival` → Date,
`results/duration/transfers` → number, transport-mode flags + `tickets/stopovers/remarks` → boolean.

### Examples

```bash
curl 'http://127.0.0.1:3000/locations?query=Köln%20Hbf&results=1'
curl 'http://127.0.0.1:3000/departures/8000207?duration=30'
curl 'http://127.0.0.1:3000/journeys?from=8000207&to=8011160&results=5&tickets=true'
```

## Adding a function

`db-vendo-client` exposes more methods (e.g. `reachableFrom`, `radar`). To expose one, add a route in `index.mjs` mirroring the existing ones — pass `parseOpt(req.query)` as the options argument.

> ⚠️ Unofficial API. Be polite with request volume and keep `db-vendo-client` pinned (see `package.json`).
