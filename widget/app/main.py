import json
import logging
import os
import time
import uuid
from pathlib import Path
from typing import Any, Dict, Optional
from urllib.parse import urlparse

import httpx
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field

from .rate_limit import InMemoryRateLimiter

# Load .env if present
load_dotenv()

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("widget-service")

ROOT_DIR = Path(__file__).resolve().parent.parent
APP_DIR = Path(__file__).resolve().parent
STATIC_DIR = ROOT_DIR / "static"
TEMPLATES_DIR = ROOT_DIR / "templates"
EXAMPLES_DIR = ROOT_DIR / "examples"

TENANTS_PATH = APP_DIR / "tenants.json"

UPSTREAM_CHAT_URL = os.getenv("UPSTREAM_CHAT_URL", "").strip()
UPSTREAM_AUTH_HEADER_NAME = os.getenv("UPSTREAM_AUTH_HEADER_NAME", "").strip()
UPSTREAM_AUTH_HEADER_VALUE = os.getenv("UPSTREAM_AUTH_HEADER_VALUE", "").strip()

# Rate limit: 30 requests per 5 minutes
rate_limiter = InMemoryRateLimiter(max_requests=30, window_seconds=300)

# In-memory session store: session_id -> {widget_key, page_host, created_at, messages}
SESSIONS: Dict[str, Dict[str, Any]] = {}


def _load_tenants() -> Dict[str, Dict[str, Any]]:
    if not TENANTS_PATH.exists():
        raise RuntimeError(f"tenants.json not found at {TENANTS_PATH}")
    with open(TENANTS_PATH, "r", encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, dict):
        raise RuntimeError("tenants.json must be an object keyed by widget_key")
    # Normalize
    out: Dict[str, Dict[str, Any]] = {}
    for k, v in data.items():
        if not isinstance(v, dict):
            continue
        allow = v.get("allow_domains") or []
        if not isinstance(allow, list):
            allow = []
        out[str(k)] = {"allow_domains": [str(x).lower() for x in allow]}
    return out


TENANTS = _load_tenants()


def _hostname_from_url(url: str) -> Optional[str]:
    if not url:
        return None
    try:
        p = urlparse(url)
        host = p.hostname
        return host.lower() if host else None
    except Exception:
        return None


def _host_allowed(host: Optional[str], allowed_domains: list) -> bool:
    if not host:
        return False
    host = host.lower()
    for d in allowed_domains:
        d = (d or "").lower()
        if not d:
            continue
        if host == d:
            return True
        if host.endswith("." + d):
            return True
    return False


def _extract_embed_host_from_headers(request: Request) -> Optional[str]:
    """
    Tries to get embedding page host from Origin/Referer headers.
    For /chat and /loader.js the Referer is typically the embedding page.
    """
    origin = request.headers.get("origin") or ""
    referer = request.headers.get("referer") or ""

    h = _hostname_from_url(origin)
    if h:
        return h
    h = _hostname_from_url(referer)
    if h:
        return h
    return None


def _tenant_or_404(widget_key: str) -> Dict[str, Any]:
    t = TENANTS.get(widget_key)
    if not t:
        raise HTTPException(status_code=404, detail=f"Unknown widget_key: {widget_key}")
    return t


def _is_local_dev_host(host: Optional[str]) -> bool:
    return host in ("localhost", "127.0.0.1")


def _enforce_embed_allowed(widget_key: str, embed_host: Optional[str]) -> None:
    tenant = _tenant_or_404(widget_key)
    allowed = tenant.get("allow_domains", [])
    if embed_host and _host_allowed(embed_host, allowed):
        return
    raise HTTPException(
        status_code=403,
        detail=f"Embedding host not allowed for widget_key={widget_key}. host={embed_host}",
    )


def _enforce_any_tenant_allows(embed_host: Optional[str]) -> None:
    """
    For /loader.js we don't know widget_key, so we allow if embed_host matches ANY tenant.
    """
    if not embed_host:
        # In practice script tag may omit referer in some contexts; allow in dev,
        # but log a warning.
        logger.warning("No Origin/Referer for /loader.js request; allowing (best-effort).")
        return

    for _k, t in TENANTS.items():
        if _host_allowed(embed_host, t.get("allow_domains", [])):
            return
    raise HTTPException(status_code=403, detail=f"Embedding host not allowed: {embed_host}")


def _rate_limit_key(request: Request, session_id: Optional[str]) -> str:
    if session_id:
        return "sid:" + session_id
    ip = (request.client.host if request.client else "") or "unknown"
    return "ip:" + ip


def _normalize_upstream_response(data: Any) -> Dict[str, Any]:
    # UI expects at least {"reply": "..."}.
    if isinstance(data, dict):
        for k in ["reply", "response", "answer", "message", "text", "content"]:
            if k in data and data[k] is not None:
                return {"reply": str(data[k]), "raw": data, "upstream": True}
        return {"reply": json.dumps(data, ensure_ascii=False), "raw": data, "upstream": True}
    if isinstance(data, (list, tuple)):
        return {"reply": json.dumps(list(data), ensure_ascii=False), "raw": data, "upstream": True}
    return {"reply": str(data), "raw": data, "upstream": True}


async def _proxy_chat(payload: Dict[str, Any]) -> Dict[str, Any]:
    # Если UPSTREAM_CHAT_URL не установлен, используем локальный API
    if not UPSTREAM_CHAT_URL:
        try:
            from .chat_api import process_chat_request
            
            session_id = payload.get("session_id")
            message = payload.get("message", "")
            widget_key = payload.get("widget_key", "demo")
            context = payload.get("context", {})
            
            # Получаем историю сообщений из сессии
            conversation_history = []
            if session_id and session_id in SESSIONS:
                conversation_history = SESSIONS[session_id].get("messages", [])
            
            # Обрабатываем запрос через локальный API
            result = process_chat_request(
                message=message,
                widget_key=widget_key,
                context=context,
                conversation_history=conversation_history,
            )
            
            # Обновляем историю сообщений в сессии
            if session_id and session_id in SESSIONS:
                if "messages" not in SESSIONS[session_id]:
                    SESSIONS[session_id]["messages"] = []
                
                # Добавляем сообщение пользователя
                SESSIONS[session_id]["messages"].append({
                    "role": "user",
                    "content": message
                })
                
                # Добавляем ответ ассистента
                SESSIONS[session_id]["messages"].append({
                    "role": "assistant",
                    "content": result.get("reply", "")
                })
                
                # Ограничиваем размер истории (последние 20 сообщений)
                if len(SESSIONS[session_id]["messages"]) > 20:
                    SESSIONS[session_id]["messages"] = SESSIONS[session_id]["messages"][-20:]
            
            return result
        except ImportError:
            logger.warning("chat_api module not available, using echo mode")
            return {"reply": str(payload.get("message", "")), "mode": "echo", "upstream": False}
        except Exception as e:
            logger.exception("Local chat API error: %s", str(e))
            return {
                "reply": f"Извините, произошла ошибка при обработке запроса: {str(e)}",
                "mode": "local",
                "upstream": False,
                "error": str(e),
            }

    # Используем внешний API, если UPSTREAM_CHAT_URL установлен
    headers: Dict[str, str] = {"content-type": "application/json"}
    if UPSTREAM_AUTH_HEADER_NAME and UPSTREAM_AUTH_HEADER_VALUE:
        headers[UPSTREAM_AUTH_HEADER_NAME] = UPSTREAM_AUTH_HEADER_VALUE

    timeout = httpx.Timeout(10.0, connect=3.0)
    async with httpx.AsyncClient(timeout=timeout) as client:
        try:
            resp = await client.post(UPSTREAM_CHAT_URL, json=payload, headers=headers)
        except httpx.RequestError as e:
            logger.exception("Upstream request error: %s", str(e))
            return {
                "reply": str(payload.get("message", "")),
                "mode": "echo",
                "upstream": False,
                "error": "upstream_unavailable",
            }

    if resp.status_code != 200:
        text = (resp.text or "").strip()
        logger.error("Upstream non-200: %s %s", resp.status_code, text[:500])
        raise HTTPException(
            status_code=502,
            detail={
                "error": "upstream_non_200",
                "status_code": resp.status_code,
                "body": text[:2000],
            },
        )

    # Try JSON, fallback to text
    try:
        data = resp.json()
        return _normalize_upstream_response(data)
    except Exception:
        text = (resp.text or "").strip()
        return {"reply": text, "raw_text": text, "upstream": True}


app = FastAPI(
    title="widget-service",
    version="1.0.0",
    docs_url=None,  # Отключаем Swagger
    redoc_url=None  # Отключаем ReDoc
)

app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")
# Монтируем examples для доступа к статическим файлам демо-страниц
app.mount("/examples", StaticFiles(directory=str(EXAMPLES_DIR)), name="examples")
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))


class SessionCreateIn(BaseModel):
    widget_key: str = Field(..., min_length=1)
    page_url: Optional[str] = None


class SessionCreateOut(BaseModel):
    session_id: str


class ChatIn(BaseModel):
    session_id: str = Field(..., min_length=1)
    message: str = Field(..., min_length=1)
    context: Dict[str, Any] = Field(default_factory=dict)
    widget_key: Optional[str] = None


@app.get("/", include_in_schema=False)
async def root():
    """Главная страница - демо-сайт с виджетом"""
    demo_path = EXAMPLES_DIR / "demo" / "index.html"
    if not demo_path.exists():
        raise HTTPException(status_code=500, detail="examples/demo/index.html missing")
    return FileResponse(str(demo_path), media_type="text/html; charset=utf-8")


@app.get("/health", include_in_schema=False)
async def health():
    return {"status": "ok", "time": int(time.time())}


@app.get("/loader.js", include_in_schema=False)
async def loader_js(request: Request):
    embed_host = _extract_embed_host_from_headers(request)
    if embed_host and _is_local_dev_host(embed_host):
        pass
    else:
        _enforce_any_tenant_allows(embed_host)

    js_path = STATIC_DIR / "loader.js"
    if not js_path.exists():
        raise HTTPException(status_code=500, detail="static/loader.js missing")

    content = js_path.read_text(encoding="utf-8")
    return Response(content=content, media_type="application/javascript; charset=utf-8")


@app.get("/chat", include_in_schema=False)
async def chat(request: Request, key: str = "demo"):
    embed_host = _extract_embed_host_from_headers(request)
    if embed_host:
        _enforce_embed_allowed(key, embed_host)
    else:
        tenant = _tenant_or_404(key)
        if not (
            _host_allowed("localhost", tenant.get("allow_domains", []))
            or _host_allowed("127.0.0.1", tenant.get("allow_domains", []))
        ):
            raise HTTPException(
                status_code=403,
                detail="Missing Origin/Referer and tenant does not allow localhost/127.0.0.1",
            )

    return templates.TemplateResponse(
        "chat.html",
        {"request": request, "widget_key": key},
    )


@app.post("/api/session", response_model=SessionCreateOut, include_in_schema=False)
async def create_session(request: Request, payload: SessionCreateIn):
    """
    Скрыт от Swagger - используется только виджетом для обратной совместимости.
    Сессии создаются автоматически при первом запросе к /api/chat.
    """
    widget_key = payload.widget_key
    tenant = _tenant_or_404(widget_key)

    page_host = _hostname_from_url(payload.page_url or "") or _extract_embed_host_from_headers(request)

    if page_host:
        if not _host_allowed(page_host, tenant.get("allow_domains", [])):
            raise HTTPException(status_code=403, detail=f"page_url host not allowed: {page_host}")
    else:
        if not (
            _host_allowed("localhost", tenant.get("allow_domains", []))
            or _host_allowed("127.0.0.1", tenant.get("allow_domains", []))
        ):
            raise HTTPException(
                status_code=403,
                detail="Unable to determine embedding host (no page_url / Origin / Referer)",
            )

    rk = _rate_limit_key(request, None)
    allowed, retry_after = rate_limiter.allow(rk)
    if not allowed:
        raise HTTPException(status_code=429, detail={"error": "rate_limited", "retry_after_seconds": retry_after})

    session_id = str(uuid.uuid4())
    SESSIONS[session_id] = {
        "widget_key": widget_key,
        "page_host": page_host,
        "created_at": time.time(),
        "messages": [],  # История сообщений для сессии
    }
    logger.info(f"Session created: {session_id} for widget_key={widget_key}, page_host={page_host}")
    return SessionCreateOut(session_id=session_id)


def _create_session_for_chat(request: Request, payload: ChatIn) -> Optional[str]:
    """Создаёт сессию на лету при unknown_session (например, после перезапуска Docker)."""
    logger.info(f"Auto-create attempt: widget_key={payload.widget_key}, context={payload.context}")
    widget_key = payload.widget_key or "demo"
    tenant = TENANTS.get(widget_key)
    if not tenant:
        logger.warning(f"Auto-create session: tenant not found for widget_key={widget_key}")
        return None
    ctx = payload.context or {}
    page_url = str(ctx.get("page_url") or "")
    page_host = _hostname_from_url(page_url) or _extract_embed_host_from_headers(request)
    
    # Если page_host не определен или это localhost/127.0.0.1, проверяем разрешение для localhost
    if not page_host or _is_local_dev_host(page_host):
        if (
            _host_allowed("localhost", tenant.get("allow_domains", []))
            or _host_allowed("127.0.0.1", tenant.get("allow_domains", []))
        ):
            page_host = "localhost"  # Устанавливаем localhost для локальной разработки
        else:
            logger.warning(f"Auto-create session: no page_host and localhost not allowed for {widget_key}")
            return None
    else:
        # Проверяем разрешение для определенного хоста
        if not _host_allowed(page_host, tenant.get("allow_domains", [])):
            logger.warning(f"Auto-create session: host {page_host} not allowed for {widget_key}, allowed: {tenant.get('allow_domains', [])}")
            return None
    
    session_id = str(uuid.uuid4())
    SESSIONS[session_id] = {
        "widget_key": widget_key,
        "page_host": page_host,
        "created_at": time.time(),
        "messages": [],
    }
    logger.info(f"Session auto-created for chat: {session_id} (widget_key={widget_key}, page_host={page_host})")
    return session_id


@app.post("/api/chat", summary="Отправка сообщения в чат", description="Отправляет сообщение в чат. Сессия создается автоматически при первом запросе, если не указана.")
async def chat_proxy(request: Request, payload: ChatIn):
    logger.info(f"Chat request: session_id={payload.session_id}, message length={len(payload.message)}, widget_key={payload.widget_key}, available sessions={len(SESSIONS)}")
    sess = SESSIONS.get(payload.session_id)
    session_id = payload.session_id
    if not sess:
        # Авто-создание сессии при unknown_session (например, устаревший sessionId из localStorage)
        logger.info(f"Session {payload.session_id} not found, attempting auto-create with widget_key={payload.widget_key}, context={payload.context}")
        new_id = _create_session_for_chat(request, payload)
        if new_id:
            session_id = new_id
            sess = SESSIONS[new_id]
            logger.info(f"Using auto-created session {new_id}")
        else:
            logger.warning(f"Auto-create session failed for widget_key={payload.widget_key}, context={payload.context}")
    if not sess:
        logger.warning(f"Session not found: {payload.session_id}, available sessions: {list(SESSIONS.keys())[:5]}")
        raise HTTPException(status_code=400, detail={"error": "unknown_session", "hint": "Call POST /api/session first"})

    widget_key = sess.get("widget_key")
    tenant = _tenant_or_404(widget_key)

    rk = _rate_limit_key(request, payload.session_id)
    allowed, retry_after = rate_limiter.allow(rk)
    if not allowed:
        raise HTTPException(status_code=429, detail={"error": "rate_limited", "retry_after_seconds": retry_after})

    if payload.widget_key and payload.widget_key != widget_key:
        raise HTTPException(status_code=400, detail={"error": "widget_key_mismatch"})

    ctx = payload.context or {}
    ctx_page_url = str(ctx.get("page_url") or "")
    ctx_host = _hostname_from_url(ctx_page_url) if ctx_page_url else None
    if ctx_host:
        if not _host_allowed(ctx_host, tenant.get("allow_domains", [])):
            raise HTTPException(status_code=403, detail={"error": "domain_not_allowed", "host": ctx_host})
    else:
        page_host = sess.get("page_host")
        if page_host and not _host_allowed(page_host, tenant.get("allow_domains", [])):
            raise HTTPException(status_code=403, detail={"error": "domain_not_allowed", "host": page_host})
        if not page_host:
            if not (
                _host_allowed("localhost", tenant.get("allow_domains", []))
                or _host_allowed("127.0.0.1", tenant.get("allow_domains", []))
            ):
                raise HTTPException(status_code=403, detail={"error": "missing_context_page_url"})

    outgoing = payload.model_dump()
    outgoing["session_id"] = session_id  # Может быть автосозданная сессия
    outgoing["widget_key"] = widget_key
    outgoing.setdefault("context", {})
    outgoing["context"]["page_host"] = ctx_host or sess.get("page_host")

    result = await _proxy_chat(outgoing)
    # Если сессия была автосоздана — возвращаем её в ответе, чтобы клиент обновил sessionId
    if session_id != payload.session_id:
        result = dict(result)
        result["session_id"] = session_id
    return JSONResponse(content=result)

