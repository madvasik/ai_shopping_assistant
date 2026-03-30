# -*- coding: utf-8 -*-
import os, json, re
from typing import Dict, Any, List, Optional
from .llm_counter import increment_llm_counter, update_llm_response, extract_usage_tokens

# Монтаж на стену/потолок + типичные предметы (строймаг продаёт крепёж, карнизы, инструмент).
_RE_CATALOG_MOUNT_VERB = re.compile(
    r"повесить|повесь|веша(?:ть|ю|ем|ете|т)|весить|установ|прикреп|смонтиров|навесить|закреп"
    r"|креплен|монтаж|просверл|забур|дюбел",
    re.IGNORECASE,
)
_RE_CATALOG_MOUNT_OBJECT = re.compile(
    r"штор|карниз|гардин|жалюзи|тюль|полк|зеркал|картин|люстр|светильник|кронштейн",
    re.IGNORECASE,
)


def _heuristic_catalog_related(query: str) -> bool:
    if not query or len(query.strip()) < 2:
        return False
    return bool(
        _RE_CATALOG_MOUNT_VERB.search(query) and _RE_CATALOG_MOUNT_OBJECT.search(query)
    )


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
    model = os.getenv("OPENAI_MODEL", "gpt-4o-mini")
    
    # Формируем полный запрос для логирования
    full_prompt = f"System: {sys_prompt}\n\nUser: {user_prompt}"
    
    try:
        increment_llm_counter("classify_intent", full_prompt)  # Увеличиваем счетчик запросов к LLM
        resp = client.chat.completions.create(
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
        pt, ct = extract_usage_tokens(resp)
        update_llm_response(text, prompt_tokens=pt, completion_tokens=ct)
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

def is_catalog_related(query: str) -> bool:
    """
    Проверяет, относится ли вопрос к каталогу строительного магазина.
    Сначала эвристика (устойчиво к ошибкам LLM на типичных запросах про монтаж),
    затем при необходимости — LLM.
    """
    if not query:
        return False

    if _heuristic_catalog_related(query):
        return True

    # Используем LLM для проверки релевантности
    client, err = _get_openai_client()
    if err or client is None:
        # Если LLM недоступен, возвращаем False по умолчанию
        return False
    
    sys_prompt = (
        "Ты помощник российского онлайн магазина строительных товаров. Определи, относится ли вопрос пользователя "
        "к тематике строительного магазина.\n\n"
        "Строительный магазин обычно продает товары для строительства, ремонта, отделки, садоводства и дачи, а также электрику и электромонтаж: "
        "кабель, автоматы и УЗО, электрощиты, розетки и выключатели, светильники и лампы для монтажа, крепёж и инструмент для электрики и т.п. "
        "Это могут быть материалы (краски, обои, клей, плитка, утеплители и т.д.), инструменты (для ремонта, садовые и т.д.), "
        "а также товары для садоводства и дачи (удобрения, семена, садовый инвентарь и т.д.).\n\n"
        "ВАЖНО про монтаж и «как сделать»:\n"
        "- Вопросы «как повесить/установить/прикрепить» что-либо к стене, потолку или оконному проёму (шторы, карниз, полка, светильник, полотенцесушитель и т.п.) "
        "считай относящимися к магазину: для этого обычно нужны карнизы и комплектующие, кронштейны, дюбели, саморезы, анкеры, перфоратор или дрель, бур, отвёртка, уровень — всё это продаётся в строительном магазине.\n"
        "- Сам предмет (например ткань штор) может не продаваться, но вопрос про крепление и инструменты для работы — всё равно «да».\n\n"
        "Определи сам, есть ли связь с товарами или работами, типичными для строительного магазина. "
        "Если да — ответь строго слово 'да'. "
        "Если вопрос явно про то, что не связано с ремонтом, стройкой, монтажом и таким ассортиментом (еда, медицина, программирование, "
        "подбор готовой мебели без монтажа, развлечения и т.д.) — ответь строго слово 'нет'.\n\n"
        "Ответь ТОЛЬКО 'да' или 'нет', без объяснений."
    )
    
    user_prompt = (
        f"Вопрос пользователя: {query}\n\n"
        "Относится ли этот вопрос к тематике строительного онлайн магазина? "
        "Учитывай монтаж, крепёж и инструменты, как в инструкции выше."
    )
    model = os.getenv("OPENAI_MODEL", "gpt-4o-mini")
    
    # Формируем полный запрос для логирования
    full_prompt = f"System: {sys_prompt}\n\nUser: {user_prompt}"
    
    try:
        increment_llm_counter("is_catalog_related", full_prompt)  # Увеличиваем счетчик запросов к LLM
        resp = client.chat.completions.create(
            model=model,
            max_tokens=10,
            temperature=0.2,
            top_p=0.95,
            messages=[
                {"role": "system", "content": sys_prompt},
                {"role": "user", "content": user_prompt},
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
    except Exception:
        # Значение по умолчанию при ошибке
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
    model = os.getenv("OPENAI_MODEL", "gpt-4o-mini")
    
    # Формируем полный запрос для логирования
    full_prompt = f"System: {sys_prompt}\n\nUser: {user_prompt}"
    
    try:
        increment_llm_counter("extract_product_names_from_query", full_prompt)  # Увеличиваем счетчик запросов к LLM
        resp = client.chat.completions.create(
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
        # При ошибке возвращаем пустой список
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
    
    model = os.getenv("OPENAI_MODEL", "gpt-4o-mini")
    
    try:
        # Формируем полный запрос для логирования
        full_prompt = f"System: {sys_prompt}\n\nUser: {user_prompt}"
        increment_llm_counter("check_products_relevance", full_prompt)
        resp = client.chat.completions.create(
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
        # При ошибке возвращаем все как релевантные (fallback)
        print(f"[DEBUG] Error in check_products_relevance: {e}")
        return [1] * len(products_to_check)
