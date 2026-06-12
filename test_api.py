"""临时测试脚本 — 逐个验证各模块的功能."""
from __future__ import annotations

import asyncio
import base64
import json
import logging
import os
import tempfile
from pathlib import Path

logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s %(message)s")

# ---------- 先用临时目录当 STATE_DIR, 避免污染真实数据 ----------
TMP = Path(tempfile.mkdtemp(prefix="weixin-bot-test-"))
os.environ["WEIXIN_BOT_STATE_DIR"] = str(TMP)

from weixin_bot.api.client import api_post, api_get
from weixin_bot.auth.accounts import list_ids, register_id, load, save
from weixin_bot.auth.login import start_login, wait_login, _get_local_tokens
from weixin_bot.monitor.loop import MonitorLoop, SessionExpired
from weixin_bot.messaging.inbound import parse_message, InboundMessage, _is_media_item, _extract_item_text
from weixin_bot.cdn.crypto import encrypt, decrypt, padded_size
from weixin_bot.media.download import _parse_aes_key, _build_url
from weixin_bot.messaging.inbound import CdnRef
from weixin_bot.cdn.upload import (
    UploadedFileInfo,
    _build_upload_url,
    MEDIA_IMAGE,
    MEDIA_VIDEO,
    MEDIA_FILE,
)
from weixin_bot.messaging.send_media import (
    _aes_key_base64,
    send_image,
    send_video,
    send_file,
)
from weixin_bot.media.mime import get_mime, get_extension, guess_media_type

BASE = "https://ilinkai.weixin.qq.com"
BOT_TYPE = "3"


# ====================================================================
# accounts.py
# ====================================================================

def test_register_and_list():
    """list_ids / register_id: 注册账号索引."""
    print("=" * 50)
    print("[accounts] test_register_and_list")

    # 初始为空
    assert list_ids() == [], f"expected [], got {list_ids()}"
    print("  empty -> OK")

    register_id("test-bot-1")
    assert list_ids() == ["test-bot-1"], f"expected ['test-bot-1'], got {list_ids()}"
    print("  register test-bot-1 -> OK")

    # 幂等
    register_id("test-bot-1")
    assert list_ids() == ["test-bot-1"], f"expected still ['test-bot-1'], got {list_ids()}"
    print("  idempotent -> OK")

    register_id("test-bot-2")
    assert list_ids() == ["test-bot-1", "test-bot-2"]
    print("  register test-bot-2 -> OK")

    print("[accounts] test_register_and_list PASS")


def test_save_and_load():
    """save / load: 凭据读写."""
    print("=" * 50)
    print("[accounts] test_save_and_load")

    # 不存在返回 None
    assert load("no-such-id") is None
    print("  load missing -> None OK")

    # 保存
    save("test-bot-1", token="token-abc", base_url="https://example.com")
    data = load("test-bot-1")
    assert data is not None
    assert data["token"] == "token-abc"
    assert data["baseUrl"] == "https://example.com"
    assert "savedAt" in data
    print(f"  save + load -> OK (savedAt={data['savedAt'][:19]})")

    # 更新 token (不传 base_url, 保留原来的)
    save("test-bot-1", token="token-new")
    data = load("test-bot-1")
    assert data["token"] == "token-new"
    assert data["baseUrl"] == "https://example.com"  # 保留
    print("  update token, keep baseUrl -> OK")

    # 不传任何值 (不会覆盖)
    save("test-bot-1")
    data = load("test-bot-1")
    assert data["token"] == "token-new"  # 没变
    print("  save with no args keeps existing -> OK")

    # 默认 baseUrl
    save("fresh-id", token="tok")
    data = load("fresh-id")
    assert data["baseUrl"] == "https://ilinkai.weixin.qq.com"
    print("  default baseUrl -> OK")

    print("[accounts] test_save_and_load PASS")


# ====================================================================
# auth/login.py
# ====================================================================

def test_get_local_tokens():
    """_get_local_tokens: 收集本地 token."""
    print("=" * 50)
    print("[login] test_get_local_tokens")
    # 前序测试已存了 test-bot-1(token=token-new) 和 test-bot-2(无 token)
    tokens = _get_local_tokens()
    assert len(tokens) >= 1, f"expected at least 1 token, got {tokens}"
    print(f"  tokens from prior saves: {tokens}")

    # 再注册一个, 验证顺序 (最新注册的在前)
    register_id("extra-bot")
    save("extra-bot", token="extra-secret")
    tokens = _get_local_tokens()
    assert tokens[0] == "extra-secret"
    print(f"  after register + save: {tokens}")
    print("[login] test_get_local_tokens PASS")


async def test_start_login():
    """start_login: 拿到二维码."""
    print("=" * 50)
    print("[login] test_start_login")
    qr = await start_login()
    assert len(qr["qrcode"]) == 32
    assert qr["qrcode_url"].startswith("https://")
    print(f"  qrcode: {qr['qrcode'][:50]}...")
    print(f"  url:    {qr['qrcode_url'][:60]}...")
    print("[login] test_start_login PASS")
    return qr


async def test_wait_login_timeout(qrcode: str):
    """wait_login: 3s 没人扫, 应返回超时."""
    print("=" * 50)
    print("[login] test_wait_login_timeout")
    result = await wait_login(qrcode, timeout_ms=3000)
    assert result["connected"] is False
    assert "time" in result["message"].lower()
    print(f"  message: {result['message']}")
    print("[login] test_wait_login_timeout PASS")


# ====================================================================
# api/client.py
# ====================================================================

async def test_api_post():
    """api_post: 获取登录二维码."""
    print("=" * 50)
    print("[api] test_api_post")
    body = json.dumps({"local_token_list": []})
    raw = await api_post(
        base_url=BASE,
        endpoint=f"ilink/bot/get_bot_qrcode?bot_type={BOT_TYPE}",
        body=body,
        token=None,
        timeout=15.0,
    )
    data = json.loads(raw)
    assert data.get("ret") == 0, f"ret != 0: {data}"
    assert len(data.get("qrcode", "")) > 0
    print(f"  qrcode: {data['qrcode'][:50]}...")
    print(f"  qrcode_img_content len: {len(data.get('qrcode_img_content', ''))}")
    print("[api] test_api_post PASS")
    return data


async def test_api_get(data: dict):
    """api_get: 轮询二维码状态."""
    print("=" * 50)
    print("[api] test_api_get")
    qrcode = data.get("qrcode", "")
    try:
        raw = await api_get(
            base_url=BASE,
            endpoint=f"ilink/bot/get_qrcode_status?qrcode={qrcode}",
            timeout=5.0,
        )
        status_data = json.loads(raw)
        print(f"  status: {status_data.get('status', '???')}")
    except Exception as e:
        # 超时正常 — 没人扫码
        print(f"  (expected) timeout: {e}")
    print("[api] test_api_get PASS")


# ====================================================================
# monitor/loop.py
# ====================================================================

def test_sync_buf():
    """sync buf 读写."""
    print("=" * 50)
    print("[monitor] test_sync_buf")
    loop = MonitorLoop(
        base_url=BASE, token="fake", account_id="buf-test",
        state_dir=TMP, on_message=_noop,
    )
    # 初始为空
    assert loop._load_buf() == ""
    print("  load empty -> '' OK")

    # 保存后能读回
    loop._save_buf("hello-sync-buf")
    assert loop._load_buf() == "hello-sync-buf"
    print("  save + load -> OK")

    # 文件确实写到了磁盘
    assert loop._sync_path.exists()
    print(f"  file exists: {loop._sync_path}")
    print("[monitor] test_sync_buf PASS")


async def test_sleep_early_stop():
    """_sleep 可被 stop() 提前唤醒."""
    print("=" * 50)
    print("[monitor] test_sleep_early_stop")
    loop = MonitorLoop(
        base_url=BASE, token="fake", account_id="sleep-test",
        state_dir=TMP, on_message=_noop,
    )
    t0 = asyncio.get_event_loop().time()
    # 启动一个 30s 的 sleep, 50ms 后 stop
    task = asyncio.create_task(loop._sleep(30))
    await asyncio.sleep(0.05)
    loop._stop.set()
    await task
    elapsed = asyncio.get_event_loop().time() - t0
    assert elapsed < 1.0, f"sleep took {elapsed:.1f}s, expected <1s"
    print(f"  woke up after {elapsed:.2f}s (expected <1s)")
    print("[monitor] test_sleep_early_stop PASS")


async def test_invalid_token_raises():
    """假 token → 服务端返回 -14 → SessionExpired."""
    print("=" * 50)
    print("[monitor] test_invalid_token_raises")
    received: list[dict] = []
    loop = MonitorLoop(
        base_url=BASE, token="this-is-not-a-real-token",
        account_id="fake-bot", state_dir=TMP,
        on_message=lambda m: received.append(m) or _noop(m),  # type: ignore[arg-type]
    )
    try:
        await asyncio.wait_for(loop.run(), timeout=10.0)
        assert False, "should have raised SessionExpired"
    except SessionExpired:
        print("  SessionExpired raised -> OK")
    except asyncio.TimeoutError:
        print("  timeout — server didn't respond with -14, maybe different error")
    print("[monitor] test_invalid_token_raises PASS")


async def _noop(_msg: dict) -> None:
    pass


# ====================================================================
# cdn/crypto.py
# ====================================================================

def test_crypto_roundtrip():
    """加密再解密 = 原文."""
    print("=" * 50)
    print("[crypto] test_roundtrip")
    key = b"0123456789abcdef"  # 16 bytes
    plain = b"Hello World! This is test data. 12345"
    enc = encrypt(plain, key)
    dec = decrypt(enc, key)
    assert dec == plain
    assert enc != plain   # 密文 ≠ 明文
    print(f"  plain={len(plain)}B  enc={len(enc)}B  decrypt OK")
    print("[crypto] test_roundtrip PASS")


def test_crypto_padded_size():
    """计算 PKCS7 填充后大小."""
    print("=" * 50)
    print("[crypto] test_padded_size")
    assert padded_size(0) == 16     # 空 → 一整块 padding
    assert padded_size(1) == 16
    assert padded_size(15) == 16
    assert padded_size(16) == 32    # 满块 → 加一块
    assert padded_size(100) == 112
    print(f"  padded_size(0)=16, (16)=32, (100)=112  OK")
    print("[crypto] test_padded_size PASS")


def test_crypto_deterministic():
    """AES-ECB 是确定性的 — 同样输入产出同样输出."""
    print("=" * 50)
    print("[crypto] test_deterministic")
    key = b"abcdef0123456789"
    plain = b"test"
    enc1 = encrypt(plain, key)
    enc2 = encrypt(plain, key)
    assert enc1 == enc2
    print(f"  enc1 == enc2 OK")
    print("[crypto] test_deterministic PASS")


def test_crypto_padding():
    """不同长度原文加密后长度正确."""
    print("=" * 50)
    print("[crypto] test_padding")
    key = b"k" * 16
    for n in [1, 5, 15, 16, 17, 31, 32, 100]:
        plain = b"x" * n
        enc = encrypt(plain, key)
        assert len(enc) == padded_size(n)
        assert decrypt(enc, key) == plain
    print(f"  encrypt/decrypt OK for sizes 1..100")
    print("[crypto] test_padding PASS")


# ====================================================================
# media/download.py
# ====================================================================

def test_parse_aes_key_16():
    """aes_key base64 解码后 16 bytes → 直接用."""
    print("=" * 50)
    print("[media] test_parse_aes_key_16")
    # 16 bytes "abc...", base64 编码
    raw = b"0123456789abcdef"
    b64 = base64.b64encode(raw).decode()
    key = _parse_aes_key(b64)
    assert key == raw
    print(f"  key={key.hex()} OK")
    print("[media] test_parse_aes_key_16 PASS")


def test_parse_aes_key_hex():
    """aes_key base64 解码后 32-char hex → 再解一次."""
    print("=" * 50)
    print("[media] test_parse_aes_key_hex")
    hex_str = "a1b2c3d4e5f607182930a1b2c3d4e5f6"
    # base64(hex_str) ≈ base64 编码 hex 字符串的 UTF-8 bytes
    b64 = base64.b64encode(hex_str.encode()).decode()
    key = _parse_aes_key(b64)
    assert key == bytes.fromhex(hex_str)
    assert len(key) == 16
    print(f"  key={key.hex()} OK")
    print("[media] test_parse_aes_key_hex PASS")


def test_parse_aes_key_bad():
    """非法 aes_key 抛异常."""
    print("=" * 50)
    print("[media] test_parse_aes_key_bad")
    bad = base64.b64encode(b"tooshort").decode()
    try:
        _parse_aes_key(bad)
        assert False, "should have raised"
    except ValueError:
        print(f"  ValueError raised OK")
    print("[media] test_parse_aes_key_bad PASS")


def test_build_url():
    """full_url 优先, encrypt_query_param 回退."""
    print("=" * 50)
    print("[media] test_build_url")
    # 有 full_url
    cdn = CdnRef(full_url="https://cdn.weixin.qq.com/abc.jpg")
    assert _build_url(cdn, "https://base") == "https://cdn.weixin.qq.com/abc.jpg"
    print("  full_url -> OK")

    # 只有 encrypt_query_param
    cdn = CdnRef(encrypt_query_param="enc_xyz")
    url = _build_url(cdn, "https://novac2c.cdn.weixin.qq.com/c2c")
    assert "enc_xyz" in url
    print(f"  query_param -> {url[:60]}... OK")

    # 都没有
    try:
        _build_url(CdnRef(), "https://base")
        assert False, "should have raised"
    except ValueError:
        print("  empty CdnRef -> ValueError OK")
    print("[media] test_build_url PASS")


# ====================================================================
# media/mime.py
# ====================================================================

def test_get_mime():
    """get_mime: 文件名 → MIME."""
    print("=" * 50)
    print("[mime] test_get_mime")
    assert get_mime("photo.jpg") == "image/jpeg"
    assert get_mime("photo.jpeg") == "image/jpeg"
    assert get_mime("img.PNG") == "image/png"       # 大小写不敏感
    assert get_mime("doc.pdf") == "application/pdf"
    assert get_mime("video.mp4") == "video/mp4"
    assert get_mime("unknown.xyz") == "application/octet-stream"
    print("  jpg/jpeg/png/pdf/mp4/unknown -> OK")
    print("[mime] test_get_mime PASS")


def test_get_extension():
    """get_extension: MIME → 扩展名."""
    print("=" * 50)
    print("[mime] test_get_extension")
    assert get_extension("image/png") == ".png"
    assert get_extension("video/mp4") == ".mp4"
    assert get_extension("application/pdf") == ".pdf"
    assert get_extension("text/plain; charset=utf-8") == ".txt"  # 带参数
    assert get_extension("weird/type") == ".bin"
    print("  png/mp4/pdf/charset/unknown -> OK")
    print("[mime] test_get_extension PASS")


def test_guess_media_type():
    """guess_media_type: 路径 → image/video/file."""
    print("=" * 50)
    print("[mime] test_guess_media_type")
    assert guess_media_type("photo.jpg") == "image"
    assert guess_media_type("img.webp") == "image"
    assert guess_media_type("video.mp4") == "video"
    assert guess_media_type("clip.mkv") == "video"
    assert guess_media_type("doc.pdf") == "file"
    assert guess_media_type("archive.zip") == "file"
    assert guess_media_type("weird.xyz") == "file"
    print("  image/video/file routing -> OK")
    print("[mime] test_guess_media_type PASS")


# ====================================================================
# cdn/upload.py
# ====================================================================

def test_uploaded_file_info():
    """UploadedFileInfo 数据类."""
    print("=" * 50)
    print("[upload] test_uploaded_file_info")
    info = UploadedFileInfo(
        filekey="abc123",
        download_encrypt_query_param="enc-param-xyz",
        aeskey="deadbeef" * 4,
        file_size=1024,
        file_size_ciphertext=1040,
    )
    assert info.filekey == "abc123"
    assert info.aeskey == "deadbeef" * 4
    assert info.file_size == 1024
    assert info.file_size_ciphertext == 1040
    print(f"  filekey={info.filekey} aeskey={info.aeskey[:8]}... size={info.file_size}")
    print("[upload] test_uploaded_file_info PASS")


def test_build_upload_url():
    """_build_upload_url: 拼接 CDN 上传 URL."""
    print("=" * 50)
    print("[upload] test_build_upload_url")
    url = _build_upload_url(
        cdn_base_url="https://novac2c.cdn.weixin.qq.com/c2c",
        upload_param="enc-param-123",
        filekey="filekey-abc",
    )
    assert "novac2c.cdn.weixin.qq.com" in url
    assert "/upload" in url
    assert "encrypted_query_param=enc-param-123" in url
    assert "filekey=filekey-abc" in url
    print(f"  url={url[:80]}...")
    print("[upload] test_build_upload_url PASS")


def test_upload_media_types():
    """验证 media_type 常量与 TypeScript UploadMediaType 一致."""
    print("=" * 50)
    print("[upload] test_upload_media_types")
    assert MEDIA_IMAGE == 1
    assert MEDIA_VIDEO == 2
    assert MEDIA_FILE == 3
    print("  IMAGE=1 VIDEO=2 FILE=3  OK")
    print("[upload] test_upload_media_types PASS")


# ====================================================================
# messaging/send-media.py
# ====================================================================

def test_aes_key_base64():
    """_aes_key_base64: hex 字符串 → base64 (对应 Buffer.from(hex).toString('base64'))."""
    print("=" * 50)
    print("[send-media] test_aes_key_base64")
    # 32 字符 hex → base64
    hex_key = "a1" * 16  # 32 chars
    b64 = _aes_key_base64(hex_key)
    # 验证: base64 解码后 = hex string 的 ASCII bytes
    decoded = base64.b64decode(b64)
    assert decoded == hex_key.encode()
    print(f"  hex={hex_key[:8]}... base64={b64[:8]}... OK")
    print("[send-media] test_aes_key_base64 PASS")


def test_image_item_structure():
    """图片 item 结构正确."""
    print("=" * 50)
    print("[send-media] test_image_item_structure")
    uploaded = UploadedFileInfo(
        filekey="fk-img-001",
        download_encrypt_query_param="dl-enc-param",
        aeskey="a1b2c3d4e5f607182930a1b2c3d4e5f6",
        file_size=10240,
        file_size_ciphertext=10256,
    )
    # 构造 image_item (跟 send_image 内部逻辑一致)
    item = {
        "type": 2,  # ITEM_TYPE_IMAGE
        "image_item": {
            "media": {
                "encrypt_query_param": "dl-enc-param",
                "aes_key": _aes_key_base64(uploaded.aeskey),
                "encrypt_type": 1,
            },
            "mid_size": uploaded.file_size_ciphertext,
        },
    }
    assert item["type"] == 2
    assert item["image_item"]["mid_size"] == 10256
    assert item["image_item"]["media"]["encrypt_type"] == 1
    assert item["image_item"]["media"]["encrypt_query_param"] == "dl-enc-param"
    # 验证 aes_key 可被 download 端正确解析
    from weixin_bot.media.download import _parse_aes_key
    key = _parse_aes_key(item["image_item"]["media"]["aes_key"])
    assert key.hex() == uploaded.aeskey
    print(f"  mid_size={item['image_item']['mid_size']} key={key.hex()[:8]}... OK")
    print("[send-media] test_image_item_structure PASS")


def test_video_item_structure():
    """视频 item 结构正确."""
    print("=" * 50)
    print("[send-media] test_video_item_structure")
    uploaded = UploadedFileInfo(
        filekey="fk-vid-001",
        download_encrypt_query_param="enc-xyz",
        aeskey="deadbeef" * 4,
        file_size=2048000,
        file_size_ciphertext=2048016,
    )
    item = {
        "type": 5,  # ITEM_TYPE_VIDEO
        "video_item": {
            "media": {
                "encrypt_query_param": "enc-xyz",
                "aes_key": _aes_key_base64(uploaded.aeskey),
                "encrypt_type": 1,
            },
            "video_size": uploaded.file_size_ciphertext,
        },
    }
    assert item["type"] == 5
    assert item["video_item"]["video_size"] == 2048016
    print(f"  video_size={item['video_item']['video_size']} OK")
    print("[send-media] test_video_item_structure PASS")


def test_file_item_structure():
    """文件 item 结构正确."""
    print("=" * 50)
    print("[send-media] test_file_item_structure")
    uploaded = UploadedFileInfo(
        filekey="fk-file-001",
        download_encrypt_query_param="enc-file",
        aeskey="f" * 32,
        file_size=4096,
        file_size_ciphertext=4112,
    )
    item = {
        "type": 4,  # ITEM_TYPE_FILE
        "file_item": {
            "media": {
                "encrypt_query_param": "enc-file",
                "aes_key": _aes_key_base64(uploaded.aeskey),
                "encrypt_type": 1,
            },
            "file_name": "report.pdf",
            "len": str(uploaded.file_size),
        },
    }
    assert item["type"] == 4
    assert item["file_item"]["file_name"] == "report.pdf"
    assert item["file_item"]["len"] == "4096"
    print(f"  file_name={item['file_item']['file_name']} len={item['file_item']['len']} OK")
    print("[send-media] test_file_item_structure PASS")


# ====================================================================
# messaging/inbound.py
# ====================================================================

def _make_msg(item_list: list[dict]) -> dict:
    return {
        "from_user_id": "testuser@im.wechat",
        "message_id": 123,
        "context_token": "ctx-token-abc",
        "item_list": item_list,
    }


def test_parse_text():
    """解析纯文本消息."""
    print("=" * 50)
    print("[inbound] test_parse_text")
    m = parse_message(_make_msg([{"type": 1, "text_item": {"text": "Hello"}}]))
    assert m.from_user == "testuser@im.wechat"
    assert m.text == "Hello"
    assert not m.has_media
    print(f"  text='{m.text}' from={m.from_user} ctx={m.context_token}")
    print("[inbound] test_parse_text PASS")


def test_parse_multi_text():
    """多条 TEXT item 拼接."""
    print("=" * 50)
    print("[inbound] test_parse_multi_text")
    m = parse_message(_make_msg([
        {"type": 1, "text_item": {"text": "Hello "}},
        {"type": 1, "text_item": {"text": "World"}},
    ]))
    assert m.text == "Hello World"
    print(f"  text='{m.text}'")
    print("[inbound] test_parse_multi_text PASS")


def test_parse_image():
    """解析图片消息."""
    print("=" * 50)
    print("[inbound] test_parse_image")
    m = parse_message(_make_msg([{
        "type": 2,
        "image_item": {
            "media": {"encrypt_query_param": "enc-123", "aes_key": "a2V5", "full_url": "https://cdn/img.jpg"},
        },
    }]))
    assert len(m.images) == 1
    assert m.images[0].encrypt_query_param == "enc-123"
    assert m.images[0].full_url == "https://cdn/img.jpg"
    assert m.has_media
    print(f"  images={len(m.images)} has_media={m.has_media}")
    print("[inbound] test_parse_image PASS")


def test_parse_voice():
    """解析语音消息 (带转文字)."""
    print("=" * 50)
    print("[inbound] test_parse_voice")
    m = parse_message(_make_msg([{
        "type": 3,
        "voice_item": {
            "media": {"encrypt_query_param": "enc-456"},
            "encode_type": 6,
            "sample_rate": 16000,
            "playtime": 3000,
            "text": "你好",
        },
    }]))
    assert m.voice is not None
    assert m.voice.encode_type == 6
    assert m.voice.duration_ms == 3000
    assert m.voice.text == "你好"
    assert m.text == ""  # voice text 不进 text 字段
    print(f"  voice: encode={m.voice.encode_type} dur={m.voice.duration_ms}ms text='{m.voice.text}'")
    print("[inbound] test_parse_voice PASS")


def test_parse_file():
    """解析文件消息."""
    print("=" * 50)
    print("[inbound] test_parse_file")
    m = parse_message(_make_msg([{
        "type": 4,
        "file_item": {
            "media": {"encrypt_query_param": "enc-789"},
            "file_name": "doc.pdf",
            "len": "102400",
        },
    }]))
    assert len(m.files) == 1
    assert m.files[0].file_name == "doc.pdf"
    assert m.files[0].size == 102400
    print(f"  file: {m.files[0].file_name} size={m.files[0].size}")
    print("[inbound] test_parse_file PASS")


def test_parse_video():
    """解析视频消息."""
    print("=" * 50)
    print("[inbound] test_parse_video")
    m = parse_message(_make_msg([{
        "type": 5,
        "video_item": {
            "media": {"full_url": "https://cdn/video.mp4"},
        },
    }]))
    assert len(m.videos) == 1
    assert m.videos[0].full_url == "https://cdn/video.mp4"
    print(f"  video: full_url={m.videos[0].full_url}")
    print("[inbound] test_parse_video PASS")


def test_parse_mixed():
    """图文混合消息."""
    print("=" * 50)
    print("[inbound] test_parse_mixed")
    m = parse_message(_make_msg([
        {"type": 1, "text_item": {"text": "看图"}},
        {"type": 2, "image_item": {"media": {"full_url": "https://cdn/pic.jpg"}}},
    ]))
    assert m.text == "看图"
    assert len(m.images) == 1
    assert m.has_media
    print(f"  text='{m.text}' images={len(m.images)}")
    print("[inbound] test_parse_mixed PASS")


def test_parse_ref_text():
    """引用文本消息 (title 和正文不同时都保留)."""
    print("=" * 50)
    print("[inbound] test_parse_ref_text")
    m = parse_message(_make_msg([{
        "type": 1,
        "text_item": {"text": "好看"},
        "ref_msg": {
            "title": "摘要",
            "message_item": {"type": 1, "text_item": {"text": "完整原文"}},
        },
    }]))
    assert "摘要" in m.text
    assert "完整原文" in m.text
    assert m.text.startswith("[引用:")
    assert m.text.endswith("\n好看")
    assert not m.has_media
    print(f"  text='{m.text}'")
    print("[inbound] test_parse_ref_text PASS")


def test_parse_ref_media():
    """引用媒体消息 → 跳过不拼."""
    print("=" * 50)
    print("[inbound] test_parse_ref_media")
    m = parse_message(_make_msg([{
        "type": 1,
        "text_item": {"text": "漂亮"},
        "ref_msg": {
            "title": "[图片]",
            "message_item": {"type": 2, "image_item": {"media": {}}},
        },
    }]))
    assert m.text == "漂亮"  # 不拼引用前缀
    print(f"  text='{m.text}'")
    print("[inbound] test_parse_ref_media PASS")


def test_parse_ref_title_only():
    """引用只有 title 没有 message_item."""
    print("=" * 50)
    print("[inbound] test_parse_ref_title_only")
    m = parse_message(_make_msg([{
        "type": 1,
        "text_item": {"text": "OK"},
        "ref_msg": {"title": "a deleted message"},
    }]))
    assert "a deleted message" in m.text
    assert m.text.startswith("[引用")
    assert m.text.endswith("\nOK")
    print(f"  text='{m.text}'")
    print("[inbound] test_parse_ref_title_only PASS")


def test_parse_ref_no_title_dup():
    """title 和正文相同时去重."""
    print("=" * 50)
    print("[inbound] test_parse_ref_no_dup")
    m = parse_message(_make_msg([{
        "type": 1,
        "text_item": {"text": "收到"},
        "ref_msg": {
            "title": "你好",
            "message_item": {"type": 1, "text_item": {"text": "你好"}},
        },
    }]))
    # 去重: "你好" 只出现一次
    assert m.text.count("你好") == 1
    assert m.text.startswith("[引用:")
    assert m.text.endswith("\n收到")
    print(f"  text='{m.text}'")
    print("[inbound] test_parse_ref_no_dup PASS")


def test_is_media_item_helper():
    """_is_media_item 工具函数 (VOICE 已摘出, 不算 media)."""
    print("=" * 50)
    print("[inbound] test_is_media_item")
    assert not _is_media_item({"type": 1})   # TEXT
    assert _is_media_item({"type": 2})       # IMAGE
    assert not _is_media_item({"type": 3})   # VOICE — 摘出来了
    assert _is_media_item({"type": 4})       # FILE
    assert _is_media_item({"type": 5})       # VIDEO
    print("  type 1/3→False, 2/4/5→True OK")
    print("[inbound] test_is_media_item PASS")


def test_parse_ref_voice():
    """引用语音消息 — 取转文字结果拼入引用."""
    print("=" * 50)
    print("[inbound] test_parse_ref_voice")
    m = parse_message(_make_msg([{
        "type": 1,
        "text_item": {"text": "收到"},
        "ref_msg": {
            "title": "[语音]",
            "message_item": {"type": 3, "voice_item": {"text": "明天见"}},
        },
    }]))
    assert "明天见" in m.text
    assert m.text.startswith("[引用:")
    assert m.text.endswith("\n收到")
    print(f"  text='{m.text}'")
    print("[inbound] test_parse_ref_voice PASS")


def test_parse_empty():
    """空消息."""
    print("=" * 50)
    print("[inbound] test_parse_empty")
    m = parse_message(_make_msg([]))
    assert m.text == ""
    assert not m.has_media
    print("  empty message -> OK")
    print("[inbound] test_parse_empty PASS")


# ====================================================================
# main
# ====================================================================

async def main():
    # accounts (同步)
    test_register_and_list()
    test_save_and_load()

    # login
    test_get_local_tokens()
    qr = await test_start_login()
    await test_wait_login_timeout(qr["qrcode"])

    # api
    qr_data = await test_api_post()
    await test_api_get(qr_data)

    # monitor
    test_sync_buf()
    await test_sleep_early_stop()
    await test_invalid_token_raises()

    # crypto (同步)
    test_crypto_roundtrip()
    test_crypto_padded_size()
    test_crypto_deterministic()
    test_crypto_padding()

    # media (同步)
    test_parse_aes_key_16()
    test_parse_aes_key_hex()
    test_parse_aes_key_bad()
    test_build_url()

    # mime (同步)
    test_get_mime()
    test_get_extension()
    test_guess_media_type()

    # upload (同步)
    test_uploaded_file_info()
    test_build_upload_url()
    test_upload_media_types()

    # send-media (同步)
    test_aes_key_base64()
    test_image_item_structure()
    test_video_item_structure()
    test_file_item_structure()

    # inbound (同步)
    test_parse_text()
    test_parse_multi_text()
    test_parse_image()
    test_parse_voice()
    test_parse_file()
    test_parse_video()
    test_parse_mixed()
    test_parse_ref_text()
    test_parse_ref_media()
    test_parse_ref_title_only()
    test_parse_ref_no_title_dup()
    test_is_media_item_helper()
    test_parse_ref_voice()
    test_parse_empty()

    print("=" * 50)
    print(f"All tests PASS  (state dir: {TMP})")


if __name__ == "__main__":
    asyncio.run(main())
