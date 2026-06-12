"""出站消息发送 — 构造 sendMessage 请求.

对应原版 messaging/send.ts.
"""

from __future__ import annotations

import json
import logging
import os
import base64
from typing import Optional

from weixin_bot.api.client import api_post

logger = logging.getLogger(__name__)

CHANNEL_VERSION = "0.1.0"

# proto 常量
MSG_TYPE_BOT = 2
MSG_STATE_FINISH = 2
ITEM_TYPE_TEXT = 1

# 微信单条消息最大长度 — 对齐 nanobot WEIXIN_MAX_MESSAGE_LEN
WEIXIN_MAX_MESSAGE_LEN = 4000


def split_message(text: str, max_len: int = WEIXIN_MAX_MESSAGE_LEN) -> list[str]:
    """将长文本拆分为多条消息, 尽量在段落边界切分.

    策略:
    1. 按空行 (双换行) 拆分为段落
    2. 按单换行拆分为句子
    3. 按字符硬切 (最后手段)

    同时保护代码块 (``` ... ```) 不被拆分.
    """
    if len(text) <= max_len:
        return [text] if text else []

    chunks: list[str] = []

    # 按空行拆分段落
    paragraphs = text.split("\n\n")
    current = ""
    in_fence = False

    for para in paragraphs:
        if not current:
            current = para
            # 追踪代码块状态
            in_fence = (current.count("```") % 2) != 0
            continue

        if len(current) + 2 + len(para) <= max_len:
            current += "\n\n" + para
            # 更新代码块状态
            fence_count = current.count("```")
            in_fence = (fence_count % 2) != 0
        else:
            # 当前段落放不下 → 先尝试按行拆分
            if len(current) > max_len:
                chunks.extend(_split_by_lines(current, max_len))
            else:
                chunks.append(current)
            current = para
            in_fence = (current.count("```") % 2) != 0

    # 最后一个段落
    if current:
        if len(current) > max_len:
            chunks.extend(_split_by_lines(current, max_len))
        else:
            chunks.append(current)

    return chunks


def _split_by_lines(text: str, max_len: int) -> list[str]:
    """按换行将文本拆分为 fit max_len 的块."""
    lines = text.split("\n")
    result: list[str] = []
    current = ""
    in_fence = False

    for line in lines:
        if line.startswith("```"):
            in_fence = not in_fence

        if not current:
            current = line
            continue

        if len(current) + 1 + len(line) <= max_len:
            current += "\n" + line
        else:
            if len(current) > max_len:
                # 硬切 — 逐字符
                result.extend(_split_hard(current, max_len))
            else:
                result.append(current)
            current = line

    if current:
        if len(current) > max_len:
            result.extend(_split_hard(current, max_len))
        else:
            result.append(current)

    return result


def _split_hard(text: str, max_len: int) -> list[str]:
    """逐字符硬切 (最后手段)."""
    result: list[str] = []
    for i in range(0, len(text), max_len):
        result.append(text[i:i + max_len])
    return result


def _gen_client_id() -> str:
    """随机 client_id, 用于追踪消息."""
    raw = os.urandom(16)
    return "weixin-bot-" + base64.b32encode(raw).decode().rstrip("=").lower()


def _check_response(raw: str, endpoint: str) -> dict:
    """解析 API 响应, 检查 ret/errcode, 失败时 raise RuntimeError."""
    data = json.loads(raw)
    ret = data.get("ret", 0)
    errcode = data.get("errcode", 0)
    if (ret is not None and ret != 0) or (errcode is not None and errcode != 0):
        msg = data.get("errmsg", "")
        raise RuntimeError(
            f"{endpoint} error ret={ret} errcode={errcode}{': ' + msg if msg else ''}"
        )
    return data


async def send_text(
    *,
    to: str,
    text: str,
    base_url: str,
    token: str,
    context_token: str = "",
) -> dict:
    """发送纯文本消息. 超过 4000 字自动分片.

    Returns {'messageId': <last_client_id>}.
    Raises RuntimeError if the API returns an error.
    """
    chunks = split_message(text)
    if not chunks:
        return {"messageId": ""}

    last_id = ""
    for chunk in chunks:
        client_id = _gen_client_id()
        last_id = client_id

        body = {
            "msg": {
                "from_user_id": "",
                "to_user_id": to,
                "client_id": client_id,
                "message_type": MSG_TYPE_BOT,
                "message_state": MSG_STATE_FINISH,
                "item_list": [
                    {"type": ITEM_TYPE_TEXT, "text_item": {"text": chunk}}
                ],
                "context_token": context_token or "",
            },
            "base_info": {"channel_version": CHANNEL_VERSION},
        }

        raw = await api_post(
            base_url=base_url,
            endpoint="ilink/bot/sendmessage",
            body=json.dumps(body, ensure_ascii=False),
            token=token,
            timeout=15.0,
        )
        _check_response(raw, "sendmessage")

    return {"messageId": last_id}
