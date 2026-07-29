/* The one shared state object, plus the wizard's step order.
 *
 * Every module reads and writes this object directly — it is the app's single
 * source of truth, deliberately not encapsulated: a framework-free UI where
 * `render(step)` redraws from state needs the state to be trivially readable.
 * What it must NOT become is a place where the same fact is stored twice.
 */

export const state = {
  token: sessionStorage.getItem("ja_token") || null,
  account: null,
  profile: null,
  trips: [],
  complaints: [],
  complaintId: null, // active detail view
  outlookEvents: [],
  // The onboarding Outlook step ignores a pre-set connection (a demo can
  // pre-connect Outlook so the warm-up runs the real calendar flow) and shows
  // just the sign-in button until the presenter completes it here. This flips
  // true only when the connect flow finishes in this session.
  outlookConnectedThisStep: false,
  step: "welcome",
  editReturn: null, // "profile" = return target after editing
  phone: { sent: false, verifiedThisSession: false },
  chats: {}, // every conversation, keyed by chatKey(trip) — see chat-store.js
  chat: null, // the open conversation: a reference into state.chats, or null
  tripDetail: null, // { trip, data, error } when the trip-detail screen is open
  book: null, // { from, to, departure, results, error } for the Book tab
};

export const STEPS = [
  "welcome", "login", "trips", "phone", "outlook",
  "preferences", "home", "notifications", "summary",
];
