# Kohaku Public Agent

A multi-session public AI Agent prototype built with
KohakuTerrarium, FastAPI, Server-Sent Events and SQLite.

The project is an application layer around KohakuTerrarium. It provides
a browser chat interface and a stable HTTP API while delegating Agent
execution, model context and operational session history to
KohakuTerrarium.

## Current version

`v0.5.0` — persistent conversation and restart recovery prototype.

## Current capabilities

- General-purpose AI conversation
- Multi-turn conversation context
- Multiple independent conversations
- Concurrent generation across different conversations
- One active generation per conversation
- Server-Sent Events streaming
- Per-conversation interruption
- Conversation history retrieval
- Output protocol marker filtering
- Browser-side background generation
- Stable public conversation IDs
- SQLite product metadata
- Persistent KohakuTerrarium `.kohakutr` sessions
- Server restart recovery
- Soft deletion and workspace cleanup
- Database and persistence health inspection
- Automated and live smoke tests

## Architecture

```text
Browser frontend
    |
    | HTTP / SSE
    v
FastAPI application
    |
    v
AgentService
    |-----------------------------|
    |                             |
    v                             v
SQLite product registry     KohakuTerrarium Studio
                                  |
                                  v
                           public-assistant Creature
                                  |
                                  v
                              LLM provider
```

### Responsibility boundaries

The application SQLite database stores:

- Public conversation ID
- User ownership metadata
- Conversation title
- Workspace path
- Kohaku session file path
- Current Studio session ID
- Current Creature ID
- Recovery and deletion status

KohakuTerrarium stores:

- Agent operational state
- Model conversation context
- Conversation events and history
- Tool and Creature state
- Persistent `.kohakutr` session files

The frontend never receives the internal Studio session ID,
Creature ID or filesystem paths.

## Repository layout

```text
app/
├─ api/                  FastAPI routes and schemas
├─ persistence/          SQLite database and repository layer
├─ agent_service.py      Long-running Studio and session manager
├─ main.py               FastAPI application and lifespan
├─ models.py             Internal public session model
└─ smoke_*.py            Integration smoke tests

creatures/
└─ public-assistant/
   ├─ config.yaml
   └─ prompts/
      └─ system.md

frontend/
├─ index.html
├─ styles.css
└─ app.js

scripts/
├─ inspect_studio_persistence.py
├─ persistence_doctor.py
└─ smoke_sse_client.py

tests/
├─ capability/
├─ integration/
└─ safety/
```

## Prerequisites

- Windows 10 or Windows 11
- Python 3.12
- `uv`
- Git
- A configured KohakuTerrarium-supported model provider

The current development dependency uses a local editable
KohakuTerrarium checkout. Keep both repositories next to each other:

```text
C:\AgentProjects\
├─ KohakuTerrarium\
└─ public-agent-lab\
```

## Installation

Open PowerShell in the project directory:

```powershell
cd C:\AgentProjects\public-agent-lab
```

Synchronize dependencies:

```powershell
uv sync --system-certs
```

Install this Agent package in editable mode:

```powershell
uv run kt install . -e
```

Check installed packages:

```powershell
uv run kt list
```

The output should include:

```text
kohaku-public-agent-lab
Creatures: public-assistant
```

Configure and authenticate an LLM provider through
KohakuTerrarium before running live Agent requests.

## Run the application

```powershell
uv run uvicorn app.main:app `
  --reload `
  --host 127.0.0.1 `
  --port 8000
```

Open:

```text
http://127.0.0.1:8000/
```

API documentation:

```text
http://127.0.0.1:8000/docs
```

## Main API endpoints

### System

```text
GET /health
GET /api/v1/system/status
```

### Conversations

```text
POST   /api/v1/sessions
GET    /api/v1/sessions
GET    /api/v1/sessions/{session_id}
DELETE /api/v1/sessions/{session_id}
```

### Messages

```text
POST /api/v1/sessions/{session_id}/messages
POST /api/v1/sessions/{session_id}/messages/stream
POST /api/v1/sessions/{session_id}/interrupt
GET  /api/v1/sessions/{session_id}/history
```

## Runtime data

Local runtime state is stored under:

```text
runtime/
├─ data/
│  └─ public_agent.db
├─ kohaku_sessions/
│  ├─ *.kohakutr
│  └─ .kt-index.kvault
└─ workspaces/
   └─ <public_id>/
```

The entire `runtime/` directory is excluded from Git.

Do not commit:

- SQLite databases
- `.kohakutr` files
- User workspaces
- Authentication tokens
- API keys
- OAuth files
- `.env` files

## Persistence behavior

Creating a conversation performs the following operations:

```text
Create stable public ID
→ Create isolated workspace
→ Start KohakuTerrarium Creature
→ Resolve the real .kohakutr session file
→ Write the public-to-runtime mapping into SQLite
```

Application restart performs:

```text
Start SQLite
→ Start persistent Studio
→ Read active conversation mappings
→ Resume each .kohakutr session
→ Refresh live Studio and Creature IDs
→ Return restored conversations to the browser
```

Deleting a conversation:

```text
Interrupt active generation
→ Mark the SQLite row as deleted
→ Stop the live Studio session
→ Delete the Kohaku session family
→ Optionally remove the workspace
```

## Persistence diagnostics

Run the read-only persistence doctor:

```powershell
uv run python scripts/persistence_doctor.py
```

JSON output:

```powershell
uv run python scripts/persistence_doctor.py --json
```

Return a non-zero exit code when anomalies exist:

```powershell
uv run python scripts/persistence_doctor.py --strict
```

The doctor reports:

- Active conversations
- Recovery failures
- Missing session files
- Orphan session files
- Deleted rows with files remaining

It does not modify the database or filesystem.

## Tests

Compile Python files:

```powershell
uv run python -m compileall app scripts tests
```

Run all non-live tests:

```powershell
Remove-Item Env:RUN_LIVE_AGENT_TESTS `
  -ErrorAction SilentlyContinue

uv run python -m unittest discover `
  -s tests `
  -t . `
  -v
```

Run SQLite persistence tests:

```powershell
uv run python -m unittest `
  tests.capability.test_persistence `
  -v
```

Run restart recovery tests without a model request:

```powershell
uv run python -m unittest `
  tests.integration.test_restart_recovery `
  -v
```

Run the real long-running AgentService smoke test:

```powershell
uv run python -m app.smoke_service
```

Run the real restart recovery smoke test:

```powershell
uv run python -m app.smoke_restart_recovery
```

Run the SSE client smoke test while Uvicorn is running:

```powershell
uv run python scripts/smoke_sse_client.py
```

## Model and tool compatibility

The current public assistant inherits the general Creature configuration
from `kt-biome`.

The Creature uses bracket tool format because the current Codex-backed
model reserves the provider-native function name `python`.

This preserves inherited tools without registering `python` as a native
provider function.

## Development status

This repository is a development prototype, not a production-ready
public service.

The current version does not yet provide:

- User registration and login
- Real multi-user authorization
- Rate limiting
- Per-user model quotas
- Production secret management
- File upload
- Document analysis
- Web search with citations
- Tool approval UI
- Production database migrations
- PostgreSQL or Redis
- Horizontal scaling
- HTTPS deployment
- Production monitoring
- Content moderation pipeline

Do not expose the current local server directly to the public internet.

## Roadmap

### v0.6 — user identity and isolation

- User model
- Authentication
- Conversation ownership enforcement
- Session cookies or tokens
- Per-user quotas

### v0.7 — files and document analysis

- Secure uploads
- File type and size validation
- Per-user storage isolation
- TXT, Markdown, PDF and DOCX analysis

### v0.8 — web research

- Web search
- Page retrieval
- Source citations
- Research Agent

### v0.9 — tools and approvals

- Structured tool events
- User approval workflow
- Audit records
- Safe code and data tools

### v1.0 — production deployment

- PostgreSQL
- Redis
- Docker
- HTTPS
- Monitoring
- Rate limits
- Usage accounting
- Production model credentials

## License and attribution

This repository contains the application code developed for the
Kohaku Public Agent project.

KohakuTerrarium and `kt-biome` are independent upstream projects and
retain their own licenses and copyright notices.