# -*- coding: utf-8 -*-
"""
Модуль для определения необходимых товаров для задач
"""
from typing import List, Dict, Optional, Any
import os, json
import re
from .llm_counter import increment_llm_counter, update_llm_response, extract_usage_tokens
from .network_utils import is_network_error
from .prompt_registry import build_prompt

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

def _fix_json_control_chars(json_str: str) -> str:
    """
    Исправляет JSON строку, заменяя неэкранированные управляющие символы 
    внутри строковых значений на их экранированные эквиваленты.
    Использует посимвольный парсинг для надежной обработки экранированных символов.
    """
    result = []
    i = 0
    in_string = False
    escape_next = False
    
    while i < len(json_str):
        char = json_str[i]
        
        if escape_next:
            # Следующий символ после обратного слеша - экранированный
            result.append(char)
            escape_next = False
        elif char == '\\':
            # Обратный слеш - следующий символ будет экранирован
            result.append(char)
            escape_next = True
        elif char == '"':
            # Кавычка - переключает состояние "внутри строки"
            result.append(char)
            in_string = not in_string
        elif in_string:
            # Внутри строкового значения: заменяем управляющие символы
            if char == '\n':
                result.append('\\n')
            elif char == '\r':
                result.append('\\r')
            elif char == '\t':
                result.append('\\t')
            elif ord(char) < 32:  # Другие управляющие символы (ASCII < 32)
                result.append(f'\\u{ord(char):04x}')
            else:
                result.append(char)
        else:
            # Вне строкового значения - оставляем как есть
            result.append(char)
        
        i += 1
    
    return ''.join(result)

def get_required_products_for_task(task_description: str) -> Dict[str, Any]:
    """
    Определяет список необходимых товаров для задачи через LLM.
    Возвращает словарь с текстовым описанием товаров и списком названий для поиска.
    
    Args:
        task_description: Описание задачи пользователя
    
    Returns:
        Словарь вида {
            "text": "Для поклейки обоев вам понадобятся...",
            "products": [{"name": "обои"}, ...]
        }
    """
    client, err = _get_openai_client()
    if err or client is None:
        # Если LLM недоступен, возвращаем пустой объект
        return {"text": "", "products": []}
    
    prompt = build_prompt("required_products_for_task", task_description=task_description)
    model = os.getenv("OPENAI_MODEL", "gpt-4o-mini")
    
    try:
        increment_llm_counter("get_required_products_for_task", prompt.full, prompt.name)
        resp = client.chat.completions.create(
            model=model,
            max_tokens=2000,
            temperature=0.6,
            top_p=0.95,
            messages=[
                {"role": "system", "content": prompt.system},
                {"role": "user", "content": prompt.user},
            ],
        )
        text = (resp.choices[0].message.content or "").strip()
        pt, ct = extract_usage_tokens(resp)
        update_llm_response(text, prompt_tokens=pt, completion_tokens=ct)

        # Извлекаем JSON из ответа
        # Удаляем markdown код блоки если есть
        if "```json" in text or "```" in text:
            # Ищем начало и конец JSON блока
            json_start = text.find("{")
            json_end = text.rfind("}") + 1
            if json_start != -1 and json_end > json_start:
                text = text[json_start:json_end]
            else:
                # Пробуем найти массив
                json_start = text.find("[")
                json_end = text.rfind("]") + 1
                if json_start != -1 and json_end > json_start:
                    text = text[json_start:json_end]
        
        # Исправляем управляющие символы в JSON перед парсингом
        text = _fix_json_control_chars(text)
        
        try:
            result = json.loads(text)
            if isinstance(result, dict):
                # Новый формат: объект с text и products
                text_content = result.get("text", "").strip()
                products_list = result.get("products", [])
                
                # Обрабатываем список товаров
                products = []
                for p in products_list:
                    if isinstance(p, dict):
                        name = p.get("name", "").strip()
                        if name:
                            products.append({"name": name})
                    elif isinstance(p, str):
                        p_str = p.strip()
                        if p_str:
                            products.append({"name": p_str})
                
                return {"text": text_content, "products": products}
            elif isinstance(result, list):
                # Старый формат для обратной совместимости
                products = []
                for p in result:
                    if isinstance(p, dict):
                        name = p.get("name", "").strip()
                        if name:
                            products.append({"name": name})
                    elif isinstance(p, str):
                        p_str = p.strip()
                        if p_str:
                            products.append({"name": p_str})
                # Формируем простой текст из списка
                if products:
                    items = []
                    for p in products:
                        items.append(f"**{p['name']}**")
                    text_content = "Для выполнения задачи вам понадобятся следующие товары:\n\n" + "\n".join(items)
                else:
                    text_content = ""
                return {"text": text_content, "products": products}
        except Exception as e:
            # Логируем ошибку парсинга для отладки
            print(f"[DEBUG] Failed to parse JSON from get_required_products_for_task: {e}")
            print(f"[DEBUG] Full text received: {text}")
            # Также обновляем ответ в логе с информацией об ошибке
            update_llm_response(f"{text}\n\n[ОШИБКА ПАРСИНГА JSON: {e}]")
            pass
    except Exception as e:
        if is_network_error(e):
            raise
        # Логируем общую ошибку
        print(f"[DEBUG] Exception in get_required_products_for_task: {e}")
    
    # Значение по умолчанию - возвращаем пустой объект
    return {"text": "", "products": []}


def should_ask_clarification(task_description: str, conversation_history: List[Dict[str, str]]) -> Optional[str]:
    """
    Определяет, нужно ли задать уточняющий вопрос перед показом товаров.
    Возвращает вопрос для уточнения или None, если уточнения не нужны.
    """
    client, err = _get_openai_client()
    if err or client is None:
        # Если LLM недоступен — без уточняющих вопросов.
        return None
    
    # Формируем историю диалога для контекста
    history_text = ""
    if conversation_history:
        for msg in conversation_history[-3:]:  # Последние 3 сообщения
            role = "Пользователь" if msg.get("role") == "user" else "Ассистент"
            content = msg.get("content", "")
            history_text += f"{role}: {content}\n"
    
    prompt = build_prompt(
        "should_ask_clarification",
        history_text=history_text,
        task_description=task_description,
    )
    model = os.getenv("OPENAI_MODEL", "gpt-4o-mini")
    
    try:
        increment_llm_counter("should_ask_clarification", prompt.full, prompt.name)
        resp = client.chat.completions.create(
            model=model,
            max_tokens=150,
            temperature=0.6,
            top_p=0.95,
            messages=[
                {"role": "system", "content": prompt.system},
                {"role": "user", "content": prompt.user},
            ],
        )
        text = (resp.choices[0].message.content or "").strip()
        pt, ct = extract_usage_tokens(resp)
        update_llm_response(text, prompt_tokens=pt, completion_tokens=ct)

        # Если ответ содержит "НЕТ" или похожее, возвращаем None
        if any(word in text.upper() for word in ["НЕТ", "НЕ НУЖЕН", "НЕ НУЖНО", "НЕ ТРЕБУЕТСЯ"]):
            return None
        
        # Если это вопрос (содержит знак вопроса), возвращаем его
        if "?" in text and len(text) > 10:
            return text
        
        return None
    except Exception as e:
        if is_network_error(e):
            raise
        return None
