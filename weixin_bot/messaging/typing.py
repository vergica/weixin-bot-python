"""打字指示器 — getConfig 拿 typing_ticket, sendTyping 发送状态.

对应原版 api/api.ts getConfig + sendTyping.
"""

from __future__ import annotations

import json
import logging

from weixin_bot.api.client import api_post

logger = logging.getLogger(__name__)

CHANNEL_VERSION = "0.1.0"

# proto TypingStatus
TYPING = 1
CANCEL = 2


# ---------------------------------------------------------------------------
# API 调用
# ---------------------------------------------------------------------------

async def get_config(
    *,
    base_url: str,
    token: str,
    ilink_user_id: str,
    context_token: str = "",
    timeout: float = 10.0,
) -> dict:
    """POST ilink/bot/getconfig, 获取用户配置 (含 typing_ticket).

    返回服务端原始 JSON: {ret, errmsg, typing_ticket, ...}
    typing_ticket 是 base64 字符串, 用于后续 send_typing.
    """
    body = json.dumps({
        "ilink_user_id": ilink_user_id,
        "context_token": context_token or "",
        "base_info": {"channel_version": CHANNEL_VERSION},
    })
    raw = await api_post(
        base_url=base_url,
        endpoint="ilink/bot/getconfig",
        body=body,
        token=token,
        timeout=timeout,
    )
    return json.loads(raw)


async def send_typing(
    *,
    base_url: str,
    token: str,
    ilink_user_id: str,
    typing_ticket: str,
    status: int = TYPING,
    timeout: float = 10.0,
) -> dict:
    """POST ilink/bot/sendtyping, 发送/取消打字指示器.

    status: TYPING(1) 开始显示, CANCEL(2) 取消.
    返回服务端响应: {ret, errmsg}.
    """
    body = json.dumps({
        "ilink_user_id": ilink_user_id,
        "typing_ticket": typing_ticket,
        "status": status,
        "base_info": {"channel_version": CHANNEL_VERSION},
    })
    raw = await api_post(
        base_url=base_url,
        endpoint="ilink/bot/sendtyping",
        body=body,
        token=token,
        timeout=timeout,
    )
    return json.loads(raw)
