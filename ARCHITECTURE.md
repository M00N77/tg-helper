# TelegramHelper — Architecture Overview

## 1. What Is It?

A personal Telegram assistant. **Two accounts in one:**

- **Control bot** (aiogram) — receives commands from the owner
- **Userbot** (Telethon) — acts on behalf of the owner in personal chats, groups, and channels

---

## 2. Startup and Lifecycle (`src/main.py`)

1. `init_db()` — creates all required tables via SQLAlchemy ORM.
2. `UserbotManager.restore_all()` — loads all Telegram sessions from DB, connects Telethon clients, attaches event handlers (mirror, auto-reply, dialog events)
3. Five background loops start:
   - **digest** (scheduler checks every 60s, sends at configured time in owner's TZ) — morning digest
   - **evening-digest** (scheduler checks every 60s, sends at 20:00 in owner's TZ) — evening digest (tasks for tomorrow)
   - **reminders** (every 5min) — deadline reminders
   - **news** (scheduler checks every 60s, sends at configured time) — automated news digests
   - **auto_sync** (every hour) — contact sync
4. Control bot starts — receives commands and free-text queries

---

## 3. Database (PostgreSQL, 17 tables)

The default database is PostgreSQL (asyncpg driver). Schema migrations are handled via Alembic.

| Table | Stores |
|---|---|
| `users` | Owner and authorized users (Telegram ID, created date) |
| `user_settings` | All settings (auto-reply, digest, reminders, news, LLM, transcription, timezone) |
| `telegram_sessions` | Encrypted session_string, api_hash and API ID for Telethon auth |
| `api_keys` | Encrypted keys for LLM providers (OpenAI, Gemini, Groq, GigaChat) |
| `contacts` | Synced dialogs (name, username, phone, archived, news_source flag, style_profile JSON) |
| `messages` | Real-time mirror copy of all messages with metadata (kind, date, text, media path) |
| `commitments` | Extracted obligations (mine/theirs, deadline, status, last reminded date) |
| `auto_reply_logs` | Transparent logs of automated responses with timestamps and triggers |
| `pending_actions` | Two-step confirmation payloads (`send_message`, etc.) |
| `news_topics` | Topics for automated news digests |
| `index_jobs` | Vector indexing progress and offsets (DB-to-Qdrant) |
| `transcription_cache` | Voice and audio transcription cache indexed by file unique ID |
| `teams` | Group chat team configuration (kanban token, board ID, active board) |
| `team_members` | Members of the teams with roles (admin/member) |
| `pending_invites` | Pending group chat team invitations |
| `meetings` | Meeting records (platform, meeting url, audio path, transcript, summary) |
| `meeting_tasks` | Tasks extracted from meeting summaries and transcripts |

### Secrets Encryption
- Session strings, api hashes, and LLM API keys are stored **encrypted** using Fernet symmetric encryption.
- *Technical Debt note:* `teams.kanban_token` (YouGile API token) is currently stored in plaintext. It is planned to migrate to encryption.

---

## 4. Bot Commands

| Command | Action |
|---|---|
| `/login` | FSM wizard: api_id → api_hash → phone → code → 2FA → save session |
| `/logout` | Deletes session and shuts down userbot |
| `/settings` | Inline menu: all settings (auto-reply, LLM, digest, reminders, news, timezone, API keys...) |
| `/sync` | Syncs contacts + prefetches last 50 messages from top-30 chats |
| `/chat Name` | Chat actions: Summary / Tasks (extract commitments) / Draft / Catchup |
| `/catchup Name` | "Where we left off" + draft reply |
| `/send instruction` | Send message with two-step confirmation (PendingAction) |
| `/search text` | Search messages (Qdrant vector search, with pre-filtering by database query) |
| `/index Name` | Vector indexing of a chat (into Qdrant embedded) |
| `/todos` | List open commitments with Done/Cancel buttons |
| `/digest` | Generate morning digest immediately |
| `/style Name` | Recalculate communication style with a contact |
| `/news topic` | Gather news from marked channels |
| `/news_channels`| Manage news source channels |
| `/news_topics`  | Manage fav topics for morning digest |
| `/team`         | Create and manage teams, list members, invite users |
| `/kanban_login` | FSM wizard: authenticate in YouGile and retrieve token |
| `/kanban_board` | List YouGile boards and select active board for group chat |
| `/meeting`      | Upload meeting recording → transcribe → summarize → create cards |
| `Any text`      | **Free-text AI agent** — determines intent and executes actions |

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
- `catchup` — "where we left off" + draft reply
- `search` — message search (vector + full-text)
- `news_digest` — news gathering
- `list_todos` — show tasks
- `set_setting` — change a setting (understands verbal commands like "turn off news")
- `find_in_chats` — smart chat discovery by topic ("where did I talk about furniture?")
- `add_reminder` / `remove_reminder` — reminder management
- `add_news_topic` / `remove_news_topic` — news topic management
- `multi` — execute multiple actions sequentially
- `chat` — simple reply (small talk / general discussion)
- `create_task` / `show_boards` / `move_task` — direct YouGile Kanban control

Voice messages are automatically transcribed and processed through the exact same agent pipeline.

---

## 6. Meeting Processing (`meeting.py`)

Upload-based flow — no external browser automation or dependencies required.

```
User sends audio/video → download file → transcription_service.transcribe()
  → LLM extracts summary + tasks → YouGileClient.create_card()
  → bot shows summary and creates Kanban tasks
```

The router is registered before `free_text.router` so file uploads are intercepted first.

**Conversation context:** Last 8 turns of conversation are stored in-memory (with 30-minute TTL for last mentioned contact), allowing the LLM to understand references like "him", "her", "that chat".

---

## 7. Auto-reply System (`src/userbot/auto_reply.py`)

Works when the owner is offline. Two modes:

- **static** — template text configured by the user
- **smart** — LLM generates a contextual reply using the last 20 messages and the loaded `style_profile`

Uses a configurable cooldown (default 30 min) per chat to prevent loops. All auto-replies are logged to `auto_reply_logs`.

---

## 8. Message Mirror (`src/userbot/mirror.py`)

Every incoming/outgoing message is copied to the `messages` table in real time, with contact upsert in `contacts`. Voice messages are saved without immediate transcription (lazy transcription when the chat is analyzed).

---

## 9. Vector Search (Qdrant)

Local Qdrant embedded is used. Collection is `messages` using COSINE distance.

- `/search` — embed query → search Qdrant → return matching messages (with optional SQL filters)
- `/index` — batch-embeds chat messages
- News digest also uses embeddings for filtering relevant news posts

---

## 10. Transcription (`src/core/transcription.py`)

Three modes:

- **local** — faster-whisper (small model, runs on CPU/GPU locally)
- **api** — OpenAI Whisper API (`whisper-1` model)
- **hybrid** — local first, falls back to API on error

Transcription results are cached in `transcription_cache` table by file unique ID.

---

## 11. LLM Providers

| Provider | Light Model | Heavy Model | Embedding |
|---|---|---|---|
| OpenAI | `gpt-5-mini` | `gpt-5.5` | `text-embedding-3-small` |
| Gemini | `gemini-2.5-flash` | `gemini-3-flash-preview` | `text-embedding-004` |
| Groq | `llama-3.3-70b-versatile` | `llama-3.3-70b-versatile` | `text-embedding-3-small` (placeholder) |
| GigaChat | `GigaChat` | `GigaChat-Pro` | Raises NotImplementedError |

Switch active provider and light/heavy model via `/settings`.

---

## 12. Technology Stack

| Component | Technology |
|---|---|
| Control Bot | aiogram 3.x |
| Userbot | Telethon 1.36+ |
| Database | PostgreSQL (asyncpg) |
| ORM | SQLAlchemy 2.0 (async) |
| Migrations | Alembic |
| LLM Providers | OpenAI SDK + google-genai |
| Vector Store | Qdrant (embedded, local) |
| Voice Transcription | faster-whisper (local) + OpenAI Whisper API |
| Meeting Transcription | Upload-based via transcription_service |
| Document Parsing | pypdf + python-docx |
| Fuzzy Matching | rapidfuzz |
| Encryption | cryptography (Fernet) |
| Configuration | pydantic-settings |
| Containerization | Docker + docker-compose |
