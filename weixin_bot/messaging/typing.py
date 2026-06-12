"""打字指示器 — getConfig 拿 typing_ticket, sendTyping 发送状态.

对应原版 api/api.ts getConfig + sendTyping.
"""

from __future__ import annotations

import asyncio
import json
import logging
from contextlib import suppress

from weixin_bot.api.client import api_post

logger = logging.getLogger(__name__)

CHANNEL_VERSION = "0.1.0"

# proto TypingStatus
TYPING = 1
CANCEL = 2

# keepalive 间隔 — 对齐 nanobot TYPING_KEEPALIVE_INTERVAL_S
TYPING_KEEPALIVE_INTERVAL_S = 5


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


# ---------------------------------------------------------------------------
# TypingIndicator — async context manager with automatic keepalive
# ---------------------------------------------------------------------------

class TypingIndicator:
    """打字指示器 — async context manager, 自动 keepalive.

    Usage:
        async with TypingIndicator(
            base_url=..., token=..., ilink_user_id=..., context_token=...
        ) as typing:
            # 处理消息 (agent 思考/生成回复)
            # keepalive 自动每 5s 发送一次 TYPING
            await send_text(...)
        # 退出时自动发送 CANCEL

    如果 get_config 失败或 typing_ticket 为空, 静默跳过 (不阻塞主流程).
    """

    def __init__(
        self,
        *,
        base_url: str,
        token: str,
        ilink_user_id: str,
        context_token: str = "",
    ):
        self._base_url = base_url
        self._token = token
        self._user_id = ilink_user_id
        self._ctx_token = context_token
        self._ticket: str = ""
        self._keepalive_task: asyncio.Task | None = None
        self._stop_event = asyncio.Event()

    async def __aenter__(self) -> "TypingIndicator":
        # 获取 ticket
        try:
            cfg = await get_config(
                base_url=self._base_url,
                token=self._token,
                ilink_user_id=self._user_id,
                context_token=self._ctx_token,
            )
            self._ticket = str(cfg.get("typing_ticket", "") or "")
        except Exception:
            logger.debug("TypingIndicator: get_config failed, skipping", exc_info=True)
            return self

        if not self._ticket:
            return self

        # 发送 TYPING
        try:
            await send_typing(
                base_url=self._base_url,
                token=self._token,
                ilink_user_id=self._user_id,
                typing_ticket=self._ticket,
                status=TYPING,
            )
        except Exception:
            logger.debug("TypingIndicator: send_typing(TYPING) failed", exc_info=True)
            return self

        # 启动 keepalive
        self._keepalive_task = asyncio.create_task(self._keepalive_loop())
        return self

    async def __aexit__(self, *args) -> None:
        # 停止 keepalive
        if self._keepalive_task:
            self._stop_event.set()
            self._keepalive_task.cancel()
            with suppress(asyncio.CancelledError):
                await self._keepalive_task
            self._keepalive_task = None

        # 发送 CANCEL
        if self._ticket:
            with suppress(Exception):
                await send_typing(
                    base_url=self._base_url,
                    token=self._token,
                    ilink_user_id=self._user_id,
                    typing_ticket=self._ticket,
                    status=CANCEL,
                )

    async def _keepalive_loop(self) -> None:
        """每 5 秒发一次 TYPING, 保持指示器不消失."""
        try:
            while not self._stop_event.is_set():
                await asyncio.sleep(TYPING_KEEPALIVE_INTERVAL_S)
                if self._stop_event.is_set():
                    break
                with suppress(Exception):
                    await send_typing(
                        base_url=self._base_url,
                        token=self._token,
                        ilink_user_id=self._user_id,
                        typing_ticket=self._ticket,
                        status=TYPING,
                    )
        finally:
            pass
