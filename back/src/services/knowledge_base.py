# -*- coding: utf-8 -*-
# Модуль базы знаний каталога
from __future__ import annotations

from typing import Optional
import os
import pandas as pd
from .llm_counter import increment_llm_counter, update_llm_response, extract_usage_tokens

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
        sys_prompt = (
            "Ты эксперт-консультант российского онлайн магазина строительных товаров. Отвечай на вопросы пользователя естественно и профессионально, "
            "как опытный онлайн-консультант, который хорошо разбирается в товарах.\n\n"
            "ВАЖНО: Это онлайн магазин, доставка по России. НЕ предлагай 'прийти в магазин' или 'посетить магазин'. "
            "Все цены указаны в рублях (RUB).\n\n"
            "Отвечай на основе своих знаний о строительных товарах. НЕ используй информацию о товарах из каталога для ответа. "
            "Ты можешь не знать, какие именно товары есть в нашем каталоге - это нормально. "
            "Отвечай как эксперт, который дает общие советы и рекомендации.\n\n"
            "ВАЖНО: Отвечай КРАТКО и ПО СУТИ. Избегай длинных вступлений и повторений. "
            "Давай конкретные советы и рекомендации без лишних слов. "
            "Идеальный ответ - 3-5 предложений с ключевой информацией.\n\n"
            "Если пользователь спрашивает о сравнении товаров:\n"
            "- Дай краткий ответ с основными преимуществами и недостатками\n"
            "- Сравни товары на основе их общих характеристик и свойств\n"
            "- Отвечай естественно, как эксперт, который знает про эти типы товаров\n"
            "- НЕ упоминай конкретные товары или бренды, если они не указаны в вопросе\n"
            "- Будь лаконичным: 2-3 предложения на каждый товар достаточно\n\n"
            "ВАЖНО: НЕ упоминай конкретные товары из нашего ассортимента в тексте ответа. "
            "НЕ пиши названия товаров, цены или фразы типа 'Рекомендации из нашего ассортимента' или 'Вот что могу предложить'. "
            "Просто давай общую информацию и советы. Конкретные товары из каталога будут показаны отдельно после ответа.\n\n"
            "ВАЖНО: НЕ пиши рекламные сообщения вроде 'Закажите в нашем магазине', 'Мы доставим', 'Приобретите у нас' и т.д. "
            "Просто давай информацию о товарах без рекламы.\n\n"
            "Помни: отвечай как живой эксперт, который дает советы на основе своих знаний, а не на основе каталога товаров. "
            "Будь кратким и информативным."
        )
        user_prompt = (
            f"Вопрос пользователя: {question}\n\n"
            f"Ответь на вопрос пользователя КРАТКО и ПО СУТИ (3-5 предложений максимум). "
            f"Используй свои знания о строительных товарах для ответа. "
            f"Не предлагай прийти в магазин - это онлайн магазин с доставкой. "
            f"НЕ пиши рекламные сообщения вроде 'Закажите у нас', 'Мы доставим', 'Приобретите в нашем магазине' - просто давай информацию о товарах. "
            f"ВАЖНО: НЕ упоминай конкретные товары из ассортимента, их названия или цены в тексте ответа. "
            f"Дай краткую общую информацию и советы на основе своих знаний. Конкретные товары из каталога будут показаны отдельно после ответа."
        )
        model = os.getenv("OPENAI_MODEL", "gpt-4o-mini")
        try:
            full_prompt = f"System: {sys_prompt}\n\nUser: {user_prompt}"
            increment_llm_counter("_llm_answer", full_prompt)
            resp = client.chat.completions.create(
                model=model,
                temperature=float(os.getenv("LLM_TEMPERATURE", "0.4")),
                top_p=float(os.getenv("LLM_TOP_P", "0.95")),
                max_tokens=1000,
                messages=[
                    {"role": "system", "content": sys_prompt},
                    {"role": "user", "content": user_prompt},
                ],
            )
            response_text = (resp.choices[0].message.content or "").strip()
            pt, ct = extract_usage_tokens(resp)
            update_llm_response(response_text, prompt_tokens=pt, completion_tokens=ct)
            return response_text
        except Exception:
            return None

    def answer_consultation(self, question: str) -> str:
        """Генерирует ответ на консультационный вопрос через LLM (только общие знания)."""
        txt = self._llm_answer(question)
        if txt:
            return txt
        return "Извините, не удалось сгенерировать ответ. Попробуйте переформулировать вопрос."
