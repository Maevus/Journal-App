## Objective
Build the ingestion pipeline for two journal sources: Google Keep 
(via gkeepapi) and GoodNotes (via iCloud Drive folder watch).

## Context
- Backend is FastAPI + SQLModel + Python 3.13
- JournalEntry model already exists in models/journal.py
- Ingestion service stub exists at services/ingestion.py
- .env already has CLAUDE_API_KEY
- User is on Mac, uses iCloud Drive, no 2FA on Google account

## Target State
Two working ingestion sources in services/ingestion.py:

1. Google Keep ingester
   - Auth via gkeepapi as per their docs, using credentials from .env https://gkeepapi.readthedocs.io/
   - Fetch all notes, normalise to JournalEntry schema
   - Content hash dedup — skip entries already in DB
   - Store source as "google_keep"

2. GoodNotes ingester  
   - Watch a configurable folder path (GOODNOTES_ICLOUD_PATH in .env)
   - Detect new/changed PDF or image files via file hash
   - If file has extractable text layer — extract it directly
   - If image/handwritten — send to Gemini vision API for OCR transcription
   - Normalise to JournalEntry schema
   - Store source as "goodnotes"

Both ingesters called from POST /ingest endpoint with a source 
parameter: "keep", "goodnotes", or "all"

## .env additions needed
- check .env files for new keys and credentials needed for the ingesters, such as:
  - GOOGLE_EMAIL
  - GOOGLE_MASTER_TOKEN
  - GOODNOTES_ICLOUD_PATH

## Scope
- Work only in services/ingestion.py and routers/ingest.py
- Do NOT modify JournalEntry schema
- Do NOT touch frontend

## Constraints
- gkeepapi for Keep access
- watchdog for folder monitoring
- pypdf for text layer extraction
- Claude vision for OCR — use existing llm_client.py with claude-3-5-sonnet-20241022
- Add new deps to requirements.txt
- Content hash dedup is mandatory — ingestion must be idempotent
- NEVER DELETE SOURCE CONTENT (e.g from Keep or the .pdfs from GoodNotes) — only read and process

## Acceptance Criteria
- [ ] POST /ingest?source=keep fetches and stores Keep notes
- [ ] POST /ingest?source=goodnotes processes files from iCloud path
- [ ] Re-running ingestion does not create duplicate entries
- [ ] Handwritten pages are transcribed via Claude vision
- [ ] All credentials read from .env, never hardcoded

## Stop Conditions
Stop and ask before:
- Adding any dependency not listed above
- Modifying JournalEntry schema
- Any file operations outside the iCloud path

## Progress
After each step: ✅ [what was done] — [file(s) affected]