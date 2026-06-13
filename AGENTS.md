# TelegramHelper — Architecture Guide for AI Agents

## Структура проекта

| Файл / Директория | Назначение |
|---|---|
| `src/bot/handlers/free_text.py` | Основной хендлер ДМ владельца. Цепочка: `_process_text` → `route_intent` (AGENT_SYSTEM) → `_dispatch` → `_execute_intent` |
| `src/group_bot/handlers/free_text.py` | Групповой хендлер. Цепочка: `route_group_intent` (GROUP_AGENT_SYSTEM) → `check_user_permission` → `_handle_*` |
| `src/core/agent.py` | Все промпты (AGENT_SYSTEM, KANBAN_AGENT_SYSTEM, GROUP_AGENT_SYSTEM), роутинг (`route_intent`, `route_group_intent`, `process_free_text`), парсинг JSON |
| `src/core/meeting_processor.py` | Обработка встреч: транскрипция → LLM → `create_yougile_tasks_from_meeting` |
| `src/core/sentiment.py` | Анализ тональности + детекция рисков (`analyze_sentiment_and_risk`) |
| `src/bot/middlewares/rbac.py` | RBAC-мидлварь для групповых роутеров |
| `src/bot/handlers/todos.py` | Commitments: `/todo`, `/trash`, `/restore` |
| `src/bot/handlers/meeting.py` | `/meeting` — загрузка записей, транскрипция |
| `src/bot/handlers/kanban.py` | `/kanban_board`, `/kanban_login` — настройка доски |
| `src/bot/handlers/yougile.py` | YouGile-клиент: create_card, move_card, get_columns, resolve_user_by_name и др. |
| `src/llm/router.py` | `build_provider`, `get_provider_chain`, `llm_with_fallback` |
| `src/db/models.py` | SQLAlchemy модели: User, Team, Meeting, TeamMember, Commitment, MessageRisk и др. |
| `src/db/repo.py` | Репозиторий: get_team_by_chat, get_team_members, get_team_member, list_open_commitments и др. |
| `src/services/webhook_server.py` | aiohttp сервер для вебхуков МТС Линк |
| `src/services/ngrok_tunnel.py` | Авто-туннель для локальной разработки |

## Иерархия хендлеров (порядок регистрации в `src/bot/app.py`)

start → login → menu → settings → commands (send, search, todos, digest, kanban, meeting) → dictionary → team → standup → blockers → activities → **group_bot** handlers → **free_text** (самый последний) → debug (catch-all)

## Два бота в одном процессе

- `src/bot/` — личный бот владельца (ДМ), фильтр `OwnerOnly()`
- `src/group_bot/` — групповой бот для команды, фильтр `GroupOnly()`
- Оба живут в одном `Dispatcher` (dp), порядок регистрации важен
- Шарят один FSM storage (MemoryStorage) — не создавать второй

## Промпты (в `src/core/agent.py`)

| Промпт | Где используется | Описание |
|---|---|---|
| `AGENT_SYSTEM` | `route_intent` (личка) | Полный набор интентов для владельца: send_message, summarize_chat, list_todos, create_task, schedule_meeting, show_my_tasks и др. |
| `KANBAN_AGENT_SYSTEM` | `process_free_text` (личка, канбан) | Только канбан: create_task (с multi), show_boards, move_task, smalltalk |
| `GROUP_AGENT_SYSTEM` | `route_group_intent` (группа) | Только командная доска: create_task_for, show_my_tasks, edit_task, close_task и др. |

## Известные ограничения

- `KANBAN_AGENT_SYSTEM` поддерживает `"multi"` для нескольких create_task
- `restore_task` в AGENT_SYSTEM (интент 11.5) работает только с commitments (личные todo), не с YouGile-задачами
- `restore_kanban_task` (интент 19.1) — для восстановления YouGile-задач из корзины
- `tg://user?id=` теги работают только если у пользователя открыт профиль в Telegram
- `llm_with_fallback` уведомляет пользователя только если переданы `notify_bot` и `notify_chat_id`
- callback_data кнопок ограничена 64 байтами — используй индексы вместо длинных ID

## Паттерны, которые нельзя нарушать

- Все HTTP-запросы только async (aiohttp / httpx с trust_env=True)
- session (AsyncSession) передаётся снаружи в core-функции, не создаётся внутри
- Вебхук-хендлер отвечает 200 немедленно, тяжёлую работу делает через `asyncio.create_task`
- Импорты внутри функций (lazy) для избежания циклических зависимостей — обычная практика (см. `_exec_meeting_intent`, `_exec_kanban_intent`)

## Переменные окружения (`.env`)

| Переменная | Описание |
|---|---|
| `BOT_TOKEN` | Основной бот (ДМ владельца) |
| `BOT_TOKEN_2` | Групповой бот |
| `DATABASE_URL` | PostgreSQL (asyncpg) |
| `WEBHOOK_BASE_URL` | Публичный URL (ngrok или прод) |
| `NGROK_AUTHTOKEN` | Токен ngrok |
| `NGROK_ENABLED` | true/false |

## Типичный флоу для багов

1. Пользователь пишет текст → роутер определяет по фильтрам (private vs group)
2. `route_intent` / `route_group_intent` вызывает LLM с соответствующим SYSTEM-промптом
3. LLM возвращает JSON → `_safe_parse` / `_safe_parse_kanban`
4. `_dispatch` / `_execute_intent` маршрутизирует по `kind` (intent)
5. Каждый `_exec_*` / `_handle_*` выполняет бизнес-логику
