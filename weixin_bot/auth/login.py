"""二维码登录流程.

对应原版 auth/login-qr.ts, 去掉多 session 管理、MCP 集成等.
"""

from __future__ import annotations

import asyncio
import json
import logging
import sys
import time

from weixin_bot.api.client import api_post, api_get
from weixin_bot.auth.accounts import list_ids, load as load_account, save as save_account, register_id

logger = logging.getLogger(__name__)

FIXED_BASE_URL = "https://ilinkai.weixin.qq.com"
DEFAULT_BOT_TYPE = "3"
MAX_QR_REFRESH = 3


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _get_local_tokens() -> list[str]:
    """收集本地已登录账号的 token, 告诉服务端方便识别已有绑定."""
    tokens: list[str] = []
    for aid in reversed(list_ids()):
        data = load_account(aid)
        t = (data or {}).get("token", "").strip()
        if t and t not in tokens:
            tokens.append(t)
        if len(tokens) >= 10:
            break
    return tokens


def _display_qr(qrcode_url: str) -> None:
    """在终端展示二维码链接."""
    print(f"\nScan QR code with WeChat, or open this link:\n  {qrcode_url}\n")


# ---------------------------------------------------------------------------
# Step 1: 获取二维码
# ---------------------------------------------------------------------------

async def start_login(bot_type: str = DEFAULT_BOT_TYPE) -> dict:
    """请求登录二维码, 返回 {'qrcode': ..., 'qrcode_url': ...}."""
    raw = await api_post(
        base_url=FIXED_BASE_URL,
        endpoint=f"ilink/bot/get_bot_qrcode?bot_type={bot_type}",
        body=json.dumps({"local_token_list": _get_local_tokens()}),
        token=None,
        timeout=15.0,
    )
    data = json.loads(raw)
    return {"qrcode": data["qrcode"], "qrcode_url": data["qrcode_img_content"]}


# ---------------------------------------------------------------------------
# Step 2: 轮询扫码状态
# ---------------------------------------------------------------------------

async def wait_login(
    qrcode: str,
    timeout_ms: int = 480_000,
    bot_type: str = DEFAULT_BOT_TYPE,
    verbose: bool = False,
) -> dict:
    """轮询二维码状态直到扫码确认/过期/超时.

    返回 {'connected': bool, 'message': str, 'bot_token'?: str, 'account_id'?: str, 'base_url'?: str}
    connected=True 表示登录成功.
    """
    deadline = time.monotonic() + timeout_ms / 1000
    scanned_printed = False
    refresh_count = 0
    pending_code: str | None = None
    base_url = FIXED_BASE_URL

    while time.monotonic() < deadline:
        endpoint = f"ilink/bot/get_qrcode_status?qrcode={qrcode}"
        if pending_code:
            endpoint += f"&verify_code={pending_code}"

        # ---- 一次长轮询 ----
        try:
            raw = await api_get(base_url=base_url, endpoint=endpoint, timeout=35.0)
            sd = json.loads(raw)
        except Exception:
            # 网络闪断 / 超时 → 重试
            await asyncio.sleep(1)
            continue

        status = sd.get("status", "")

        if verbose:
            print(f"\n  [verbose] raw response: {json.dumps(sd, ensure_ascii=False)}")

        # ---- 状态分支 ----
        if status == "wait":
            sys.stdout.write(".")
            sys.stdout.flush()

        elif status == "scaned":
            if pending_code:
                pending_code = None  # 配对码正确, 清除
            if not scanned_printed:
                print("\nScanned — waiting for confirmation...")
                scanned_printed = True

        elif status == "need_verifycode":
            prompt = "Wrong code, try again: " if pending_code else "Enter number shown on phone: "
            pending_code = input(prompt).strip()
            continue  # 立即重试, 不 sleep

        elif status == "expired":
            refresh_count += 1
            if refresh_count > MAX_QR_REFRESH:
                return {"connected": False, "message": "QR expired too many times."}
            print(f"\nQR expired, refreshing ({refresh_count}/{MAX_QR_REFRESH})...")
            result = await start_login(bot_type)
            qrcode = result["qrcode"]
            _display_qr(result["qrcode_url"])
            scanned_printed = False

        elif status == "verify_code_blocked":
            print("\nToo many wrong attempts, refreshing QR...")
            pending_code = None
            refresh_count += 1
            if refresh_count > MAX_QR_REFRESH:
                return {"connected": False, "message": "Verification blocked."}
            result = await start_login(bot_type)
            qrcode = result["qrcode"]
            _display_qr(result["qrcode_url"])
            scanned_printed = False

        elif status == "scaned_but_redirect":
            host = sd.get("redirect_host", "")
            if host:
                base_url = f"https://{host}"
                logger.info("Redirecting to %s", host)

        elif status == "binded_redirect":
            print("\nAlready connected, no need to re-login.")
            return {"connected": True, "already_connected": True, "message": "Already connected."}

        elif status == "confirmed":
            bot_id = sd.get("ilink_bot_id", "")
            if not bot_id:
                return {"connected": False, "message": "Server did not return bot ID."}
            return {
                "connected": True,
                "bot_token": sd.get("bot_token", ""),
                "account_id": bot_id,
                "base_url": sd.get("baseurl") or FIXED_BASE_URL,
                "message": "Login successful.",
            }

        await asyncio.sleep(1)

    return {"connected": False, "message": "Login timed out."}


# ---------------------------------------------------------------------------
# 完整登录流程 (start + wait + save)
# ---------------------------------------------------------------------------

async def login() -> dict:
    """完整登录: 获取二维码 → 轮询 → 保存凭据. 同步阻塞直到完成."""
    # Step 1
    qr = await start_login()
    _display_qr(qr["qrcode_url"])
    print("Waiting for scan (timeout 8 min)...")

    # Step 2
    result = await wait_login(qr["qrcode"])
    print()  # 换行

    if not result["connected"]:
        return result

    if result.get("already_connected"):
        return result

    # Step 3: 保存凭据
    token = result.get("bot_token", "")
    account_id = result.get("account_id", "")
    base_url = result.get("base_url", "")
    if token and account_id:
        register_id(account_id)
        save_account(account_id, token=token, base_url=base_url)
        logger.info("Saved credentials for %s", account_id)

    return result
