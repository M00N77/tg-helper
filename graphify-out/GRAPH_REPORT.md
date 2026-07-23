# Graph Report - .  (2026-07-23)

## Corpus Check
- cluster-only mode — file stats not available

## Summary
- 1805 nodes · 5835 edges · 123 communities (108 shown, 15 thin omitted)
- Extraction: 91% EXTRACTED · 9% INFERRED · 0% AMBIGUOUS · INFERRED: 530 edges (avg confidence: 0.69)
- Token cost: 0 input · 0 output

## Graph Freshness
- Built from commit: `93b43ff5`
- Run `git rev-parse HEAD` and compare to check if the graph is stale.
- Run `graphify update .` after code changes (no API cost).

## Community Hubs (Navigation)
- repo.py
- meeting.py
- get_team_by_chat
- ChatMessage
- bot/handlers/free_text.py
- GeminiProvider
- route_intent
- kanban.py
- TestRealGroqDemo
- session
- dictionary.py
- models.py
- _check_once
- settings.py
- YouGileClient
- TestExecKanbanIntent
- evening_digest.py
- group_bot/handlers/free_text.py
- session.py
- Message
- llm_with_fallback
- chat_cmd.py
- setup_kanban.py
- _safe_parse
- test_yougile.py
- _execute_intent
- decrypt
- extract_and_save_commitments
- build_news_digest
- process_meeting_audio
- get_session
- chat_service.py
- tasks.py
- test_team.py
- Team
- send.py
- get_or_create_user
- GroqProvider
- manager.py
- agent.py
- app.py
- digest.py
- test_db.py
- free_voice
- check_user_permission
- test_config.py
- search.py
- User
- test_kanban_tasks.py
- news_topics.py
- task_approval.py
- meeting_room.py
- TestMeetingExtractParsing
- KanbanIntentResponse
- ActivityPlugin
- _extract_raw
- TestBuildBoardText
- get_provider_chain
- TeamMember
- sync_dialogs
- vector_store.py
- test_intent_dispatch.py
- style_profile.py
- sentiment.py
- .transcribe
- GigaChatProvider
- _exec_kanban_intent
- link.py
- _strip_fence
- Notifier
- env.py
- MeetingListener
- TestSafeParse
- TestMeetingExtractReal
- FakeChat
- TestSystemPrompts
- TestRouteIntentWithHistory
- test_migration.py
- graphify.js
- route_group_intent
- TestRouteIntent
- test_llm_real_route_intent.py
- handle_group_migration
- .test_real_scenario_multi_step

## God Nodes (most connected - your core abstractions)
1. `get_session()` - 287 edges
2. `session()` - 205 edges
3. `Message` - 165 edges
4. `get_or_create_user()` - 156 edges
5. `ChatMessage` - 116 edges
6. `route_intent()` - 98 edges
7. `YouGileClient` - 93 edges
8. `get_team_by_chat()` - 79 edges
9. `UserbotManager` - 71 edges
10. `User` - 57 edges

## Surprising Connections (you probably didn't know these)
- `is_team_owner()` --indirect_call--> `session()`  [INFERRED]
  src/bot/filters.py → tests/conftest.py
- `cmd_activities_on()` --indirect_call--> `session()`  [INFERRED]
  src/bot/handlers/activities.py → tests/conftest.py
- `cmd_activities_off()` --indirect_call--> `session()`  [INFERRED]
  src/bot/handlers/activities.py → tests/conftest.py
- `cmd_pulse_time()` --indirect_call--> `session()`  [INFERRED]
  src/bot/handlers/activities.py → tests/conftest.py
- `cmd_pulse_close_after()` --indirect_call--> `session()`  [INFERRED]
  src/bot/handlers/activities.py → tests/conftest.py

## Import Cycles
- None detected.

## Communities (123 total, 15 thin omitted)

### Community 0 - "repo.py"
Cohesion: 0.07
Nodes (52): cb_yg_assign(), Any, ActivitySession, Конкретный запуск групповой активности (пульс-опрос, метафора, квиз) в чате кома, add_auto_reply_log(), aggregate_pulse_responses(), cache_transcript(), close_activity_session() (+44 more)

### Community 1 - "meeting.py"
Cohesion: 0.09
Nodes (53): cb_meeting_back(), cb_meeting_howto(), cb_meeting_mtslink_token(), cb_meeting_upload_hint(), cb_mtask_add(), cb_mtask_back(), cb_mtask_cancel(), cb_mtask_confirm() (+45 more)

### Community 2 - "get_team_by_chat"
Cohesion: 0.08
Nodes (43): OwnerOrTeamMember, BaseFilter, Допускает любого участника чата, если чат зарегистрирован как командный., Допускает владельца ИЛИ участника командного чата., TeamAccessByChat, cmd_activities_off(), cmd_activities_on(), cmd_pulse_close_after() (+35 more)

### Community 3 - "ChatMessage"
Cohesion: 0.06
Nodes (13): ChatMessage, Возвращает (system_instruction, contents) для google-genai., _to_gemini_contents(), TestBurnoutReal, TestClassifyContactsReal, TestExpandKeywordsReal, TestDigestReal, TestNewsDigestReal (+5 more)

### Community 4 - "bot/handlers/free_text.py"
Cohesion: 0.11
Nodes (41): _check_intent_perms(), _dispatch(), _exec_add_reminder(), _exec_meeting_intent(), _exec_remove_news_topic(), _exec_remove_reminder(), _parse_iso_to_utc_naive(), Свободный текст (и голос) → агент → действие. Регистрируется последним в bot/app (+33 more)

### Community 5 - "GeminiProvider"
Cohesion: 0.09
Nodes (20): LLMDefaults, GeminiProvider, OpenAIProvider, _create_single_provider(), provider(), Интеграционные тесты анализа выгорания с реальным Gemini., provider(), Интеграционные тесты поиска чатов с реальным Gemini (keyword expansion + classif (+12 more)

### Community 6 - "route_intent"
Cohesion: 0.09
Nodes (4): now_local + tz_name инжектятся в системный промпт, чтобы LLM мог парсить     от, route_intent(), Каждый тест отправляет фразу → route_intent → проверяет intent., TestRouteIntentReal

### Community 7 - "kanban.py"
Cohesion: 0.12
Nodes (38): _get_chat_id(), get_team_for_event(), is_team_owner(), CallbackQuery, Проверяет, является ли пользователь владельцем команды (или глобальным владельце, Возвращает команду по чату события, с fallback на поиск по владельцу для ЛС., cb_goto_main_confirm(), cb_goto_main_no() (+30 more)

### Community 9 - "session"
Cohesion: 0.13
Nodes (38): InlineKeyboardBuilder, _find_chats_and_offer(), cmd_meeting(), breadcrumb(), cb_menu_back(), cb_menu_chats(), cb_menu_chats_find(), cb_menu_chats_send() (+30 more)

### Community 10 - "dictionary.py"
Cohesion: 0.09
Nodes (31): Lock, cb_dict_add(), cb_dict_clear_cancel(), cb_dict_clear_confirm(), cb_dict_clear_execute(), cb_dict_list(), cb_dict_upload(), cmd_dict() (+23 more)

### Community 11 - "models.py"
Cohesion: 0.19
Nodes (37): DeclarativeBase, ActivityResponse, ApiKey, AutoReplyLog, Base, Blocker, EmailMessage, IndexJob (+29 more)

### Community 12 - "_check_once"
Cohesion: 0.13
Nodes (37): _check_once(), _future(), _mock_commitment(), _mock_session_execute(), _mock_settings(), _past(), datetime, Unit-тесты для src/core/reminders.py. (+29 more)

### Community 13 - "settings.py"
Cohesion: 0.13
Nodes (36): cb_menu_settings(), _back_row(), cb_choose(), cb_close(), cb_input_auto_reply(), cb_input_digest(), cb_input_display_name(), cb_input_gemini() (+28 more)

### Community 14 - "YouGileClient"
Cohesion: 0.09
Nodes (19): cmd_dashboard(), build_board_text(), cb_kanban_board(), cb_kanban_sync(), Показать текущую канбан-доску, cb_meeting_yougile(), cmd_weekly(), Получить карточки в колонке (+11 more)

### Community 15 - "TestExecKanbanIntent"
Cohesion: 0.09
Nodes (17): _make_team(), Интеграционные тесты для канбан-диспетчера в free_text.py.  Проверяем _exec_ka, Чтение из вложенного parameters (формат KanbanIntentResponse)., Нет названия задачи — ответ с просьбой уточнить., Колонка не найдена — список доступных., Показать доску — форматирует колонки и задачи., Успешное перемещение задачи., Задача не найдена на доске. (+9 more)

### Community 16 - "evening_digest.py"
Cohesion: 0.13
Nodes (19): cmd_test_evening_digest(), Ручной запуск вечернего дайджеста — /test_evening_digest., _build_digest_text(), evening_digest_loop(), _get_tomorrow_commitments(), _get_yougile_cards(), Вечерний дайджест: задачи на завтра из Commitment и YouGile., send_evening_digest() (+11 more)

### Community 17 - "group_bot/handlers/free_text.py"
Cohesion: 0.15
Nodes (29): detect_platform(), get_board_id(), _parse_deadline(), Интеграция с YouGile канбан-доской., Вернуть ID активной доски (active_board_id, затем kanban_board_id)., # TODO: учитывать company_name / выбор компании, если их несколько., find_team_member_by_name(), list_team_members() (+21 more)

### Community 18 - "session.py"
Cohesion: 0.13
Nodes (22): get_bot(), digest_scheduler_loop(), Каждую минуту проверяет, пора ли отправлять дайджест.     Сравнение времени — в, news_scheduler_loop(), Дайджест из подписанных каналов на тему: cosine-фильтр постов через embeddings +, Напоминания о Commitment'ах: пинги об overdue и о приближении дедлайна.  Overd, reminders_loop(), blocker_escalation_loop() (+14 more)

### Community 19 - "Message"
Cohesion: 0.13
Nodes (20): cmd_blocker_dismiss(), cmd_blocker_resolve(), cmd_cancel(), cmd_login(), cmd_logout(), _finalize_login(), FSMContext, step_2fa() (+12 more)

### Community 20 - "llm_with_fallback"
Cohesion: 0.12
Nodes (25): cmd_burnout(), _llm_burnout(), Анализ эмоционального выгорания — /burnout., cb_toggle(), CallbackQuery, _classify_contacts(), _expand_keywords(), FoundChat (+17 more)

### Community 21 - "chat_cmd.py"
Cohesion: 0.14
Nodes (26): cmd_catchup(), CommandObject, Killer #4: /catchup <контакт> — где мы остановились + черновик ответа., _action_load(), _actions_keyboard(), _candidates_keyboard(), cb_cancel(), cb_catchup() (+18 more)

### Community 22 - "setup_kanban.py"
Cohesion: 0.10
Nodes (28): cmd_setup_yougile(), В группе: выдаёт deep-link на приватный чат с ботом для входа по логину/паролю., update_team_kanban(), GroupOnly, BaseFilter, Фильтры aiogram для группового роутера.  GroupOnly пропускает апдейты только и, Пропускает только сообщения/колбэки из групп и супергрупп., _cancel_keyboard() (+20 more)

### Community 23 - "_safe_parse"
Cohesion: 0.13
Nodes (3): _safe_parse(), Для каждого intent из AGENT_SYSTEM проверяем, что _safe_parse     корректно пар, TestAllIntentsParsed

### Community 24 - "test_yougile.py"
Cohesion: 0.11
Nodes (11): _mock_failed_response(), _mock_response(), Unit-тесты для YouGileClient с замокаными HTTP-запросами., TestCreateCard, TestDeleteTask, TestGetBoards, TestGetCardsInColumn, TestGetColumns (+3 more)

### Community 25 - "_execute_intent"
Cohesion: 0.12
Nodes (19): HTMLParser, _candidates_keyboard_chat(), _candidates_keyboard_send(), _confirm_keyboard(), _execute_intent(), message_to_text(), Превращает Message в строку для LLM-промта., style_profile_as_prompt_hint() (+11 more)

### Community 26 - "decrypt"
Cohesion: 0.12
Nodes (22): on_activity_answer(), CallbackQuery, Приём анонимного голоса по inline-кнопке., decrypt(), encrypt(), Псевдонимный идентификатор респондента в рамках одной сессии активности., Расшифровывает значение, но если оно не является валидным Fernet-токеном     (н, respondent_hash() (+14 more)

### Community 27 - "extract_and_save_commitments"
Cohesion: 0.13
Nodes (9): extract_and_save_commitments(), _parse_iso(), _parse_json_array(), datetime, LLM-извлечение обещаний из переписки в Commitment-список., Тесты для src/core/commitment_extractor.py: парсинг JSON, парсинг ISO, extract_a, TestExtractAndSaveCommitments, TestParseIso (+1 more)

### Community 28 - "build_news_digest"
Cohesion: 0.12
Nodes (9): build_news_digest(), _cosine(), _gather_posts(), TelegramClient, Готовит дайджест. Если only_marked_sources=False — берёт все подписанные каналы., _make_post(), Тесты для src/core/news.py: build_news_digest, _cosine, news_scheduler_loop., TestBuildNewsDigest (+1 more)

### Community 29 - "process_meeting_audio"
Cohesion: 0.15
Nodes (25): Request, Response, process_meeting_audio(), Path, Meeting, Запись встречи (платформонезависимая)., finish_meeting(), get_meeting_by_mtslink_id() (+17 more)

### Community 30 - "get_session"
Cohesion: 0.11
Nodes (20): cmd_pulse_now(), cmd_pulse_results(), Запускает пульс-опрос немедленно (демо/тест)., Подводит итоги последнего открытого опроса команды и закрывает его., get_session(), AsyncSession, cmd_risks(), Проверки прав участников команды в групповом чате.  Роль определяется по двум (+12 more)

### Community 31 - "chat_service.py"
Cohesion: 0.19
Nodes (22): _backfill_transcripts(), _cached_count(), _classify(), _last_cached_message_id(), load_chat(), _media_dir(), messages_to_transcript(), _peer_id_from_message() (+14 more)

### Community 32 - "tasks.py"
Cohesion: 0.12
Nodes (23): PendingTeamTask, Задача от участника команды, ожидающая подтверждения исполнителем., confirm_pending_team_task(), create_pending_team_task(), ensure_team_member(), get_pending_team_task(), get_team_member(), mark_team_task_approved() (+15 more)

### Community 33 - "test_team.py"
Cohesion: 0.20
Nodes (18): add_team_member(), create_team(), cmd_i_am_director(), FakeMessage, FakeSessionCM, Тесты для командной работы: Team + TeamMember в repo.py и filters.py., test_add_team_member(), test_add_team_member_admin() (+10 more)

### Community 34 - "Team"
Cohesion: 0.15
Nodes (20): cb_team_create(), cb_team_list(), cmd_cancel(), cmd_team(), CommandObject, FSMContext, Управление командой: создание, приглашение участников, роли., Начало создания команды (+12 more)

### Community 35 - "send.py"
Cohesion: 0.17
Nodes (19): OwnerOnly, Допускает владельца + список ALLOWED_TELEGRAM_IDS., _candidates_keyboard(), cb_cancel(), cb_confirm(), cb_edit(), cb_pick(), cmd_send() (+11 more)

### Community 36 - "get_or_create_user"
Cohesion: 0.14
Nodes (19): _coerce_setting_value(), _exec_set_setting(), cmd_news(), cmd_news_channels(), CommandObject, /news <тема> и /news_channels — управление новостными каналами., step_gemini_key(), step_gigachat_key() (+11 more)

### Community 37 - "GroqProvider"
Cohesion: 0.12
Nodes (12): DemoScenario, print_all(), _print_safe(), Набор тестовых сообщений для живой презентации бота. Каждое сообщение — это фра, GroqProvider, provider(), Тесты для демо-презентации: проверка роутинга интентов через Groq. Запуск:, Для каждого интента из демо-сценариев проверяем корректность _safe_parse. (+4 more)

### Community 38 - "manager.py"
Cohesion: 0.15
Nodes (14): upsert_message(), attach_auto_reply(), attach_dialog_event_handlers(), TelegramClient, Live-апдейт is_archived через Telethon UpdateFolderPeers (folder_id=1 — архив)., TelegramClient, attach_mirror(), _classify() (+6 more)

### Community 39 - "agent.py"
Cohesion: 0.17
Nodes (9): process_free_text(), LLM-роутер интентов: свободный текст владельца → структурированное действие., Парсит ответ LLM. Возвращает список интентов., Вызывает LLM с канбан-промптом, парсит JSON в KanbanIntentResponse     и маршру, _safe_parse_kanban(), Unit-тесты для LLM-агента: KanbanIntentResponse, парсинг, process_free_text., process_free_text использует ЛОКАЛЬНЫЕ импорты внутри функции.     Патчим по ор, TestProcessFreeText (+1 more)

### Community 40 - "app.py"
Cohesion: 0.16
Nodes (15): catch_all_debug(), Bot, FSMContext, run_bot(), Совместимость: клиент использует короткоживущие httpx-сессии per-request,, InviteCheckMiddleware, BaseMiddleware, Outer middleware: на каждом сообщении из группового чата     молча проверяет pe (+7 more)

### Community 41 - "digest.py"
Cohesion: 0.24
Nodes (14): cmd_digest(), CommandObject, build_digest(), _gather_payload(), _payload_to_text(), Утренний дайджест: входящие без ответа, горящие обещания и авто-ответы за ночь., send_digest(), fmt_local() (+6 more)

### Community 42 - "test_db.py"
Cohesion: 0.18
Nodes (13): _columns_for(), Проверка подключения к БД и наличия всех таблиц с правильной структурой., Проверяет, что все колонки из моделей есть в БД., _table_names(), test_all_tables_exist(), test_commitments_table_columns(), test_contacts_table_columns(), test_json_columns_exist() (+5 more)

### Community 43 - "free_voice"
Cohesion: 0.23
Nodes (15): catch_while_waiting_board(), _extract_tasks_from_intent(), free_text(), free_voice(), _process_text(), FSMContext, _summarize_intent_for_memory(), add_turn() (+7 more)

### Community 44 - "check_user_permission"
Cohesion: 0.21
Nodes (9): Any, BaseMiddleware, CallbackQuery, Проверяет права пользователя на выполнение intent-действия в групповом чате., RBACMiddleware, check_user_permission(), AsyncSession, get_role_permissions() (+1 more)

### Community 45 - "test_config.py"
Cohesion: 0.15
Nodes (5): BaseSettings, Path, Settings, Проверка конфигурации: переменные из .env загружаются корректно., test_settings_singleton()

### Community 46 - "search.py"
Cohesion: 0.24
Nodes (12): cb_cancel(), cb_forward(), cb_idx_pick(), cmd_index(), cmd_search(), _do_index(), CallbackQuery, CommandObject (+4 more)

### Community 47 - "User"
Cohesion: 0.20
Nodes (14): NewsTopic, Темы-фавориты для авто-новостей. Каждая утром собирается отдельным дайджестом., User, add_news_topic(), delete_news_topic(), delete_telegram_session(), fetch_chat_messages(), fetch_my_messages_in_chat() (+6 more)

### Community 48 - "test_kanban_tasks.py"
Cohesion: 0.25
Nodes (11): _card_titles(), _make_callback(), _make_client(), _make_team(), _nav_buttons(), Тесты для пагинации cb_kanban_tasks., Return list of (text, callback_data) for the nav row (◀ or ▶)., Return list of card titles from the inline keyboard. (+3 more)

### Community 49 - "news_topics.py"
Cohesion: 0.28
Nodes (12): cb_add(), cb_delete(), cb_toggle(), _check(), cmd_news_topics(), CallbackQuery, FSMContext, InlineKeyboardMarkup (+4 more)

### Community 50 - "task_approval.py"
Cohesion: 0.27
Nodes (11): CallbackData, approve_pending_task(), get_pending_task(), approval_keyboard(), cb_task_approve(), cb_task_reject(), _mock_create_task(), CallbackQuery (+3 more)

### Community 51 - "meeting_room.py"
Cohesion: 0.26
Nodes (9): update_team_mtslink_token(), create_jitsi_room(), create_meeting_room(), create_mtslink_room(), _slug(), _to_msk_iso(), POST /eventsessions/{id}/records/conversions — запуск конвертации в MP4.     Во, register_record_webhook() (+1 more)

### Community 52 - "TestMeetingExtractParsing"
Cohesion: 0.17
Nodes (4): Тесты парсинга ответа LLM для MEETING_EXTRACT_SYSTEM., Проверяем, что _safe_parse корректно разбирает JSON     из MEETING_EXTRACT_SYST, Проверяем, что _safe_parse парсит join_meeting с разными URL платформ., TestMeetingExtractParsing

### Community 53 - "KanbanIntentResponse"
Cohesion: 0.31
Nodes (4): KanbanIntentResponse, BaseModel, Структурированный ответ LLM-агента для управления Канбан-доской., TestKanbanIntentResponse

### Community 54 - "ActivityPlugin"
Cohesion: 0.18
Nodes (5): ActivityPlugin, PulsePoll, InlineKeyboardMarkup, Protocol, Псевдонимный пульс-опрос настроения по шкале 1..5 (проективная диагностика).

### Community 56 - "TestBuildBoardText"
Cohesion: 0.29
Nodes (4): _mock_client(), Тесты build_board_text с мокнутым YouGileClient.  Проверяет, что после исправл, Проверяем отображение количества задач в build_board_text., TestBuildBoardText

### Community 57 - "get_provider_chain"
Cohesion: 0.29
Nodes (9): _exec_add_reminders_from_chat(), _analyze_sentiment(), _assign_tasks_by_ai(), cmd_kanban_analytics(), Аналитика сроков YouGile-доски — /kanban_analytics., Запрашивает у LLM назначение исполнителей и применяет через YouGile API.     Во, get_provider_chain(), AsyncSession (+1 more)

### Community 58 - "TeamMember"
Cohesion: 0.23
Nodes (10): FSMContext, Вызывается из /start yougile_login_{chat_id} (см. start.py) и запускает FSM., start_yougile_login_flow(), cmd_start(), process_display_name(), CommandObject, FSMContext, L (+2 more)

### Community 59 - "sync_dialogs"
Cohesion: 0.31
Nodes (8): auto_sync_loop(), Раз в час обновляем кэш контактов и архивный статус. Страховка к live-handler'у, _entity_display_name(), _entity_kind(), prefetch_recent_messages(), TelegramClient, Утилиты для работы с диалогами Telethon., sync_dialogs()

### Community 60 - "vector_store.py"
Cohesion: 0.24
Nodes (3): Qdrant embedded в data/qdrant. Коллекция messages пересоздаётся при первом upser, VectorHit, VectorStore

### Community 61 - "test_intent_dispatch.py"
Cohesion: 0.28
Nodes (4): _exec_add_news_topic(), Тесты роутинга интентов: парсинг всех 25 интентов, route_intent с историей/часов, TestAddNewsTopicIntent, TestRemoveNewsTopicIntent

### Community 62 - "style_profile.py"
Cohesion: 0.39
Nodes (6): cmd_style(), CommandObject, build_style_profile(), _parse_json_safe(), Профиль стиля общения с конкретным контактом. Подмешивается в промпт авто-ответа, update_style_profile_for_contact()

### Community 63 - "sentiment.py"
Cohesion: 0.32
Nodes (6): BaseModel, SentimentRiskResult, analyze_sentiment(), analyze_sentiment_and_risk(), Анализ тональности текстов через LLM-провайдера.  LLM дешевле и точнее локальн, Возвращает 'positive' | 'negative' | 'neutral' | 'speech' | None.

### Community 64 - ".transcribe"
Cohesion: 0.43
Nodes (3): Path, Локальный faster-whisper / OpenAI Whisper API / hybrid (local с fallback в API)., TranscriptionService

### Community 66 - "_exec_kanban_intent"
Cohesion: 0.29
Nodes (7): _build_yougile_user_keyboard(), cb_voice_board_select(), cb_yg_page(), _exec_kanban_intent(), CallbackQuery, InlineKeyboardMarkup, Исполнитель канбан-интентов (create_task, show_boards, move_task, smalltalk).

### Community 67 - "link.py"
Cohesion: 0.47
Nodes (5): Router, _build_user_keyboard(), make_router(), InlineKeyboardMarkup, _register_handlers()

### Community 70 - "env.py"
Cohesion: 0.60
Nodes (3): do_run_migrations(), run_async_migrations(), run_migrations_online()

### Community 71 - "MeetingListener"
Cohesion: 0.40
Nodes (3): MeetingListener, meeting_listener.py — Selenium-подход заменён на audio upload. Файл оставлен дл, Устаревший класс. Используй handle_meeting_file в meeting.py.

### Community 73 - "TestMeetingExtractReal"
Cohesion: 0.40
Nodes (3): Реальные вызовы Gemini с транскрипцией встречи., Проверяем, что LLM корректно извлекает дедлайн из фразы «до среды»., TestMeetingExtractReal

### Community 107 - "route_group_intent"
Cohesion: 0.67
Nodes (3): Any, LLM-роутер для группового чата команды: свободный текст участника →     структу, route_group_intent()

## Knowledge Gaps
- **2 isolated node(s):** `DemoScenario`, `_Option`
  These have ≤1 connection - possible missing edges or undocumented components.
- **15 thin communities (<3 nodes) omitted from report** — run `graphify query` to explore isolated nodes.

## Suggested Questions
_Questions this graph is uniquely positioned to answer:_

- **Why does `get_session()` connect `get_session` to `repo.py`, `meeting.py`, `get_team_by_chat`, `bot/handlers/free_text.py`, `kanban.py`, `session`, `dictionary.py`, `_check_once`, `settings.py`, `YouGileClient`, `evening_digest.py`, `group_bot/handlers/free_text.py`, `session.py`, `Message`, `llm_with_fallback`, `chat_cmd.py`, `setup_kanban.py`, `_execute_intent`, `decrypt`, `extract_and_save_commitments`, `build_news_digest`, `process_meeting_audio`, `chat_service.py`, `tasks.py`, `test_team.py`, `Team`, `send.py`, `get_or_create_user`, `manager.py`, `agent.py`, `digest.py`, `free_voice`, `check_user_permission`, `search.py`, `news_topics.py`, `task_approval.py`, `meeting_room.py`, `get_provider_chain`, `TeamMember`, `sync_dialogs`, `test_intent_dispatch.py`, `style_profile.py`, `.transcribe`, `_exec_kanban_intent`, `link.py`, `handle_group_migration`?**
  _High betweenness centrality (0.205) - this node is a cross-community bridge._
- **Why does `route_intent()` connect `route_intent` to `ChatMessage`, `bot/handlers/free_text.py`, `GroqProvider`, `agent.py`, `TestRealGroqDemo`, `route_group_intent`, `free_voice`, `TestRouteIntent`, `TestRouteIntentWithHistory`, `.test_real_scenario_multi_step`, `test_llm_real_route_intent.py`, `llm_with_fallback`, `_safe_parse`, `test_intent_dispatch.py`?**
  _High betweenness centrality (0.089) - this node is a cross-community bridge._
- **Why does `ChatMessage` connect `ChatMessage` to `meeting.py`, `get_team_by_chat`, `GeminiProvider`, `route_intent`, `session.py`, `llm_with_fallback`, `_execute_intent`, `extract_and_save_commitments`, `build_news_digest`, `process_meeting_audio`, `get_session`, `send.py`, `GroqProvider`, `agent.py`, `digest.py`, `KanbanIntentResponse`, `_extract_raw`, `get_provider_chain`, `style_profile.py`, `sentiment.py`, `GigaChatProvider`, `TestMeetingExtractReal`, `route_group_intent`?**
  _High betweenness centrality (0.086) - this node is a cross-community bridge._
- **Are the 204 inferred relationships involving `session()` (e.g. with `is_team_owner()` and `.__call__()`) actually correct?**
  _`session()` has 204 INFERRED edges - model-reasoned connections that need verification._
- **Are the 9 inferred relationships involving `Message` (e.g. with `_backfill_transcripts()` and `_cached_count()`) actually correct?**
  _`Message` has 9 INFERRED edges - model-reasoned connections that need verification._
- **Are the 19 inferred relationships involving `ChatMessage` (e.g. with `SendStates` and `KanbanIntentResponse`) actually correct?**
  _`ChatMessage` has 19 INFERRED edges - model-reasoned connections that need verification._
- **What connects `DemoScenario`, `_Option` to the rest of the system?**
  _2 weakly-connected nodes found - possible documentation gaps or missing edges._