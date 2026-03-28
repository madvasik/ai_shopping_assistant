# -*- coding: utf-8 -*-
# Модуль базы знаний каталога
from __future__ import annotations
from dataclasses import dataclass
from typing import List, Dict, Any, Optional
import os, json
import pandas as pd
from .llm_counter import increment_llm_counter, update_llm_response

# Безопасная, опциональная зависимость от product_search.Retriever
try:
    from .product_search import Retriever
except Exception as e:
    Retriever = None  # type: ignore
    _retriever_import_err = e
else:
    _retriever_import_err = None

__all__ = ["CatalogKB", "KBAnswer"]

def _get_mistral_client():
    """Получает клиент Mistral API"""
    try:
        from mistralai import Mistral
    except Exception as e:
        return None, f"Ошибка импорта mistralai: {e}"
    api_key = os.getenv("MISTRAL_API_KEY")
    if not api_key:
        return None, "MISTRAL_API_KEY не установлен"
    try:
        client = Mistral(api_key=api_key)
        return client, None
    except Exception as e:
        return None, str(e)

CORE_FIELDS = [
    "title", "category", "description", "price", "price_currency", "search_text",
]

def _fmt_price(price: Any, cur: Any) -> str:
    if pd.isna(price): return "-"
    try: return f"{int(price):,} {cur or ''}".replace(",", " ")
    except Exception: return f"{price} {cur or ''}".strip()

def _slim_row(row: pd.Series) -> Dict[str, Any]:
    return {k: row.get(k, None) for k in CORE_FIELDS}

def _detect_lang(text: str) -> str:
    return "ru" if any("\u0400" <= ch <= "\u04FF" for ch in text or "") else "en"

@dataclass
class KBAnswer:
    answer: str
    items: pd.DataFrame  # может быть пустым

class CatalogKB:
    """Легковесная база знаний: BM25 поиск по каталогу + опциональный ответ LLM."""
    def __init__(self, df: pd.DataFrame):
        if _retriever_import_err is not None or Retriever is None:
            raise ImportError(
                f"search.Retriever failed to import: {_retriever_import_err}"
            )
        self.df = df.copy()
        for c in CORE_FIELDS:
            if c not in self.df.columns: 
                self.df[c] = None
        if "search_text" not in self.df.columns:
            self.df["search_text"] = (
                self.df["title"].astype(str) + " " +
                self.df["category"].astype(str) + " " +
                self.df["description"].astype(str)
            )
        self.retriever = Retriever(self.df)

    def retrieve(self, query: str, k: int = 40) -> pd.DataFrame:
        return self.retriever.search(query or "", top_k=k)

    def _context_from_items(self, items: pd.DataFrame, limit: int = 40) -> str:
        items = items.head(limit).reset_index(drop=True)
        # Примечание: default=str делает pandas.Timestamp и другие не-JSON типы сериализуемыми
        return "\n".join(
            json.dumps(_slim_row(r), ensure_ascii=False, default=str)
            for _, r in items.iterrows()
        )

    def _llm_answer(self, question: str, context_jsonl: str, language: str) -> Optional[str]:
        client, err = _get_mistral_client()
        if err or client is None: return None
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
        
        # Формируем промпт - отвечай на основе своих знаний, не используй информацию о товарах из каталога
        if context_jsonl:
            # Если есть контекст товаров (старый режим для обратной совместимости)
            user_prompt = (
                f"Вопрос пользователя: {question}\n\n"
                f"Ответь на вопрос пользователя КРАТКО и ПО СУТИ (3-5 предложений максимум). "
                f"Используй свои знания о строительных товарах для ответа. "
                f"НЕ используй информацию о товарах из каталога ниже - она нужна только для справки, но ты можешь не знать какие товары есть в нашем каталоге. "
                f"Не предлагай прийти в магазин - это онлайн магазин с доставкой. "
                f"НЕ пиши рекламные сообщения вроде 'Закажите у нас', 'Мы доставим', 'Приобретите в нашем магазине' - просто давай информацию о товарах. "
                f"ВАЖНО: НЕ упоминай конкретные товары из ассортимента, их названия или цены в тексте ответа. "
                f"Дай краткую общую информацию и советы на основе своих знаний. Конкретные товары из каталога будут показаны отдельно после ответа.\n\n"
                f"(Информация о товарах каталога для справки, но не используй её в ответе):\n{context_jsonl[:80000]}"
            )
        else:
            # Если контекста нет (Consultation flow) - отвечаем только на основе знаний
            user_prompt = (
                f"Вопрос пользователя: {question}\n\n"
                f"Ответь на вопрос пользователя КРАТКО и ПО СУТИ (3-5 предложений максимум). "
                f"Используй свои знания о строительных товарах для ответа. "
                f"Не предлагай прийти в магазин - это онлайн магазин с доставкой. "
                f"НЕ пиши рекламные сообщения вроде 'Закажите у нас', 'Мы доставим', 'Приобретите в нашем магазине' - просто давай информацию о товарах. "
                f"ВАЖНО: НЕ упоминай конкретные товары из ассортимента, их названия или цены в тексте ответа. "
                f"Дай краткую общую информацию и советы на основе своих знаний. Конкретные товары из каталога будут показаны отдельно после ответа."
            )
        payload = user_prompt
        model = os.getenv("MISTRAL_MODEL", "mistral-medium-latest")
        try:
            # Логируем полный промпт (включая system и user сообщения)
            full_prompt = f"System: {sys_prompt}\n\nUser: {payload}"
            increment_llm_counter("_llm_answer", full_prompt)  # Увеличиваем счетчик запросов к LLM
            resp = client.chat.complete(
                model=model,
                temperature=float(os.getenv("LLM_TEMPERATURE", "0.4")),
                top_p=float(os.getenv("LLM_TOP_P", "0.95")),
                max_tokens=1000,  # Ограничение для более коротких ответов, но достаточно для завершения мысли
                messages=[{"role":"system","content":sys_prompt},{"role":"user","content":payload}],
            )
            response_text = (resp.choices[0].message.content or "").strip()
            # Обновляем ответ в логе
            update_llm_response(response_text)
            return response_text
        except Exception:
            return None

    def answer_consultation(self, question: str) -> str:
        """Генерирует ответ на консультационный вопрос БЕЗ использования товаров из каталога.
        LLM отвечает только на основе своих знаний."""
        lang = "ru"
        # НЕ передаем товары в LLM - используем пустой контекст
        txt = self._llm_answer(question, "", lang)
        if txt:
            return txt
        # Резервный вариант
        return "Извините, не удалось сгенерировать ответ. Попробуйте переформулировать вопрос."

    def answer(self, question: str, top_k: int = 40, recommend_k: int = 5) -> KBAnswer:
        """Генерирует ответ на вопрос пользователя с рекомендациями товаров"""
        # Всегда используем русский язык для российского онлайн магазина
        lang = "ru"
        items = self.retrieve(question, k=top_k)
        ctx = self._context_from_items(items, limit=top_k)
        txt = self._llm_answer(question, ctx, lang)
        
        # Фильтруем товары по релевантности (BM25 score > 0.3 и > 0.0)
        # Это исключает нерелевантные товары, которые могли попасть в результаты поиска
        if not items.empty and "_bm25_score" in items.columns:
            relevant_items = items[
                (items["_bm25_score"] > 0.3) & 
                (items["_bm25_score"] > 0.0)
            ].copy()
            # Если после фильтрации остались релевантные товары, используем их
            # Если все товары были отфильтрованы как нерелевантные, возвращаем пустой список
            if not relevant_items.empty:
                items = relevant_items
            else:
                # Если нет релевантных товаров, возвращаем пустой DataFrame
                items = pd.DataFrame(columns=items.columns)
        
        if txt:
            # Если есть релевантные товары, возвращаем их, иначе пустой список
            if not items.empty:
                return KBAnswer(answer=txt, items=items.head(recommend_k))
            else:
                return KBAnswer(answer=txt, items=pd.DataFrame(columns=self.df.columns))

        # Резервный вариант (без LLM) - только русский язык для российского онлайн магазина
        if items.empty:
            # Если нет релевантных товаров, возвращаем сообщение об этом
            return KBAnswer(
                answer="К сожалению, не удалось найти релевантные товары в каталоге. Попробуйте переформулировать запрос.",
                items=pd.DataFrame(columns=self.df.columns)
            )
        
        head = items.head(recommend_k)
        lines: List[str] = []
        lines.append("Ниже — краткий ответ по каталогу и рекомендации.")
        lines.append("Рекомендации:")
        for _, r in head.iterrows():
            category_info = f" ({r.get('category')})" if pd.notna(r.get("category")) else ""
            lines.append(f"• {r.get('title')}{category_info} — {_fmt_price(r.get('price'), r.get('price_currency'))}")
            if pd.notna(r.get("description")):
                desc = str(r.get("description"))[:100]  # Первые 100 символов описания
                lines.append(f"  {desc}...")
        return KBAnswer(answer="\n".join(lines), items=head)
