from __future__ import annotations

import argparse
import json
from datetime import datetime
from typing import Any, Callable

import requests
import urllib3

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

DEFAULT_URL = "http://127.0.0.1:9527/api/msg/msgs"
DEFAULT_HEADERS = {
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "zh-CN,zh;q=0.9",
    "Authorization": "bearer <replace-with-token>",
    "Cache-Control": "no-cache",
    "Connection": "keep-alive",
    "Content-Type": "application/json;charset=UTF-8",
    "CurrentSessionId": "1",
    "Origin": "http://127.0.0.1:9527",
    "Pragma": "no-cache",
    "Referer": "http://127.0.0.1:9527/session/1/comment/wxid_xxx",
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/147.0.0.0 Safari/537.36",
}
DEFAULT_COOKIES = {
    "pma_lang": "zh_CN",
}
DEFAULT_SENDER_MAP = {
    "wxid_assistant": "assistant",
    "wxid_user": "user",
}
DEFAULT_MSG_TYPES = [
    "text",
    "image",
    "video",
    "voice",
    "file",
    "transfer",
    "merge",
    "ref",
    "emoji",
    "system",
    "card_link",
]
DEFAULT_RAW_OUTPUT = "messages_export.json"
DEFAULT_MERGED_OUTPUT = "chat_merged.json"


def fetch_messages(
    *,
    url: str,
    headers: dict[str, str] | None = None,
    cookies: dict[str, str] | None = None,
    pages: int = 10,
    size: int = 100,
    username: str = "wxid_xxx",
    start_db: str = "message_0.db",
    msg_types: list[str] | None = None,
    verify: bool = False,
    timeout: int = 15,
    progress_callback: Callable[[int, int, int], None] | None = None,
) -> list[dict[str, Any]]:
    messages: list[dict[str, Any]] = []
    page_count = max(1, int(pages))
    page_size = max(1, int(size))
    types = msg_types or DEFAULT_MSG_TYPES

    for page_index in range(page_count):
        payload = {
            "username": username,
            "page": page_index + 1,
            "size": page_size,
            "start": page_index * page_size,
            "start_db": start_db,
            "msg_types": types,
            "search_text": "",
            "start_time": "",
            "end_time": "",
        }

        response = requests.post(
            url,
            headers=headers or {},
            cookies=cookies or {},
            json=payload,
            verify=verify,
            timeout=timeout,
        )
        response.raise_for_status()

        data = response.json()
        page_messages = data.get("messages", []) if isinstance(data, dict) else []
        if not isinstance(page_messages, list):
            page_messages = []

        messages.extend(page_messages)
        if progress_callback:
            progress_callback(page_index + 1, len(page_messages), len(messages))

    return messages


def normalize_messages(
    messages: list[dict[str, Any]],
    sender_map: dict[str, str] | None = None,
    unsupported_type_text: str = "unsupported_type",
) -> list[dict[str, Any]]:
    export_data: list[dict[str, Any]] = []
    sender_alias = sender_map or {}

    for item in reversed(messages):
        if not isinstance(item, dict):
            continue
        props = item.get("windows_v4_properties")
        if not isinstance(props, dict):
            continue

        sender_value = str(props.get("sender") or "")
        sender = sender_alias.get(sender_value, sender_value)
        local_type = int(props.get("local_type") or 0)
        content = str(props.get("message_content_data") or "")
        create_time = props.get("create_time")

        time_str = ""
        if create_time:
            try:
                time_str = datetime.fromtimestamp(float(create_time)).strftime("%Y-%m-%d %H:%M:%S")
            except (TypeError, ValueError, OSError):
                time_str = ""

        if local_type != 1:
            content = f"{unsupported_type_text}{local_type}"

        export_data.append(
            {
                "time": time_str,
                "sender": sender,
                "type": local_type,
                "content": content,
            }
        )

    return export_data


def merge_consecutive_messages(records: list[dict[str, Any]], sep: str = "\n") -> list[dict[str, Any]]:
    if not records:
        return []

    merged: list[dict[str, Any]] = []
    current = records[0].copy()

    for item in records[1:]:
        if item.get("sender") == current.get("sender"):
            current["content"] = str(current.get("content", "")) + sep + str(item.get("content", ""))
        else:
            merged.append(current)
            current = item.copy()

    merged.append(current)
    return merged


def download_and_prepare(
    *,
    url: str,
    headers: dict[str, str] | None,
    cookies: dict[str, str] | None,
    sender_map: dict[str, str] | None,
    pages: int,
    size: int,
    username: str,
    start_db: str,
    verify: bool = False,
    timeout: int = 15,
    progress_callback: Callable[[int, int, int], None] | None = None,
) -> dict[str, Any]:
    messages = fetch_messages(
        url=url,
        headers=headers,
        cookies=cookies,
        pages=pages,
        size=size,
        username=username,
        start_db=start_db,
        verify=verify,
        timeout=timeout,
        progress_callback=progress_callback,
    )
    export_data = normalize_messages(messages, sender_map=sender_map)
    merged_data = merge_consecutive_messages(export_data)
    return {
        "raw": export_data,
        "merged": merged_data,
        "fetched_count": len(messages),
    }


def write_json(path: str, data: Any) -> None:
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def parse_json_object(text: str, fallback: dict[str, str]) -> dict[str, str]:
    raw = (text or "").strip()
    if not raw:
        return fallback
    data = json.loads(raw)
    if not isinstance(data, dict):
        raise ValueError("expected JSON object")
    return {str(key): str(value) for key, value in data.items()}


def main() -> None:
    parser = argparse.ArgumentParser(description="Download and merge chat messages.")
    parser.add_argument("--url", default=DEFAULT_URL)
    parser.add_argument("--raw-output", default=DEFAULT_RAW_OUTPUT)
    parser.add_argument("--merged-output", default=DEFAULT_MERGED_OUTPUT)
    parser.add_argument("--pages", type=int, default=10)
    parser.add_argument("--size", type=int, default=100)
    parser.add_argument("--username", default="wxid_xxx")
    parser.add_argument("--start-db", default="message_0.db")
    parser.add_argument("--headers-json", default="")
    parser.add_argument("--cookies-json", default="")
    parser.add_argument("--sender-map-json", default="")
    args = parser.parse_args()

    try:
        headers = parse_json_object(args.headers_json, DEFAULT_HEADERS)
        cookies = parse_json_object(args.cookies_json, DEFAULT_COOKIES)
        sender_map = parse_json_object(args.sender_map_json, DEFAULT_SENDER_MAP)

        result = download_and_prepare(
            url=args.url,
            headers=headers,
            cookies=cookies,
            sender_map=sender_map,
            pages=args.pages,
            size=args.size,
            username=args.username,
            start_db=args.start_db,
            verify=False,
            timeout=15,
            progress_callback=lambda page, page_count, total: print(page, page_count, total),
        )

        print("request succeeded")
        print("messages count:", result["fetched_count"])
        print("-" * 60)

        write_json(args.raw_output, result["raw"])
        write_json(args.merged_output, result["merged"])

        print(f"exported raw data to {args.raw_output}")
        print(f"exported merged data to {args.merged_output}")
    except requests.exceptions.RequestException as error:
        print("request failed:", error)
    except json.JSONDecodeError as error:
        print("json parse failed:", error)
    except Exception as error:  # noqa: BLE001
        print("error:", error)


if __name__ == "__main__":
    main()

