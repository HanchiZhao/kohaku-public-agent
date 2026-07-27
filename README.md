# Kohaku Public Agent

A multi-session public AI Agent prototype built with
KohakuTerrarium, FastAPI, Server-Sent Events, SQLAlchemy and SQLite.

Kohaku Public Agent provides a browser chat interface and a stable
public HTTP API. It delegates Agent execution, model context, tools,
operational history and resumable session state to KohakuTerrarium.

## Current version

`v0.5.1` — stability, regression protection and developer
documentation release.

## Current capabilities

- General-purpose AI conversation
- Multi-turn conversation context
- Multiple independent conversations
- Concurrent generation across different conversations
- One active generation per conversation
- Server-Sent Events streaming
- Per-conversation interruption
- Browser-equivalent stream cancellation
- Conversation history retrieval
- Output protocol marker filtering
- Browser-side background generation
- Stable public conversation IDs
- SQLite product metadata
- Persistent KohakuTerrarium `.kohakutr` sessions
- Server restart and model-context recovery
- Legacy runtime-binding repair
- Soft deletion and workspace cleanup
- Database and persistence health inspection
- Headless Agent input configuration
- Windows process-shutdown regression protection
- Unified Quick, Live, SSE and All validation modes

## Architecture overview

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

Detailed architecture documentation is available at:

```text
docs/architecture.md
```

## Responsibility boundaries

### Application SQLite database

The application database stores product-level metadata:

- Stable public conversation ID
- Ownership metadata
- Conversation title
- Workspace path
- Kohaku session-file path
- Current Studio session ID
- Current Creature ID
- Recovery status
- Deletion status
- Creation and update timestamps

### KohakuTerrarium

KohakuTerrarium stores and manages:

- Agent operational state
- Model conversation context
- Conversation events and history
- Creature state
- Tool state
- Persistent `.kohakutr` session files
- Studio sessions
- Generation interruption
- Session restoration

The frontend never receives internal Studio session IDs, Creature IDs,
session-file paths or workspace paths.

## Repository layout

```text
app/
├─ api/
│  ├─ dependencies.py
│  ├─ routes.py
│  ├─ schemas.py
│  └─ system_routes.py
├─ persistence/
│  ├─ database.py
│  ├─ records.py
│  ├─ repository.py
│  └─ tables.py
├─ agent_service.py
├─ main.py
├─ models.py
└─ smoke_*.py

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
├─ run_process_with_timeout.py
├─ run_v05_checks.ps1
└─ smoke_sse_client.py

tests/
├─ capability/
│  ├─ test_models.py
│  ├─ test_persistence.py
│  ├─ test_public_assistant_config.py
│  └─ test_service_guards.py
├─ integration/
│  ├─ test_agent_service_live.py
│  ├─ test_api.py
│  ├─ test_fronted.py
│  ├─ test_index_shutdown.py
│  ├─ test_restart_recovery.py
│  └─ test_system_status.py
└─ safety/
   └─ test_process_timeout_runner.py
```

## Prerequisites

- Windows 10 or Windows 11
- Python 3.12
- `uv`
- Git
- Node.js for JavaScript syntax checks
- A configured KohakuTerrarium-supported model provider
- A local KohakuTerrarium checkout

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
Set-Location "C:\AgentProjects\public-agent-lab"
```

Synchronize dependencies:

```powershell
uv sync --system-certs
```

Install the Agent package in editable mode:

```powershell
uv run kt install . -e
```

Check installed Kohaku packages:

```powershell
uv run kt list
```

The output should include:

```text
kohaku-public-agent-lab
Creatures: public-assistant
```

Configure and authenticate a supported LLM provider through
KohakuTerrarium before running Live or SSE validation.

## Environment variables

The repository contains:

```text
.env.example
```

It documents the application-specific environment variables.

Important:

> The application currently does not automatically load `.env` files.

`.env.example` is a reference template. Values must be supplied through
PowerShell, the operating-system environment or the deployment
environment.

Example PowerShell configuration:

```powershell
$env:PUBLIC_AGENT_DATABASE_URL = `
    "sqlite+aiosqlite:///./runtime/data/public_agent.db"

$env:KT_SESSION_DIR = `
    "C:\AgentProjects\public-agent-lab\runtime\kohaku_sessions"
```

Both variables are optional during normal local development because the
application provides project-local defaults.

## Run the application

Start Uvicorn:

```powershell
uv run uvicorn app.main:app `
    --reload `
    --host 127.0.0.1 `
    --port 8000
```

Open the browser application:

```text
http://127.0.0.1:8000/
```

Open API documentation:

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

The following files must never be committed:

- `.env`
- API keys
- Authentication tokens
- OAuth credentials
- SQLite databases
- `.kohakutr` files
- `.kvault` files
- User workspaces
- Local source snapshots
- Local handoff archives

## Persistence behavior

Creating a conversation performs:

```text
Generate stable public ID
→ Create isolated workspace
→ Start KohakuTerrarium Creature
→ Resolve the real .kohakutr file
→ Store public-to-runtime mapping in SQLite
```

Application restart performs:

```text
Start SQLite
→ Start persistent Studio
→ Read active conversation mappings
→ Resume each .kohakutr session
→ Refresh Studio and Creature IDs
→ Repair stale runtime bindings
→ Return restored conversations to the browser
```

Deleting a conversation performs:

```text
Interrupt active generation
→ Mark the SQLite row as deleted
→ Stop the live Studio session
→ Delete the Kohaku session family
→ Remove the workspace when requested
```

## Persistence diagnostics

Run the read-only persistence doctor:

```powershell
uv run python scripts/persistence_doctor.py
```

Produce JSON output:

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
- Deleted rows with session files remaining

The doctor does not modify the database or filesystem.

## Unified validation runner

The validation entry point remains named:

```text
scripts/run_v05_checks.ps1
```

The filename is retained for compatibility with the v0.5 release family.
The script validates the current v0.5.1 codebase.

### Quick mode

```powershell
powershell -ExecutionPolicy Bypass `
    -File .\scripts\run_v05_checks.ps1 `
    -Mode quick
```

Quick mode performs:

- Python compilation
- JavaScript syntax validation
- Git whitespace validation
- Complete non-live unittest discovery
- Public Assistant effective-config regression tests
- Process-monitor safety tests
- Session-index shutdown regression
- Strict persistence diagnostics

Quick mode does not require Uvicorn or a live model request.

### Live mode

```powershell
powershell -ExecutionPolicy Bypass `
    -File .\scripts\run_v05_checks.ps1 `
    -Mode live
```

Live mode performs:

- Real AgentService model smoke test
- Real multi-turn context validation
- Real restart recovery validation
- Model-context recovery after service reconstruction
- Full child-process exit monitoring

Each Live smoke test must:

```text
Print its success marker
+
Return exit code 0
+
Terminate the complete Python process within the exit grace period
```

A smoke-test body printing success is not sufficient by itself.

Optional timeout overrides:

```powershell
powershell -ExecutionPolicy Bypass `
    -File .\scripts\run_v05_checks.ps1 `
    -Mode live `
    -LiveOverallTimeoutSeconds 900 `
    -LiveExitGraceSeconds 30
```

### SSE mode

Start Uvicorn in a separate terminal:

```powershell
uv run uvicorn app.main:app `
    --reload `
    --host 127.0.0.1 `
    --port 8000
```

Then run:

```powershell
powershell -ExecutionPolicy Bypass `
    -File .\scripts\run_v05_checks.ps1 `
    -Mode sse
```

SSE mode validates:

- `/health`
- `/api/v1/system/status`
- Normal SSE start/token/done behavior
- Real active-generation interruption
- `/interrupt` returning `was_busy=true`
- Browser-equivalent client-stream cancellation
- Final recovery to `is_busy=false`

### All mode

With Uvicorn running in another terminal:

```powershell
powershell -ExecutionPolicy Bypass `
    -File .\scripts\run_v05_checks.ps1 `
    -Mode all
```

All mode runs:

```text
Quick
→ Live
→ SSE
```

Any failed command, failed assertion, persistence anomaly, timeout or
non-zero exit code fails the complete validation.

## Public Assistant configuration contracts

The Public Assistant inherits:

```yaml
base_config: "@kt-biome/creatures/general"
```

It explicitly overrides two important settings.

### Headless input

```yaml
input:
  type: none
```

The web application supplies user messages programmatically through
FastAPI and AgentService. It must not inherit terminal CLI input.

CLI input can start a blocking `sys.stdin.readline()` executor thread.
On Windows, that thread can prevent the Python interpreter from exiting
after a smoke-test body has already completed.

The effective configuration is protected by an automated regression
test.

### Bracket tool format

```yaml
controller:
  tool_format: bracket
```

The current model integration reserves the provider-native function name
`python`. Bracket formatting preserves inherited tools without
registering that name as a provider-native function.

## Shutdown behavior

Application shutdown performs:

```text
Reject new work
→ Interrupt active generations
→ Wait for per-session locks
→ Stop Studio sessions
→ Close Studio
→ Close the process-wide Kohaku session index
→ Restore environment variables
→ Close the database
```

Closing the process-wide session index is required to release:

```text
.kt-index.kvault
```

The Live validation runner starts the smoke tests as child processes and
requires each complete Python process to exit after reporting success.

This protects against interpreter-exit hangs caused by residual threads,
executors or open runtime resources.

## Development safety boundary

This repository is a development prototype, not a production-ready
public service.

The current version does not provide:

- User registration or login
- Real multi-user authorization
- Conversation ownership enforcement
- Rate limiting
- Per-user model quotas
- Production secret management
- File upload
- Document-analysis isolation
- Tool approval UI
- Sandboxed code execution
- PostgreSQL
- Redis
- Horizontal scaling
- HTTPS termination
- Production monitoring
- Content moderation pipeline
- Backup and disaster-recovery automation

Do not expose the current Uvicorn server directly to the public
internet.

The current recommended use cases are:

- Local development
- Controlled internal testing
- Private-network demonstrations
- Architecture and persistence experiments

## Release workflow

The expected release workflow is:

```text
Create feature branch
→ Run Quick during development
→ Run Live after runtime changes
→ Start Uvicorn
→ Run All before release
→ Review git diff
→ Commit exact files
→ Push feature branch
→ Create Pull Request
→ Review Files changed
→ Merge into main
→ Pull main locally
→ Run post-merge Quick
→ Create annotated tag
→ Publish GitHub Pre-release
```

Do not use `git add .` when unrelated local files may be present.

## Roadmap

### v0.6 — identity and access isolation

- User model
- Authentication
- Conversation ownership
- Authorization enforcement
- Secure session cookies
- Per-user limits

### v0.7 — quotas and cost controls

- Rate limiting
- Concurrent-generation limits
- Token accounting
- Request budgets
- Storage quotas

### v0.8 — files and document analysis

- Secure uploads
- File ownership
- File-size and type validation
- Workspace isolation
- PDF, DOCX, TXT, Markdown and CSV processing

### v0.9 — safe tools and approvals

- Structured tool events
- User approval workflow
- Tool-call audit records
- Sandboxed code and data tools

### v1.0 — controlled production deployment

- PostgreSQL
- Redis
- HTTPS
- Reverse proxy
- Backups
- Monitoring
- Usage accounting
- Production credential management

## License and attribution

This repository contains application code developed for the
Kohaku Public Agent project.

KohakuTerrarium and `kt-biome` are independent upstream projects and
retain their own licenses and copyright notices.