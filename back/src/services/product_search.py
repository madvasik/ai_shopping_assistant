from typing import List, Dict, Any, Optional
import pandas as pd
import numpy as np
from rank_bm25 import BM25Okapi
import re

def _ensure_cols(df: pd.DataFrame) -> pd.DataFrame:
    """Обеспечивает наличие необходимых колонок"""
    for c in ["title", "category", "description", "price", "price_currency", "search_text"]:
        if c not in df.columns:
            df[c] = np.nan
    return df

def _tokenize(text: str) -> List[str]:
    """Токенизация текста для BM25"""
    if pd.isna(text):
        return []
    text = str(text).lower()
    # Простая токенизация: разбиваем по пробелам и убираем пунктуацию
    tokens = re.findall(r'\b\w+\b', text)
    return tokens

def load_products(csv_path) -> pd.DataFrame:
    """Загружает товары из CSV или XLSX файла"""
    if str(csv_path).lower().endswith(".xlsx"):
        df = pd.read_excel(csv_path)
    else:
        df = pd.read_csv(csv_path)
    df = _ensure_cols(df)
    df["price"] = pd.to_numeric(df["price"], errors="coerce")
    
    # Формируем search_text из title + category + description
    if "search_text" not in df.columns or df["search_text"].isna().all():
        df["search_text"] = (
            df["title"].fillna("").astype(str) + " " +
            df["category"].fillna("").astype(str) + " " +
            df["description"].fillna("").astype(str)
        )
    
    df["title"] = df["title"].fillna("Item")
    return df

class Retriever:
    """BM25-based retriever для поиска товаров"""
    
    def __init__(self, df: pd.DataFrame):
        self.df = df.copy()
        
        # Токенизируем все тексты для BM25
        tokenized_corpus = [
            _tokenize(text) for text in self.df["search_text"].astype(str)
        ]
        
        # Инициализируем BM25
        self.bm25 = BM25Okapi(tokenized_corpus)
    
    def search(self, query: str, top_k: int = 100) -> pd.DataFrame:
        """Поиск товаров по запросу с использованием BM25"""
        if not query or not query.strip():
            # Если запрос пустой, возвращаем все товары
            return self.df.head(top_k).copy()
        
        # Токенизируем запрос
        tokenized_query = _tokenize(query)
        
        if not tokenized_query:
            return self.df.head(top_k).copy()
        
        # Получаем BM25 scores
        scores = self.bm25.get_scores(tokenized_query)
        
        # Добавляем scores в DataFrame
        result_df = self.df.copy()
        result_df["_bm25_score"] = scores
        
        # Улучшаем ранжирование: добавляем бонус за полное совпадение всех значимых слов запроса
        # Исключаем стоп-слова (для, и, или, в, на, с и т.д.)
        stop_words = {"для", "и", "или", "в", "на", "с", "по", "от", "до", "из", "к", "о", "об", "про", "со", "то", "же", "как", "что"}
        query_words = set(tokenized_query) - stop_words
        
        if query_words:  # Если остались значимые слова после удаления стоп-слов
            # Нормализуем слова запроса (убираем окончания для более гибкого сравнения)
            def normalize_word(word: str) -> str:
                """Нормализует слово для более гибкого сравнения"""
                # Убираем типичные окончания русского языка (упрощенная нормализация)
                if len(word) > 4:
                    # Убираем окончания -а, -я, -ы, -и, -ов, -ев и т.д.
                    if word.endswith(('а', 'я', 'ы', 'и', 'ов', 'ев', 'ей', 'ам', 'ями', 'ом', 'ем', 'ой', 'ей')):
                        return word[:-1]
                    # Убираем окончания -ами, -ями
                    if len(word) > 5 and word.endswith(('ами', 'ями')):
                        return word[:-2]
                return word
            
            # Также создаем множество вариантов для более гибкого сравнения
            def get_word_variants(word: str) -> set:
                """Возвращает варианты слова для более гибкого сравнения"""
                variants = {word, normalize_word(word)}
                # Добавляем варианты без окончаний
                if len(word) > 3:
                    variants.add(word[:-1])  # Без последней буквы
                return variants
            
            # Создаем множества вариантов для каждого слова запроса
            query_word_variants = []
            for w in query_words:
                variants = get_word_variants(w)
                query_word_variants.append(variants)
            
            # Вычисляем бонус за полное совпадение
            def calculate_bonus(search_text: str) -> float:
                """Вычисляет бонус за полное совпадение всех значимых слов запроса"""
                if pd.isna(search_text):
                    return 0.0
                search_text_lower = str(search_text).lower()
                search_words = set(_tokenize(search_text_lower))
                normalized_search_words = {normalize_word(w) for w in search_words}
                # Также добавляем варианты слов из поискового текста
                search_word_variants = set()
                for w in search_words:
                    search_word_variants.update(get_word_variants(w))
                
                # Проверяем, сколько слов запроса найдено в тексте товара
                matched_count = 0
                for query_variants in query_word_variants:
                    # Проверяем, есть ли хотя бы один вариант слова запроса в вариантах текста товара
                    if query_variants.intersection(search_word_variants) or query_variants.intersection(normalized_search_words):
                        matched_count += 1
                
                total_query_words = len(query_word_variants)
                
                if matched_count == total_query_words:
                    # Большой бонус за полное совпадение всех значимых слов
                    return total_query_words * 3.0  # Бонус 3.0 за каждое значимое слово
                elif matched_count == 0:
                    return 0.0
                elif matched_count < total_query_words:
                    # Штраф за неполное совпадение - чем меньше совпало слов, тем больше штраф
                    # Если совпало меньше половины - очень большой штраф
                    if matched_count < total_query_words / 2:
                        return -10.0  # Очень большой штраф за неполное совпадение
                    else:
                        # Если совпало больше половины, но не все - средний штраф
                        return -3.0
                else:
                    # Это не должно произойти, но на всякий случай
                    return 0.0
            
            # Применяем бонус к каждому товару
            bonuses = result_df["search_text"].apply(calculate_bonus)
            result_df["_bm25_score"] = result_df["_bm25_score"] + bonuses
        
        # Сортируем по убыванию score и возвращаем top_k
        return result_df.sort_values("_bm25_score", ascending=False).head(top_k).copy()

def apply_filters(df: pd.DataFrame, slots: Dict[str, Any]) -> pd.DataFrame:
    """Применяет фильтры к результатам поиска. Только фильтр по цене."""
    out = df.copy()
    
    # Фильтр по бюджету
    bmin, bmax = slots.get("budget_min"), slots.get("budget_max")
    if bmin is not None:
        out = out[out["price"].fillna(0) >= float(bmin)]
    if bmax is not None:
        out = out[out["price"].fillna(1e18) <= float(bmax)]
    
    return out
