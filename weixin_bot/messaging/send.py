"""出站消息发送 — 构造 sendMessage 请求.

对应原版 messaging/send.ts.
"""

from __future__ import annotations

import json
import os
import base64
from typing import Optional

from weixin_bot.api.client import api_post

CHANNEL_VERSION = "0.1.0"

# proto 常量
MSG_TYPE_BOT = 2
MSG_STATE_FINISH = 2
ITEM_TYPE_TEXT = 1


def _gen_client_id() -> str:
    """随机 client_id, 用于追踪消息."""
    raw = os.urandom(16)
    return "weixin-bot-" + base64.b32encode(raw).decode().rstrip("=").lower()


async def send_text(
    *,
    to: str,
    text: str,
    base_url: str,
    token: str,
    context_token: str = "",
) -> dict:
    """发送纯文本消息, 返回 {'messageId': ...}."""
    client_id = _gen_client_id()

    body = {
        "msg": {
            "from_user_id": "",
            "to_user_id": to,
            "client_id": client_id,
            "message_type": MSG_TYPE_BOT,
            "message_state": MSG_STATE_FINISH,
            "item_list": [
                {"type": ITEM_TYPE_TEXT, "text_item": {"text": text}}
            ],
            "context_token": context_token or "",
        },
        "base_info": {"channel_version": CHANNEL_VERSION},
    }

    await api_post(
        base_url=base_url,
        endpoint="ilink/bot/sendmessage",
        body=json.dumps(body, ensure_ascii=False),
        token=token,
        timeout=15.0,
    )
    return {"messageId": client_id}
