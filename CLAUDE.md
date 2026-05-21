# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Architecture

This is a journal app with two separate sub-projects:

- **`frontend/`** — React 19 + TypeScript + Vite SPA
- **`backend/`** — FastAPI + uvicorn (Python 3.13 via pyenv), venv at `backend/.venv`

The projects are independent; there is no shared root `package.json` or build script.

## Frontend

```bash
cd frontend
npm run dev      # dev server (Vite HMR)
npm run build    # type-check + production build
npm run lint     # ESLint
```

TypeScript is configured with `noUnusedLocals`, `noUnusedParameters`, and `erasableSyntaxOnly` — the build will fail if unused symbols are left in source.

## Backend

```bash
source backend/.venv/bin/activate
uvicorn main:app --reload   # adjust module path once backend code exists
```

Installed packages: FastAPI 0.136, Pydantic v2, uvicorn, anyio, starlette.

## Skills

This project uses Matt Pocock's agent skills (managed via `skills-lock.json`). Skills live in `.agents/skills/` and include `prototype`, `tdd`, `diagnose`, `improve-codebase-architecture`, `zoom-out`, and others. Run them with `/skill-name` in Claude Code.
