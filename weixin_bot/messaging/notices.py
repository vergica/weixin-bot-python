"""错误通知 — 出站失败时回传中文提示给用户.

对应原版 messaging/error-notice.ts. fire-and-forget, 通知本身失败不抛.
"""

from __future__ import annotations

import logging

from weixin_bot.messaging.send import send_text

logger = logging.getLogger(__name__)


async def send_error_notice(
    *,
    to: str,
    text: str,
    base_url: str,
    token: str,
    context_token: str = "",
) -> None:
    """给用户发一条错误提示. 自身失败只记录日志, 绝不抛异常."""
    try:
        await send_text(
            to=to, text=text,
            base_url=base_url, token=token, context_token=context_token,
        )
    except Exception:
        logger.warning("send_error_notice failed to=%s", to)
