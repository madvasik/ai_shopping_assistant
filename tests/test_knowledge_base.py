# -*- coding: utf-8 -*-
"""Тесты `src.services.knowledge_base`: клиент OpenAI и CatalogKB.answer_consultation."""
import builtins
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

from src.services.knowledge_base import CatalogKB, _get_openai_client as kb_get_client


def test_kb_get_openai_client_missing_key(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    c, err = kb_get_client()
    assert c is None and err and "OPENAI_API_KEY" in err


def test_kb_get_openai_client_openai_init_fails(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-key")
    with patch("openai.OpenAI", side_effect=RuntimeError("init fail")):
        c, err = kb_get_client()
    assert c is None and "init fail" in err


def test_kb_get_openai_client_success(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-key")
    fake = MagicMock()
    with patch("openai.OpenAI", return_value=fake):
        c, err = kb_get_client()
    assert c is fake and err is None


def test_kb_get_openai_import_fails(monkeypatch):
    real_import = builtins.__import__

    def fake_import(name, globals=None, locals=None, fromlist=(), level=0):
        if name == "openai" or (fromlist and "OpenAI" in fromlist):
            raise ImportError("blocked openai")
        return real_import(name, globals, locals, fromlist, level)

    monkeypatch.setattr(builtins, "__import__", fake_import)
    monkeypatch.setenv("OPENAI_API_KEY", "sk-x")
    c, err = kb_get_client()
    assert c is None and "openai" in err.lower()


def test_catalog_kb_adds_missing_columns():
    df = pd.DataFrame({"title": ["Только название"]})
    kb = CatalogKB(df)
    for c in (
        "category",
        "description",
        "price",
        "price_currency",
        "search_text",
    ):
        assert c in kb.df.columns


def test_llm_answer_no_client(monkeypatch, simple_catalog_df):
    monkeypatch.setattr(
        "src.services.knowledge_base._get_openai_client",
        lambda: (None, "x"),
    )
    kb = CatalogKB(simple_catalog_df)
    assert kb._llm_answer("q") is None


def test_llm_answer_success(monkeypatch, simple_catalog_df):
    c = MagicMock()
    resp = MagicMock()
    resp.choices = [MagicMock(message=MagicMock(content="Краткий ответ."))]
    resp.usage = MagicMock(prompt_tokens=1, completion_tokens=1)
    c.chat.completions.create.return_value = resp
    monkeypatch.setattr(
        "src.services.knowledge_base._get_openai_client",
        lambda: (c, None),
    )
    kb = CatalogKB(simple_catalog_df)
    assert kb._llm_answer("вопрос") == "Краткий ответ."


def test_llm_answer_exception(monkeypatch, simple_catalog_df):
    c = MagicMock()
    c.chat.completions.create.side_effect = RuntimeError("x")
    monkeypatch.setattr(
        "src.services.knowledge_base._get_openai_client",
        lambda: (c, None),
    )
    kb = CatalogKB(simple_catalog_df)
    assert kb._llm_answer("q") is None


def test_answer_consultation_fallback(monkeypatch, simple_catalog_df):
    monkeypatch.setattr(
        "src.services.knowledge_base._get_openai_client",
        lambda: (None, "x"),
    )
    kb = CatalogKB(simple_catalog_df)
    text = kb.answer_consultation("что такое грунтовка")
    assert "не удалось" in text.lower() or "переформулировать" in text.lower()


def test_answer_consultation_with_llm(monkeypatch, simple_catalog_df):
    c = MagicMock()
    resp = MagicMock()
    resp.choices = [MagicMock(message=MagicMock(content="Грунтовка — это..."))]
    resp.usage = MagicMock(prompt_tokens=1, completion_tokens=1)
    c.chat.completions.create.return_value = resp
    monkeypatch.setattr(
        "src.services.knowledge_base._get_openai_client",
        lambda: (c, None),
    )
    kb = CatalogKB(simple_catalog_df)
    assert "Грунтовка" in kb.answer_consultation("что такое грунтовка")
