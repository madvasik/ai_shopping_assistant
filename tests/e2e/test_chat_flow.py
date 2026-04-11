# -*- coding: utf-8 -*-
import datetime as dt
import os
import time
from pathlib import Path

import pytest

from src.services import logs_db
from src.services.network_utils import NETWORK_ERROR_REPLY
from widget.app import chat_api


pytestmark = pytest.mark.e2e

_REPORTS_DIR = Path(__file__).resolve().parent / "reports"
_WIDGET_CATEGORY_PREFIX = "__WS_CAT__"

CONSULTATION_CASES = [
    "Что лучше для стен: виниловые или флизелиновые обои?",
]

TASK_DIRECT_CASES = [
    "Нужен перфоратор 800Вт",
]

TASK_CLARIFICATION_CASES = [
    ("Хочу повесить полку на стену", "Бетон"),
]

OFFTOPIC_CASES = [
    "Что приготовить на ужин?",
]


def _clean_reply_for_report(reply: str) -> str:
    return (reply or "").replace(_WIDGET_CATEGORY_PREFIX, "")


def _format_markdown_report(
    *,
    title: str,
    started_at: str,
    cases: list[dict],
) -> str:
    total_llm_calls = sum(len(case["llm_calls"]) for case in cases)
    total_cost_usd = sum(
        (call.get("cost_usd") or 0.0)
        for case in cases
        for call in case["llm_calls"]
    )
    lines = [
        f"# {title}",
        "",
        f"- Старт: `{started_at}`",
        f"- Сценариев: `{len(cases)}`",
        f"- Вызовов LLM: `{total_llm_calls}`",
        f"- Стоимость: `${total_cost_usd:.6f}`",
        "",
        "## Сценарии",
        "",
    ]

    for idx, case in enumerate(cases, start=1):
        lines.extend(
            [
                f"### {idx}. {case['title']}",
                "",
                f"- Режим: `{case['flow']}`",
                f"- Вызовов LLM: `{len(case['llm_calls'])}`",
                "",
                "#### Диалог",
                "",
            ]
        )

        for step_idx, step in enumerate(case["steps"], start=1):
            lines.extend(
                [
                    f"##### Шаг {step_idx}. {step['label']}",
                    "",
                    f"**Сообщение пользователя**: `{step['message']}`",
                    "",
                    "**Ответ ассистента**:",
                    "",
                    "```text",
                    _clean_reply_for_report(step["reply"]),
                    "```",
                    "",
                ]
            )

        lines.extend(["#### Вызовы LLM", ""])

        if not case["llm_calls"]:
            lines.append("_Вызовы LLM не залогированы._")
            lines.append("")
            continue

        for call_idx, call in enumerate(case["llm_calls"], start=1):
            lines.extend(
                [
                    f"- Вызов {call_idx}: `{call.get('function', '')}` / `{call.get('prompt_name', '')}` / "
                    f"длительность `{call.get('duration', '')}` / токены запроса `{call.get('prompt_tokens')}` / "
                    f"токены ответа `{call.get('completion_tokens')}` / стоимость `${call.get('cost_usd')}`",
                ]
            )
        lines.append("")

    return "\n".join(lines).strip() + "\n"


def _write_report(report_path: Path, *, started_at: str, cases: list[dict]):
    report_path.write_text(
        _format_markdown_report(
            title="E2E: отчёт по сценариям чата",
            started_at=started_at,
            cases=cases,
        ),
        encoding="utf-8",
    )


def _new_report_path() -> tuple[Path, str]:
    started = dt.datetime.now()
    _REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    existing_reports = sorted(_REPORTS_DIR.glob("report_*.md"))
    if existing_reports:
        return existing_reports[-1], started.isoformat(timespec="seconds")
    return (
        _REPORTS_DIR / f"report_{started.strftime('%Y-%m-%d_%H-%M-%S')}.md",
        started.isoformat(timespec="seconds"),
    )


def _call_chat(message: str, history: list[dict] | None = None) -> dict:
    return chat_api.process_chat_request(message, history or [])


def _call_chat_with_retries(message: str, history: list[dict] | None = None, attempts: int = 5) -> dict:
    last = None
    for attempt in range(attempts):
        last = _call_chat(message, history)
        if last["reply"] != NETWORK_ERROR_REPLY:
            return last
        if attempt < attempts - 1:
            time.sleep(4)
    assert last is not None
    return last


def _assert_successful_reply(reply: str) -> None:
    assert reply.strip()
    assert reply != NETWORK_ERROR_REPLY
    assert "traceback" not in reply.lower()


@pytest.fixture
def require_openai_key():
    if not os.getenv("OPENAI_API_KEY"):
        pytest.skip("Для e2e нужен OPENAI_API_KEY в окружении")


@pytest.fixture(scope="session")
def e2e_report():
    report_path, started_at = _new_report_path()
    report = {
        "path": report_path,
        "started_at": started_at,
        "cases": [],
    }
    _write_report(report_path, started_at=started_at, cases=report["cases"])
    return report


@pytest.fixture
def isolated_chat_api(monkeypatch, tmp_path):
    db_path = tmp_path / "e2e_logs.db"
    monkeypatch.setattr(logs_db, "_DEFAULT_DB_PATH", db_path)

    conn = getattr(logs_db._local, "conn", None)
    if conn is not None:
        conn.close()
    logs_db._local = logs_db.threading.local()

    chat_api._retriever = None
    chat_api._kb = None
    chat_api._df = None
    chat_api._current_user_message = None
    chat_api._current_user_request_id = None

    yield

    chat_api._retriever = None
    chat_api._kb = None
    chat_api._df = None
    chat_api._current_user_message = None
    chat_api._current_user_request_id = None

    conn = getattr(logs_db._local, "conn", None)
    if conn is not None:
        conn.close()
    logs_db._local = logs_db.threading.local()


def _record_case(e2e_report, *, title: str, flow: str, steps: list[dict]):
    all_requests = logs_db.get_all_user_requests()
    llm_calls = []
    for request in reversed(all_requests):
        llm_calls.extend(request.get("llm_requests", []))

    e2e_report["cases"].append(
        {
            "title": title,
            "flow": flow,
            "steps": steps,
            "llm_calls": llm_calls,
        }
    )
    _write_report(
        e2e_report["path"],
        started_at=e2e_report["started_at"],
        cases=e2e_report["cases"],
    )


@pytest.mark.parametrize("message", CONSULTATION_CASES, ids=lambda x: x[:40])
def test_e2e_consultation_flow(require_openai_key, isolated_chat_api, e2e_report, message):
    steps = []

    result = _call_chat_with_retries(message)
    steps.append(
        {
            "label": "Консультация",
            "message": message,
            "reply": result["reply"],
        }
    )
    _record_case(e2e_report, title=message, flow="consultation", steps=steps)

    reply = result["reply"]
    _assert_successful_reply(reply)
    assert "__WS_CAT__" in reply
    assert "• " in reply


@pytest.mark.parametrize("message", TASK_DIRECT_CASES, ids=lambda x: x[:40])
def test_e2e_task_flow_without_clarification(require_openai_key, isolated_chat_api, e2e_report, message):
    steps = []

    result = _call_chat_with_retries(message)
    steps.append(
        {
            "label": "Задача без уточнения",
            "message": message,
            "reply": result["reply"],
        }
    )
    _record_case(e2e_report, title=message, flow="task_direct", steps=steps)

    reply = result["reply"]
    _assert_successful_reply(reply)
    assert "__WS_CAT__" in reply
    assert "• " in reply


@pytest.mark.parametrize(
    ("message", "follow_up"),
    TASK_CLARIFICATION_CASES,
    ids=[message[:40] for message, _ in TASK_CLARIFICATION_CASES],
)
def test_e2e_task_flow_with_clarification_and_follow_up(
    require_openai_key,
    isolated_chat_api,
    e2e_report,
    message,
    follow_up,
):
    steps = []

    first = _call_chat_with_retries(message)
    steps.append(
        {
            "label": "Первый запрос по задаче",
            "message": message,
            "reply": first["reply"],
        }
    )
    _assert_successful_reply(first["reply"])
    final_reply = first["reply"]

    if "?" in first["reply"]:
        history = [
            {"role": "user", "content": message},
            {"role": "assistant", "content": first["reply"]},
        ]
        second = _call_chat_with_retries(follow_up, history)
        steps.append(
            {
                "label": "Ответ на уточнение",
                "message": follow_up,
                "reply": second["reply"],
            }
        )
        final_reply = second["reply"]
        _assert_successful_reply(final_reply)
        assert "__WS_CAT__" in final_reply
        assert "• " in final_reply

    _record_case(e2e_report, title=message, flow="task_with_optional_clarification", steps=steps)


@pytest.mark.parametrize("message", OFFTOPIC_CASES, ids=lambda x: x[:40])
def test_e2e_offtopic_flow(require_openai_key, isolated_chat_api, e2e_report, message):
    steps = []

    result = _call_chat_with_retries(message)
    steps.append(
        {
            "label": "Вне тематики",
            "message": message,
            "reply": result["reply"],
        }
    )
    _record_case(e2e_report, title=message, flow="offtopic", steps=steps)

    assert "не относится к тематике" in result["reply"].lower()
