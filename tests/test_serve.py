# -*- coding: utf-8 -*-
"""本地服务的测试 —— 重点是**阶梯的判定逻辑**，那是"交互式追问"的核心。

为什么必须测：用户原话「**交互式必须要做**」，而交互式的价值全在
"默认不直接给答案、先反问他"这一条上。判定错了（比如把实质回答误判成卡壳），
就会在第二轮直接把答案倒给他 —— 那跟以前没有区别（实测：他那 12 次追问
每次都是直接给答案，读完就忘，只产出 13 张卡、0 条笔记）。

不启真服务：只测纯函数与账本写入，避免测试挂住端口。
跑法：python tests/test_serve.py
"""
from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

HERE = os.path.dirname(os.path.abspath(__file__))
PROJ = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(PROJ, "src"))
import serve as V  # noqa: E402
import study as S  # noqa: E402

FAILS: list[str] = []
PASSES: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    (PASSES if ok else FAILS).append(f"{name}{(' — ' + detail) if detail else ''}")


def _try_gbk(raw: bytes) -> str:
    """.bat 能不能按 GBK 解开（cmd 就是按系统 ANSI 码页读它的）。解不开返回空串。"""
    try:
        return raw.decode("gbk")
    except UnicodeDecodeError:
        return ""


# ---------------------------------------------------------------- 1 卡壳判定

for t in ("不知道", "不会", "答不上来", "想不出来", "没思路", "我放弃", "算了",
          "   ", "?", "……", "太难了", "别问了"):
    check(f"算卡壳：{t!r}", V.is_stall(t), f"is_stall={V.is_stall(t)}")

for t in ("脂筏把信号分子浓缩在一起，所以效率高",
          "我猜是因为它富含胆固醇",
          "脂筏上有 GPCR 这类受体",
          "我觉得是浓缩效应，但不确定特异性那部分",
          "能" * 3):
    check(f"不算卡壳（有实质内容）：{t[:16]!r}", not V.is_stall(t),
          f"is_stall={V.is_stall(t)}")

check("★ 「不确定」这种**有内容的犹豫**不算卡壳（否则会把思考误判成放弃）",
      not V.is_stall("我不太确定，可能是浓缩效应吧"), )

# ---------------------------------------------------------------- 2 硬开关

for t in ("别问了直接讲", "直接讲吧", "给答案", "告诉我吧", "别绕了"):
    check(f"硬开关命中：{t!r}", V.wants_answer(t))
check("普通问题不触发硬开关", not V.wants_answer("脂筏为什么能当信号转导平台？"))
check("★ 硬开关优先于阶梯：他明说了就直接给答案，不跟他绕",
      V.wants_answer("太难了，直接讲"))

# ---------------------------------------------------------------- 3 追问 prompt

p1 = V.build_ask_prompt("课件原文……", "他框选的一段", "为什么？", [], 27)
check("prompt 含课件原文（AI 必须有依据）", "课件原文" in p1)
check("prompt 含他框选的那一段", "他框选的一段" in p1)
check("prompt 含他的提问", "为什么？" in p1)
check("首次追问时卡壳数为 0", "连续卡壳 0 次" in p1)

hist = [{"role": "assistant", "text": "你觉得呢？"},
        {"role": "user", "text": "不知道", "stall": True},
        {"role": "assistant", "text": "再想想原文最后一句"},
        {"role": "user", "text": "不会", "stall": True}]
p2 = V.build_ask_prompt("X", "", "还是不会", hist, 27)
check("★ prompt 里报出**连续卡壳次数**（AI 靠它决定该不该给答案）",
      "连续卡壳 2 次" in p2, p2[-120:])
check("  把之前的来回也带进去", "你觉得呢？" in p2 and "再想想原文最后一句" in p2)
check("  没有框选时给兜底文案（不硬塞空串）", "他没有框选" in V.build_ask_prompt("X", "", "q", [], 1))
check("  历史过长时只取最近 8 条（不无限膨胀）",
      V.build_ask_prompt("X", "", "q",
                         [{"role": "user", "text": f"第{i}轮" * 5, "stall": False}
                          for i in range(20)], 1).count("第0轮") == 0)

# ---------------------------------------------------------------- 4 账本直写

ROOT = os.path.join(tempfile.gettempdir(), "course-pipeline-serve", str(os.getpid()))


def _cleanup() -> None:
    shutil.rmtree(ROOT, ignore_errors=True)
    p = os.path.dirname(ROOT)
    try:
        if os.path.isdir(p) and not os.listdir(p):
            os.rmdir(p)
    except OSError:
        pass


import atexit  # noqa: E402
atexit.register(_cleanup)

LIB = os.path.join(ROOT, "某课")
os.makedirs(LIB, exist_ok=True)
LES = "某课:讲次:节:survey"

r = V.record("/api/mark", "mark", {"lesson": LES, "at": "t1",
                                   "mark": {"p": 27, "r": [1.0, 2.0, 3.0, 4.0],
                                            "q": "脂筏算不算细胞器？"}}, LIB)
check("★ 标记能**直接进账本**（这就是「一键式」）", r.get("ok"), str(r))
d = S.load_study(LIB)
check("  账本里查得到",
      d["lessons"][LES]["marks"][0]["q"] == "脂筏算不算细胞器？",
      json.dumps(d["lessons"], ensure_ascii=False)[:160])

V.record("/api/grade", "grade", {"lesson": LES, "at": "t2", "mode": "ask-first",
                                 "qid": "C03.16#0", "grade": "mid"}, LIB)
d = S.load_study(LIB)
check("★ 自评也能直接进账本",
      d["lessons"][LES]["grades"] == {"C03.16#0": "mid"},
      str(d["lessons"][LES]["grades"]))

check("缺 lesson 时明确报错、不炸",
      not V.record("/api/mark", "mark", {"mark": {"p": 1}}, LIB).get("ok"))
check("未知类型明确报错",
      not V.record("/x", "什么鬼", {"lesson": LES}, LIB).get("ok"))

# 直写与"导出再导入"必须落成**同一份账本**（否则一键式与离线两条路会分叉）
one = {"lesson": LES, "mode": "survey", "exported_at": "t3",
       "marks": [{"p": 27, "r": [1.0, 2.0, 3.0, 4.0], "q": "脂筏算不算细胞器？"}]}
d2 = S.load_study(LIB)
rep = S.import_export(LIB, d2, one)
check("★ 直写过的标记再用导出导入一次 → 认得出是重复（两条路同一份账本）",
      any("重复跳过 1 处" in x for x in rep), str(rep))

# ---------------------------------------------------------------- 5 目录穿越

check("★ 静态路由拒绝目录穿越（.. 不许逃出库）",
      V.Handler.__dict__.get("_static") is not None)
_src = __import__("inspect").getsource(V.Handler._static)
check("  _static 里有 ../ 与绝对路径的拦截", "startswith(\"..\")" in _src
      and "isabs" in _src, _src[:200])

# ---------------------------------------------------------------- 6 一键启动的 .bat

# ★ 回归（真实事故，用户截图报的）：.bat 写成 UTF-8 → cmd.exe 按 GBK 解析 →
#   每行中文变乱码，**而且乱码行被当成命令执行**，连 `python run.py serve` 都被啃掉，
#   服务压根没起来，用户看到的就是"连不上大模型"。
#   `chcp 65001` 救不了：cmd 是先解析、后执行。
BAT = os.path.join(PROJ, "启动学习库.bat")
check(".bat 放在项目根（跟 run.py 并排，不然 cd %~dp0 后找不到 run.py）",
      os.path.isfile(BAT), BAT)
if os.path.isfile(BAT):
    raw = open(BAT, "rb").read()
    check("★ .bat 能被 **GBK** 解码（cmd 就是按系统 ANSI 码页读它的）",
          True if _try_gbk(raw) else False,
          "解不开 = 中文会变乱码并被当成命令执行")
    txt = _try_gbk(raw) or ""
    check("★ 关键命令行在（字面量，不许被乱码啃掉）",
          "python run.py serve" in txt)
    check("  用 chcp 936（和控制台同码页）", "chcp 936" in txt)
    check("  以 @echo off 开头", txt.lstrip().startswith("@echo off"))
    check("  有 pause（出错时窗口不闪退，用户看得到原因）", "pause" in txt)
    check("  没有 UTF-8 BOM（BOM 会让 cmd 把第一行认成乱码）",
          not raw.startswith(b"\xef\xbb\xbf"), str(list(raw[:3])))

# ---------------------------------------------------------------- 汇总

for p in PASSES:
    print("PASS  " + p)
for f_ in FAILS:
    print("FAIL  " + f_)
print("=" * 60)
print(f"通过 {len(PASSES)} / 失败 {len(FAILS)}")
raise SystemExit(1 if FAILS else 0)
