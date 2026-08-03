import asyncio
import logging

from src.crypto import decrypt, encrypt, try_decrypt

logger = logging.getLogger(__name__)


class CryptoService:
    """Асинхронная обёртка над синхронным Fernet-шифрованием.

    Вызовы Fernet выносятся в пул потоков через asyncio.to_thread, чтобы
    не блокировать event loop. Используется бизнес-логикой (сервисами и
    хендлерами): они шифруют значения перед записью в репозиторий и
    расшифровывают после чтения.
    """

    async def encrypt_data(self, plaintext: str) -> str:
        return await asyncio.to_thread(encrypt, plaintext)

    async def decrypt_data(self, ciphertext: str | None, *, fallback_raw: bool = False) -> str | None:
        """Расшифровывает значение.

        fallback_raw=True сохраняет поведение try_decrypt: если значение
        не является валидным Fernet-токеном (легаси-данные в открытом виде,
        смена ENCRYPTION_KEY), возвращает его как есть вместо ошибки.
        """
        if ciphertext is None:
            return None
        if fallback_raw:
            return await asyncio.to_thread(try_decrypt, ciphertext)
        return await asyncio.to_thread(decrypt, ciphertext)


crypto_service = CryptoService()
