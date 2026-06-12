"""发送媒体消息 — 图片/视频/文件, 基于已上传的 CDN 引用.

对应原版 messaging/send.ts 的 sendImageMessageWeixin / sendVideoMessageWeixin / sendFileMessageWeixin.
"""

from __future__ import annotations

import base64
import json
import logging

from weixin_bot.api.client import api_post
from weixin_bot.cdn.upload import UploadedFileInfo

logger = logging.getLogger(__name__)

# proto 常量
MSG_TYPE_BOT = 2
MSG_STATE_FINISH = 2
ITEM_TYPE_TEXT = 1
ITEM_TYPE_IMAGE = 2
ITEM_TYPE_VIDEO = 5
ITEM_TYPE_FILE = 4

CHANNEL_VERSION = "0.1.0"


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _aes_key_base64(aeskey_hex: str) -> str:
    """hex 字符串 → base64 编码, 用于 media.aes_key.

    TypeScript 原版: Buffer.from(uploaded.aeskey).toString("base64")
    uploaded.aeskey 是 hex 字符串, Buffer.from(hex_str) 取 hex 字符串的 UTF-8 字节,
    再 base64 编码这 32 个字节.
    """
    return base64.b64encode(aeskey_hex.encode()).decode()


async def _send_media_items(
    *,
    to: str,
    text: str,
    media_item: dict,
    base_url: str,
    token: str,
    context_token: str = "",
) -> dict:
    """发送一条或多条消息 (text 可选前置). 每条单独发送, item_list 始终只有一项.

    返回最后一条消息的 messageId.
    """
    items: list[dict] = []
    if text:
        items.append({"type": ITEM_TYPE_TEXT, "text_item": {"text": text}})
    items.append(media_item)

    last_client_id = ""
    for item in items:
        last_client_id = _gen_client_id()
        body = {
            "msg": {
                "from_user_id": "",
                "to_user_id": to,
                "client_id": last_client_id,
                "message_type": MSG_TYPE_BOT,
                "message_state": MSG_STATE_FINISH,
                "item_list": [item],
                "context_token": context_token or "",
            },
            "base_info": {"channel_version": CHANNEL_VERSION},
        }
        await api_post(
            base_url=base_url,
            endpoint="ilink/bot/sendmessage",
            body=json.dumps(body, ensure_ascii=False),
            token=token,
            timeout=15.0,
        )

    return {"messageId": last_client_id}


def _gen_client_id() -> str:
    """随机 client_id, 与 send.py 保持一致."""
    import os
    raw = os.urandom(16)
    return "weixin-bot-" + base64.b32encode(raw).decode().rstrip("=").lower()


# ---------------------------------------------------------------------------
# 公开接口
# ---------------------------------------------------------------------------

async def send_image(
    *,
    to: str,
    uploaded: UploadedFileInfo,
    text: str = "",
    base_url: str,
    token: str,
    context_token: str = "",
) -> dict:
    """发送图片消息. 可选附带文字 caption."""
    image_item = {
        "type": ITEM_TYPE_IMAGE,
        "image_item": {
            "media": {
                "encrypt_query_param": uploaded.download_encrypt_query_param,
                "aes_key": _aes_key_base64(uploaded.aeskey),
                "encrypt_type": 1,
            },
            "mid_size": uploaded.file_size_ciphertext,
        },
    }
    return await _send_media_items(
        to=to, text=text, media_item=image_item,
        base_url=base_url, token=token, context_token=context_token,
    )


async def send_video(
    *,
    to: str,
    uploaded: UploadedFileInfo,
    text: str = "",
    base_url: str,
    token: str,
    context_token: str = "",
) -> dict:
    """发送视频消息. 可选附带文字 caption."""
    video_item = {
        "type": ITEM_TYPE_VIDEO,
        "video_item": {
            "media": {
                "encrypt_query_param": uploaded.download_encrypt_query_param,
                "aes_key": _aes_key_base64(uploaded.aeskey),
                "encrypt_type": 1,
            },
            "video_size": uploaded.file_size_ciphertext,
        },
    }
    return await _send_media_items(
        to=to, text=text, media_item=video_item,
        base_url=base_url, token=token, context_token=context_token,
    )


async def send_file(
    *,
    to: str,
    uploaded: UploadedFileInfo,
    file_name: str,
    text: str = "",
    base_url: str,
    token: str,
    context_token: str = "",
) -> dict:
    """发送文件消息. file_name 会显示给接收方, 可选附带文字 caption."""
    file_item = {
        "type": ITEM_TYPE_FILE,
        "file_item": {
            "media": {
                "encrypt_query_param": uploaded.download_encrypt_query_param,
                "aes_key": _aes_key_base64(uploaded.aeskey),
                "encrypt_type": 1,
            },
            "file_name": file_name,
            "len": str(uploaded.file_size),  # 明文大小, 字符串类型
        },
    }
    return await _send_media_items(
        to=to, text=text, media_item=file_item,
        base_url=base_url, token=token, context_token=context_token,
    )
