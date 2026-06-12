# Поток данных корпоративной Wiki в Telegram

## 1. Пополнение базы знаний (Write Flow)

```mermaid
flowchart TB
    User([Пользователь]) -->|сообщение / файл / голосовое| TG[Telegram]

    TG --> UBot[Userbot Telethon]
    UBot -->|зеркалит| DB[(PostgreSQL
        сырые сообщения)]
    UBot -->|скачивает PDF/DOCX/TXT/MD| Doc[Парсинг документов
        pypdf / python-docx]
    UBot -->|голосовые .ogg| Whis[Whisper
        распознавание речи]

    Doc -->|extracted_text| DB
    Whis -->|transcript| DB

    DB --> Handler[Обработчик aiogram
        команда "сохрани в БЗ"]
    Handler --> LLM[LLM Provider
        извлекает заголовок,
        категорию, теги]

    LLM --> Embed[LLM Embedding
        text-embedding-3-small
        / text-embedding-004]
    Embed --> Q[(Qdrant
        коллекция "wiki"
        COSINE distance)]

    LLM --> Pg[(PostgreSQL
        wiki_articles
        title, content,
        tags, category)]
    LLM --> Fts[(FTS5
        полнотекстовый
        индекс BM25)]
```

## 2. Чтение / Q&A (Read Flow)

```mermaid
flowchart TB
    User([Пользователь]) -->|"спроси у БЗ: ..."| Agent[Agent Intent Router]

    Agent -->|intent: ask_wiki| LLM1[LLM
        извлекает суть запроса]

    LLM1 --> Query[Запрос]

    Query --> Embed[LLM Embedding]
    Embed --> QSearch[Qdrant search
        top-K статей]

    Query --> FtsSearch[FTS5 fallback
        полнотекстовый поиск]

    QSearch --> Context[Контекст:
        найденные статьи]
    FtsSearch --> Context

    Context --> LLM2[LLM формирует ответ
        с цитированием источников]

    LLM2 --> Answer([Ответ пользователю
        в Telegram])
```

## 3. Полная архитектура

```mermaid
flowchart LR
    subgraph Telegram
        UBot[Userbot]
        Bot[Control Bot]
    end

    subgraph Input [Источники данных]
        Msg[Сообщения]
        Doc[Документы PDF/DOCX/TXT]
        Voice[Голосовые .ogg]
    end

    subgraph Processing [Обработка]
        Whis[Whisper]
        Parser[pypdf / python-docx]
        Agent[Agent Router]
    end

    subgraph LLM_Layer [LLM]
        Chat[Chat GPT/Gemini/Llama]
        Embed[Embedding]
    end

    subgraph Storage [Хранилище]
        Qd[(Qdrant векторы)]
        PG[(PostgreSQL статьи)]
        FTS[(FTS5 индекс)]
    end

    Input --> Telegram
    Telegram --> Processing
    Processing --> LLM_Layer
    LLM_Layer --> Storage

    Storage -.->|RAG| Chat
    Chat --> Telegram
```

## 4. Компоненты

| Компонент | Статус |
|-----------|--------|
| Telegram API (aiogram + Telethon) | Готово |
| LLM провайдеры (OpenAI, Gemini, Groq, GigaChat) | Готово |
| Векторное хранилище (Qdrant embedded) | Готово |
| Распознавание голоса (Whisper local/API) | Готово |
| Парсинг документов (PDF, DOCX, TXT, MD) | Готово |
| Полнотекстовый поиск (FTS5) | Готово |
| Суммаризация (LLM) | Готово |
| Agent / Intent routing | Готово |
| Модель данных wiki_articles + wiki_categories | Нужно добавить |
| RAG pipeline (контекст → ответ с цитированием) | Нужно добавить |
| Интенты "save_to_wiki", "ask_wiki" | Нужно добавить |
| Новая коллекция в Qdrant ("wiki") | Нужно добавить |
| UI: команды /wiki_save, /wiki_ask, /wiki_list | Нужно добавить |

## 5. Резюме — два конвейера

**Ingestion Pipeline:** `Telegram → Userbot → DB → LLM (embed) → Qdrant`

**Retrieval Pipeline:** `Telegram → Agent → LLM (embed) → Qdrant (search) → LLM (answer) → Telegram`
