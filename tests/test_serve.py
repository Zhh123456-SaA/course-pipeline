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

import glob
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
check("  没有框选时给兜底文案（不硬塞空串）", "没框选" in V.build_ask_prompt("X", "", "q", [], 1))
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

# ---------------------------------------------------------------- 4b 追问来回要能查看

# 用户原话：「那我在**哪里查看**我和 ai 的交互和反问呢」——
# 在这之前，阶梯的来回只活在浏览器一个小面板里、服务端只在内存里，
# **一个字都没进账本**，关掉就没了（又变回"读完就忘"）。
V.study.append_ladder(LIB, LES, 27, "脂筏为什么能当信号转导平台？",
                      "你框那段里，脂筏富含的是哪两类成分？", stalls=0)
V.study.append_ladder(LIB, LES, 27, "不知道",
                      "那我们缩小一点，先说说细胞膜的主要成分是什么？", stalls=1)
V.study.append_ladder(LIB, LES, 27, "脂筏为什么能当信号转导平台？",
                      "你框那段里，脂筏富含的是哪两类成分？", stalls=0)   # 同一轮重复
got = S.ladder_of(LIB)
check("★ 追问来回能存进账本、也能读回来", len(got) == 2, str(len(got)))
check("★ 记下了「我问的」与「AI 回的」原文",
      any(x["q"] == "不知道" and "缩小一点" in x["a"] for x in got),
      json.dumps(got, ensure_ascii=False)[:180])
check("  带上了卡壳次数（阶梯靠它决定给不给答案）",
      any(x["stalls"] == 1 for x in got))
check("  同一轮重复调用不重复记", len([x for x in got if x["q"] == "不知道"]) == 1)
check("  按 lesson 过滤", len(S.ladder_of(LIB, LES)) == 2
      and S.ladder_of(LIB, "别的课") == [])
V.study.append_ladder(LIB, LES, 1, "  ", "x")
check("空问题不记（不产生垃圾条目）", len(S.ladder_of(LIB)) == 2)

# ★ 实测踩到"读回来是空的"：服务端没传 course 时把**库根**当成课程目录，
#   去找 <库根>/.ledger/study.json（不存在）。库根与课程目录两种都要能吃。
ROOT2 = os.path.join(ROOT, "库根")
shutil.rmtree(ROOT2, ignore_errors=True)
os.makedirs(os.path.join(ROOT2, "课A"), exist_ok=True)
V.study.append_ladder(os.path.join(ROOT2, "课A"), "课A:讲:节:x", 3, "问题甲", "回答甲")
check("★ 传库根也能读到（自动扫每门课）", len(S.ladder_of(ROOT2)) == 1,
      str(S.ladder_of(ROOT2)))
check("  传课程目录也能读到", len(S.ladder_of(os.path.join(ROOT2, "课A"))) == 1)
check("  课程里有 ladder 字段（不是只存在内存）",
      bool(S.load_study(os.path.join(ROOT2, "课A"))["lessons"]["课A:讲:节:x"].get("ladder")))

# ---------------------------------------------------------------- 4b2 子对话
# 用户原话：「我希望制作成**子对话**的形式……**可以之后再调出来读**」。
# 光有平铺的来回列表，读起来是一串孤立的问答；带上 tid，同一段对话的来回
# 才能重新拼回去 —— 几个月后点开还能从头读到尾，还能接着往下问。
V.study.append_ladder(LIB, LES, 27, "那胆固醇在里头干嘛？", "你觉得它凭什么待在膜里？",
                      tid="m27-11_22_33_44")
V.study.append_ladder(LIB, LES, 27, "还是不懂", "那我们缩小一点，先看膜的成分。",
                      stalls=1, tid="m27-11_22_33_44")
V.study.append_ladder(LIB, LES, 40, "另一段对话", "先说第一个。",
                      tid="m40-5_6_7_8")
V.study.append_ladder(LIB, LES, 40, "另一段对话", "先说第一个。", tid="m40-9_9_9_9")
th = S.threads_of(LIB, LES)
check("★ 同一段对话的来回拼回一起（不是平铺的流水账）",
      any(x["tid"] == "m27-11_22_33_44" and x["n"] == 2 for x in th),
      json.dumps(th, ensure_ascii=False)[:200])
check("★ 标题 = 这段对话的**第一个问题**",
      any(x["tid"] == "m27-11_22_33_44"
          and x["title"] == "那胆固醇在里头干嘛？" for x in th))
check("  页号、轮数、首末时间都带上了（卡片上要显示）",
      all(x["page"] and x["n"] and x["at"] and x["at_end"] for x in th))
check("★ 不同的框 = 不同的子对话（不会串成一段）",
      len([x for x in th if x["tid"].startswith("m40-")]) == 2)
check("★ 最新的那段排最前（先看刚聊的）",
      th[0]["tid"] == "m40-9_9_9_9", th[0]["tid"])
check("★ tid 不同的同样问答**各记一条**（去重键里必须含 tid）",
      len([x for x in S.ladder_of(LIB, LES) if x["q"] == "另一段对话"]) == 2)
# 功能上线前记的老账本没有 tid —— 按**页**归段。这不是凑数：老界面的会话键
# 就是「节:第N页」，一页本来就只有一段对话流；硬按"一条一段"拆的话，
# 以前 8 轮对话会变成 8 张单轮卡片，比平铺列表还难读。
check("★ 老账本（没 tid）按**页**归段（对上老界面「一页一段对话」）",
      len([x for x in th if x["tid"] == "@p27" and x["n"] == 2]) == 1,
      str([(x["tid"], x["n"]) for x in th]))
check("  而且标题仍然是这段的第一问",
      any(x["tid"] == "@p27" and x["title"] == "脂筏为什么能当信号转导平台？"
          for x in th))

# 「接着问」靠它：把老对话从账本读回来喂给 AI（内存里的会话重启就没了）
h = V.seed_hist(LIB, LES, "m27-11_22_33_44")
check("★ seed_hist 把一段老对话读回来（隔几个月也能接着问）",
      [x["text"] for x in h] == ["那胆固醇在里头干嘛？", "你觉得它凭什么待在膜里？",
                                 "还是不懂", "那我们缩小一点，先看膜的成分。"],
      json.dumps(h, ensure_ascii=False)[:200])
check("  读回来的来回带着「卡壳」判定（阶梯的计数才不会清零）",
      any(x.get("stall") for x in h if x["role"] == "user"))
check("  要是不存在的 tid → 空表，不炸", V.seed_hist(LIB, LES, "根本没这段") == [])
check("  空 tid → 空表（不去扫整个库）", V.seed_hist(LIB, LES, "") == [])

# ---------------------------------------------------------------- 4b3 接着哪一段问
# 真调 ask_once，但把**引擎换掉** —— 不联网、不烧 key。
# 盯的是「换 tid = 换一段对话」这条：客户端点了「接着问」，
# 服务端必须把**那一段**的来回从账本捞回来喂给 AI，而不是张冠李戴。
_chat, _avail, _strip = V.engine.chat, V.engine.available, V.engine.strip_think
_prompts: list[str] = []
V.engine.available = lambda: (True, "ok")
V.engine.strip_think = lambda s: (s or "").strip()


def _fake_chat(system, prompt, **kw):
    _prompts.append(prompt)
    return ("你先说说膜上都有哪些成分？", {})


V.engine.chat = _fake_chat
try:
    r1 = V.ask_once(LIB, {"session": "s1", "lesson": LES, "page": 27,
                          "question": "那胆固醇到底干嘛的？", "tid": "m27-11_22_33_44"})
    check("★ 追问把这一段对话的编号回给前端（卡片才摆得对）",
          r1.get("ok") and r1.get("tid") == "m27-11_22_33_44", str(r1)[:140])
    check("★ 接着一段**老**对话问：服务端从账本把前面的来回捞回来喂给 AI",
          "那胆固醇在里头干嘛？" in _prompts[-1] and "还是不懂" in _prompts[-1],
          _prompts[-1][-260:])
    check("  新的一轮记进**同一段**（不是另起一段）",
          any(x.get("tid") == "m27-11_22_33_44" and x["q"] == "那胆固醇到底干嘛的？"
              for x in S.ladder_of(LIB, LES)))

    V.ask_once(LIB, {"session": "s1", "lesson": LES, "page": 40,
                     "question": "换成另一段了", "tid": "m40-9_9_9_9"})
    check("★ 换了编号 = 换了对话：上一段的来回不再跟着（不糊成一团）",
          "那胆固醇在里头干嘛？" not in _prompts[-1], _prompts[-1][-200:])

    V.ask_once(LIB, {"session": "s1", "lesson": LES, "page": 27,
                     "question": "不知道", "tid": "m27-11_22_33_44"})
    check("  卡壳那句被标进账本（阶梯的 3 次计数后面还要用）",
          any(x.get("stall") for x in S.ladder_of(LIB, LES) if x["q"] == "不知道"))
    check("  换回来时老那段又接上了（前后能来回切）",
          "那胆固醇在里头干嘛？" in _prompts[-1], _prompts[-1][-200:])

    # 老版页面（不带 tid）也不能坏：按「节+页」兜底
    r2 = V.ask_once(LIB, {"session": "旧页面:p3", "lesson": LES, "page": 3,
                          "question": "没带编号的老客户端"})
    check("  老页面不带 tid 也能用（服务端按「节+页」兜底，不炸）",
          r2.get("ok") and r2.get("tid") == "旧页面:p3:p3", str(r2)[:120])
finally:
    V.engine.chat, V.engine.available, V.engine.strip_think = _chat, _avail, _strip

# ---------------------------------------------------------------- 4b4 框的真相源
# 评审结论 A2：框以前有**两份真相**（页面 localStorage + 账本），而页面**只写不读**。
# 后果①换浏览器/清缓存，课件上的框全没了、右边的对话还在；
# 后果②对话卡片的「回到这一页」只能跳到页、跳不到那个框。
# 这条链子（/api/mark 写 → marks_of 读回来 → 页面画回去）以前从没被测过。
BOX = {"p": 27, "r": [12.0, 34.0, 56.0, 78.0], "q": "我当时框的这一块"}
V.record("/api/mark", "mark", {"lesson": LES, "at": "t9", "mark": BOX}, LIB)
ms = S.marks_of(LIB, LES)
check("★ 账本里的框能读回来（页面打开时据此把框画回去）",
      any(m.get("p") == 27 and m.get("r") == [12.0, 34.0, 56.0, 78.0] for m in ms),
      json.dumps(ms, ensure_ascii=False)[:200])
check("  带上它属于哪一节（跨节汇总时才知道是谁的）",
      all(m.get("lesson") == LES for m in ms))
V.record("/api/mark", "mark", {"lesson": LES, "at": "t10", "mark": dict(BOX)}, LIB)
check("  同一处框重复写只算一个（按 页+矩形 去重）",
      len([m for m in S.marks_of(LIB, LES)
           if m.get("r") == [12.0, 34.0, 56.0, 78.0]]) == 1)
# 实测有这种情况：同一处框记过两次，一次没问、一次问了 ——
# 按矩形去重时"没问"那条会先到，**问题必须保住**，否则框画回来是哑的
DUMB = {"p": 30, "r": [1.0, 1.0, 2.0, 2.0]}
V.record("/api/mark", "mark", {"lesson": LES, "at": "t10b", "mark": dict(DUMB)}, LIB)
V.record("/api/mark", "mark",
         {"lesson": LES, "at": "t10c",
          "mark": {"p": 30, "r": [1.0, 1.0, 2.0, 2.0], "q": "这个框里的图是什么意思"}},
         LIB)
merged = [m for m in S.marks_of(LIB, LES) if m.get("p") == 30]
check("★ 同一处框的两条记录合并，**问题不丢**（画回来才不是哑框）",
      len(merged) == 1 and merged[0].get("q") == "这个框里的图是什么意思",
      json.dumps(merged, ensure_ascii=False))
V.record("/api/mark", "mark",
         {"lesson": "课A:讲:节:x", "at": "t11",
          "mark": {"p": 3, "r": [1.0, 2.0, 3.0, 4.0], "q": "库根那门课的框"}},
         os.path.join(ROOT2, "课A"))
check("  传库根也能读（与 ladder_of 共用同一套根解析）",
      len(S.marks_of(ROOT2)) >= 1, str(S.marks_of(ROOT2))[:120])
check("  没有账本的目录 → 空表，不炸", S.marks_of(os.path.join(ROOT, "没这门课")) == [])

# ---------------------------------------------------------------- 4b5 对话能删
# 用户原话：「那上面这个『标记』功能完全没有任何意义了」之后紧接着的一句：
# 「框是临时的能删，对话是永久的反而不能删，很奇怪。」—— 那就补上。
# 两条铁律：① 删之前**先备份**（账本是唯一真相源，删错了没法重建）；
#          ② 删的是**指定的那一段/那一轮**，别人一条都不能少。
DEL_LES = LES
for i in range(1, 4):
    V.study.append_ladder(LIB, DEL_LES, 60, f"第{i}问", f"第{i}答", tid="m60-1_2_3_4")
V.study.append_ladder(LIB, DEL_LES, 60, "别人的一段", "别人的回答", tid="m60-9_9_9_9")
check("  准备：那一段有 3 轮、另一段 1 轮",
      len([x for x in S.ladder_of(LIB, DEL_LES) if x.get("tid") == "m60-1_2_3_4"]) == 3
      and len([x for x in S.ladder_of(LIB, DEL_LES) if x.get("tid") == "m60-9_9_9_9"]) == 1)

n = S.delete_turn(LIB, DEL_LES, "m60-1_2_3_4", 2, page=60)
left = [x for x in S.ladder_of(LIB, DEL_LES) if x.get("tid") == "m60-1_2_3_4"]
check("★ 删一轮：只少那一轮，剩下的顺序不乱",
      n == 1 and [x["q"] for x in left] == ["第1问", "第3问"],
      str([x["q"] for x in left]))
check("  删某一轮**不动别的对话**",
      len([x for x in S.ladder_of(LIB, DEL_LES) if x.get("tid") == "m60-9_9_9_9"]) == 1)
check("★ 删之前留了备份（账本删错了没法重建）",
      glob.glob(os.path.join(LIB, ".ledger", "backups", "study.json.*")),
      "没找到备份")
check("  备份放在 .ledger/backups/ 里（放在 .ledger/ 下会被 git 收进去）",
      not glob.glob(os.path.join(LIB, ".ledger", "*.bak-*")))

n = S.delete_thread(LIB, DEL_LES, "m60-1_2_3_4", page=60)
check("★ 删整段：那一段全没了、别的段还在",
      n == 2 and not [x for x in S.ladder_of(LIB, DEL_LES)
                      if x.get("tid") == "m60-1_2_3_4"]
      and len([x for x in S.ladder_of(LIB, DEL_LES)
               if x.get("tid") == "m60-9_9_9_9"]) == 1, str(n))
check("  删不存在的段 → 0，不炸也不乱删",
      S.delete_thread(LIB, DEL_LES, "根本没这段", page=60) == 0
      and S.delete_turn(LIB, DEL_LES, "m60-9_9_9_9", 99, page=60) == 0)

# 老账本（功能上线前）的来回没有 tid，页面给的是 `@p<页>` 合成编号 —— 也得能删
V.study.append_ladder(LIB, DEL_LES, 60, "老账本那一问", "老账本那一答")   # 不带 tid
check("  准备：有一条老记录（没 tid）躺在第 60 页", 
      len([x for x in S.ladder_of(LIB, DEL_LES)
           if int(x.get("p") or 0) == 60 and not x.get("tid")]) == 1)
check("★ 老账本那种「没 tid」的段，按页也能删（不然永远删不掉）",
      S.delete_thread(LIB, DEL_LES, "@p60", page=60) == 1
      and not [x for x in S.ladder_of(LIB, DEL_LES)
               if int(x.get("p") or 0) == 60 and not x.get("tid")]
      and len([x for x in S.ladder_of(LIB, DEL_LES)
               if x.get("tid") == "m60-9_9_9_9"]) == 1)

# ---------------------------------------------------------------- 4e 平板能用
# 用户要在平板上学习（数理还要手写）—— 默认只绑 127.0.0.1，平板**根本连不上**；
# 而且他不知道该输哪个网址。所以：绑 0.0.0.0 时要**把局域网地址打出来**。
_src = open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                         "src", "serve.py"), encoding="utf-8").read()
check("★ 服务会算出局域网地址（平板要输的就是它）", "def lan_ip(" in _src)
check("★ 绑 0.0.0.0 时把「平板/手机请开：http://…」打出来",
      "平板/手机请开" in _src and "lan_ip()" in _src)
check("  默认仍只绑本机，并提示怎么开给平板",
      'host: str = "127.0.0.1"' in _src and "--host 0.0.0.0" in _src)
_ip = V.lan_ip()
check("  lan_ip() 返回一个像样的地址（或空串，但绝不炸）",
      _ip == "" or (_ip.count(".") == 3), repr(_ip))

# 两个启动器都要在，且都是 GBK（cmd 按系统码页读它）
_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _f, _need in (("启动学习库.bat", "run.py serve"),
                  ("平板访问.bat", "--host 0.0.0.0")):
    _p = os.path.join(_root, _f)
    check(f"★ {_f} 在（用户只会双击，不会敲命令）", os.path.isfile(_p))
    if os.path.isfile(_p):
        _raw = open(_p, "rb").read()
        check(f"  {_f} 能被 GBK 解开、且关键行在",
              bool(_try_gbk(_raw)) and _need in _try_gbk(_raw))
        check(f"  {_f} 没有 UTF-8 BOM", _raw[:3] != b"\xef\xbb\xbf", str(list(_raw[:3])))
        check(f"  {_f} 是 CRLF 行尾（cmd 对 LF 的 .bat 有解析毛病）",
              _raw.count(b"\r\n") > 5 and _raw.count(b"\n") == _raw.count(b"\r\n"),
              f"CRLF={_raw.count(chr(13).encode()+chr(10).encode())} "
              f"LF={_raw.count(chr(10).encode())}")
# 光工作区对没用：`.gitattributes` 里那条 `* text=auto eol=lf` 会让**新克隆**的仓库
# 把 .bat 签出成 LF（"这台机器好好的，换台机器双击就废"）。必须有例外规则。
_ga = os.path.join(_root, ".gitattributes")
check("★ .gitattributes 给 .bat 留了 CRLF 例外（否则新克隆出来的 .bat 是 LF）",
      os.path.isfile(_ga) and any("*.bat" in ln and "crlf" in ln.lower()
                                  for ln in open(_ga, encoding="utf-8")))

# ---------------------------------------------------------------- 4c 知识点接口（侧栏用）

KCSDIR = os.path.join(ROOT, "库K")
os.makedirs(os.path.join(KCSDIR, "生物", ".ledger"), exist_ok=True)
json.dump({"version": 1, "chapters": [{"id": "L1", "label": "第一章", "kcs": [
    {"id": "L1.2", "label": "第二个", "page": 9, "type": "concept",
     "importance": "must", "is_hub": True, "points": ["a", "b", "c", "d"],
     "questions": [{"q": "你问过的问题"}]},
    {"id": "L1.1", "label": "第一个", "page": 3, "type": "principle",
     "importance": "key", "points": [], "questions": []},
]}]}, open(os.path.join(KCSDIR, "生物", ".ledger", "kcs.json"), "w",
          encoding="utf-8"), ensure_ascii=False)
_ks = V.kcs_flat(KCSDIR, "生物")
check("★ /api/kcs：摊平所有知识点", len(_ks) == 2, str(len(_ks)))
check("★ 按页码排序（侧栏里顺序才对）", [k["page"] for k in _ks] == [3, 9],
      str([k["page"] for k in _ks]))
check("  带上了页码/重要度/枢纽标记", _ks[1]["hub"] is True
      and _ks[1]["importance"] == "must")
check("  要点截到前 3 条（侧栏放不下）", len(_ks[1]["points"]) == 3)
check("  带上他在这一页问过的问题", _ks[1]["questions"] == ["你问过的问题"])
check("没有骨架的课返回空表、不炸", V.kcs_flat(KCSDIR, "没有这门课") == [])

# ---------------------------------------------------------------- 4d 框里读出来

# 用户原话：「问的确实很好，**但你的问题和我的划线没关系啊**」。
# 根因：他画的框在程序眼里只是**图片上的四个百分比数字** —— "框里是什么"程序不知道，
# 所以只能拿整页文字去问。修法：把框那一小块裁出来交给视觉模型（抄隔壁 deepreader）。
#
# ★ 这里踩过一个只有实跑才抓得到的坑：`from src.vision import VisionClient` 之前
#   **必须先调一次 engine.settings()** —— 是它把真源目录加进 sys.path 的。
#   顺序反了会 ModuleNotFoundError，整条"读框"静默失败，AI 又只能拿整页瞎问。
_src = __import__("inspect").getsource(V.read_region)
check("★ read_region：先取 settings（顺带把真源加进 sys.path）再 import 视觉类",
      _src.index("engine.settings()") < _src.index("from src.vision import"),
      "顺序反了会 ModuleNotFoundError")
check("★ read_region：没有 rect 时明确拒绝、不炸", not V.read_region(ROOT, "x", 1, None, "", "").get("ok"))
check("★ read_region：rect 长度不对也拒绝", not V.read_region(ROOT, "x", 1, [1, 2], "", "").get("ok"))
check("★ read_region：找不到页图时给出可读的原因",
      "页图" in (V.read_region(ROOT, "没有这门课", 1, [0, 0, 50, 50], "", "").get("msg") or ""),
      str(V.read_region(ROOT, "没有这门课", 1, [0, 0, 50, 50], "", "")))
check("★ read_region：框太小会拒绝（避免拿一条缝去问模型）",
      "太小" in (V.read_region(KCSDIR, "生物", 1, [0, 0, 0.1, 0.1], "", "").get("msg") or "")
      or not V.read_region(KCSDIR, "生物", 1, [0, 0, 0.1, 0.1], "", "").get("ok"))
check("★ page_image：没有账本时返回空串、不炸", V.page_image(ROOT, "没有这门课", 1) == "")
check("★ 追问 prompt 里框选内容是**主角**，整页原文降为背景",
      "以它为主" in V.build_ask_prompt("整页文字", "框里读出来的", "问", [], 1)
      and "仅供背景" in V.build_ask_prompt("整页文字", "框里读出来的", "问", [], 1))
check("  没框选时 prompt 也说得清楚",
      "没框选" in V.build_ask_prompt("整页文字", "", "问", [], 1))

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
