"""长轮询引擎 — getUpdates 循环, 负责拉消息.

对应原版 monitor/monitor.ts, 去掉 OpenClaw 框架耦合.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from collections import OrderedDict
from pathlib import Path
from typing import Callable, Awaitable

from weixin_bot.api.client import WeixinApiClient
from weixin_bot.auth.accounts import STATE_DIR as DEFAULT_STATE_DIR
from weixin_bot.messaging.context_token import ContextTokenCache

logger = logging.getLogger(__name__)

CHANNEL_VERSION = "0.1.0"

# 轮询错误退避 — 对齐 nanobot monitor.ts
MAX_CONSECUTIVE_FAILURES = 3
BACKOFF_DELAY_S = 30
RETRY_DELAY_S = 2


class SessionExpired(Exception):
    """errcode == -14, token 已失效, 需重新登录."""


# session 暂停时长 (秒) — 对齐 nanobot SESSION_PAUSE_DURATION_S
SESSION_PAUSE_DURATION_S = 60 * 60  # 1 小时


class MonitorLoop:
    """getUpdates 长轮询循环.

    Usage:
        loop = MonitorLoop(
            base_url="https://...",
            token="...",
            account_id="my-bot",
            on_message=handle_message,
        )
        asyncio.create_task(loop.run())
        # ... later ...
        await loop.stop()
    """

    def __init__(
        self,
        *,
        base_url: str,
        token: str,
        account_id: str,
        state_dir: str | Path | None = None,
        on_message: Callable[[dict], Awaitable[None]],
        allow_from: list[str] | None = None,
    ):
        self._base_url = base_url
        self._token = token
        self._account_id = account_id
        self._on_message = on_message
        self._allow_from = set(allow_from or [])
        self._stop = asyncio.Event()
        self._current_task: asyncio.Task | None = None
        _dir = Path(state_dir) if state_dir else DEFAULT_STATE_DIR
        self._sync_path = _dir / "accounts" / f"{account_id}.sync.json"
        # context_token 缓存 — 自动在接收时缓存, 发送前过期刷新
        self.ctx_tokens = ContextTokenCache()
        # Session paused state (自愈: errcode -14 后暂停 1h 再恢复)
        self._session_pause_until: float = 0.0
        # 消息去重 — 最近 1000 条 message_id
        self._processed_ids: OrderedDict[str, None] = OrderedDict()
        # 持久化 HTTP 客户端 — 连接复用
        self._api: WeixinApiClient | None = None

    # ------------------------------------------------------------------
    # public
    # ------------------------------------------------------------------

    async def run(self) -> None:
        """启动长轮询. 阻塞直到 stop() 被调用.

        errcode -14 暂停 1 小时后自动恢复, 不再抛 SessionExpired.
        stop() 会 cancel 飞行中的 HTTP 请求, 实现即时退出.
        """
        # 创建持久化 HTTP 客户端 (连接复用)
        from weixin_bot.config import get as _config_get
        route_tag = str(_config_get("route_tag") or "").strip()
        self._api = WeixinApiClient(
            base_url=self._base_url,
            token=self._token,
            timeout=45.0,  # 略大于最长轮询 timeout
            route_tag=route_tag,
        )
        await self._api.connect()

        try:
            await self._run_loop()
        finally:
            await self._api.close()
            self._api = None

    async def _run_loop(self) -> None:
        """实际轮询循环 — 在 run() 内被调用, 持有复用的 WeixinApiClient."""
        await self._notify_start()
        buf = self._load_buf()
        timeout = 35.0
        consecutive_failures = 0

        while not self._stop.is_set():
            # ---- 检查 session 暂停 ----
            remaining = self._session_pause_remaining()
            if remaining > 0:
                mins = max((remaining + 59) // 60, 1)
                logger.warning(
                    "Session paused — %d min remaining (errcode -14)", mins
                )
                await self._sleep(remaining)
                if self._stop.is_set():
                    break

            self._current_task = asyncio.create_task(
                self._get_updates(buf, timeout)
            )
            try:
                resp = await self._current_task
                self._current_task = None
            except asyncio.CancelledError:
                break
            except Exception:
                consecutive_failures += 1
                delay = BACKOFF_DELAY_S if consecutive_failures >= MAX_CONSECUTIVE_FAILURES else RETRY_DELAY_S
                logger.warning(
                    "getUpdates error (consecutive=%d), retry in %ds",
                    consecutive_failures, delay, exc_info=True,
                )
                if consecutive_failures >= MAX_CONSECUTIVE_FAILURES:
                    consecutive_failures = 0  # reset counter after backoff
                await self._sleep(delay)
                continue

            # 业务错误
            if resp.get("errcode") == -14:
                consecutive_failures = 0  # 预期错误, 不计数
                self._pause_session()
                remaining = self._session_pause_remaining()
                logger.warning(
                    "Session expired (errcode -14). Pausing %d min.",
                    max((remaining + 59) // 60, 1),
                )
                continue

            if resp.get("ret", 0) != 0:
                consecutive_failures += 1
                delay = BACKOFF_DELAY_S if consecutive_failures >= MAX_CONSECUTIVE_FAILURES else RETRY_DELAY_S
                logger.warning(
                    "getUpdates ret=%d errcode=%s (consecutive=%d), retry in %ds",
                    resp.get("ret"), resp.get("errcode"),
                    consecutive_failures, delay,
                )
                if consecutive_failures >= MAX_CONSECUTIVE_FAILURES:
                    consecutive_failures = 0
                await self._sleep(delay)
                continue

            # 成功 — 重置计数器
            consecutive_failures = 0

            # 成功
            buf = resp.get("get_updates_buf", buf)
            self._save_buf(buf)

            if "longpolling_timeout_ms" in resp:
                timeout = resp["longpolling_timeout_ms"] / 1000

            for msg in resp.get("msgs", []) or []:
                # 跳过 bot 自己的消息
                if msg.get("message_type") == 2:
                    continue

                # 消息去重
                msg_id = str(msg.get("message_id", "") or "")
                if not msg_id:
                    msg_id = f"{msg.get('from_user_id', '')}_{msg.get('create_time_ms', '')}"
                if not msg_id.strip("_"):
                    continue  # 无法生成 ID, 跳过
                if msg_id in self._processed_ids:
                    logger.debug("Duplicate message %s, skipping", msg_id)
                    continue
                self._processed_ids[msg_id] = None
                # 限制内存: 最多保留 1000 条
                while len(self._processed_ids) > 1000:
                    self._processed_ids.popitem(last=False)

                # 缓存 context_token (后续发送前可自动刷新)
                from_user = msg.get("from_user_id", "") or ""
                ctx = msg.get("context_token", "") or ""
                if from_user and ctx:
                    self.ctx_tokens.cache(from_user, ctx)

                # 访问控制
                if not self._is_allowed(from_user):
                    logger.warning(
                        "Access denied for %s (not in allow_from)", from_user
                    )
                    # 尝试发送提示消息 (如果有 context_token)
                    if ctx:
                        try:
                            from weixin_bot.messaging.send import send_text as _send_text
                            await _send_text(
                                to=from_user,
                                text="[Access Denied] You are not authorized. Contact the bot owner for access.",
                                base_url=self._base_url,
                                token=self._token,
                                context_token=ctx,
                            )
                        except Exception:
                            pass
                    continue

                try:
                    await self._on_message(msg)
                except Exception:
                    logger.exception("on_message failed")

        await self._notify_stop()

    async def stop(self) -> None:
        """发送停止信号并 cancel 飞行中的 getUpdates 请求, 即时退出."""
        self._stop.set()
        if self._current_task and not self._current_task.done():
            self._current_task.cancel()

    # ------------------------------------------------------------------
    # internal
    # ------------------------------------------------------------------

    async def _get_updates(self, buf: str, timeout: float) -> dict:
        assert self._api is not None
        raw = await self._api.post(
            endpoint="ilink/bot/getupdates",
            body={
                "get_updates_buf": buf,
                "base_info": {"channel_version": CHANNEL_VERSION},
            },
            timeout=timeout,
        )
        return json.loads(raw)

    async def _notify_start(self) -> None:
        assert self._api is not None
        try:
            await self._api.post(
                endpoint="ilink/bot/msg/notifystart",
                body={"base_info": {"channel_version": CHANNEL_VERSION}},
                timeout=10.0,
            )
        except Exception:
            logger.warning("notifyStart failed (ignored)", exc_info=True)

    async def _notify_stop(self) -> None:
        assert self._api is not None
        try:
            await self._api.post(
                endpoint="ilink/bot/msg/notifystop",
                body={"base_info": {"channel_version": CHANNEL_VERSION}},
                timeout=10.0,
            )
        except Exception:
            logger.warning("notifyStop failed (ignored)", exc_info=True)

    # ------------------------------------------------------------------
    # access control
    # ------------------------------------------------------------------

    def _is_allowed(self, user_id: str) -> bool:
        """检查用户是否在白名单中. 空白名单 = 全部允许."""
        if not self._allow_from:
            return True
        return user_id in self._allow_from

    # ------------------------------------------------------------------
    # session pause (自愈)
    # ------------------------------------------------------------------

    def _pause_session(self) -> None:
        """暂停轮询 (errcode -14 时调用)."""
        self._session_pause_until = time.time() + SESSION_PAUSE_DURATION_S

    def _session_pause_remaining(self) -> int:
        """返回暂停剩余秒数 (0 表示无暂停)."""
        remaining = int(self._session_pause_until - time.time())
        if remaining <= 0:
            self._session_pause_until = 0.0
            return 0
        return remaining

    # ------------------------------------------------------------------
    # sync buf
    # ------------------------------------------------------------------

    def _load_buf(self) -> str:
        try:
            if self._sync_path.exists():
                data = json.loads(self._sync_path.read_text("utf-8"))
                buf = data.get("get_updates_buf", "")
                logger.info("Loaded sync buf (%d chars)", len(buf))
                return buf
        except Exception:
            logger.warning("Failed to load sync buf", exc_info=True)
        return ""

    def _save_buf(self, buf: str) -> None:
        try:
            self._sync_path.parent.mkdir(parents=True, exist_ok=True)
            self._sync_path.write_text(
                json.dumps({"get_updates_buf": buf}, ensure_ascii=False),
                "utf-8",
            )
        except Exception:
            logger.warning("Failed to save sync buf", exc_info=True)

    async def _sleep(self, seconds: float) -> None:
        """可中断的 sleep — stop() 时立刻醒来."""
        try:
            await asyncio.wait_for(self._stop.wait(), timeout=seconds)
        except asyncio.TimeoutError:
            pass
