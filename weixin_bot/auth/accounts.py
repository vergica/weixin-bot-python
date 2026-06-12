"""账号凭据持久化 — JSON 文件读写.

{项目根目录}/.weixin-bot/
  accounts.json          → ["id1", "id2"]         账号索引
  accounts/{id}.json     → {token, baseUrl, ...}  单个账号凭据
"""

from __future__ import annotations

import json
import os
from pathlib import Path

# 项目根目录 (weixin_bot/auth/accounts.py → 上两级)
_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
STATE_DIR = Path(os.environ.get("WEIXIN_BOT_STATE_DIR", _PROJECT_ROOT / ".weixin-bot"))
DEFAULT_BASE_URL = "https://ilinkai.weixin.qq.com"


def _index_path() -> Path:
    return STATE_DIR / "accounts.json"


def _account_path(account_id: str) -> Path:
    return STATE_DIR / "accounts" / f"{account_id}.json"


# ---------------------------------------------------------------------------
# Account index
# ---------------------------------------------------------------------------

def list_ids() -> list[str]:
    """返回所有已注册的 account_id."""
    try:
        if _index_path().exists():
            data = json.loads(_index_path().read_text(encoding="utf-8"))
            if isinstance(data, list):
                return [s for s in data if isinstance(s, str) and s.strip()]
    except (json.JSONDecodeError, OSError):
        pass
    return []


def register_id(account_id: str) -> None:
    """添加 account_id 到索引 (已存在则跳过)."""
    ids = list_ids()
    if account_id in ids:
        return
    ids.append(account_id)
    _index_path().parent.mkdir(parents=True, exist_ok=True)
    _index_path().write_text(json.dumps(ids, ensure_ascii=False, indent=2), encoding="utf-8")


# ---------------------------------------------------------------------------
# Per-account credential file
# ---------------------------------------------------------------------------

def load(account_id: str) -> dict | None:
    """读取单个账号凭据, 不存在返回 None."""
    try:
        p = _account_path(account_id)
        if p.exists():
            return json.loads(p.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        pass
    return None


def save(account_id: str, /, token: str = "", base_url: str = "") -> None:
    """保存 (或更新) 账号凭据. 对已有文件做 merge."""
    existing = load(account_id) or {}
    if token:
        existing["token"] = token
        existing["savedAt"] = _now_iso()
    if base_url:
        existing["baseUrl"] = base_url
    else:
        existing.setdefault("baseUrl", DEFAULT_BASE_URL)

    p = _account_path(account_id)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(existing, ensure_ascii=False, indent=2), encoding="utf-8")


def _now_iso() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat()
