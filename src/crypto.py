import hashlib
import hmac
import logging

from cryptography.fernet import Fernet, InvalidToken

from src.config import settings

logger = logging.getLogger(__name__)


_fernet = Fernet(settings.encryption_key.encode())

_HMAC_KEY = settings.encryption_key.encode()


def respondent_hash(telegram_id: int, session_id: int) -> str:
    """Псевдонимный идентификатор респондента в рамках одной сессии активности.

    HMAC-SHA256 от (telegram_id, session_id) с секретом приложения. Один и тот же
    человек в одной сессии даёт один и тот же хеш (для дедупликации голосов),
    но восстановить telegram_id из хеша нельзя, и в разных сессиях хеши разные.
    """
    msg = f"{telegram_id}:{session_id}".encode()
    return hmac.new(_HMAC_KEY, msg, hashlib.sha256).hexdigest()


def encrypt(plaintext: str) -> str:
    return _fernet.encrypt(plaintext.encode()).decode()


def decrypt(ciphertext: str) -> str:
    try:
        return _fernet.decrypt(ciphertext.encode()).decode()
    except InvalidToken as exc:
        raise ValueError("Не удалось расшифровать: неверный ключ или повреждённые данные") from exc


def try_decrypt(value: str | None) -> str | None:
    """Расшифровывает значение, но если оно не является валидным Fernet-токеном
    (например, легаси-данные, сохранённые в открытом виде до внедрения шифрования),
    возвращает его как есть. Используется для прозрачной миграции на шифрование.
    """
    if value is None:
        return None
    try:
        return _fernet.decrypt(value.encode()).decode()
    except (InvalidToken, ValueError):
        logger.warning("try_decrypt: InvalidToken для %s… Возможно, изменился ENCRYPTION_KEY в .env", str(value)[:30])
        return value
