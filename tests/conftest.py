import asyncio
import sys
from pathlib import Path

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.config import settings

if hasattr(asyncio, "WindowsSelectorEventLoopPolicy"):
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())


@pytest_asyncio.fixture
async def session():
    engine = create_async_engine(settings.database_url, future=True, pool_size=1, max_overflow=0)
    async_session_factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    async with async_session_factory() as s:
        async with s.begin():
            yield s
    await engine.dispose()


@pytest_asyncio.fixture
async def raw_conn():
    engine = create_async_engine(settings.database_url, future=True, pool_size=1, max_overflow=0)
    async with engine.connect() as conn:
        yield conn
    await engine.dispose()
