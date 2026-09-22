# -*- coding: utf-8 -*-
"""用 AI 从「解答」反推题目 —— 给没有提问的批注也能制卡。

为什么以**解答**为轴心，而不是以框选转写为轴心（用户实测得出的结论）
------------------------------------------------------------------
一条批注有三个可能的语境来源，实测对比（物理实验课第 34 页）：

  | 来源 | 内容 | 质量 |
  |---|---|---|
  | 框选转写 transcript | 「画面上部是前序内容的截断局部…无法辨认完整公式」 | ❌ 模型在描述图，不是转写 |
  | 讲义整页原文 | 「𝐷0 = 𝐷0 = 1 𝑛 ෍ 𝑖=1 𝑛 …」 | ⚠️ 公式被抽成散落单字符 |
  | **AI 的解答** | 「这部分在计算被测量D0多次测量平均值的A类标准不确定度…」 | ✅ 完整、通顺、已消化原文 |

**只有解答是高质量的** —— 它是视觉模型同时看了图 + 读了那块区域 + 组织过讲解之后的产物；
transcript 只是顺手做的一次转写，框歪了就退化成描述。

复用 deepreader 的引擎（兑现 R13）
--------------------------------
直接用真源 `ppt-deepreader` 的 `src.config.Settings` + `src.llm.LLMClient`：
重试/退避/超时/截断感知/剥思考链/密钥四级回退全都白拿，不另写 HTTP 客户端。
密钥来源实测为 `dsh:credentials.yaml`，不需要额外配置。
"""
from __future__ import annotations

import os
from typing import Any

from ledger import atomic_write_json, content_hash, load_json
import engine

HERE = os.path.dirname(os.path.abspath(__file__))

#: 改这个会让全部出题缓存失效（与 deepreader 的 PROMPT_VERSION 同思路）
QA_PROMPT_VERSION = 1

SYSTEM = """你是一位助教。任务：把一段讲解**改写成一道可以自测的问题**。

铁律（违反任何一条都算失败）：
1. 问题必须**只凭这段讲解就能回答** —— 讲解本身就是答案。
2. 必须针对讲解里的**具体知识点**：某个概念的定义、某个公式为什么成立、
   某个量的含义、某条规则该怎么用。禁止停留在"讲了什么"这一层。
3. **禁止空泛问法**：不许出现「这一块」「这段」「以下内容」「讲了什么」
   「说明了什么」「是什么」开头这类。
4. 问题不超过 40 字，一行，以问号结尾。
5. 只输出 JSON，不要任何解释、不要 markdown 围栏。

输出格式：
{"question": "……？"}"""

SYSTEM_STRICT = """上一次生成的问题不合格。这次更严格：

请针对讲解里**最具体的那一个点**提问 —— 比如某个公式为什么长这样、
某个量为什么这么定义、某条规则用的时候要注意什么。

反面例子（都不合格）：
  ✗「这一块讲的是什么？」
  ✗「A 类不确定度重要吗？」
  ✗「以下内容说明了什么？」

只输出 JSON：{"question": "……？"}"""

#: 空泛问法——命中即判定不合格
_BAD_PATTERNS = (
    "这一块", "这一段", "这段", "以下内容", "上面", "文中", "本页",
    "讲了什么", "是什么内容", "说明了什么", "有什么意义",
)


def engine_available() -> tuple[bool, str]:
    """探测 deepreader 引擎是否可用（委托给 engine 模块）。"""
    return engine.available()


def _settings():
    return engine.settings()


def _client():
    return engine.client()


# ---------------------------------------------------------------- 质量闸门

def check_question(q: str) -> tuple[bool, str]:
    """出题质量闸门。返回 (合格, 不合格原因)。

    这些规则都是照着「什么样的问题算垃圾」定的 —— 第一版制卡就是因为
    没有闸门，产出过「❓ 这一块讲的是什么？」配一段图片描述的废卡。
    """
    q = (q or "").strip()
    if not q:
        return False, "空问题"
    if "\n" in q:
        return False, "不是单行"
    if len(q) < 6:
        return False, f"太短（{len(q)} 字）"
    if len(q) > 80:
        return False, f"太长（{len(q)} 字）"
    if not q.endswith(("？", "?")):
        return False, "不是问句"
    for b in _BAD_PATTERNS:
        if b in q:
            return False, f"空泛（含「{b}」）"
    return True, ""


# ---------------------------------------------------------------- 缓存

def cache_path(library_root: str) -> str:
    return os.path.join(library_root, ".ledger", "qa_cache.json")


def _cache_key(explanation: str, latex: str, model: str) -> str:
    return content_hash({"v": QA_PROMPT_VERSION, "expl": explanation,
                         "latex": latex, "model": model})


def _load_cache(library_root: str) -> dict:
    return load_json(cache_path(library_root), None) or {"version": 1, "by_key": {}}


def _save_cache(library_root: str, d: dict) -> None:
    d["version"] = 1
    atomic_write_json(cache_path(library_root), d)


# ---------------------------------------------------------------- 出题

def _parse_question(text: str) -> str:
    """从模型返回里抠出 question。宽容优先，抠不出再退回整段文本。"""
    t = engine.strip_think(text or "").strip()
    try:
        obj = engine.parse_json(t)
        if isinstance(obj, dict) and obj.get("question"):
            return str(obj["question"]).strip()
        if isinstance(obj, list) and obj and isinstance(obj[0], dict):
            return str(obj[0].get("question") or "").strip()
    except Exception:  # noqa: BLE001 - 解析失败就退回原文
        pass
    # 退路：取第一行去掉引号/围栏
    line = t.split("\n")[0].strip().strip("`").strip('"').strip()
    return line


def generate_question(library_root: str, explanation: str, latex: str = "",
                      use_cache: bool = True) -> dict[str, Any]:
    """从一段解答反推一道题。返回 {question, model, tokens, cached, attempts, reason}。

    不合格会**再试一次**（换更严格的提示词）——照搬 deepreader「四层防线 + 针对性重写」的做法。
    两次都不合格就如实返回 reason，由调用方决定跳过（绝不硬塞一个凑数的问题）。
    """
    explanation = (explanation or "").strip()
    latex = (latex or "").strip()
    if len(explanation) < 30:
        return {"question": "", "reason": "解答太短，不足以出题", "attempts": 0}

    settings = _settings()
    model = getattr(settings, "llm_model", "") or "?"
    key = _cache_key(explanation, latex, model)

    cache = _load_cache(library_root)
    if use_cache and key in cache.get("by_key", {}):
        hit = dict(cache["by_key"][key])
        hit["cached"] = True
        return hit

    user = f"【讲解】\n{explanation}"
    if latex:
        user += f"\n\n【该区域的公式】\n{latex}"

    client = _client()
    total_tokens = 0
    attempts = 0
    last_reason = ""
    question = ""

    for system in (SYSTEM, SYSTEM_STRICT):
        attempts += 1
        try:
            res = client.chat(system, user, max_tokens=2048)
        except Exception as e:  # noqa: BLE001
            last_reason = f"调用失败：{type(e).__name__}: {e}"
            break
        total_tokens += int(res.usage.get("total_tokens") or 0)
        question = _parse_question(res.text)
        ok, why = check_question(question)
        if ok:
            last_reason = ""
            break
        last_reason = why

    ok, why = check_question(question)
    out: dict[str, Any] = {
        "question": question if ok else "",
        "reason": "" if ok else (last_reason or why),
        "model": model,
        "tokens": total_tokens,
        "attempts": attempts,
        "cached": False,
    }
    if ok:
        cache.setdefault("by_key", {})[key] = {k: v for k, v in out.items() if k != "cached"}
        _save_cache(library_root, cache)
    return out
