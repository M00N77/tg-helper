import logging

from pyngrok import conf, ngrok

from src.config import settings

logger = logging.getLogger(__name__)

_tunnel = None


async def start_tunnel() -> str | None:
    if not settings.NGROK_ENABLED:
        return settings.WEBHOOK_BASE_URL or None

    global _tunnel
    if settings.NGROK_AUTHTOKEN:
        conf.get_default().auth_token = settings.NGROK_AUTHTOKEN

    _tunnel = ngrok.connect(settings.WEBHOOK_PORT, "http")
    public_url = _tunnel.public_url
    logger.info("ngrok tunnel started: %s", public_url)
    return public_url


async def stop_tunnel() -> None:
    if _tunnel is not None:
        ngrok.disconnect(_tunnel.public_url)
        ngrok.kill()
