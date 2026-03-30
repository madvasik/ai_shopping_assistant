# -*- coding: utf-8 -*-
"""
Утилиты для обнаружения и обработки сетевых ошибок при запросах к OpenAI API.
"""
import logging

logger = logging.getLogger(__name__)

# Сообщение для пользователя при сетевых ошибках (VPN / интернет)
NETWORK_ERROR_REPLY = (
    "Не удалось связаться с сервисом ИИ — проверьте подключение к интернету или VPN и повторите позже."
)


def is_network_error(exc: Exception) -> bool:
    """Возвращает True, если исключение вызвано сетевой/VPN проблемой.

    Включает:
    - APIConnectionError / APITimeoutError — нет соединения или таймаут
    - PermissionDeniedError (HTTP 403) — геоблок Cloudflare/OpenAI для региона без VPN
    - OSError с сетевыми признаками
    - httpx-ошибки (используются внутри openai SDK)
    """
    try:
        from openai import APIConnectionError, APITimeoutError, PermissionDeniedError
        if isinstance(exc, (APIConnectionError, APITimeoutError)):
            return True
        # 403 Forbidden от Cloudflare = геоблок для РФ без VPN
        if isinstance(exc, PermissionDeniedError):
            return True
    except ImportError:
        pass

    if isinstance(exc, OSError):
        msg = str(exc).lower()
        if any(w in msg for w in (
            "connect", "network", "timeout", "refused",
            "reset", "unreachable", "timed out", "no route",
        )):
            return True

    # httpx используется внутри openai SDK
    try:
        import httpx
        if isinstance(exc, (
            httpx.ConnectError, httpx.ConnectTimeout,
            httpx.ReadTimeout, httpx.NetworkError,
        )):
            return True
    except ImportError:
        pass

    return False


def log_network_error(exc: Exception, context: str = "") -> None:
    """Логирует сетевую ошибку без ключей и тел запросов."""
    error_type = type(exc).__name__
    detail = str(exc)
    if len(detail) > 200:
        detail = detail[:200] + "..."
    logger.error(
        "Сетевая ошибка AI API | context=%s | type=%s | detail=%s",
        context, error_type, detail,
    )
