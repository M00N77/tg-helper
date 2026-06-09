import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from sqlalchemy import text as sql_text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from src.config import settings
from src.db.models import Base

# Windows fix: psycopg requires SelectorEventLoop
if hasattr(asyncio, "WindowsSelectorEventLoopPolicy"):
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())


engine = create_async_engine(
    settings.database_url,
    future=True,
    pool_size=5,
    max_overflow=10,
    pool_pre_ping=True,
)
SessionLocal = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)


async def init_db() -> None:
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

        # Автоматическое создание FTS5 виртуальной таблицы и триггеров для SQLite
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
                  VALUES('delete', old.id, COALESCE(old.text, '') || ' ' || COALESCE(old.transcript, '') || ' ' || COALESCE(old.extracted_text, ''));
                END;
            """))
            await conn.execute(sql_text("""
                CREATE TRIGGER IF NOT EXISTS messages_au AFTER UPDATE ON messages BEGIN
                  INSERT INTO messages_fts(messages_fts, rowid, text) 
                  VALUES('delete', old.id, COALESCE(old.text, '') || ' ' || COALESCE(old.transcript, '') || ' ' || COALESCE(old.extracted_text, ''));
                  INSERT INTO messages_fts(rowid, text) 
                  VALUES (new.id, COALESCE(new.text, '') || ' ' || COALESCE(new.transcript, '') || ' ' || COALESCE(new.extracted_text, ''));
                END;
            """))


@asynccontextmanager
async def get_session() -> AsyncIterator[AsyncSession]:
    async with SessionLocal() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
