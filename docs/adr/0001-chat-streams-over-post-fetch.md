# Both LLM streams run over POST+fetch

## Original decision (2026-06-30): chat over POST+fetch, synthesis stays GET+EventSource

The synthesis panel streams over `GET /stream/synthesis` via `EventSource` (`useSSE`). The
V2 chat **diverges**: it streams over `POST /stream/chat`, consumed by a separate
`useChatStream` hook built on `fetch` + a `ReadableStream` reader. We did this because chat
must carry the **client-held conversation history** up to the server each turn (the server
stays stateless) and must receive a **structured sources side-channel** for citations —
neither of which fits `EventSource`'s GET-only, text-only model. Synthesis has neither need,
so it stays on the simpler EventSource path; the two streaming transports coexist on purpose.
Reversing this (e.g. cramming history into a GET URL) was rejected as fragile (URL limits,
encoding, history leaking into logs).

## Superseded (2026-08-04): synthesis moved to POST+fetch too

The half of that decision that kept synthesis on GET+EventSource is **withdrawn**. It was
argued on ergonomics — synthesis has no history and no side-channel, so the simpler transport
was enough — and that reasoning missed a security property of the method itself: **`GET
/stream/synthesis` was a paid Opus call reachable by cross-site navigation.** The session
cookie is `SameSite=Lax`, which the browser *does* send on a top-level cross-site GET, and
CORS never prevents a request being *processed*, only its response being *read*. So any
third-party page could point a link or an iframe at the endpoint and burn a logged-in user's
daily budget — and for a BYO-key user, who is quota-exempt by design (F4), spend their own
Anthropic key. Nothing about that is visible to the victim, since the attacker never needs
the response.

The fix is structural rather than a token: `POST /stream/synthesis` with the scope
(`limit`, `days`, `source`) as a JSON body. A POST with `Content-Type: application/json` is
not a CORS "simple request", so the browser must preflight it, and the existing allow-list
(`backend/main.py`, restricted to `config.FRONTEND_URL`) already refuses that preflight. The
scope moved into the body deliberately: a form-encoded POST *would* be simple, and simple
means no preflight. A CSRF token was rejected as a second secret to mint, store, and rotate
for a guarantee the method boundary already gives us.

So the two transports have **converged, not diverged further**. The reader loop and SSE frame
parser — `AbortController`, `res.body.getReader()`, the `TextDecoder` buffer splitting on
`\n\n`, and the `event:`/multi-line-`data:` parse — now live once in
`frontend/src/hooks/usePostStream.ts`, which takes a per-event handler map; `useChatStream`
registers a `sources` handler and `useSSE` (the synthesis stream, name kept from its
EventSource days) registers none. The refusal frames are protocol, not per-stream payload, so
the shared client owns both `event: quota` and `event: rate_limited` for everyone.

Two things follow that the original shape could not have. `reportIfAuthExpired` now runs on
the synthesis stream, so a session that dies mid-stream flips the app to the login gate
instead of reading as "Stream connection failed" — `EventSource` exposes no status code, so
this was previously impossible. And "Stop generating" now aborts on the wire rather than
merely closing the reader. The one loss is `EventSource`'s automatic reconnect, which cost
nothing: `useSSE` already called `es.close()` in `onerror`, so it never reconnected anyway.

The original decision's core claim stands and is unchanged: history and structured
side-channels do not fit `EventSource`, and cramming them into a GET URL is still rejected.
