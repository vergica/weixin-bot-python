"""MIME 类型检测 — 扩展名 ↔ MIME 映射.

对应原版 media/mime.ts. 用于 send_media 判断文件该用 image/video/file 哪种方式上传.
"""

from __future__ import annotations

from pathlib import Path

# 扩展名 → MIME (小写)
_EXT_TO_MIME: dict[str, str] = {
    # 图片
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".webp": "image/webp",
    ".bmp": "image/bmp",
    # 视频
    ".mp4": "video/mp4",
    ".mov": "video/quicktime",
    ".webm": "video/webm",
    ".mkv": "video/x-matroska",
    ".avi": "video/x-msvideo",
    # 音频
    ".mp3": "audio/mpeg",
    ".ogg": "audio/ogg",
    ".wav": "audio/wav",
    # 文档
    ".pdf": "application/pdf",
    ".doc": "application/msword",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".xls": "application/vnd.ms-excel",
    ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    ".ppt": "application/vnd.ms-powerpoint",
    ".pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    ".txt": "text/plain",
    ".csv": "text/csv",
    # 压缩包
    ".zip": "application/zip",
    ".tar": "application/x-tar",
    ".gz": "application/gzip",
}

# MIME → 扩展名
_MIME_TO_EXT: dict[str, str] = {
    "image/jpeg": ".jpg",
    "image/jpg": ".jpg",
    "image/png": ".png",
    "image/gif": ".gif",
    "image/webp": ".webp",
    "image/bmp": ".bmp",
    "video/mp4": ".mp4",
    "video/quicktime": ".mov",
    "video/webm": ".webm",
    "video/x-matroska": ".mkv",
    "video/x-msvideo": ".avi",
    "audio/mpeg": ".mp3",
    "audio/ogg": ".ogg",
    "audio/wav": ".wav",
    "application/pdf": ".pdf",
    "application/zip": ".zip",
    "application/x-tar": ".tar",
    "application/gzip": ".gz",
    "text/plain": ".txt",
    "text/csv": ".csv",
}


def get_mime(filename: str) -> str:
    """从文件名推测 MIME 类型. 未知扩展名返回 application/octet-stream."""
    ext = Path(filename).suffix.lower()
    return _EXT_TO_MIME.get(ext, "application/octet-stream")


def get_extension(mime_type: str) -> str:
    """从 MIME 类型取扩展名. 未知返回 .bin."""
    ct = mime_type.split(";")[0].strip().lower()
    return _MIME_TO_EXT.get(ct, ".bin")


def guess_media_type(path: str | Path) -> str:
    """根据文件扩展名判断媒体类型: image / video / file.

    用于 send_media 决定调用 upload_image / upload_video / upload_file.
    """
    mime = get_mime(str(path))
    if mime.startswith("image/"):
        return "image"
    if mime.startswith("video/"):
        return "video"
    return "file"
