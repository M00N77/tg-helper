"""Тесты для src/core/news.py: build_news_digest, _cosine, news_scheduler_loop."""
from __future__ import annotations

import asyncio
import math
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.core.news import _cosine, build_news_digest, news_scheduler_loop


def _make_async_iter(items):
    async def _gen():
        for item in items:
            yield item
    return _gen()


def _make_post(
    text: str,
    channel_name: str = "TestChannel",
    channel_username: str = "test_ch",
    channel_peer_id: int = 123,
    message_id: int = 1,
    *,
    hours_ago: int = 1,
) -> dict:
    return {
        "channel_name": channel_name,
        "channel_username": channel_username,
        "channel_peer_id": channel_peer_id,
        "message_id": message_id,
        "date": datetime.now(timezone.utc) - timedelta(hours=hours_ago),
        "text": text,
    }


@pytest.fixture
def mock_session():
    s = AsyncMock()
    s.__aenter__.return_value = s
    s.__aexit__.return_value = False
    return s


@pytest.fixture
def mock_owner():
    owner = MagicMock()
    owner.id = 1
    owner.settings = MagicMock()
    owner.settings.use_heavy_model = False
    owner.settings.timezone = "Europe/Moscow"
    owner.settings.news_enabled = True
    owner.settings.news_digest_time = "07:00"
    return owner


@pytest.fixture
def mock_client():
    client = AsyncMock()
    return client


@pytest.fixture
def mock_channel():
    return MagicMock(
        peer_id=12345,
        display_name="Test Channel",
        username="test_channel",
        is_news_source=True,
    )


# ── _cosine ────────────────────────────────────────────────────────────────

class TestCosine:
    def test_cosine_identical_vectors(self):
        v = [1.0, 2.0, 3.0]
        assert _cosine(v, v) == pytest.approx(1.0)

    def test_cosine_orthogonal_vectors(self):
        a = [1.0, 0.0, 0.0]
        b = [0.0, 1.0, 0.0]
        assert _cosine(a, b) == pytest.approx(0.0)

    def test_cosine_empty_vectors(self):
        assert _cosine([], []) == 0.0
        assert _cosine([], [1.0]) == 0.0

    def test_cosine_different_lengths(self):
        assert _cosine([1.0, 0.0], [1.0]) == 0.0

    def test_cosine_zero_norm(self):
        assert _cosine([0.0, 0.0], [1.0, 0.0]) == 0.0


# ── build_news_digest ──────────────────────────────────────────────────────

class TestBuildNewsDigest:
    async def test_no_llm_key_returns_error(
        self, mock_session, mock_owner, mock_channel,
    ):
        with (
            patch("src.core.news.get_session", return_value=mock_session),
            patch("src.core.news.get_or_create_user", return_value=mock_owner),
            patch("src.core.news.list_contacts", return_value=[mock_channel]),
            patch("src.llm.router.build_provider", return_value=None),
        ):
            result = await build_news_digest(
                AsyncMock(), owner_telegram_id=1, topic="AI",
            )
            assert "LLM-ключ" in result

    async def test_no_channels_returns_error(
        self, mock_session, mock_owner,
    ):
        with (
            patch("src.core.news.get_session", return_value=mock_session),
            patch("src.core.news.get_or_create_user", return_value=mock_owner),
            patch("src.core.news.list_contacts", return_value=[]),
            patch("src.llm.router.build_provider", return_value=MagicMock()),
        ):
            result = await build_news_digest(
                AsyncMock(), owner_telegram_id=1, topic="AI",
            )
            assert "каналов" in result

    async def test_no_channels_fallback_to_all(
        self, mock_session, mock_owner,
    ):
        channel = MagicMock(peer_id=123, display_name="Ch", username="ch")
        with (
            patch("src.core.news.get_session", return_value=mock_session),
            patch("src.core.news.get_or_create_user", return_value=mock_owner),
            patch("src.core.news.list_contacts", side_effect=[[], [channel]]),
            patch("src.llm.router.build_provider", return_value=MagicMock()),
            patch("src.core.news._gather_posts", return_value=[]),
        ):
            result = await build_news_digest(
                AsyncMock(), owner_telegram_id=1, topic="AI",
            )
            assert "постов не нашёл" in result

    async def test_no_posts_returns_message(
        self, mock_session, mock_owner, mock_channel,
    ):
        provider = MagicMock()
        provider.chat = AsyncMock()
        provider.embed = AsyncMock()
        with (
            patch("src.core.news.get_session", return_value=mock_session),
            patch("src.core.news.get_or_create_user", return_value=mock_owner),
            patch("src.core.news.list_contacts", return_value=[mock_channel]),
            patch("src.llm.router.build_provider", return_value=provider),
            patch("src.core.news._gather_posts", return_value=[]),
        ):
            result = await build_news_digest(
                AsyncMock(), owner_telegram_id=1, topic="AI",
            )
            assert "постов не нашёл" in result

    async def test_embed_filters_irrelevant_posts(
        self, mock_session, mock_owner, mock_channel,
    ):
        posts = [
            _make_post("Python 3.13 released with JIT", channel_name="DevNews"),
            _make_post("New AI model beats benchmarks", channel_name="TechAI"),
            _make_post("Рецепт пирога с капустой", channel_name="Cooking"),
        ]
        topic_vec = [1.0, 0.0, 0.0]
        post1_vec = [0.9, 0.1, 0.0]
        post2_vec = [0.7, 0.3, 0.0]
        post3_vec = [0.0, 1.0, 0.0]

        provider = MagicMock()
        provider.chat = AsyncMock(return_value="<b>Дайджест</b>")
        provider.embed = AsyncMock(side_effect=[
            topic_vec, post1_vec, post2_vec, post3_vec,
        ])

        with (
            patch("src.core.news.get_session", return_value=mock_session),
            patch("src.core.news.get_or_create_user", return_value=mock_owner),
            patch("src.core.news.list_contacts", return_value=[mock_channel]),
            patch("src.llm.router.build_provider", return_value=provider),
            patch("src.core.news._gather_posts", return_value=posts),
        ):
            await build_news_digest(
                AsyncMock(), owner_telegram_id=1, topic="Python",
            )

        provider.chat.assert_called_once()
        user_msg = provider.chat.call_args[0][0][1]
        assert posts[0]["text"] in user_msg.content
        assert posts[1]["text"] in user_msg.content
        assert posts[2]["text"] not in user_msg.content

    async def test_embed_not_implemented_falls_to_keyword_filter(
        self, mock_session, mock_owner, mock_channel,
    ):
        posts = [
            _make_post("Latest Python news and updates", channel_name="DevNews"),
            _make_post("Cooking pasta recipe", channel_name="Cooking"),
            _make_post("Weather forecast for tomorrow", channel_name="Weather"),
        ]
        provider = MagicMock()
        provider.chat = AsyncMock(return_value="<b>Дайджест</b>")
        provider.embed = AsyncMock(side_effect=NotImplementedError)

        with (
            patch("src.core.news.get_session", return_value=mock_session),
            patch("src.core.news.get_or_create_user", return_value=mock_owner),
            patch("src.core.news.list_contacts", return_value=[mock_channel]),
            patch("src.llm.router.build_provider", return_value=provider),
            patch("src.core.news._gather_posts", return_value=posts),
        ):
            result = await build_news_digest(
                AsyncMock(), owner_telegram_id=1, topic="Python",
            )

            assert isinstance(result, str)
            provider.chat.assert_called_once()
            user_msg = provider.chat.call_args[0][0][1]
            assert "Python" in user_msg.content
            assert "Cooking" not in user_msg.content

    async def test_gigachat_embed_not_implemented_graceful(
        self, mock_session, mock_owner, mock_channel,
    ):
        posts = [
            _make_post("Some post about technology", channel_name="Tech"),
        ]
        provider = MagicMock()
        provider.chat = AsyncMock(return_value="дайджест")
        provider.embed = AsyncMock(side_effect=NotImplementedError)

        with (
            patch("src.core.news.get_session", return_value=mock_session),
            patch("src.core.news.get_or_create_user", return_value=mock_owner),
            patch("src.core.news.list_contacts", return_value=[mock_channel]),
            patch("src.llm.router.build_provider", return_value=provider),
            patch("src.core.news._gather_posts", return_value=posts),
        ):
            result = await build_news_digest(
                AsyncMock(), owner_telegram_id=1, topic="tech",
            )
            assert isinstance(result, str)
            assert len(result) > 0

    async def test_digest_result_sanitized(
        self, mock_session, mock_owner, mock_channel,
    ):
        posts = [
            _make_post("Important news", channel_name="NewsChannel"),
        ]
        provider = MagicMock()
        provider.chat = AsyncMock(return_value="<script>evil</script><b>ok</b>")
        provider.embed = AsyncMock(return_value=[0.1] * 3)

        with (
            patch("src.core.news.get_session", return_value=mock_session),
            patch("src.core.news.get_or_create_user", return_value=mock_owner),
            patch("src.core.news.list_contacts", return_value=[mock_channel]),
            patch("src.llm.router.build_provider", return_value=provider),
            patch("src.core.news._gather_posts", return_value=posts),
        ):
            result = await build_news_digest(
                AsyncMock(), owner_telegram_id=1, topic="News",
            )
            assert "<script>" not in result
            assert "<b>ok</b>" in result


# ── news_scheduler_loop ────────────────────────────────────────────────────

class TestNewsSchedulerLoop:
    async def test_scheduler_skips_if_disabled(
        self, mock_session, mock_owner,
    ):
        mock_owner.settings.news_enabled = False

        with (
            patch("src.core.news.get_session", return_value=mock_session),
            patch("src.core.news.get_or_create_user", return_value=mock_owner),
            patch("src.core.news.asyncio.sleep", side_effect=asyncio.CancelledError),
            patch("src.core.news.notifier") as mock_notifier,
        ):
            with pytest.raises(asyncio.CancelledError):
                await news_scheduler_loop()

        mock_notifier.notify.assert_not_called()

    async def test_scheduler_skips_if_no_topics(
        self, mock_session, mock_owner,
    ):
        mock_owner.settings.news_enabled = True
        mock_owner.settings.news_digest_time = "07:00"
        mock_now = datetime(2026, 6, 7, 7, 0, 0)

        with (
            patch("src.core.news.get_session", return_value=mock_session),
            patch("src.core.news.get_or_create_user", return_value=mock_owner),
            patch("src.core.news.now_in_tz", return_value=mock_now),
            patch("src.core.news.list_news_topics", return_value=[]),
            patch("src.core.news.asyncio.sleep", side_effect=asyncio.CancelledError),
            patch("src.core.news.notifier") as mock_notifier,
        ):
            with pytest.raises(asyncio.CancelledError):
                await news_scheduler_loop()

        mock_notifier.notify.assert_not_called()


# Список покрытых сценариев:
# TestCosine:
#   - identical_vectors → 1.0
#   - orthogonal_vectors → 0.0
#   - empty_vectors → 0.0
#   - different_lengths → 0.0
#   - zero_norm → 0.0
# TestBuildNewsDigest:
#   - no_llm_key → error msg
#   - no_channels → error msg
#   - fallback from only_marked_sources to all channels
#   - no_posts → "постов не нашёл"
#   - embed filter excludes low-similarity posts
#   - embed NotImplementedError → keyword fallback
#   - embed NotImplementedError graceful handling
#   - sanitize_html strips <script>, keeps <b>
# TestNewsSchedulerLoop:
#   - disabled setting → notify not called
#   - enabled but no topics → notify not called
