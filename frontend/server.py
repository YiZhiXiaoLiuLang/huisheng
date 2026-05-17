from __future__ import annotations

import argparse
import json
import os
import ssl
import sys
import time
import uuid
from datetime import datetime
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Any, Callable, Iterator
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen

import twin_core

ROOT = Path(__file__).resolve().parent
REPO_ROOT = ROOT.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.append(str(REPO_ROOT))

import downloadchatmsg_v2

STATIC_DIR = ROOT / "static"
DATA_DIR = ROOT / "data"
CONVERSATIONS_DIR = DATA_DIR / "conversations"
API_REQUEST_LOG_PATH = DATA_DIR / "api_requests.jsonl"

DEFAULT_BASE_URL = os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1").rstrip("/")
DEFAULT_API_KEY = os.getenv("OPENAI_API_KEY", "")
DEFAULT_MODEL = os.getenv("OPENAI_MODEL", "gpt-4.1-mini")
REQUEST_TIMEOUT = int(os.getenv("OPENAI_TIMEOUT", "120"))
NEW_CONVERSATION_TITLE = "新对话"
ASSISTANT_MESSAGE_SEPARATOR = "&n&"

MIME_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".json": "application/json; charset=utf-8",
    ".svg": "image/svg+xml",
    ".ico": "image/x-icon",
}


def now_text() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def ensure_dirs() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    CONVERSATIONS_DIR.mkdir(parents=True, exist_ok=True)
    twin_core.ensure_dirs()


def append_api_request_log(entry: dict[str, Any]) -> None:
    ensure_dirs()
    entry.setdefault("time", now_text())
    with API_REQUEST_LOG_PATH.open("a", encoding="utf-8") as file:
        json.dump(entry, file, ensure_ascii=False)
        file.write("\n")


def safe_conversation_id(raw_id: str | None) -> str:
    if not raw_id:
        return ""
    value = raw_id.strip()
    if not value:
        return ""
    allowed = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_")
    if not all(char in allowed for char in value):
        raise ValueError("鏃犳晥鐨勪細璇?ID")
    return value


def conversation_path(conversation_id: str) -> Path:
    safe_id = safe_conversation_id(conversation_id)
    if not safe_id:
        raise ValueError("缂哄皯浼氳瘽 ID")
    return CONVERSATIONS_DIR / f"{safe_id}.json"


def message_for_storage(
    sender: str,
    content: str,
    message_type: int = 1,
    reasoning_content: str = "",
) -> dict[str, Any]:
    message = {
        "time": now_text(),
        "sender": sender,
        "type": message_type,
        "content": content,
    }
    if reasoning_content:
        message["reasoning_content"] = reasoning_content
    return message


def new_conversation(title: str | None = None) -> dict[str, Any]:
    created = now_text()
    conversation = {
        "id": uuid.uuid4().hex,
        "title": title or NEW_CONVERSATION_TITLE,
        "created_at": created,
        "updated_at": created,
        "messages": [],
    }
    save_conversation(conversation)
    return conversation


def load_conversation(conversation_id: str) -> dict[str, Any]:
    path = conversation_path(conversation_id)
    if not path.exists():
        raise FileNotFoundError(conversation_id)
    with path.open("r", encoding="utf-8") as file:
        data = json.load(file)
    data.setdefault("id", conversation_id)
    data.setdefault("title", NEW_CONVERSATION_TITLE)
    data.setdefault("messages", [])
    return data


def save_conversation(conversation: dict[str, Any]) -> None:
    ensure_dirs()
    conversation["updated_at"] = now_text()
    path = conversation_path(str(conversation["id"]))
    temp_path = path.with_suffix(".tmp")
    with temp_path.open("w", encoding="utf-8") as file:
        json.dump(conversation, file, ensure_ascii=False, indent=2)
        file.write("\n")
    temp_path.replace(path)


def list_conversations() -> list[dict[str, Any]]:
    ensure_dirs()
    items: list[dict[str, Any]] = []
    for path in CONVERSATIONS_DIR.glob("*.json"):
        try:
            with path.open("r", encoding="utf-8") as file:
                data = json.load(file)
            messages = data.get("messages") if isinstance(data.get("messages"), list) else []
            last_message = messages[-1]["content"] if messages and isinstance(messages[-1], dict) else ""
            items.append(
                {
                    "id": data.get("id", path.stem),
                    "title": data.get("title") or NEW_CONVERSATION_TITLE,
                    "created_at": data.get("created_at", ""),
                    "updated_at": data.get("updated_at", ""),
                    "message_count": len(messages),
                    "last_message": last_message,
                }
            )
        except (OSError, json.JSONDecodeError, TypeError):
            continue
    items.sort(key=lambda item: item.get("updated_at", ""), reverse=True)
    return items


def title_from_text(text: str) -> str:
    compact = " ".join(text.strip().split())
    if not compact:
        return NEW_CONVERSATION_TITLE
    return compact[:24]


def split_assistant_messages(content: str) -> list[str]:
    parts = [part.strip() for part in content.split(ASSISTANT_MESSAGE_SEPARATOR)]
    return [part for part in parts if part]


def mask_sensitive_headers(headers: dict[str, str]) -> dict[str, str]:
    masked: dict[str, str] = {}
    for key, value in headers.items():
        key_text = str(key)
        key_lower = key_text.lower()
        if key_lower in {"authorization", "cookie", "set-cookie", "x-api-key", "api-key"}:
            masked[key_text] = "***"
        else:
            masked[key_text] = str(value)
    return masked


def mask_sensitive_cookies(cookies: dict[str, str]) -> dict[str, str]:
    masked: dict[str, str] = {}
    for key, value in cookies.items():
        key_text = str(key)
        key_lower = key_text.lower()
        if "token" in key_lower or "session" in key_lower or "auth" in key_lower:
            masked[key_text] = "***"
        else:
            masked[key_text] = str(value)
    return masked


def build_openai_messages(messages: list[dict[str, Any]], system_prompt: str) -> list[dict[str, str]]:
    output: list[dict[str, str]] = []
    if system_prompt.strip():
        output.append({"role": "system", "content": system_prompt.strip()})
    for item in messages:
        sender = item.get("sender")
        content = str(item.get("content", "")).strip()
        if not content:
            continue
        if sender == "user":
            output.append({"role": "user", "content": content})
        elif sender == "assistant":
            output.append({"role": "assistant", "content": content})
    return output


def append_system_prompt_tail(messages: list[dict[str, str]], system_prompt: str) -> list[dict[str, str]]:
    prompt = system_prompt.strip()
    if not prompt:
        return messages
    messages.append({"role": "user", "content": prompt})
    return messages


def build_chat_context_messages(max_items: int = 120) -> list[dict[str, str]]:
    try:
        if not twin_core.CORPUS_PATH.exists():
            return []
        corpus = twin_core.read_json(twin_core.CORPUS_PATH)
    except Exception:
        return []
    if not isinstance(corpus, list) or not corpus:
        return []

    output: list[dict[str, str]] = []
    for item in corpus[-max_items:]:
        if not isinstance(item, dict):
            continue
        sender = str(item.get("sender", "")).strip().lower()
        role = "assistant" if sender == "assistant" else "user"
        content = str(item.get("content", "")).strip()
        if not content:
            continue
        output.append({"role": role, "content": content})
    return output


def load_imported_history(offset: int = 0, limit: int = 100) -> dict[str, Any]:
    try:
        if not twin_core.CORPUS_PATH.exists():
            return {"messages": [], "total": 0, "offset": 0, "limit": limit}
        corpus = twin_core.read_json(twin_core.CORPUS_PATH)
    except Exception:
        return {"messages": [], "total": 0, "offset": 0, "limit": limit}
    if not isinstance(corpus, list):
        return {"messages": [], "total": 0, "offset": 0, "limit": limit}

    total = len(corpus)
    safe_limit = max(1, min(200, int(limit)))
    safe_offset = max(0, min(max(total - 1, 0), int(offset))) if total else 0
    start = max(0, total - safe_offset - safe_limit)
    end = total - safe_offset if safe_offset else total
    sliced = corpus[start:end]
    messages: list[dict[str, Any]] = []
    for item in sliced:
        if not isinstance(item, dict):
            continue
        messages.append(
            {
                "sender": str(item.get("sender", "user")),
                "content": str(item.get("content", "")),
                "time": str(item.get("time", "")),
                "type": int(item.get("type", 1) or 1),
            }
        )
    return {"messages": messages, "total": total, "offset": safe_offset, "limit": safe_limit}


def completion_url(base_url: str) -> str:
    clean = base_url.rstrip("/")
    if clean.endswith("/chat/completions"):
        return clean
    return f"{clean}/chat/completions"


def stream_openai_compatible_api(
    *,
    messages: list[dict[str, str]],
    api_key: str,
    base_url: str,
    model: str,
    temperature: float,
    thinking_enabled: bool,
    reasoning_effort: str,
    conversation_id: str,
) -> Iterator[dict[str, str]]:
    if not api_key:
        raise RuntimeError("Missing API key. Set it in UI settings or OPENAI_API_KEY.")

    payload = {
        "model": model,
        "messages": messages,
        "stream": True,
        "thinking": {"type": "enabled" if thinking_enabled else "disabled"},
    }
    if thinking_enabled:
        payload["reasoning_effort"] = reasoning_effort
    else:
        payload["temperature"] = temperature
    url = completion_url(base_url)
    request = Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        method="POST",
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "Accept": "text/event-stream",
        },
    )
    request_started = time.time()
    log_entry: dict[str, Any] = {
        "time": now_text(),
        "conversation_id": conversation_id,
        "request": {
            "url": url,
            "method": "POST",
            "headers": {
                "Authorization": "Bearer ***" if api_key else "",
                "Content-Type": "application/json",
                "Accept": "text/event-stream",
            },
            "payload": payload,
        },
        "response": {
            "status": None,
            "content": "",
            "reasoning_content": "",
            "raw_event_count": 0,
        },
        "error": None,
    }

    try:
        context = ssl.create_default_context()
        with urlopen(request, timeout=REQUEST_TIMEOUT, context=context) as response:
            log_entry["response"]["status"] = response.status
            for raw_line in response:
                line = raw_line.decode("utf-8", errors="replace").strip()
                if not line or line.startswith(":") or not line.startswith("data:"):
                    continue
                data_text = line.removeprefix("data:").strip()
                if data_text == "[DONE]":
                    break
                log_entry["response"]["raw_event_count"] += 1
                try:
                    data = json.loads(data_text)
                    choice = data.get("choices", [{}])[0]
                    delta = choice.get("delta") or {}
                    content = delta.get("content")
                    reasoning_content = delta.get("reasoning_content")
                except (json.JSONDecodeError, TypeError, KeyError, IndexError) as error:
                    raise RuntimeError(f"Failed to parse stream event: {data_text[:500]}") from error
                if reasoning_content is not None:
                    log_entry["response"]["reasoning_content"] += str(reasoning_content)
                    yield {"type": "reasoning", "content": str(reasoning_content)}
                if content is not None:
                    log_entry["response"]["content"] += str(content)
                    yield {"type": "content", "content": str(content)}
    except HTTPError as error:
        raw_error = error.read().decode("utf-8", errors="replace")
        log_entry["response"]["status"] = error.code
        log_entry["error"] = f"HTTP {error.code} {raw_error}"
        raise RuntimeError(f"API 璇锋眰澶辫触锛欻TTP {error.code} {raw_error}") from error
    except URLError as error:
        log_entry["error"] = f"URL error: {error.reason}"
        raise RuntimeError(f"API connection failed: {error.reason}") from error
    except TimeoutError as error:
        log_entry["error"] = "Timeout"
        raise RuntimeError("API request timeout.") from error
    except Exception as error:
        log_entry["error"] = str(error)
        raise
    finally:
        log_entry["duration_ms"] = round((time.time() - request_started) * 1000)
        append_api_request_log(log_entry)


def complete_openai_once(
    *,
    messages: list[dict[str, str]],
    api_key: str,
    base_url: str,
    model: str,
    temperature: float = 0.2,
    max_tokens: int | None = None,
) -> str:
    if not api_key:
        raise RuntimeError("缂哄皯 API Key锛屾棤娉曟墽琛屽ぇ妯″瀷鐢诲儚鎬荤粨")
    payload = {
        "model": model,
        "messages": messages,
        "stream": False,
        "temperature": temperature,
    }
    if max_tokens is not None:
        payload["max_tokens"] = max_tokens
    url = completion_url(base_url)
    request = Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        method="POST",
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        },
    )
    request_started = time.time()
    log_entry: dict[str, Any] = {
        "time": now_text(),
        "request_type": "chat.completions.once",
        "request": {
            "url": url,
            "method": "POST",
            "headers": {
                "Authorization": "Bearer ***" if api_key else "",
                "Content-Type": "application/json",
                "Accept": "application/json",
            },
            "payload": payload,
        },
        "response": {
            "status": None,
            "content": "",
            "raw_event_count": 0,
        },
        "error": None,
    }
    raw_text = ""
    data: dict[str, Any] | None = None
    try:
        with urlopen(request, timeout=REQUEST_TIMEOUT, context=ssl.create_default_context()) as response:
            log_entry["response"]["status"] = response.status
            raw_text = response.read().decode("utf-8", errors="replace")
            log_entry["response"]["content"] = raw_text
            data = json.loads(raw_text)
        return str(data["choices"][0]["message"]["content"]).strip()
    except HTTPError as error:
        raw_error = error.read().decode("utf-8", errors="replace")
        log_entry["response"]["status"] = error.code
        log_entry["error"] = f"HTTP {error.code} {raw_error}"
        raise RuntimeError(f"API 璇锋眰澶辫触锛欻TTP {error.code} {raw_error}") from error
    except URLError as error:
        log_entry["error"] = f"URL error: {error.reason}"
        raise RuntimeError(f"API connection failed: {error.reason}") from error
    except TimeoutError as error:
        log_entry["error"] = "Timeout"
        raise RuntimeError("API request timeout.") from error
    except Exception as error:  # noqa: BLE001
        log_entry["error"] = str(error)
        raise RuntimeError(f"妯″瀷杩斿洖鏃犳硶瑙ｆ瀽: {data if data is not None else raw_text}") from error
    finally:
        log_entry["duration_ms"] = round((time.time() - request_started) * 1000)
        append_api_request_log(log_entry)


class MemoryStewardAgent:
    """Long-context manager: retrieval + history context + checkpoint pointer."""

    name = "memory_steward"

    def __init__(
        self,
        *,
        memory_index: dict[str, Any] | None,
        include_chat_context: bool,
        context_k: int,
        context_loader: Callable[[int], list[dict[str, str]]],
    ) -> None:
        self.memory_index = memory_index or {}
        self.include_chat_context = include_chat_context
        self.context_k = context_k
        self.context_loader = context_loader

    def plan_turn(self, latest_user_message: str, memory_limit: int = 5) -> dict[str, Any]:
        memories = twin_core.search_memories(self.memory_index, latest_user_message, limit=memory_limit)
        context_messages: list[dict[str, str]] = []
        if self.include_chat_context:
            context_messages = self.context_loader(self.context_k)
        state = twin_core.load_state() or {}
        return {
            "memories": memories,
            "context_messages": context_messages,
            "memory_hits": len(memories),
            "context_count": len(context_messages),
            "context_k": self.context_k if self.include_chat_context else 0,
            "active_checkpoint_id": str(state.get("active_checkpoint_id", "")),
        }


class StyleActorAgent:
    """Reply performer: profile prompt + style-consistent generation payload."""

    name = "style_actor"

    def __init__(
        self,
        *,
        profile: dict[str, Any] | None,
        extra_prompt: str,
    ) -> None:
        self.profile = profile or {}
        self.extra_prompt = extra_prompt

    def build_system_prompt(self, memories: list[dict[str, Any]]) -> str:
        return twin_core.build_system_prompt(self.profile, memories=memories, extra_prompt=self.extra_prompt)

    @staticmethod
    def compose_messages(
        *,
        conversation_messages: list[dict[str, Any]],
        system_prompt: str,
        context_messages: list[dict[str, str]],
        append_tail_user_prompt: bool,
    ) -> list[dict[str, str]]:
        payload = build_openai_messages(conversation_messages, system_prompt)
        if context_messages:
            insert_index = 1 if payload and payload[0].get("role") == "system" else 0
            payload[insert_index:insert_index] = context_messages
        if append_tail_user_prompt:
            return append_system_prompt_tail(payload, system_prompt)
        return payload


class MultiAgentOrchestrator:
    """Coordinates Memory Steward and Style Actor for one chat turn."""

    def __init__(self, memory_steward: MemoryStewardAgent, style_actor: StyleActorAgent) -> None:
        self.memory_steward = memory_steward
        self.style_actor = style_actor

    def build_chat_payload(
        self,
        *,
        conversation_messages: list[dict[str, Any]],
        latest_user_message: str,
        append_tail_user_prompt: bool,
    ) -> dict[str, Any]:
        memory_result = self.memory_steward.plan_turn(latest_user_message, memory_limit=5)
        system_prompt = self.style_actor.build_system_prompt(memory_result["memories"])
        openai_messages = self.style_actor.compose_messages(
            conversation_messages=conversation_messages,
            system_prompt=system_prompt,
            context_messages=memory_result["context_messages"],
            append_tail_user_prompt=append_tail_user_prompt,
        )
        return {
            "system_prompt": system_prompt,
            "openai_messages": openai_messages,
            "trace": {
                "memory_steward": {
                    "memory_hits": memory_result["memory_hits"],
                    "context_count": memory_result["context_count"],
                    "context_k": memory_result["context_k"],
                    "active_checkpoint_id": memory_result["active_checkpoint_id"],
                },
                "style_actor": {
                    "system_prompt_ready": bool(system_prompt.strip()),
                    "message_count": len(openai_messages),
                },
            },
        }


class ChatHandler(BaseHTTPRequestHandler):
    server_version = "LocalAIChat/1.1"

    def do_GET(self) -> None:
        try:
            parsed = urlparse(self.path)
            if parsed.path == "/":
                self.serve_static(STATIC_DIR / "index.html")
                return
            if parsed.path == "/api/config":
                self.send_json(
                    {
                        "base_url": DEFAULT_BASE_URL,
                        "model": DEFAULT_MODEL,
                        "has_env_api_key": bool(DEFAULT_API_KEY),
                    }
                )
                return
            if parsed.path == "/api/conversations":
                self.send_json({"conversations": list_conversations()})
                return
            if parsed.path == "/api/twin/status":
                self.send_json(self.get_twin_status())
                return
            if parsed.path == "/api/twin/sources":
                self.send_json({"sources": twin_core.summarize_sources()})
                return
            if parsed.path == "/api/checkpoints":
                conversation_id = self.get_query_value(parsed.query, "conversation_id")
                self.send_json({"checkpoints": twin_core.list_checkpoints(conversation_id or None)})
                return
            if parsed.path == "/api/context-preview":
                k = self.parse_context_k(self.get_query_value(parsed.query, "k"))
                self.send_json({"messages": build_chat_context_messages(max_items=k)})
                return
            if parsed.path == "/api/imported-history":
                offset = self.parse_non_negative_int(self.get_query_value(parsed.query, "offset"), 0)
                limit = self.parse_non_negative_int(self.get_query_value(parsed.query, "limit"), 80)
                self.send_json(load_imported_history(offset=offset, limit=limit))
                return
            if parsed.path.startswith("/api/conversations/"):
                conversation_id = parsed.path.rsplit("/", 1)[-1]
                self.send_json(load_conversation(conversation_id))
                return
            if parsed.path.startswith("/static/"):
                relative = parsed.path.removeprefix("/static/").lstrip("/")
                self.serve_static(STATIC_DIR / relative)
                return
            self.send_error_json(HTTPStatus.NOT_FOUND, "Not found")
        except FileNotFoundError:
            self.send_error_json(HTTPStatus.NOT_FOUND, "Conversation not found")
        except ValueError as error:
            self.send_error_json(HTTPStatus.BAD_REQUEST, str(error))
        except Exception as error:  # noqa: BLE001
            self.send_error_json(HTTPStatus.INTERNAL_SERVER_ERROR, str(error))

    def do_POST(self) -> None:
        try:
            parsed = urlparse(self.path)
            if parsed.path == "/api/conversations":
                payload = self.read_json_body()
                conversation = new_conversation(payload.get("title"))
                self.send_json(conversation, HTTPStatus.CREATED)
                return
            if parsed.path == "/api/chat":
                self.handle_chat()
                return
            if parsed.path == "/api/messages":
                self.handle_add_message()
                return
            if parsed.path == "/api/twin/import":
                self.handle_twin_import()
                return
            if parsed.path == "/api/twin/fetch-import":
                self.handle_twin_fetch_import()
                return
            if parsed.path == "/api/checkpoints":
                self.handle_create_checkpoint()
                return
            if parsed.path == "/api/checkpoints/restore":
                self.handle_restore_checkpoint()
                return
            self.send_error_json(HTTPStatus.NOT_FOUND, "Not found")
        except ValueError as error:
            self.send_error_json(HTTPStatus.BAD_REQUEST, str(error))
        except RuntimeError as error:
            self.send_error_json(HTTPStatus.BAD_GATEWAY, str(error))
        except Exception as error:  # noqa: BLE001
            self.send_error_json(HTTPStatus.INTERNAL_SERVER_ERROR, str(error))

    def do_DELETE(self) -> None:
        try:
            parsed = urlparse(self.path)
            if parsed.path.startswith("/api/conversations/"):
                conversation_id = parsed.path.rsplit("/", 1)[-1]
                path = conversation_path(conversation_id)
                if path.exists():
                    path.unlink()
                self.send_json({"ok": True})
                return
            self.send_error_json(HTTPStatus.NOT_FOUND, "Not found")
        except ValueError as error:
            self.send_error_json(HTTPStatus.BAD_REQUEST, str(error))
        except Exception as error:  # noqa: BLE001
            self.send_error_json(HTTPStatus.INTERNAL_SERVER_ERROR, str(error))

    def do_OPTIONS(self) -> None:
        self.send_response(HTTPStatus.NO_CONTENT)
        self.send_cors_headers()
        self.end_headers()

    def handle_chat(self) -> None:
        payload = self.read_json_body()
        conversation_id = safe_conversation_id(payload.get("conversation_id"))
        if not conversation_id:
            raise ValueError("Please send at least one user message first.")
        conversation = load_conversation(conversation_id)
        messages = conversation.setdefault("messages", [])
        if not messages or messages[-1].get("sender") != "user":
            raise ValueError("No new user message to generate from.")

        settings = payload.get("settings") if isinstance(payload.get("settings"), dict) else {}
        api_key = str(settings.get("api_key") or DEFAULT_API_KEY)
        base_url = str(settings.get("base_url") or DEFAULT_BASE_URL)
        model = str(settings.get("model") or DEFAULT_MODEL)
        user_system_prompt = str(settings.get("system_prompt") or "")
        prepend_chat_context = self.parse_bool(settings.get("prepend_chat_context", False))
        append_tail_user_prompt = self.parse_bool(settings.get("append_tail_user_prompt", True))
        context_k = self.parse_context_k(settings.get("context_k", 40))
        temperature = self.parse_temperature(settings.get("temperature", 0.7))
        thinking_enabled = self.parse_bool(settings.get("thinking_enabled", True))
        reasoning_effort = self.parse_reasoning_effort(settings.get("reasoning_effort", "high"))
        memory_index = twin_core.load_memory_index() or {}
        profile = twin_core.load_skill()
        latest_user = str(messages[-1].get("content", "")) if messages else ""
        memory_steward = MemoryStewardAgent(
            memory_index=memory_index,
            include_chat_context=prepend_chat_context,
            context_k=context_k,
            context_loader=build_chat_context_messages,
        )
        style_actor = StyleActorAgent(
            profile=profile,
            extra_prompt=user_system_prompt,
        )
        orchestrator = MultiAgentOrchestrator(memory_steward, style_actor)
        agent_payload = orchestrator.build_chat_payload(
            conversation_messages=messages,
            latest_user_message=latest_user,
            append_tail_user_prompt=append_tail_user_prompt,
        )
        openai_messages = agent_payload["openai_messages"]

        self.start_sse_response()
        self.send_sse_event("conversation", {"conversation": conversation})
        self.send_sse_event("agent_trace", agent_payload["trace"])

        assistant_text = ""
        reasoning_text = ""
        try:
            for chunk in stream_openai_compatible_api(
                messages=openai_messages,
                api_key=api_key,
                base_url=base_url,
                model=model,
                temperature=temperature,
                thinking_enabled=thinking_enabled,
                reasoning_effort=reasoning_effort,
                conversation_id=str(conversation.get("id", conversation_id)),
            ):
                if chunk["type"] == "reasoning":
                    reasoning_text += chunk["content"]
                    self.send_sse_event("reasoning_delta", {"content": chunk["content"]})
                else:
                    assistant_text += chunk["content"]
                    self.send_sse_event("delta", {"content": chunk["content"]})

            for index, part in enumerate(split_assistant_messages(assistant_text) or [""]):
                messages.append(
                    message_for_storage(
                        "assistant",
                        part,
                        reasoning_content=reasoning_text if index == 0 else "",
                    )
                )
            save_conversation(conversation)
            self.send_sse_event("done", {"conversation": conversation})
        except (BrokenPipeError, ConnectionResetError):
            if assistant_text or reasoning_text:
                for index, part in enumerate(split_assistant_messages(assistant_text) or [""]):
                    messages.append(
                        message_for_storage(
                            "assistant",
                            part,
                            reasoning_content=reasoning_text if index == 0 else "",
                        )
                    )
            save_conversation(conversation)
        except Exception as error:  # noqa: BLE001
            if assistant_text or reasoning_text:
                for index, part in enumerate(split_assistant_messages(assistant_text) or [""]):
                    messages.append(
                        message_for_storage(
                            "assistant",
                            part,
                            reasoning_content=reasoning_text if index == 0 else "",
                        )
                    )
            save_conversation(conversation)
            self.send_sse_event("error", {"error": str(error), "conversation": conversation})

    def handle_add_message(self) -> None:
        payload = self.read_json_body()
        user_text = str(payload.get("message", "")).strip()
        if not user_text:
            raise ValueError("娑堟伅涓嶈兘涓虹┖")

        conversation_id = safe_conversation_id(payload.get("conversation_id"))
        conversation = load_conversation(conversation_id) if conversation_id else new_conversation(title_from_text(user_text))
        messages = conversation.setdefault("messages", [])
        messages.append(message_for_storage("user", user_text))
        if conversation.get("title") == NEW_CONVERSATION_TITLE:
            conversation["title"] = title_from_text(user_text)
        save_conversation(conversation)
        self.send_json({"conversation": conversation})

    def import_twin_from_records(
        self,
        *,
        records: list[dict[str, Any]],
        persona_name: str | None,
        target_sender: str | None,
        llm_summarize: bool,
        llm_summary_max_chars: int,
        prepend_chat_context: bool,
        llm_settings: dict[str, Any],
    ) -> dict[str, Any]:
        with NamedTemporaryFile("w", encoding="utf-8", suffix=".json", delete=False) as temp_file:
            json.dump(records, temp_file, ensure_ascii=False, indent=2)
            temp_file.flush()
            temp_path = temp_file.name

        try:
            index = twin_core.import_profile(
                temp_path,
                persona_name=persona_name,
                target_sender=target_sender,
            )
        finally:
            try:
                Path(temp_path).unlink(missing_ok=True)
            except OSError:
                pass

        profile = index.get("profile", {}) if isinstance(index, dict) else {}
        llm_summary = ""
        if llm_summarize and isinstance(profile, dict):
            voice = profile.get("voice") if isinstance(profile.get("voice"), dict) else {}
            prompt_payload = {
                "summary": profile.get("summary", ""),
                "tone_tags": voice.get("tone_tags", []),
                "signature_phrases": voice.get("signature_phrases", []),
                "common_topics": voice.get("common_topics", []),
                "style_rules": voice.get("style_rules", []),
                "stats": profile.get("stats", {}),
                "persona_name": profile.get("persona_name", ""),
            }
            base_url = str(llm_settings.get("base_url") or DEFAULT_BASE_URL)
            api_key = str(llm_settings.get("api_key") or DEFAULT_API_KEY)
            model = str(llm_settings.get("model") or DEFAULT_MODEL)
            llm_messages = [
                {
                    "role": "system",
                    "content": "You summarize persona profiles from structured extraction. Be concise and do not invent facts.",
                },
                {
                    "role": "user",
                    "content": (
                        "Summarize into 4 short sections:\n"
                        "1) one-line persona\n2) tone and rhythm\n3) common topics/preferences\n4) imitation tips\n"
                        f"Within {llm_summary_max_chars} Chinese characters.\n\n"
                        "Principles: Extract only what is directly stated. Do not infer. If no evidence, write 'insufficient material'.\n"
                        "Be gentle and respectful with emotional details.\n\n"
                        "From the raw material, identify:\n"
                        "- Key milestones (birthdays, buying car/house, etc.)\n"
                        "- Shared habits (weekend routines, evening rituals, co-watched shows, co-played games, shared hobbies)\n"
                        "- Exclusive rituals (daily goodnight texts, anniversary celebrations)\n"
                        "- Inside jokes, nicknames, secret codes, memes only you understand\n\n"
                        + json.dumps(prompt_payload, ensure_ascii=False)
                    ),
                },
            ]
            if prepend_chat_context:
                chunks = index.get("chunks") if isinstance(index, dict) else []
                sample_lines: list[str] = []
                if isinstance(chunks, list):
                    for chunk in chunks[:80]:
                        if not isinstance(chunk, dict):
                            continue
                        sender = str(chunk.get("sender", "unknown"))
                        content = str(chunk.get("content", "")).strip().replace("\n", " ")
                        if not content:
                            continue
                        sample_lines.append(f"{sender}: {content}")
                chat_context = "Chat history snippets for style reference:\n" + (
                    "\n".join(sample_lines) if sample_lines else "none"
                )
                llm_messages.insert(0, {"role": "user", "content": chat_context})
            llm_summary = complete_openai_once(
                messages=llm_messages,
                api_key=api_key,
                base_url=base_url,
                model=model,
                temperature=0.2,
                max_tokens=self.max_tokens_from_chars(llm_summary_max_chars),
            )
            profile = twin_core.apply_llm_summary_to_profile(profile, llm_summary)
            twin_core.write_json(twin_core.SKILL_PATH, profile)
            index["profile"] = profile
            twin_core.write_json(twin_core.MEMORY_INDEX_PATH, index)

        return {
            "ok": True,
            "status": self.get_twin_status(),
            "profile": profile,
            "llm_summary": llm_summary,
            "imported_count": len(records),
        }

    def handle_twin_import(self) -> None:
        payload = self.read_json_body()
        persona_name = str(payload.get("persona_name") or "").strip() or None
        target_sender = str(payload.get("target_sender") or "").strip() or None
        raw_json = str(payload.get("raw_json") or "").strip()
        source_path = str(payload.get("source_path") or "").strip()
        llm_summarize = self.parse_bool(payload.get("llm_summarize", False))
        llm_summary_max_chars = self.parse_summary_max_chars(payload.get("llm_summary_max_chars", 220))
        prepend_chat_context = self.parse_bool(payload.get("prepend_chat_context", False))
        llm_settings = payload.get("settings") if isinstance(payload.get("settings"), dict) else {}

        if raw_json:
            try:
                parsed = json.loads(raw_json)
            except json.JSONDecodeError as error:
                raise ValueError(f"绮樿创鍐呭涓嶆槸鍚堟硶 JSON: {error}") from error
            if not isinstance(parsed, list):
                raise ValueError("绮樿创鍐呭蹇呴』鏄?messages_export.json 杩欑鏁扮粍鏍煎紡")
            result = self.import_twin_from_records(
                records=parsed,
                persona_name=persona_name,
                target_sender=target_sender,
                llm_summarize=llm_summarize,
                llm_summary_max_chars=llm_summary_max_chars,
                prepend_chat_context=prepend_chat_context,
                llm_settings=llm_settings,
            )
        else:
            if not source_path:
                source_path = twin_core.default_source_path() or ""
            if not source_path:
                raise ValueError("缂哄皯 source_path锛屼笖鏈壘鍒伴粯璁ゆ簮鏂囦欢")
            parsed_records: list[dict[str, Any]] | None = None
            try:
                with Path(source_path).open("r", encoding="utf-8") as file:
                    parsed_source = json.load(file)
                if isinstance(parsed_source, list):
                    parsed_records = parsed_source
            except (OSError, json.JSONDecodeError, TypeError):
                parsed_records = None

            if parsed_records is not None:
                result = self.import_twin_from_records(
                    records=parsed_records,
                    persona_name=persona_name,
                    target_sender=target_sender,
                    llm_summarize=llm_summarize,
                    llm_summary_max_chars=llm_summary_max_chars,
                    prepend_chat_context=prepend_chat_context,
                    llm_settings=llm_settings,
                )
            else:
                index = twin_core.import_profile(
                    source_path,
                    persona_name=persona_name,
                    target_sender=target_sender,
                )
                profile = index.get("profile", {}) if isinstance(index, dict) else {}
                result = {
                    "ok": True,
                    "status": self.get_twin_status(),
                    "profile": profile,
                    "llm_summary": "",
                }

        self.send_json(result)

    def handle_twin_fetch_import(self) -> None:
        payload = self.read_json_body()
        persona_name = str(payload.get("persona_name") or "").strip() or None
        target_sender = str(payload.get("target_sender") or "").strip() or None
        llm_summarize = self.parse_bool(payload.get("llm_summarize", False))
        llm_summary_max_chars = self.parse_summary_max_chars(payload.get("llm_summary_max_chars", 220))
        prepend_chat_context = self.parse_bool(payload.get("prepend_chat_context", False))
        llm_settings = payload.get("settings") if isinstance(payload.get("settings"), dict) else {}
        fetch_config = payload.get("fetch_config") if isinstance(payload.get("fetch_config"), dict) else {}

        url = str(fetch_config.get("url") or "").strip()
        if not url:
            raise ValueError("缺少抓取 URL")
        username = str(fetch_config.get("username") or "wxid_xxx").strip()
        start_db = str(fetch_config.get("start_db") or "message_0.db").strip() or "message_0.db"
        pages = max(1, min(200, self.parse_non_negative_int(fetch_config.get("pages"), 10) or 10))
        size = max(1, min(500, self.parse_non_negative_int(fetch_config.get("size"), 100) or 100))

        headers_raw = fetch_config.get("headers")
        cookies_raw = fetch_config.get("cookies")
        sender_map_raw = fetch_config.get("sender_map")

        if headers_raw is None:
            headers = {}
        elif isinstance(headers_raw, dict):
            headers = {str(k): str(v) for k, v in headers_raw.items()}
        else:
            raise ValueError("headers 必须是 JSON 对象")

        if cookies_raw is None:
            cookies = {}
        elif isinstance(cookies_raw, dict):
            cookies = {str(k): str(v) for k, v in cookies_raw.items()}
        else:
            raise ValueError("cookies 必须是 JSON 对象")

        if sender_map_raw is None:
            sender_map = {}
        elif isinstance(sender_map_raw, dict):
            sender_map = {str(k): str(v) for k, v in sender_map_raw.items()}
        else:
            raise ValueError("sender_map 必须是 JSON 对象")

        started = time.time()
        try:
            fetched = downloadchatmsg_v2.download_and_prepare(
                url=url,
                headers=headers,
                cookies=cookies,
                sender_map=sender_map,
                pages=pages,
                size=size,
                username=username,
                start_db=start_db,
                verify=False,
                timeout=20,
            )
        except Exception as error:  # noqa: BLE001
            raise RuntimeError(f"抓取失败: {error}") from error

        records = fetched.get("raw") if isinstance(fetched, dict) else []
        if not isinstance(records, list):
            records = []
        if not records:
            raise RuntimeError("抓取成功但无可导入消息")

        result = self.import_twin_from_records(
            records=records,
            persona_name=persona_name,
            target_sender=target_sender,
            llm_summarize=llm_summarize,
            llm_summary_max_chars=llm_summary_max_chars,
            prepend_chat_context=prepend_chat_context,
            llm_settings=llm_settings,
        )
        result["fetch"] = {
            "url": url,
            "username": username,
            "start_db": start_db,
            "pages": pages,
            "size": size,
            "fetched_count": int(fetched.get("fetched_count", len(records))),
            "raw_count": len(records),
            "merged_count": len(fetched.get("merged") or []),
            "duration_ms": round((time.time() - started) * 1000),
            "headers": mask_sensitive_headers(headers),
            "cookies": mask_sensitive_cookies(cookies),
        }
        self.send_json(result)

    def handle_create_checkpoint(self) -> None:
        payload = self.read_json_body()
        conversation_id = safe_conversation_id(payload.get("conversation_id"))
        if not conversation_id:
            raise ValueError("缂哄皯 conversation_id")
        conversation = load_conversation(conversation_id)
        label = str(payload.get("label") or "").strip() or None
        checkpoint = twin_core.create_checkpoint(conversation, label=label)
        self.send_json({"ok": True, "checkpoint": checkpoint})

    def handle_restore_checkpoint(self) -> None:
        payload = self.read_json_body()
        checkpoint_id = str(payload.get("checkpoint_id") or "").strip()
        if not checkpoint_id:
            raise ValueError("缂哄皯 checkpoint_id")
        conversation = twin_core.restore_checkpoint(checkpoint_id)
        save_conversation(conversation)
        self.send_json({"ok": True, "conversation": conversation})

    def get_twin_status(self) -> dict[str, Any]:
        state = twin_core.load_state()
        skill = twin_core.load_skill() or {}
        memory_index = twin_core.load_memory_index() or {}
        chunks = memory_index.get("chunks") if isinstance(memory_index, dict) else []
        return {
            "state": state,
            "agents": [
                {"name": "memory_steward", "role": "长期记忆检索 + Checkpoint 管理"},
                {"name": "style_actor", "role": "画像加载 + 风格化回复生成"},
            ],
            "profile": {
                "persona_name": skill.get("persona_name", ""),
                "target_sender": skill.get("target_sender", ""),
                "summary": skill.get("summary", ""),
                "generated_at": skill.get("generated_at", ""),
            },
            "memory_count": len(chunks) if isinstance(chunks, list) else 0,
            "ready": bool(skill and isinstance(chunks, list) and len(chunks) > 0),
        }

    def read_json_body(self) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length", "0"))
        if length <= 0:
            return {}
        raw = self.rfile.read(length).decode("utf-8")
        try:
            data = json.loads(raw)
        except json.JSONDecodeError as error:
            raise ValueError("Invalid JSON request body.") from error
        if not isinstance(data, dict):
            raise ValueError("JSON 璇锋眰浣撳繀椤绘槸瀵硅薄")
        return data

    def serve_static(self, path: Path) -> None:
        resolved = path.resolve()
        if STATIC_DIR.resolve() not in resolved.parents and resolved != (STATIC_DIR / "index.html").resolve():
            self.send_error_json(HTTPStatus.FORBIDDEN, "Forbidden")
            return
        if not resolved.exists() or not resolved.is_file():
            self.send_error_json(HTTPStatus.NOT_FOUND, "Not found")
            return
        content = resolved.read_bytes()
        self.send_response(HTTPStatus.OK)
        self.send_cors_headers()
        self.send_header("Content-Type", MIME_TYPES.get(resolved.suffix, "application/octet-stream"))
        self.send_header("Content-Length", str(len(content)))
        self.end_headers()
        self.wfile.write(content)

    def start_sse_response(self) -> None:
        self.send_response(HTTPStatus.OK)
        self.send_cors_headers()
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "keep-alive")
        self.end_headers()

    def send_sse_event(self, event: str, payload: Any) -> None:
        data = json.dumps(payload, ensure_ascii=False)
        body = f"event: {event}\ndata: {data}\n\n".encode("utf-8")
        self.wfile.write(body)
        self.wfile.flush()

    def send_json(self, payload: Any, status: HTTPStatus = HTTPStatus.OK) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_cors_headers()
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def send_error_json(self, status: HTTPStatus, message: str) -> None:
        self.send_json({"error": message}, status)

    def send_cors_headers(self) -> None:
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, DELETE, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, Authorization")

    @staticmethod
    def get_query_value(query: str, key: str) -> str:
        if not query:
            return ""
        for pair in query.split("&"):
            if "=" not in pair:
                continue
            raw_key, raw_value = pair.split("=", 1)
            if raw_key == key:
                return raw_value
        return ""

    @staticmethod
    def parse_temperature(value: Any) -> float:
        try:
            temperature = float(value)
        except (TypeError, ValueError):
            return 0.7
        return min(2.0, max(0.0, temperature))

    @staticmethod
    def parse_context_k(value: Any) -> int:
        try:
            k = int(value)
        except (TypeError, ValueError):
            return 40
        return max(1, min(400, k))

    @staticmethod
    def parse_summary_max_chars(value: Any) -> int:
        try:
            chars = int(value)
        except (TypeError, ValueError):
            return 220
        return max(60, min(5000, chars))

    @staticmethod
    def parse_non_negative_int(value: Any, default: int = 0) -> int:
        try:
            number = int(value)
        except (TypeError, ValueError):
            return default
        return max(0, number)

    @staticmethod
    def max_tokens_from_chars(chars: int) -> int:
        return max(32, min(2048, int(chars * 1.5)))

    @staticmethod
    def parse_bool(value: Any) -> bool:
        if isinstance(value, bool):
            return value
        if isinstance(value, str):
            return value.strip().lower() not in {"0", "false", "disabled", "off", "no"}
        return bool(value)

    @staticmethod
    def parse_reasoning_effort(value: Any) -> str:
        effort = str(value or "high").strip().lower()
        if effort == "max":
            return "max"
        if effort == "xhigh":
            return "max"
        return "high"

    def log_message(self, format_text: str, *args: Any) -> None:
        print(f"[{time.strftime('%H:%M:%S')}] {self.address_string()} {format_text % args}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="OpenAI-compatible local chat app")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=9666)
    return parser.parse_args()


def main() -> None:
    ensure_dirs()
    twin_core.bootstrap_default_profile()
    args = parse_args()
    server = ThreadingHTTPServer((args.host, args.port), ChatHandler)
    print(f"AI chat app running at http://{args.host}:{args.port}")
    print("Use OPENAI_API_KEY, OPENAI_BASE_URL, OPENAI_MODEL or set them in the web settings panel.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")
    finally:
        server.server_close()


if __name__ == "__main__":
    main()

