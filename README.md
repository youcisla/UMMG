# UMMG — Unified Model Memory Gateway

A persistent external memory layer for multiple LLM providers. All models behave
as stateless compute engines behind one shared brain.

```
Claude Desktop / VS Code / curl
        │
        │ POST /v1/chat/completions  (OpenAI shape)
        ▼
┌──────────────────────────────────────────────────────────────┐
│  UMMG Gateway  —  http://127.0.0.1:8787                       │
│                                                              │
│   Router ──► Auth ──► Registry ──► Memory Core ──► Adapter  │
│                                                              │
│   Memory Core (always-on, both directions):                  │
│     • SQLite event log   (data/events.db)                    │
│     • FAISS vector store (data/vectors.faiss)                │
│     • Embeddings         (primary OpenAI-compat, ST fallback)│
│     • Rolling summarizer (background task)                   │
│     • Context packet builder + token truncation              │
│                                                              │
│   Adapters (thin, no memory logic):                          │
│     anthropic ──► 127.0.0.1:8791 (headroom)                  │
│     minimax   ──► 127.0.0.1:8792 (headroom)                  │
│     local     ──► 127.0.0.1:11434 (Ollama)                   │
└──────────────────────────────────────────────────────────────┘
```

## What this gives you

- **One endpoint** (8787) for every model.
- **Memory persists** across all models and across restarts.
- **Switching models does NOT lose context** — the context packet is the same regardless of adapter.
- **OpenAI-compatible API** — works with Claude Desktop, VS Code, any OpenAI client.
- **Streaming** via SSE.
- **Bearer auth** at the gateway.
- **Structured logging** + per-request latency capture.

## Requirements

- Windows 10/11
- Python 3.10+
- `headroom` on PATH (or at `%USERPROFILE%\.local\bin\headroom.exe`)
- Anthropic API key, MiniMax API key
- (Optional) Ollama at `127.0.0.1:11434` for local models

## Install

```powershell
cd C:\Tools\gateway
python -m pip install -r requirements.txt
Copy-Item .env.example .env
notepad .env   # fill in GATEWAY_BEARER_TOKEN, ANTHROPIC_API_KEY, MINIMAX_API_KEY
```

Generate a strong bearer token:

```powershell
python -c "import secrets; print(secrets.token_urlsafe(32))"
```

## Run

```powershell
.\start.ps1
```

Then:

```powershell
curl http://127.0.0.1:8787/health
curl http://127.0.0.1:8787/v1/models

$TOK = (Get-Content .env | Select-String '^GATEWAY_BEARER_TOKEN=').ToString().Split('=',2)[1].Trim()

curl -H "Authorization: Bearer $TOK" -H "Content-Type: application/json" `
     -d '{"model":"claude-sonnet","messages":[{"role":"user","content":"hello"}]}' `
     http://127.0.0.1:8787/v1/chat/completions
```

## Stop

```powershell
.\stop.ps1
```

Memory (`data/`) is preserved across restarts.

## Connect Claude Desktop

Already wired if your `~/.claude/settings.json` contains:

```json
"env": { "ANTHROPIC_BASE_URL": "http://127.0.0.1:8787" }
```

Set `ANTHROPIC_API_KEY` to your `GATEWAY_BEARER_TOKEN` (Claude Desktop sends it as `x-api-key`).

## Connect VS Code

Set in your shell:

```powershell
$env:ANTHROPIC_BASE_URL = "http://127.0.0.1:8787"
$env:ANTHROPIC_API_KEY = "<your GATEWAY_BEARER_TOKEN>"
```

## Models registered

Edit `models.yaml` to add models. Three adapters ship:

| Friendly name  | Adapter    | Forwards to                  |
|----------------|------------|------------------------------|
| `claude-opus`  | anthropic  | headroom 8791 → Anthropic    |
| `claude-sonnet`| anthropic  | headroom 8791 → Anthropic    |
| `minimax-m3`   | minimax    | headroom 8792 → MiniMax      |
| `local-gemma4` | local      | Ollama 11434                 |

Prefix matching also works: `claude-*` → anthropic, `minimax-*` → minimax, `local-*` → local.

## Memory

- **Write path**: user input → SQLite (BEFORE adapter); assistant output → SQLite (AFTER adapter); both embedded + upserted to FAISS.
- **Read path**: user input embedded → top-K FAISS search → latest summary → context packet → prepended to messages.
- **Background summarizer**: every N assistant events, summarizes the last K events via `claude-sonnet` and writes a summary row.
- **Graceful degradation**: if embeddings fail, memory writes still land in SQLite; FAISS search returns empty; the request still serves.

To reset memory: stop the gateway and delete `data/vectors.faiss` + `data/events.db` + `data/vectors.payloads.json`.

## Files

```
gateway/
├── main.py              # entrypoint
├── router.py            # /v1/chat/completions + /health + /v1/models
├── auth.py              # bearer token check
├── config.py            # .env + models.yaml loader
├── registry.py          # model → adapter resolution
├── adapters/            # thin HTTP clients to upstreams
├── memory/              # events, store, embed, retrieve, summarize
├── context/             # packet builder + token truncation
├── observability/       # latency tracker
├── data/                # runtime (gitignored)
├── logs/                # stdout/stderr per process
├── models.yaml
├── .env / .env.example
├── requirements.txt
├── start.ps1
└── stop.ps1
```

## Logs

- `logs/gateway.out.log`, `logs/gateway.err.log`
- `logs/headroom-anthropic.out.log`, `logs/headroom-anthropic.err.log`
- `logs/headroom-minimax.out.log`, `logs/headroom-minimax.err.log`

All structured JSON lines.

## Troubleshooting

**Gateway exits immediately** — check `logs/gateway.err.log`. Most common: missing or placeholder value in `.env`.

**`/health` shows `ok=false` for an upstream** — that headroom process didn't start. Check its `.err.log`. Common causes: bad API key, port already in use.

**`local-*` models return 503** — Ollama isn't running. Start it: `ollama serve`.

**Embedding fallback active** — means headroom's `/v1/embeddings` probe failed at boot. Memory still works; retrieval is local-sentence-transformer based. To restore primary embeddings, ensure `models.yaml` → `memory.embedding.primary_base_url` is reachable.

**Memory grows unbounded** — v1 ships without pruning. Manually delete `data/` if it gets too large.