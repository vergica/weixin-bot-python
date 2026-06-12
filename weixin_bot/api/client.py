"""Weixin iLink HTTP API client — 核心请求函数.

对应原版 src/api/api.ts 的 apiPostFetch / apiGetFetch.
"""

from __future__ import annotations

import base64
import json
import logging
import os
from typing import Optional

import httpx

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# 常量 (对应原版 package.json 的 ilink_appid 和 version)
# ---------------------------------------------------------------------------
CHANNEL_VERSION = "0.1.0"
ILINK_APP_ID = "bot"


def _build_client_version(version: str) -> int:
    """version 编码为 uint32: 0x00MMNNPP (major<<16 | minor<<8 | patch).

    对应原版 buildClientVersion().
    """
    parts = version.split(".")
    major = int(parts[0]) if len(parts) > 0 else 0
    minor = int(parts[1]) if len(parts) > 1 else 0
    patch = int(parts[2]) if len(parts) > 2 else 0
    return ((major & 0xFF) << 16) | ((minor & 0xFF) << 8) | (patch & 0xFF)


ILINK_APP_CLIENT_VERSION = _build_client_version(CHANNEL_VERSION)


def _random_wechat_uin() -> str:
    """X-WECHAT-UIN header: 随机 uint32 → 十进制字符串 → base64.

    对应原版 randomWechatUin().
    """
    value = int.from_bytes(os.urandom(4), "big")
    return base64.b64encode(str(value).encode()).decode()


def _ensure_trailing_slash(url: str) -> str:
    return url if url.endswith("/") else f"{url}/"


# ---------------------------------------------------------------------------
# Headers 构造
# ---------------------------------------------------------------------------

def _build_common_headers() -> dict[str, str]:
    """所有请求共用的 headers (GET/POST 都需要).

    对应原版 buildCommonHeaders().
    """
    return {
        "iLink-App-Id": ILINK_APP_ID,
        "iLink-App-ClientVersion": str(ILINK_APP_CLIENT_VERSION),
    }


def _build_post_headers(token: Optional[str] = None) -> dict[str, str]:
    """POST 请求 headers, 在 common headers 基础上加 auth 和随机 UIN.

    对应原版 buildHeaders().
    """
    headers = {
        "Content-Type": "application/json",
        "AuthorizationType": "ilink_bot_token",
        "X-WECHAT-UIN": _random_wechat_uin(),
        **_build_common_headers(),
    }
    if token and token.strip():
        headers["Authorization"] = f"Bearer {token.strip()}"
    return headers


# ====================================================================
# WeixinApiClient — 持久化 HTTP 客户端 (连接复用)
# ====================================================================

class WeixinApiClient:
    """持久化 HTTP 客户端, 复用连接池.

    用于长轮询等高频请求场景. 一个实例一个 httpx.AsyncClient,
    支持连接复用, 避免每次请求都新建 TCP+TLS.

    Usage:
        client = WeixinApiClient(base_url="https://...", token="...")
        await client.connect()
        try:
            raw = await client.post("ilink/bot/sendmessage", body)
        finally:
            await client.close()
    """

    def __init__(
        self,
        base_url: str,
        token: str = "",
        *,
        timeout: float = 30.0,
        route_tag: str = "",
    ):
        self._base_url = base_url
        self._token = token
        self._http: httpx.AsyncClient | None = None
        self._default_timeout = timeout
        self._route_tag = route_tag

    # ------------------------------------------------------------------
    # lifecycle
    # ------------------------------------------------------------------

    async def connect(self) -> None:
        """创建并启动底层 httpx.AsyncClient."""
        if self._http is not None:
            return
        self._http = httpx.AsyncClient(
            timeout=httpx.Timeout(self._default_timeout + 10, connect=30),
            follow_redirects=True,
        )

    async def close(self) -> None:
        """关闭底层 httpx.AsyncClient."""
        if self._http:
            await self._http.aclose()
            self._http = None

    async def __aenter__(self):
        await self.connect()
        return self

    async def __aexit__(self, *args):
        await self.close()

    @property
    def is_connected(self) -> bool:
        return self._http is not None

    # ------------------------------------------------------------------
    # public API
    # ------------------------------------------------------------------

    async def post(
        self,
        endpoint: str,
        body: str | dict,
        *,
        token: str | None = None,
        timeout: float | None = None,
    ) -> str:
        """POST JSON 到 Weixin API, 返回原始响应文本.

        body 可以是 JSON 字符串或 dict (自动 json.dumps).
        """
        assert self._http is not None, "client not connected"
        base = _ensure_trailing_slash(self._base_url)
        auth_token = token if token is not None else self._token
        headers = _build_post_headers(auth_token)
        if self._route_tag:
            headers["SKRouteTag"] = self._route_tag

        body_str = body if isinstance(body, str) else json.dumps(body, ensure_ascii=False)

        logger.debug("POST %s%s body=%.200s...", base, endpoint, body_str)

        resp = await self._http.post(
            f"{base}{endpoint}",
            content=body_str,
            headers=headers,
            timeout=timeout,
        )
        raw_text = resp.text
        logger.debug(
            "%s status=%d raw=%.200s...", endpoint, resp.status_code, raw_text
        )
        resp.raise_for_status()
        return raw_text

    async def get(
        self,
        endpoint: str,
        *,
        timeout: float | None = None,
        base_url: str | None = None,
    ) -> str:
        """GET Weixin API, 返回原始响应文本.

        base_url 可选 — 用于 QR 轮询时重定向到不同 host.
        """
        assert self._http is not None, "client not connected"
        url_base = _ensure_trailing_slash(base_url or self._base_url)
        headers = _build_common_headers()

        logger.debug("GET %s%s", url_base, endpoint)

        resp = await self._http.get(
            f"{url_base}{endpoint}",
            headers=headers,
            timeout=timeout,
        )
        raw_text = resp.text
        logger.debug(
            "%s status=%d raw=%.200s...", endpoint, resp.status_code, raw_text
        )
        resp.raise_for_status()
        return raw_text

    async def post_json(self, endpoint: str, body: str, *, token: str | None = None, timeout: float | None = None) -> dict:
        """POST 并返回解析后的 JSON dict."""
        raw = await self.post(endpoint, body, token=token, timeout=timeout)
        return json.loads(raw)


# ====================================================================
# 便捷函数 — 每次新建连接 (向后兼容, 用于一次性请求)
# ====================================================================

async def api_post(
    *,
    base_url: str,
    endpoint: str,
    body: str,
    token: Optional[str] = None,
    timeout: float = 15.0,
) -> str:
    """POST JSON 到 Weixin API endpoint, 返回原始响应文本 (每次新建连接).

    用于低频/一次性请求. 高频场景推荐用 WeixinApiClient.
    """
    base = _ensure_trailing_slash(base_url)
    headers = _build_post_headers(token)

    logger.debug("POST %s%s body=%.200s...", base, endpoint, body)

    async with httpx.AsyncClient(timeout=timeout) as client:
        resp = await client.post(
            f"{base}{endpoint}",
            content=body,
            headers=headers,
        )
        raw_text = resp.text
        logger.debug(
            "%s status=%d raw=%.200s...", endpoint, resp.status_code, raw_text
        )
        resp.raise_for_status()
        return raw_text


async def api_get(
    *,
    base_url: str,
    endpoint: str,
    timeout: float = 35.0,
) -> str:
    """GET Weixin API endpoint, 返回原始响应文本 (每次新建连接).

    用于低频/一次性请求. 高频场景推荐用 WeixinApiClient.
    """
    base = _ensure_trailing_slash(base_url)
    headers = _build_common_headers()

    logger.debug("GET %s%s", base, endpoint)

    async with httpx.AsyncClient(timeout=timeout) as client:
        resp = await client.get(
            f"{base}{endpoint}",
            headers=headers,
        )
        raw_text = resp.text
        logger.debug(
            "%s status=%d raw=%.200s...", endpoint, resp.status_code, raw_text
        )
        resp.raise_for_status()
        return raw_text
