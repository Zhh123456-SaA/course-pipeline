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
import re
import shutil
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

# ---------------------------------------------------------------- 5b 讲次结构（导览页）
# 用户建议：让 AI 先读课件的「本讲导览/学习目标」页，领会思路，再据此归属知识点。

demo_pages = [
    {"no": 1, "text": "封面"},
    {"no": 2, "text": "本讲学习目标\n1. 掌握受力分析\n2. 理解惯性系"},
    {"no": 3, "text": "本章内容\n第一部分 力的种类\n第二部分 牛顿定律"},
    {"no": 10, "text": "张力：拉紧的绳……"},
    {"no": 11, "text": ""},
]
ov = K.overview_pages(demo_pages)
check("能认出导览/目标页", set(ov) >= {2, 3} and 10 not in ov, str(ov))
check("导览页数有上限", len(K.overview_pages(demo_pages, limit=1)) == 1)

no_kw = [{"no": 1, "text": "封面页"}, {"no": 2, "text": "随便一点内容"}]
check("没有导览页时退回最前面几页",
      K.overview_pages(no_kw) == [1, 2], str(K.overview_pages(no_kw)))

hl = K.page_headlines(demo_pages)
check("页标题速览含页号与首行", "第2页:" in hl and "本讲学习目标" in hl, hl[:60])
check("空页不出现在速览里", "第11页" not in hl)

# apply_links：把收口结果套回知识点，**只接受能解析到真实 id 的依赖**
link_kcs = [
    {"id": "L05.1", "label": "甲", "page": 6, "points": ["a"], "deps": []},
    {"id": "L05.2", "label": "乙", "page": 9, "points": ["b"], "deps": []},
    {"id": "L05.3", "label": "丙", "page": 12, "points": ["c"], "deps": []},
]
links = {
    "links": [
        {"id": "L05.1", "part": "力的种类", "deps": [], "is_hub": True},
        {"id": "L05.2", "part": "力的种类", "deps": ["L05.1"], "is_hub": False},
        {"id": "L05.3", "part": "牛顿定律", "deps": ["L05.1", "不存在", "L05.3"], "is_hub": False},
    ],
    "order": ["L05.1", "L05.2", "L05.3"],
}
link_kcs, order = K.apply_links(link_kcs, links)
check("归属部分被套上", link_kcs[0]["part"] == "力的种类")
check("真实依赖被套上", link_kcs[1]["deps"] == ["L05.1"], str(link_kcs[1]["deps"]))
check("悬空依赖被丢掉", "不存在" not in link_kcs[2]["deps"], str(link_kcs[2]["deps"]))
check("自依赖被丢掉", "L05.3" not in link_kcs[2]["deps"])
check("枢纽标记被套上", link_kcs[0]["is_hub"] is True)
check("学习顺序被保留", order == ["L05.1", "L05.2", "L05.3"], str(order))

# 顺序缺项要补齐（不能因为模型漏写就丢掉知识点）
_, order2 = K.apply_links(
    [{"id": "L05.9", "label": "x", "page": 3, "points": ["p"], "deps": []},
     {"id": "L05.4", "label": "y", "page": 1, "points": ["p"], "deps": []}],
    {"links": [], "order": ["L05.9"]})
check("学习顺序补齐漏项且不重复",
      sorted(order2) == ["L05.4", "L05.9"] and len(order2) == 2, str(order2))

# put_lecture 要能存下 outline 与 order
import copy  # noqa: E402
_demo_data: dict = {"version": 1, "title": "", "chapters": []}
K.put_lecture(_demo_data, "L05", "第五讲", link_kcs,
              outline={"title": "动力学", "parts": [{"label": "力的种类"}]},
              order=order)
ch = K.chapters_of(_demo_data)["L05"]
check("put_lecture 存下 outline", ch["outline"]["title"] == "动力学")
check("put_lecture 存下 order", ch["order"] == ["L05.1", "L05.2", "L05.3"])
check("put_lecture 覆盖同 id 章节（不重复追加）",
      len(_demo_data["chapters"]) == 1)
K.put_lecture(_demo_data, "L05", "第五讲v2", link_kcs)
check("再 put 同一讲仍是 1 章且内容已更新",
      len(_demo_data["chapters"]) == 1 and _demo_data["chapters"][0]["label"] == "第五讲v2")

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

# ---------------------------------------------------------------- 6b 知识点图谱
# 用户要求「建立知识点图谱」—— Obsidian 的图谱只认 [[双链]]，
# 所以知识点必须成为独立笔记、互相链接。文件名不能有 Windows 禁用字符。

import render as R  # noqa: E402

check("笔记名用 id + 清洗后的 label",
      R.kc_note_name({"id": "L05.9", "label": "绳与棒中弹性力方向的判别"})
      == "L05.9 绳与棒中弹性力方向的判别")
check("文件名里的禁用字符被替掉",
      "/" not in R.kc_note_name({"id": "L05.1", "label": "a/b:c*d?e"})
      and ":" not in R.kc_note_name({"id": "L05.1", "label": "a/b:c*d?e"}),
      R.kc_note_name({"id": "L05.1", "label": "a/b:c*d?e"}))
check("双链格式正确", R.kc_link({"id": "L05.2", "label": "x"}) == "[[L05.2 x]]")

# 原子笔记：生成块 + 你的理解区；重跑要保住你写的字
import tempfile as _tf  # noqa: E402
_tmpd = os.path.join(PROJ, ".pdw_test_kcnotes")
os.makedirs(_tmpd, exist_ok=True)
_p = os.path.join(_tmpd, "L09.1 测试.md")
_body = R.render_kc_note({"id": "L09.1", "label": "测试", "type": "concept",
                          "importance": "must", "points": ["要点一"],
                          "self_test": ["问题一？"], "deps": [], "questions": [],
                          "pages": [3]},
                         "L09", "L09", {}, {}, [])
_first = R.write_kc_note(_p, "测试", _body)
check("原子笔记含生成块", "<!-- gen:begin -->" in _first and "问题一？" in _first)
check("原子笔记含「我的理解」区", "## 我的理解" in _first)
check("标题只出现一次（曾因两处都加而重复）", _first.count("# 测试") == 1,
      str(_first.count("# 测试")))
# 写点自己的东西再重跑
with open(_p, "a", encoding="utf-8") as f:
    f.write("\n我自己的理解，不许被冲掉。\n")
R.write_kc_note(_p, "测试", _body)
_second = open(_p, encoding="utf-8").read()
check("重跑后我写的内容仍在", "不许被冲掉" in _second)
check("重跑后生成块仍在", "问题一？" in _second)
shutil.rmtree(_tmpd, ignore_errors=True)

# ---------------------------------------------------------------- 6c 真实产物：图谱与问题
if chapters:
    kc_dir = os.path.join(LIB, "notes", "知识点")
    kc_files = [f for f in os.listdir(kc_dir)] if os.path.isdir(kc_dir) else []
    check("生成了知识点原子笔记", len(kc_files) >= 10, f"{len(kc_files)} 篇")

    # ★ 最关键的一条：**每一处 [[双链]] 都必须指向真实存在的笔记**。
    #   实测踩过：回到讲次的链接用了导览标题，而讲次笔记的文件名是讲次 id，链是断的。
    existing = {os.path.splitext(f)[0] for f in os.listdir(os.path.join(LIB, "notes"))
                if f.endswith(".md")}
    existing |= {os.path.splitext(f)[0] for f in kc_files}
    broken: list[str] = []
    for d in (os.path.join(LIB, "notes"), kc_dir):
        if not os.path.isdir(d):
            continue
        for f in os.listdir(d):
            if not f.endswith(".md"):
                continue
            text = open(os.path.join(d, f), encoding="utf-8").read()
            # 先剥掉行内代码：指南里用 `[[双链]]` 讲概念，那不是真链接（踩过假阳性）
            text = re.sub(r"`[^`]*`", "", text)
            for m in re.findall(r"\[\[([^\]|#]+)", text):
                if m.strip() not in existing:
                    broken.append(f"{f} -> [[{m}]]")
    check("所有 [[双链]] 都能解析（图谱里不会出现断链）", not broken,
          str(broken[:3]))

    qpath = os.path.join(LIB, "notes", "_问题清单.md")
    check("生成了自测问题清单", os.path.exists(qpath))
    if os.path.exists(qpath):
        qtext = open(qpath, encoding="utf-8").read()
        n_q = qtext.count("\n    - ") + qtext.count("— ")
        check("问题清单里有题", n_q >= 10, f"{n_q} 道")
        check("问题以问号结尾", "？" in qtext)
        check("问题按部分分组", "📂" in qtext)
    check("生成了使用指南", os.path.exists(os.path.join(LIB, "notes", "_怎么用这个库.md")))

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
