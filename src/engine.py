# -*- coding: utf-8 -*-
"""共享引擎：复用真源 ppt-deepreader 的配置与 LLM 客户端。

兑现 R13「两个独立程序，只共享引擎三件套」：
  - **不复制代码**，直接 import 真源 `ppt-deepreader` 的 `src.config.Settings`
    与 `src.llm.LLMClient`；
  - 于是重试/退避/超时/**截断感知**/剥思考链/密钥四级回退全都白拿，
    不用另写 HTTP 客户端，也不会重踩它那 23 条实测坑；
  - 密钥来源实测是 `dsh:credentials.yaml`，**不需要任何额外配置**。

`qa.py`（AI 出题）与 `kcs.py`（知识点提取）都从这里拿引擎。
"""
from __future__ import annotations

import os
import sys
from typing import Any

HERE = os.path.dirname(os.path.abspath(__file__))
DEEPREADER = os.path.abspath(os.path.join(HERE, "..", "..", "ppt-deepreader"))

_imported = False


def _ensure_path() -> None:
    global _imported
    if DEEPREADER not in sys.path:
        sys.path.insert(0, DEEPREADER)
    _imported = True


def available() -> tuple[bool, str]:
    """探测引擎是否可用。返回 ``(可用, 说明)``。不联网。"""
    if not os.path.isdir(os.path.join(DEEPREADER, "src")):
        return False, f"找不到真源引擎目录：{DEEPREADER}"
    try:
        s = settings()
        if not s.llm_api_key or s.llm_api_key == "not-needed":
            return False, "引擎可用，但没有拿到 API Key（环境变量 / .env / DSH 凭据库都没有）"
        return True, f"ok（model={s.llm_model}, key={s.llm_api_key_source}）"
    except Exception as e:  # noqa: BLE001 - 任何导入/配置失败都算不可用
        return False, f"{type(e).__name__}: {e}"


def settings():
    _ensure_path()
    from src.config import Settings  # noqa: PLC0415
    return Settings.load()


def client():
    _ensure_path()
    from src.llm import LLMClient  # noqa: PLC0415
    return LLMClient(settings())


def model_name() -> str:
    try:
        return getattr(settings(), "llm_model", "") or "?"
    except Exception:  # noqa: BLE001
        return "?"


def strip_think(text: str) -> str:
    _ensure_path()
    from src.llm import strip_think as f  # noqa: PLC0415
    return f(text)


def parse_json(text: str) -> Any:
    """宽容 JSON 解析（deepreader 的实现：围栏/废话/尾随逗号/中文引号都能吃）。"""
    _ensure_path()
    from src.llm import parse_json_lenient  # noqa: PLC0415
    return parse_json_lenient(strip_think(text or ""))


def chat(system: str, user: str, *, max_tokens: int = 4096) -> tuple[str, dict[str, Any]]:
    """发一轮对话，返回 ``(文本, 元信息)``。元信息含真实模型名与 token 用量。

    注意：**必须读响应里的 model 字段**才知道真实使用的模型 ——
    网关会把某些 id 静默改写（deepreader 实测踩过）。
    """
    res = client().chat(system, user, max_tokens=max_tokens)
    meta = {
        "model": res.model or model_name(),
        "tokens": int((res.usage or {}).get("total_tokens") or 0),
        "finish_reason": res.finish_reason,
        "truncated": res.truncated,
        "elapsed_s": round(res.elapsed_s, 1),
    }
    return res.text or "", meta


def chat_json(system: str, user: str, *, max_tokens: int = 4096,
              cap: int = 32000) -> tuple[Any, dict[str, Any]]:
    """发一轮对话并解析 JSON。

    **截断时逐级 ×2 抬升重试** —— 这是 deepreader 用真金白银换来的结论：
    同上限重发必然再截断一次（等于白烧一次推理）。本机实测模型思考链很长，
    6000 token 会被思考吃光、正文为空，所以这里必须抬升。

    抬到上限还不够就**明确报错**，并区分「上限不够」与「模型写不对」。
    """
    mt = max_tokens
    ladder: list[int] = []
    last_meta: dict[str, Any] = {}
    while True:
        text, meta = chat(system, user, max_tokens=mt)
        last_meta = meta
        if not meta.get("truncated"):
            obj = parse_json(text)
            meta["attempts"] = len(ladder) + 1
            meta["tokens_escalation"] = ladder
            meta["max_tokens_used"] = mt
            return obj, meta
        ladder.append(mt)
        if mt >= cap:
            raise ValueError(
                f"输出被截断且已抬升到上限 {cap}（抬升过程 {ladder}）—— "
                f"this 说明单次请求要吐的内容确实太大：请把 window 调小，"
                f"或降低单条输出量。**不要**把它误判成「模型写不对」。"
            )
        mt = min(mt * 2, cap)
