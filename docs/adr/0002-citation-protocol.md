# Citation protocol: inline `[n]` markers + a terminal `sources` frame

The chat answer cites entries by emitting inline `[n]` markers in its prose; the backend
streams the answer text, then — before `[DONE]` — emits one `event: sources` SSE frame
carrying the full numbered set of retrieved entries as `{ n, id, date, source, snippet }`.
The frontend renders a clickable chip only for the `[n]` markers that actually appear in the
answer, so the citation set reflects what the model *used*, not everything retrieved. We
chose a single stream carrying both text and structured sources (over a second request, or a
sources-first frame) because it keeps the server stateless — it holds nothing between
requests to serve sources separately — and lets the frontend do the "only-used" filtering
with no extra round-trip. Clicking a citation opens the entry's day in the existing
`DayDetail` component (the finest-grained view that exists today; there is no per-entry view).
