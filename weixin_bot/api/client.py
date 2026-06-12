"""Weixin iLink HTTP API client — 核心请求函数.

对应原版 src/api/api.ts 的 apiPostFetch / apiGetFetch.
"""

from __future__ import annotations

import base64
import logging
import os
import re
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


# ---------------------------------------------------------------------------
# 核心请求函数
# ---------------------------------------------------------------------------

async def api_post(
    *,
    base_url: str,
    endpoint: str,
    body: str,
    token: Optional[str] = None,
    timeout: float = 15.0,
) -> str:
    """POST JSON 到 Weixin API endpoint, 返回原始响应文本.

    对应原版 apiPostFetch().

    - base_url:  https://ilinkai.weixin.qq.com (自动补尾 /)
    - endpoint:  ilink/bot/getupdates (相对路径, 不含前置 /)
    - body:      JSON 字符串
    - token:     Bearer token (可选, 某些接口如 get_bot_qrcode 不需要)
    - timeout:   超时秒数, 默认 15s (长轮询应传 35s+)
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
    """GET Weixin API endpoint, 返回原始响应文本.

    对应原版 apiGetFetch(). 主要用于轮询二维码状态 (长轮询, 默认 35s timeout).

    - base_url:  https://ilinkai.weixin.qq.com
    - endpoint:  ilink/bot/get_qrcode_status?qrcode=xxx (含 query string)
    - timeout:   超时秒数, 默认 35s
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
