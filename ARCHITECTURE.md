# TelegramHelper — Architecture Overview

## 1. What Is It?

A personal Telegram assistant. **Two accounts in one:**

- **Control bot** (aiogram) — receives commands from the owner
- **Userbot** (Telethon) — acts on behalf of the owner in personal chats, groups, and channels

---

## 2. Startup and Lifecycle (`src/main.py`)

1. `init_db()` — creates tables if they don't exist
2. `UserbotManager.restore_all()` — loads all Telegram sessions from DB, connects Telethon clients, attaches event handlers
3. Four background loops start:
   - **digest** (every 60s) — morning digest
   - **reminders** (every 5min) — deadline reminders
   - **news** (every 60s) — automated news digests
   - **auto_sync** (every hour) — contact sync
4. Control bot starts — receives commands

---

## 3. Database (PostgreSQL, 11 tables)

| Table | Stores |
|---|---|
| `users` | Owner (single row, single-tenant) |
| `user_settings` | All settings (auto-reply, digest, reminders, news, LLM, transcription...) |
| `telegram_sessions` | Encrypted session_string, api_hash |
| `api_keys` | Encrypted OpenAI/Gemini keys |
| `contacts` | All dialogs (name, username, phone, archived, is_news_source, communication style) |
| `messages` | All messages (copy) with metadata (type, date, text, transcript, extracted text) |
| `commitments` | Extracted obligations (mine/theirs, deadline, status) |
| `auto_reply_logs` | Auto-reply logs |
| `pending_actions` | Confirmations before sending |
| `news_topics` | Topics for automated news digests |
| `index_jobs` | Vector indexing progress |
| `transcription_cache` | Voice transcription cache |
| `teams` | YouGile kanban token, board ID, provider per chat |

All keys and tokens are stored **encrypted** (Fernet).

---

## 4. Bot Commands

| Command | Action |
|---|---|
| `/login` | FSM wizard: api_id → api_hash → phone → code → 2FA → save session |
| `/logout` | Deletes session |
| `/settings` | Inline menu: all settings (auto-reply, LLM, digest, reminders, news, timezone, API keys...) |
| `/sync` | Syncs contacts + prefetches last 50 messages from top-30 chats |
| `/chat Name` | Chat actions: Summary / Tasks (extract commitments) / Draft / Catchup |
| `/catchup Name` | "Where we left off" + draft reply |
| `/send instruction` | Send message with two-step confirmation |
| `/search text` | Search all messages (Qdrant vector → fallback ILIKE) |
| `/index Name` | Vector indexing of a chat (into Qdrant) |
| `/todos` | List open commitments with Done/Cancel buttons |
| `/digest [now\|on\|off\|at HH:MM]` | Morning digest control |
| `/style Name` | Analyze communication style with a contact |
| `/news topic [--hours=N]` | News digest by topic |
| `/news_channels` | Mark/unmark news source channels |
| `/news_topics` | Automated digest management |
| `/kanban_login` | FSM wizard: login → password → auto-auth via YouGile API |
| `/kanban_board` | List YouGile boards, pick one by number |
| `Any text` | **Free-text AI agent** — determines intent and executes |

---

## 5. Free-text AI Agent (`free_text.py` + `agent.py`)

The most powerful feature. Any non-command text is routed through an LLM:

```
Text → LLM → JSON intent → dispatch
```

**Supported intents:**
- `send_message` — send a message (with confirmation)
- `summarize_chat` — chat summarization
- `tasks_for_chat` — extract commitments from chat
- `catchup` — "where we left off"
- `search` — message search
- `news_digest` — news
- `list_todos` — show tasks
- `set_setting` — change a setting
- `find_in_chats` — smart chat discovery by topic
- `add_reminder` / `remove_reminder` — reminder management
- `add_news_topic` / `remove_news_topic` — news topic management
- `multi` — execute multiple actions sequentially
- `chat` — simple reply (small talk)
- `unknown` — help

Voice messages also work: Whisper → text → same pipeline.

**Conversation context:** Last 8 turns stored in memory (with 30-minute TTL for last mentioned contact), so the LLM understands "him", "that chat", "her".

---

## 6. Auto-reply System (`src/userbot/auto_reply.py`)

Works when the owner is offline. Two modes:

- **static** — template text
- **smart** — LLM generates a contextual reply (last 20 messages + communication style)

30-minute cooldown per chat. Logged to `auto_reply_logs`.

---

## 7. Message Mirror (`src/userbot/mirror.py`)

Every incoming/outgoing message is copied to `messages` in real time, with contact upsert in `contacts`. Voice messages are saved without transcription (lazy transcription when the chat is analyzed).

---

## 8. Commitments and Reminders

Extracted via LLM:
- From `/chat Name → Tasks`, `/catchup`
- From free-text: "remind me to reply to Peter tomorrow"
- From automated meeting digests (WIP)

Background loop checks deadlines every 5 minutes and sends warnings.

---

## 9. Vector Search (Qdrant)

Local Qdrant at `data/qdrant/`. Collection `messages`, COSINE distance.

- `/search` — embed → search → your messages
- `/index` — batch-embeds chat messages
- News digest also uses embeddings for relevance

---

## 10. Transcription (`src/core/transcription.py`)

Three modes:

- **local** — faster-whisper (small model, runs on your machine)
- **api** — OpenAI Whisper API
- **hybrid** — local first, falls back to API on error

Cached in `transcription_cache`.

---

## 11. LLM Providers

| Provider | Light Model | Heavy Model | Embedding |
|---|---|---|---|
| OpenAI | `gpt-5-mini` | `gpt-5.5` | `text-embedding-3-small` |
| Gemini | `gemini-3-flash` | `gemini-3.1-pro` | `text-embedding-004` |

Switch via `/settings`.

---

## 12. Digests

**Morning:** Over the last 14 hours — who wrote without a reply, burning deadlines, auto-replies. LLM formats into a structure.

**News:** By topics — collects posts from marked channels, embedding relevance → LLM summary with source attribution.

---

## 13. Communication Style (`style_profile`)

For each contact, the LLM analyzes the last 80 outgoing messages and saves a JSON profile: address (ты/вы), register, length, emoji usage, punctuation, typical phrases. Used by smart auto-reply and draft replies.

---

## 14. Architectural Patterns

```
mirror.py → upsert_message()         # real-time
load_chat() → _backfill_transcripts() # lazy transcription
smart_find() → keywords FTS5 + name_score + Telegram fallback
Keys encrypted with Fernet, replies confirmed via PendingAction
```

**Session pattern:** Handlers use `async with get_session() as session:` (from `src.db.session`) — **not** aiogram dependency injection.
**Single-tenant:** `OwnerOnly` filter on all routers — only `owner_telegram_id` can interact with the bot.

---

## 15. Kanban Integration (YouGile)

Fully implemented and connected. Flow:

1. `/kanban_login` — FSM wizard asks for login and password
2. `YouGileClient.generate_token()` calls `POST /api-v2/auth/companies` to list companies, then `POST /api-v2/auth/keys` to create an API key for the first company
3. Token is saved to `teams` table via `update_team_kanban()`
4. `/kanban_board` — calls `GET /api-v2/boards` with the saved token, shows numbered list
5. User picks a board by number → `board_id` saved to `teams` table

**Files:**
- `src/bot/handlers/kanban.py` — FSM handlers and board selection
- `src/bot/handlers/yougile.py` — `YouGileClient` with `get_columns()`, `create_card()`, `move_card()`, `get_cards_in_column()`, `get_boards()`, `generate_token()`
- `src/bot/states.py` — `KanbanStates` (waiting_token) and `KanbanAuthStates` (waiting_login, waiting_password, waiting_for_board)
- `src/db/models.py` — `Team` model (chat_id, kanban_token, kanban_board_id, kanban_provider)
- `src/db/repo.py` — `get_team_by_chat()`, `update_team_kanban()`

---

## 16. Work-in-Progress (Not Connected)

- **Meetings** (Yandex Telemost) — join, record, transcribe, extract tasks
- **Teams** — multi-user with roles

---

## 17. Technology Stack

| Component | Technology |
|---|---|
| Control Bot | aiogram 3.x |
| Userbot | Telethon 1.36+ |
| Database | PostgreSQL (async via asyncpg) |
| ORM | SQLAlchemy 2.0 (async) |
| Migrations | Alembic |
| LLM Providers | OpenAI SDK + google-genai |
| Vector Store | Qdrant (embedded, local) |
| Voice Transcription | faster-whisper (local) + OpenAI Whisper API |
| Document Parsing | pypdf + python-docx |
| Fuzzy Matching | rapidfuzz |
| Encryption | cryptography (Fernet) |
| Configuration | pydantic-settings |
| Containerization | Docker + docker-compose |
