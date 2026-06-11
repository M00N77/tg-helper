import hashlib
import hmac

from cryptography.fernet import Fernet, InvalidToken

from src.config import settings


_fernet = Fernet(settings.encryption_key.encode())

_HMAC_KEY = settings.encryption_key.encode()


def respondent_hash(telegram_id: int, session_id: int) -> str:
    """Стабильный анонимный идентификатор респондента в рамках одной сессии активности.

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
