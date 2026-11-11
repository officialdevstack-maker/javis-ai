# javis-ai — TODO

A local, private Jarvis-style assistant. Everything runs on this machine.

**Four roles** (built on one shared core, Phases 0–8):
1. **Personal:** email, SMS, WhatsApp, calls, calendar
2. **Business:** moderate and run websites / online businesses
3. **Dev:** help build, or fully create, websites and mobile apps
4. **Life:** goals, habits, reviews

Plus a **trading** module (research, journaling, backtesting, paper trading first).

**Stack:** Ollama (Llama) · Python 3.11+ / FastAPI · Vite + React + TypeScript · SQLite · Chroma

**Rules for Copilot (apply to every task):**
- Do one task at a time. Don't start the next until the acceptance check passes.
- Backend: type hints everywhere, Pydantic models for all request/response bodies, `async` endpoints, config from `.env` only (no hardcoded values).
- Frontend: TypeScript strict mode, no `any`. Run `npx tsc --noEmit` before calling a task done.
- Nothing leaves the machine: no cloud APIs, no telemetry.
- Any tool that writes, sends, or deletes must require explicit user confirmation.

---

## Target layout

```
javis-ai/
├─ backend/
│  ├─ app/
│  │  ├─ main.py            # FastAPI app, CORS, routers
│  │  ├─ config.py          # settings from .env
│  │  ├─ llm.py             # Ollama client (chat, stream, tool calls)
│  │  ├─ agent.py           # tool-calling loop
│  │  ├─ db.py              # SQLite setup + queries
│  │  ├─ memory.py          # long-term facts
│  │  ├─ rag.py             # ingest + search (Chroma)
│  │  ├─ scheduler.py       # APScheduler jobs
│  │  ├─ scopes.py          # private / public / business
│  │  ├─ autonomy.py        # permission tiers, audit log, kill switch
│  │  ├─ connectors/        # gmail, sms, whatsapp, calls, github, market data...
│  │  └─ tools/
│  │     ├─ __init__.py     # tool registry
│  │     ├─ files.py
│  │     ├─ notes.py
│  │     └─ ...
│  ├─ requirements.txt
│  └─ .env.example
├─ frontend/                # Vite + React + TS
└─ TODO.md
```

---

## Phase 0 — Environment

- [ ] Install Ollama and pull a model: `ollama pull llama3.1:8b` (use `llama3.2:3b` if RAM is under 16 GB)
- [ ] Confirm it works: `ollama run llama3.1:8b "hello"`
- [ ] Create `backend/` with a virtualenv, `requirements.txt` (`fastapi`, `uvicorn[standard]`, `httpx`, `pydantic-settings`, `python-dotenv`), and `.env.example` with `OLLAMA_URL=http://localhost:11434` and `MODEL=llama3.1:8b`
- [ ] Create `frontend/` with `npm create vite@latest frontend -- --template react-ts`
- [ ] Add `.gitignore` (`.env`, `venv/`, `node_modules/`, `data/`, `chroma/`)

**Done when:** a Python script can call Ollama's `/api/chat` and print a reply.

## Phase 1 — Streaming chat backend

- [x] `config.py`: load settings via `pydantic-settings`
- [x] `llm.py`: async function `stream_chat(messages)` that calls Ollama `/api/chat` with `stream=true` and yields text chunks
- [x] `main.py`: `GET /health` and `POST /chat` (body: list of messages) returning a streamed response (SSE)
- [x] Enable CORS for the Vite dev origin (`http://localhost:5173`)

**Done when:** `curl -N -X POST localhost:8000/chat ...` streams tokens as they're generated.

## Phase 2 — Chat UI

- [x] `frontend/src/api.ts`: function that POSTs to `/chat` and reads the stream with `fetch` + `ReadableStream`
- [x] `Chat.tsx`: message list, input box, send button, streaming assistant bubble, stop button (AbortController)
- [x] Basic dark theme, auto-scroll, markdown rendering for assistant messages
- [x] Vite proxy or `VITE_API_URL` env for the backend URL

**Done when:** typing in the browser streams a Llama reply live, and `npx tsc --noEmit` is clean.

## Phase 3 — Persistence

- [x] `db.py`: SQLite tables `conversations`, `messages`, `settings`
- [x] Endpoints: `GET /conversations`, `POST /conversations`, `GET /conversations/{id}`, `DELETE /conversations/{id}`
- [x] `/chat` takes a `conversation_id` and saves both user and assistant messages
- [x] Sidebar in the UI: conversation list, new chat, delete, auto-title from first message

**Done when:** refreshing the page keeps all conversations.

## Phase 4 — Tools (this is what makes it Jarvis)

- [x] `tools/__init__.py`: registry where each tool has a name, description, JSON schema, `requires_confirmation: bool`, and a Python function
- [x] `agent.py`: loop that sends messages plus tool schemas to Ollama, executes any tool calls, feeds results back, repeats until a final answer (cap at 5 iterations)
- [x] First tools: `read_file`, `list_directory`, `search_notes`, `save_note`, `get_datetime`
- [x] File tools restricted to an allow-listed folder set in `.env` (`ALLOWED_DIRS`), reject path traversal
- [x] Confirmation flow: for `requires_confirmation` tools the backend pauses and sends a `confirm_request` event; UI shows Approve / Deny; backend resumes on the answer
- [x] UI shows tool calls inline (name, arguments, result) so you can see what it did

**Done when:** "list the files in my notes folder and summarize the newest one" works end to end, and `save_note` asks for approval first.

## Phase 5 — Knowledge (RAG)

- [x] `rag.py`: ingest a folder (`.txt`, `.md`, `.pdf`, `.docx`) → chunk (~500 tokens, 50 overlap) → embed with `ollama pull nomic-embed-text` → store in Chroma
- [x] Each chunk stores metadata: `source`, `scope` (private/public/business), `path`
- [x] `POST /ingest` and `GET /sources`
- [x] Tool `search_knowledge(query, scope?)` returning top-k chunks with sources
- [x] Answers cite source filenames
- [x] UI: sources page to add a folder, see indexed files, re-index, remove

**Done when:** you can ask a question about a document you indexed and get a cited answer.

## Phase 6 — Memory and scopes

- [ ] `memory.py`: table of long-term facts (`text`, `scope`, `created_at`); tools `remember(fact, scope)` and `recall(query)`
- [ ] Inject relevant memories into the system prompt each turn
- [ ] `scopes.py`: every conversation, document, memory, and tool call carries a scope; the active scope filters what the agent can see
- [ ] UI: scope switcher (private / public / business) and a memory viewer with delete

**Done when:** switching to the business scope hides private documents and memories from the agent.

## Phase 7 — Autonomy tiers, audit log, connectors

Do this before any tool that touches the outside world.

- [ ] `autonomy.py`: every tool has a tier: `0` read-only, `1` draft only, `2` act after approval, `3` act on its own within limits. New tools default to 1 or 2. Tier 3 is opt-in per tool, with limits (max amount, max recipients, rate limit, allowed domains).
- [ ] Audit log table: every tool call with args, result, timestamp, and who approved it. UI page to browse and filter it.
- [ ] Kill switch: one button and one endpoint that instantly disables all tier 2 and 3 tools.
- [ ] Prompt-injection guard: text from emails, web pages, messages, and documents is untrusted data. Any action proposed while untrusted content is in context requires approval, whatever its tier.
- [ ] `connectors/base.py`: base class with `connect()`, `healthcheck()`, `tools()`. Secrets go in the OS keyring or an encrypted file, never in prompts, logs, or plain DB columns.
- [ ] Settings page: list connectors, status, connect/disconnect, per-connector scope and tier.
- [ ] Make `llm.py` provider-agnostic: Ollama by default, a model per task (small model for chat, larger for planning and code). Any cloud model is opt-in and blocked for the private scope.

**Done when:** a tier 2 tool cannot run without approval, every call appears in the audit log, and the kill switch stops them.

## Phase 8 — Automation and insights

- [ ] `scheduler.py`: APScheduler running inside the FastAPI lifespan
- [ ] Jobs stored in SQLite (name, cron, prompt, scope, enabled)
- [ ] First job: **Daily brief**, runs a saved prompt against your notes and knowledge and stores the result
- [ ] Insight tools: `summarize_document(path)`, `weekly_review(scope)`
- [ ] UI: automations page to create, edit, enable, run now, and view past outputs

**Done when:** a daily brief appears every morning without you opening the app.

## Phase 9 — Personal assistant

- [ ] Email connector (Gmail API or IMAP): read, search, summarize, draft. Sending is tier 2.
- [ ] Inbox triage: classify important vs noise, daily digest via the scheduler
- [ ] Calendar connector: read events, propose times, create events (tier 2)
- [ ] SMS connector (Twilio, Africa's Talking, or Termii): `send_sms` (tier 2) plus an inbound webhook
- [ ] WhatsApp connector using the official WhatsApp Business Cloud API (send, receive, templates). Do not automate a personal WhatsApp account with unofficial libraries; those get banned.
- [ ] Calls: telephony provider connector with `faster-whisper` (speech-to-text) and `piper` (text-to-speech) in the loop. Start with "call me and read my brief", then outbound calls to others. Always disclose it's an AI and save transcripts.
- [ ] Contacts table (with scope) and a `find_contact` tool
- [ ] Unified inbox page: messages across all channels with suggested replies

**Done when:** you can say "reply to Ada's email saying I'll be there Friday" and it drafts it, asks approval, and sends.

## Phase 10 — Life assistant

- [ ] Goals tables: goals, milestones, tasks (title, why, target date, metric, status)
- [ ] Tools: `create_goal`, `break_down_goal`, `update_progress`, `suggest_next_actions`
- [ ] Weekly review (scheduled): progress, blockers, what to change; saved as a note
- [ ] Daily check-in sent to your chosen channel; simple habit tracker
- [ ] Pull signals from calendar, notes, and tasks to show where your time actually goes
- [ ] UI: goals dashboard with progress and next actions

**Done when:** it turns a goal into milestones, schedules the first tasks, and reviews progress every week.

## Phase 11 — Dev assistant

- [ ] Workspace sandbox: allow-listed project folders; commands run in Docker or a restricted subprocess with timeouts
- [ ] Tools: `read_repo`, `search_code`, `write_file` (shows a diff, tier 2), `run_command` (allow-list, tier 2), `run_tests`, `git_status`, `git_commit` (tier 2). No force push.
- [ ] Planner flow: spec → task list → implement one task → run type check and tests → report. Checkpoint after every task.
- [ ] Scaffolding templates: Next.js, Vite + React, Expo, FastAPI
- [ ] Option to hand heavy coding to a larger model (local or opt-in cloud); the assistant writes the spec and TODO
- [ ] GitHub connector: issues and PRs, read-only first
- [ ] Deploy tool with preview-first (tier 2)

**Done when:** "build a landing page for X" produces a project, runs its checks, and shows you the diff before anything is committed.

## Phase 12 — Business assistant

- [ ] Site/store connectors, one per platform (WordPress/WooCommerce, Shopify, or your own REST/Firestore backend). Read-only first: orders, traffic, inventory, tickets.
- [ ] Moderation: classify comments, reviews, and user content into a review queue with suggested actions. Auto-act only on obvious spam (tier 3 with limits).
- [ ] Support: draft customer replies from a knowledge base (RAG); tier 1 first, tier 2 later
- [ ] Daily and weekly business brief: revenue, orders, failures, anomalies
- [ ] Content: draft product copy, posts, newsletters; scheduling is tier 2
- [ ] Ops alerts: uptime checks, error rates, failed payments
- [ ] Money-moving actions (refunds, price changes, payouts) stay tier 2 permanently

**Done when:** it sends a morning brief of your business and a moderation queue you can clear in a few clicks.

## Phase 13 — Trading (research first, execution last)

- [ ] Market data connector, read-only: prices, candles, news (broker or exchange API)
- [ ] Watchlists and alerts (price, indicator) via the scheduler
- [ ] Research tools: summarize news and filings, compare assets, explain a setup. The model researches and explains; it does not predict.
- [ ] Trade journal: log every idea and trade with reasoning and outcome; weekly mistake review
- [ ] Backtester: strategies as plain Python rules, tested on historical data with fees and slippage. The model can write and critique strategy code, but results come from the backtest, not its opinion.
- [ ] Paper trading connector; run it for weeks before anything live
- [ ] Live trading (separate connector, only after a reviewed paper track record): API key with trade-only permissions (no withdrawals), hard limits in config (max position size, max daily loss, allowed instruments), every order at tier 2, and a daily-loss circuit breaker that disables it
- [ ] UI note on trading pages: ideas and analysis, not financial advice

**Done when:** a strategy can be backtested, paper traded, and journaled end to end with no live orders.

## Phase 14 — Hardening and extras

- [ ] Optional local-only auth (a single passphrase) and bind the server to `127.0.0.1`
- [ ] Model switcher in the UI (list from Ollama `/api/tags`)
- [ ] Tests: `pytest` for tools and path restrictions; `vitest` for the stream parser
- [ ] Voice interface in the UI (push-to-talk), reusing the speech-to-text and text-to-speech from Phase 9
- [ ] Browser/web search tool (read-only, results treated as untrusted)

---

## Progress log

| Date | Phase | Notes |
|------|-------|-------|
| 2026-11-11 | Phase 1 | Added FastAPI health and SSE chat endpoints, configured Ollama streaming and Vite CORS; verified health, CORS, TypeScript, and live token streaming. |
| 2026-11-11 | Phase 2 | Added responsive dark chat UI, markdown, cancellable streaming API client, VITE_API_URL config, and strict TypeScript; verified browser chat through Ollama. |
| 2026-11-11 | Phase 3 | Added SQLite persistence, conversation endpoints, confirmed deletion, auto-titles, and sidebar; verified persisted chat turn and API lifecycle. |
| 2026-11-11 | Phase 4 | Added allow-listed tool execution, Ollama tool loop, inline activity, explicit save approval/denial, and path traversal protections; verified live newest-note summary and denied write. |
| 2026-11-11 | Phase 5 | Added local Chroma/Ollama RAG for TXT, Markdown, PDF, and DOCX; scope-filtered search, source management UI, and filename citations; verified ingestion and a cited local chat answer. |