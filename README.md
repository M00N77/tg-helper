# TelegramHelper

Персональный AI-ассистент для Telegram-аккаунта. Состоит из двух частей в одном процессе:

- **Userbot** (Telethon) — подключается к твоему аккаунту через MTProto. Зеркалит все входящие/исходящие сообщения в локальную БД, авто-отвечает оффлайн, отправляет сообщения от твоего имени.
- **Control Bot** (aiogram) — Telegram-бот, через который ты управляешь userbot'ом. Понимает команды и **свободный текст или голос** через LLM-агента.

Поддерживает командную работу: в групповых чатах участники команды получают доступ к канбану, встречам и дайджестам.

---

## Ключевые возможности

### Индивидуальные
- 🤖 **Свободный текст и голос** — агент понимает обычный язык и голосовые
- 🧠 **Анализ выгорания** — LLM анализирует исходящие сообщения, даёт рекомендации
- 📈 **Аналитика рисков** — флаги задач которые висят дольше нормы
- 📋 **Dashboard** — сводка горящих обещаний, задач и рисков одной командой
- 📅 **Недельный отчёт** — что сделано, что горит, статистика по доске
- 🌙 **Вечерний дайджест** — задачи на завтра из обязательств и YouGile
- 📰 **Новости** — авто-дайджест по подписанным каналам и темам
- ⏰ **Напоминания** — пинги о дедлайнах с настраиваемым lead-time

### Командные
- 👥 **Управление командой** — создание, приглашение по @username, роли (admin/member)
- 📊 **Канбан-интеграция** (YouGile) — авто-создание задач из переписки и встреч
- 🎥 **Встречи** — создание комнат (Jitsi / МТС Линк), транскрипция аудио/видео → саммари → задачи
- 🔄 **Invite-проверка** — новый участник пишет в группу → автоматическое добавление в команду
- 🏢 **Командный дайджест** — обязательства и канбан для всей команды

---

## Фишка: пишешь как человеку

Скажи или напиши боту обычным языком — агент сам поймёт что сделать.

| Фраза владельца | Действие |
|---|---|
| «Напиши Оле, что созвон в 8» | Черновик → подтверждение → отправка |
| «Напиши ему привет» (после разговора про контакт) | Помнит последний контакт из диалога |
| «В одном из чатов я договаривался про мебель — на чём остановились?» | Поиск по тексту + по именам контактов → выбор → catchup |
| «Дай выдержку из чата с Артёмом» | Саммари |
| «Какие задачи в чате с боссом» | Извлечение и сохранение обещаний |
| «Дай новости по AI агентам за 48 часов» | Дайджест по подписанным каналам |
| «Добавь тему AI», «убери тему регулирование» | Управление авто-новостями |
| «Дайджест в 9 утра», «выключи новости», «часовой пояс Europe/Moscow» | Меняет настройки разговором |
| «Напомни завтра в 18:00 позвонить маме» | Создаёт напоминание (агент знает твой TZ) |
| «Поставь напоминания из чата с Артёмом» | Извлекает обещания и кладёт в /todos |
| «Включи новости и дайджест в 7, добавь тему AI» | Несколько действий за раз |
| Голосовое сообщение | Транскрипция → агент → действие |
| Файл встречи (аудио/видео) | Транскрипция → саммари → задачи на YouGile |

Действия, видимые другим (отправка), всегда подтверждаются inline-кнопкой.

---

## Команды

**Основные**
- `/start` — приветствие, главное меню
- `/help` — справка
- `/menu` — главное меню навигации
- `/cancel` — отменить текущую операцию / FSM

**Аккаунт**
- `/login` — пошагово: api_id → api_hash → телефон → код → 2FA
- `/logout` — удалить сохранённую сессию
- `/sync` — обновить контакты + фоновый prefetch последних сообщений в топ-30 активных чатов

**Настройки**
- `/settings` — главное меню разделов с inline-кнопками

**Чаты**
- `/chat <имя>` — выбор контакта → саммари / задачи / черновик / catchup
- `/catchup <имя>` — сразу к «где мы остановились»
- `/send <инструкция>` — «скажи Оле, что созвон в 8» (с подтверждением)
- `/search <текст>` — поиск (FTS5 + векторный, если индексировано)
- `/index <имя>` — проиндексировать чат для семантического поиска

**Память**
- `/todos` — открытые обещания (мои и мне), кнопки done/cancel
- `/style <имя>` — пересчитать профиль моего стиля общения с этим контактом
- `/digest [now\|on\|off\|at HH:MM]` — утренний дайджест
- `/test_evening_digest` — ручной запуск вечернего дайджеста

**Команда**
- `/team` — управление командой (создать, пригласить, участники, настройки)
- `/team invite` — пригласить участника по @username
- `/team members` — список участников команды

**Канбан (YouGile)**
- `/kanban` — взаимодействие с доской: задачи, колонки
- `/kanban_login` — авторизация в YouGile
- `/kanban_board <id>` — выбрать активную доску
- `/kanban_analytics` — аналитика: среднее время задач, флаги риска, настроение команды
- `/dashboard` — сводный отчёт: обязательства + канбан + риски
- `/weekly` — недельный отчёт по задачам и встречам
- `/burnout` — анализ эмоционального состояния по переписке

**Новости**
- `/news <тема> [--hours=24]` — разовый дайджест из подписанных каналов
- `/news_channels` — пометить каналы-источники
- `/news_topics` — темы для утренних авто-новостей

**Встречи**
- `/meeting` — главное меню: отправить запись встречи для транскрипции и создания задач
- `/meeting join` — создать комнату встречи (Jitsi / МТС Линк)

---

## Как это устроено

```
┌──────────────────────────────────────────────────┐
│  Control Bot (aiogram)                           │
│  команды, FSM, inline-меню,                      │
│  free-text + голос → агент                       │
│  фильтры: OwnerOnly / OwnerOrTeamMember          │
└────────────┬─────────────────────────────────────┘
             │
┌────────────▼─────────────────────────────────────┐
│  Core                                            │
│  • Agent (LLM intent router)                     │
│  • LLM router (OpenAI ↔ Gemini ↔ Groq ↔ GigaChat)│
│  • ChatService, Summarizer, Style profile        │
│  • Commitments, Reminders, Digest, News          │
│  • Evening digest                                │
│  • Conversation context (краткая память)         │
└────────────┬───────────────────────┬─────────────┘
             │ MTProto                │ embeddings/LLM
┌────────────▼─────────┐     ┌────────▼──────────┐
│ Userbot (Telethon)   │     │ Storage           │
│ • Auth с 2FA         │     │ • SQLite + FTS5   │
│ • NewMessage mirror  │     │ • Qdrant embedded │
│ • UpdateFolderPeers  │     │ • Fernet secrets  │
│ • Auto-reply offline │     │ • Alembic         │
└──────────────────────┘     └───────────────────┘
```

**Фоновые задачи** в одном event loop:
- `digest-scheduler` — утренний дайджест в выбранное время по TZ владельца
- `evening-digest` — вечерний дайджест (задачи на завтра) в 20:00
- `news-scheduler` — авто-дайджест по темам-фаворитам
- `reminders-loop` — пинги о приближении/просрочке дедлайнов
- `auto-sync` — раз в час обновляет контакты и архивный статус

**Real-time mirror.** Каждое входящее и исходящее сообщение в любом чате тут же пишется в `messages` таблицу. SQLite FTS5-индекс синхронизируется триггерами — поиск работает локально за миллисекунды (не через Telegram API).

**Lazy-транскрипция.** Голосовые при mirror'инге сохраняются без транскрипта — она запускается в момент анализа конкретного чата (faster-whisper локально или OpenAI Whisper API, по настройке).

**Invite-проверка.** Middleware на каждом сообщении из группы проверяет `pending_invites` — если пользователь приглашён, он автоматически добавляется в `team_members`.

**Prefetch при `/sync`.** Один раз для топ-N активных чатов подтягиваются последние ~50 сообщений в БД — заполняет холодный кэш. После этого mirror поддерживает свежесть.

---

## Стек

- Python 3.12
- **aiogram 3.x** — control bot
- **Telethon 1.36+** — userbot (MTProto)
- **SQLAlchemy 2** + **aiosqlite** + **SQLite FTS5** — БД и полнотекстовый поиск
- **Alembic** — миграции схемы БД
- **Qdrant** (embedded) — векторный поиск
- **OpenAI SDK** + **google-genai** — LLM (gpt-5-mini / gpt-5.5, gemini-2.5-flash / gemini-3-flash-preview)
- **Groq SDK** — дополнительный LLM-провайдер (llama-4)
- **GigaChat SDK** — дополнительный LLM-провайдер
- **faster-whisper** + OpenAI Whisper API — транскрипция голоса (local / api / hybrid)
- **pypdf**, **python-docx** — документы
- **Транскрипция встреч** — upload-based, через transcription_service
- **Создание комнат** — Jitsi (бесплатно) / МТС Линк (по API-токену)
- **rapidfuzz** — fuzzy-резолвер контактов
- **cryptography (Fernet)** — шифрование секретов

Имена моделей вынесены в `src/config.py:LLMDefaults` — заменить при выходе новых.

---

## Запуск через Docker

### 1. Подготовь данные

| Что | Где взять |
|---|---|
| `BOT_TOKEN` | [@BotFather](https://t.me/BotFather) → `/newbot` |
| `OWNER_TELEGRAM_ID` | [@userinfobot](https://t.me/userinfobot) — пришлёт `id` |
| `ENCRYPTION_KEY` | Fernet-ключ (см. ниже) |

```powershell
# С пакетом cryptography:
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"

# Без cryptography:
python -c "import secrets, base64; print(base64.urlsafe_b64encode(secrets.token_bytes(32)).decode())"
```

### 2. `.env`

```env
BOT_TOKEN=123456:AA...
OWNER_TELEGRAM_ID=987654321
ENCRYPTION_KEY=<base64-fernet-key>
DATABASE_URL=sqlite+aiosqlite:///data/app.db
```

### 3. Подними

```bash
docker compose up -d --build
docker compose logs -f assistant
```

Первая сборка занимает 3–5 минут (Python + ffmpeg + зависимости). Образ кэшируется.

`./data/` смонтирован как volume — там лежат:
- `app.db` — SQLite с FTS5 индексом
- `qdrant/` — векторное хранилище
- `media/` — скачанные voice/audio/документы
- `cache/` — кэш моделей `faster-whisper` (~500 MB после первой транскрипции)

### 4. Авторизация в боте

В чате с control-ботом:

1. `/start` — приветствие
2. `/login` — пошагово:
   - `api_id` (число) с https://my.telegram.org → API development tools
   - `api_hash` (32 hex)
   - телефон в формате `+71234567890`
   - код из Telegram — **с пробелами между цифрами** (`1 2 3 4 5`), иначе Telegram автоматически инвалидирует код, увидев его открыто в чате
   - 2FA-пароль, если включён (сообщение с паролем удаляется сразу после успеха)
3. `/settings → 🔑 API-ключи` — OpenAI, Gemini, Groq или GigaChat ключ (проверится перед сохранением)
4. `/settings → 🌍 Часовой пояс` — пресет или произвольный IANA
5. `/sync` — подтянуть контакты + фоновый prefetch сообщений

После этого можно писать боту обычным текстом или голосом.

---

## Настройки

`/settings` — главное меню. Каждая фича — отдельный раздел с описанием и тогглами:

- **🌍 Часовой пояс** — от него отталкиваются шедулеры и отображения времён.
- **🔄 Авто-ответ** — режимы `static` (заготовленный текст) и `smart` (LLM в твоём стиле). Кулдаун 5/15/30/60 мин. Только ЛС, не группы и не боты, только когда оффлайн.
- **☀ Дайджест** — утренняя сводка: ждут ответа, горящие обещания, авто-ответы.
- **⏰ Напоминания** — пинги о дедлайнах. Lead 1/2/4/12/24 ч; алерт при просрочке.
- **📰 Новости** — авто-дайджест по темам из `/news_topics`.
- **🛡 Приватность** — игнорировать архив (по умолчанию ВКЛ): архивные чаты не подгружаются никуда.
- **🤖 LLM** — переключение OpenAI ↔ Gemini ↔ Groq ↔ GigaChat, лёгкая ↔ тяжёлая модель.
- **🎤 Транскрипция** — `local` / `api` / `hybrid`.
- **🔑 API-ключи** — хранятся зашифрованными.

Любую настройку можно поменять и **разговором**: «дайджест в 7 утра», «выключи новости», «текст автоответа: Сейчас занят».

---

## Безопасность и приватность

- Владелец (`OWNER_TELEGRAM_ID`) и участники команды проходят фильтры `OwnerOnly` / `OwnerOrTeamMember`.
- Шифрование Fernet для session-string, api_hash, всех LLM-ключей. Ключ из `ENCRYPTION_KEY` не попадает в БД.
- Сообщения с 2FA-паролем и API-ключами удаляются из чата сразу после успеха.
- `.env` и `data/` исключены из git.
- Mirror пишет сообщения в **локальную БД на твоей машине**, никуда не отправляет.
- Архивные чаты по умолчанию исключаются из видимости.
- Все отправляемые сообщения — через двухшаговое подтверждение (`PendingAction`).

---

## Структура проекта

```
src/
├── main.py                  # bootstrap: init_db → restore userbots → schedulers → bot
├── config.py                # pydantic-settings + LLMDefaults
├── crypto.py                # Fernet
├── bot/
│   ├── app.py               # регистрация роутеров, middleware
│   ├── filters.py           # OwnerOnly, OwnerOrTeamMember, TeamAccessByChat
│   ├── states.py            # FSM-states (Login, Kanban, Team, Meeting, …)
│   ├── middlewares/
│   │   └── invite_check.py  # InviteCheckMiddleware — авто-добавление в команду
│   └── handlers/
│       ├── start.py         # /start, /help
│       ├── login.py         # /login (FSM с 2FA), /logout, /cancel
│       ├── settings.py      # /settings — меню + разделы
│       ├── chat_cmd.py      # /chat, /sync (с prefetch)
│       ├── catchup_cmd.py   # /catchup
│       ├── send.py          # /send + подтверждения
│       ├── search.py        # /search, /index
│       ├── todos.py         # /todos
│       ├── digest_cmd.py    # /digest
│       ├── digest_evening_cmd.py  # /test_evening_digest
│       ├── style_cmd.py     # /style
│       ├── news_cmd.py      # /news, /news_channels
│       ├── news_topics.py   # /news_topics
│       ├── menu.py          # /menu — главное меню
│       ├── team.py          # /team — управление командой
│       ├── meeting.py       # /meeting — транскрипция встреч, создание задач
│       ├── kanban.py        # /kanban, /kanban_login, /kanban_board
│       ├── kanban_analytics.py  # /kanban_analytics
│       ├── dashboard.py     # /dashboard
│       ├── weekly.py        # /weekly
│       ├── burnout.py       # /burnout — анализ выгорания через LLM
│       ├── yougile.py       # YouGileClient (API-клиент)
│       └── free_text.py     # AI-агент: текст/голос → intent → действие
├── core/
│   ├── agent.py             # LLM intent router
│   ├── chat_finder.py       # smart_find: FTS5 + LLM-classify имён + Tg fallback
│   ├── conversation_context.py  # краткая память диалога с агентом
│   ├── notifier.py          # bridge userbot → control bot
│   ├── contact_resolver.py  # rapidfuzz по локальной БД контактов
│   ├── chat_service.py      # load_chat: incremental + lazy транскрипция
│   ├── transcription.py     # faster-whisper / OpenAI Whisper hybrid
│   ├── documents.py         # PDF/DOCX/TXT
│   ├── summarizer.py        # summary / draft / catchup промпты
│   ├── style_profile.py     # JSON-профиль стиля per-контакт
│   ├── commitment_extractor.py  # извлечение обещаний
│   ├── digest.py            # утренний дайджест + scheduler
│   ├── evening_digest.py    # вечерний дайджест + scheduler
│   ├── news.py              # дайджест по каналам + scheduler
│   ├── reminders.py         # пинги о дедлайнах
│   ├── auto_sync.py         # фоновый re-sync контактов раз в час
│   ├── timeutil.py          # zoneinfo helpers
│   ├── text_sanitizer.py    # привод HTML к Telegram-whitelist
│   ├── vector_store.py      # Qdrant embedded
│   └── indexer.py           # batch индексация → embeddings
├── services/
│   └── meeting_room.py      # Jitsi / МТС Линк — создание комнат встреч
├── userbot/
│   ├── manager.py           # UserbotManager + pending login
│   ├── dialogs.py           # sync_dialogs, prefetch_recent_messages
│   ├── auto_reply.py        # NewMessage handler (offline + cooldown)
│   ├── dialog_events.py     # UpdateFolderPeers → Contact.is_archived
│   └── mirror.py            # Real-time mirror всех сообщений в БД и FTS5
├── llm/
│   ├── base.py              # ChatMessage + Protocol
│   ├── openai_provider.py   # OpenAI
│   ├── gemini_provider.py   # Google Gemini
│   ├── groq_provider.py     # Groq (llama-4)
│   ├── gigachat_provider.py # GigaChat (Сбер)
│   └── router.py            # build_provider по UserSettings
└── db/
    ├── models.py            # User, Settings, Session, ApiKey, Contact, Message,
    │                        # Commitment, AutoReplyLog, IndexJob, TranscriptionCache,
    │                        # PendingAction, NewsTopic, Team, TeamMember,
    │                        # PendingInvite, Meeting, MeetingTask
    ├── session.py           # async engine + init_db (включая FTS5 schema)
    └── repo.py              # CRUD с (де)шифрованием на границе + fts_search
```

---

## Известные ограничения

- **Telegram ToS**: userbot с авто-ответом — серая зона. По умолчанию авто-ответ выключен и шлёт нейтральный заготовленный текст с кулдауном между ответами.
- **Один инстанс**: Qdrant embedded держит lock на `data/qdrant/` — параллельно бот не запустишь.
- **Однопользовательский по умолчанию**: через `ALLOWED_TELEGRAM_IDS` можно добавить других; командный режим — через /team.

---

## Шпаргалка

```bash
# логи
docker compose logs -f assistant

# зайти в контейнер
docker compose exec assistant sh

# полный сброс БД (сессия Telethon, ключи, контакты — всё)
docker compose down
rm -f data/app.db data/app.db-journal data/app.db-shm data/app.db-wal
docker compose up -d

# миграции (если менялись модели)
alembic upgrade head

# смена версий моделей: src/config.py → LLMDefaults
```

---

## Лицензия

MIT.
