// Journey Autopilot — db-vendo-client sidecar
//
// A thin HTTP wrapper around db-vendo-client. Our Python backend is the brain;
// db-vendo-client is Node-only, so we run it as a small local service and let
// Python talk to it over HTTP. This is the same engine the (now offline) public
// v6.db.transport.rest ran — we just host it ourselves so we are not at the
// mercy of someone else's rate limits.
//
// Each route maps 1:1 to a db-vendo-client method, so "all functions" are
// available and adding a new one is a few lines. The data source is DB's own
// "dbnav" (DB Navigator) profile — i.e. the numbers match the DB Navigator app.

import Fastify from 'fastify'
import { createClient } from 'db-vendo-client'
import { profile } from 'db-vendo-client/p/dbnav/index.js'


const PORT = Number(process.env.DB_SERVICE_PORT || 3000)
const HOST = process.env.DB_SERVICE_HOST || '127.0.0.1'
// DB asks unofficial clients to identify themselves with a contact/app string.
const USER_AGENT = process.env.DB_USER_AGENT || 'journey-autopilot (self-hosted db-vendo-client)'

const client = createClient({ ...profile, randomizeUserAgent: true }, USER_AGENT)
const app = Fastify({ logger: true })

// Query strings arrive as strings; db-vendo-client expects real Dates, numbers
// and booleans for its options. Coerce the well-known option keys, pass the
// rest through untouched.
const DATE_KEYS = new Set(['when', 'departure', 'arrival'])
const NUMBER_KEYS = new Set(['results', 'duration', 'transfers', 'transferTime', 'n'])
const BOOL_KEYS = new Set([
  'stopovers', 'tickets', 'remarks', 'subStops', 'entrances', 'polylines',
  'arrivals', 'departures', 'nationalExpress', 'national', 'regionalExpress',
  'regional', 'suburban', 'bus', 'ferry', 'subway', 'tram', 'taxi',
])

const parseOpt = (query = {}) => {
  const opt = {}
  for (const [key, value] of Object.entries(query)) {
    if (DATE_KEYS.has(key)) opt[key] = new Date(value)
    else if (NUMBER_KEYS.has(key)) opt[key] = Number(value)
    else if (BOOL_KEYS.has(key)) opt[key] = value === 'true'
    else opt[key] = value
  }
  return opt
}

// --- Routes (one per db-vendo-client method) --------------------------------

// Liveness probe — Python uses this to detect whether the sidecar is up.
app.get('/health', async () => ({ ok: true, profile: 'dbnav', engine: 'db-vendo-client' }))

// Name -> station list (each result carries the EVA number as `id`).
app.get('/locations', async (req) => {
  const { query, ...opt } = req.query
  return client.locations(query, parseOpt(opt))
})

// Live departure board for a station (EVA id). Carries delays + platform changes.
app.get('/departures/:id', async (req) => client.departures(req.params.id, parseOpt(req.query)))

// Live arrival board for a station (EVA id).
app.get('/arrivals/:id', async (req) => client.arrivals(req.params.id, parseOpt(req.query)))

// Routing between two stations. With ?tickets=true it also returns prices.
app.get('/journeys', async (req) => {
  const { from, to, ...opt } = req.query
  return client.journeys(from, to, parseOpt(opt))
})

// Follow a single trip (all stops + realtime). `id` must be URL-encoded.
app.get('/trips/:id', async (req) => client.trip(decodeURIComponent(req.params.id), parseOpt(req.query)))

// Stations near a coordinate.
app.get('/nearby', async (req) => {
  const { latitude, longitude, ...opt } = req.query
  const location = { type: 'location', latitude: Number(latitude), longitude: Number(longitude) }
  return client.nearby(location, parseOpt(opt))
})

// Any error from the DB API surfaces as a clean 502 JSON for the Python side.
app.setErrorHandler((err, req, reply) => {
  req.log.error(err)
  reply.code(502).send({ error: err.message })
})

app.listen({ port: PORT, host: HOST })
  .then(() => app.log.info(`db-service listening on http://${HOST}:${PORT}`))
  .catch((err) => { app.log.error(err); process.exit(1) })
