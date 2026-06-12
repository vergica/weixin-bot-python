"""Markdown 过滤器 — 清洗微信不支持的 Markdown 语法.

对应原版 messaging/markdown-filter.ts. 非流式简化版:
  行级: 代码块直通, 分隔线/表格直通, 标题/引用/缩进去标记
  行内: 图片删除, CJK斜体删标记, 非CJK斜体保留, 粗体直通, strikethrough删除
"""

from __future__ import annotations

import re

_CJK = re.compile(r"[⺀-鿿가-힯豈-﫿]")


def _is_cjk(text: str) -> bool:
    return _CJK.search(text) is not None


def filter_markdown(text: str) -> str:
    """单 pass 过滤 Markdown.

    扫描文本, 行首检测块级模式, 正文扫描行内模式.
    行内模式 (***, *, ___, _, ![...](...)) 发现后子扫描到闭标记,
    根据 CJK 决定删/留标记, 然后继续主循环.
    """
    out: list[str] = []
    i = 0
    n = len(text)
    sol = True         # 当前位置是否为行首
    in_fence = False   # 是否在代码块内

    def _output_until(end: int) -> None:
        nonlocal i
        if end > i:
            out.append(text[i:end])
        i = end

    while i < n:
        # ---- 换行 ----
        if text[i] == "\n":
            out.append("\n")
            i += 1
            sol = True
            continue

        # ---- 代码块内 → 直通 ----
        if in_fence:
            if sol and i + 2 < n and text[i:i + 3] == "```":
                in_fence = False
                nl = text.find("\n", i)
                if nl == -1:
                    out.append(text[i:])
                    i = n
                else:
                    out.append(text[i:nl + 1])
                    i = nl + 1
                    sol = True
                continue
            nl = text.find("\n", i)
            if nl == -1:
                out.append(text[i:])
                i = n
            else:
                out.append(text[i:nl + 1])
                i = nl + 1
                sol = True
            continue

        # ---- 行首模式 ----
        if sol:
            # 代码块开始 ```
            if i + 2 < n and text[i:i + 3] == "```":
                in_fence = True
                nl = text.find("\n", i)
                if nl == -1:
                    out.append(text[i:])
                    i = n
                else:
                    out.append(text[i:nl + 1])
                    i = nl + 1
                    sol = True
                continue

            # 引用 > → 删标记
            if text[i] == ">":
                i += 1
                if i < n and text[i] == " ":
                    i += 1
                sol = False
                continue

            # 标题 ##### / ###### → 删标记
            if text[i] == "#":
                j = i
                while j < n and text[j] == "#":
                    j += 1
                cnt = j - i
                if cnt in (5, 6) and j < n and text[j] == " ":
                    i = j + 1
                    sol = False
                    continue
                sol = False
                continue

            # 分隔线 --- / *** / ___
            if text[i] in ("-", "*", "_"):
                ch = text[i]
                j = i
                while j < n and text[j] == ch:
                    j += 1
                if j - i >= 3:
                    while j < n and text[j] in (" ", "\t"):
                        j += 1
                    if j >= n or text[j] == "\n":
                        nl = text.find("\n", i)
                        if nl == -1:
                            out.append(text[i:])
                            i = n
                        else:
                            out.append(text[i:nl + 1])
                            i = nl + 1
                        sol = True
                        continue
                sol = False
                continue

            # 缩进 → 删前导空白
            if text[i] in (" ", "\t"):
                j = i
                while j < n and text[j] in (" ", "\t"):
                    j += 1
                if j < n:
                    i = j
                else:
                    i = n
                sol = False
                continue

            # 空行
            sol = False
            # fall through to body

        # ---- 正文扫描 ----
        sol = False

        # 查找下一个特殊字符
        special = min(
            (idx for idx in (
                text.find("\n", i),
                text.find("~", i),
                text.find("![", i),
                text.find("***", i),
                text.find("**", i),
                text.find("*", i),
                text.find("___", i),
                text.find("__", i),
                text.find("_", i),
            ) if idx != -1),
            default=-1,
        )

        if special == -1 or special > i:
            # 在特殊字符前都是安全文本
            limit = n if special == -1 else special
            _output_until(limit)
            if limit == n:
                break
            i = limit

        c = text[i]

        # ~ → strikethrough 删除
        if c == "~":
            i += 1
            continue

        # ![ → 图片: 找 ](url) 然后整段删除
        if c == "!" and i + 1 < n and text[i + 1] == "[":
            i += 2
            cb = text.find("]", i)
            if cb != -1 and cb + 1 < n and text[cb + 1] == "(":
                cp = text.find(")", cb + 2)
                if cp != -1:
                    i = cp + 1  # 整段跳过
                    continue
            # 不完整的图片语法 → 恢复 ![
            out.append("![")
            continue

        # *** → 粗斜体
        if c == "*" and i + 2 < n and text[i + 1] == "*" and text[i + 2] == "*":
            i += 3
            end = text.find("***", i)
            if end != -1:
                content = text[i:end]
                if _is_cjk(content):
                    out.append(content)
                else:
                    out.append(f"***{content}***")
                i = end + 3
            else:
                out.append("***")
            continue

        # ** → 粗体, 直通
        if c == "*" and i + 1 < n and text[i + 1] == "*":
            out.append("**")
            i += 2
            continue

        # *X → 斜体 (X 非空格非换行)
        if c == "*" and i + 1 < n and text[i + 1] not in (" ", "\n"):
            i += 1
            # 找闭标记 (跳过 **)
            j = i
            while j < n:
                if text[j] == "\n":
                    out.append("*")
                    break
                if text[j] == "*":
                    if j + 1 < n and text[j + 1] == "*":
                        j += 2  # 跳过 **
                        continue
                    content = text[i:j]
                    rest_start = j + 1
                    if _is_cjk(content):
                        out.append(content)
                    else:
                        out.append(f"*{content}*")
                    i = rest_start
                    break
                j += 1
            else:
                out.append("*")
            continue

        # ___ → 下划线粗斜体
        if c == "_" and i + 2 < n and text[i + 1] == "_" and text[i + 2] == "_":
            i += 3
            end = text.find("___", i)
            if end != -1:
                content = text[i:end]
                if _is_cjk(content):
                    out.append(content)
                else:
                    out.append(f"___{content}___")
                i = end + 3
            else:
                out.append("___")
            continue

        # __ → 下划线粗体, 直通
        if c == "_" and i + 1 < n and text[i + 1] == "_":
            out.append("__")
            i += 2
            continue

        # _X → 下划线斜体 (X 非空格非换行)
        if c == "_" and i + 1 < n and text[i + 1] not in (" ", "\n"):
            i += 1
            j = i
            while j < n:
                if text[j] == "\n":
                    out.append("_")
                    break
                if text[j] == "_":
                    if j + 1 < n and text[j + 1] == "_":
                        j += 2
                        continue
                    content = text[i:j]
                    rest_start = j + 1
                    if _is_cjk(content):
                        out.append(content)
                    else:
                        out.append(f"_{content}_")
                    i = rest_start
                    break
                j += 1
            else:
                out.append("_")
            continue

        # 普通字符
        out.append(c)
        i += 1

    return "".join(out)
