# -*- coding: utf-8 -*-
"""
Тесты `src.services.product_search`: токенизация, загрузка SQLite, Retriever (BM25).
"""
import sqlite3

import numpy as np
import pandas as pd
import pytest

from src.services.product_search import (
    Retriever,
    _ensure_cols,
    _tokenize,
    load_products,
)


def test_tokenize_basic():
    assert _tokenize("Hello world") == ["hello", "world"]
    assert _tokenize("Краска для стен") == ["краска", "для", "стен"]


def test_tokenize_empty_and_nan():
    assert _tokenize("") == []
    assert _tokenize(np.nan) == []


def test_ensure_cols_adds_missing():
    df = pd.DataFrame({"title": ["A"]})
    out = _ensure_cols(df)
    for c in ("category", "description", "price", "price_currency", "search_text"):
        assert c in out.columns


def test_load_products_rejects_non_sqlite(tmp_path):
    p = tmp_path / "data.txt"
    p.write_text("x", encoding="utf-8")
    with pytest.raises(ValueError, match="SQLite"):
        load_products(p)


def test_load_products_rejects_bad_table_name(tmp_path):
    db = tmp_path / "x.db"
    sqlite3.connect(db).close()
    with pytest.raises(ValueError, match="Некорректное"):
        load_products(db, table_name="foo;drop")


def test_load_products_missing_table(tmp_path):
    db = tmp_path / "empty.db"
    conn = sqlite3.connect(db)
    conn.execute("CREATE TABLE other (id INT)")
    conn.commit()
    conn.close()
    with pytest.raises(ValueError, match="не найдена"):
        load_products(db, table_name="products")


def test_load_products_roundtrip(tmp_path):
    db = tmp_path / "catalog.db"
    conn = sqlite3.connect(db)
    conn.execute(
        """CREATE TABLE products (
            title TEXT, category TEXT, description TEXT,
            price REAL, price_currency TEXT, search_text TEXT
        )"""
    )
    conn.execute(
        "INSERT INTO products VALUES (?,?,?,?,?,?)",
        ("Молоток", "Инструменты", "Стальной", 500.0, "RUB", None),
    )
    conn.commit()
    conn.close()

    df = load_products(db)
    assert len(df) == 1
    assert df.iloc[0]["title"] == "Молоток"
    assert df.iloc[0]["price"] == 500.0
    assert "молоток" in str(df.iloc[0]["search_text"]).lower()


def test_retriever_search_prefers_matching_product():
    # Уникальный токен только у первой строки — детерминированный top-1
    df = pd.DataFrame(
        {
            "title": ["Обои флизелиновые", "Клей ПВА"],
            "category": ["Отделка", "Материалы"],
            "description": ["", ""],
            "price": [100, 50],
            "price_currency": ["RUB", "RUB"],
            "search_text": [
                "обои флизелиновые отделка xyzunique123",
                "клей пва материалы",
            ],
        }
    )
    r = Retriever(df)
    out = r.search("xyzunique123", top_k=5)
    assert not out.empty
    assert out.iloc[0]["title"] == "Обои флизелиновые"
    assert (out["_bm25_score"] >= 0).all()


def test_retriever_empty_query_returns_head():
    df = pd.DataFrame(
        {
            "title": ["A", "B"],
            "category": ["c", "c"],
            "description": ["", ""],
            "price": [1, 2],
            "price_currency": ["RUB", "RUB"],
            "search_text": ["a x", "b y"],
        }
    )
    r = Retriever(df)
    out = r.search("", top_k=1)
    assert len(out) == 1


def test_retriever_query_only_punctuation_returns_head():
    df = pd.DataFrame(
        {
            "title": ["First", "Second"],
            "category": ["c", "c"],
            "description": ["", ""],
            "price": [1, 2],
            "price_currency": ["RUB", "RUB"],
            "search_text": ["alpha", "beta"],
        }
    )
    r = Retriever(df)
    out = r.search("...,,", top_k=1)
    assert len(out) == 1
    assert out.iloc[0]["title"] == "First"


def test_retriever_nan_search_text_row_still_ranks():
    df = pd.DataFrame(
        {
            "title": ["A", "B"],
            "category": ["", ""],
            "description": ["", ""],
            "price": [1, 2],
            "price_currency": ["RUB", "RUB"],
            "search_text": ["keyword match here", np.nan],
        }
    )
    r = Retriever(df)
    out = r.search("keyword", top_k=2)
    assert not out.empty
    assert out.iloc[0]["title"] == "A"


def test_retriever_partial_query_match_penalizes_score_order():
    df = pd.DataFrame(
        {
            "title": ["Оба слова", "Одно слово"],
            "category": ["", ""],
            "description": ["", ""],
            "price": [1, 2],
            "price_currency": ["RUB", "RUB"],
            "search_text": [
                "краска фасадная акриловая",
                "краска водная простая",
            ],
        }
    )
    r = Retriever(df)
    out = r.search("краска фасадная", top_k=2)
    assert out.iloc[0]["title"] == "Оба слова"
