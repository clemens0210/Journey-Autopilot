/* The only place that talks to the backend.
 *
 * Every request carries the session bearer token, and a non-2xx response is
 * turned into a thrown Error with the server's `detail` — so callers can wrap
 * a call in try/catch and show `err.message` verbatim.
 */

import { state } from "./state.js";

export async function api(path, { method = "GET", body } = {}) {
  const headers = { "Content-Type": "application/json" };
  if (state.token) headers["Authorization"] = `Bearer ${state.token}`;
  const resp = await fetch(path, {
    method,
    headers,
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  const data = await resp.json().catch(() => ({}));
  if (!resp.ok) throw new Error(data.detail || `Error ${resp.status}`);
  return data;
}

export async function saveProfile(patch) {
  const data = await api("/api/profile", { method: "PUT", body: patch });
  state.profile = data.profile;
}
