import asyncio
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import text as sql_text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from src.config import settings

# Windows fix: psycopg requires SelectorEventLoop
if hasattr(asyncio, "WindowsSelectorEventLoopPolicy"):
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())


logger = logging.getLogger(__name__)

engine = create_async_engine(
    settings.database_url,
    future=True,
    pool_size=5,
    max_overflow=10,
    pool_pre_ping=True,
)
SessionLocal = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)


async def init_db() -> None:
    """Проверка схемы БД + FTS5 для SQLite. DDL — только через Alembic, не create_all."""
    async with engine.begin() as conn:
        # SQLite FTS5 виртуальная таблица (только для SQLite)
        if "sqlite" in settings.database_url:
            await conn.execute(sql_text("""
                CREATE VIRTUAL TABLE IF NOT EXISTS messages_fts USING fts5(
                    text,
                    content='messages',
                    content_rowid='id'
                );
            """))
            await conn.execute(sql_text("""
                CREATE TRIGGER IF NOT EXISTS messages_ai AFTER INSERT ON messages BEGIN
                  INSERT INTO messages_fts(rowid, text) 
                  VALUES (new.id, COALESCE(new.text, '') || ' ' || COALESCE(new.transcript, '') || ' ' || COALESCE(new.extracted_text, ''));
                END;
            """))
            await conn.execute(sql_text("""
                CREATE TRIGGER IF NOT EXISTS messages_ad AFTER DELETE ON messages BEGIN
                  INSERT INTO messages_fts(messages_fts, rowid, text) 
                  VALUES('delete', old.id, COALESCE(old.text, '') || ' ' || COALESCE(old.transcript, '') || ' ' || COALESCE(new.extracted_text, ''));
                END;
            """))
            await conn.execute(sql_text("""
                CREATE TRIGGER IF NOT EXISTS messages_au AFTER UPDATE ON messages BEGIN
                  INSERT INTO messages_fts(messages_fts, rowid, text) 
                  VALUES('delete', old.id, COALESCE(old.text, '') || ' ' || COALESCE(old.transcript, '') || ' ' || COALESCE(new.extracted_text, ''));
                  INSERT INTO messages_fts(rowid, text) 
                  VALUES (new.id, COALESCE(new.text, '') || ' ' || COALESCE(new.transcript, '') || ' ' || COALESCE(new.extracted_text, ''));
                END;
            """))

        # НЕдеструктивная проверка: не отстала ли БД от последней миграции
        def _check_revision(sync_conn) -> None:
            alembic_cfg = Config(str(Path(__file__).resolve().parent.parent.parent / "alembic.ini"))
            script = ScriptDirectory.from_config(alembic_cfg)
            context = MigrationContext.configure(sync_conn)
            current_rev = context.get_current_revision()
            heads = script.get_heads()

            if current_rev not in heads:
                logger.warning(
                    "БД на ревизии %s, а последняя — %s. "
                    "Выполните: alembic upgrade head",
                    current_rev, heads,
                )

        await conn.run_sync(_check_revision)


@asynccontextmanager
async def get_session() -> AsyncIterator[AsyncSession]:
    async with SessionLocal() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
