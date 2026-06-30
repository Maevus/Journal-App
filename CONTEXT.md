# Journal App — Context

A personal journal analytics app: it ingests entries (Google Keep, GoodNotes, plain text),
scores mood, and lets the owner explore patterns, search, synthesise, and chat over their
own writing. This glossary pins the terms that are specific to this domain.

## Language

**Entry**:
A single dated piece of the owner's journal, from one source.
_Avoid_: note, record, document.

**Source**:
Where an entry came from — `google_keep`, `goodnotes`, or `text`. Content-hash dedup is
keyed per source.
_Avoid_: provider, origin, channel.

**Synthesis**:
A generated narrative summary over a selected range/source of entries.
_Avoid_: summary, report, digest (those are the export/PDF feature, not this).

**Turn**:
One user question paired with the single grounded answer it produces, in the chat. The
model's internally-rewritten standalone search query is plumbing *inside* a turn, not a
turn. The bot never asks a clarifying question back — every turn is exactly one Q + one A.
_Avoid_: message, exchange, round.

**Conversation**:
The single, ordered list of **Turns** currently on screen in the chat. Exactly one, lives
only in the browser tab, ephemeral (a reload clears it), never persisted or named; a Clear
button resets it.
_Avoid_: thread, session, history.

**Citation**:
A specific retrieved **Entry** the answer grounded a claim on, marked inline in the prose as
`[n]` and rendered as a clickable chip. Clicking a citation opens that entry's day. The
citation *set* for a **Turn** is only the entries the answer actually referenced, not every
retrieved candidate.
_Avoid_: source (overloaded — see **Source** above), reference, footnote.

## Relationships

- An **Entry** has exactly one **Source**.
- A **Synthesis** is generated over many **Entries** (selected by range + source).
- A **Conversation** is an ordered list of **Turns**.
- A **Turn** produces zero or more **Citations**, each pointing at one retrieved **Entry**.

## Example dialogue

> **Dev:** "When the user asks a follow-up, is that a new **Conversation**?"
> **Owner:** "No — same **Conversation**, another **Turn**. It only resets when I hit Clear or
> reload the tab."
> **Dev:** "And a **Citation** — does it point at the day or the exact **Entry**?"
> **Owner:** "At the **Entry** the answer used, but clicking it opens that **Entry**'s day."

## Flagged ambiguities

- "source" was used for both an entry's origin (**Source**) and a chat **Citation** — kept
  **Source** for origin; a chat reference is a **Citation**.
- "session"/"thread" were floated for the chat — rejected in favour of **Conversation**
  (singular, ephemeral, in-tab); "session" stays reserved for future multi-user work.
