# -*- coding: utf-8 -*-
import logging

import httpx
import pytest
from openai import APIConnectionError, APITimeoutError, PermissionDeniedError

from src.services.network_utils import is_network_error, log_network_error


def test_is_network_error_with_openai_connection_errors():
    request = httpx.Request("GET", "https://example.com")

    assert is_network_error(APIConnectionError(message="boom", request=request)) is True
    assert is_network_error(APITimeoutError(request=request)) is True


def test_is_network_error_with_openai_permission_denied():
    request = httpx.Request("GET", "https://example.com")
    response = httpx.Response(403, request=request)

    exc = PermissionDeniedError("forbidden", response=response, body=None)

    assert is_network_error(exc) is True


@pytest.mark.parametrize(
    "exc",
    [
        OSError("Connection refused by host"),
        httpx.ConnectError("connect failed"),
        httpx.ConnectTimeout("timed out"),
        httpx.ReadTimeout("read timed out"),
        httpx.NetworkError("network down"),
    ],
)
def test_is_network_error_with_oserror_and_httpx(exc):
    assert is_network_error(exc) is True


def test_is_network_error_returns_false_for_non_network_cases():
    assert is_network_error(ValueError("oops")) is False
    assert is_network_error(OSError("permission denied")) is False


def test_log_network_error_truncates_and_logs_context(caplog):
    long_detail = "x" * 250

    with caplog.at_level(logging.ERROR):
        log_network_error(RuntimeError(long_detail), context="chat")

    assert len(caplog.records) == 1
    record = caplog.records[0]
    assert "Сетевая ошибка AI API" in record.message
    assert "context=chat" in record.message
    assert "type=RuntimeError" in record.message
    assert "..." in record.message
