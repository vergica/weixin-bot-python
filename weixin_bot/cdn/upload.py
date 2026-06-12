"""CDN 媒体上传 — 文件 → AES 加密 → CDN POST → 返回下载引用.

对应原版 cdn/upload.ts + cdn/cdn-upload.ts.
上传流水线: 读文件 → 算 MD5 → getUploadUrl → AES-ECB 加密 → POST CDN → 收 x-encrypted-param.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
from dataclasses import dataclass
from urllib.parse import quote

import httpx

from weixin_bot.api.client import api_post
from weixin_bot.cdn.crypto import encrypt as aes_encrypt, padded_size
from weixin_bot.media.download import DEFAULT_CDN_BASE

logger = logging.getLogger(__name__)

# proto 常量 (对应 UploadMediaType)
MEDIA_IMAGE = 1
MEDIA_VIDEO = 2
MEDIA_FILE  = 3
MEDIA_VOICE = 4

UPLOAD_MAX_RETRIES = 3
CHANNEL_VERSION = "0.1.0"


# ---------------------------------------------------------------------------
# 返回值
# ---------------------------------------------------------------------------

@dataclass
class UploadedFileInfo:
    """上传完成后的文件信息, 用于构造出站媒体消息.

    - download_encrypt_query_param: 填入 media.encrypt_query_param
    - aeskey: hex 字符串, 发送时需 base64(aeskey.encode()) 填入 media.aes_key
    - file_size:          明文字节数
    - file_size_ciphertext: 密文字节数 (PKCS7 对齐), 填入 mid_size / video_size
    """
    filekey: str
    download_encrypt_query_param: str
    aeskey: str
    file_size: int
    file_size_ciphertext: int


# ---------------------------------------------------------------------------
# getUploadUrl (对应 api.ts getUploadUrl)
# ---------------------------------------------------------------------------

async def _get_upload_url(
    *,
    base_url: str,
    token: str,
    filekey: str,
    media_type: int,
    to_user_id: str,
    rawsize: int,
    rawfilemd5: str,
    filesize: int,
    aeskey: str,
    no_need_thumb: bool = True,
) -> dict:
    """POST ilink/bot/getuploadurl, 返回 {upload_full_url, upload_param, ...}."""
    body = json.dumps({
        "filekey": filekey,
        "media_type": media_type,
        "to_user_id": to_user_id,
        "rawsize": rawsize,
        "rawfilemd5": rawfilemd5,
        "filesize": filesize,
        "no_need_thumb": no_need_thumb,
        "aeskey": aeskey,
        "base_info": {"channel_version": CHANNEL_VERSION},
    })
    raw = await api_post(
        base_url=base_url,
        endpoint="ilink/bot/getuploadurl",
        body=body,
        token=token,
        timeout=15.0,
    )
    return json.loads(raw)


# ---------------------------------------------------------------------------
# CDN POST (对应 cdn-upload.ts uploadBufferToCdn)
# ---------------------------------------------------------------------------

def _build_upload_url(cdn_base_url: str, upload_param: str, filekey: str) -> str:
    """从 upload_param 拼接 CDN 上传 URL (对应 cdn-url.ts buildCdnUploadUrl)."""
    return (
        f"{cdn_base_url}/upload"
        f"?encrypted_query_param={quote(upload_param)}"
        f"&filekey={quote(filekey)}"
    )


async def _upload_buffer_to_cdn(
    *,
    buf: bytes,
    upload_full_url: str = "",
    upload_param: str = "",
    filekey: str,
    cdn_base_url: str,
    aeskey: bytes,
) -> str:
    """加密文件内容后 POST 到 CDN, 返回 response header 里的 x-encrypted-param.

    5xx 最多重试 UPLOAD_MAX_RETRIES 次; 4xx 立即抛异常不重试.
    """
    ciphertext = aes_encrypt(buf, aeskey)

    if upload_full_url:
        cdn_url = upload_full_url
    elif upload_param:
        cdn_url = _build_upload_url(cdn_base_url, upload_param, filekey)
    else:
        raise ValueError("CDN upload URL missing (need upload_full_url or upload_param)")

    logger.debug("CDN POST url=%s... ciphertext=%d bytes", cdn_url[:80], len(ciphertext))

    last_error: Exception | None = None

    for attempt in range(1, UPLOAD_MAX_RETRIES + 1):
        try:
            async with httpx.AsyncClient(timeout=60.0) as client:
                resp = await client.post(
                    cdn_url,
                    content=ciphertext,
                    headers={"Content-Type": "application/octet-stream"},
                )

            is_client_error = 400 <= resp.status_code < 500
            if is_client_error:
                err_msg = resp.headers.get("x-error-message") or resp.text
                logger.error(
                    "CDN client error attempt=%d status=%d err=%s",
                    attempt, resp.status_code, err_msg,
                )
                raise RuntimeError(f"CDN upload client error {resp.status_code}: {err_msg}")

            if resp.status_code != 200:
                err_msg = resp.headers.get("x-error-message") or f"status {resp.status_code}"
                logger.error(
                    "CDN server error attempt=%d status=%d err=%s",
                    attempt, resp.status_code, err_msg,
                )
                raise RuntimeError(f"CDN upload server error: {err_msg}")

            download_param = resp.headers.get("x-encrypted-param")
            if not download_param:
                logger.error("CDN response missing x-encrypted-param attempt=%d", attempt)
                raise RuntimeError("CDN upload response missing x-encrypted-param header")

            logger.debug("CDN upload success attempt=%d download_param=%s", attempt, download_param[:40])
            return download_param

        except RuntimeError as e:
            last_error = e
            if "client error" in str(e):
                raise  # 4xx 不重试, 直接往上抛
            if attempt < UPLOAD_MAX_RETRIES:
                logger.warning("CDN upload attempt %d failed, retrying... err=%s", attempt, e)
            else:
                logger.error("CDN upload all %d attempts failed", UPLOAD_MAX_RETRIES)

    raise last_error or RuntimeError(f"CDN upload failed after {UPLOAD_MAX_RETRIES} attempts")


# ---------------------------------------------------------------------------
# 上传流水线 (对应 upload.ts uploadMediaToCdn)
# ---------------------------------------------------------------------------

async def _upload_media(
    *,
    file_path: str,
    to_user_id: str,
    base_url: str,
    token: str,
    cdn_base_url: str,
    media_type: int,
) -> UploadedFileInfo:
    """通用上传流水线: 读文件 → MD5 → getUploadUrl → 加密 → CDN POST."""
    plaintext = open(file_path, "rb").read()
    rawsize = len(plaintext)
    rawfilemd5 = hashlib.md5(plaintext).hexdigest()
    filesize = padded_size(rawsize)
    filekey = os.urandom(16).hex()  # 32 字符 hex
    aeskey = os.urandom(16)          # raw 16 bytes
    aeskey_hex = aeskey.hex()        # 传给 getUploadUrl 用 hex 字符串

    logger.debug(
        "Upload: file=%s rawsize=%d filesize=%d md5=%s filekey=%s",
        file_path, rawsize, filesize, rawfilemd5, filekey,
    )

    upload_url_resp = await _get_upload_url(
        base_url=base_url,
        token=token,
        filekey=filekey,
        media_type=media_type,
        to_user_id=to_user_id,
        rawsize=rawsize,
        rawfilemd5=rawfilemd5,
        filesize=filesize,
        aeskey=aeskey_hex,
    )

    upload_full_url = (upload_url_resp.get("upload_full_url") or "").strip()
    upload_param = upload_url_resp.get("upload_param") or ""

    if not upload_full_url and not upload_param:
        raise RuntimeError(f"getUploadUrl returned no upload URL: {upload_url_resp}")

    download_param = await _upload_buffer_to_cdn(
        buf=plaintext,
        upload_full_url=upload_full_url,
        upload_param=upload_param,
        filekey=filekey,
        cdn_base_url=cdn_base_url,
        aeskey=aeskey,
    )

    logger.info(
        "Upload complete: filekey=%s size=%d download_param=%s...",
        filekey, rawsize, download_param[:40],
    )
    return UploadedFileInfo(
        filekey=filekey,
        download_encrypt_query_param=download_param,
        aeskey=aeskey_hex,
        file_size=rawsize,
        file_size_ciphertext=filesize,
    )


# ---------------------------------------------------------------------------
# 公开接口
# ---------------------------------------------------------------------------

async def upload_image(
    file_path: str,
    to_user_id: str,
    *,
    base_url: str,
    token: str,
    cdn_base_url: str = DEFAULT_CDN_BASE,
) -> UploadedFileInfo:
    """上传图片到微信 CDN (media_type=IMAGE)."""
    return await _upload_media(
        file_path=file_path, to_user_id=to_user_id,
        base_url=base_url, token=token, cdn_base_url=cdn_base_url,
        media_type=MEDIA_IMAGE,
    )


async def upload_video(
    file_path: str,
    to_user_id: str,
    *,
    base_url: str,
    token: str,
    cdn_base_url: str = DEFAULT_CDN_BASE,
) -> UploadedFileInfo:
    """上传视频到微信 CDN (media_type=VIDEO)."""
    return await _upload_media(
        file_path=file_path, to_user_id=to_user_id,
        base_url=base_url, token=token, cdn_base_url=cdn_base_url,
        media_type=MEDIA_VIDEO,
    )


async def upload_file(
    file_path: str,
    to_user_id: str,
    *,
    base_url: str,
    token: str,
    cdn_base_url: str = DEFAULT_CDN_BASE,
) -> UploadedFileInfo:
    """上传文件到微信 CDN (media_type=FILE)."""
    return await _upload_media(
        file_path=file_path, to_user_id=to_user_id,
        base_url=base_url, token=token, cdn_base_url=cdn_base_url,
        media_type=MEDIA_FILE,
    )
