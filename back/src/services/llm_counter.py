# -*- coding: utf-8 -*-
"""
Модуль для подсчета запросов к LLM в рамках диалога.
Использует глобальную callback функцию для совместимости с разными контекстами выполнения.
"""
from typing import Optional, Callable, Dict, Any, Tuple


def extract_usage_tokens(completion: Any) -> Tuple[Optional[int], Optional[int]]:
    """
    Достаёт prompt/completion tokens из ответа chat.completions.create.
    Учитывает разные формы объекта usage (dict / pydantic, input_tokens и т.д.).
    """
    u = getattr(completion, "usage", None)
    if u is None:
        return None, None
    pt: Optional[int] = None
    ct: Optional[int] = None
    if isinstance(u, dict):
        pt = u.get("prompt_tokens")
        ct = u.get("completion_tokens")
        if pt is None:
            pt = u.get("input_tokens")
        if ct is None:
            ct = u.get("output_tokens")
    else:
        pt = getattr(u, "prompt_tokens", None)
        ct = getattr(u, "completion_tokens", None)
        if pt is None and hasattr(u, "input_tokens"):
            pt = getattr(u, "input_tokens", None)
        if ct is None and hasattr(u, "output_tokens"):
            ct = getattr(u, "output_tokens", None)
    return pt, ct

# Глобальная callback функция для увеличения счетчика и логирования запроса
_llm_counter_callback: Optional[Callable[[str, Optional[str]], None]] = None
# Глобальная callback функция для обновления ответа в последней записи
_llm_response_callback: Optional[Callable[[str], None]] = None

def set_llm_counter_callback(callback: Optional[Callable[[str, Optional[str]], None]]):
    """Устанавливает callback функцию для увеличения счетчика запросов к LLM и логирования"""
    global _llm_counter_callback
    _llm_counter_callback = callback

def set_llm_response_callback(callback: Optional[Callable[[str], None]]):
    """Устанавливает callback функцию для обновления ответа в последней записи"""
    global _llm_response_callback
    _llm_response_callback = callback

def increment_llm_counter(function_name: str = "Unknown", prompt_preview: Optional[str] = None):
    """
    Увеличивает счетчик запросов к LLM на 1 через callback функцию и логирует запрос
    
    Args:
        function_name: Название функции, которая делает запрос к LLM
        prompt_preview: Полный текст промпта (без обрезки)
    """
    global _llm_counter_callback
    if _llm_counter_callback is not None:
        try:
            _llm_counter_callback(function_name, prompt_preview)
        except Exception:
            # Игнорируем ошибки, если callback не может быть выполнен
            pass

def update_llm_response(response_preview: Optional[str] = None,
                        prompt_tokens: Optional[int] = None,
                        completion_tokens: Optional[int] = None):
    """
    Обновляет ответ в последней записи лога запросов к LLM

    Args:
        response_preview: Полный текст ответа (без обрезки)
        prompt_tokens: Количество токенов в запросе (из resp.usage)
        completion_tokens: Количество токенов в ответе (из resp.usage)
    """
    global _llm_response_callback
    if _llm_response_callback is not None:
        try:
            _llm_response_callback(response_preview or "N/A", prompt_tokens, completion_tokens)
        except Exception:
            pass
