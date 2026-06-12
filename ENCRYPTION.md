# Шифрование

## Механизм

**`src/crypto.py`** — Fernet (AES-128-CBC + HMAC-SHA256, симметричное шифрование).

Ключ — `encryption_key` из `src/config.py` (base64-строка).

## Слои шифрования

| Слой | Файл | Описание |
|------|------|----------|
| `encrypt()` / `decrypt()` | `src/crypto.py:25-33` | Прямые вызовы для ручного шифрования |
| `try_decrypt()` | `src/crypto.py:36-46` | Расшифровка с fallback на легаси-plaintext |
| `EncryptedString` | `src/db/models.py:27-51` | SQLAlchemy `TypeDecorator` — автоматическое шифрование на уровне ORM (прозрачно для кода) |
| `respondent_hash()` | `src/crypto.py:14-22` | HMAC-SHA256 односторонний хеш (не шифрование, а псевдонимизация) |

## Какие данные зашифрованы

| Данные | Таблица | Столбец | Метод | Зачем |
|--------|---------|---------|-------|-------|
| API hash Telegram | `telegram_sessions` | `api_hash_enc` | `encrypt()` в `repo.py:75` | Позволяет авторизоваться в MTProto |
| Session string Telegram | `telegram_sessions` | `session_string_enc` | `encrypt()` в `repo.py:76` | Активная сессия Telegram |
| Ключи LLM (OpenAI, Gemini, Groq, GigaChat) | `api_keys` | `key_enc` | `encrypt()` в `repo.py:105` | Платные API-ключи |
| Kanban token (YouGile) | `teams` | `kanban_token` | `EncryptedString` ORM (`models.py:268`) | Токен внешнего сервиса |
| MTS Link token | `teams` | `mtslink_token` | `EncryptedString` ORM (`models.py:273`) | Токен внешнего сервиса |
| ID респондента (псевдонимный) | `activity_responses` | — | `respondent_hash()` HMAC (`activities.py:22`) | Псевдонимизация голосов в пульс-опросах |

## Что НЕ шифруется

- Сообщения (text, transcript, extracted_text) — хранятся открыто
- Медиафайлы на диске
- Метаданные (даты, peer_id, message_id, sender_id)
- Настройки пользователя (кроме kanban_token, mtslink_token)
- Контакты, commitments, результаты опросов (кроме хеша респондента)

## Поток данных

```
Код приложения (открытый текст)
       │
       ▼
┌──────────────────────────────┐
│  EncryptedString ORM         │
│  └── process_bind_param()    │
│       → encrypt()            │
│  ┌── process_result_value()  │
│       → try_decrypt()        │
└──────────┬───────────────────┘
           ▼
      PostgreSQL (base64-токен Fernet)
```
