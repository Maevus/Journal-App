# Chat streams over POST+fetch while synthesis stays GET+EventSource

The synthesis panel streams over `GET /stream/synthesis` via `EventSource` (`useSSE`). The
V2 chat **diverges**: it streams over `POST /stream/chat`, consumed by a separate
`useChatStream` hook built on `fetch` + a `ReadableStream` reader. We did this because chat
must carry the **client-held conversation history** up to the server each turn (the server
stays stateless) and must receive a **structured sources side-channel** for citations —
neither of which fits `EventSource`'s GET-only, text-only model. Synthesis has neither need,
so it stays on the simpler EventSource path; the two streaming transports coexist on purpose.
Reversing this (e.g. cramming history into a GET URL) was rejected as fragile (URL limits,
encoding, history leaking into logs).
