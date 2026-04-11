# -*- coding: utf-8 -*-
"""
Тесты `src.services.task_analyzer`: фабрика OpenAI, правка JSON с управляющими
символами, разбор ответа LLM для списка товаров, уточняющие вопросы.
"""
import json
from unittest.mock import MagicMock, patch

import pytest

from src.services.task_analyzer import (
    _fix_json_control_chars,
    _get_openai_client as ta_get_client,
    get_required_products_for_task,
    should_ask_clarification,
)
from tests.support.openai_client import openai_client_returning


# ---------------------------------------------------------------------------
# Фабрика клиента OpenAI
# ---------------------------------------------------------------------------


def test_ta_get_openai_client_missing_key(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    c, err = ta_get_client()
    assert c is None and err and "OPENAI_API_KEY" in err


def test_ta_get_openai_client_openai_init_fails(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-key")
    with patch("openai.OpenAI", side_effect=RuntimeError("init fail")):
        c, err = ta_get_client()
    assert c is None and "init fail" in err


def test_ta_get_openai_client_success(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-key")
    fake = MagicMock()
    with patch("openai.OpenAI", return_value=fake):
        c, err = ta_get_client()
    assert c is fake and err is None


def test_ta_get_openai_import_fails(monkeypatch):
    import builtins

    real_import = builtins.__import__

    def fake_import(name, globals=None, locals=None, fromlist=(), level=0):
        if name == "openai" or (fromlist and "OpenAI" in fromlist):
            raise ImportError("blocked openai")
        return real_import(name, globals, locals, fromlist, level)

    monkeypatch.setattr(builtins, "__import__", fake_import)
    monkeypatch.setenv("OPENAI_API_KEY", "sk-x")
    c, err = ta_get_client()
    assert c is None and "openai" in err.lower()


# ---------------------------------------------------------------------------
# Исправление управляющих символов в JSON (_fix_json_control_chars)
# ---------------------------------------------------------------------------


def test_fix_json_control_chars_newline_inside_string():
    raw = '{"text": "line1\nline2", "products": []}'
    fixed = _fix_json_control_chars(raw)
    data = json.loads(fixed)
    assert data["text"] == "line1\nline2"
    assert data["products"] == []


def test_fix_json_carriage_return_and_tab_inside_string():
    raw = '{"text": "a\rb\tc", "products": []}'
    fixed = _fix_json_control_chars(raw)
    data = json.loads(fixed)
    assert data["text"] == "a\rb\tc"


def test_fix_json_other_control_char_becomes_unicode_escape():
    raw = '{"text": "x\x01y", "products": []}'
    fixed = _fix_json_control_chars(raw)
    data = json.loads(fixed)
    assert "\x01" in data["text"] or data["text"] == "x\x01y"


def test_fix_json_escape_and_quotes():
    s = r'{"a": "x\"y"}'
    assert _fix_json_control_chars(s) == s


# ---------------------------------------------------------------------------
# Список товаров под задачу (get_required_products_for_task)
# ---------------------------------------------------------------------------


def test_get_required_products_no_client(monkeypatch):
    monkeypatch.setattr(
        "src.services.task_analyzer._get_openai_client",
        lambda: (None, "x"),
    )
    assert get_required_products_for_task("задача") == {"text": "", "products": []}


def test_get_required_products_dict_format(monkeypatch):
    payload = (
        '{"text": "Нужны **обои**.", "products": [{"name": "обои"}, {"name": "клей"}]}'
    )
    c = openai_client_returning(payload)
    monkeypatch.setattr(
        "src.services.task_analyzer._get_openai_client",
        lambda: (c, None),
    )
    out = get_required_products_for_task("поклеить обои")
    assert "обои" in out["text"]
    assert [p["name"] for p in out["products"]] == ["обои", "клей"]


def test_get_required_products_unescaped_newline_in_json_fixed(monkeypatch):
    # LLM вернул перенос строки внутри значения без экранирования — чинится _fix_json
    inner = 'Два\nабзаца'
    payload = '{"text": "' + inner + '", "products": [{"name": "x"}]}'
    c = openai_client_returning(payload)
    monkeypatch.setattr(
        "src.services.task_analyzer._get_openai_client",
        lambda: (c, None),
    )
    out = get_required_products_for_task("q")
    assert "Два" in out["text"] and "абзаца" in out["text"]
    assert out["products"] == [{"name": "x"}]


def test_get_required_products_markdown_fenced(monkeypatch):
    inner = '{"text": "t", "products": [{"name": "x"}]}'
    c = openai_client_returning(f"```json\n{inner}\n```")
    monkeypatch.setattr(
        "src.services.task_analyzer._get_openai_client",
        lambda: (c, None),
    )
    out = get_required_products_for_task("q")
    assert out["products"] == [{"name": "x"}]


def test_get_required_products_list_legacy(monkeypatch):
    c = openai_client_returning('[{"name": "гвозди"}, "шурупы"]')
    monkeypatch.setattr(
        "src.services.task_analyzer._get_openai_client",
        lambda: (c, None),
    )
    out = get_required_products_for_task("q")
    assert len(out["products"]) == 2
    assert "гвозди" in out["text"]


def test_get_required_products_mixed_product_entries(monkeypatch):
    c = openai_client_returning(
        '{"text": "t", "products": [{"name": "a"}, "", {"name": ""}, {"name": "b"}]}'
    )
    monkeypatch.setattr(
        "src.services.task_analyzer._get_openai_client",
        lambda: (c, None),
    )
    out = get_required_products_for_task("q")
    assert [p["name"] for p in out["products"]] == ["a", "b"]


def test_get_required_products_invalid_json(monkeypatch):
    c = openai_client_returning("not { json")
    monkeypatch.setattr(
        "src.services.task_analyzer._get_openai_client",
        lambda: (c, None),
    )
    out = get_required_products_for_task("q")
    assert out == {"text": "", "products": []}


def test_get_required_products_api_raises(monkeypatch):
    c = MagicMock()
    c.chat.completions.create.side_effect = RuntimeError("x")
    monkeypatch.setattr(
        "src.services.task_analyzer._get_openai_client",
        lambda: (c, None),
    )
    assert get_required_products_for_task("q") == {"text": "", "products": []}


# ---------------------------------------------------------------------------
# Уточняющий вопрос перед подбором (should_ask_clarification)
# ---------------------------------------------------------------------------


def test_should_ask_clarification_no_client(monkeypatch):
    monkeypatch.setattr(
        "src.services.task_analyzer._get_openai_client",
        lambda: (None, "x"),
    )
    assert should_ask_clarification("x", []) is None


def test_should_ask_clarification_net(monkeypatch):
    c = openai_client_returning("НЕТ, уточнение не нужно")
    monkeypatch.setattr(
        "src.services.task_analyzer._get_openai_client",
        lambda: (c, None),
    )
    assert should_ask_clarification("повесить полку", []) is None


def test_should_ask_clarification_returns_question(monkeypatch):
    c = openai_client_returning("Из какого материала стена у вас сделана?")
    monkeypatch.setattr(
        "src.services.task_analyzer._get_openai_client",
        lambda: (c, None),
    )
    q = should_ask_clarification("нужен дюбель", [])
    assert q is not None
    assert "?" in q


def test_should_ask_clarification_includes_dialog_history(monkeypatch):
    c = openai_client_returning("Какой диаметр отверстия вам нужен?")
    monkeypatch.setattr(
        "src.services.task_analyzer._get_openai_client",
        lambda: (c, None),
    )
    history = [
        {"role": "user", "content": "хочу полку"},
        {"role": "assistant", "content": "Ок"},
        {"role": "user", "content": "на гипсокартон"},
    ]
    q = should_ask_clarification("нужны дюбели", history)
    assert q and "?" in q
    call_kw = c.chat.completions.create.call_args[1]
    user_msg = call_kw["messages"][1]["content"]
    assert "Пользователь:" in user_msg
    assert "Ассистент:" in user_msg
    assert "гипсокартон" in user_msg


def test_should_ask_clarification_short_answer_no_question(monkeypatch):
    c = openai_client_returning("да")
    monkeypatch.setattr(
        "src.services.task_analyzer._get_openai_client",
        lambda: (c, None),
    )
    assert should_ask_clarification("x", []) is None


def test_should_ask_clarification_api_raises(monkeypatch):
    c = MagicMock()
    c.chat.completions.create.side_effect = RuntimeError("x")
    monkeypatch.setattr(
        "src.services.task_analyzer._get_openai_client",
        lambda: (c, None),
    )
    assert should_ask_clarification("x", []) is None
