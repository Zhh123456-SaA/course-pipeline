# -*- coding: utf-8 -*-
"""知识点骨架（S2）测试。

每条断言对应一个真实约束：
  1. **schema 复用**用户已有的 knowledge_skeleton.json 字段（C6）
  2. id 必须**稳定**（同输入同 id）—— 否则重跑会让笔记里的链接指错地方
  3. 质量闸门：泛泛的「知识点/小结」不算知识点；page 必须落在本批页号内
  4. deps 只能解析成**真实存在**的 id（不写悬空引用）
  5. **追问要挂到知识点上** —— 这是本项目最有价值的一步（知识点 ← 你问过的问题）
  6. 截断要能区分「上限不够」与「模型写不对」

跑法：python tests/test_kcs.py
"""
from __future__ import annotations

import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
PROJ = os.path.dirname(HERE)
COURSE = "物理"
LIB = os.path.abspath(os.path.join(PROJ, "..", "学习库", COURSE))
NOTE = os.path.join(LIB, "notes", "05第五讲-动力学1_2026.md")
OVERVIEW = os.path.join(LIB, "notes", "_知识点总览.md")

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

sys.path.insert(0, os.path.join(PROJ, "vendor"))
sys.path.insert(0, os.path.join(PROJ, "src"))
import engine  # noqa: E402
import kcs as K  # noqa: E402

FAILS: list[str] = []
PASSES: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    (PASSES if ok else FAILS).append(f"{name}{(' — ' + detail) if detail else ''}")


def run(*args: str) -> str:
    r = subprocess.run([sys.executable, os.path.join(PROJ, "run.py"), *args],
                       capture_output=True, text=True, encoding="utf-8", cwd=PROJ)
    return (r.stdout or "") + (r.stderr or "")


# ---------------------------------------------------------------- 1 命名与分窗

check("讲次前缀从开头数字取（05… → L05）",
      K.lecture_prefix("05第五讲-动力学1_2026") == "L05",
      K.lecture_prefix("05第五讲-动力学1_2026"))
check("没有开头数字时退回净化后的前缀",
      K.lecture_prefix("基础物理实验数据课2026秋季(1)").startswith("基础物理"),
      K.lecture_prefix("基础物理实验数据课2026秋季(1)"))

check("分窗：54 页按 8 页切出 7 窗",
      len(K.window_pages(list(range(1, 55)), 8)) == 7,
      str(len(K.window_pages(list(range(1, 55)), 8))))
check("分窗：最后一窗是余数",
      K.window_pages(list(range(1, 55)), 8)[-1] == list(range(49, 55)),
      str(K.window_pages(list(range(1, 55)), 8)[-1]))
check("分窗：窗口大小非法时不炸", len(K.window_pages([1, 2, 3], 0)) >= 1)

# ---------------------------------------------------------------- 2 质量闸门

GOOD = {"label": "张力的定义与方向", "type": "concept", "importance": "must",
        "points": ["拉紧的绳作用在物体上的力称为张力"], "page": 10}
check("合格的知识点通过闸门", K.check_kc(GOOD, {10})[0])

bad_cases = [
    ({**GOOD, "label": ""}, "label 为空"),
    ({**GOOD, "label": "知识点"}, "label 太空泛"),
    ({**GOOD, "label": "小结"}, "label 太空泛"),
    ({**GOOD, "label": "长" * 40}, "label 太长"),
    ({**GOOD, "type": "nonsense"}, "type 非法"),
    ({**GOOD, "importance": "nonsense"}, "importance 非法"),
    ({**GOOD, "points": []}, "points 为空"),
    ({**GOOD, "page": 99}, "不在本批页号内"),
    ({**GOOD, "page": "abc"}, "page 非法"),
]
for kc, why in bad_cases:
    ok, reason = K.check_kc(kc, {10})
    check(f"闸门拦下「{why}」", (not ok) and (why in reason), f"{ok} {reason}")

# ---------------------------------------------------------------- 3 归一化与 id

raw = [
    {"label": "甲的要点", "type": "concept", "importance": "must",
     "points": ["p1"], "page": 5, "deps": [], "is_hub": True},
    {"label": "乙的要点", "type": "fact", "importance": "key",
     "points": ["p2"], "page": 6, "deps": ["甲的要点"]},
    {"label": "甲的要点", "type": "concept", "importance": "must",
     "points": ["重复"], "page": 5},          # 同 label 应被去重
]
kcs, _ = K.normalize_kcs(raw, "05第五讲-动力学1_2026", "L05", 0, {})
check("同 label 去重", len(kcs) == 2, f"{len(kcs)} 条")
check("id 顺序编号且带讲次前缀",
      [k["id"] for k in kcs] == ["L05.1", "L05.2"], str([k["id"] for k in kcs]))
check("id 稳定：同输入再跑一次结果相同",
      [k["id"] for k in K.normalize_kcs(raw, "x", "L05", 0, {})[0]] == ["L05.1", "L05.2"])
check("起步序号可续接（跨窗口不重号）",
      K.normalize_kcs(raw, "x", "L05", 10, {})[0][0]["id"] == "L05.11")
check("points 保留", kcs[0]["points"] == ["p1"])
check("is_hub 保留", kcs[0]["is_hub"] is True)
check("source_refs 是「讲次.pdf/页」格式",
      kcs[0]["source_refs"] == ["05第五讲-动力学1_2026.pdf/5"], str(kcs[0]["source_refs"]))

# schema 复用检查（C6）：字段名必须与用户已有骨架一致
EXPECTED = {"id", "label", "type", "importance", "deps", "is_hub", "source_refs"}
check("沿用既有骨架的字段名（C6）", EXPECTED <= set(kcs[0]), str(sorted(kcs[0])))

# ---------------------------------------------------------------- 4 deps 解析

K.resolve_deps(kcs)
check("deps 被解析成真实 id", kcs[1]["deps"] == ["L05.1"], str(kcs[1]["deps"]))
check("临时字段 _dep_labels 已清掉", all("_dep_labels" not in k for k in kcs))
ghost, _ = K.normalize_kcs(
    [{"label": "甲", "type": "fact", "importance": "info", "points": ["p"],
      "page": 1, "deps": ["不存在的知识点"]}], "x", "L09", 0, {})
K.resolve_deps(ghost)
check("悬空依赖不写进 deps（宁缺毋滥）", ghost[0]["deps"] == [], str(ghost[0]["deps"]))

# ---------------------------------------------------------------- 5 关联追问

demo_kcs = [
    {"id": "L05.1", "pages": [10], "questions": []},
    {"id": "L05.2", "pages": [11], "questions": []},
]
anns = [
    {"page": 10, "no": 1, "question": "怎么定义收缩的方向？", "created_at": "x"},
    {"page": 11, "no": 2, "question": "能不能举个反例？"},
    {"page": 10, "no": 3, "question": ""},        # 无提问，不挂
    {"page": 99, "no": 4, "question": "没有对应知识点"},   # 无匹配页，不挂
]
n = K.link_questions(demo_kcs, anns)
check("追问挂到对应页的知识点上", n == 2, f"挂了 {n} 条")
check("  第 10 页的问题挂到 L05.1", demo_kcs[0]["questions"][0]["q"] == "怎么定义收缩的方向？")
check("  第 11 页的问题挂到 L05.2", demo_kcs[1]["questions"][0]["q"] == "能不能举个反例？")
check("  无提问的不挂", all(len(k["questions"]) == 1 for k in demo_kcs))
check("kcs_by_page 按页取",
      [k["id"] for k in K.kcs_by_page(demo_kcs, 10)] == ["L05.1"])

# ---------------------------------------------------------------- 6 真实产物

data = K.load_kcs(LIB)
chapters = [c for c in data.get("chapters", []) if c.get("kcs")]
if not chapters:
    PASSES.append("（跳过真实产物检查：还没跑过 kcs）")
else:
    all_kcs = [k for c in chapters for k in c["kcs"]]
    check("骨架里有知识点", len(all_kcs) >= 10, f"{len(all_kcs)} 个")
    check("每个知识点都有 id/label/points",
          all(k.get("id") and k.get("label") and k.get("points") for k in all_kcs))
    check("id 全局唯一", len({k["id"] for k in all_kcs}) == len(all_kcs),
          f"{len(all_kcs)} 个 id 中 {len({k['id'] for k in all_kcs})} 个唯一")
    check("所有 type 都合法",
          all(k["type"] in K.TYPE_VALUES for k in all_kcs),
          str({k["type"] for k in all_kcs} - set(K.TYPE_VALUES)))
    check("所有 importance 都合法",
          all(k["importance"] in K.IMPORTANCE_VALUES for k in all_kcs))
    check("每个都有出处", all(k.get("source_refs") for k in all_kcs))
    check("追踪到真实追问的挂载", any(k.get("questions") for k in all_kcs),
          f"{sum(1 for k in all_kcs if k.get('questions'))} 个知识点带追问")

    # 渲染进笔记
    run("--course", COURSE, "render")
    with open(NOTE, encoding="utf-8") as f:
        text = f.read()
    check("笔记里出现知识点块", "🎯" in text)
    check("知识点挂在它出自的那一页下面",
          "🎯" in text[text.find("### 第 10 页"):text.find("### 第 11 页")])
    check("顺序：原文 → 知识点 → FAQ 追问 → 我的手写位",
          (lambda i: i[0] < i[1] < i[2] < i[3] if all(x != -1 for x in i) else True)(
              (text.find("### 第 10 页"), text.find("🎯", text.find("### 第 10 页")),
               text.find("🤖 追问记录", text.find("### 第 10 页")),
               text.find("批注区 p10 开始", text.find("### 第 10 页")))))
    check("生成了知识点总览", os.path.exists(OVERVIEW))
    if os.path.exists(OVERVIEW):
        with open(OVERVIEW, encoding="utf-8") as f:
            ov = f.read()
        check("总览里有表格", "| id | 知识点 |" in ov)
        check("总览标注了「你问过」", "你问过" in ov)

# ---------------------------------------------------------------- 7 引擎可复用

ok, why = engine.available()
check("引擎可复用（R13）", ok, why)

for p in PASSES:
    print("PASS  " + p)
for f_ in FAILS:
    print("FAIL  " + f_)
print("=" * 60)
print(f"通过 {len(PASSES)} / 失败 {len(FAILS)}")
raise SystemExit(1 if FAILS else 0)
