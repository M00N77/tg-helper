# Что нового — потоки данных и архитектура

Добавлено 11-12 июня 2026.

---

## Новые потоки данных

### 1. Ежедневные стендапы (`standup_scheduler.py`)
- **Шедулер** каждую минуту проверяет, пора ли постить стендап в групповой чат команды
- **Поток:** `standup_scheduler_loop` → проверка `standup_time` у Team → `_post_standup()` → send_message с шаблоном + сохраняет `standup_msg_id`
- Участники пишут ответы → парсятся в таблицу `standups` (done_today, plan_today, blockers, mood)
- **Фильтр:** только будни, команды с `standup_enabled=true`

### 2. Эскалация блокеров (`standup_scheduler.py`, 2-й loop)
- **Поток:** `blocker_escalation_loop` (каждые 30 мин) → выбирает `Blockers WHERE status='open'` → вычисляет возраст блокера → если превышает порог severity → пост в чат команды
- **Пороги:** critical=1ч, high=4ч, medium=24ч, low=72ч
- Новая таблица `blockers` (severity, статус эскалации)

### 3. Пульс-опросы команды (`activities/scheduler.py`)
- **Новый модуль** — `src/group_bot/activities/` с паттерном **Strategy/Plugin**
- `activities_scheduler_loop` каждую минуту проверяет `team.pulse_time` → запускает `PulsePoll`
- Анонимный опрос настроения (1-5), случайный вопрос-метафора
- **Авто-закрытие:** после `pulse_auto_close_minutes` → итоги публикуются реплаем
- Таблицы: `activity_sessions`, `activity_responses`
- **Расширяемо:** новая активность = новый класс в `registry.py`, без правки scheduler/handler

### 4. Анализ тональности сообщений (`message_sentiments`)
- Новая таблица `message_sentiments` (team_id, user_id, sentiment, display_name)
- Фиксирует эмоциональное состояние участников команды

### 5. Риски сообщений (`message_risks`)
- Новая таблица `message_risks` (message_text, risk_reason, yougile_task_id)
- Автоматическое выявление рисков в сообщениях группы

### 6. Ожидающие задачи команды (`pending_team_tasks`)
- Таблица для задач, созданных через групповой чат, которые ещё не ушли в YouGile
- Статусы: pending → synced → failed

### 7. Email-интеграция (`email_messages`)
- Новая таблица `email_messages` (subject, body, sender, deadline, commitment_id)
- Письма с дедлайнами привязываются к обязательствам (commitments)

---

## Архитектурные изменения

### Новые фоновые задачи в `main.py:60-70`

Было **5** задач, стало **9**:

| Задача | Назначение |
|---|---|
| `digest-scheduler` | утренний дайджест (был) |
| `evening-digest` | вечерний дайджест (был) |
| `reminders-loop` | напоминания (был) |
| `news-scheduler` | новости (был) |
| `auto-sync` | синхронизация (был) |
| **`trash-cleaner`** | **hard-delete корзины Commitments раз в час** |
| **`standup-scheduler`** | **постинг стендапов** |
| **`blocker-escalation`** | **эскалация блокеров** |
| **`activities-scheduler`** | **пульс-опросы** |

### Новые таблицы БД (+8)

`standups`, `blockers`, `time_logs`, `sociometry_cache`, `email_messages`, `activity_sessions`, `activity_responses`, `message_sentiments`, `message_risks`, `pending_team_tasks`

### Новые поля в существующих таблицах

- `messages.reply_to_msg_id` — поддержка ответов на сообщения
- `teams.standup_enabled / standup_time / standup_msg_id` — настройки стендапов
- `teams.activities_enabled / pulse_time` — настройки пульс-опросов
- `teams.pulse_auto_close_minutes` — таймаут авто-закрытия

### Новые интенты LLM-агента в `agent.py`

- `trash_task` / `restore_task` — корзина обязательств
- `create_task`, `show_boards`, `move_task` — Kanban (ранее отсутствовали в основном агенте)
- Для группы: `create_task_for`, `show_my_tasks`, `edit_task`, `transfer_deadline`, `change_assignee`, `close_task`, `comment_task`

### Soft-delete корзины

- `commitments.deleted_at` — мягкое удаление обязательств
- `_clean_trash_loop` — hard-delete по истечении срока
- Интенты `trash_task` / `restore_task` для управления через free-text

---

**Итого:** проект эволюционировал из персонального AI-ассистента в командный PM-инструмент с полноценными стендапами, пульс-командой, эскалацией блокеров, аналитикой тональности/рисков и Kanban-интеграцией. Теперь 9 фоновых задач и ~28 таблиц в БД.
