import sys, time, datetime
from pathlib import Path
import streamlit as st
from dotenv import load_dotenv

# Добавляем корневую директорию проекта в путь для импортов
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from src.services.llm_counter import set_llm_counter_callback, set_llm_response_callback
from src.services import logs_db

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
    <div class="version-badge-fixed">version 30.03.2026</div>
""", unsafe_allow_html=True)


# Заглушки — логирование происходит в widget/app/chat_api.py
def _noop_counter(function_name="Unknown", prompt_preview=None, prompt_name=None):
    pass

def _noop_response(response_preview=None, prompt_tokens=None, completion_tokens=None):
    pass

set_llm_counter_callback(_noop_counter)
set_llm_response_callback(_noop_response)

# ──────────────────────────────────────────────
# Основной контент — панель логов
# ──────────────────────────────────────────────
st.title("📊 Панель логов LLM")

stats = logs_db.get_stats()

# Статистика вверху
st.subheader("📈 Общая статистика")
col1, col2, col3, col4 = st.columns(4)
with col1:
    st.metric("Запросов пользователя", stats["total_user_requests"])
with col2:
    st.metric("Вызовов LLM всего", stats["total_llm_calls"])
with col3:
    st.metric(
        "Токенов всего",
        f"{stats['total_tokens']:,}" if stats["any_usage_logged"] else "—",
    )
with col4:
    st.metric(
        "Стоимость всего",
        f"${stats['total_cost_usd']:.4f}" if stats["any_usage_logged"] else "—",
    )

st.divider()

# --- Сетевые ошибки ---
network_errors = logs_db.get_network_errors(limit=10)
if network_errors:
    st.subheader("🔴 Сетевые ошибки AI API")
    st.caption(
        "Ошибки подключения к OpenAI (VPN / интернет). "
        "Показаны последние записи (без ключей и тел запросов)."
    )
    for err in network_errors:
        ts = err.get("ts", 0)
        dt = datetime.datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M:%S") if ts else "—"
        err_type = err.get("type", "Unknown")
        st.error(f"{dt} — {err_type}")
    st.divider()

# Кнопка очистки логов
if st.button("🗑️ Очистить логи", type="secondary"):
    logs_db.clear_logs()
    st.rerun()

st.divider()

# Отображаем список запросов пользователя
all_user_requests = logs_db.get_all_user_requests()

if all_user_requests:
    for user_req in all_user_requests:
        llm_requests = user_req.get("llm_requests", [])
        if not llm_requests:
            continue

        # Подсчитываем статистику для этого запроса
        total = len(llm_requests)
        req_cost = sum(r.get("cost_usd") or 0 for r in llm_requests)
        req_tokens = sum((r.get("prompt_tokens") or 0) + (r.get("completion_tokens") or 0) for r in llm_requests)
        req_has_usage = any(r.get("prompt_tokens") is not None for r in llm_requests)

        # Форматируем время запроса
        timestamp = user_req.get("timestamp", time.time())
        time_str = time.strftime("%H:%M:%S", time.localtime(timestamp))

        cost_str = f"${req_cost:.4f}" if req_has_usage and req_cost else ""
        tokens_str = f"{req_tokens:,} токенов" if req_has_usage else ""
        meta = " | ".join(filter(None, [f"LLM: {total}", tokens_str, cost_str, time_str]))

        # Создаем expander для каждого запроса пользователя
        with st.expander(
            f"💬 {user_req.get('user_message', 'Неизвестный запрос')} | {meta}",
            expanded=False
        ):
            # Статистика для этого запроса пользователя
            sc1, sc2, sc3 = st.columns(3)
            sc1.metric("Вызовов LLM", total)
            sc2.metric("Токенов", f"{req_tokens:,}" if req_has_usage else "—")
            sc3.metric("Стоимость", f"${req_cost:.4f}" if req_has_usage else "—")
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

                pt = llm_req.get("prompt_tokens")
                ct = llm_req.get("completion_tokens")
                call_cost = llm_req.get("cost_usd")
                usage_str = ""
                if pt is not None:
                    usage_str = f" | {pt}↑ {ct}↓ tok | ${call_cost:.5f}"

                prompt_name = llm_req.get("prompt_name") or ""
                prompt_label = f" | prompt: `{prompt_name}`" if prompt_name else ""
                st.markdown(
                    f"**{status_badge} `{llm_req.get('function', 'Unknown')}`**"
                    f"{prompt_label} ({duration_display}){usage_str} | ID: #{llm_req.get('id', 'N/A')}"
                )

                col_prompt, col_response = st.columns(2)

                with col_prompt:
                    system_prompt = llm_req.get('system_prompt', '')
                    user_prompt = llm_req.get('user_prompt', 'N/A')

                    full_prompt = ""
                    if system_prompt:
                        full_prompt = f"System prompt:\n{system_prompt}\n\n"
                    full_prompt += f"User prompt:\n{user_prompt}"

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
    time.sleep(0.1)
    st.rerun()
