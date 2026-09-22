# -*- coding: utf-8 -*-
"""Anki 卡片测试（离线可跑，不要求 Anki 正在运行）。

每条断言对应一个真实约束：
  1. 卡片身份必须**只由源数据决定** —— 否则重跑会重复制卡
  2. 重跑不能重复推送（靠账本里的 anki_note_id）
  3. 正面必须有语境（原文），否则「这个公式怎么推的」这类问题无法作答
  4. 背面必须有出处（哪一讲第几页）
  5. 字段名映射要认中英两种（中文版 Anki 是「正面/背面」，没有 Basic 这个类型名）
  6. TSV 导出格式要能让 Anki 直接导入

跑法：python tests/test_cards.py
"""
from __future__ import annotations

import os
import re
import subprocess
import sys

# Windows 控制台默认 GBK：print 中文/emoji 会抛 UnicodeEncodeError 并让退出码变 1
# （明明全过却报失败）。强制 UTF-8 输出。
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

HERE = os.path.dirname(os.path.abspath(__file__))
PROJ = os.path.dirname(HERE)
COURSE = "物理"
LIB = os.path.abspath(os.path.join(PROJ, "..", "学习库", COURSE))

sys.path.insert(0, os.path.join(PROJ, "vendor"))
sys.path.insert(0, os.path.join(PROJ, "src"))
import archive  # noqa: E402
import cards as C  # noqa: E402

FAILS: list[str] = []
PASSES: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    (PASSES if ok else FAILS).append(f"{name}{(' — ' + detail) if detail else ''}")


def run(*args: str) -> str:
    r = subprocess.run([sys.executable, os.path.join(PROJ, "run.py"), *args],
                       capture_output=True, text=True, encoding="utf-8", cwd=PROJ)
    return (r.stdout or "") + (r.stderr or "")


# ---------------------------------------------------------------- 1 身份

check("卡片身份只由源数据决定（同输入同 id）",
      C.card_id("abcdef1234567890", 10, 1) == C.card_id("abcdef1234567890", 10, 1))
check("不同页 → 不同 id", C.card_id("a" * 20, 10, 1) != C.card_id("a" * 20, 11, 1))
check("不同序号 → 不同 id", C.card_id("a" * 20, 10, 1) != C.card_id("a" * 20, 10, 2))
check("追问另有 id", C.card_id("a" * 20, 10, 1, 1) != C.card_id("a" * 20, 10, 1))
check("id 里带页号，便于人眼核对", ":p010:" in C.card_id("a" * 20, 10, 1))

# ---------------------------------------------------------------- 2 内容

front = C.render_front("接触力③：张力", "怎么定义收缩的方向？")
check("正面含原文（语境）", "接触力③" in front)
check("正面含问题", "怎么定义收缩的方向" in front)

back = C.render_back("这是解答", "E=mc^2", {"lecture": "第五讲", "page": 10, "bbox": "[0,0,1,1]"})
check("背面含解答", "这是解答" in back)
check("背面公式用 Anki 的 \\[...\\] 语法", "\\[E=mc^2\\]" in back)
check("背面带出处：讲次", "第五讲" in back)
check("背面带出处：页号", "第 10 页" in back)
check("背面带出处：框选区域", "框选" in back)

check("HTML 特殊字符被转义（防注入/破版）",
      "&lt;script&gt;" in C.render_front("<script>alert(1)</script>", "q"))

# ★ 空问题必须**报错**，不能再悄悄伪造一个通用问题（这是垃圾卡的根因）
try:
    C.render_front("某段原文", "")
    check("空问题会让 render_front 报错（防垃圾卡复发）", False, "居然没报错")
except ValueError:
    check("空问题会让 render_front 报错（防垃圾卡复发）", True)

# 转录是画面描述时，正面应改用公式当语境
f_desc = C.render_front("", "这个公式怎么来的", "E=mc^2")
check("转录不可用时用公式当语境", "E=mc^2" in f_desc and "❓" in f_desc)

# ---------------------------------------------------------------- 3 从真数据构建

arch = archive.load_archive(LIB)
if not arch.get("by_sha1"):
    print(f"跳过：{LIB} 账本里没有批注，先跑 archive")
    raise SystemExit(0)

# ⚠️ 本测试会临时改 accounts 账本，先把原始字节备份好，最后**原样还原**。
#    （踩过：早先版本直接改真实账本里第一条卡的 anki_note_id 再删掉，
#      而那条恰好有真实的 Anki note id —— 把用户在 Anki 里的卡片搞丢了。）
CARDS_PATH = C.cards_ledger_path(LIB)
_orig_bytes = open(CARDS_PATH, "rb").read() if os.path.exists(CARDS_PATH) else None


def restore_ledger() -> None:
    if _orig_bytes is not None:
        with open(CARDS_PATH, "wb") as f:
            f.write(_orig_bytes)


built: list[dict] = []
skipped: list[dict] = []
for sha, rec in arch["by_sha1"].items():
    if not rec.get("lecture_id"):
        continue
    anns = []
    for a in rec["annotations"]:
        b = dict(a)
        b["_sha1"] = sha
        anns.append(b)
    made, sk = C.build_cards(COURSE, rec["lecture_id"], rec.get("source_file", ""),
                             anns, "slug", None)
    built += made
    skipped += sk

check("从真实批注构建出卡片", len(built) >= 7, f"{len(built)} 张")
check("每张卡都有稳定 id", all(c.get("id") for c in built))
check("每张卡都有正反面", all(c["fields"]["Front"] and c["fields"]["Back"] for c in built))
check("每张卡带出处（讲次+页号）",
      all(c["source"].get("lecture") and c["source"].get("page") for c in built))
check("追问单独成卡（第 12 页那条）",
      any(":t1" in c["id"] for c in built),
      str([c["id"] for c in built if ":t" in c["id"]]))

ids = [c["id"] for c in built]
check("卡片 id 无重复", len(ids) == len(set(ids)), f"{len(ids)} vs {len(set(ids))}")

# ★ 质量闸门：没有提问的批注**不能**制卡
#   只统计「匹配到讲次」的批注 —— 没匹配上的那组（自测用的 photoredox）
#   本来就不参与制卡，算进来会把预期数字带偏（踩过）。
n_noq = sum(1 for rec in arch["by_sha1"].values() if rec.get("lecture_id")
            for a in rec["annotations"] if not (a.get("question") or "").strip())
check("无提问的批注被跳过（实测：这类卡无法作答，被用户当场指出）",
      len(skipped) == n_noq, f"跳过 {len(skipped)} 条，匹配到讲次的批注里无提问 {n_noq} 条")
check("被跳过的批注**没有**混进卡片 id",
      not any(":p034:" in c["id"] or ":p055:" in c["id"] for c in built)
      or n_noq == 0,
      str([c["id"] for c in built if ":p034:" in c["id"] or ":p055:" in c["id"]]))

# ★ 正面绝不能出现伪造的通用问题
check("正面不出现伪造的通用问题",
      not any("这一块讲的是什么" in c["fields"]["Front"] for c in built))
check("每张卡正面都有真问题（❓ 后面非空）",
      all(re.search(r"❓\s*\S", c["fields"]["Front"]) for c in built))

# ★ 「转录是画面描述」时不当语境（避免把模型对模糊图的描述塞进题面）
desc_leak = [c["id"] for c in built
             if C.is_descriptive(c["fields"]["Front"])]
check("画面描述没有混进任何一张卡的正面", not desc_leak, str(desc_leak))

# ---------------------------------------------------------------- 3b AI 出题
import qa  # noqa: E402

# 出题质量闸门（每条都是照着"什么样的问题算垃圾"定的）
gate_cases = [
    ("", False, "空"),
    ("这一块讲的是什么？", False, "空泛"),
    ("这段说明了什么？", False, "空泛"),
    ("好", False, "太短"),
    ("这个公式是怎么推出来的", False, "没有问号"),
    ("第一行？\n第二行", False, "多行"),
    ("A类不确定度为什么要除以根号n？", True, "合格"),
]
for q, want_ok, why in gate_cases:
    ok, reason = qa.check_question(q)
    check(f"出题闸门：{why} → {'通过' if want_ok else '拦下'}",
          ok == want_ok, f"{q[:20]!r} → {ok} {reason}")

# 用假 provider 验证「无提问 → AI 出题」这条路径（离线，不花钱）
def fake_provider(_ann):
    return {"question": "假问题：A类不确定度为什么要除以根号n？",
            "model": "fake", "tokens": 1}


ai_cards, _ai_skipped = C.build_cards(
    COURSE, "基础物理实验数据课2026秋季(1)", "x.pdf",
    [{"page": 34, "no": 1, "question": "", "explanation": "x" * 50,
      "transcript": "画面上部是…残段…", "_sha1": "0e58b702feed" * 5}],
    "slug", None, question_provider=fake_provider)
check("无提问的批注：给了 provider 就出题", len(ai_cards) == 1, f"{len(ai_cards)} 张")
if ai_cards:
    _ac = ai_cards[0]
    check("AI 卡打了 AI出题 标签", "AI出题" in _ac["tags"], str(_ac["tags"]))
    check("AI 卡的 source.kind = ai", _ac["source"].get("kind") == "ai")
    check("AI 卡正面**只有题目**（不给语境，避免泄题）",
          "画面上部" not in _ac["fields"]["Front"]
          and "假问题" in _ac["fields"]["Front"])
    check("AI 卡背面仍有出处", "第 34 页" in _ac["fields"]["Back"])

# 没给 provider 时，无提问的批注仍然被跳过（默认行为不变）
_, skip_no_provider = C.build_cards(
    COURSE, "基础物理实验数据课2026秋季(1)", "x.pdf",
    [{"page": 34, "no": 1, "question": "", "explanation": "x" * 50, "_sha1": "a" * 40}],
    "slug", None)
check("没给 provider 时无提问的批注被跳过", len(skip_no_provider) == 1,
      str(skip_no_provider))

# provider 出题失败 → 不能硬塞，必须跳过并留下原因
_, skip_bad = C.build_cards(
    COURSE, "基础物理实验数据课2026秋季(1)", "x.pdf",
    [{"page": 34, "no": 1, "question": "", "explanation": "x" * 50, "_sha1": "a" * 40}],
    "slug", None, question_provider=lambda _a: {"question": "", "reason": "太短"})
check("provider 出题失败时跳过且留下原因",
      len(skip_bad) == 1 and "太短" in skip_bad[0]["reason"], str(skip_bad))

# 引擎可用性（只探测，不发请求）
_eng_ok, _eng_why = qa.engine_available()
check("deepreader 引擎可被 course-pipeline 复用（R13）", _eng_ok, _eng_why)

# ---------------------------------------------------------------- 4 落账本 + 幂等

# 注意用 --no-ai：测试**不联网、不花钱**。
# 也不能加 --prune —— 那会把账本里的 AI 卡删掉（它们这次没被构建）。
run("--course", COURSE, "cards", "--no-ai")
data1 = C.load_cards(LIB)
check("卡片已落账本 .ledger/cards.json", len(data1["by_id"]) >= 7, f"{len(data1['by_id'])} 张")
check("账本是落盘的 JSON", os.path.exists(C.cards_ledger_path(LIB)))

# 模拟"已经推过 Anki"：改第一条卡再还原。
# 账本已整体备份（见上），且跑完必定还原 —— 绝不能把用户在 Anki 里的 note id 搞丢。
victim = sorted(data1["by_id"])[0]
_real = data1["by_id"][victim].get("anki_note_id")
data1["by_id"][victim]["anki_note_id"] = 999999999
C.save_cards(LIB, data1)

run("--course", COURSE, "cards", "--no-ai")
data2 = C.load_cards(LIB)
check("重跑后已推送标记 **不被清掉**（否则会重复制卡）",
      data2["by_id"][victim].get("anki_note_id") == 999999999,
      str(data2["by_id"][victim].get("anki_note_id")))
check("重跑后卡片总数不变", len(data2["by_id"]) == len(data1["by_id"]),
      f"{len(data2['by_id'])} vs {len(data1['by_id'])}")

# 无论走哪条路，最终都还原成原始账本（含真实 anki_note_id）
restore_ledger()
data2 = C.load_cards(LIB)
check("测试结束后账本已还原成原样（真实 anki note id 没被动）",
      (_real is None and data2["by_id"][victim].get("anki_note_id") is None)
      or data2["by_id"][victim].get("anki_note_id") == _real,
      f"{data2['by_id'][victim].get('anki_note_id')} vs {_real}")

# ---------------------------------------------------------------- 5 导出

tsv = os.path.join(LIB, "cards", "anki_import.tsv")
check("导出了 Anki 可导入的 TSV", os.path.exists(tsv))
with open(tsv, encoding="utf-8") as f:
    rows = [l.rstrip("\n") for l in f if l.strip()]
header = [l for l in rows if l.startswith("#")]
data_rows = [l for l in rows if not l.startswith("#")]
check("TSV 首行是 Anki 认可的分隔符声明",
      rows[0] == "#separator:tab", rows[0] if rows else "(空)")
check("TSV 数据行数与卡片数一致", len(data_rows) == len(data2["by_id"]),
      f"{len(data_rows)} vs {len(data2['by_id'])}")
check("TSV 表头声明了笔记类型", any(l.startswith("#notetype:") for l in header),
      str(header))
# 表头里的笔记类型必须与 Anki 里真实存在的类型一致（中文版没有 Basic）
nt = next((l.split(":", 1)[1] for l in header if l.startswith("#notetype:")), "")
ac0 = C.AnkiConnect()
if ac0.available():
    real = ac0.model_names()
    check("TSV 声明的笔记类型在 Anki 里真实存在", nt in real, f"{nt} vs {real}")
else:
    PASSES.append("（跳过笔记类型核对：AnkiConnect 未运行）")

# ---------------------------------------------------------------- 6 字段映射（中英）

note_cn = C.to_anki_note({"deck": "D", "fields": {"Front": "F", "Back": "B"},
                          "tags": ["t"]}, "问答题", {"Front": "正面", "Back": "背面"})
check("中文版字段名映射正确", note_cn["fields"] == {"正面": "F", "背面": "B"},
      str(note_cn["fields"]))
note_en = C.to_anki_note({"deck": "D", "fields": {"Front": "F", "Back": "B"},
                          "tags": ["t"]}, "Basic", {"Front": "Front", "Back": "Back"})
check("英文版字段名映射正确", note_en["fields"] == {"Front": "F", "Back": "B"})
check("note 结构符合 AnkiConnect 要求",
      {"deckName", "modelName", "fields", "tags", "options"} <= set(note_cn))
check("配图会拼到背面", "<img" in C.to_anki_note(
    {"deck": "D", "fields": {"Front": "F", "Back": "B"}, "tags": []},
    "问答题", {"Front": "正面", "Back": "背面"}, "x.jpg")["fields"]["背面"])

# ---------------------------------------------------------------- 7 AnkiConnect（可选）

ac = C.AnkiConnect()
if ac.available():
    model, fmap = ac.pick_basic_model()
    check("能挑到有正反两面的笔记类型（中文版没有 Basic 这个名字）",
          bool(model) and set(fmap) == {"Front", "Back"}, f"{model} {fmap}")
    ids_in_anki = ac.invoke("findNotes", query=f'"deck:课程::{COURSE}"')
    check("Anki 里确有本课程的卡片", len(ids_in_anki) >= 7, f"{len(ids_in_anki)} 张")
else:
    PASSES.append("（跳过 Anki 实时检查：AnkiConnect 未运行）")

# ---------------------------------------------------------------- 汇总

for p in PASSES:
    print("PASS  " + p)
for f_ in FAILS:
    print("FAIL  " + f_)
print("=" * 60)
print(f"通过 {len(PASSES)} / 失败 {len(FAILS)}")
raise SystemExit(1 if FAILS else 0)
