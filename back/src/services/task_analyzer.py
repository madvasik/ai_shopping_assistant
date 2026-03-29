# -*- coding: utf-8 -*-
"""
Модуль для определения необходимых товаров для задач
"""
from typing import List, Dict, Optional, Any
import os, json
import re
from .llm_counter import increment_llm_counter, update_llm_response

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
    
    sys_prompt = (
        "Ты помощник российского онлайн магазина строительных товаров. "
        "Определи, какие товары нужны для выполнения задачи пользователя и создай связный текст с их описанием.\n\n"
        "КРИТИЧЕСКИ ВАЖНО: Указывай ТОЛЬКО товары, которые продаются в строительных магазинах. "
        "Строительный магазин обычно продает товары для строительства, ремонта, отделки, садоводства и дачи.\n"
        "Это могут быть материалы (краски, обои, клей, плитка, утеплители, цемент, кирпич и т.д.), "
        "инструменты (для ремонта, садовые и т.д.), крепеж (гвозди, саморезы, дюбели, анкеры и т.д.), "
        "сантехника, электрика, товары для садоводства (удобрения, семена, садовый инвентарь и т.д.).\n\n"
        "НЕ указывай товары, которые НЕ продаются в строительных магазинах:\n"
        "- Бытовая техника (стиральные машины, холодильники, пылесосы и т.д.)\n"
        "- Электроника (телевизоры, компьютеры, смартфоны и т.д.)\n"
        "- Мебель (диваны, столы, стулья и т.д.)\n"
        "- Одежда и обувь\n"
        "- Продукты питания\n"
        "- Хозяйственные товары общего назначения (полотенца, посуда и т.д.), если они не относятся к строительству\n\n"
        "Если задача требует товаров, которые обычно НЕ продаются в строительных магазинах, верни пустой объект {}.\n\n"
        "Верни ТОЛЬКО JSON объект с полями 'text' и 'products'.\n"
        "- 'text': связный текст в виде параграфов, где товары естественно вписаны в предложения. "
        "Товары должны быть выделены жирным шрифтом (**название**). "
        "Группируй товары логически по категориям (материалы, инструменты для нанесения, измерительные инструменты и т.д.). "
        "Каждый параграф должен быть отделен пустой строкой.\n"
        "- 'products': массив объектов с полем 'name' для поиска товаров в базе. "
        "Каждый объект должен содержать только поле 'name' с названием товара.\n\n"
        "КРИТИЧЕСКИ ВАЖНО: Используй ОБЩИЕ названия товаров БЕЗ указания конкретных типов, если пользователь не уточнил тип.\n"
        "НО если пользователь указал критически важную информацию (например, тип поверхности: бетон, гипсокартон, дерево), "
        "используй более специфичные названия, которые отражают эту информацию.\n"
        "Например:\n"
        "- 'хочу повесить шторы' + 'бетон' → 'дюбели для бетона' (не просто 'дюбели')\n"
        "- 'хочу поклеить обои' + 'старые обои' → 'клей для обоев' (который подходит для поклейки поверх старых)\n"
        "- 'хочу покрасить стены' + 'дерево' → 'краска для дерева' (не просто 'краска')\n"
        "НЕ перечисляй все возможные типы товара - используй только то, что соответствует уточнению пользователя.\n\n"
        "Пример ответа для \"хочу поклеить обои\":\n"
        "{\n"
        "  \"text\": \"Для поклейки обоев вам понадобятся следующие материалы и инструменты.\\n\\nВ первую очередь нужны **обои** для покрытия стен и **клей для обоев** для приклеивания их к поверхности. Перед началом работ потребуется **грунтовка** для подготовки поверхности стен перед поклейкой.\\n\\nДля нанесения клея используйте **валик** для равномерного распределения на больших участках и **кисть** для работы в углах и труднодоступных местах.\\n\\nПри поклейке пригодятся **шпатель** для разглаживания обоев и удаления пузырьков воздуха, а также **нож для обоев** для обрезки лишних краев.\\n\\nДля точной работы понадобятся **уровень или отвес** для проверки вертикальности при поклейке обоев и **рулетка** для измерения размеров стен и обоев.\\n\\nТакже стоит иметь под рукой **ветошь или губку** для удаления излишков клея с поверхности обоев.\",\n"
        "  \"products\": [{\"name\": \"обои\"}, {\"name\": \"клей для обоев\"}, {\"name\": \"грунтовка\"}, {\"name\": \"валик\"}, {\"name\": \"кисть\"}, {\"name\": \"шпатель\"}, {\"name\": \"нож для обоев\"}, {\"name\": \"уровень\"}, {\"name\": \"отвес\"}, {\"name\": \"рулетка\"}, {\"name\": \"ветошь\"}]\n"
        "}\n\n"
        "Если задача не требует товаров из нашего каталога строительных товаров, верни пустой объект {}."
    )
    
    user_prompt = f"Задача пользователя: {task_description}\n\nСоздай связный текст с описанием необходимых товаров и список товаров для поиска."
    model = os.getenv("OPENAI_MODEL", "gpt-4o-mini")
    
    try:
        # Передаем полный промпт в лог (включая system и user сообщения)
        full_prompt = f"System: {sys_prompt}\n\nUser: {user_prompt}"
        increment_llm_counter("get_required_products_for_task", full_prompt)  # Увеличиваем счетчик запросов к LLM
        resp = client.chat.completions.create(
            model=model,
            max_tokens=2000,
            temperature=0.6,
            top_p=0.95,
            messages=[
                {"role": "system", "content": sys_prompt},
                {"role": "user", "content": user_prompt},
            ],
        )
        text = (resp.choices[0].message.content or "").strip()
        update_llm_response(text,
            prompt_tokens=resp.usage.prompt_tokens if resp.usage else None,
            completion_tokens=resp.usage.completion_tokens if resp.usage else None)

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
        # Логируем общую ошибку
        print(f"[DEBUG] Exception in get_required_products_for_task: {e}")
        pass
    
    # Значение по умолчанию - возвращаем пустой объект
    return {"text": "", "products": []}


def should_ask_clarification(task_description: str, conversation_history: List[Dict[str, str]]) -> Optional[str]:
    """
    Определяет, нужно ли задать уточняющий вопрос перед показом товаров.
    Возвращает вопрос для уточнения или None, если уточнения не нужны.
    """
    client, err = _get_openai_client()
    if err or client is None:
        # Fallback: если LLM недоступен, не задаем вопросы
        return None
    
    # Формируем историю диалога для контекста
    history_text = ""
    if conversation_history:
        for msg in conversation_history[-3:]:  # Последние 3 сообщения
            role = "Пользователь" if msg.get("role") == "user" else "Ассистент"
            content = msg.get("content", "")
            history_text += f"{role}: {content}\n"
    
    sys_prompt = (
        "Ты помощник российского онлайн магазина строительных товаров. "
        "Определи, нужно ли задать уточняющий вопрос пользователю перед подбором товаров.\n\n"
        "ВАЖНО: Определи сам, относятся ли товары, необходимые для задачи пользователя, к ассортименту строительного магазина. "
        "Строительный магазин обычно продает товары для строительства, ремонта, отделки, садоводства и дачи.\n"
        "НЕ задавай вопросы, если задача требует товаров, которые обычно НЕ продаются в строительных магазинах "
        "(например, бытовая техника, электроника, мебель и т.д.).\n\n"
        "КРИТИЧЕСКИ ВАЖНО: Задавай вопросы ТОЛЬКО для критически важных моментов выбора товара, "
        "где неправильный выбор может привести к проблемам, несовместимости или неработоспособности товара.\n\n"
        "Принципы определения критически важных вопросов:\n"
        "- Вопрос должен касаться параметров, которые определяют совместимость товара с задачей\n"
        "- Неправильный ответ на вопрос может привести к покупке неподходящего товара\n"
        "- Вопрос должен быть конкретным и иметь четкий ответ, который повлияет на выбор\n"
        "- Если товар может использоваться для разных материалов/поверхностей/условий, и это критично - нужно уточнить\n\n"
        "НЕ задавай вопросы про:\n"
        "- Количество или площадь (не влияет на выбор типа товара)\n"
        "- Цвет или дизайн (не влияет на совместимость)\n"
        "- Бренд или марку (не критично для выбора)\n"
        "- Технические детали, которые не критичны для совместимости\n"
        "- Общие вопросы, которые не влияют на правильность выбора товара\n\n"
        "Задавай уточняющие вопросы ТОЛЬКО если:\n"
        "- Это критически важно для правильного выбора товара\n"
        "- Можно задать один конкретный вопрос, который определит критический параметр\n"
        "- Задача требует товаров, которые обычно продаются в строительных магазинах\n"
        "- Пользователь еще не предоставил эту информацию в запросе или истории диалога\n\n"
        "НЕ задавай вопросы, если:\n"
        "- Запрос уже содержит всю критически важную информацию\n"
        "- Пользователь уже ответил на уточняющие вопросы в истории диалога\n"
        "- Вопрос не относится к критически важным параметрам выбора\n"
        "- Задача требует товаров, которые обычно НЕ продаются в строительных магазинах\n\n"
        "Верни ТОЛЬКО уточняющий вопрос (если нужен) или слово 'НЕТ' (если вопрос не нужен). "
        "Вопрос должен быть простым, понятным и фокусироваться на критически важном параметре выбора."
    )
    
    user_prompt = (
        f"История диалога:\n{history_text}\n\n"
        f"Последний запрос пользователя: {task_description}\n\n"
        "Нужно ли задать уточняющий вопрос перед подбором товаров?"
    )
    model = os.getenv("OPENAI_MODEL", "gpt-4o-mini")
    
    try:
        # Передаем полный промпт в лог (включая system и user сообщения)
        full_prompt = f"System: {sys_prompt}\n\nUser: {user_prompt}"
        increment_llm_counter("should_ask_clarification", full_prompt)  # Увеличиваем счетчик запросов к LLM
        resp = client.chat.completions.create(
            model=model,
            max_tokens=150,
            temperature=0.6,
            top_p=0.95,
            messages=[
                {"role": "system", "content": sys_prompt},
                {"role": "user", "content": user_prompt},
            ],
        )
        text = (resp.choices[0].message.content or "").strip()
        update_llm_response(text,
            prompt_tokens=resp.usage.prompt_tokens if resp.usage else None,
            completion_tokens=resp.usage.completion_tokens if resp.usage else None)

        # Если ответ содержит "НЕТ" или похожее, возвращаем None
        if any(word in text.upper() for word in ["НЕТ", "НЕ НУЖЕН", "НЕ НУЖНО", "НЕ ТРЕБУЕТСЯ"]):
            return None
        
        # Если это вопрос (содержит знак вопроса), возвращаем его
        if "?" in text and len(text) > 10:
            return text
        
        return None
    except Exception:
        return None
