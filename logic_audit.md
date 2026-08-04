  # Логический аудит бизнес-логики TelegramHelper

  Дата: 2026-08-04. Векторы: FSM Traps, RBAC/жизненный цикл команды, Invariants/осиротевшие данные, Feature Collisions.
  Критичность: 🔴 критическая / 🟠 высокая / 🟡 средняя.

  Контекст, который важно зафиксировать до разбора: **в системе один бот** (`BOT_TOKEN_2` нигде не используется, `src/config.py:31` — один `bot_token`). «Групповой бот» — тот же токен, отфильтрованный `GroupOnly()`. Поэтому все безфильтровые хендлеры личного бота доступны и в групповом чате.

  ---

  ## Вектор 1: FSM Traps

  ### 1.1 🔴 Крэш на нетекстовом сообщении в state «ввод текста» — пользователь застревает навсегда

  > ✅ СДЕЛАНО — ветка `fix/fsm-non-text-guard`. Хелпер `require_text` в `src/bot/fsm_utils.py`; пропатчены: `process_card_title`, `process_card_description`, `process_board`, `process_login`, `process_password`, `process_deadline` (kanban.py), `step_invite` (team.py), `process_display_name` (start.py), `step_mtask_edit`/`step_mtask_add` (meeting.py). Тесты: `tests/test_fsm_guards.py` (11 шт.).

  **Файлы:** `src/bot/handlers/kanban.py:810-823` (`process_card_title`), `:825-833` (`process_card_description`), `:732-749` (`process_board`), `:1230-1271` (`process_deadline`), `src/bot/handlers/team.py:220-227` (`step_invite`), `src/bot/handlers/start.py:101-109` (`process_display_name`).

  **Сценарий воспроизведения:**
  1. Владелец нажимает «➕ Добавить задачу» → бот просит ввести название (`waiting_title`).
  2. Пользователь присылает фото/стикер/голосовое вместо текста.
  3. `message.text` равен `None` → `None == "❌ Отмена"` ложно → `message.text.strip()` бросает `AttributeError`.
  4. Исключение не обрабатывается: пользователь не получает ответ, FSM-состояние **сохраняется**. Каждое следующее текстовое сообщение снова попадает в этот же хендлер → крашится → стейт не сбрасывается. Замкнутый круг без выхода (кроме `/cancel`).

  То же самое в `process_deadline` (kanban.py:1230-1236) — хендлер ещё и **удаляет сообщение пользователя** перед крэшем, стирая его фото.

  **Продуктовое решение:** добавить в каждый FSM-consumer проверку `if not message.text: ...` (ветка «нужен текст» + сброс/подсказка), либо повесить один общий fallback-хендлер на `F.any_content_type` для активных стейтов, который отвечает «Пожалуйста, пришлите текст» без сброса стейта, и хендлер, сбрасывающий стейт по команде `/cancel`. Паттерн уже есть в `dictionary.py:167` (`step_dict_single_term_invalid`) — распространить на все стейты.

  ### 1.2 🔴 `/start`, `/menu` и «Главное меню» не очищают FSM — следующее сообщение крадёт устаревший consumer

  > ✅ СДЕЛАНО — ветка `fix/fsm-clear-on-navigation`. `cmd_start` (start.py) и `cmd_menu`/`cb_menu_back` (menu.py) и `cb_goto_main_yes` (kanban.py) вызывают `await state.clear()` — любая глобальная навигация сбрасывает FSM. Тесты: `tests/test_fsm_navigation.py` (6 шт.).

  **Файлы:** `src/bot/handlers/start.py:21-98` (`cmd_start` — нет `state.clear()`), `src/bot/handlers/menu.py:52-71` (`cmd_menu`), `src/bot/handlers/kanban.py:799-804` (`cb_goto_main_yes` — показывает меню без очистки стейта).

  **Сценарий воспроизведения:**
  1. Владелец настраивает OpenAI-ключ (`SettingsStates.waiting_openai_key`).
  2. Параллельно отправляет `/start` — получает меню, визуально «всё сброшено».
  3. Печатает следующий осмысленный текст — он молча уходит в `step_openai_key` и валидируется как API-ключ. Пользователь думает, что бот не слышит его, а его текст попал в настройки.

  **Продуктовое решение:** `cmd_start`/`cmd_menu`/`goto:main` должны вызывать `await state.clear()` (или показывать подтверждение «Есть незавершённый процесс — продолжить/отменить»). Лучшее решение — единый диалоговый guard: любая глобальная навигация (start/menu/back) сбрасывает стейт.

  ### 1.3 🟠 Старые inline-кнопки перехватывают активный state без проверки

   > ✅ СДЕЛАНО — ветка `fix/fsm-stale-callback-guard`. Helper `enter_state` в `src/bot/fsm_utils.py` блокирует вход в новый стейт, если текущий не пуст, и показывает `alert("Сначала завершите текущий процесс")`. Пропатчены все callback-хендлеры входа в FSM: `cb_kanban_add`, `cb_kanban_deadline`, `cb_mtask_edit`, `cb_mtask_add`, `cb_team_create`, `cb_team_invite`, все `cb_input_*` (settings), `cb_menu_chats_find`, `cb_menu_chats_send`, `cb_menu_news_add`, `cb_add` (news_topics), `cb_edit` (send), `start_yougile_login_flow`. Тесты: `tests/test_fsm_callback_guard.py` (3 шт.), `test_fsm_navigation.py`, `test_fsm_guards.py`, `test_kanban_sync.py` (44 шт. всего).

  **Файлы:** `src/bot/handlers/kanban.py:765-783` (`cb_kanban_add` → `waiting_title`), `:1205-1227` (`cb_kanban_deadline` → `setting_deadline`), `src/bot/handlers/meeting.py:443-491` (`cb_mtask_edit`/`cb_mtask_add`), `src/bot/handlers/team.py:126-136, 305-325`, `settings.py:516-627` (все `cb_input_*`), `src/bot/handlers/menu.py:113, 120, 370`, `news_topics.py:79-87`, `send.py:218-224`, `setup_yougile.py:40-49` (deep-link перезаписывает любой стейт).

  **Сценарий воспроизведения:**
  1. Владелец в `SettingsStates.waiting_gemini_key` — ждёт ввода ключа.
  2. Скроллит вверх, нажимает старую кнопку «📅 Дедлайн» из вчерашнего канбан-диалога.
  3. Стейт молча перезаписывается на `setting_deadline`; следующий текст уходит в `process_deadline` (и крэшится по 1.1).

  В `meeting.py` хуже: `cb_mtask_edit`/`cb_mtask_add` перезаписывают `action_id`/`task_idx` в data стейта — нажатие старой кнопки «изменить задачу N» в процессе редактирования задачи M перенаправляет следующий ввод на задачу N без предупреждения.

  **Продуктовое решение:** перед `set_state` проверять текущий стейт; если активен чужой стейт — либо игнорировать кнопку с `alert("Сначала завершите текущий процесс")`, либо подтверждать переход. Все callback-хендлеры, которые входят в стейт, должны идти через один helper `enter_state(user_id, target, require_idle=True)`.

  ### 1.4 🟠 Онбординг нового пользователя не имеет отмены

  **Файлы:** `src/bot/handlers/start.py:57-59` (установка `OnboardingStates.waiting_display_name`), `login.py:38-46` (глобальный `/cancel` — только `OwnerOnly`), `team.py:22-29` (`/cancel` — только `OwnerOrTeamMember`).

  **Сценарий воспроизведения:** новый пользователь (не владелец, не член команды) открывает бота → попадает в онбординг → передумал и хочет выйти. `/cancel` не сработает ни в личке (OwnerOnly), ни в группе (не член команды). Сообщение уходит в debug catch-all (`app.py:69-96`, только лог). Единственный выход — отправить `/start` снова. В групповом чате такой пользователь при этом автоматически станет членом команды по 2.2 и получит cancel через team.py.

  **Продуктовое решение:** `cmd_start` и `/cancel` — снять с `/cancel` фильтр `OwnerOnly`, сделать универсальный хендлер `state.clear()` + «Процесс прерван», зарегистрированный до остальных; для онбординга добавить кнопку «Отмена» в ReplyKeyboard.

  ### 1.5 🟡 Голосовое сообщение молча сбрасывает любой активный процесс

  **Файлы:** `src/bot/handlers/free_text.py:1261-1274` (`free_voice` — при активном стейте, кроме `waiting_for_board`, вызывает `state.clear()` без предупреждения).

  **Сценарий:** владелец на полпути ввода карточки канбана (`waiting_description`) диктует голосовое → стейт сброшен, введённый title потерян, голос обрабатывается как команда агента. Данные теряются молча.

  **Продуктовое решение:** при активном стейте показывать подтверждение: «Вы прервали создание задачи. Отправить голос как команду?» с кнопками, либо сохранять `VoiceTaskState` в data стейта и восстанавливать после обработки голоса.

  ### 1.6 🟡 FSM-состояние встречи переживает отправку файла — состояние и действие рассинхронизированы

  **Файлы:** `src/bot/handlers/meeting.py:152-225` (`handle_meeting_file` — фильтр `F.audio|F.video|F.document`, guard только на `DictStates.waiting_for_file`), `:514, 538` (consumers `step_mtask_edit`/`step_mtask_add` с `F.text` без fallback).

  **Сценарий:** пользователь в `MeetingStates.waiting_task_edit`, отправляет аудиофайл → сообщение уходит в `handle_meeting_file` (начинается транскрипция встречи!), стейт при этом остаётся `waiting_task_edit`. Следующий текст молча съедается `step_mtask_edit` (или наоборот — фото/стикер молча глотает debug-роутер, стейт живёт).

  **Продуктовое решение:** `handle_meeting_file` должен проверять `state.get_state()` и сбрасывать чужие стейты; добавить fallback «не текст» для `waiting_task_edit/add`.

  ---

  ## Вектор 2: RBAC и жизненный цикл команды

  ### 2.1 🔴 Нет механизма удаления/передачи владения вообще — owner ушёл, команда умерла

  **Файлы:** `src/db/repo.py:1089-1104` (`remove_team_member` — dead code, импорт только в `team.py:13`, вызовов нет), `src/group_bot/handlers/director.py:48-63` (`/i_am_director` — отказ, если команда уже есть), `src/group_bot/permissions.py:13-41` (роль «admin» выводится из `team.owner_telegram_id`), grep по всему `src/`: **нет** `left_chat_member`/`my_chat_member`/`ChatMemberUpdated`-хендлеров, **нет** функции смены `owner_telegram_id` (записывается только при создании: `repo.py:856-864`, `director.py:57-62`, `team.py:185-191`).

  **Сценарий воспроизведения (тупик):**
  1. Создатель команды выходит из группы или удаляет аккаунт.
  2. Ничего не происходит: `owner_telegram_id`, токены, доска остаются в БД; standup/блокеры/пульс продолжают поститься (`standup_scheduler.py` и `activities/scheduler.py` читают `team.chat_id` напрямую).
  3. НО: единственный admin в системе — это owner (`repo.py:1016-1018` — `role="admin"` присваивается только owner'у; UI «настроить роли» рекламируется в `team.py:442-443`, но хендлера нет). Все остальные — «member».
  4. `/setup_kanban` (`setup_kanban.py:37-46`), `/kanban_token` (`:125-134`), смена доски (`:289-291`), подтверждения администратора (`group free_text.py:329-330`) требуют `is_admin` → заблокированы навсегда для всех.
  5. Передать владение нельзя (функции нет). Пересоздать команду нельзя (`/i_am_director` откажет). → **Перманентный dead-end.**

  **Продуктовое решение:**
  - Хендлер `left_chat_member` / `my_chat_member`: при уходе owner'а — автопередача владения самому старому admin'у, иначе — самому старому member'у, иначе — пометка команды «без владельца» + запрос `/i_am_director` от любого члена.
  - Функция `transfer_ownership(team_id, new_owner_id)`: переписывает `owner_telegram_id` и выставляет роли (старый owner → member, новый → admin).
  - UI: команда `/give_ownership @user` для owner'а.
  - Команда `/leave` для члена: удаляет TeamMember + очищает его yougile_user_id.

  ### 2.2 🔴 Исключённые из группы пользователи навсегда остаются членами (и могут вернуться кнопкой)

  **Файлы:** `src/group_bot/handlers/free_text.py:627-638` (`ensure_team_member` на **любое** сообщение в зарегистрированном чате — приглашение не требуется), `src/group_bot/handlers/link.py:124-144` (`link_yougile:*` — без проверки членства: если члена нет, `ensure_team_member` **создаёт** строку, `link.py:137`).

  **Сценарий воспроизведения:**
  1. Пользователь исключён админом из Telegram-группы.
  2. В БД строка `team_members` осталась (удаления нет — п.2.1).
  3. Пользователь жмёт старое сообщение «👤 Выберите себя в YouGile» в ЛС (или, зная `team_id` из утёкшей кнопки, формирует `link_yougile:7:user123`) → `link.py:137` **создаёт членство заново** и привязывает yougile_user_id.
  4. Проверки членства (`kanban.py:192-200, 226-229, 342-345`) снова проходят — доступ к доске и задачам команды восстановлен без ведома владельца.

  **Продуктовое решение:**
  - `ensure_team_member` в `link.py` заменить на строгую проверку: `get_team_member` или отказ с alert'ом; членство создавать только через invite-flow.
  - `link_yougile` валидировать `yougile_user_id`: пользователь должен привязывать только себя, а не произвольный id.
  - Подписка на `my_chat_member`/`left_chat_member`: при исключении — удалять TeamMember (и, опционально, ставить `pending_team_tasks` в cancelled).

  ### 2.3 🟠 IDOR в `kanban:link_confirm:` — привязка чужого telegram_id к чужому YouGile-пользователю

  **Файлы:** `src/bot/handlers/kanban.py:1169-1194` (`cb_kanban_link_confirm`): `tg_id = int(parts[3])` берётся из callback_data без проверки, что это `callback.from_user.id` и что этот пользователь — член команды; `set_team_member_yougile_id(session, team.id, tg_id, yg_user_id)` без проверки роли.

  **Сценарий:** участник группы получает сообщение «привяжите себя» (или перехватывает callback), подменяет `tg_id` на чужой → чужому участнику навсегда назначается YouGile-пользователь: все задачи «назначены на него» (`show_my_tasks`, assign-логика).

  **Продуктовое решение:** в `cb_kanban_link_confirm` использовать только `callback.from_user.id`; добавить `can_manage_kanban`/member-проверку; убрать tg_id из callback_data.

  ### 2.4 🟠 Личный канбан-роутер без фильтров доступен из группового чата

  **Файлы:** `src/bot/handlers/kanban.py:41` (`router = Router(name="kanban")` — **без** фильтра; сравни `free_text.py:112` с `OwnerOnly`), `src/bot/app.py:133` (регистрация до групповых роутеров).

  **Сценарий:** любой участник группы (даже не TeamMember — в группу может писать кто угодно) нажимает кнопки из сообщений личного бота, которые попали в общий чат (это один бот): `kanban:board` (просмотр доски и **автозапись** board_id, kanban.py:379-447), `kanban:tasks`, `kanban:add` (FSM-создание карточек), `kanban:col`, `kanban:sync`, `kanban:stats`, `kanban:link_user`, `kanban:deadline` — только `get_team_for_event` + токен, без проверки членства. Исключение: `kanban:settings`, `kanban:change_board:`, `sb:*`, `kb_team*` — там есть `can_manage_kanban`.

  **Продуктовое решение:** на роутер `kanban.py` повесить фильтр приватности (`F.chat.type == "private"`), а в DM — проверку членства в выбранной команде на каждый callback; либо групповые сообщения не должны доходить до этого роутера в принципе (аналог `GroupOnly` для приватных).

  ### 2.5 🟠 RBAC фактически не работает: мидлварь не зарегистрирована, права открываются в плюс

  **Файлы:** `src/bot/middlewares/rbac.py:14-75` (определена, но `app.py:156-158` — только комментарии, вызова `.outer_middleware` нет), `src/db/repo.py:1858-1864` (нет таблицы `role_permissions` → `{"allowed_intents": ["*"]}` — fail-open), `src/bot/handlers/free_text.py:1523-1534` (`_check_intent_perms` возвращает True для private-чатов и при `member is None`), `meeting.py:250-558` (`get_pending_action` без `user_id` — любой член с `action_id` может подтвердить задачи встречи).

  **Продуктовое решение:**
  - Зарегистрировать `RBACMiddleware` на групповых роутерах.
  - Fail-closed: отсутствие записи → deny, а не `["*"]`.
  - `_check_intent_perms` не должен возвращать True при `member is None`.
  - `get_pending_action` проверять `user_id == callback.from_user.id`.

  ### 2.6 🟡 Owner-less команды создаются фоном

  **Файлы:** `src/db/repo.py:1129-1132` (`update_team_kanban`: нет команды → создаёт `Team(chat_id=...)` с `owner_telegram_id=0`), `src/bot/handlers/kanban.py:579-581`.

  **Сценарий:** `/kanban_login` для несуществующей/удалённой команды создаёт пустую команду с токеном, без членов и владельца → по п.2.1 эта команда навсегда без администратора, но с рабочим токеном.

  **Продуктовое решение:** запретить создание команды в `update_team_kanban` — вернуть ошибку «Команда не найдена»; команды создаются только через `/i_am_director` или `/team`.

  ### 2.7 🟡 Мёртвые кнопки управления интеграцией

  **Файлы:** `src/bot/handlers/kanban.py:1025-1028`, `src/bot/handlers/team.py:401-403` — кнопки «❌ Отключить канбан» (`kanban:disconnect`), «Сменить токен» (`kanban:relogin`), «Сменить доску» (`kanban:change_board` без суффикса), `kanban:back_to_menu`, `kanban:setup:{team.id}`, `kanban:task:`, `kanban:assign:` **отрисованы, но хендлеров нет** — нажатие уходит в debug catch-all молча. Пользователь не может отключить сломанную интеграцию (см. 3.1).

  **Продуктовое решение:** реализовать `kanban:disconnect` (очистка `kanban_token`, `kanban_board_id`, `active_board_id`, отвязка yougile_user_id у членов) и «Сменить токен» (переход в FSM логина с guard на параллельный запуск — п.3.4).

  ---

  ## Вектор 3: Invariants & Orphaned Data

  ### 3.1 🔴 Протухший токен YouGile: задача «Создается...» → фальшивый «✅ Задачи созданы!»

  **Файлы:** `src/core/meeting_processor.py:30-76` (`create_yougile_tasks_from_meeting`): при ошибке API (401 от протухшего токена) `except Exception` → `first_col_id = None` → возврат `(0, 0, [], None)`; `src/bot/handlers/meeting.py:275-281` и `:390-396`: `failed == 0` → рендер «✅ Задачи созданы!» (на деле создано 0). Токен нигде не инвалидируется: grep `kanban_token = None`/clear — пусто; единственная кнопка отключения мертва (2.7).

  **Сценарий:** токен истёк → после встречи бот присылает «✅ Задачи созданы!» → команда уверена, что задачи в YouGile, а их нет. Повторной попытки нет (нет retry/reaper).

  **Продуктовое решение:**
  - `create_yougile_tasks_from_meeting` должен различать «нет колонок» (fail) и «колонок нет вообще»; на `HTTPStatusError(401)` — помечать интеграцию сломанной (`kanban_token=None` или `integration_status="broken"`) и возвращать `failed_count = len(tasks)`.
  - Callers: при `created_count == 0 and failed_count == 0 and tasks` — показывать «⚠ Не удалось создать задачи: интеграция сломана», а не успех.
  - Добавить reaper: задачи в статусе `processing` старше N минут → back to `pending` (и использовать его для `PendingTeamTask`).

  ### 3.2 🟠 Статус `processing` зависает навсегда (латентный баг ветки одобрения)

  **Файлы:** `src/group_bot/handlers/tasks.py:52-64` — после `confirm_pending_team_task` (pending→processing), если у исполнителя нет `yougile_user_id`, статус **не откатывается** (задача в `processing` навсегда, повторное подтверждение невозможно — guard `status='pending'`). Крэш между confirm и `mark_team_task_approved`/`mark_team_task_failed` даёт то же самое. Reaper'а нет (`src/main.py:60-70` — фоновые задачи без sweeper'а).

  Дополнительно: вся ветка `PendingTeamTask`/`PendingTask` **осиротела**: `create_pending_team_task` (`repo.py:622-640`) и `create_pending_task` (`repo.py:751-765`) не имеют ни одного caller'а, `task_approval.py` — мок (`:35-38` возвращает `mock_task_id`, YouGile не вызывается). «Создается…»-статуса в UI нет — он есть в БД.

  **Продуктовое решение:** reaper (30–60 мин → back to pending с error_message), откат статуса в `tasks.py:57-64`, и решение по судьбе мёртвой ветки: либо подключить producers (create_pending_team_task при создании задачи на исполнителя), либо удалить таблицы/кнопки.

  ### 3.3 🟠 Переподключение YouGile оставляет старую доску: `kanban_board_id` и `active_board_id` расходятся

  **Файлы:** `src/bot/handlers/kanban.py:571-593` (`process_password` → `update_team_kanban`), `src/db/repo.py:1133-1134` (перезапись токена + `kanban_board_id=""`, но `active_board_id` не трогается), `repo.py:1365-1376` (set_active_board), `src/bot/handlers/yougile.py:11-13` (`get_board_id` предпочитает `active_board_id`).

  **Сценарий:** 1) `/kanban_login` под старым аккаунтом → доска A сохранена в `active_board_id`. 2) Владелец перелогинивается под новым аккаунтом → `kanban_token` новый, `active_board_id` = старая доска A чужого аккаунта. 3) Все запросы уходят на доску A с новым токеном → «доска не найдена / нет прав», авто-подбор доски в `cb_kanban_board` (kanban.py:399-423) не срабатывает (get_board_id возвращает stale значение).

  **Продуктовое решение:** `update_team_kanban` (или `process_password`) должен обнулять `active_board_id`/`active_board_name` вместе с токеном; добавить валидацию токена API-вызовом в FSM-путь (в `/kanban_token` она есть — `setup_kanban.py:165-186`).

  ### 3.4 🟠 Двойной параллельный запуск `/setup_yougile` — FSM перетирает контекст первой сессии

  **Файлы:** `src/bot/handlers/setup_yougile.py:40-49`, `src/bot/handlers/kanban.py:499-526, 571-593`, `src/bot/app.py:114` (MemoryStorage, ключ (chat,user)).

  **Сценарий:** 1) Владелец в вкладке A запускает `yougile_login_{team1}` → в FSM data пишется `setup_chat_id=team1`. 2) Во вкладке B запускает `yougile_login_{team2}` → data перезаписывается на team2. 3) Вкладка A вводит пароль → токен сохраняется **для team2**. Вторая команда получает чужой токен, первая — ничего. Плюс: `process_password` пишет токен без валидации (п.3.3) и может перезаписать рабочий токен рабочим же.

  **Продуктовое решение:** guard «сессия уже активна» (идемпотентный ключ в FSM: если `setup_chat_id` уже задан и не равен новому — блокировать новый запуск с подсказкой), однократная установка `setup_chat_id` (не перезаписывать, если уже установлен).

  ### 3.5 🟠 Meeting: гонка дублирующих вебхуков + зависшие «active» встречи без реaper'а

  **Файлы:** `src/services/webhook_server.py:113-130` (проверка `status in ("recording","active")` и перевод в `downloading` — не атомарно: два параллельных вебхука могут оба пройти проверку и оба запустить `download_and_process_meeting` → двойная скачка/двойной ffmpeg/двойные YouGile-карточки), `src/db/models.py:392` (нет UNIQUE на `mtslink_record_id`), `src/main.py:60-70` (нет sweeper'а по старым встречам), `webhook_server.py:263-267` (status="failed" → повторный вебхук навсегда пропускается стейт-гейтом).

  **Сценарий:** 1) МТС присылает webhook дважды (retry) → две задачи в YouGile из одной встречи. 2) Webhook не пришёл вовсе (нет `PUBLIC_WEBHOOK_URL` при создании — `meeting_room.py:100-105`) → встреча навсегда в `active` с пометкой «запись...» в UI. 3) Остановка сервера (`webhook_server.py:296-305`, отмена задач через 30с) → встреча застряла в `downloading`/`processing` → повторная доставка вебхука отбита гейтом.

  **Продуктовое решение:** атомарный CAS-переход: `UPDATE meetings SET status='downloading' WHERE id=:id AND status IN ('recording','active')`, проверять rowcount; UNIQUE-индекс на `mtslink_record_id`; reaper: встречи в `active` старше N часов → `failed` + уведомление; «failed»-встречи допускать к повторной обработке (или ручная кнопка «повторить»).

  ### 3.6 🟠 MTS-встреча, созданная из ЛС, не может быть обработана в принципе

  **Файлы:** `src/bot/handlers/free_text.py:1553-1565` (DM-флоу: `team_chat_id=message.chat.id` — **id приватного чата**), `src/services/meeting_room.py:107-115` → `src/db/repo.py:1348-1362` (`update_team_mtslink_token` ищет команду по chat_id приватного чата → None → токен **молча не сохраняется**), `webhook_server.py:137-142` (нет `team.mtslink_token` → `status="failed"`).

  **Сценарий:** владелец в ЛС говорит «запланируй встречу на МТС» → ссылка создана, бот в ЛС сообщает «всё готово», а вебхук при записи находит встречу без токена → `failed`. Пользователь видит успех, а потом «Ошибка обработки».

  **Продуктовое решение:** в DM-флоу резолвить команду через `get_team_by_owner`/выбор команды пользователем и сохранять токен по `team.chat_id`; либо явно сообщать «MTS-встречи доступны только из командного чата».

  ### 3.7 🟡 Каскад удаления команды: код удаления отсутствует, ORM-каскад для meetings хрупкий

  **Файлы:** `src/db/models.py:265` (Meeting в relationship без cascade), `:389` (FK `ondelete="CASCADE"`), `models.py:320, 346, 361, 375, 486, 499, 559` (7 таблиц ссылаются на teams.id **без** ORM-relationship — только DB-cascade), `repo.py` — нет `delete_team`.

  **Сценарий (латентный):** будущий `session.delete(team)` сломается на Meeting (NOT NULL FK + нет `passive_deletes` → SQLAlchemy попытается обнулить `team_id` → IntegrityError); для ActivitySession/SociometryCache/YouGileUserAlias/PendingInvite/PendingTask/EmailMessage — работает только если включены FK на уровне БД (Postgres — да, SQLite-дев-среда — нет). UserSettings участников не привязаны к команде и осиротеют при любом удалении (это ок, но учитывать).

  **Продуктовое решение:** явная функция `delete_team` с ручным удалением всех дочерних таблиц в одной транзакции + `passive_deletes="all"` на relationship'ах; тест-кейс удаления команды.

  ---

  ## Вектор 4: Feature Collisions

  ### 4.1 🔴 «да/ок»-ответ администратора съедается `confirm_task_approval` без пропуска дальше — standup/pulse/диалог умирают

  **Файлы:** `src/group_bot/handlers/free_text.py:324-335` — `confirm_task_approval` матчит **любой** reply с текстом да/ok/ок/подтверждаю; если в ответе нет «заявка #N» — `return` **без** `raise SkipHandler` (в aiogram 3 это останавливает распространение).

  **Сценарий:** 1) Админ — единственный, у кого этот хендлер активен (`is_admin` guard, `:329`). 2) Бот задаёт пульс-опрос (кнопки) или стендап-промпт. 3) Админ отвечает «да» на пульс-сообщение → `confirm_task_approval` ловит, не находит «заявка #N», `return` → **дальше ничего не выполняется**: ни `handle_standup_reply` (`standup.py:115-128`), ни group_free_text, ни текстовые ответы на пульс. Ответ потерян (пульс ждёт кнопку, но текстовый ответ тоже должен был быть обработан). Любой «да» в диалоге админа с ботом глотается.

  **Продуктовое решение:** заменить `return` на `raise SkipHandler` во всех ветках, где сообщение не относится к заявке (`free_text.py:332-335, 340-341`); добавить guard на `reply_to_message.from_user.is_bot` + проверку, что реплай — сообщение бота про заявку.

  ### 4.2 🟠 Standup- и pulse-шедулеры не взаимоисключают друг друга — постинг в один чат в одну минуту

  **Файлы:** `src/core/standup_scheduler.py:50-76` и `src/group_bot/activities/scheduler.py:125-156` — оба итерируют `select(Team)` и сравнивают `team.standup_time` / `team.pulse_time` с текущим временем; оба используют `Europe/Moscow`; оба `bot.send_message(chat_id=team.chat_id)` без проверки «уже есть открытый опрос». Оба времени пользовательские (`standup_time` и `pulse_time`).

  **Сценарий:** `standup_time = 17:00`, `pulse_time = 17:00` → в чат одновременно падают промпт стендапа и пульс с кнопками; ответы админа «да» на пульс глотаются по 4.1; визуально сообщения перемешиваются, никто не понимает, на что отвечать.

  **Продуктовое решение:** единый диспетчер фоновых постов на команду: перед постингом пульса проверять «открытый standup в этом чате сегодня» (и наоборот), при совпадении времени — сдвигать пульс на +N минут; блокировка на уровне команды (в БД или in-memory mutex по team.id).

  ### 4.3 🟠 Привязка транскрипции МТС — только по event_id, без измерения «пользователь/команда»

  **Файлы:** `src/services/webhook_server.py:22-38` (`extract_event_id` — эвристика, принимает `eventId`/`event_id`/`session_id`/вложенные варианты, первый непустой побеждает; приоритет top-level `session_id` может перебить вложенный `eventId`), `:113-118` (поиск `get_meeting_by_mtslink_id` → fallback по session_id, **глобально, без фильтра user/team**), `src/services/mtslink_api.py:10-34` (webhook регистрируется на весь аккаунт, без фильтра событий), `src/bot/handlers/free_text.py:1616-1619` (`join_meeting` по URL создаёт Meeting **без** event_id/session_id → вебхук никогда не найдёт строку → «No meeting found» → транскрипция молча теряется), `src/db/repo.py:1183-1193` (dedupe-trap: `create_meeting` возвращает существующую строку по (team, url, active) — новая запись того же URL никогда не совпадёт с новым event_id).

  **Сценарий (вопрос аудита «два чата одновременно»):** один telegram_id запускает записи в двух командах — коллизии нет, пока event_id уникальны и распарсились. Реальные потери: (1) webhook с нераспарсенным event_id → тихо дропается (`:81-84`); (2) встреча через «присоединиться по ссылке» → транскрипт теряется навсегда; (3) ручная запись в интерфейсе МТС (не через бота) → «No meeting found»; (4) транскрипт всегда постится в чат команды-создателя, даже если пользователь делился этой же записью с другой командой — «кто записал»/«куда предназначалось» не хранится.

  **Продуктовое решение:** (а) хранить при создании встречи `mtslink_session_id` **обязательно** (в `join_meeting` — резолвить через API: `get_session_by_event`); (б) подтвердить формат вебхука на живых данных и зафиксировать `extract_event_id` (отдать приоритет `eventId`/`data.eventId` над `session_id`); (c) при «No meeting found» — не дропать молча, а слать диагностическое сообщение owner'у команды; (d) положить в Meeting поле «ожидаемый чат доставки» (`expected_chat_id`), а не выводить из `team.chat_id`.

  ### 4.4 🟡 Фоновые дайджесты не портят контекст LLM, но reply на дайджест бессмысленен

  **Файлы:** `src/core/conversation_context.py:23` — стор ключуется **только по user_id** (не по чату), maxlen=8 ходов; пишется только из `free_text.py:1167, 1441, 1473, 1491`; `src/core/evening_digest.py:104-107` → `notifier.notify` (бот шлёт в тот же DM владельца, где идёт диалог; без reply_to; в контекст не попадает).

  **Сценарий:** 1) Владелец отвечает на сообщение вечернего дайджеста «что это за задача?» → `_process_text` не читает `reply_to_message` → LLM получает только текущий текст без контекста дайджеста → бессмысленный ответ. 2) Если владелец в будущем начнёт писать боту из нескольких чатов (группа с тегом бота), контекст user_id смешается между чатами.

  **Продуктовое решение:** при `message.reply_to_message` от бота — подмешивать текст цитируемого сообщения в промпт; ключ контекста сделать `(user_id, chat_id)`.

  ### 4.5 🟡 Встречи, заброшенные при остановке, навсегда не обрабатываются

  **Файлы:** `src/services/webhook_server.py:296-305` (shutdown ждёт 30с и отменяет задачи), `:126-128` (стейт-гейт) — встреча в `downloading`/`processing` после отмены: повторный вебхук отбит, reaper'а нет.

  **Продуктовое решение:** см. 3.5 (reaper + допуск статусов downloading/processing к повторной обработке после таймаута).

  ---

  ## Сводная таблица приоритетов

  | # | Уязвимость | Вектор | Критичность |
  |---|---|---|---|
  | 1.1 | Крэш на нетекстовом вводе в state, вечный застрев | FSM | 🔴 |
  | 1.2 | `/start`/меню не чистят FSM — кража следующего сообщения | FSM | 🔴 |
  | 2.1 | Уход owner'а = перманентный dead-end (нет передачи/удаления) | RBAC | 🔴 |
  | 2.2 | Исключённые остаются членами; `link_yougile` возвращает доступ | RBAC | 🔴 |
  | 3.1 | Протухший токен → фейковый «✅ Задачи созданы!» | Invariants | 🔴 |
  | 4.1 | «да/ок» админа глотается без SkipHandler — standup/pulse мертвы | Collision | 🔴 |
  | 1.3 | Старые inline-кнопки перехватывают FSM | FSM | 🟠 ✅ |
  | 1.4 | Онбординг без отмены | FSM | 🟠 |
  | 2.3 | IDOR `kanban:link_confirm` | RBAC | 🟠 |
  | 2.4 | Канбан-роутер без фильтров в групповом чате | RBAC | 🟠 |
  | 2.5 | RBAC не подключён / fail-open | RBAC | 🟠 |
  | 3.2 | `processing` зависает навсегда; ветка pending-tasks осиротела | Invariants | 🟠 |
  | 3.3 | Переподключение YouGile: stale `active_board_id` | Invariants | 🟠 |
  | 3.4 | Параллельный `/setup_yougile` перетирает FSM | Invariants | 🟠 |
  | 3.5 | Гонка вебхуков + зависшие встречи без reaper'а | Invariants | 🟠 |
  | 3.6 | MTS-встреча из ЛС не обрабатывается (токен в приватный chat_id) | Invariants | 🟠 |
  | 4.2 | Standup и пульс без взаимоисключения | Collision | 🟠 |
  | 4.3 | Вебхук МТС: эвристика event_id, потеря транскриптов | Collision | 🟠 |
  | 1.5 | Голос молча сбрасывает FSM | FSM | 🟡 |
  | 1.6 | FSM встречи переживает отправку файла | FSM | 🟡 |
  | 2.6 | Owner-less команда создаётся фоном | RBAC | 🟡 |
  | 2.7 | Мёртвые кнопки отключения интеграции | RBAC | 🟡 |
  | 3.7 | Удаления команды нет; ORM-каскад хрупкий | Invariants | 🟡 |
  | 4.4 | Reply на дайджест без контекста; контекст по user_id | Collision | 🟡 |
  | 4.5 | Заброшенные встречи при shutdown | Collision | 🟡 |
