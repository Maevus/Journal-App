# How mood scores are determined

A reference for the mood pipeline: what the number is, exactly how the LLM
produces it, and how it flows to the chart. Code lives in
`backend/services/ingestion.py` (scoring) and `backend/services/llm_client.py`
(the LLM call); the chart reads it via `backend/routers/trends.py`.

## TL;DR

Each entry is scored **once, automatically, in the background, right after it is
ingested** — never at chart-draw time and never during synthesis. The score is a
single float `0.0`–`1.0` (very negative → very positive) produced by an LLM via a
**forced tool call** with a strict schema, then clamped and saved on the entry
row (`JournalEntry.mood_score`). A one-sentence `mood_reasoning` is saved
alongside it.

## When it runs

Scoring is a fire-and-forget task scheduled per **new or content-changed** entry
(unchanged entries are skipped via content-hash dedup, so existing entries are
never re-scored):

- `POST /ingest` → `background_tasks.add_task(score_mood_background, …)` (`routers/ingest.py:56`)
- startup Keep sync → `asyncio.create_task(score_mood_background(…))` (`main.py`)

It runs **after** the ingest response is sent, so ingest never blocks on it, and
it is **best-effort**: any failure is logged and swallowed, leaving
`mood_score = None` (`services/ingestion.py:score_mood_background`).

## The exact LLM call

`score_mood_background` calls `structured_output(...)` with these arguments
(`services/ingestion.py`):

- **Model:** `claude-opus-4-8` (the project `DEFAULT_MODEL`)
- **`max_tokens`:** `512` (a ceiling; a long reasoning truncated at 256 produced
  invalid tool JSON → a validation error, so the cap was raised for headroom)
- **System prompt:**
  > You are an emotional-intelligence expert. Score the mood of a journal entry
  > on a scale from 0.0 (very negative) to 1.0 (very positive). Return a precise
  > float and a one-sentence reasoning.
- **User prompt:**
  > Analyse the mood of this journal entry:
  >
  > `{content[:600]}`
- **Output schema** (`_MoodScore`): `{ score: float, reasoning: str }`

### How the float is actually extracted (not text parsing)

`structured_output` (`services/llm_client.py:90`) does **not** parse free text. It
forces the model to answer through a tool call, which guarantees a typed result:

1. It builds a tool named `structured_extract` whose `input_schema` is
   `_MoodScore.model_json_schema()` — i.e. an object requiring a numeric `score`
   and a string `reasoning`.
2. It calls the Messages API with `tools=[that tool]` **and**
   `tool_choice={"type": "tool", "name": "structured_extract"}` — so the model
   *must* respond by calling the tool with arguments matching the schema.
3. It pulls the `tool_use` block from the response and validates its `.input`
   into the `_MoodScore` Pydantic model (`llm_client.py:122-124`). No regex, no
   JSON-from-prose guessing.
4. Transient errors (rate limit / connection / timeout) retry up to 3× with
   exponential backoff; other errors (e.g. billing) raise immediately.

So "how the model determines the score" is: it reads the first 600 characters of
the entry under the system instruction above and emits a `score` float (plus a
reasoning sentence) as structured tool arguments.

## Write-back

```text
entry.mood_score     = max(0.0, min(1.0, result.score))   # clamp into [0, 1]
entry.mood_reasoning = result.reasoning
```
(`services/ingestion.py`). The clamp guards against the model returning a value
slightly outside the range. When an entry's content is edited, both fields are
reset to `None` so the new text is re-scored.

## What the number means (and doesn't)

- **Scale:** `0.0` = very negative, `1.0` = very positive. Mid-range (~0.5) is
  neutral/mixed.
- **It is a subjective LLM judgment, not a calibrated metric.** Two runs on the
  same text can differ slightly — no `temperature` is pinned and the model uses
  adaptive thinking, so scoring is **not deterministic**. Re-scoring (e.g. after
  an edit, or via the backfill script) can shift a value by a few hundredths.
- **Only the first 600 characters are scored** (`content[:600]`). A long entry is
  judged on its opening, not its whole arc.
- **Failures are silent.** If the call errors (network, billing, etc.), the entry
  keeps `mood_score = None` and simply doesn't appear on the chart until
  re-scored.

## Cost — is the reasoning free?

No, the reasoning costs **extra output tokens** — but it's the smaller half of
the bill, and the `max_tokens` cap is *not* what drives it. The dominant cost is
the **input**, which you pay whether or not you ask for reasoning.

Two real calls from the backfill (Opus 4.8 = $5/1M input, $25/1M output):

| call | input tok | output tok | input $ | output $ | total |
|---|---|---|---|---|---|
| entry 770 | 788 | 115 | $0.0039 | $0.0029 | **$0.0068** |
| entry 816 | 849 | 120 | $0.0042 | $0.0030 | **$0.0073** |

- **Input ≈ $0.004/entry, and it's fixed.** It's ~750–850 tokens: the ~600-char
  entry slice + the system prompt + the `_MoodScore` tool-schema JSON. Identical
  whether or not reasoning is requested.
- **Output ≈ $0.003/entry — this is where reasoning lives.** A bare score is ~5
  tokens (~$0.0001); the one-sentence reasoning is ~110 tokens (~$0.0027). So the
  reasoning is the *majority* of the output.
- **Per-entry total ≈ $0.007** (so ~949 entries ≈ $6–7, which matches the backfill
  spend). Breakdown:

  ```
  score only:        ~$0.004 input + ~$0.0001 output ≈ $0.004
  score + reasoning: ~$0.004 input + ~$0.003   output ≈ $0.007   ← actual
  reasoning premium:                                    ~$0.0025–0.003
  ```
- **The schema already required reasoning**, so every mood call has *always* paid
  for it — we simply used to discard it. Storing it is the same spend, more value.
- **Raising `max_tokens` 256 → 512 does not increase cost.** `max_tokens` is a
  ceiling, not a charge; you pay for tokens actually generated, and reasonings sit
  well under either cap. The bump only prevents truncation.

To make scoring cheaper you'd drop `reasoning` from `_MoodScore` — but that saves
only the ~$0.0025 output premium, not the ~$0.004 input you're already paying.
Adjusting `max_tokens` saves nothing.

## How the score is consumed

- **Mood chart:** `MoodChart` → `useTrends` → `GET /trends`. `trends.py` selects
  entries where `mood_score IS NOT NULL`, filters by day/source, and **averages
  the scores per calendar day** → `[{date, score}]`. No LLM at draw time. If
  there are no scored entries *and* no source filter, it returns a deterministic
  sinusoid stub so the chart isn't empty.
- **Day drill-down:** `GET /entries` returns each entry's `mood_score` and
  `mood_reasoning`; `DayDetail` shows the reasoning as a "Mood read:" line.
- **Synthesis does *not* use the score** — it reads entry *content*, separately.

## Backfilling / re-scoring

Entries created before `mood_reasoning` existed have a score but no reasoning.
`scripts/backfill_mood_reasoning.py` re-scores any entry with
`mood_reasoning IS NULL` (one LLM call each; resumable, per-entry commits). The
**demo** DB doesn't use the LLM at all — `seed_demo.py` hand-sets `mood_score`
and fills canned, score-band `mood_reasoning`.

## Pointers

- Scoring + write-back: `backend/services/ingestion.py` (`_MoodScore`, `score_mood_background`)
- Forced-tool-call mechanism: `backend/services/llm_client.py` (`structured_output`)
- Chart aggregation: `backend/routers/trends.py`
- Per-entry read: `backend/routers/entries.py`
- Related decisions: `docs/decisions.md`
