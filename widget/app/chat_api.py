# -*- coding: utf-8 -*-
"""
API для обработки сообщений чата на базе сервисов из back/src/services/
"""
import os
import sys
import time
import logging
import pandas as pd
from pathlib import Path
from typing import List, Dict, Any, Optional
from dotenv import load_dotenv

logger = logging.getLogger(__name__)

# Добавляем путь к сервисам
# widget/app/chat_api.py -> widget/ -> ai-commercial-chatbot/ -> back/
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
BACK_DIR = PROJECT_ROOT / "back"
sys.path.insert(0, str(BACK_DIR))

# Глобальная переменная для корня проекта (используется в _get_data_path)
_PROJECT_ROOT = PROJECT_ROOT

from src.services.intent_classifier import (
    classify_intent,
    is_catalog_related,
    extract_product_names_from_query,
    check_products_relevance,
)
from src.services.product_search import load_products, Retriever
from src.services.knowledge_base import CatalogKB
from src.services.task_analyzer import (
    get_required_products_for_task,
    should_ask_clarification,
)
from src.services.llm_counter import set_llm_counter_callback, set_llm_response_callback
from src.services.network_utils import is_network_error, log_network_error, NETWORK_ERROR_REPLY
from src.services import logs_db

# Загружаем переменные окружения
load_dotenv()

# Глобальная переменная для отслеживания текущего запроса пользователя
_current_user_message: Optional[str] = None
_current_user_request_id: Optional[int] = None

# Глобальные переменные для кэширования
_retriever: Optional[Retriever] = None
_kb: Optional[CatalogKB] = None
_df: Optional[pd.DataFrame] = None

# Конфигурация
TOP_K_CANDIDATES = 60
FINAL_K = 5
MAX_CLARIFICATION_QUESTIONS = 2
TEMPERATURE = float(os.getenv("LLM_TEMPERATURE", "0.4"))
TOP_P = float(os.getenv("LLM_TOP_P", "0.95"))

# Тарифы gpt-4o-mini ($ за токен)
_INPUT_PRICE_PER_TOKEN = 0.150 / 1_000_000
_OUTPUT_PRICE_PER_TOKEN = 0.600 / 1_000_000


def _get_data_path() -> Path:
    """Определяет путь к источнику данных о товарах."""
    env_path = os.getenv("PRODUCTS_DB_PATH")
    if env_path:
        p = Path(env_path).expanduser()
        if p.exists():
            return p

    # Проверяем относительные пути от корня проекта
    sqlite_paths = [
        _PROJECT_ROOT / "back" / "database" / "products.db",
        _PROJECT_ROOT / "back" / "database" / "products.sqlite3",
        Path("/mnt/data/products.db"),
        Path("/mnt/data/products.sqlite3"),
    ]
    for sqlite_path in sqlite_paths:
        if sqlite_path.exists():
            return sqlite_path

    return _PROJECT_ROOT / "back" / "database" / "products.db"


def _get_products_table_name() -> str:
    """Возвращает имя таблицы с товарами в SQLite."""
    return os.getenv("PRODUCTS_TABLE", "products")


def _increment_llm_counter(function_name: str = "Unknown", prompt_preview: str = None):
    """Callback для увеличения счетчика запросов к LLM и логирования в SQLite."""
    try:
        global _current_user_message, _current_user_request_id

        # Извлекаем System и User промпты из полного промпта
        system_prompt = ""
        user_prompt = prompt_preview or "N/A"

        if "System:" in user_prompt and "User:" in user_prompt:
            parts = user_prompt.split("User:", 1)
            if len(parts) == 2:
                system_prompt = parts[0].replace("System:", "").strip()
                user_prompt = parts[1].strip()

        # Находим или создаём группу
        if _current_user_request_id is None and _current_user_message:
            found = logs_db.find_last_user_request_by_message(_current_user_message)
            if found:
                _current_user_request_id = found["id"]

        if _current_user_request_id is None:
            _current_user_request_id = logs_db.add_user_request(
                _current_user_message or "Неизвестный запрос"
            )

        logs_db.add_llm_call(
            user_request_id=_current_user_request_id,
            function=function_name,
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            original_user_message=_current_user_message or "N/A",
        )
    except Exception:
        pass


def _update_llm_response(response_preview: str = None,
                         prompt_tokens: int = None,
                         completion_tokens: int = None):
    """Callback для обновления ответа в последней записи (SQLite)."""
    global _current_user_request_id
    try:
        ur_id = _current_user_request_id
        if ur_id is None:
            return

        cost_usd = None
        if prompt_tokens is not None or completion_tokens is not None:
            pt = prompt_tokens or 0
            ct = completion_tokens or 0
            cost_usd = round(pt * _INPUT_PRICE_PER_TOKEN + ct * _OUTPUT_PRICE_PER_TOKEN, 6)

        logs_db.update_llm_response(
            user_request_id=ur_id,
            response_preview=response_preview or "N/A",
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            cost_usd=cost_usd,
        )
    except Exception:
        pass


# Устанавливаем callback функции для логирования при импорте модуля
set_llm_counter_callback(_increment_llm_counter)
set_llm_response_callback(_update_llm_response)

def _init_services():
    """Инициализирует сервисы (Retriever и CatalogKB)"""
    global _retriever, _kb, _df
    
    # Устанавливаем callback функции для логирования (всегда)
    set_llm_counter_callback(_increment_llm_counter)
    set_llm_response_callback(_update_llm_response)
    
    if _retriever is not None and _kb is not None:
        return
    
    data_path = _get_data_path()
    
    if not data_path.exists():
        raise FileNotFoundError(
            f"Файл с данными не найден: {data_path}. "
            "Убедитесь, что SQLite база products.db доступна, "
            "либо явно укажите PRODUCTS_DB_PATH."
        )

    _df = load_products(data_path, table_name=_get_products_table_name())
    
    if _df.empty:
        raise ValueError("Загруженный датасет пуст")
    
    _retriever = Retriever(_df)
    _kb = CatalogKB(_df)


def _format_product_card(row: pd.Series) -> str:
    """Форматирует карточку товара в текстовый формат"""
    title = str(row.get("title", "Товар"))
    price = row.get("price")
    currency = row.get("price_currency") or ""
    
    if pd.notna(price):
        price_str = f"{int(price):,} {currency}".replace(",", " ")
    else:
        price_str = "Цена не указана"
    
    return f"• {title} — {price_str}"


# Метка группы для парсера виджета: не попадает во вступление с markdown,
# заголовок секции рисует только карусель (без дубля **Категория:**).
_WIDGET_CATEGORY_PREFIX = "__WS_CAT__"


def _widget_category_line(product_name: str) -> str:
    label = (product_name or "").strip().capitalize() or "Товары"
    return f"\n{_WIDGET_CATEGORY_PREFIX}{label}\n"


def _process_message(
    message: str,
    conversation_history: List[Dict[str, str]],
) -> str:
    """
    Обрабатывает сообщение пользователя и возвращает ответ
    
    Args:
        message: Текст сообщения пользователя
        conversation_history: История диалога в формате [{"role": "user/assistant", "content": "..."}]
    
    Returns:
        Текст ответа ассистента
    """
    global _current_user_message, _current_user_request_id
    
    # Устанавливаем текущее сообщение пользователя для группировки
    _current_user_message = message
    
    # Убеждаемся, что callback установлены перед обработкой
    set_llm_counter_callback(_increment_llm_counter)
    set_llm_response_callback(_update_llm_response)
    
    _init_services()
    
    # Добавляем текущее сообщение в историю для обработки
    messages = conversation_history + [{"role": "user", "content": message}]
    
    # Проверяем, является ли это ответом на уточняющий вопрос
    is_follow_up_answer = False
    if len(messages) >= 2:
        prev_msg = messages[-2]
        if prev_msg.get("role") == "assistant" and "?" in prev_msg.get("content", ""):
            is_follow_up_answer = True
    
    # Если это ответ на уточняющий вопрос, формируем улучшенный запрос
    if is_follow_up_answer:
        user_messages = []
        assistant_questions = []
        for msg in messages:
            if msg.get("role") == "user":
                content = msg.get("content", "").strip()
                if len(content) >= 3 or len(user_messages) == 0:
                    user_messages.append(content)
            elif msg.get("role") == "assistant" and "?" in msg.get("content", ""):
                assistant_questions.append(msg.get("content", "").strip())
        
        if len(user_messages) > 0:
            main_task = user_messages[0]
            if len(user_messages) > 1 and len(user_messages[-1]) >= 10:
                enhanced_query = f"{main_task}. {user_messages[-1]}"
            else:
                enhanced_query = main_task
            
            if assistant_questions and len(user_messages) > 1:
                last_question = assistant_questions[-1]
                last_answer = user_messages[-1]
                enhanced_query = f"{main_task}. {last_question} Ответ: {last_answer}"
        else:
            enhanced_query = message
        
        if not enhanced_query:
            enhanced_query = message
        
        last_user = enhanced_query
    else:
        # Это не ответ на уточняющий вопрос - сначала проверяем отношение к каталогу
        last_user = message
        
        # Проверяем отношение к каталогу ПЕРЕД классификацией интента
        catalog_related = is_catalog_related(last_user)
        
        if not catalog_related:
            return (
                "Извините, ваш вопрос не относится к тематике нашего ассортимента товаров. "
                "Я могу помочь с подбором товаров или ответить на вопросы о товарах из нашего каталога "
                "строительного магазина (обои, краски, клей, инструменты для ремонта и т.д.)."
            )
        
        # Классифицируем интент только если вопрос относится к каталогу
        intent = classify_intent(messages, temperature=TEMPERATURE, top_p=TOP_P)
        
        # Ветвление в зависимости от интента
        if intent == "consultation":
            # Консультационный flow
            # Извлекаем упомянутые товары
            mentioned_product_names = extract_product_names_from_query(
                last_user, temperature=TEMPERATURE, top_p=TOP_P
            )
            
            # Генерируем ответ через LLM
            llm_answer_text = _kb.answer_consultation(last_user)
            
            # Ищем товары по упомянутым названиям
            products_by_product = {}
            if mentioned_product_names:
                for product_name in mentioned_product_names:
                    search_results = _retriever.search(product_name, top_k=10)
                    
                    if not search_results.empty:
                        relevant_results = search_results[
                            search_results["_bm25_score"] > 0.3
                        ].copy()
                        
                        if not relevant_results.empty:
                            candidates = relevant_results.head(3)
                            products_list = candidates.to_dict("records")
                            
                            if products_list:
                                relevance_scores = check_products_relevance(
                                    product_name, products_list
                                )
                                
                                filtered_products = []
                                for idx, score in enumerate(relevance_scores):
                                    if idx < len(products_list) and score == 1:
                                        filtered_products.append(products_list[idx])
                                
                                if filtered_products:
                                    filtered_df = pd.DataFrame(filtered_products)
                                    products_by_product[product_name] = filtered_df
            
            # Формируем ответ
            response_parts = [llm_answer_text]
            
            if products_by_product:
                for product_name, product_results in products_by_product.items():
                    if not product_results.empty:
                        response_parts.append(_widget_category_line(product_name))
                        for _, r in product_results.iterrows():
                            response_parts.append(_format_product_card(r))
            
            return "\n".join(response_parts)
    
    # Problem solving flow
    # Проверяем, нужно ли задать уточняющий вопрос
    clarification_count = sum(
        1
        for m in messages
        if m.get("role") == "assistant" and "?" in m.get("content", "")
    )
    
    clarification_question = None
    if not is_follow_up_answer and clarification_count < MAX_CLARIFICATION_QUESTIONS:
        clarification_question = should_ask_clarification(last_user, messages)
    
    if clarification_question:
        return clarification_question
    
    # Определяем необходимые товары для задачи
    required_products_result = get_required_products_for_task(last_user)
    
    if isinstance(required_products_result, dict):
        required_products_text = required_products_result.get("text", "")
        required_products = required_products_result.get("products", [])
    else:
        required_products = (
            required_products_result if isinstance(required_products_result, list) else []
        )
        required_products_text = ""
    
    # Проверяем, относится ли задача к строительным товарам
    if not required_products or len(required_products) == 0:
        test_search = _retriever.search(last_user, top_k=5)
        if test_search.empty or test_search["_bm25_score"].max() < 0.3:
            return "Извините, но ваша задача не относится к ассортименту нашего магазина."
        else:
            return (
                "Извините, произошла ошибка при определении необходимых товаров. "
                "Пожалуйста, попробуйте переформулировать ваш запрос более конкретно."
            )
    
    # Ищем товары по каждому названию
    products_by_name = {}
    for product_info in required_products:
        if isinstance(product_info, dict):
            product_name = product_info.get("name", "")
        else:
            product_name = str(product_info)
        
        if not product_name:
            continue
        
        search_results = _retriever.search(product_name, top_k=10)
        
        if not search_results.empty:
            relevant_results = search_results[
                search_results["_bm25_score"] > 0.3
            ].copy()
            
            if not relevant_results.empty:
                products_by_name[product_name] = relevant_results.head(3)
    
    # Формируем ответ
    response_parts = []
    
    if required_products_text and required_products_text.strip():
        response_parts.append(required_products_text)
    else:
        product_list_items = []
        for product_info in required_products:
            if isinstance(product_info, dict):
                product_name = product_info.get("name", "")
            else:
                product_name = str(product_info)
            
            if product_name:
                product_list_items.append(f"• **{product_name}**")
        
        if product_list_items:
            list_text = (
                "Для выполнения задачи вам понадобятся следующие товары:\n\n"
                + "\n".join(product_list_items)
            )
            response_parts.append(list_text)
    
    # Показываем найденные товары
    # Сначала фильтруем товары по релевантности
    filtered_products_by_name = {}
    if products_by_name:
        for product_name, product_results in products_by_name.items():
            if not product_results.empty:
                products_list = product_results.to_dict("records")
                
                if products_list:
                    relevance_scores = check_products_relevance(
                        product_name, products_list
                    )
                    
                    filtered_products = []
                    for idx, score in enumerate(relevance_scores):
                        if idx < len(products_list) and score == 1:
                            filtered_products.append(products_list[idx])
                    
                    if filtered_products:
                        filtered_products_by_name[product_name] = filtered_products
    
    if filtered_products_by_name:
        for product_name, filtered_products in filtered_products_by_name.items():
            response_parts.append(_widget_category_line(product_name))
            for r in filtered_products:
                response_parts.append(_format_product_card(pd.Series(r)))
    
    if not response_parts:
        # Fallback: обычный поиск
        cands = _retriever.search(last_user, top_k=TOP_K_CANDIDATES)
        
        if not cands.empty and "_bm25_score" in cands.columns:
            cands = cands.sort_values("_bm25_score", ascending=False)
        
        final = cands.head(FINAL_K)
        
        if final.empty:
            return "Пока не нашел подходящих результатов. Попробуйте изменить запрос."
        
        response_parts.append("Вот что могу предложить:")
        for _, r in final.iterrows():
            response_parts.append(_format_product_card(r))
    
    return "\n".join(response_parts)


def _save_network_error_to_log(error_type: str) -> None:
    """Фиксирует сетевую ошибку в SQLite для отображения в Streamlit-панели."""
    try:
        logs_db.add_network_error(error_type)
    except Exception:
        pass


def process_chat_request(
    message: str,
    conversation_history: Optional[List[Dict[str, str]]] = None,
) -> Dict[str, Any]:
    """
    Обрабатывает запрос чата и возвращает ответ в формате для виджета.

    Args:
        message: Текст сообщения пользователя
        conversation_history: История диалога

    Returns:
        Словарь с полем "reply" содержащим текст ответа
    """
    global _current_user_message, _current_user_request_id
    
    try:
        # Проверяем, является ли это ответом на уточняющий вопрос
        is_follow_up = False
        if conversation_history:
            for msg in reversed(conversation_history[-3:]):
                if msg.get("role") == "assistant" and "?" in msg.get("content", ""):
                    is_follow_up = True
                    break
        
        # Устанавливаем текущее сообщение пользователя для группировки
        _current_user_message = message

        # Если это ответ на уточняющий вопрос, используем ID последней группы
        if is_follow_up:
            all_urs = logs_db.get_all_user_requests()
            if all_urs:
                last_ur = all_urs[0]  # новые первыми
                _current_user_request_id = last_ur["id"]
                _current_user_message = last_ur.get("user_message", message)
            else:
                _current_user_request_id = None
        else:
            _current_user_request_id = None
        
        # Убеждаемся, что callback установлены при каждом запросе
        set_llm_counter_callback(_increment_llm_counter)
        set_llm_response_callback(_update_llm_response)
        
        if conversation_history is None:
            conversation_history = []
        
        reply = _process_message(message, conversation_history)
        
        # Сбрасываем текущее сообщение после обработки
        _current_user_message = None
        _current_user_request_id = None

        return {"reply": reply}
    except Exception as e:
        _current_user_message = None
        _current_user_request_id = None
        if is_network_error(e):
            log_network_error(e, context="process_chat_request")
            _save_network_error_to_log(type(e).__name__)
            return {"reply": NETWORK_ERROR_REPLY}
        logger.exception("Ошибка при обработке сообщения: %s", type(e).__name__)
        return {"reply": f"Ошибка при обработке сообщения: {str(e)}"}


# Устанавливаем callback функции для логирования в конце файла после определения всех функций
try:
    set_llm_counter_callback(_increment_llm_counter)
    set_llm_response_callback(_update_llm_response)
except Exception as e:
    pass  # Ошибки инициализации не критичны для работы виджета
