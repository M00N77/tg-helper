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
    """Расшифровывает значение.
    Если передан None — возвращает None.
    Если передан Fernet-токен (начинается с gAAAAA), но расшифровка не удалась (неверный ключ / повреждение) —
    возвращает None (fail-fast, предотвращает утечку шифротекста в сторонние API).
    Если это легаси plaintext (не является Fernet-токеном) — возвращает строку как есть.
    """
    if value is None:
        return None
    try:
        return _fernet.decrypt(value.encode()).decode()
    except InvalidToken:
        if value.startswith("gAAAAA"):
            logger.error(
                "try_decrypt: InvalidToken on Fernet token! ENCRYPTION_KEY mismatch or corrupted data. "
                "Returning None to prevent ciphertext leakage. value_hash=%s",
                hashlib.sha256(value.encode()).hexdigest()[:12],
            )
            return None
        logger.warning(
            "try_decrypt: unencrypted legacy data encountered, returning as is. value_hash=%s",
            hashlib.sha256(value.encode()).hexdigest()[:12],
        )
        return value
    except ValueError:
        if value.startswith("gAAAAA"):
            return None
        logger.warning("try_decrypt: ValueError, returning as is. value_hash=%s", hashlib.sha256(value.encode()).hexdigest()[:12])
        return value
