"""GigaChat provider — синхронный SDK, обёрнутый в asyncio.to_thread()."""
import asyncio

from gigachat import GigaChat
from gigachat.models import Chat, Messages, MessagesRole

from src.llm.base import ChatMessage


class GigaChatProvider:
    name = "gigachat"

    def __init__(self, credentials: str) -> None:
        self._credentials = credentials

    async def validate_key(self) -> bool:
        try:
            def _check() -> None:
                with GigaChat(
                    credentials=self._credentials,
                    verify_ssl_certs=False,
                ) as client:
                    client.get_models()
            await asyncio.to_thread(_check)
            return True
        except Exception:
            return False

    async def chat(self, messages: list[ChatMessage], *, heavy: bool = False) -> str:
        model = "GigaChat-Pro" if heavy else "GigaChat"

        def _call() -> str:
            with GigaChat(
                credentials=self._credentials,
                verify_ssl_certs=False,
                model=model,
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

        return await asyncio.to_thread(_call) or ""

    async def embed(self, text: str) -> list[float]:
        raise NotImplementedError(
            "GigaChat не поддерживает embeddings, используй OpenAI для поиска"
        )
