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

# ★ v2 新增的两条硬过滤（用户实测反馈「太碎、了解的事实类太多」）
ok_fact, why_fact = K.check_kc({**GOOD, "type": "fact"}, {10})
check("不要 fact 类（信息密度低）", (not ok_fact) and "fact" in why_fact, why_fact)
ok_info, why_info = K.check_kc({**GOOD, "importance": "info"}, {10})
check("不要 info 级（了解即可）", (not ok_info) and "info" in why_info, why_info)

# ★ 应用包装（用户原话「什么物理与医疗不要有」）
for lb in ("骨折牵引中的张力控制", "压强与减压床垫", "摩擦的医学实例"):
    ok_a, why_a = K.check_kc({**GOOD, "label": lb}, {10})
    check(f"label 是应用包装要被拦：{lb}", (not ok_a) and "应用包装" in why_a, why_a)

# ★ 要点级的应用包装：只剔那一条，其余保留
kc_app = {**GOOD, "points": ["F = -kx，负号表示方向与形变相反。",
                             "医学版本：血管壁的弹性可用胡克定律描述。",
                             "k 为劲度系数，弹性限度内成立。"]}
clean, removed = K.strip_applications(kc_app)
check("剔掉带医学包装的那条要点", len(removed) == 1 and len(clean["points"]) == 2,
      f"去 {len(removed)} 留 {len(clean['points'])}")
check("  保留的要点没被误伤",
      "F = -kx" in clean["points"][0] and "劲度系数" in clean["points"][1])

kc_all_app = {**GOOD, "points": ["医学联系：血管弯曲处管壁承受附加压力。",
                                 "临床对应：主动脉弓。"]}
clean2, removed2 = K.strip_applications(kc_all_app)
check("要点全是应用包装时整条作废（由调用方判定）", clean2["points"] == [],
      str(clean2["points"]))

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
    {"id": "L05.1", "page": 10, "pages": [10], "questions": []},
    {"id": "L05.2", "page": 11, "pages": [11], "questions": []},
]
anns = [
    {"page": 10, "no": 1, "question": "怎么定义收缩的方向？", "created_at": "x"},
    {"page": 11, "no": 2, "question": "能不能举个反例？"},
    {"page": 10, "no": 3, "question": ""},        # 无提问，不挂
    {"page": 99, "no": 4, "question": "附近没有知识点"},   # 无匹配页，不挂
]
n = K.link_questions(demo_kcs, anns)
check("追问挂到对应页的知识点上", n == 2, f"挂了 {n} 条")
check("  第 10 页的问题挂到 L05.1", demo_kcs[0]["questions"][0]["q"] == "怎么定义收缩的方向？")
check("  第 11 页的问题挂到 L05.2", demo_kcs[1]["questions"][0]["q"] == "能不能举个反例？")

# ★ 就近匹配：知识点跨页时模型只挑一个锚定页，精确匹配会漏。
#   实测踩到：用户在第 10 页问张力，而合并后的「张力」知识点锚在第 11 页。
near_kcs = [{"id": "L05.5", "page": 11, "pages": [11], "questions": []}]
n_near = K.link_questions(near_kcs, [{"page": 10, "no": 1, "question": "怎么定义收缩的方向？"}])
check("邻近页的追问也能挂上（跨页知识点）", n_near == 1, f"挂了 {n_near} 条")
far_kcs = [{"id": "L05.9", "page": 30, "pages": [30], "questions": []}]
n_far = K.link_questions(far_kcs, [{"page": 10, "no": 1, "question": "太远了"}])
check("隔太远的追问不乱挂", n_far == 0, f"挂了 {n_far} 条")

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

    def page_section(body: str, no: int) -> str:
        a = body.find(f"### 第 {no} 页")
        if a == -1:
            return ""
        b = body.find(f"### 第 {no + 1} 页", a)
        return body[a:b if b != -1 else len(body)]

    # **不写死页号**：知识点会随 prompt 版本重排锚定页（v1 锚第 10 页、v2 锚第 11 页）。
    # 从账本里挑一页「既有知识点、又有追问记录」的，才测得到完整顺序。
    lec = next((c for c in chapters if c["id"].startswith("05")), chapters[0])
    kc_pages = {int(k["page"]) for k in lec["kcs"]}
    q_pages = {int(q["page"]) for k in lec["kcs"] for q in (k.get("questions") or [])}
    both = sorted(kc_pages & q_pages)
    probe = both[0] if both else sorted(kc_pages)[0]
    sec = page_section(text, probe)
    check(f"第 {probe} 页有知识点块", "🎯" in sec, sec[:60])

    if both:
        i_kc = sec.find("🎯")
        i_q = sec.find("🤖 追问记录")
        i_mine = sec.find(f"批注区 p{probe} 开始")
        check(f"第 {probe} 页顺序：知识点 → 追问 → 我的手写位",
              i_kc != -1 and i_q != -1 and i_mine != -1 and i_kc < i_q < i_mine,
              f"kc={i_kc} q={i_q} mine={i_mine}")
    else:
        PASSES.append("（跳过顺序检查：没有同时含知识点与追问的页）")

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
