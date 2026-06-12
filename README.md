# weixin-bot

Standalone Python client for the WeChat iLink bot protocol. Zero framework dependencies — just `httpx` and `cryptography`.

Based on the [Tencent openclaw-weixin](https://github.com/Tencent/openclaw-weixin) TypeScript implementation, stripped of all framework coupling (~40% code reduction while preserving full protocol coverage).

[English](README.md) | [中文](README.zh.md)

## Quick Start

```bash
# Install
git clone <repo-url> && cd weixin-bot
uv sync
uv run python -m weixin_bot
```

The bot will:
1. Check connectivity to the WeChat iLink API
2. Show a QR code (scan with WeChat to bind the bot)
3. Start monitoring — send it a message and it echoes back

State is stored in `.weixin-bot/` under the project root:
```
.weixin-bot/
  accounts.json            # account index
  accounts/{id}.json       # credentials (token, baseUrl)
  accounts/{id}.sync.json  # getUpdates cursor
  config.yaml              # optional configuration
  media/                   # downloaded media files
```

## Configuration (optional)

Create `.weixin-bot/config.yaml`:

```yaml
# API base URL (change for IDC redirect)
base_url: "https://ilinkai.weixin.qq.com"

# CDN base URL for media upload/download
cdn_base_url: "https://novac2c.cdn.weixin.qq.com/c2c"

# Long-poll timeout in seconds
timeout: 35

# Bot type (3 = enterprise bot)
bot_type: 3
```

Or via environment variables:

```bash
export WEIXIN_BOT_BASE_URL="https://ilinkai.weixin.qq.com"
export WEIXIN_BOT_CDN_BASE_URL="https://novac2c.cdn.weixin.qq.com/c2c"
```

Priority: **env var > config.yaml > default**.

## Interactive Commands

While the monitor is running, send these to the bot on WeChat:

| Command | Description |
|---|---|
| `!list` | List files in the media directory |
| `!send 0` | Upload & send file at index 0 |
| `!send filename` | Upload & send by filename |
| `!typing` | Test typing indicator |

## API Usage

```python
import asyncio
from weixin_bot.auth.login import login
from weixin_bot.monitor.loop import MonitorLoop
from weixin_bot.messaging.inbound import parse_message
from weixin_bot.messaging.send import send_text

async def main():
    # Login (opens QR, saves credentials)
    result = await login()

    # Monitor loop
    async def handle(msg: dict):
        m = parse_message(msg)
        await send_text(
            to=m.from_user, text=f"Echo: {m.text}",
            base_url=result["base_url"],
            token=result["bot_token"],
            context_token=m.context_token,
        )

    loop = MonitorLoop(
        base_url=result["base_url"],
        token=result["bot_token"],
        account_id=result["account_id"],
        on_message=handle,
    )
    try:
        await loop.run()
    except KeyboardInterrupt:
        await loop.stop()

asyncio.run(main())
```

### Send Media

```python
from weixin_bot.cdn.upload import upload_image
from weixin_bot.messaging.send_media import send_image

# Upload a local image, then send it
uploaded = await upload_image(
    "photo.jpg", "user@im.wechat",
    base_url=..., token=...,
)
await send_image(
    to="user@im.wechat", uploaded=uploaded,
    base_url=..., token=...,
)
```

### Typing Indicator

```python
from weixin_bot.messaging.typing import get_config, send_typing, TYPING, CANCEL

cfg = await get_config(
    base_url=..., token=...,
    ilink_user_id="user@im.wechat",
)
await send_typing(
    base_url=..., token=...,
    ilink_user_id="user@im.wechat",
    typing_ticket=cfg["typing_ticket"],
    status=TYPING,  # show "typing..."
)
# ... generate reply ...
await send_typing(..., status=CANCEL)
```

### Markdown Filter

```python
from weixin_bot.messaging.markdown import filter_markdown

clean = filter_markdown("**bold** *中文斜体* ![img](url)")
# → "**bold** 中文斜体 "
```

## Module Structure

```
weixin_bot/
  api/client.py           # HTTP client (api_post, api_get)
  auth/accounts.py        # Token storage (JSON files)
  auth/login.py           # QR code login flow
  cdn/crypto.py           # AES-128-ECB encrypt/decrypt
  cdn/upload.py           # File → encrypt → CDN upload
  media/download.py       # CDN download → decrypt
  media/mime.py           # MIME type detection
  messaging/inbound.py    # Parse inbound messages (text/image/voice/file/video + ref_msg)
  messaging/send.py       # Send text messages
  messaging/send_media.py # Send image/video/file
  messaging/typing.py     # sendTyping + getConfig
  messaging/markdown.py   # Markdown filter (CJK-aware)
  messaging/notices.py    # Error notification to users
  monitor/loop.py         # Long-poll getUpdates engine
  config.py               # config.yaml + env var loader
  __main__.py             # Interactive test entry point
```

## Message Types

| Type | Code | Inbound | Outbound |
|---|---|---|---|
| Text | 1 | ✅ (incl. emoji/@mentions) | ✅ |
| Image | 2 | ✅ (CDN download + decrypt) | ✅ (upload + send) |
| Voice | 3 | ✅ (SILK, with speech-to-text) | ❌ (needs SILK encoder) |
| File | 4 | ✅ (CDN download + decrypt) | ✅ (upload + send) |
| Video | 5 | ✅ (CDN download + decrypt) | ✅ (upload + send) |
| Quoted message | ref_msg | ✅ (parsed into text prefix) | ❌ (protocol unclear) |

## License

MIT
