import os, sys, time, json
from pathlib import Path
import streamlit as st
from dotenv import load_dotenv

# Добавляем корневую директорию проекта в путь для импортов
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from src.services.llm_counter import set_llm_counter_callback, set_llm_response_callback

# Путь к файлу логов (общий для виджета и Streamlit)
# Используем абсолютный путь /app/llm_logs.json для совместимости с Docker
LOGS_FILE = Path("/app/llm_logs.json")

# Загружаем .env из корня проекта (на 2 уровня выше от текущего файла)
env_path = Path(__file__).parent.parent.parent.parent / ".env"
load_dotenv(env_path)

st.set_page_config(page_title="LLM Logs Panel", page_icon="📊", layout="wide")

# Загрузка CSS стилей
css_path = Path(__file__).parent / "assets" / "style.css"
if css_path.exists():
    st.markdown('<style>' + css_path.read_text(encoding='utf-8') + '</style>', unsafe_allow_html=True)

# Добавляем версию в левый верхний угол основного контента (fixed)
st.markdown("""
    <style>
        .version-badge-fixed {
            position: fixed !important;
            top: 10px !important;
            left: 10px !important;
            background: rgba(37, 99, 235, 0.95) !important;
            color: #ffffff !important;
            padding: 8px 14px !important;
            border-radius: 6px !important;
            font-size: 13px !important;
            font-weight: 700 !important;
            z-index: 999999 !important;
            border: 2px solid rgba(255, 255, 255, 0.3) !important;
            box-shadow: 0 2px 8px rgba(0, 0, 0, 0.3) !important;
            font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif !important;
            text-shadow: 0 1px 2px rgba(0, 0, 0, 0.2) !important;
        }
        @media (prefers-color-scheme: dark) {
            .version-badge-fixed {
                background: rgba(79, 124, 255, 0.95) !important;
                color: #ffffff !important;
                border-color: rgba(255, 255, 255, 0.4) !important;
            }
        }
        /* Убеждаемся, что версия видна поверх всех элементов Streamlit */
        header[data-testid="stHeader"] {
            z-index: 999998 !important;
        }
        /* Убираем отступы у main блока, чтобы версия была видна */
        .main .block-container {
            padding-top: 50px !important;
        }
    </style>
    <div class="version-badge-fixed">version 28.03.2026</div>
""", unsafe_allow_html=True)


def _load_logs() -> dict:
    """Загружает логи из файла"""
    if not LOGS_FILE.exists():
        return {"user_requests": [], "llm_request_count": 0}
    try:
        with open(LOGS_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
            # Миграция старого формата
            if "llm_requests_log" in data and "user_requests" not in data:
                # Конвертируем старый формат в новый
                user_requests = []
                current_group = None
                for req in data.get("llm_requests_log", []):
                    # Если нет группы, создаем новую
                    if current_group is None or req.get("id", 0) <= current_group.get("last_llm_id", 0):
                        current_group = {
                            "id": len(user_requests) + 1,
                            "user_message": "Неизвестный запрос",
                            "timestamp": req.get("start_time", time.time()),
                            "llm_requests": []
                        }
                        user_requests.append(current_group)
                    current_group["llm_requests"].append(req)
                    current_group["last_llm_id"] = req.get("id", 0)
                data = {"user_requests": user_requests, "llm_request_count": data.get("llm_request_count", 0)}
            return data
    except Exception:
        return {"user_requests": [], "llm_request_count": 0}


def _save_logs(logs: dict):
    """Сохраняет логи в файл"""
    try:
        with open(LOGS_FILE, "w", encoding="utf-8") as f:
            json.dump(logs, f, ensure_ascii=False, indent=2)
    except Exception:
        pass


# Устанавливаем callback функцию для увеличения счетчика запросов к LLM и логирования
# Эти функции не используются в Streamlit, но нужны для совместимости с llm_counter
def increment_llm_counter(function_name: str = "Unknown", prompt_preview: str = None):
    """Заглушка - логирование происходит в widget/app/chat_api.py"""
    pass


def update_llm_response(response_preview: str = None):
    """Заглушка - логирование происходит в widget/app/chat_api.py"""
    pass


set_llm_counter_callback(increment_llm_counter)
set_llm_response_callback(update_llm_response)

# Основной контент - панель логов
st.title("📊 Панель логов LLM")

# Загружаем логи из файла
logs = _load_logs()
user_requests = logs.get("user_requests", [])
llm_request_count = logs.get("llm_request_count", 0)

# Подсчитываем статистику
total_llm_calls = sum(len(ur.get("llm_requests", [])) for ur in user_requests)

# Статистика вверху
st.subheader("📈 Общая статистика")
col1, col2 = st.columns(2)
with col1:
    st.metric("Запросов пользователя", len(user_requests))
with col2:
    st.metric("Вызовов LLM всего", total_llm_calls)

st.divider()

# Кнопка очистки логов
if st.button("🗑️ Очистить логи", type="secondary"):
    empty_logs = {"user_requests": [], "llm_request_count": 0}
    _save_logs(empty_logs)
    st.rerun()

st.divider()

# Отображаем список запросов пользователя
if user_requests and len(user_requests) > 0:
    # Показываем запросы в обратном порядке (последний сверху)
    all_user_requests = list(reversed(user_requests.copy()))
    
    for user_req in all_user_requests:
            llm_requests = user_req.get("llm_requests", [])
            if not llm_requests:
                continue
            
            # Подсчитываем статистику для этого запроса
            total = len(llm_requests)
            
            # Форматируем время запроса
            timestamp = user_req.get("timestamp", time.time())
            time_str = time.strftime("%H:%M:%S", time.localtime(timestamp))
            
            # Создаем expander для каждого запроса пользователя
            with st.expander(
                f"💬 {user_req.get('user_message', 'Неизвестный запрос')} | Вызовов LLM: {total} | {time_str}",
                expanded=False
            ):
                # Статистика для этого запроса пользователя
                st.caption(f"📊 Статистика запроса: {total} вызовов LLM")
                st.divider()
                # Отображаем каждый вызов LLM
                for llm_req in llm_requests:
                    # Формируем строку с длительностью
                    if llm_req.get('duration'):
                        duration_display = llm_req['duration']
                        status_badge = "✅"
                    elif llm_req.get('start_time'):
                        current_duration = time.time() - llm_req['start_time']
                        if current_duration < 1:
                            duration_display = f"{current_duration * 1000:.0f}мс..."
                        elif current_duration < 60:
                            duration_display = f"{current_duration:.2f}с..."
                        else:
                            minutes = int(current_duration // 60)
                            seconds = current_duration % 60
                            duration_display = f"{minutes}м {seconds:.1f}с..."
                        status_badge = "⏳"
                    else:
                        duration_display = 'Выполняется...'
                        status_badge = "⏳"
                    
                    st.markdown(f"**{status_badge} `{llm_req.get('function', 'Unknown')}`** ({duration_display}) | ID: #{llm_req.get('id', 'N/A')}")
                    
                    col_prompt, col_response = st.columns(2)
                    
                    with col_prompt:
                        # Получаем System и User промпты
                        system_prompt = llm_req.get('system_prompt', '')
                        user_prompt = llm_req.get('user_prompt', llm_req.get('prompt_preview', 'N/A'))  # Поддержка старого формата
                        
                        # Формируем полный запрос с разделением на System и User
                        full_prompt = ""
                        if system_prompt:
                            full_prompt = f"System prompt:\n{system_prompt}\n\n"
                        full_prompt += f"User prompt:\n{user_prompt}"
                        
                        # Используем тот же стиль, что и для ответа (st.text_area)
                        st.text_area(
                            "📝 Запрос к модели:",
                            value=full_prompt,
                            height=400,
                            disabled=True,
                            key=f"prompt_{user_req.get('id')}_{llm_req.get('id')}"
                        )
                    
                    with col_response:
                        st.text_area(
                            "🤖 Ответ:",
                            value=llm_req.get('response_preview', 'Ожидание ответа...'),
                            height=400,
                            disabled=True,
                            key=f"response_{user_req.get('id')}_{llm_req.get('id')}"
                        )
                    
                    st.divider()
else:
    st.info("📋 Запросы к LLM будут отображаться здесь. Запросы приходят из виджета чата.")

# Автообновление каждые 2 секунды
if "last_refresh" not in st.session_state:
    st.session_state.last_refresh = time.time()

current_time = time.time()
if current_time - st.session_state.last_refresh >= 2:
    st.session_state.last_refresh = current_time
    time.sleep(0.1)  # Небольшая задержка для предотвращения бесконечного цикла
    st.rerun()
