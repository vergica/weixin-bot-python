# weixin-bot

微信 iLink 机器人协议的独立 Python 客户端。零框架依赖 — 仅需 `httpx` 和 `cryptography`。

基于 [Tencent openclaw-weixin](https://github.com/Tencent/openclaw-weixin) TypeScript 实现，去除所有框架耦合（代码量减少约 40%，完整保留协议覆盖）。

[English](README.md) | [中文](README.zh.md)

## 快速开始

```bash
# 安装
git clone https://github.com/vergica/weixin-bot-python.git && cd weixin-bot
uv sync
uv run python -m weixin_bot
```

程序会：
1. 检测微信 iLink API 连通性
2. 显示二维码（用微信扫码绑定机器人）
3. 开始监听消息 — 发一条消息，机器人自动回复

状态文件存储在项目根目录的 `.weixin-bot/` 下：
```
.weixin-bot/
  accounts.json            # 账号索引
  accounts/{id}.json       # 凭据（token、baseUrl）
  accounts/{id}.sync.json  # getUpdates 游标
  config.yaml              # 可选配置文件
  media/                   # 下载的媒体文件
```

## 配置（可选）

创建 `.weixin-bot/config.yaml`：

```yaml
# API 基础 URL（IDC 重定向时修改）
base_url: "https://ilinkai.weixin.qq.com"

# CDN 基础 URL（媒体上传/下载）
cdn_base_url: "https://novac2c.cdn.weixin.qq.com/c2c"

# 长轮询超时时间（秒）
timeout: 35

# 机器人类型（3 = 企业机器人）
bot_type: 3

# 访问控制白名单 — 逗号分隔的用户 ID（空 = 全部允许）
allow_from: ""

# SKRouteTag 请求头（IDC 路由用，空 = 不发送）
route_tag: ""
```

或通过环境变量：

```bash
export WEIXIN_BOT_BASE_URL="https://ilinkai.weixin.qq.com"
export WEIXIN_BOT_CDN_BASE_URL="https://novac2c.cdn.weixin.qq.com/c2c"
export WEIXIN_BOT_ALLOW_FROM="user1@im.wechat,user2@im.wechat"
export WEIXIN_BOT_ROUTE_TAG="my-dc"
```

优先级：**环境变量 > config.yaml > 默认值**。

## 交互命令

监控运行期间，在微信上向机器人发送：

| 命令 | 说明 |
|---|---|
| `!list` | 列出 media 目录下的文件 |
| `!send 0` | 上传并发送编号为 0 的文件 |
| `!send 文件名` | 按文件名上传并发送 |
| `!typing` | 测试"对方正在输入…"指示器 |

## API 用法

### 基础 Echo 机器人

```python
import asyncio
from weixin_bot.auth.login import login
from weixin_bot.monitor.loop import MonitorLoop
from weixin_bot.messaging.inbound import parse_message
from weixin_bot.messaging.send import send_text

async def main():
    # 登录（展示二维码，保存凭据）
    result = await login()

    # 监控循环
    async def handle(msg: dict):
        m = parse_message(msg)
        # 使用 loop.ctx_tokens 获取已自动刷新的 context_token
        ctx = await loop.ctx_tokens.get(
            user_id=m.from_user, base_url=result["base_url"],
            auth_token=result["bot_token"],
        ) or m.context_token

        await send_text(
            to=m.from_user, text=f"Echo: {m.text}",
            base_url=result["base_url"],
            token=result["bot_token"],
            context_token=ctx,
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

### 发送媒体

```python
from weixin_bot.cdn.upload import upload_image
from weixin_bot.messaging.send_media import send_image

# 上传本地图片，然后发送
uploaded = await upload_image(
    "photo.jpg", "user@im.wechat",
    base_url=..., token=...,
)
await send_image(
    to="user@im.wechat", uploaded=uploaded,
    base_url=..., token=...,
)
```

### 打字指示器（自动 keepalive）

```python
from weixin_bot.messaging.typing import TypingIndicator

async with TypingIndicator(
    base_url=..., token=...,
    ilink_user_id="user@im.wechat",
    context_token=...,
):
    # Agent 处理消息、生成回复
    # 打字指示器每 5 秒自动续期
    await send_text(...)
# 退出时自动发送 CANCEL
```

手动控制（底层 API）：

```python
from weixin_bot.messaging.typing import get_config, send_typing, TYPING, CANCEL

cfg = await get_config(base_url=..., token=..., ilink_user_id="user@im.wechat")
await send_typing(base_url=..., token=..., typing_ticket=cfg["typing_ticket"], status=TYPING)
# ... 生成回复 ...
await send_typing(..., status=CANCEL)
```

### 持久化 HTTP 客户端

高频请求场景下复用连接池：

```python
from weixin_bot.api.client import WeixinApiClient

async with WeixinApiClient(base_url="https://...", token="...") as client:
    raw = await client.post("ilink/bot/sendmessage", body={...})
    data = await client.post_json("ilink/bot/getconfig", body=json_str)
```

### Markdown 过滤

```python
from weixin_bot.messaging.markdown import filter_markdown

clean = filter_markdown("**粗体** *中文斜体* ![图片](url)")
# → "**粗体** 中文斜体 "
```

## 模块结构

```
weixin_bot/
  api/client.py           # HTTP 客户端（WeixinApiClient + api_post/api_get）
  auth/accounts.py        # 凭据存储（JSON 文件）
  auth/login.py           # 二维码登录流程
  cdn/crypto.py           # AES-128-ECB 加解密
  cdn/upload.py           # 文件 → 加密 → CDN 上传
  media/download.py       # CDN 下载 → 解密（含降级重试）
  media/mime.py           # MIME 类型检测
  messaging/inbound.py    # 入站消息解析（文本/图片/语音/文件/视频 + 引用）
  messaging/send.py       # 发送文本消息（长消息自动拆分）
  messaging/send_media.py # 发送图片/视频/文件
  messaging/typing.py     # sendTyping + getConfig + TypingIndicator keepalive
  messaging/context_token.py # context_token 缓存 + 自动刷新
  messaging/markdown.py   # Markdown 过滤器（支持 CJK 判断）
  messaging/notices.py    # 错误通知回传用户
  monitor/loop.py         # 长轮询引擎（去重、退避、session 自动恢复）
  config.py               # 配置加载（config.yaml + 环境变量）
  __main__.py             # 交互测试入口
```

## 消息类型

| 类型 | 代码 | 入站 | 出站 |
|---|---|---|---|
| 文本 | 1 | ✅（含 emoji/@提及） | ✅ |
| 图片 | 2 | ✅（CDN 下载 + 解密） | ✅（上传 + 发送） |
| 语音 | 3 | ✅（SILK，含语音转文字） | ❌（需 SILK 编码器） |
| 文件 | 4 | ✅（CDN 下载 + 解密） | ✅（上传 + 发送） |
| 视频 | 5 | ✅（CDN 下载 + 解密） | ✅（上传 + 发送） |
| 引用消息 | ref_msg | ✅（解析为文本前缀） | ❌（协议未明确） |

## 致谢

本项目灵感来源于：

- [Tencent openclaw-weixin](https://github.com/Tencent/openclaw-weixin) — 微信 iLink 机器人协议的原始 TypeScript 实现
- [HKUDS/nanobot](https://github.com/HKUDS/nanobot) — 超轻量个人 AI agent，提供了高质量的参考实现

## 许可证

MIT
