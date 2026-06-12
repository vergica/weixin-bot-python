"""入站媒体下载 & AES 解密.

对应原版 cdn/pic-decrypt.ts + media/media-download.ts.
"""

from __future__ import annotations

import base64
import logging
from urllib.parse import quote

import httpx

from weixin_bot.cdn.crypto import decrypt as aes_decrypt
from weixin_bot.messaging.inbound import CdnRef

logger = logging.getLogger(__name__)

from weixin_bot.config import get as config_get

# 对应原版 CDN_BASE_URL
DEFAULT_CDN_BASE = str(config_get("cdn_base_url"))


# ---------------------------------------------------------------------------
# AES key 解析
# ---------------------------------------------------------------------------

def _parse_aes_key(aes_key: str) -> bytes:
    """解析 CDNMedia.aes_key.

    aes_key 是 base64 编码, 解码后有两种情况:
      - 16 bytes:  直接用
      - 32 bytes:  是 hex 字符串, 再解码一次得到 16 bytes key

    对应原版 parseAesKey().
    """
    decoded = base64.b64decode(aes_key)

    if len(decoded) == 16:
        return decoded

    if len(decoded) == 32:
        text = decoded.decode("ascii")
        if all(c in "0123456789abcdefABCDEF" for c in text):
            return bytes.fromhex(text)

    raise ValueError(
        f"aes_key 解码后应为 16 bytes 或 32-char hex, 实际 {len(decoded)} bytes"
    )


# ---------------------------------------------------------------------------
# URL — 降级重试
# ---------------------------------------------------------------------------

def _build_candidates(cdn: CdnRef, cdn_base: str) -> list[tuple[str, str]]:
    """构建下载 URL 候选列表 [(label, url), ...].

    优先 full_url, 其次 encrypt_query_param. 两条都可能是空.
    """
    candidates: list[tuple[str, str]] = []
    if cdn.full_url:
        candidates.append(("full_url", cdn.full_url))
    if cdn.encrypt_query_param:
        param_url = f"{cdn_base}/download?encrypted_query_param={quote(cdn.encrypt_query_param)}"
        if not cdn.full_url or param_url != cdn.full_url:
            candidates.append(("encrypt_query_param", param_url))
    if not candidates:
        raise ValueError("CdnRef 必须提供 full_url 或 encrypt_query_param")
    return candidates


def _is_retryable_download_error(err: Exception) -> bool:
    """判断下载错误是否可重试 (5xx / 超时 / 传输错误)."""
    if isinstance(err, httpx.TimeoutException | httpx.TransportError):
        return True
    if isinstance(err, httpx.HTTPStatusError):
        status_code = err.response.status_code if err.response is not None else 0
        return status_code >= 500
    return False


# ---------------------------------------------------------------------------
# public
# ---------------------------------------------------------------------------

async def download_media(
    cdn: CdnRef,
    *,
    cdn_base: str = DEFAULT_CDN_BASE,
) -> bytes:
    """从微信 CDN 下载媒体文件并 AES 解密, 返回明文 bytes.

    下载策略:
    1. 优先 full_url
    2. full_url 失败 (5xx/超时/传输错误) → 降级到 encrypt_query_param
    3. 4xx 等客户端错误不降级, 直接失败

    对图片/文件/视频都需要解密, 语音需要转码 (sil→wav, 暂未实现).
    """
    candidates = _build_candidates(cdn, cdn_base)

    async with httpx.AsyncClient(timeout=60.0) as client:
        data = b""
        for i, (source, cdn_url) in enumerate(candidates):
            try:
                logger.info("Downloading CDN media via %s: %s...", source, cdn_url[:80])
                resp = await client.get(cdn_url)
                resp.raise_for_status()
                data = resp.content
                break
            except Exception as e:
                has_next = i + 1 < len(candidates)
                should_fallback = (
                    source == "full_url"
                    and has_next
                    and _is_retryable_download_error(e)
                )
                if should_fallback:
                    logger.warning(
                        "CDN download failed via full_url, falling back to encrypt_query_param: %s", e
                    )
                    continue
                raise

    logger.debug("Downloaded %d bytes, decrypting...", len(data))
    key = _parse_aes_key(cdn.aes_key)
    return aes_decrypt(data, key)
