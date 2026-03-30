# -*- coding: utf-8 -*-
from pathlib import Path

import pytest

from src.services import logs_db


@pytest.fixture
def isolated_logs_db(tmp_path, monkeypatch):
    db_path = tmp_path / "logs.db"
    monkeypatch.setattr(logs_db, "_DEFAULT_DB_PATH", db_path)
    existing_conn = getattr(logs_db._local, "conn", None)
    if existing_conn is not None:
        existing_conn.close()
    logs_db._local = logs_db.threading.local()
    yield db_path
    conn = getattr(logs_db._local, "conn", None)
    if conn is not None:
        conn.close()
    logs_db._local = logs_db.threading.local()


def test_get_conn_reuses_thread_local_connection(isolated_logs_db):
    conn1 = logs_db._get_conn()
    conn2 = logs_db._get_conn()
    assert conn1 is conn2
    assert conn1.row_factory is not None


def test_add_and_find_last_user_request_by_message(isolated_logs_db):
    first_id = logs_db.add_user_request("обои", timestamp=100.0)
    second_id = logs_db.add_user_request("обои", timestamp=200.0)

    found = logs_db.find_last_user_request_by_message("обои")

    assert found is not None
    assert found["id"] == second_id
    assert found["timestamp"] == 200.0
    assert second_id > first_id


def test_trim_user_requests_keeps_latest_entries(isolated_logs_db, monkeypatch):
    monkeypatch.setattr(logs_db, "MAX_USER_REQUESTS", 3)

    for idx in range(5):
        logs_db.add_user_request(f"msg-{idx}", timestamp=float(idx))

    all_requests = logs_db.get_all_user_requests()

    assert [row["user_message"] for row in all_requests] == ["msg-4", "msg-3", "msg-2"]


def test_update_llm_response_updates_oldest_open_call_and_stats(isolated_logs_db):
    user_request_id = logs_db.add_user_request("нужна краска", timestamp=100.0)
    first_call_id = logs_db.add_llm_call(
        user_request_id=user_request_id,
        function="classify",
        prompt_name="classify_intent",
        system_prompt="sys",
        user_prompt="user",
        original_user_message="нужна краска",
        start_time=100.0,
    )
    second_call_id = logs_db.add_llm_call(
        user_request_id=user_request_id,
        function="search",
        start_time=101.0,
    )

    logs_db.update_llm_response(
        user_request_id=user_request_id,
        response_preview="готово",
        prompt_tokens=10,
        completion_tokens=5,
        cost_usd=0.123,
        duration="321мс",
    )

    rows = logs_db.get_all_user_requests()
    calls = rows[0]["llm_requests"]

    assert [call["id"] for call in calls] == [first_call_id, second_call_id]
    assert calls[0]["prompt_name"] == "classify_intent"
    assert calls[0]["response_preview"] == "готово"
    assert calls[0]["prompt_tokens"] == 10
    assert calls[0]["completion_tokens"] == 5
    assert calls[0]["cost_usd"] == 0.123
    assert calls[0]["duration"] == "321мс"
    assert calls[1]["duration"] is None

    stats = logs_db.get_stats()
    assert stats == {
        "total_user_requests": 1,
        "total_llm_calls": 2,
        "total_tokens": 15,
        "total_cost_usd": 0.123,
        "any_usage_logged": True,
    }


def test_update_llm_response_falls_back_to_last_call_when_all_closed(isolated_logs_db):
    user_request_id = logs_db.add_user_request("дюбели", timestamp=100.0)
    logs_db.add_llm_call(user_request_id=user_request_id, function="first", start_time=100.0)
    logs_db.add_llm_call(user_request_id=user_request_id, function="second", start_time=101.0)

    logs_db.update_llm_response(user_request_id, duration="done-1")
    logs_db.update_llm_response(user_request_id, duration="done-2")
    logs_db.update_llm_response(user_request_id, response_preview="fallback-hit", duration="done-3")

    calls = logs_db.get_all_user_requests()[0]["llm_requests"]
    assert calls[0]["duration"] == "done-1"
    assert calls[1]["duration"] == "done-3"
    assert calls[1]["response_preview"] == "fallback-hit"


def test_update_llm_response_without_any_calls_is_noop(isolated_logs_db):
    user_request_id = logs_db.add_user_request("пусто", timestamp=100.0)
    logs_db.update_llm_response(user_request_id, response_preview="x", duration="1с")

    rows = logs_db.get_all_user_requests()
    assert rows[0]["llm_requests"] == []


def test_network_errors_and_clear_logs(isolated_logs_db, monkeypatch):
    monkeypatch.setattr(logs_db, "MAX_NETWORK_ERRORS", 2)

    logs_db.add_user_request("шпаклевка", timestamp=1.0)
    logs_db.add_network_error("Timeout")
    logs_db.add_network_error("ConnectError")
    logs_db.add_network_error("PermissionDenied")

    errors = logs_db.get_network_errors(limit=10)
    assert [row["type"] for row in errors] == ["PermissionDenied", "ConnectError"]

    logs_db.clear_logs()

    assert logs_db.get_all_user_requests() == []
    assert logs_db.get_network_errors() == []
    assert logs_db.get_stats() == {
        "total_user_requests": 0,
        "total_llm_calls": 0,
        "total_tokens": 0,
        "total_cost_usd": 0,
        "any_usage_logged": False,
    }
