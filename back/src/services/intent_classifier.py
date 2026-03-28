# -*- coding: utf-8 -*-
import os, json
from typing import Dict, Any, List, Optional, TYPE_CHECKING
from pydantic import BaseModel, Field
from .llm_counter import increment_llm_counter, update_llm_response

if TYPE_CHECKING:
    import pandas as pd

def _get_mistral_client():
    """
    Возвращает (client, err). Использует нативный клиент Mistral API.
    """
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

class Slots(BaseModel):
    """Упрощенные слоты - только бюджет"""
    budget_min: Optional[float] = None
    budget_max: Optional[float] = None
    language: str = "ru"

    def merge(self, other: "Slots"):
        """Объединяет слоты из другого объекта"""
        for k, v in other.model_dump().items():
            if v in (None, [], ""):
                continue
            setattr(self, k, v)


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
    client, err = _get_mistral_client()
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
    
    sys_prompt = (
        "Ты классификатор интентов для российского онлайн магазина строительных товаров. Классифицируй сообщение пользователя в одну из двух категорий:\n"
        "- 'task': Пользователь хочет найти/подобрать товары для конкретной задачи (например, 'Нужна краска', 'Товары до 70к', 'Ищу обои', 'Нужны ли крепления для установки стиральной машины?', 'Хочу поклеить обои')\n"
        "- 'consultation': Пользователь задает вопросы, сравнения или просит совета о товарах (например, 'Какая краска лучше?', 'Что лучше кисточка или валик?', 'Сколько клея на рулон?', 'Что лучше для стен?', 'Чем лучше резать обои? ножом или ножницами?')\n\n"
        "КРИТИЧЕСКИ ВАЖНО:\n"
        "- Вопросы сравнения ('что лучше X или Y?', 'чем лучше X? Y или Z?') - ВСЕГДА 'consultation'\n"
        "- Вопросы типа 'какая X лучше?' - ВСЕГДА 'consultation'\n"
        "- Вопросы типа 'Нужны ли X?' в контексте задачи (например, после 'хочу установить стиральную машину') - это 'task', а не 'consultation'\n"
        "- Если пользователь спрашивает 'что лучше' или 'чем лучше' - это ВСЕГДА 'consultation'\n\n"
        "Верни ТОЛЬКО слово 'task' или 'consultation', ничего больше."
    )
    
    user_prompt = f"Контекст диалога:\n{context_text}\n\nКлассифицируй интент последнего сообщения пользователя:"
    model = os.getenv("MISTRAL_MODEL", "mistral-medium-latest")
    
    # Формируем полный запрос для логирования
    full_prompt = f"System: {sys_prompt}\n\nUser: {user_prompt}"
    
    try:
        increment_llm_counter("classify_intent", full_prompt)  # Увеличиваем счетчик запросов к LLM
        resp = client.chat.complete(
            model=model,
            max_tokens=10,
            temperature=temperature,
            top_p=top_p,
            messages=[
                {"role": "system", "content": sys_prompt},
                {"role": "user", "content": user_prompt},
            ],
        )
        text = (resp.choices[0].message.content or "").strip().lower()
        # Обновляем ответ в логе
        update_llm_response(text)
        if "consultation" in text:
            return "consultation"
        elif "task" in text:
            return "task"
        else:
            # Значение по умолчанию при неопределенном ответе
            return "task"
    except Exception:
        # Значение по умолчанию при ошибке
        return "task"

def is_catalog_related(query: str, retriever: Any, threshold: float = 0.1) -> bool:
    """
    Проверяет, относится ли вопрос к каталогу, используя только LLM.
    Возвращает True, если вопрос релевантен каталогу строительного магазина.
    """
    if not query:
        return False
    
    # Используем LLM для проверки релевантности
    client, err = _get_mistral_client()
    if err or client is None:
        # Если LLM недоступен, возвращаем False по умолчанию
        return False
    
    sys_prompt = (
        "Ты помощник российского онлайн магазина строительных товаров. Определи, относится ли вопрос пользователя "
        "к тематике строительного магазина.\n\n"
        "Строительный магазин обычно продает товары для строительства, ремонта, отделки, садоводства и дачи. "
        "Это могут быть материалы (краски, обои, клей, плитка, утеплители и т.д.), инструменты (для ремонта, садовые и т.д.), "
        "а также товары для садоводства и дачи (удобрения, семена, садовый инвентарь и т.д.).\n\n"
        "Определи сам, продаются ли товары, относящиеся к вопросу пользователя, в строительном магазине. "
        "Если вопрос касается товаров, которые обычно продаются в строительных магазинах - ответь 'да'. "
        "Если вопрос касается товаров, которые НЕ продаются в строительных магазинах (например, бытовая техника, "
        "электроника, мебель, еда, программирование и т.д.) - ответь 'нет'.\n\n"
        "Ответь ТОЛЬКО 'да' или 'нет', без объяснений."
    )
    
    user_prompt = f"Вопрос пользователя: {query}\n\nОтносится ли этот вопрос к тематике строительного онлайн магазина?"
    model = os.getenv("MISTRAL_MODEL", "mistral-medium-latest")
    
    # Формируем полный запрос для логирования
    full_prompt = f"System: {sys_prompt}\n\nUser: {user_prompt}"
    
    try:
        increment_llm_counter("is_catalog_related", full_prompt)  # Увеличиваем счетчик запросов к LLM
        resp = client.chat.complete(
            model=model,
            max_tokens=10,
            temperature=0.2,
            top_p=0.95,
            messages=[
                {"role": "system", "content": sys_prompt},
                {"role": "user", "content": user_prompt},
            ],
        )
        text = (resp.choices[0].message.content or "").strip().lower()
        # Обновляем ответ в логе
        update_llm_response(text)
        return "да" in text
    except Exception:
        # Значение по умолчанию при ошибке
        return False

def find_product_in_catalog(query: str, retriever: Any, threshold: float = 0.3) -> Optional[Any]:
    """
    Находит конкретный товар в каталоге.
    Возвращает DataFrame с найденными товарами, если сходство > threshold, иначе None.
    """
    if not query or not retriever:
        return None
    try:
        results = retriever.search(query, top_k=5)
        if results.empty:
            return None
        top_item = results.iloc[0]
        # Используем BM25 score вместо _sem_sim
        if top_item["_bm25_score"] > threshold:
            return results.head(1)
        return None
    except Exception:
        return None

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
    
    client, err = _get_mistral_client()
    if err or client is None:
        return []
    
    sys_prompt = (
        "Ты помощник российского онлайн магазина строительных товаров. "
        "Твоя задача - извлечь из запроса пользователя ВСЕ наименования товаров (типы товаров, инструменты, материалы), которые упоминаются.\n\n"
        "Примеры товаров, которые могут упоминаться:\n"
        "- кисть, кисти, кисточка, кисточки → 'кисти'\n"
        "- валик, валики → 'валики'\n"
        "- обои → 'обои'\n"
        "- краска → 'краска'\n"
        "- клей → 'клей'\n"
        "- нож, ножницы → 'нож', 'ножницы'\n"
        "- грунтовка → 'грунтовка'\n"
        "- шпатель → 'шпатель'\n"
        "- и другие строительные товары и инструменты\n\n"
        "ВАЖНО:\n"
        "- Извлекай ВСЕ товары и инструменты, которые упоминаются в запросе\n"
        "- Если пользователь спрашивает 'чем лучше резать обои? ножом или ножницами?', извлеки: ['обои', 'нож', 'ножницы']\n"
        "- Если пользователь спрашивает 'что лучше кисточка или валик', извлеки: ['кисти', 'валики']\n"
        "- Если пользователь спрашивает 'нужна краска', извлеки: ['краска']\n"
        "- Если пользователь спрашивает 'как поклеить обои', извлеки: ['обои']\n"
        "- НЕ извлекай действия (покрасить, поклеить, резать), материалы (стена, потолок), характеристики (цена, размер)\n"
        "- Возвращай наименования в нормализованном виде (единственное число для инструментов: 'нож', 'ножницы', 'кисть')\n"
        "- Если товар упоминается в разных формах (кисть/кисти/кисточка), верни нормализованное наименование ('кисти')\n\n"
        "Верни ТОЛЬКО JSON массив строк с наименованиями товаров в нижнем регистре, без объяснений, без markdown.\n"
        "Пример: [\"кисти\", \"валики\"] или [\"краска\"] или [\"обои\", \"нож\", \"ножницы\"] или [] если товары не упоминаются."
    )
    
    user_prompt = f"Запрос пользователя: {query}\n\nИзвлеки все наименования товаров и инструментов из этого запроса:"
    model = os.getenv("MISTRAL_MODEL", "mistral-medium-latest")
    
    # Формируем полный запрос для логирования
    full_prompt = f"System: {sys_prompt}\n\nUser: {user_prompt}"
    
    try:
        increment_llm_counter("extract_product_names_from_query", full_prompt)  # Увеличиваем счетчик запросов к LLM
        resp = client.chat.complete(
            model=model,
            max_tokens=200,
            temperature=temperature,
            top_p=top_p,
            messages=[
                {"role": "system", "content": sys_prompt},
                {"role": "user", "content": user_prompt},
            ],
        )
        text = (resp.choices[0].message.content or "").strip()
        
        # Очищаем markdown код блоки если есть
        if text.startswith("```"):
            text = text.strip("`")
            if text.startswith("json"):
                text = text[4:]
            text = text.strip()
        
        # Обновляем ответ в логе
        update_llm_response(text)
        
        # Извлекаем JSON массив
        if text.startswith("["):
            product_names = json.loads(text)
            if isinstance(product_names, list):
                # Возвращаем все извлеченные товары без фильтрации
                return [name.lower().strip() for name in product_names if isinstance(name, str)]
        
        return []
    except Exception as e:
        # При ошибке возвращаем пустой список
        return []

def nlu_with_llm(messages: List[Dict[str,str]], current_slots: Slots,
                 mode: str = "assist",
                 temperature: float = 0.2,
                 top_p: float = 0.95,
                 seed: int = 42) -> Slots:
    """
    Извлекает слоты используя только LLM, без эвристики.
    mode: off | assist | dominant (в настоящее время не используется, всегда использует LLM)
    """
    if mode == "off":
        # Если режим выключен, возвращаем текущие слоты без изменений
        return current_slots or Slots()

    # Извлечение через Mistral API
    client, err = _get_mistral_client()
    if err or client is None:
        # Если LLM недоступен, возвращаем текущие слоты
        return current_slots or Slots()

    sys_prompt = (
        "Ты NLU ассистент для российского онлайн магазина. Верни ТОЛЬКО валидный JSON по схеме ниже, "
        "без объяснений, без markdown.\n"
        "Схема:\n"
        "{\n"
        '  "budget_min": float|null,\n'
        '  "budget_max": float|null,\n'
        '  "language": "ru"\n'
        "}\n"
        "Правила: Извлекай только то, что указал пользователь. "
        "Бюджет в рублях (RUB). '70к' или '70 тыс' => 70000.\n"
    )

    user_prompt = json.dumps(
        {
            "last_messages": messages[-6:],
            "current_slots": current_slots.model_dump() if current_slots else {}
        },
        ensure_ascii=False
    )

    model = os.getenv("MISTRAL_MODEL", "mistral-medium-latest")

    try:
        resp = client.chat.complete(
            model=model,
            max_tokens=200,
            temperature=temperature,
            top_p=top_p,
            messages=[
                {"role":"system", "content": sys_prompt},
                {"role":"user", "content": user_prompt},
            ],
        )
        text = (resp.choices[0].message.content or "").strip()
        if text.startswith("```"):
            text = text.strip("`")
            text = text[text.find("{") : text.rfind("}")+1]
        data = json.loads(text)
        s_llm = Slots(**data)
    except Exception:
        s_llm = Slots()

    s = current_slots or Slots()
    s.merge(s_llm)
    return s

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
    
    client, err = _get_mistral_client()
    if err or client is None:
        # Если LLM недоступен, возвращаем все как релевантные (fallback)
        return [1] * len(products_to_check)
    
    # Формируем описание товаров для промпта
    products_text = []
    valid_indices = []
    for idx, product in enumerate(products_to_check):
        title = str(product.get("title", "")).strip()
        if not title or title == "nan":
            # Товары без названия считаем нерелевантными (вернем 0)
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
    
    # Если нет валидных товаров, возвращаем все нули
    if not valid_indices:
        return [0] * len(products_to_check)
    
    products_list = "\n".join(products_text)
    
    sys_prompt = (
        "Ты помощник российского онлайн магазина строительных товаров. "
        "Определи, соответствуют ли товары указанной категории карусели.\n\n"
        "Для каждого товара верни 1, если товар соответствует категории карусели, или 0, если не соответствует.\n"
        "Товар считается соответствующим категории, если он ТОЧНО относится к этому типу товаров.\n"
        "Например:\n"
        "- Для категории 'Леса строительные' подходят только строительные леса (рамные, клиновые, вышки-туры), "
        "НЕ подходят гвозди, перчатки, степлеры и другие товары, даже если они 'строительные'.\n"
        "- Для категории 'Тротуарная плитка' подходит только тротуарная плитка для мощения, "
        "НЕ подходит керамическая плитка для внутренней отделки.\n\n"
        "Будь строгим: товар должен точно соответствовать категории, а не просто быть связанным со строительством.\n\n"
        "Верни ТОЛЬКО строку из 0 и 1 (например, '011' для трех товаров, где первый не соответствует категории, а второй и третий соответствуют), "
        "без объяснений, без пробелов, без других символов."
    )
    
    user_prompt = (
        f"Категория карусели: {category_name}\n\n"
        f"Товары для проверки:\n{products_list}\n\n"
        f"Верни строку из 0 и 1 для каждого товара (1 - соответствует категории '{category_name}', 0 - не соответствует):"
    )
    
    model = os.getenv("MISTRAL_MODEL", "mistral-medium-latest")
    
    try:
        # Формируем полный запрос для логирования
        full_prompt = f"System: {sys_prompt}\n\nUser: {user_prompt}"
        increment_llm_counter("check_products_relevance", full_prompt)
        resp = client.chat.complete(
            model=model,
            max_tokens=10,
            temperature=temperature,
            top_p=top_p,
            messages=[
                {"role": "system", "content": sys_prompt},
                {"role": "user", "content": user_prompt},
            ],
        )
        text = (resp.choices[0].message.content or "").strip()
        # Обновляем ответ в логе
        update_llm_response(text)
        
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
        # При ошибке возвращаем все как релевантные (fallback)
        print(f"[DEBUG] Error in check_products_relevance: {e}")
        return [1] * len(products_to_check)
