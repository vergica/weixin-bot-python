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
# URL
# ---------------------------------------------------------------------------

def _build_url(cdn: CdnRef, cdn_base: str) -> str:
    """确定下载 URL. 优先 full_url, 其次用 encrypt_query_param 拼接."""
    if cdn.full_url:
        return cdn.full_url
    if cdn.encrypt_query_param:
        return f"{cdn_base}/download?encrypted_query_param={quote(cdn.encrypt_query_param)}"
    raise ValueError("CdnRef 必须提供 full_url 或 encrypt_query_param")


# ---------------------------------------------------------------------------
# public
# ---------------------------------------------------------------------------

async def download_media(
    cdn: CdnRef,
    *,
    cdn_base: str = DEFAULT_CDN_BASE,
) -> bytes:
    """从微信 CDN 下载媒体文件并 AES 解密, 返回明文 bytes.

    对图片/文件/视频都需要解密, 语音需要转码 (sil→wav, 暂未实现).
    """
    url = _build_url(cdn, cdn_base)
    logger.info("Downloading CDN media: %s...", url[:80])

    async with httpx.AsyncClient(timeout=60.0) as client:
        resp = await client.get(url)
        resp.raise_for_status()
        encrypted = resp.content

    logger.debug("Downloaded %d bytes, decrypting...", len(encrypted))
    key = _parse_aes_key(cdn.aes_key)
    return aes_decrypt(encrypted, key)
