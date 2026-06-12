"""Weixin Bot — 功能验证入口.

运行: python -m weixin_bot
逐项测试所有模块, 每步打印详细结果.
"""

from __future__ import annotations

import asyncio
import json
import logging
import sys
from pathlib import Path

# ---------- logging ----------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("weixin-bot")

# 安静一点 — 只显示 WARNING 以上的第三方日志
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)
logging.getLogger("asyncio").setLevel(logging.WARNING)

from weixin_bot.api.client import api_post, api_get
from weixin_bot.auth.accounts import (
    STATE_DIR,
    list_ids,
    register_id,
    load as load_account,
    save as save_account,
)
from weixin_bot.auth.login import start_login, wait_login
from weixin_bot.monitor.loop import MonitorLoop, SessionExpired
from weixin_bot.messaging.inbound import parse_message, InboundMessage
from weixin_bot.messaging.send import send_text
from weixin_bot.media.download import download_media
from weixin_bot.cdn.upload import upload_image, upload_video, upload_file
from weixin_bot.messaging.send_media import send_image, send_video, send_file
from weixin_bot.media.mime import guess_media_type
from weixin_bot.messaging.typing import get_config, send_typing, TYPING, CANCEL
from weixin_bot.messaging.notices import send_error_notice
from weixin_bot.config import get as config_get

MEDIA_DIR = STATE_DIR / "media"

BASE = str(config_get("base_url"))


# ====================================================================
# helpers
# ====================================================================

def section(title: str) -> None:
    print()
    print("=" * 60)
    print(f"  {title}")
    print("=" * 60)


def ok(msg: str = "") -> None:
    suffix = f"  — {msg}" if msg else ""
    print(f"  [OK]{suffix}")


def fail(msg: str) -> None:
    print(f"  [FAIL] {msg}")


def info(key: str, value: str) -> None:
    print(f"  {key}: {value}")


# ====================================================================
# Step 1: 环境检查
# ====================================================================

async def step_env() -> None:
    section("Step 1: Environment")

    info("Python", sys.version.split()[0])
    info("State dir", str(STATE_DIR))
    info("API base", BASE)

    existing = list_ids()
    info("Registered accounts", str(len(existing)))
    for aid in existing:
        data = load_account(aid) or {}
        t = data.get("token", "")
        info(f"  - {aid}", f"token={'***' if t else '(none)'}, baseUrl={data.get('baseUrl', '?')}")

    ok("environment ready")


# ====================================================================
# Step 2: API 连通性
# ====================================================================

async def step_api_connectivity() -> None:
    section("Step 2: API connectivity")

    # 2a — POST
    print("  [2a] POST get_bot_qrcode ...")
    try:
        raw = await api_post(
            base_url=BASE,
            endpoint="ilink/bot/get_bot_qrcode?bot_type=3",
            body=json.dumps({"local_token_list": []}),
            token=None,
            timeout=15.0,
        )
        data = json.loads(raw)
        assert data.get("ret") == 0, f"ret={data.get('ret')}"
        qrcode = data["qrcode"]
        info("qrcode", f"{qrcode[:16]}... ({len(qrcode)} chars)")
        info("qrcode_url", data.get("qrcode_img_content", "")[:70] + "...")
        ok()
    except Exception as e:
        fail(f"api_post: {e}")
        return

    # 2b — GET (短超时, 只验证能连通)
    print("  [2b] GET get_qrcode_status ...")
    try:
        raw = await api_get(
            base_url=BASE,
            endpoint=f"ilink/bot/get_qrcode_status?qrcode={qrcode}",
            timeout=5.0,
        )
        sd = json.loads(raw)
        info("status", sd.get("status", "?"))
        ok()
    except Exception as e:
        # 超时正常 — 没人扫码
        info("result", f"timeout (expected — no one scanned): {e}")
        ok("timeout acceptable")

    ok("API connectivity complete")


# ====================================================================
# Step 3: 账号存储
# ====================================================================

async def step_accounts() -> None:
    section("Step 3: Account storage")

    test_id = "_test_weixin_bot"

    # 3a — 读写
    print("  [3a] save + load ...")
    save_account(test_id, token="test-token-123", base_url="https://example.com")
    data = load_account(test_id)
    assert data is not None
    assert data["token"] == "test-token-123"
    assert data["baseUrl"] == "https://example.com"
    assert "savedAt" in data
    info("savedAt", data["savedAt"])
    ok()

    # 3b — 不存在返回 None
    print("  [3b] load missing ...")
    assert load_account("does-not-exist-xyz") is None
    ok()

    # 3c — 注册索引
    print("  [3c] register_id + list_ids ...")
    register_id(test_id)
    ids = list_ids()
    assert test_id in ids
    info("registered count", str(len(ids)))
    ok()

    # 3d — 幂等注册
    print("  [3d] register_id idempotent ...")
    before = len(list_ids())
    register_id(test_id)
    assert len(list_ids()) == before
    ok()

    # 清理测试数据 — 不污染账号列表
    ids = list_ids()
    if test_id in ids:
        ids.remove(test_id)
        (STATE_DIR / "accounts.json").write_text(
            json.dumps(ids, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    test_file = STATE_DIR / "accounts" / f"{test_id}.json"
    if test_file.exists():
        test_file.unlink()
    info("cleanup", "test account removed")

    ok("account storage complete")


# ====================================================================
# Step 4: 登录
# ====================================================================

async def step_login() -> dict | None:
    section("Step 4: Login")

    # 已有有效 token → 直接复用, 不走扫码流程
    for aid in reversed(list_ids()):
        data = load_account(aid) or {}
        if data.get("token"):
            info("existing account", aid)
            info("base_url", data.get("baseUrl", BASE))
            print("  Already logged in — reusing saved credentials.")
            print("  (Delete .weixin-bot/ and re-run to force re-login.)")
            return {
                "connected": True,
                "account_id": aid,
                "bot_token": data["token"],
                "base_url": data.get("baseUrl", BASE),
            }

    # 无本地凭据 → 扫码登录
    print("  Getting QR code ...")
    qr = await start_login()
    info("qrcode", f"{qr['qrcode'][:16]}...")
    print()
    print("  ╔══════════════════════════════════════════════════════╗")
    print("  ║  Scan the QR code with WeChat, or open this link:   ║")
    print(f"  ║  {qr['qrcode_url']}")
    print("  ╚══════════════════════════════════════════════════════╝")
    print()
    print("  Waiting for scan (timeout 8 min, Ctrl+C to cancel)...")
    print("  Dots = waiting, newline = scanned")
    print()

    try:
        result = await wait_login(qr["qrcode"], verbose=True)
    except KeyboardInterrupt:
        print("\n  Login cancelled.")
        return None

    print()
    if not result.get("connected"):
        fail(result.get("message", "unknown"))
        return None

    if result.get("already_connected"):
        info("result", "already connected (no new credentials)")
        return None

    info("result", "connected!")
    info("account_id", result.get("account_id", "?"))
    info("base_url", result.get("base_url", "?"))
    token = result.get("bot_token", "")
    info("bot_token", f"{token[:10]}... ({len(token)} chars)" if token else "MISSING")

    # 保存
    account_id = result["account_id"]
    register_id(account_id)
    save_account(account_id, token=token, base_url=result.get("base_url", ""))
    info("saved", str(STATE_DIR / "accounts" / f"{account_id}.json"))
    ok("login complete, credentials saved")
    return result


def _save_media(data: bytes, filename: str) -> str:
    """保存媒体文件, 返回绝对路径."""
    MEDIA_DIR.mkdir(parents=True, exist_ok=True)
    path = MEDIA_DIR / filename
    path.write_bytes(data)
    return str(path)


# ====================================================================
# Step 5: 长轮询收消息
# ====================================================================

async def step_monitor(account_id: str, token: str, base_url: str) -> None:
    section("Step 5: Monitor (long-poll)")

    msg_count = 0

    def _err(prefix: str, e: Exception) -> str:
        """格式化错误消息: str(e) 为空时只显示类型名, 避免无意义的尾部冒号."""
        msg = str(e)
        return f"{prefix} [{type(e).__name__}]{': ' + msg if msg else ''}"

    # 扫描 media 目录
    def _list_media() -> list[Path]:
        if not MEDIA_DIR.exists():
            return []
        return sorted(
            [p for p in MEDIA_DIR.iterdir() if p.is_file()],
            key=lambda p: p.stat().st_mtime,
        )

    async def handle_message(msg: dict) -> None:
        nonlocal msg_count
        msg_count += 1
        m = parse_message(msg)

        # ---- 获取 fresh context_token (自动刷新过期 token) ----
        ctx = await loop.ctx_tokens.get(
            user_id=m.from_user, base_url=base_url, auth_token=token,
        )
        if not ctx:
            ctx = m.context_token  # fallback: 用消息里的原始 token

        types = []
        if m.text: types.append("text")
        if m.images: types.append(f"image×{len(m.images)}")
        if m.voice: types.append(f"voice({m.voice.duration_ms}ms)")
        if m.files: types.append(f"file×{len(m.files)}")
        if m.videos: types.append(f"video×{len(m.videos)}")
        print(f"\n  >>> #{msg_count} from={m.from_user} [{', '.join(types)}]")
        if m.text:
            print(f"      text: {m.text[:200]}{'...' if len(m.text)>200 else ''}")

        # ---- 入站媒体自动下载 ----
        if m.images:
            for i, img in enumerate(m.images):
                print(f"      image: full_url={img.full_url[:60]}...")
                try:
                    data = await download_media(img)
                    path = _save_media(data, f"{m.msg_id}_image_{i}.jpg")
                    print(f"      -> saved {len(data)}B to {path}")
                except Exception as e:
                    print(f"      -> download FAILED: {e}")
                    await send_error_notice(
                        to=m.from_user, text=_err("下载失败", e),
                        base_url=base_url, token=token, context_token=ctx,
                    )
        if m.voice:
            print(f"      voice: encode_type={m.voice.encode_type} text={m.voice.text[:50]}")
            if m.voice.text:
                print(f"      -> speech-to-text: {m.voice.text}")
        if m.files:
            for i, f in enumerate(m.files):
                print(f"      file: {f.file_name} size={f.size}")
                try:
                    data = await download_media(f.cdn)
                    name = f.file_name or f"file_{i}.bin"
                    path = _save_media(data, f"{m.msg_id}_{name}")
                    print(f"      -> saved {len(data)}B to {path}")
                except Exception as e:
                    print(f"      -> download FAILED: {e}")
                    await send_error_notice(
                        to=m.from_user, text=_err("下载失败", e),
                        base_url=base_url, token=token, context_token=ctx,
                    )
        if m.videos:
            for i, v in enumerate(m.videos):
                print(f"      video: full_url={v.full_url[:60]}...")
                try:
                    data = await download_media(v)
                    path = _save_media(data, f"{m.msg_id}_video_{i}.mp4")
                    print(f"      -> saved {len(data)}B to {path}")
                except Exception as e:
                    print(f"      -> download FAILED: {e}")
                    await send_error_notice(
                        to=m.from_user, text=_err("下载失败", e),
                        base_url=base_url, token=token, context_token=ctx,
                    )

        # ---- 命令处理 ----
        text = m.text.strip()

        if text == "!list":
            files = _list_media()
            if not files:
                await _reply(m, "Media 目录为空。", base_url, token, ctx)
            else:
                lines = [f"共 {len(files)} 个文件:"]
                for i, fp in enumerate(files):
                    size_kb = fp.stat().st_size / 1024
                    lines.append(f"  [{i}] {fp.name} ({size_kb:.1f} KB)")
                await _reply(m, "\n".join(lines), base_url, token, ctx)

        elif text == "!typing":
            # 测试打字指示器: getConfig → sendTyping(1) → 等3s → sendTyping(2)
            print("      !typing test start")
            try:
                cfg = await get_config(
                    base_url=base_url, token=token,
                    ilink_user_id=m.from_user, context_token=ctx,
                )
                ticket = cfg.get("typing_ticket", "")
                print(f"      getConfig: ret={cfg.get('ret')} ticket={'***' if ticket else 'MISSING'}")
                if not ticket:
                    await _reply(m, f"getConfig: 无 typing_ticket\n{json.dumps(cfg, ensure_ascii=False)}", base_url, token, ctx)
                    return

                await send_typing(
                    base_url=base_url, token=token,
                    ilink_user_id=m.from_user, typing_ticket=ticket, status=TYPING,
                )
                print("      sendTyping(1) OK — 对方正在输入...")
                await _reply(m, "typing on (3s)...", base_url, token, ctx)
                await asyncio.sleep(3)

                await send_typing(
                    base_url=base_url, token=token,
                    ilink_user_id=m.from_user, typing_ticket=ticket, status=CANCEL,
                )
                print("      sendTyping(2) OK — 取消")
                await _reply(m, "typing off. ticket ok!", base_url, token, ctx)
            except Exception as e:
                print(f"      !typing FAILED: {e}")
                await _reply(m, f"typing test failed: {e}", base_url, token, ctx)

        elif text.startswith("!send"):
            parts = text.split(maxsplit=1)
            arg = parts[1].strip() if len(parts) > 1 else ""
            files = _list_media()

            if not files:
                await _reply(m, "Media 目录为空。", base_url, token, ctx)
                return

            # 按序号或文件名匹配
            target: Path | None = None
            if arg.isdigit():
                idx = int(arg)
                if 0 <= idx < len(files):
                    target = files[idx]
            else:
                for fp in files:
                    if fp.name == arg or arg in fp.name:
                        target = fp
                        break

            if target is None:
                await _reply(m, f"找不到文件: {arg}\n输入 !list 查看文件列表。", base_url, token, ctx)
                return

            # 上传 + 发送
            fname = target.name
            fsize = target.stat().st_size
            media_type = guess_media_type(str(target))
            print(f"      !send {fname} ({media_type}, {fsize}B)")
            await _reply(m, f"正在上传 {fname} ({fsize/1024:.1f} KB)...", base_url, token, ctx)

            try:
                if media_type == "image":
                    uploaded = await upload_image(
                        str(target), m.from_user,
                        base_url=base_url, token=token,
                    )
                    result = await send_image(
                        to=m.from_user, uploaded=uploaded,
                        base_url=base_url, token=token, context_token=ctx,
                    )
                elif media_type == "video":
                    uploaded = await upload_video(
                        str(target), m.from_user,
                        base_url=base_url, token=token,
                    )
                    result = await send_video(
                        to=m.from_user, uploaded=uploaded,
                        base_url=base_url, token=token, context_token=ctx,
                    )
                else:
                    uploaded = await upload_file(
                        str(target), m.from_user,
                        base_url=base_url, token=token,
                    )
                    result = await send_file(
                        to=m.from_user, uploaded=uploaded, file_name=fname,
                        base_url=base_url, token=token, context_token=ctx,
                    )
                print(f"      -> sent! messageId={result['messageId'][:16]}...")
            except Exception as e:
                print(f"      -> send FAILED: {e}")
                await send_error_notice(
                    to=m.from_user, text=_err("发送失败", e),
                    base_url=base_url, token=token, context_token=ctx,
                )

        else:
            # 默认 echo
            if m.text:
                await _reply(m, f"[Echo] {m.text}", base_url, token, ctx)
            else:
                await _reply(m, "[Echo] (non-text message)", base_url, token, ctx)

    async def _reply(m: "InboundMessage", text: str, base_url: str, token: str, ctx: str) -> None:
        try:
            result = await send_text(
                to=m.from_user, text=text,
                base_url=base_url, token=token, context_token=ctx,
            )
            print(f"      reply: {text[:60]}...  messageId={result['messageId'][:12]}...")
        except Exception as e:
            print(f"      reply FAILED: {e}")
            await send_error_notice(
                to=m.from_user, text=_err("回复失败", e),
                base_url=base_url, token=token, context_token=ctx,
            )

    loop = MonitorLoop(
        base_url=base_url,
        token=token,
        account_id=account_id,
        on_message=handle_message,
    )

    print(f"  Starting monitor for {account_id} ...")
    print(f"  Commands: !list, !send <N|name>, !typing")
    print(f"  Press Ctrl+C to stop.\n")

    try:
        await loop.run()
    except SessionExpired:
        fail("Session expired! Token is no longer valid. Re-login needed.")
    except KeyboardInterrupt:
        print(f"\n  Stopped. Received {msg_count} message(s).")
    finally:
        await loop.stop()

    ok(f"monitor exited (messages received: {msg_count})")


# ====================================================================
# main
# ====================================================================

async def main() -> None:
    print()
    print("  Weixin Bot — Functional Verification")
    print("  -------------------------------------")

    await step_env()
    await step_api_connectivity()
    await step_accounts()

    # 登录
    login_result = await step_login()
    if login_result is None or login_result.get("already_connected"):
        # 回退: 用已有账号中最新注册且有 token 的
        picked = None
        for aid in reversed(list_ids()):
            data = load_account(aid) or {}
            if data.get("token"):
                picked = (aid, data)
                break
        if picked:
            account_id, data = picked
            token = data["token"]
            base_url = data.get("baseUrl", BASE)
            print()
            print(f"  Reusing existing account: {account_id}")
        else:
            print("  No existing account with valid token. Exiting.")
            return
    else:
        account_id = login_result["account_id"]
        token = login_result["bot_token"]
        base_url = login_result.get("base_url", BASE)

    await step_monitor(account_id, token, base_url)

    section("Done")
    print("  All steps complete.")
    print()


if __name__ == "__main__":
    asyncio.run(main())
