# -*- coding: utf-8 -*-
import pandas as pd
import pytest

from src.services import llm_counter
from src.services import logs_db


@pytest.fixture
def simple_catalog_df():
    return pd.DataFrame(
        {
            "title": ["Молоток стальной", "Отвёртка"],
            "category": ["Инструмент", "Инструмент"],
            "description": ["", ""],
            "price": [100.0, 50.0],
            "price_currency": ["RUB", "RUB"],
            "search_text": [
                "молоток стальной инструмент",
                "отвёртка инструмент",
            ],
        }
    )


@pytest.fixture(autouse=True)
def reset_llm_counter_callbacks():
    llm_counter.set_llm_counter_callback(None)
    llm_counter.set_llm_response_callback(None)
    yield
    llm_counter.set_llm_counter_callback(None)
    llm_counter.set_llm_response_callback(None)


@pytest.fixture(autouse=True)
def close_logs_db_connection():
    yield
    conn = getattr(logs_db._local, "conn", None)
    if conn is not None:
        conn.close()
    logs_db._local = logs_db.threading.local()
