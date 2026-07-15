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

import tls from 'tls'
import Fastify from 'fastify'
import { createClient } from 'db-vendo-client'
import { profile } from 'db-vendo-client/p/dbnav/index.js'

// --- TLS fingerprint workaround (the real cause of HTTP 452 "OPS_BLOCKED") ---
//
// DB's mobile backend sits behind Akamai, which fingerprints the TLS ClientHello
// (JA3/JA4) and instantly rejects clients whose handshake doesn't look like the
// real DB Navigator app — with HTTP 452 + {"code":"OPS_BLOCKED"}, on the very
// first request, regardless of IP. Node's default OpenSSL handshake is one such
// rejected fingerprint (curl's OpenSSL/LibreSSL handshake, by contrast, passes),
// which is why the block was instant and identical across networks.
//
// db-vendo-client tries to mitigate this by reordering TLS ciphers (only when
// DB_PROFILE=db) and pinning ALPN to http/1.1, but as of 6.10.x that is no
// longer enough on its own. Advertising a curl-like signature_algorithms list
// changes the fingerprint enough to get past Akamai again. We patch tls.connect
// (the single choke point every outbound HTTPS connection goes through, incl.
// the library's own https.Agent) to inject it. Env override via DB_TLS_SIGALGS.
const SIGALGS = process.env.DB_TLS_SIGALGS || [
  'ecdsa_secp256r1_sha256', 'rsa_pss_rsae_sha256', 'rsa_pkcs1_sha256',
  'ecdsa_secp384r1_sha384', 'rsa_pss_rsae_sha384', 'rsa_pkcs1_sha384',
  'rsa_pss_rsae_sha512', 'rsa_pkcs1_sha512',
].join(':')
const _tlsConnect = tls.connect.bind(tls)
tls.connect = (...args) => {
  const opt = args[0]
  if (opt && typeof opt === 'object') {
    if (!opt.sigalgs) opt.sigalgs = SIGALGS
    // node-fetch@2 can only read HTTP/1.1; keep ALPN off h2 to match the library.
    opt.ALPNProtocols = ['http/1.1']
  }
  return _tlsConnect(...args)
}


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
    else if (BOOL_KEYS.has(key)) opt[key] = String(value).toLowerCase() === 'true'
    else opt[key] = value
  }
  return opt
}

// --- Routes (one per db-vendo-client method) --------------------------------

// Liveness probe — Python uses this to detect whether the sidecar is up.
app.get('/health', async () => ({ ok: true, profile: 'db-nav', engine: 'db-vendo-client' }))

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

// Refresh one previously discovered journey using its opaque provider token.
// This is the execution-time source of truth; the token is kept server-side.
app.get('/journeys/refresh/:token', async (req) => {
  // Fastify has already URL-decoded path parameters. Decoding a second time
  // corrupts opaque tokens that legitimately contain percent characters.
  return client.refreshJourney(req.params.token, parseOpt(req.query))
})

// Follow a single trip (all stops + realtime). `id` must be URL-encoded.
app.get('/trips/:id', async (req) => client.trip(decodeURIComponent(req.params.id), parseOpt(req.query)))

// Stations near a coordinate.
app.get('/nearby', async (req) => {
  const { latitude, longitude, ...opt } = req.query
  const location = { type: 'location', latitude: Number(latitude), longitude: Number(longitude) }
  return client.nearby(location, parseOpt(opt))
})

// Any error from the DB API surfaces as a clean JSON error for the Python side.
//
// DB's mobile backend (app.services-bahn.de) anti-bot-blocks clients that look
// automated with HTTP 452 + {"code":"OPS_BLOCKED"} — db-vendo-client discards
// that body and throws a bare `Error(res.statusText)` ("Unknown"), so the only
// signal left on our side is the response status. Detect it here and return a
// 503 with a message that actually explains what happened, instead of a
// mysterious "Unknown".
app.setErrorHandler((err, req, reply) => {
  req.log.error(err)
  if (err.response?.status === 452) {
    reply.code(503).send({
      error: 'db_blocked',
      message: 'Deutsche Bahn temporarily blocked this app (anti-bot rate limit, HTTP 452). '
        + 'This clears on its own after a cooldown — avoid hammering the sidecar with rapid '
        + 'requests in the meantime.',
    })
    return
  }
  reply.code(502).send({ error: err.message })
})

app.listen({ port: PORT, host: HOST })
  .then(() => app.log.info(`db-service listening on http://${HOST}:${PORT}`))
  .catch((err) => { app.log.error(err); process.exit(1) })
