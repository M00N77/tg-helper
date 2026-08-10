"""GigaChat provider — синхронный SDK, обёрнутый в asyncio.to_thread()."""
import asyncio
import logging
from pathlib import Path

from gigachat import GigaChat
from gigachat.exceptions import AuthenticationError as GigaAuthError
from gigachat.exceptions import GigaChatException, ResponseError
from gigachat.models import Chat, Messages, MessagesRole

logger = logging.getLogger(__name__)

from src.llm.base import (
    AuthError,
    ChatMessage,
    LLMError,
    ProviderTimeoutError,
    RateLimitedError,
    ServerError,
)

_CERT_PATH = Path(__file__).resolve().parent.parent.parent / "certificates"
_CA_BUNDLE: Path | None = None
if _CERT_PATH.is_dir():
    for p in _CERT_PATH.iterdir():
        if p.suffix.lower() in (".cer", ".pem") and p.is_file():
            _CA_BUNDLE = p
            break


def _client_kwargs() -> dict:
    if _CA_BUNDLE:
        return {"verify_ssl_certs": True, "ca_bundle_file": str(_CA_BUNDLE)}
    return {"verify_ssl_certs": False}


def translate_gigachat_error(exc: Exception) -> LLMError:
    """Переводит SDK-исключения gigachat в иерархию LLMError.

    У SDK нет структурированных кодов статуса, поэтому при неизвестной
    ошибке делаем аккуратную эвристику по тексту (без слепых retry).
    """
    if isinstance(exc, GigaAuthError):
        return AuthError(str(exc))
    text = str(exc).lower()
    if isinstance(exc, (ResponseError, GigaChatException)):
        if "429" in text or "rate" in text or "лимит" in text:
            return RateLimitedError(message=str(exc))
        if "401" in text or "unauthorized" in text or "авториз" in text:
            return AuthError(str(exc))
        if "timeout" in text or "таймаут" in text or "timed out" in text:
            return ProviderTimeoutError(str(exc))
        if "5" in text and ("server" in text or "internal" in text):
            return ServerError(str(exc))
        # 4xx/перманентные ошибки SDK — без retry
        return LLMError(str(exc))
    if "timeout" in text or "timed out" in text:
        return ProviderTimeoutError(str(exc))
    return LLMError(str(exc))


class GigaChatProvider:
    name = "gigachat"

    def __init__(self, credentials: str) -> None:
        self._credentials = credentials

    async def validate_key(self) -> bool:
        try:
            def _check() -> None:
                with GigaChat(
                    credentials=self._credentials,
                    **_client_kwargs(),
                ) as client:
                    client.get_models()
            await asyncio.to_thread(_check)
            return True
        except Exception as exc:
            logger.warning("GigaChat validate_key failed: %s", exc)
            return False

    async def chat(self, messages: list[ChatMessage], *, heavy: bool = False) -> str:
        model = "GigaChat-Pro" if heavy else "GigaChat"

        def _call() -> str:
            with GigaChat(
                credentials=self._credentials,
                model=model,
                **_client_kwargs(),
            ) as client:
                payload = Chat(messages=[
                    Messages(
                        role=MessagesRole.SYSTEM
                        if m.role == "system"
                        else MessagesRole.USER
                        if m.role == "user"
                        else MessagesRole.ASSISTANT,
                        content=m.content,
                    )
                    for m in messages
                ])
                return client.chat(payload).choices[0].message.content

        try:
            return await asyncio.to_thread(_call) or ""
        except Exception as exc:
            logger.exception("Ошибка авторизации GigaChat: проверьте ключи в настройках")
            raise translate_gigachat_error(exc) from exc

    async def embed(self, text: str) -> list[float]:
        raise NotImplementedError(
            "GigaChat не поддерживает embeddings, используй OpenAI для поиска"
        )