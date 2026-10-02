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

import json
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
sys.path.insert(0, HERE)
import _pick  # noqa: E402

COURSE = "物理"
LIB = os.path.join(_pick.library_root(PROJ), COURSE)

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

# ---------------------------------------------------------------- 2b 转写清洗
# ★ 用户实测发现：transcript 里散文与「半成品公式记号」混在一起
#   （deepreader 的 prompt 要求「向量就写『向量P』」，那是为它自己网页显示做的妥协）。
#   直接印在卡面很难看；而同一个批注的 latex 字段是干净的 —— 所以拆开、公式走 latex。
REAL_VEC_TR = (
    "当物体在转动参考系中还有相对运动时，除[惯性离心力外还会]出现科里奥利力。\n"
    "向量F科 = -2m 向量ω × 向量v′ = 2m 向量v′ × 向量ω\n"
    "F科 = 2m ω v′ sinθ"
)
REAL_VEC_LATEX = ("\\vec{F}_{\\text{科}}=-2m\\,\\vec{\\omega}\\times\\vec{v}'"
                  "=2m\\,\\vec{v}'\\times\\vec{\\omega}\n\n"
                  "F_{\\text{科}}=2m\\omega v'\\sin\\theta")

check("散文行不被当成公式",
      not C.looks_like_formula("当物体在转动参考系中还有相对运动时，除[惯性离心力外还会]出现科里奥利力。"))
check("「向量X」记号被识别为公式行",
      C.looks_like_formula("向量F科 = -2m 向量ω × 向量v′ = 2m 向量v′ × 向量ω"))
check("纯符号公式行被识别", C.looks_like_formula("F科 = 2m ω v′ sinθ"))
check("普通中文句子不被误判", not C.looks_like_formula("力是物体之间的相互作用。"))

# ★ 组合箭头（U+20D7）是「向量箭头」，deepreader 的 prompt 要求别用但仍会漏出来。
#   不认它，整行就会被误当散文 —— 实测漏网：卡面同时出现「即 P⃗ = m g⃗」和它的
#   LaTeX，重复又难看。
check("组合箭头向量式被识别（实测漏网的那条）",
      C.looks_like_formula("即 P\u20d7 = m g\u20d7"))
check("带中文标注的 LaTeX 行被识别",
      C.looks_like_formula("(x_i,y_i) y_i-\\hat y_i（紫色箭头向右）"))
check("纯标题行不被误判", not C.looks_like_formula("接触力③：张力"))
check("无符号的中文散文不被误判",
      not C.looks_like_formula("车中观察者 看到 B 静止不动 却观察到弹簧被拉伸 牛顿定律「失效」"))
check("带括号的散文不被误判（括号不算数学符号）",
      not C.looks_like_formula("当物体还有相对运动时，除[惯性离心力外还会]出现科里奥利力。"))

# 组合箭头那一条：正面的公式必须走 latex，不能再出现裸的 P⃗
front_arrow = C.render_front("即 P\u20d7 = m g\u20d7", "这里的P是什么", "\\vec{P}=m\\vec{g}")
check("★ 组合箭头残式不再上卡面", "\u20d7" not in front_arrow)
check("★ 改用规范 LaTeX", "\\vec{P}=m\\vec{g}" in front_arrow)

prose, formulas = C.split_transcript(REAL_VEC_TR)
check("拆出 1 行散文", prose.count("\n") == 0 and "科里奥利力" in prose, prose[:30])
check("拆出 2 行公式", len(formulas) == 2, f"{len(formulas)} 行")
check("散文里没有「向量F科」残渣", "向量F科" not in prose)

check("latex 按空行拆成多条", len(C.latex_blocks(REAL_VEC_LATEX)) == 2,
      str(C.latex_blocks(REAL_VEC_LATEX)))

front_vec = C.render_front(REAL_VEC_TR, "这个是怎么来的", REAL_VEC_LATEX)
check("★ 卡面不再出现「向量F科」半成品记号", "向量F科" not in front_vec)
check("★ 卡面改用规范 LaTeX", "\\vec{F}_{\\text{科}}" in front_vec)
check("  两条公式都在", front_vec.count("\\[") == 2, str(front_vec.count("\\[")))
check("  散文语境保留", "科里奥利力" in front_vec)
check("  问题还在", "❓ 这个是怎么来的" in front_vec)

# 没有 latex 时的退路：公式行原样给出，别让正面空着
front_nolatex = C.render_front("向量F = ma", "这式子哪来的", "")
check("没有 latex 时退化为原样给公式行（不空着）", "向量F = ma" in front_nolatex)

# 纯散文（没有公式）的老行为不变
front_plain = C.render_front("接触力③：张力", "怎么定义收缩的方向？", "")
check("没有公式的转写行为不变", "接触力③" in front_plain and "❓" in front_plain)

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

# ---------------------------------------------------------------- 4b 同步状态与更新
# ★ 只推新卡是不够的：卡片内容会随规则改进而变（例：正面从「向量F科 = …」
#   改成规范 LaTeX）。账本更新了、Anki 里却停在旧版 —— 用户看到过期内容。
#   所以要有 synced_fields 指纹来识别"过时"，并调 updateNoteFields。

check("内容指纹只取决于正反面",
      C.fields_fingerprint({"fields": {"Front": "a", "Back": "b"}, "deck": "X"})
      == C.fields_fingerprint({"fields": {"Front": "a", "Back": "b"}, "deck": "Y"}))
check("内容变了指纹就变",
      C.fields_fingerprint({"fields": {"Front": "a", "Back": "b"}})
      != C.fields_fingerprint({"fields": {"Front": "a", "Back": "c"}}))

# merge_cards 必须保留同步状态：否则每次重跑都误判"内容变了"→ 反复更新 Anki
probe_id = "deadbeef0000:p999:n1"
C.save_cards(LIB, {"version": 1, "by_id": {
    probe_id: {"id": probe_id, "deck": "D", "fields": {"Front": "F", "Back": "B"},
               "tags": [], "source": {}, "anki_note_id": 424242,
               "synced_fields": "oldfingerprint"},
}})
merged, _ = C.merge_cards(LIB, [{"id": probe_id, "deck": "D",
                                 "fields": {"Front": "F", "Back": "B"},
                                 "tags": [], "source": {}}])
check("merge_cards 保留 anki_note_id",
      merged["by_id"][probe_id].get("anki_note_id") == 424242,
      str(merged["by_id"][probe_id].get("anki_note_id")))
check("merge_cards 保留 synced_fields（否则会反复更新 Anki）",
      merged["by_id"][probe_id].get("synced_fields") == "oldfingerprint",
      str(merged["by_id"][probe_id].get("synced_fields")))
restore_ledger()

_d_now = C.load_cards(LIB)
_synced = [c for c in _d_now["by_id"].values() if c.get("anki_note_id")]
check("已同步的卡都记了内容指纹",
      all(c.get("synced_fields") for c in _synced),
      f"{sum(1 for c in _synced if not c.get('synced_fields'))} 张缺指纹")

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

# ---------------------------------------------------------------- 5b 记住笔记类型

# ★ 回归（真实缺口）：Anki 没开时导出的 TSV 一律声明 `#notetype:Basic`，
#   而中文版 Anki 根本没有 Basic 这个类型（叫「问答题」）——
#   用户拿这份 TSV 去导入就会踩坑。可是「上次同步成功用的是哪个类型」
#   账本完全知道，不该丢。所以同步时记下来，离线导出时用它。
check("账本没记过类型时 remembered_notetype 返回空串",
      C.remembered_notetype({}) == "" and C.remembered_notetype({"notetype": "  "}) == "")
check("记住了就能读出来",
      C.remembered_notetype({"notetype": "问答题"}) == "问答题")

_led = C.cards_ledger_path(LIB)
_saved_led = json.load(open(_led, encoding="utf-8"))
try:
    _mem = json.loads(json.dumps(_saved_led))
    _mem["notetype"] = "问答题"
    C.save_cards(LIB, _mem)
    check("类型能落进账本并读回",
          C.remembered_notetype(C.load_cards(LIB)) == "问答题")

    # 合并新卡不能把顶层记住的类型弄丢（merge 是「重建账本」最常走的路径）
    _merged, _ = C.merge_cards(LIB, list(_mem["by_id"].values()))
    check("合并新卡不会丢掉记住的笔记类型",
          C.remembered_notetype(_merged) == "问答题",
          str(C.remembered_notetype(_merged)))

    # 离线导出应当采用记住的类型，而不是退回 Basic
    _tsv2 = os.path.join(LIB, "cards", "_notetype_probe.tsv")
    _nt = C.remembered_notetype(C.load_cards(LIB)) or "Basic"
    C.export_tsv([], _tsv2, notetype=_nt, deck="课程::物理")
    with open(_tsv2, encoding="utf-8") as f:
        _hdr = f.read()
    check("离线导出采用账本记住的笔记类型（不是 Basic）",
          "#notetype:问答题" in _hdr, " | ".join(_hdr.splitlines()[:4]))
    os.remove(_tsv2)
finally:
    C.save_cards(LIB, _saved_led)   # 还原账本，别污染

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

# ---------------------------------------------------------------- 9 从「对话」出卡
#
# 用户要的「一键出卡」。闸门是**关键**：阶梯追问里大多数轮次 AI 只是在**反问你**
# （「你先说说膜的主要成分是什么？」）—— 把反问当答案做成卡，背面就是空的，
# 正是这个项目返工过一次的**垃圾卡**。

TH = {"tid": "m27-11_22_33_44", "page": 27, "n": 2,
      "title": "脂筏为什么能当信号转导平台？",
      "turns": [
          {"q": "脂筏为什么能当信号转导平台？", "a": "你框那段里，脂筏富含的是哪两类成分？",
           "gave_answer": False, "stall": False, "sel": ""},
          {"q": "别问了直接讲", "a": "脂筏富含胆固醇和鞘脂，它们在膜上排得更紧，"
           "把信号分子浓缩在一起，所以转导效率高。", "gave_answer": True,
           "stall": True, "sel": "【框里读出来的内容】\n脂筏富含胆固醇与鞘脂。"},
      ]}

made, skipped = C.build_cards_from_threads("生物", "生物:第3章:第3章:survey",
                                           "x.pptx", "a" * 64, [TH])
check("★ 从对话出卡：只收 AI 真的给了讲解的那一轮",
      len(made) == 1 and len(skipped) == 1, f"{len(made)} 张 / {len(skipped)} 跳")
check("  被跳过的正是「AI 只是反问」的那一轮（不是漏了）",
      "没有给出讲解" in skipped[0]["reason"], skipped[0]["reason"])
check("★★ 题面用**这段对话开头那一问** —— 不是「别问了直接讲」这句命令",
      "脂筏为什么能当信号转导平台？" in made[0]["fields"]["Front"]
      and "别问了直接讲" not in made[0]["fields"]["Front"],
      made[0]["fields"]["Front"][:120])
check("★ 卡背 = AI 的讲解 + 出处（第 N 页）",
      "胆固醇和鞘脂" in made[0]["fields"]["Back"]
      and "第 27 页" in made[0]["fields"]["Back"], made[0]["fields"]["Back"][:120])
check("★ 框里读出来的原文当语境（有就用 —— 你当时到底在看哪一块）",
      "脂筏富含胆固醇与鞘脂" in made[0]["fields"]["Front"],
      made[0]["fields"]["Front"][:120])
check("  卡上标了来源是「对话」，并记下对话编号与轮次（以后回得去）",
      made[0]["source"]["kind"] == "thread" and made[0]["source"]["tid"] == TH["tid"]
      and made[0]["source"]["turn"] == 2)
check("  标签里带「对话」+ 课程 + 讲次 + 页",
      "对话" in made[0]["tags"] and "生物" in made[0]["tags"]
      and "p27" in made[0]["tags"], str(made[0]["tags"]))

# ---- 身份：**删掉别的对话不能改到我的身份**（用户要"删对话"，这是前提）----
id_a = C.thread_card_id("a" * 64, 27, "m27-1", 1)
check("★ 对话卡身份只由 (源, 页, 对话编号, 轮次) 决定",
      id_a == C.thread_card_id("a" * 64, 27, "m27-1", 1)
      and id_a != C.thread_card_id("a" * 64, 27, "m27-2", 1)
      and id_a != C.thread_card_id("a" * 64, 28, "m27-1", 1)
      and id_a != C.thread_card_id("a" * 64, 27, "m27-1", 2))
two = [TH, dict(TH, tid="m40-9_9_9_9", page=40)]
first_run, _ = C.build_cards_from_threads("生物", "L", "x.pptx", "a" * 64, two)
survivor_before = [c["id"] for c in first_run if c["source"]["tid"] == "m40-9_9_9_9"]
second_run, _ = C.build_cards_from_threads("生物", "L", "x.pptx", "a" * 64,
                                           [two[1]])       # 删掉第一段
check("★★ 删掉前一段对话后，后一段的卡片**身份不变**（否则会悄悄改写旧卡）",
      survivor_before == [c["id"] for c in second_run], str(survivor_before))
check("  对话编号再长/再怪也不会把身份弄脏（短哈希 + 段数固定）",
      len(C.thread_card_id("a" * 64, 27,
                           "生物:第3章:第3章:survey:p27", 1).split(":")) == 4,
      C.thread_card_id("a" * 64, 27, "生物:第3章:第3章:survey:p27", 1))

# ---- 闸门细则 ----
def _turn(**kw):
    base = {"q": "磷脂为什么能横向移动？", "a": "磷脂横向移动不用换层，" * 4,
            "gave_answer": True, "stall": False}
    base.update(kw)
    return base


check("★ 说出「别问了直接讲」那一轮**照样出卡**（那一轮正是 AI 给讲解的一轮）",
      C.thread_turn_cardable(_turn(q="别问了直接讲", stall=True),
                             "磷脂为什么能横向移动？")[0])
check("  但题面会换成开头那一问，不是那句命令",
      C._front_question("别问了直接讲", "磷脂为什么能横向移动？")
      == "磷脂为什么能横向移动？")
check("  开头也没问到东西时，宁可不出卡（没有可用的题面）",
      C._front_question("别问了直接讲", "不知道") == ""
      and not C.thread_turn_cardable(_turn(q="不知道"), "不知道")[0])
check("  AI 只是在反问的那一轮不出卡（背面会是空的）",
      not C.thread_turn_cardable(_turn(gave_answer=False))[0])
check("  太短的问题 + 没有开头问 → 不出卡",
      not C.thread_turn_cardable(_turn(q="嗯"), "")[0])
check("  AI 回答太短的不出卡（撑不起卡背）",
      not C.thread_turn_cardable(_turn(a="对。"))[0])
check("  合格的一轮出卡，且不给理由（空理由 = 没被挡）",
      C.thread_turn_cardable(_turn())[0] and C.thread_turn_cardable(_turn())[1] == "")

# ---- 题面必须**像个问题**（用户实测反馈：出过正面写「我不知道啊」的卡）----
check("★ 表态不算题面：「我不知道啊」「是的」「嗯」都不行",
      all(C._usable_front(x) == "" for x in
          ("我不知道啊", "是的", "嗯", "对", "算了", "直接讲", "别问了直接讲")))
check("★ 陈述也不算题面（打开卡会一头雾水：这要我答什么）",
      C._usable_front("是的，因为能斯特方程") == "")
check("  真问题才算：问号/疑问词",
      C._usable_front("这个图又是什么意思？") == "这个图又是什么意思？"
      and C._usable_front("为什么前者快后者慢") == "为什么前者快后者慢")
check("★ 拿不出题面时返回**空理由**（不是硬跳过）——由调用方决定要不要 AI 反推",
      C.thread_turn_cardable(_turn(q="我不知道啊"), "我也不知道啊") == (False, ""))

# ---- 拿不出题面 → 用 AI 从解答反推（复用既有机制与质量闸门）----
NOFRONT = {"tid": "m56-1", "page": 56, "n": 1, "title": "我不知道啊",
           "turns": [{"q": "我不知道啊", "a": "这张表的核心是一句话：细胞内外离子分布"
                      "是不对称的，靠钠钾泵每消耗一个 ATP 泵出 3 个 Na⁺、泵进 2 个 K⁺。",
                      "gave_answer": True, "stall": True, "sel": ""}]}
got, skip = C.build_cards_from_threads("生物", "L", "x.pptx", "a" * 64, [NOFRONT])
check("★ 没给 AI 出题能力时：跳过，并说清该怎么跑（别加 --no-ai）",
      not got and "别加 --no-ai" in skip[0]["reason"], skip[0]["reason"])

got, skip = C.build_cards_from_threads(
    "生物", "L", "x.pptx", "a" * 64, [NOFRONT],
    question_provider=lambda t: {"question": "细胞内外离子分布为什么不对称？",
                                 "model": "m", "tokens": 1})
check("★★ 给了 AI 出题能力时：从**解答**反推出一道题当卡面",
      len(got) == 1 and "为什么不对称" in got[0]["fields"]["Front"],
      got[0]["fields"]["Front"][:100] if got else str(skip))
check("  卡上标明题面是 AI 出的（以后能分辨）",
      got and got[0]["source"]["front_kind"] == "ai" and "AI出题" in got[0]["tags"],
      str(got[0]["source"]) if got else "")
check("  反推失败就跳过，绝不硬塞一个凑数题面",
      not C.build_cards_from_threads(
          "生物", "L", "x.pptx", "a" * 64, [NOFRONT],
          question_provider=lambda t: {"question": "", "reason": "模型摆烂"})[0])

# ---- 卡背开头那句口头语要去掉（实测看到的卡背第一句）----
check("★ 去掉开头的口头语（「行，那我讲完你得回答我一个问题。」）",
      C._strip_lead_filler(
          "行，那我讲完你得回答我一个问题。这张表的核心是不对称。")
      .startswith("这张表的核心"), C._strip_lead_filler(
          "行，那我讲完你得回答我一个问题。这张表的核心是不对称。")[:40])
check("  只丢**短**的第一句 —— 长句往往是正文，不能丢",
      C._strip_lead_filler("好的，" + "这是一段很长的正文，" * 4 + "。")
      .startswith("好的"))
check("  正常开头的回答一个字不动",
      C._strip_lead_filler("细胞内外离子分布是不对称的。") == "细胞内外离子分布是不对称的。")

# ---- 卡背末尾那句**反问**要去掉（用户验收原话：「卡答案里面还有 ai 的反问，
#      这是不对的」—— 那是助教在考你，不是答案）----
_LONG = ("这张表的核心是离子分布不对称，靠钠钾泵维持。" * 3)
for mk in ("反向验证", "反过来考你", "我考考你", "再问你一个", "现在换你答"):
    h, q = C._strip_tail_question(_LONG + f"{mk}：如果把里外都稀释 10 倍会怎样？")
    check(f"★ 末尾的「{mk}…」被砍掉（卡背不留没人回答的问题）",
          h == _LONG.rstrip("。") or h.startswith("这张表的核心"), h[-40:])
    check(f"  砍下来的那句**不丢**，存起来（{mk}）", q.startswith(mk), q[:30])
check("★ 讲解**中间**的「反过来…」是正文，不能动",
      C._strip_tail_question("反过来，细胞要主动运输。这是重点。")[0]
      == "反过来，细胞要主动运输。这是重点。")
check("  末尾带标记但**没有问号** → 不动（不是反问）",
      C._strip_tail_question(_LONG + "反向验证一下上面的推导。")[1] == "")
check("  标记落在前半段 → 不砍（那不是'末尾的反问'）",
      C._strip_tail_question("反向验证：先看这张表？" + "后面是正文。" * 20)[1] == "")

_cards_ok, _ = C.build_cards_from_threads(
    "生物", "L", "x.pptx", "a" * 64,
    [{"tid": "m9-1", "page": 9, "n": 1, "title": "这图什么意思？",
      "turns": [{"q": "这图什么意思？",
                 "a": _LONG + "反向验证：如果反过来会怎样？",
                 "gave_answer": True, "sel": ""}]}])
check("★ 走完整条出卡链：卡背没有反问结尾，且反问被记进 source",
      _cards_ok and "反向验证" not in _cards_ok[0]["fields"]["Back"]
      and _cards_ok[0]["source"]["reverse_q"].startswith("反向验证"),
      (_cards_ok[0]["fields"]["Back"][-60:] if _cards_ok else "没出卡"))
# ★ 实测踩到：不先洗正文就拿去反推，AI 会把末尾那句反问当成题目
#   → 卡面问一遍、卡背末尾又问同一句。
_seen: dict = {}


def _spy(ann):
    _seen["text"] = ann.get("explanation", "")
    return {"question": "这图里的梯度是干什么用的？"}


C.build_cards_from_threads(
    "生物", "L", "x.pptx", "a" * 64,
    [{"tid": "m9-2", "page": 9, "n": 1, "title": "我不知道啊",
      "turns": [{"q": "我不知道啊",
                 "a": _LONG + "现在换你答：那 Ca²⁺ 为什么压那么低？",
                 "gave_answer": True, "sel": ""}]}],
    question_provider=_spy)
check("★ 递给 AI 反推的是**洗干净的正文**（否则末尾反问会被当成题目）",
      "现在换你答" not in _seen.get("text", ""), _seen.get("text", "")[-50:])

# ---- 重跑幂等：同一段对话跑两遍 → 同样的 id（merge 才去得了重）----
again, _ = C.build_cards_from_threads("生物", "L", "x.pptx", "a" * 64, [TH])
check("★ 重跑同样的对话 → 同样的卡片身份（重跑不会重复制卡）",
      [c["id"] for c in again] == [c["id"] for c in made])

# ---------------------------------------------------------------- 汇总

for p in PASSES:
    print("PASS  " + p)
for f_ in FAILS:
    print("FAIL  " + f_)
print("=" * 60)
print(f"通过 {len(PASSES)} / 失败 {len(FAILS)}")
raise SystemExit(1 if FAILS else 0)
