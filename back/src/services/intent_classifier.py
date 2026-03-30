# -*- coding: utf-8 -*-
import os, json, re
from typing import Dict, Any, List, Optional
from .llm_counter import increment_llm_counter, update_llm_response, extract_usage_tokens
from .network_utils import is_network_error
from .prompt_registry import build_prompt


def _parse_yes_no_ru(text: str) -> Optional[bool]:
    """Первое слово да/нет; иначе None (нужен запасной разбор)."""
    if not text:
        return None
    first = re.split(r"[\s.,;:!?«»\"]+", text.strip().lower(), maxsplit=1)[0]
    if first in ("да", "yes"):
        return True
    if first in ("нет", "no"):
        return False
    return None


def _get_openai_client():
    """
    Возвращает (client, err). Использует нативный клиент OpenAI API.
    """
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

def classify_intent(messages: List[Dict[str,str]], 
                   temperature: float = 0.2,
                   top_p: float = 0.95) -> str:
    """
    Классифицирует намерение пользователя: "task" (подбор товаров) или "consultation" (вопросы/сравнения).
    Возвращает "task" или "consultation".
    Использует только LLM, без эвристики.
    """
    # Получаем последнее сообщение пользователя
    last_user = ""
    for m in reversed(messages):
        if m.get("role") == "user":
            last_user = m.get("content", "")
            break
    
    if not last_user:
        return "task"  # По умолчанию task
    
    # Используем только LLM
    client, err = _get_openai_client()
    if err or client is None:
        # Если LLM недоступен, возвращаем task по умолчанию
        return "task"
    
    # Формируем контекст диалога для лучшего понимания
    context_messages = []
    for msg in messages[-5:]:  # Последние 5 сообщений для контекста
        if msg.get("role") == "user":
            context_messages.append(f"Пользователь: {msg.get('content', '')}")
        elif msg.get("role") == "assistant":
            context_messages.append(f"Ассистент: {msg.get('content', '')}")
    
    context_text = "\n".join(context_messages) if context_messages else f"Пользователь: {last_user}"
    
    prompt = build_prompt("classify_intent", context_text=context_text)
    model = os.getenv("OPENAI_MODEL", "gpt-4o-mini")
    
    try:
        increment_llm_counter("classify_intent", prompt.full, prompt.name)
        resp = client.chat.completions.create(
            model=model,
            max_tokens=10,
            temperature=temperature,
            top_p=top_p,
            messages=[
                {"role": "system", "content": prompt.system},
                {"role": "user", "content": prompt.user},
            ],
        )
        text = (resp.choices[0].message.content or "").strip().lower()
        pt, ct = extract_usage_tokens(resp)
        update_llm_response(text, prompt_tokens=pt, completion_tokens=ct)
        if "consultation" in text:
            return "consultation"
        elif "task" in text:
            return "task"
        else:
            # Значение по умолчанию при неопределенном ответе
            return "task"
    except Exception as e:
        if is_network_error(e):
            raise
        return "task"

def is_catalog_related(query: str) -> bool:
    """
    Проверяет, относится ли вопрос к каталогу строительного магазина через LLM.
    """
    if not query:
        return False

    # Используем LLM для проверки релевантности
    client, err = _get_openai_client()
    if err or client is None:
        # Если LLM недоступен, возвращаем False по умолчанию
        return False
    
    prompt = build_prompt("is_catalog_related", query=query)
    model = os.getenv("OPENAI_MODEL", "gpt-4o-mini")
    
    try:
        increment_llm_counter("is_catalog_related", prompt.full, prompt.name)
        resp = client.chat.completions.create(
            model=model,
            max_tokens=10,
            temperature=0.2,
            top_p=0.95,
            messages=[
                {"role": "system", "content": prompt.system},
                {"role": "user", "content": prompt.user},
            ],
        )
        text = (resp.choices[0].message.content or "").strip()
        pt, ct = extract_usage_tokens(resp)
        update_llm_response(text, prompt_tokens=pt, completion_tokens=ct)
        parsed = _parse_yes_no_ru(text)
        if parsed is not None:
            return parsed
        low = text.lower()
        return "да" in low
    except Exception as e:
        if is_network_error(e):
            raise
        return False

def extract_product_names_from_query(query: str, 
                                     temperature: float = 0.2,
                                     top_p: float = 0.95) -> List[str]:
    """
    Извлекает наименования товаров из запроса пользователя через LLM.
    Возвращает список всех упомянутых товаров без фильтрации по категориям.
    Фильтрация по наличию в базе происходит позже при поиске.
    """
    if not query:
        return []
    
    client, err = _get_openai_client()
    if err or client is None:
        return []
    
    prompt = build_prompt("extract_product_names", query=query)
    model = os.getenv("OPENAI_MODEL", "gpt-4o-mini")
    
    try:
        increment_llm_counter("extract_product_names_from_query", prompt.full, prompt.name)
        resp = client.chat.completions.create(
            model=model,
            max_tokens=200,
            temperature=temperature,
            top_p=top_p,
            messages=[
                {"role": "system", "content": prompt.system},
                {"role": "user", "content": prompt.user},
            ],
        )
        text = (resp.choices[0].message.content or "").strip()

        # Очищаем markdown код блоки если есть
        if text.startswith("```"):
            text = text.strip("`")
            if text.startswith("json"):
                text = text[4:]
            text = text.strip()

        pt, ct = extract_usage_tokens(resp)
        update_llm_response(text, prompt_tokens=pt, completion_tokens=ct)
        
        # Извлекаем JSON массив
        if text.startswith("["):
            product_names = json.loads(text)
            if isinstance(product_names, list):
                # Возвращаем все извлеченные товары без фильтрации
                return [name.lower().strip() for name in product_names if isinstance(name, str)]
        
        return []
    except Exception as e:
        if is_network_error(e):
            raise
        return []

def check_products_relevance(category_name: str, products: List[Dict[str, Any]], 
                            temperature: float = 0.2,
                            top_p: float = 0.95) -> List[int]:
    """
    Проверяет соответствие товаров категории карусели через LLM.
    Проверяет все переданные товары за один запрос (обычно до 3 товаров в карусели).
    
    Возвращает список из 0 и 1 (0 - не соответствует категории, 1 - соответствует).
    
    Args:
        category_name: Название категории карусели (например, "Леса строительные")
        products: Список товаров (каждый товар - dict с полями title, category, description и т.д.)
    
    Returns:
        Список бинарных значений [0 или 1] для каждого товара
    """
    if not category_name or not products or len(products) == 0:
        return []
    
    # Проверяем все переданные товары (обычно это вся карусель - до 3 товаров)
    products_to_check = products

    # Сначала отделяем товары с валидным названием — без этого LLM не вызываем и не считаем их релевантными
    products_text = []
    valid_indices = []
    for idx, product in enumerate(products_to_check):
        title = str(product.get("title", "")).strip()
        if not title or title == "nan":
            continue

        category = str(product.get("category", "")).strip()
        description = str(product.get("description", "")).strip()

        product_num = len(valid_indices) + 1
        product_info = f"Товар {product_num}: {title}"
        if category and category != "nan":
            product_info += f" (категория: {category})"
        if description and description != "nan":
            product_info += f" - {description}"

        products_text.append(product_info)
        valid_indices.append(idx)

    if not valid_indices:
        return [0] * len(products_to_check)

    client, err = _get_openai_client()
    if err or client is None:
        # Нет LLM: нерелевантные позиции уже отсеяны — остальные считаем релевантными
        out = [0] * len(products_to_check)
        for i in valid_indices:
            out[i] = 1
        return out
    
    products_list = "\n".join(products_text)
    
    prompt = build_prompt(
        "check_products_relevance",
        category_name=category_name,
        products_list=products_list,
    )
    model = os.getenv("OPENAI_MODEL", "gpt-4o-mini")
    
    try:
        increment_llm_counter("check_products_relevance", prompt.full, prompt.name)
        resp = client.chat.completions.create(
            model=model,
            max_tokens=10,
            temperature=temperature,
            top_p=top_p,
            messages=[
                {"role": "system", "content": prompt.system},
                {"role": "user", "content": prompt.user},
            ],
        )
        text = (resp.choices[0].message.content or "").strip()
        pt, ct = extract_usage_tokens(resp)
        update_llm_response(text, prompt_tokens=pt, completion_tokens=ct)

        # Извлекаем бинарные значения из ответа
        # Убираем все пробелы и нецифровые символы, оставляем только 0 и 1
        binary_str = "".join([c for c in text if c in "01"])
        
        # Создаем результат для всех товаров (изначально все 0)
        result = [0] * len(products_to_check)
        
        # Заполняем результаты для валидных товаров
        for idx, valid_idx in enumerate(valid_indices):
            if idx < len(binary_str):
                result[valid_idx] = int(binary_str[idx])
            else:
                # Если не хватило значений, оставляем 0
                result[valid_idx] = 0
        
        return result
    except Exception as e:
        if is_network_error(e):
            raise
        print(f"[DEBUG] Error in check_products_relevance: {e}")
        return [1] * len(products_to_check)
