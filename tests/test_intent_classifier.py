# -*- coding: utf-8 -*-
"""
Unit-тесты для `src.services.intent_classifier`:
клиент OpenAI, классификация интента, проверка тематики каталога,
извлечение сущностей, релевантность карточек.
"""
from unittest.mock import MagicMock, patch

import pytest

from src.services.intent_classifier import (
    _get_openai_client as ic_get_client,
    check_products_relevance,
    classify_intent,
    extract_product_names_from_query,
    is_catalog_related,
)
from tests.support.openai_client import openai_client_returning


# ---------------------------------------------------------------------------
# OpenAI client factory (_get_openai_client)
# ---------------------------------------------------------------------------


def test_ic_get_openai_client_missing_key(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    c, err = ic_get_client()
    assert c is None and err and "OPENAI_API_KEY" in err


def test_ic_get_openai_client_openai_init_fails(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-key")
    with patch("openai.OpenAI", side_effect=RuntimeError("init fail")):
        c, err = ic_get_client()
    assert c is None and "init fail" in err


def test_ic_get_openai_client_success(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-key")
    fake = MagicMock()
    with patch("openai.OpenAI", return_value=fake):
        c, err = ic_get_client()
    assert c is fake and err is None


def test_ic_get_openai_import_fails(monkeypatch):
    import builtins

    real_import = builtins.__import__

    def fake_import(name, globals=None, locals=None, fromlist=(), level=0):
        if name == "openai" or (fromlist and "OpenAI" in fromlist):
            raise ImportError("blocked openai")
        return real_import(name, globals, locals, fromlist, level)

    monkeypatch.setattr(builtins, "__import__", fake_import)
    monkeypatch.setenv("OPENAI_API_KEY", "sk-x")
    c, err = ic_get_client()
    assert c is None and "openai" in err.lower()


# ---------------------------------------------------------------------------
# classify_intent
# ---------------------------------------------------------------------------


def test_classify_intent_no_user_message():
    assert classify_intent([{"role": "assistant", "content": "hi"}]) == "task"


def test_classify_intent_no_openai_client(monkeypatch):
    monkeypatch.setattr(
        "src.services.intent_classifier._get_openai_client",
        lambda: (None, "no key"),
    )
    assert classify_intent([{"role": "user", "content": "купить краску"}]) == "task"


def test_classify_intent_consultation(monkeypatch):
    c = openai_client_returning("consultation")
    monkeypatch.setattr(
        "src.services.intent_classifier._get_openai_client",
        lambda: (c, None),
    )
    assert classify_intent([{"role": "user", "content": "что лучше?"}]) == "consultation"


def test_classify_intent_task_keyword(monkeypatch):
    c = openai_client_returning("это точно task вариант")
    monkeypatch.setattr(
        "src.services.intent_classifier._get_openai_client",
        lambda: (c, None),
    )
    assert classify_intent([{"role": "user", "content": "нужен молоток"}]) == "task"


def test_classify_intent_gibberish_defaults_task(monkeypatch):
    c = openai_client_returning("maybe later")
    monkeypatch.setattr(
        "src.services.intent_classifier._get_openai_client",
        lambda: (c, None),
    )
    assert classify_intent([{"role": "user", "content": "x"}]) == "task"


def test_classify_intent_api_raises(monkeypatch):
    c = MagicMock()

    def boom(**_kwargs):
        raise RuntimeError("api down")

    c.chat.completions.create.side_effect = boom
    monkeypatch.setattr(
        "src.services.intent_classifier._get_openai_client",
        lambda: (c, None),
    )
    assert classify_intent([{"role": "user", "content": "x"}]) == "task"


def test_classify_intent_includes_assistant_in_context(monkeypatch):
    c = openai_client_returning("task")
    monkeypatch.setattr(
        "src.services.intent_classifier._get_openai_client",
        lambda: (c, None),
    )
    messages = [
        {"role": "user", "content": "нужны обои"},
        {"role": "assistant", "content": "Какие именно?"},
        {"role": "user", "content": "флизелин"},
    ]
    assert classify_intent(messages) == "task"
    call_kw = c.chat.completions.create.call_args[1]
    user_msg = call_kw["messages"][1]["content"]
    assert "Ассистент:" in user_msg


# ---------------------------------------------------------------------------
# is_catalog_related
# ---------------------------------------------------------------------------


def test_is_catalog_related_empty():
    assert is_catalog_related("") is False


def test_is_catalog_related_no_client(monkeypatch):
    monkeypatch.setattr(
        "src.services.intent_classifier._get_openai_client",
        lambda: (None, "x"),
    )
    assert is_catalog_related("краска") is False


def test_is_catalog_related_heuristic_curtains_without_llm(monkeypatch):
    """Монтаж штор — в тематике строймага; эвристика до LLM (в т.ч. без API)."""
    monkeypatch.setattr(
        "src.services.intent_classifier._get_openai_client",
        lambda: (None, "x"),
    )
    assert is_catalog_related("как повесить шторы?") is True


def test_is_catalog_related_da(monkeypatch):
    c = openai_client_returning("да")
    monkeypatch.setattr(
        "src.services.intent_classifier._get_openai_client",
        lambda: (c, None),
    )
    assert is_catalog_related("обои") is True


def test_is_catalog_related_net(monkeypatch):
    c = openai_client_returning("нет, это не то")
    monkeypatch.setattr(
        "src.services.intent_classifier._get_openai_client",
        lambda: (c, None),
    )
    assert is_catalog_related("пицца") is False


def test_is_catalog_related_api_raises(monkeypatch):
    c = MagicMock()
    c.chat.completions.create.side_effect = RuntimeError("x")
    monkeypatch.setattr(
        "src.services.intent_classifier._get_openai_client",
        lambda: (c, None),
    )
    assert is_catalog_related("q") is False


# ---------------------------------------------------------------------------
# extract_product_names_from_query
# ---------------------------------------------------------------------------


def test_extract_product_names_empty_and_no_client(monkeypatch):
    assert extract_product_names_from_query("") == []
    monkeypatch.setattr(
        "src.services.intent_classifier._get_openai_client",
        lambda: (None, "e"),
    )
    assert extract_product_names_from_query("краска") == []


def test_extract_product_names_json_ok(monkeypatch):
    c = openai_client_returning('["Краска", "валик"]')
    monkeypatch.setattr(
        "src.services.intent_classifier._get_openai_client",
        lambda: (c, None),
    )
    names = extract_product_names_from_query("нужна краска и валик")
    assert names == ["краска", "валик"]


def test_extract_product_names_markdown_fence(monkeypatch):
    c = openai_client_returning('```json\n["обои"]\n```')
    monkeypatch.setattr(
        "src.services.intent_classifier._get_openai_client",
        lambda: (c, None),
    )
    assert extract_product_names_from_query("обои") == ["обои"]


def test_extract_product_names_invalid_json(monkeypatch):
    c = openai_client_returning("not json")
    monkeypatch.setattr(
        "src.services.intent_classifier._get_openai_client",
        lambda: (c, None),
    )
    assert extract_product_names_from_query("x") == []


def test_extract_product_names_malformed_json_array(monkeypatch):
    c = openai_client_returning("[not valid json")
    monkeypatch.setattr(
        "src.services.intent_classifier._get_openai_client",
        lambda: (c, None),
    )
    assert extract_product_names_from_query("x") == []


def test_extract_product_names_json_not_list(monkeypatch):
    c = openai_client_returning('{"a": 1}')
    monkeypatch.setattr(
        "src.services.intent_classifier._get_openai_client",
        lambda: (c, None),
    )
    assert extract_product_names_from_query("x") == []


# ---------------------------------------------------------------------------
# check_products_relevance
# ---------------------------------------------------------------------------


def test_check_products_relevance_empty():
    assert check_products_relevance("", [{"title": "x"}]) == []
    assert check_products_relevance("cat", []) == []


def test_check_products_relevance_no_client(monkeypatch):
    monkeypatch.setattr(
        "src.services.intent_classifier._get_openai_client",
        lambda: (None, "x"),
    )
    prods = [{"title": "A", "category": "c", "description": ""}]
    assert check_products_relevance("категория", prods) == [1]


def test_check_products_relevance_parses_binary(monkeypatch):
    c = openai_client_returning("1 0 1")
    monkeypatch.setattr(
        "src.services.intent_classifier._get_openai_client",
        lambda: (c, None),
    )
    prods = [
        {"title": "A", "category": "x", "description": ""},
        {"title": "B", "category": "x", "description": ""},
        {"title": "C", "category": "x", "description": ""},
    ]
    assert check_products_relevance("тест", prods) == [1, 0, 1]


def test_check_products_relevance_invalid_titles_all_zero():
    prods = [
        {"title": "", "category": "x", "description": ""},
        {"title": "nan", "category": "x", "description": ""},
    ]
    assert check_products_relevance("x", prods) == [0, 0]


def test_check_products_relevance_api_error_returns_ones(monkeypatch):
    c = MagicMock()
    c.chat.completions.create.side_effect = RuntimeError("fail")
    monkeypatch.setattr(
        "src.services.intent_classifier._get_openai_client",
        lambda: (c, None),
    )
    prods = [{"title": "Ok", "category": "c", "description": ""}]
    assert check_products_relevance("x", prods) == [1]


def test_check_products_relevance_includes_description(monkeypatch):
    c = openai_client_returning("1")
    monkeypatch.setattr(
        "src.services.intent_classifier._get_openai_client",
        lambda: (c, None),
    )
    prods = [
        {
            "title": "Товар",
            "category": "Кат",
            "description": "Длинное описание для промпта",
        }
    ]
    assert check_products_relevance("категория", prods) == [1]


def test_check_products_relevance_binary_shorter_than_products(monkeypatch):
    c = openai_client_returning("1")
    monkeypatch.setattr(
        "src.services.intent_classifier._get_openai_client",
        lambda: (c, None),
    )
    prods = [
        {"title": "A", "category": "", "description": ""},
        {"title": "B", "category": "", "description": ""},
    ]
    assert check_products_relevance("x", prods) == [1, 0]
