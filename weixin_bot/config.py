"""项目配置 — .weixin-bot/config.yaml + 环境变量覆盖.

优先级: 环境变量 > config.yaml > 默认值.
yaml 解析内置实现, 无需外部依赖.
"""

from __future__ import annotations

import os
from pathlib import Path

from weixin_bot.auth.accounts import STATE_DIR

# 默认值
_DEFAULTS = {
    "base_url": "https://ilinkai.weixin.qq.com",
    "cdn_base_url": "https://novac2c.cdn.weixin.qq.com/c2c",
    "channel_version": "0.1.0",
    "timeout": 35,
    "bot_type": 3,
    "allow_from": "",  # 逗号分隔的白名单, 空=全部允许
}

_ENV_MAP = {
    "base_url": "WEIXIN_BOT_BASE_URL",
    "cdn_base_url": "WEIXIN_BOT_CDN_BASE_URL",
    "channel_version": "WEIXIN_BOT_CHANNEL_VERSION",
    "timeout": "WEIXIN_BOT_TIMEOUT",
    "bot_type": "WEIXIN_BOT_TYPE",
    "allow_from": "WEIXIN_BOT_ALLOW_FROM",
    "route_tag": "WEIXIN_BOT_ROUTE_TAG",
}


# ---------------------------------------------------------------------------
# internals
# ---------------------------------------------------------------------------

def _parse_flat_yaml(text: str) -> dict:
    """解析扁平的 key: value 格式, 支持注释和引号."""
    result: dict = {}
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if ":" not in stripped:
            continue
        key, _, value = stripped.partition(":")
        key = key.strip()
        value = value.strip().strip("\"'")
        if not key:
            continue
        # 类型推断
        if value.isdigit():
            result[key] = int(value)
        elif value.lower() in ("true", "false"):
            result[key] = value.lower() == "true"
        else:
            result[key] = value
    return result


def _load_file() -> dict:
    path = STATE_DIR / "config.yaml"
    if not path.exists():
        return {}
    try:
        return _parse_flat_yaml(path.read_text("utf-8"))
    except Exception:
        return {}


# ---------------------------------------------------------------------------
# public
# ---------------------------------------------------------------------------

def get(key: str) -> str | int | bool:
    """读取配置项."""
    # 1. 环境变量
    env_key = _ENV_MAP.get(key)
    if env_key:
        env_val = os.environ.get(env_key)
        if env_val is not None:
            default = _DEFAULTS.get(key)
            if isinstance(default, int):
                try:
                    return int(env_val)
                except ValueError:
                    pass
            return env_val

    # 2. config.yaml
    yaml_val = _load_file().get(key)
    if yaml_val is not None:
        return yaml_val

    # 3. 默认值
    return _DEFAULTS.get(key, "")


def allow_list() -> list[str]:
    """读取 allow_from 白名单 (逗号分隔).

    空列表 = 全部允许.
    """
    raw = str(get("allow_from")).strip()
    if not raw:
        return []
    return [u.strip() for u in raw.split(",") if u.strip()]
