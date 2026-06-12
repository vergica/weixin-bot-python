"""context_token 生命周期管理 — 缓存 + 自动刷新.

iLink context_token 在服务端 ~90-160s 无活动后过期. 如果 agent 处理
耗时较长 (AI 生成等), 回复时 token 已失效, 消息会静默丢失.

本模块提供自动刷新: 缓存时记录时间戳, 获取时若超过 60s 则调 getconfig 刷新.
"""

from __future__ import annotations

import logging
import time

from weixin_bot.messaging.typing import get_config

logger = logging.getLogger(__name__)

# 超过 60s 就尝试刷新 (服务端 ~90-160s 过期, 留余量)
CONTEXT_TOKEN_MAX_AGE_S = 60


class ContextTokenCache:
    """缓存每个用户的 context_token 并自动刷新."""

    def __init__(self):
        self._tokens: dict[str, str] = {}
        self._timestamps: dict[str, float] = {}

    # ------------------------------------------------------------------
    # public
    # ------------------------------------------------------------------

    def cache(self, user_id: str, ctx_token: str) -> None:
        """记录最新的 context_token 和时间戳."""
        if not user_id or not ctx_token:
            return
        self._tokens[user_id] = ctx_token
        self._timestamps[user_id] = time.time()

    async def get(
        self,
        *,
        user_id: str,
        base_url: str,
        auth_token: str,
    ) -> str:
        """获取 context_token. 过期时自动调 getconfig 刷新.

        Args:
            user_id:     用户 ID (from_user_id)
            base_url:    API 基础 URL
            auth_token:  Bot token (Authorization Bearer)

        Returns:
            context_token 字符串; 无缓存或刷新失败时返回空字符串.
        """
        ctx = self._tokens.get(user_id, "")
        if not ctx:
            return ""

        age = time.time() - self._timestamps.get(user_id, 0)
        if age < CONTEXT_TOKEN_MAX_AGE_S:
            return ctx  # 还新鲜

        # 过期 — 尝试刷新
        logger.debug(
            "context_token for %s is %.0fs old, refreshing via getconfig...",
            user_id,
            age,
        )
        try:
            data = await get_config(
                base_url=base_url,
                token=auth_token,
                ilink_user_id=user_id,
                context_token=ctx,
            )
            if data.get("ret", 0) == 0:
                new_token = str(data.get("context_token", "") or "")
                if new_token and new_token != ctx:
                    logger.info(
                        "context_token refreshed for %s (age %.0fs -> fresh)",
                        user_id,
                        age,
                    )
                    self._tokens[user_id] = new_token
                    self._timestamps[user_id] = time.time()
                    return new_token
                else:
                    # token 没变 (可能刚刷新过), 更新时间戳避免重复请求
                    self._timestamps[user_id] = time.time()
                    return ctx
        except Exception:
            # 刷新失败 — 返回旧 token (可能还能用)
            logger.debug(
                "context_token refresh failed for %s, using cached token",
                user_id,
            )

        # 刷新失败也更新时间戳, 避免每次都重试 getconfig
        self._timestamps[user_id] = time.time()
        return ctx

    def clear(self, user_id: str = "") -> None:
        """清除缓存. user_id 为空则清空全部."""
        if user_id:
            self._tokens.pop(user_id, None)
            self._timestamps.pop(user_id, None)
        else:
            self._tokens.clear()
            self._timestamps.clear()
