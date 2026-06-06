"""Проверка подключения к БД и наличия всех таблиц с правильной структурой."""

from sqlalchemy import text

from src.db.models import Base


async def _table_names(raw_conn):
    result = await raw_conn.execute(
        text("SELECT tablename FROM pg_catalog.pg_tables WHERE schemaname = 'public'")
    )
    return {row[0] for row in result}


async def _columns_for(raw_conn, table: str):
    result = await raw_conn.execute(
        text("""
            SELECT column_name, data_type, is_nullable, column_default
            FROM information_schema.columns
            WHERE table_schema = 'public' AND table_name = :t
            ORDER BY ordinal_position
        """),
        {"t": table},
    )
    return {(r[0], r[1], r[2]) for r in result}


EXPECTED_TABLES = {
    "users",
    "user_settings",
    "telegram_sessions",
    "api_keys",
    "contacts",
    "messages",
    "commitments",
    "auto_reply_logs",
    "index_jobs",
    "transcription_cache",
    "pending_actions",
    "news_topics",
    "alembic_version",
}


async def test_db_connection(raw_conn):
    result = await raw_conn.execute(text("SELECT 1 AS ok"))
    assert result.scalar_one() == 1


async def test_db_version(raw_conn):
    result = await raw_conn.execute(text("SELECT version()"))
    ver = result.scalar_one()
    assert "PostgreSQL" in ver


async def test_all_tables_exist(raw_conn):
    tables = await _table_names(raw_conn)
    missing = EXPECTED_TABLES - tables
    assert not missing, f"Missing tables: {missing}"


async def test_users_table_columns(raw_conn):
    cols = await _columns_for(raw_conn, "users")
    col_names = {c[0] for c in cols}
    assert "id" in col_names
    assert "telegram_id" in col_names
    assert "created_at" in col_names


async def test_user_settings_table_columns(raw_conn):
    cols = {c[0] for c in await _columns_for(raw_conn, "user_settings")}
    assert "user_id" in cols
    assert "auto_reply_enabled" in cols
    assert "llm_provider" in cols
    assert "timezone" in cols
    assert "digest_time" in cols


async def test_telegram_sessions_table_columns(raw_conn):
    cols = {c[0] for c in await _columns_for(raw_conn, "telegram_sessions")}
    assert "user_id" in cols
    assert "api_id" in cols
    assert "api_hash_enc" in cols
    assert "session_string_enc" in cols


async def test_contacts_table_columns(raw_conn):
    cols = {c[0] for c in await _columns_for(raw_conn, "contacts")}
    assert "peer_id" in cols
    assert "display_name" in cols
    assert "style_profile" in cols
    assert "is_news_source" in cols


async def test_messages_table_columns(raw_conn):
    cols = {c[0] for c in await _columns_for(raw_conn, "messages")}
    assert "message_id" in cols
    assert "text" in cols
    assert "is_outgoing" in cols
    assert "kind" in cols
    assert "indexed_in_vector" in cols


async def test_commitments_table_columns(raw_conn):
    cols = {c[0] for c in await _columns_for(raw_conn, "commitments")}
    assert "direction" in cols
    assert "deadline_at" in cols
    assert "status" in cols


async def test_json_columns_exist(raw_conn):
    contacts_cols = await _columns_for(raw_conn, "contacts")
    pending_cols = await _columns_for(raw_conn, "pending_actions")

    style_profile_types = {c[1] for c in contacts_cols if c[0] == "style_profile"}
    payload_types = {c[1] for c in pending_cols if c[0] == "payload"}

    assert any("json" in t.lower() for t in style_profile_types), (
        f"style_profile should be JSON type, got {style_profile_types}"
    )
    assert any("json" in t.lower() for t in payload_types), (
        f"payload should be JSON type, got {payload_types}"
    )


async def test_models_metadata_match(raw_conn):
    """Проверяет, что все колонки из моделей есть в БД."""
    for name, table in Base.metadata.tables.items():
        pg_columns = await _columns_for(raw_conn, name)
        pg_col_names = {c[0] for c in pg_columns}

        model_col_names = {c.name for c in table.columns}

        missing_in_pg = model_col_names - pg_col_names
        assert not missing_in_pg, (
            f"Table '{name}': columns {missing_in_pg} exist in model but not in PostgreSQL"
        )


async def test_alembic_version_table(raw_conn):
    result = await raw_conn.execute(text("SELECT version_num FROM alembic_version"))
    version = result.scalar_one()
    assert version is not None
    assert len(version) > 0
