# -*- coding: utf-8 -*-
# Модуль базы знаний каталога
from __future__ import annotations

from typing import Optional
import os
import pandas as pd
from .llm_counter import increment_llm_counter, update_llm_response, extract_usage_tokens
from .network_utils import is_network_error
from .prompt_registry import build_prompt

__all__ = ["CatalogKB"]

CORE_FIELDS = [
    "title", "category", "description", "price", "price_currency", "search_text",
]


def _get_openai_client():
    """Получает клиент OpenAI API"""
    try:
        from openai import OpenAI
    except Exception as e:
        return None, f"Ошибка импорта openai: {e}"
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        return None, "OPENAI_API_KEY не установлен"
    try:
        client = OpenAI(api_key=api_key)
        return client, None
    except Exception as e:
        return None, str(e)


class CatalogKB:
    """Консультации по строительным товарам через LLM (без подмешивания каталога в промпт)."""

    def __init__(self, df: pd.DataFrame):
        self.df = df.copy()
        for c in CORE_FIELDS:
            if c not in self.df.columns:
                self.df[c] = None
        if "search_text" not in self.df.columns:
            self.df["search_text"] = (
                self.df["title"].astype(str) + " "
                + self.df["category"].astype(str) + " "
                + self.df["description"].astype(str)
            )

    def _llm_answer(self, question: str) -> Optional[str]:
        client, err = _get_openai_client()
        if err or client is None:
            return None
        prompt = build_prompt("consultation_answer", question=question)
        model = os.getenv("OPENAI_MODEL", "gpt-4o-mini")
        try:
            increment_llm_counter("_llm_answer", prompt.full, prompt.name)
            resp = client.chat.completions.create(
                model=model,
                temperature=float(os.getenv("LLM_TEMPERATURE", "0.4")),
                top_p=float(os.getenv("LLM_TOP_P", "0.95")),
                max_tokens=1000,
                messages=[
                    {"role": "system", "content": prompt.system},
                    {"role": "user", "content": prompt.user},
                ],
            )
            response_text = (resp.choices[0].message.content or "").strip()
            pt, ct = extract_usage_tokens(resp)
            update_llm_response(response_text, prompt_tokens=pt, completion_tokens=ct)
            return response_text
        except Exception as e:
            if is_network_error(e):
                raise
            return None

    def answer_consultation(self, question: str) -> str:
        """Генерирует ответ на консультационный вопрос через LLM (только общие знания)."""
        txt = self._llm_answer(question)
        if txt:
            return txt
        return "Извините, не удалось сгенерировать ответ. Попробуйте переформулировать вопрос."
