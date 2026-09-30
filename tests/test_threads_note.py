# -*- coding: utf-8 -*-
"""对话出口（我和 AI 的对话 → Obsidian）的测试。

用户原话：「我希望制作成**子对话**的形式……**可以之后再调出来读**」。
评审结论：在这之前对话**一个出口都没有** —— 账本 JSON 里有、网页上有，
`notes/` 里一个字都没有，Anki 也拿不到。

这个文件盯三件事：
1. 一段对话渲染出来的 markdown 是不是**人读得懂**的（题、答、轮数、出处）；
2. 挂的**位置**对不对（挂在你当时看的那一页下面，不是随便堆在文件末尾）；
3. 重跑**幂等**、而且**不碰你写的东西**（生成块之外的字一个字都不动）。

跑法：python tests/test_threads_note.py
"""
from __future__ import annotations

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
sys.path.insert(0, HERE)
import render as R  # noqa: E402

FAILS: list[str] = []
PASSES: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    (PASSES if ok else FAILS).append(f"{name}{(' — ' + detail) if detail else ''}")


TH = {
    "tid": "m27-11_22_33_44", "lesson": "生物:第3章:第3章:survey", "page": 27,
    "title": "脂筏为什么能当信号转导平台？", "n": 3, "gave_answer": True,
    "at": "2026-09-29 22:23", "at_end": "2026-09-29 23:14",
    "turns": [
        {"q": "脂筏为什么能当信号转导平台？", "a": "你框那段里，脂筏富含的是哪两类成分？",
         "at": "2026-09-29 22:23", "stalls": 0, "gave_answer": False},
        {"q": "不知道", "a": "那我们缩小一点，别管脂筏了。", "at": "2026-09-29 22:23",
         "stalls": 1, "gave_answer": False},
        {"q": "别问了直接讲", "a": "脂筏富含胆固醇和鞘脂。\n第二行：它们排得更紧。",
         "at": "2026-09-29 23:14", "stalls": 1, "gave_answer": True},
    ],
}

# ---------------------------------------------------------------- 1 一块对话

md = R.render_thread_md(TH, 1)
check("★ 一段对话渲染成一块 markdown（带标题层级，能进笔记目录）",
      md.startswith("#### 🗣️ 我和 AI 的对话 #1"), md.splitlines()[0])
check("★ 有你的问题、也有 AI 的回答（两边原文都在）",
      "脂筏为什么能当信号转导平台？" in md and "你框那段里" in md)
check("★ 每一轮的「我问」都留着（不是只留最后一句）",
      md.count("**🙋 我问**") == 3, str(md.count("**🙋 我问**")))
check("★ 元信息：轮数 / 时间跨度 / 对话编号 / 给过完整讲解",
      "3 轮" in md and "22:23 → 23:14" in md and "m27-11_22_33_44" in md
      and "完整讲解" in md)
check("★ 开头那一问单独标出来（扫一眼就知道这段在聊什么）",
      "开头那一问：**脂筏为什么能当信号转导平台？**" in md)
# 多行回答必须每一行都在引用块里 —— 否则第二行会漏到正文里，
# 排版会散掉（AI 给完整讲解时经常是多段）
check("★ AI 的多行回答逐行加引用号（不然第二行漏出块外，排版散掉）",
      "> 🤖 **AI**：脂筏富含胆固醇和鞘脂。" in md
      and "> 第二行：它们排得更紧。" in md, md.split("别问了")[-1][:120])
check("★ 换行/引用号这些不炸（空行也要带 >）",
      R._md_quote("甲\n\n乙") == "> 甲\n>\n> 乙", repr(R._md_quote("甲\n\n乙")))

# ---------------------------------------------------------------- 2 挂到哪一页

by_page = R.render_threads_by_page([
    TH, dict(TH, tid="m40-x", page=40, at="2026-09-30 10:00"),
    dict(TH, tid="坏页", page="xx"),
])
check("★ 按页号分组（找得回「我当时看的那一页」）",
      sorted(by_page) == [27, 40], str(sorted(by_page)))
check("  页号不是数字的脏数据被丢掉，不炸", "坏页" not in str(by_page))

lec = {"id": "第3章", "file": "x.pptx", "page_count": 3, "slug": "s",
       "pages": [{"no": 26, "text": "第26页原文"}, {"no": 27, "text": "第27页原文"},
                 {"no": 28, "text": "第28页原文"}]}
body = R.render_lecture_body("生物", lec, include_images=False,
                             threads_by_page=by_page)
check("★★ 对话挂在**它自己那一页**下面（不是堆在文件末尾）",
      body.index("### 第 27 页") < body.index("我和 AI 的对话")
      < body.index("### 第 28 页"), "位置错了")
check("  没对话的那几页不多长东西", "### 第 26 页" in body
      and body.count("我和 AI 的对话") == 1)
check("★ 页首摘要报出「含 N 段你和 AI 的对话」（并列出在第几页）",
      "含 **2** 段你和 AI 的对话（第 27、40 页）" in body,
      str([ln for ln in body.splitlines() if "段你和 AI" in ln]))
check("  并指向总表（Obsidian 双链）", "[[_我和AI的对话]]" in body)

# ---------------------------------------------------------------- 3 总表

pg = R.render_threads_page("生物", {"第3章": [TH]}, {"第3章": "第3章"})
check("★ 总表报总数（几段、几轮）", "共 **1** 段对话、**3** 轮来回" in pg)
check("★ 总表按讲次 → 页分组", "## 📂 第3章" in pg and "### 第 27 页" in pg)
check("★ 每一页给一条**双链**回讲次笔记的那一页（Obsidian 里点得回去）",
      "[[第3章#第 27 页]]" in pg, pg[:200])
check("  没有对话时给一句人话，而不是空白页",
      "还没有对话" in R.render_threads_page("生物", {}, {}))

# ---------------------------------------------------------------- 4 写盘：幂等 + 不碰你的字

tmp = os.path.join(PROJ, ".tmp-threads")
shutil.rmtree(tmp, ignore_errors=True)
os.makedirs(tmp, exist_ok=True)
page_path = os.path.join(tmp, "_我和AI的对话.md")

first = R.write_note(page_path, "生物 · 我和 AI 的对话", pg, tail=R.TH_HANDWRITTEN)
check("★ 第一次写：建出文件 + 生成块 + 我的地盘",
      R.GEN_BEGIN in first and "## 我的补充" in first)
check("  已有的对话在里面", "脂筏为什么能当信号转导平台？" in first)

# 在「我的补充」里写字，重跑后必须还在
written = first.replace("## 我的补充\n", "## 我的补充\n\n我当时以为脂筏是个细胞器。\n")
with open(page_path, "w", encoding="utf-8") as f:
    f.write(written)
again = R.write_note(page_path, "生物 · 我和 AI 的对话", pg, tail=R.TH_HANDWRITTEN)
check("★ 重跑不碰你写的字（生成块之外一个字不动）",
      "我当时以为脂筏是个细胞器。" in again)
check("★ 重跑幂等（同样的输入 → 同样的文件）", again == written, "内容变了")

# 对话变多了 → 生成块更新，你的字仍在
pg2 = R.render_threads_page("生物", {"第3章": [TH, dict(TH, tid="m40-x", page=40)]},
                            {"第3章": "第3章.md"})
more = R.write_note(page_path, "生物 · 我和 AI 的对话", pg2, tail=R.TH_HANDWRITTEN)
check("★ 新增对话后重跑：生成块更新 + 你的字还在",
      "共 **2** 段对话" in more and "我当时以为脂筏是个细胞器。" in more)

# 没有 gen 标记的文件绝不改写（那是用户自己的文件）
bad = os.path.join(tmp, "手写的.md")
with open(bad, "w", encoding="utf-8") as f:
    f.write("# 我自己写的\n\n别动我。\n")
try:
    R.write_note(bad, "x", "y")
    check("★ 没有 gen 标记的文件拒绝改写（绝不覆盖你自己的文件）", False, "竟然写了")
except ValueError:
    check("★ 没有 gen 标记的文件拒绝改写（绝不覆盖你自己的文件）", True)
check("  那个文件确实没被动", "别动我。" in open(bad, encoding="utf-8").read())

shutil.rmtree(tmp, ignore_errors=True)

# ---------------------------------------------------------------- 汇总

for p in PASSES:
    print("PASS  " + p)
for f_ in FAILS:
    print("FAIL  " + f_)
print("=" * 60)
print(f"通过 {len(PASSES)} / 失败 {len(FAILS)}")
raise SystemExit(1 if FAILS else 0)
