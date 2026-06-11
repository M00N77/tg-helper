import logging
import time

from pyngrok import conf, ngrok
from pyngrok.exception import PyngrokNgrokHTTPError

from src.config import settings

logger = logging.getLogger(__name__)

_tunnel = None

MAX_RETRIES = 3
RETRY_DELAY = 3


def _find_existing_tunnel(port: int) -> str | None:
    """Проверяет локальный ngrok API на туннель, уже открытый на нужный порт."""
    try:
        for t in ngrok.get_tunnels():
            if t.config.get("addr", "").endswith(f":{port}"):
                logger.info("Reusing existing ngrok tunnel: %s", t.public_url)
                return t.public_url
    except Exception:
        pass
    return None


def _connect_with_retry(port: int, proto: str = "http") -> str:
    """Пытается открыть туннель с N ретраями и человеческим сообщением об ошибке."""
    last_exc: Exception | None = None

    for attempt in range(MAX_RETRIES):
        try:
            tunnel = ngrok.connect(port, proto)
            logger.info("ngrok tunnel started: %s", tunnel.public_url)
            return tunnel.public_url
        except PyngrokNgrokHTTPError as exc:
            last_exc = exc
            error_text = str(exc)
            if "ERR_NGROK_334" in error_text or "already online" in error_text:
                logger.warning(
                    "ngrok endpoint занят (попытка %d/%d): %s",
                    attempt + 1, MAX_RETRIES, exc,
                )
            else:
                logger.warning(
                    "ngrok connect failed (попытка %d/%d): %s",
                    attempt + 1, MAX_RETRIES, exc,
                )
            ngrok.kill()
            if attempt < MAX_RETRIES - 1:
                time.sleep(RETRY_DELAY)
        except Exception as exc:
            last_exc = exc
            logger.warning(
                "ngrok connect error (попытка %d/%d): %s",
                attempt + 1, MAX_RETRIES, exc,
            )
            ngrok.kill()
            if attempt < MAX_RETRIES - 1:
                time.sleep(RETRY_DELAY)

    raise RuntimeError(
        f"ngrok tunnel не удалось запустить после {MAX_RETRIES} попыток.\n"
        f"Последняя ошибка: {last_exc}\n\n"
        f"Возможные причины и решения:\n"
        f"1. Free-домен ngrok занят другим процессом — "
        f"остановите старый туннель в дашборде https://dashboard.ngrok.com/tunnels/agents\n"
        f"2. Превышен лимит одновременных туннелей free-аккаунта\n"
        f"3. Дождитесь освобождения домена (обычно 1-2 минуты) и перезапустите бота."
    )


async def start_tunnel() -> str | None:
    if not settings.NGROK_ENABLED:
        return settings.WEBHOOK_BASE_URL or None

    global _tunnel
    if settings.NGROK_AUTHTOKEN:
        conf.get_default().auth_token = settings.NGROK_AUTHTOKEN

    existing = _find_existing_tunnel(settings.WEBHOOK_PORT)
    if existing:
        _tunnel = existing
        return existing

    public_url = _connect_with_retry(settings.WEBHOOK_PORT, "http")
    _tunnel = public_url
    return public_url


async def stop_tunnel() -> None:
    try:
        for t in ngrok.get_tunnels():
            ngrok.disconnect(t.public_url)
    except Exception:
        pass
    try:
        ngrok.kill()
    except Exception:
        pass
