# Kohaku Public Agent Architecture

## 1. Purpose

Kohaku Public Agent is a browser-accessible, multi-session AI Agent
prototype.

The application adds a public product layer around KohakuTerrarium. It
does not replace the KohakuTerrarium runtime.

The application is responsible for:

- Public HTTP and SSE APIs
- Browser chat behavior
- Stable public conversation IDs
- Product metadata
- Runtime mapping
- Persistence diagnostics
- Application lifecycle coordination

KohakuTerrarium remains responsible for:

- Agent execution
- Creature configuration
- Model context
- Tools
- Operational conversation history
- Session persistence
- Session restoration
- Generation interruption

## 2. High-level system

```text
┌──────────────────────────────────────────────────────────────┐
│ Browser                                                      │
│                                                              │
│  index.html + styles.css + app.js                            │
│                                                              │
│  - Session list                                              │
│  - Message history                                           │
│  - Message composer                                          │
│  - SSE consumer                                              │
│  - Stop and AbortController                                  │
└──────────────────────────────┬───────────────────────────────┘
                               │
                               │ HTTP / JSON / SSE
                               ▼
┌──────────────────────────────────────────────────────────────┐
│ FastAPI application                                          │
│                                                              │
│  app/main.py                                                 │
│  app/api/routes.py                                           │
│  app/api/system_routes.py                                    │
│  app/api/schemas.py                                          │
│                                                              │
│  - Request validation                                        │
│  - Public API contracts                                      │
│  - StreamingResponse                                         │
│  - Error translation                                         │
│  - Static frontend hosting                                   │
└──────────────────────────────┬───────────────────────────────┘
                               │
                               ▼
┌──────────────────────────────────────────────────────────────┐
│ AgentService                                                 │
│                                                              │
│  app/agent_service.py                                        │
│                                                              │
│  - Public/session mapping                                    │
│  - Session lifecycle                                         │
│  - Per-session locking                                       │
│  - Busy state                                                │
│  - Streaming                                                 │
│  - Interruption                                              │
│  - Recovery                                                  │
│  - Deletion                                                  │
│  - Shutdown coordination                                     │
└─────────────────────┬───────────────────────┬────────────────┘
                      │                       │
                      ▼                       ▼
┌─────────────────────────────┐  ┌────────────────────────────┐
│ SQLite / SQLAlchemy         │  │ KohakuTerrarium Studio     │
│                             │  │                            │
│ Product metadata            │  │ Agent runtime              │
│ Runtime binding             │  │ Creature                   │
│ Recovery state              │  │ Model and tools            │
│ Deletion state              │  │ Context and history        │
└─────────────────────────────┘  │ .kohakutr persistence      │
                                 └────────────────────────────┘
```

## 3. Frontend layer

The frontend is a native HTML, CSS and JavaScript single-page
application.

### Files

```text
frontend/
├─ index.html
├─ styles.css
└─ app.js
```

### Responsibilities

The frontend manages:

- Conversation selection
- Message rendering
- Local message drafts
- Per-conversation generation jobs
- SSE parsing
- Progressive assistant-text display
- Stop requests
- Client-side stream cancellation
- Browser-visible error states

The frontend only uses public conversation IDs.

It must never rely on:

- Studio session IDs
- Creature IDs
- Filesystem paths
- `.kohakutr` filenames
- Database row IDs

## 4. FastAPI layer

The FastAPI layer translates public HTTP behavior into AgentService
operations.

### Main components

```text
app/main.py
app/api/routes.py
app/api/system_routes.py
app/api/schemas.py
app/api/dependencies.py
```

### Responsibilities

- Validate JSON request bodies
- Validate path parameters
- Resolve the application-wide AgentService
- Convert internal exceptions into HTTP responses
- Construct SSE events
- Filter internal output protocol markers
- Return public response schemas
- Host static frontend assets

The API layer does not directly create Studio sessions or execute SQL.

## 5. AgentService layer

`AgentService` is the main application service layer.

It owns the coordination between the public API, the application
database and KohakuTerrarium.

### Managed session concept

Each active public conversation is represented in memory by a managed
session containing information such as:

```text
public conversation ID
Studio session ID
Creature ID
workspace path
creation time
per-session asyncio lock
busy state
```

### Per-session lock

Each conversation has its own turn lock.

This provides:

```text
One active generation per conversation
+
Concurrent generation across different conversations
```

It prevents two simultaneous requests from mutating the same model
context.

### Public ID mapping

The browser uses a stable public conversation ID.

AgentService maps that ID to current runtime data:

```text
public_id
→ database record
→ Studio session ID
→ Creature ID
→ .kohakutr session path
```

Studio and Creature IDs may change after restoration. The public ID
remains stable.

## 6. Persistence architecture

The project deliberately uses two persistence layers with different
responsibilities.

### SQLite product registry

SQLite stores:

- Stable public ID
- Ownership metadata
- Title
- Workspace
- Session path
- Current runtime binding
- Recovery state
- Deletion state
- Timestamps

### KohakuTerrarium session persistence

KohakuTerrarium stores:

- Conversation events
- Model context
- Agent state
- Creature state
- Tool state
- Operational history

This information is persisted in `.kohakutr` files.

### Why the responsibilities are separated

The application should not create a second, competing copy of the full
Agent conversation state.

SQLite answers:

```text
Which public product conversation is this?
Where is its runtime state?
Is it active, deleted or recovery-failed?
```

KohakuTerrarium answers:

```text
What has the Agent seen?
What does the model remember?
What happened during previous turns?
How should the Agent resume?
```

## 7. Conversation creation

```text
POST /api/v1/sessions
→ Validate request
→ Generate stable public ID
→ Create workspace
→ Start Creature through Studio
→ Resolve actual Studio session ID
→ Resolve actual Creature ID
→ Discover real .kohakutr file
→ Store mapping in SQLite
→ Add managed session to memory
→ Return public session response
```

The application does not invent a `.kohakutr` path in advance. It
records the actual file produced by KohakuTerrarium.

## 8. Streaming message flow

```text
Browser submits message
→ POST /messages/stream
→ FastAPI validates request
→ AgentService resolves public conversation
→ Acquire per-session turn lock
→ Set is_generating=true
→ Inject message into KohakuTerrarium
→ KohakuTerrarium calls the model
→ Model produces chunks
→ AgentService yields chunks
→ API removes internal protocol markers
→ API sends SSE token events
→ Browser decodes and renders tokens
→ API sends done
→ AgentService resets is_generating=false
→ Release turn lock
```

### SSE events

The public stream supports:

```text
start
token
done
error
```

A successful normal stream must include:

```text
start
+
at least one visible token
+
done
```

## 9. Interruption flow

The browser-equivalent stop flow is:

```text
SSE connection established
→ Session enters is_busy=true
→ Browser sends POST /interrupt
→ Server returns was_busy=true
→ Browser aborts its fetch stream
→ KohakuTerrarium interruption propagates
→ AgentService leaves generation
→ finally resets is_generating=false
→ Session returns is_busy=false
```

The interruption smoke test distinguishes a real interruption from a
model response that simply completed too quickly.

`was_busy=false` is not silently counted as proof of successful active
interruption.

## 10. Restart recovery

Application startup performs:

```text
Start database
→ Query active conversation records
→ Check .kohakutr files
→ Start persistent Studio
→ Resume each Kohaku session
→ Resolve current Studio and Creature IDs
→ Repair stale runtime bindings
→ Rebuild managed-session registry
```

The stable public ID remains unchanged even when internal runtime IDs
change.

Recovery failures are recorded and exposed through the system-status
endpoint and persistence doctor.

## 11. Deletion

Deletion separates product state from runtime cleanup.

```text
Resolve public conversation
→ Prevent new work
→ Interrupt active generation
→ Acquire turn lock
→ Mark database row deleted
→ Stop Studio session
→ Delete Kohaku session family
→ Remove workspace when requested
→ Remove managed session from memory
```

The product row remains available for consistency diagnostics while its
active runtime resources are removed.

## 12. Application lifecycle

FastAPI lifespan owns the application-wide AgentService.

### Startup

```text
Create AgentService
→ Start database
→ Configure KT_SESSION_DIR
→ Start Studio
→ Restore conversations
→ Store service in app.state
```

### Shutdown

```text
Mark service closing
→ Interrupt active generations
→ Wait for per-session locks
→ Stop Studio sessions
→ Close Studio
→ Close process-wide session index
→ Restore environment variables
→ Close database
```

Closing the process-wide session index releases:

```text
.kt-index.kvault
```

This is especially important on Windows, where open file handles can
prevent temporary runtime directories from being removed.

## 13. Headless Agent configuration

The Public Assistant is controlled through FastAPI and AgentService.

It therefore uses:

```yaml
input:
  type: none
```

It must not inherit CLI input.

CLI input may start a blocking `sys.stdin.readline()` operation in an
executor thread. Such a thread can survive after the smoke-test body has
finished and prevent Python interpreter shutdown.

The effective inherited configuration is tested automatically.

## 14. Tool-format compatibility

The Public Assistant uses:

```yaml
controller:
  tool_format: bracket
```

The current model integration reserves the provider-native function
name `python`.

Bracket formatting allows the inherited tools to remain available
without registering that reserved native function name.

## 15. Process-exit monitoring

A Live smoke test can print its success message before the Python
interpreter has fully exited.

The validation runner therefore uses:

```text
scripts/run_process_with_timeout.py
```

The monitor requires:

```text
Required success marker observed
+
Child exit code is zero
+
Complete child process exits inside grace period
```

It detects:

- Overall test timeout
- Missing success marker
- Non-zero child exit
- Interpreter hang after success
- Residual child-process trees

A timeout is a real validation failure. Forced termination is used only
for cleanup after failure and is never reported as success.

## 16. Validation architecture

### Quick

- Compilation
- JavaScript syntax
- Whitespace
- Non-live tests
- Effective Agent configuration
- Monitor safety tests
- Session-index shutdown
- Persistence doctor

### Live

- Real model call
- Multi-turn context
- AgentService reconstruction
- Restart recovery
- Real interpreter exit

### SSE

- HTTP health
- Persistence health
- Normal streaming
- Active interruption
- Client cancellation
- Final idle recovery

### All

```text
Quick → Live → SSE
```

## 17. Current security boundary

The project currently has no production user-identity or authorization
layer.

The system must not be exposed directly to the public internet.

Missing production controls include:

- Authentication
- Authorization
- User isolation
- Rate limiting
- Quotas
- Tool sandboxing
- Secret management
- HTTPS termination
- Audit logging
- Content moderation
- Distributed locks
- Multi-worker generation ownership

These controls are planned for later releases.