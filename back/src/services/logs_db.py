# -*- coding: utf-8 -*-
"""
SQLite-хранилище логов LLM-запросов.

Таблицы
-------
user_requests   — запрос пользователя (группа LLM-вызовов)
llm_calls       — один вызов LLM внутри группы
network_errors  — сетевые ошибки (VPN / geo-block и т.п.)
"""
from __future__ import annotations

import os
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

# По умолчанию /app/logs.db (Docker) или рядом с back/
_DEFAULT_DB_PATH = Path(os.getenv(
    "LOGS_DB_PATH",
    "/app/logs.db" if Path("/app").exists() else str(Path(__file__).resolve().parents[3] / "logs.db"),
))

_local = threading.local()

MAX_USER_REQUESTS = 50
MAX_NETWORK_ERRORS = 100

# ------------------------------------------------------------------
# Подключение
# ------------------------------------------------------------------

def _get_conn(db_path: Path | None = None) -> sqlite3.Connection:
    """Возвращает (thread-local) соединение, создаёт БД при первом вызове."""
    path = str(db_path or _DEFAULT_DB_PATH)
    conn: sqlite3.Connection | None = getattr(_local, "conn", None)
    conn_path: str | None = getattr(_local, "conn_path", None)
    if conn is not None and conn_path == path:
        return conn
    conn = sqlite3.connect(path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=3000")
    _init_tables(conn)
    _local.conn = conn
    _local.conn_path = path
    return conn


def _init_tables(conn: sqlite3.Connection) -> None:
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS user_requests (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            user_message    TEXT    NOT NULL DEFAULT '',
            timestamp       REAL    NOT NULL
        );

        CREATE TABLE IF NOT EXISTS llm_calls (
            id                      INTEGER PRIMARY KEY AUTOINCREMENT,
            user_request_id         INTEGER NOT NULL REFERENCES user_requests(id) ON DELETE CASCADE,
            function                TEXT    NOT NULL DEFAULT 'Unknown',
            prompt_name             TEXT    NOT NULL DEFAULT '',
            system_prompt           TEXT    NOT NULL DEFAULT '',
            user_prompt             TEXT    NOT NULL DEFAULT '',
            original_user_message   TEXT    NOT NULL DEFAULT '',
            response_preview        TEXT,
            start_time              REAL,
            duration                TEXT,
            prompt_tokens           INTEGER,
            completion_tokens       INTEGER,
            cost_usd                REAL
        );

        CREATE TABLE IF NOT EXISTS network_errors (
            id   INTEGER PRIMARY KEY AUTOINCREMENT,
            ts   REAL    NOT NULL,
            type TEXT    NOT NULL DEFAULT 'Unknown'
        );

        CREATE INDEX IF NOT EXISTS idx_llm_calls_ur ON llm_calls(user_request_id);
    """)
    _ensure_column(conn, "llm_calls", "prompt_name", "TEXT NOT NULL DEFAULT ''")


def _ensure_column(conn: sqlite3.Connection, table_name: str, column_name: str, column_def: str) -> None:
    columns = {
        row["name"]
        for row in conn.execute(f"PRAGMA table_info({table_name})").fetchall()
    }
    if column_name in columns:
        return
    conn.execute(f"ALTER TABLE {table_name} ADD COLUMN {column_name} {column_def}")
    conn.commit()


# ------------------------------------------------------------------
# user_requests
# ------------------------------------------------------------------

def add_user_request(user_message: str, timestamp: float | None = None) -> int:
    """Создаёт новую группу и возвращает её id."""
    conn = _get_conn()
    ts = timestamp or time.time()
    cur = conn.execute(
        "INSERT INTO user_requests (user_message, timestamp) VALUES (?, ?)",
        (user_message, ts),
    )
    conn.commit()
    _trim_user_requests(conn)
    return cur.lastrowid  # type: ignore[return-value]


def find_last_user_request_by_message(message: str) -> Optional[Dict[str, Any]]:
    conn = _get_conn()
    row = conn.execute(
        "SELECT * FROM user_requests WHERE user_message = ? ORDER BY id DESC LIMIT 1",
        (message,),
    ).fetchone()
    return dict(row) if row else None


def _trim_user_requests(conn: sqlite3.Connection) -> None:
    """Оставляет только последние MAX_USER_REQUESTS записей."""
    conn.execute(f"""
        DELETE FROM user_requests
        WHERE id NOT IN (
            SELECT id FROM user_requests ORDER BY id DESC LIMIT {MAX_USER_REQUESTS}
        )
    """)
    conn.commit()


# ------------------------------------------------------------------
# llm_calls
# ------------------------------------------------------------------

def add_llm_call(
    user_request_id: int,
    function: str = "Unknown",
    prompt_name: str = "",
    system_prompt: str = "",
    user_prompt: str = "",
    original_user_message: str = "",
    start_time: float | None = None,
) -> int:
    """Добавляет запись о вызове LLM. Возвращает id записи."""
    conn = _get_conn()
    cur = conn.execute(
        """INSERT INTO llm_calls
           (user_request_id, function, prompt_name, system_prompt, user_prompt,
            original_user_message, start_time)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (user_request_id, function, prompt_name, system_prompt, user_prompt,
         original_user_message, start_time or time.time()),
    )
    conn.commit()
    return cur.lastrowid  # type: ignore[return-value]


def update_llm_response(
    user_request_id: int,
    response_preview: str | None = None,
    prompt_tokens: int | None = None,
    completion_tokens: int | None = None,
    cost_usd: float | None = None,
    duration: str | None = None,
) -> None:
    """Обновляет первую незавершённую (duration IS NULL) запись в группе."""
    conn = _get_conn()
    # FIFO: ищем первый llm_call без duration
    row = conn.execute(
        """SELECT id, start_time FROM llm_calls
           WHERE user_request_id = ? AND duration IS NULL
           ORDER BY id ASC LIMIT 1""",
        (user_request_id,),
    ).fetchone()

    if row is None:
        # Fallback: последний вызов в группе
        row = conn.execute(
            "SELECT id, start_time FROM llm_calls WHERE user_request_id = ? ORDER BY id DESC LIMIT 1",
            (user_request_id,),
        ).fetchone()

    if row is None:
        return

    call_id = row["id"]
    start = row["start_time"]

    # Вычисляем duration
    if duration is None and start:
        elapsed = time.time() - start
        if elapsed < 1:
            duration = f"{elapsed * 1000:.0f}мс"
        elif elapsed < 60:
            duration = f"{elapsed:.2f}с"
        else:
            m = int(elapsed // 60)
            s = elapsed % 60
            duration = f"{m}м {s:.1f}с"

    conn.execute(
        """UPDATE llm_calls
           SET response_preview = ?,
               prompt_tokens    = ?,
               completion_tokens = ?,
               cost_usd          = ?,
               duration          = ?
           WHERE id = ?""",
        (response_preview, prompt_tokens, completion_tokens, cost_usd, duration, call_id),
    )
    conn.commit()


# ------------------------------------------------------------------
# network_errors
# ------------------------------------------------------------------

def add_network_error(error_type: str) -> None:
    conn = _get_conn()
    conn.execute(
        "INSERT INTO network_errors (ts, type) VALUES (?, ?)",
        (time.time(), error_type),
    )
    conn.commit()
    # Тримим
    conn.execute(f"""
        DELETE FROM network_errors
        WHERE id NOT IN (
            SELECT id FROM network_errors ORDER BY id DESC LIMIT {MAX_NETWORK_ERRORS}
        )
    """)
    conn.commit()


def get_network_errors(limit: int = 10) -> List[Dict[str, Any]]:
    conn = _get_conn()
    rows = conn.execute(
        "SELECT ts, type FROM network_errors ORDER BY id DESC LIMIT ?", (limit,)
    ).fetchall()
    return [dict(r) for r in rows]


# ------------------------------------------------------------------
# Чтение / агрегация (для Streamlit-панели)
# ------------------------------------------------------------------

def get_stats() -> Dict[str, Any]:
    """Возвращает агрегированную статистику."""
    conn = _get_conn()

    total_user = conn.execute("SELECT COUNT(*) FROM user_requests").fetchone()[0]
    total_llm = conn.execute("SELECT COUNT(*) FROM llm_calls").fetchone()[0]

    row = conn.execute(
        "SELECT COALESCE(SUM(prompt_tokens), 0) AS pt, "
        "       COALESCE(SUM(completion_tokens), 0) AS ct, "
        "       COALESCE(SUM(cost_usd), 0) AS cost "
        "FROM llm_calls WHERE prompt_tokens IS NOT NULL"
    ).fetchone()

    any_usage = conn.execute(
        "SELECT 1 FROM llm_calls WHERE prompt_tokens IS NOT NULL LIMIT 1"
    ).fetchone() is not None

    return {
        "total_user_requests": total_user,
        "total_llm_calls": total_llm,
        "total_tokens": row["pt"] + row["ct"],
        "total_cost_usd": row["cost"],
        "any_usage_logged": any_usage,
    }


def get_all_user_requests() -> List[Dict[str, Any]]:
    """Возвращает все группы запросов с вложенными llm_calls (новые первыми)."""
    conn = _get_conn()
    ur_rows = conn.execute(
        "SELECT * FROM user_requests ORDER BY id DESC"
    ).fetchall()

    result = []
    for ur in ur_rows:
        ur_dict = dict(ur)
        calls = conn.execute(
            "SELECT * FROM llm_calls WHERE user_request_id = ? ORDER BY id ASC",
            (ur_dict["id"],),
        ).fetchall()
        ur_dict["llm_requests"] = [dict(c) for c in calls]
        result.append(ur_dict)
    return result


# ------------------------------------------------------------------
# Очистка
# ------------------------------------------------------------------

def clear_logs() -> None:
    conn = _get_conn()
    conn.executescript("""
        DELETE FROM llm_calls;
        DELETE FROM user_requests;
        DELETE FROM network_errors;
    """)
