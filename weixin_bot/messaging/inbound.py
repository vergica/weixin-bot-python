"""入站消息解析 — 原始 WeixinMessage JSON → InboundMessage.

对应原版 messaging/inbound.ts + api/types.ts 的协议类型.
"""

from __future__ import annotations

from dataclasses import dataclass, field


# ---------------------------------------------------------------------------
# 子类型 — 对应协议里的 MessageItem 各 item
# ---------------------------------------------------------------------------

@dataclass
class CdnRef:
    """CDN 媒体引用 (图片/视频/文件/语音的下载信息)."""
    encrypt_query_param: str = ""
    aes_key: str = ""
    full_url: str = ""


@dataclass
class VoiceItem:
    """语音消息 (SILK 编码)."""
    cdn: CdnRef = field(default_factory=CdnRef)
    encode_type: int = 0     # 6=silk
    sample_rate: int = 0
    duration_ms: int = 0
    text: str = ""           # 服务端语音转文字结果


@dataclass
class FileItem:
    """文件消息."""
    cdn: CdnRef = field(default_factory=CdnRef)
    file_name: str = ""
    size: int = 0


# ---------------------------------------------------------------------------
# 入站消息
# ---------------------------------------------------------------------------

@dataclass
class InboundMessage:
    """解析后的入站消息, 屏蔽原始 JSON 细节."""

    from_user: str                    # o9cq80_xxx@im.wechat
    msg_id: str = ""
    context_token: str = ""

    # 文本 (所有 type=1 item 拼接)
    text: str = ""

    # 媒体
    images: list[CdnRef] = field(default_factory=list)    # type=2
    voice: VoiceItem | None = None                        # type=3
    files: list[FileItem] = field(default_factory=list)   # type=4
    videos: list[CdnRef] = field(default_factory=list)    # type=5

    @property
    def has_media(self) -> bool:
        return bool(self.images or self.voice or self.files or self.videos)


# ---------------------------------------------------------------------------
# 解析函数
# ---------------------------------------------------------------------------

def parse_message(msg: dict) -> InboundMessage:
    """把 getUpdates 返回的原始消息字典转为 InboundMessage."""
    result = InboundMessage(
        from_user=msg.get("from_user_id", ""),
        msg_id=str(msg.get("message_id", "")),
        context_token=msg.get("context_token", ""),
    )

    for item in msg.get("item_list", []) or []:
        _parse_item(result, item)

    return result


def _parse_item(msg: InboundMessage, item: dict) -> None:
    t = item.get("type", 0)
    if t == 1:
        _parse_text(msg, item)
    elif t == 2:
        _parse_image(msg, item)
    elif t == 3:
        _parse_voice(msg, item)
    elif t == 4:
        _parse_file(msg, item)
    elif t == 5:
        _parse_video(msg, item)


def _is_media_item(item: dict) -> bool:
    """item 是否为媒体类型 (image/file/video).

    VOICE 不在此列 — 引用语音时取语音转文字结果拼入引用前缀.
    """
    return item.get("type", 0) in (2, 4, 5)


def _extract_item_text(item: dict) -> str:
    """从单个 item 中提取文本 — 用于引用消息的正文提取."""
    t = item.get("type", 0)
    if t == 1:  # TEXT
        return (item.get("text_item") or {}).get("text", "")
    if t == 3:  # VOICE → 语音转文字结果
        return (item.get("voice_item") or {}).get("text", "")
    return ""


def _parse_text(msg: InboundMessage, item: dict) -> None:
    ti = item.get("text_item") or {}
    text = ti.get("text", "")
    if not text:
        return

    # 引用消息处理 — 只在第一条 TEXT item 上解析 ref_msg
    if not msg.text:
        ref = item.get("ref_msg")
        if ref:
            ref_item = ref.get("message_item")
            # 被引用的是媒体 → 跳过不拼 (TS: isMediaItem → return text)
            if not ref_item or not _is_media_item(ref_item):
                parts: list[str] = []
                title = ref.get("title", "")
                if title:
                    parts.append(title)
                if ref_item:
                    body = _extract_item_text(ref_item)
                    if body and body not in parts:  # 去重: title 和正文相同时只保留一份
                        parts.append(body)
                if parts:
                    msg.text = f"[引用: {' | '.join(parts)}]\n"

    msg.text += text  # 多条 TEXT item 直接拼接


def _parse_image(msg: InboundMessage, item: dict) -> None:
    ii = item.get("image_item") or {}
    m = ii.get("media") or {}
    msg.images.append(CdnRef(
        encrypt_query_param=m.get("encrypt_query_param", ""),
        aes_key=m.get("aes_key", ""),
        full_url=m.get("full_url", ""),
    ))


def _parse_voice(msg: InboundMessage, item: dict) -> None:
    vi = item.get("voice_item") or {}
    m = vi.get("media") or {}
    msg.voice = VoiceItem(
        cdn=CdnRef(
            encrypt_query_param=m.get("encrypt_query_param", ""),
            aes_key=m.get("aes_key", ""),
            full_url=m.get("full_url", ""),
        ),
        encode_type=vi.get("encode_type", 0),
        sample_rate=vi.get("sample_rate", 0),
        duration_ms=vi.get("playtime", 0),
        text=vi.get("text", ""),
    )


def _parse_file(msg: InboundMessage, item: dict) -> None:
    fi = item.get("file_item") or {}
    m = fi.get("media") or {}
    msg.files.append(FileItem(
        cdn=CdnRef(
            encrypt_query_param=m.get("encrypt_query_param", ""),
            aes_key=m.get("aes_key", ""),
            full_url=m.get("full_url", ""),
        ),
        file_name=fi.get("file_name", ""),
        size=int(fi.get("len", 0)),
    ))


def _parse_video(msg: InboundMessage, item: dict) -> None:
    vi = item.get("video_item") or {}
    m = vi.get("media") or {}
    msg.videos.append(CdnRef(
        encrypt_query_param=m.get("encrypt_query_param", ""),
        aes_key=m.get("aes_key", ""),
        full_url=m.get("full_url", ""),
    ))
