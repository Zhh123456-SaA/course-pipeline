# -*- coding: utf-8 -*-
"""HTML 课生成器的测试。

最重要的一条是**「不截断」回归**：早先一个「部分」超过 6 个知识点就被 `[:6]`
砍掉尾巴 —— 生物 66 个知识点只生成了 30 个，**36 个被静默丢掉**，而且不报错。
产出少一半点都不吭声，这类"看起来正常的丢数据"最难发现，必须有断言盯住。

跑法：python tests/test_lesson.py
"""
from __future__ import annotations

import inspect
import os
import re
import sys

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

HERE = os.path.dirname(os.path.abspath(__file__))
PROJ = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(PROJ, "src"))
sys.path.insert(0, HERE)
import _pick  # noqa: E402
import lesson_html as L  # noqa: E402

FAILS: list[str] = []
PASSES: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    (PASSES if ok else FAILS).append(f"{name}{(' — ' + detail) if detail else ''}")


def kc(i: int, label: str = "", part: str = "P", deps=None) -> dict:
    return {"id": f"T.{i}", "label": label or f"知识点{i}", "part": part,
            "page": i, "type": "concept", "importance": "key",
            "points": [f"要点{i}"], "self_test": [f"问题{i}？"],
            "deps": deps or []}


# ---------------------------------------------------------------- 1 绝不截断

# 一个部分有 14 个知识点 —— 正是生物课的真实形状（66 个 / 5 个部分）
many = [kc(i) for i in range(1, 15)]
ch = {"id": "测试讲", "label": "测试讲", "kcs": many,
      "outline": {"title": "T", "objectives": ["目标"], "parts": [
          {"label": "P", "from": 1, "to": 20, "summary": "s"}]}}

ordered = L.kcs_of_part(ch, "P")
check("kcs_of_part 返回全部知识点（不再截断到 6）", len(ordered) == 14, str(len(ordered)))

chunks = L.split_lessons(ordered)
check("14 个知识点切成 3 节（6+6+2）", [len(c) for c in chunks] == [6, 6, 2],
      str([len(c) for c in chunks]))
check("★ 切分后总数一个不少", sum(len(c) for c in chunks) == len(ordered),
      f"{sum(len(c) for c in chunks)} vs {len(ordered)}")
check("★ 每节都不超过上限", all(len(c) <= L.MAX_KCS for c in chunks))
check("切分不重复、不遗漏（并集等于全集）",
      {k["id"] for c in chunks for k in c} == {k["id"] for k in ordered})

names = L.lesson_names("P", len(chunks))
check("多节时标 1/3、2/3、3/3", names == ["P（1/3）", "P（2/3）", "P（3/3）"], str(names))
check("只有一节时用原名，不加编号", L.lesson_names("P", 1) == ["P"])
check("空输入返回空（不炸）", L.split_lessons([]) == [] and L.lesson_names("P", 0) == ["P"])

# 边界：正好等于上限、以及上限+1
check("正好 6 个 → 1 节", len(L.split_lessons([kc(i) for i in range(1, 7)])) == 1)
check("7 个 → 2 节", len(L.split_lessons([kc(i) for i in range(1, 8)])) == 2)

# ---------------------------------------------------------------- 2 依赖顺序

a = kc(1, "基础")
b = kc(2, "进阶", deps=["T.1"])
c = kc(3, "高级", deps=["T.2"])
order = [k["id"] for k in L.topo_order([c, b, a])]
check("按依赖顺序排（前置在前，哪怕输入是倒的）",
      order == ["T.1", "T.2", "T.3"], str(order))

cyc = [kc(1, "甲", deps=["T.2"]), kc(2, "乙", deps=["T.1"])]
o2 = L.topo_order(cyc)
check("有环时不死循环、不丢项", len(o2) == 2, str([k["id"] for k in o2]))

miss = [kc(1, "甲", deps=["不存在"])]
check("依赖指向不存在的 id 时照常返回", len(L.topo_order(miss)) == 1)

# ---------------------------------------------------------------- 3 HTML 正确性

one = [kc(1, "带 <标签> 与 & 符号"), kc(2, "引号\"测试")]
html = L.build_lesson("测试课", ch, "P", one, lambda k: ("../assets/s/p001.jpg", 1))
check("标签被转义（不会破页）", "<标签>" not in html and "&lt;标签&gt;" in html)
check("没有未替换的占位符", "{ANSWER}" not in html and "{{" not in html)
check("题目数量与知识点一致",
      len(re.findall(r'class="q" data-q', html)) == len(one))
check("答案默认是隐藏的（检索练习的关键）",
      'class="answer"' in html and 'class="answer show"' not in html)
check("页图用的是相对路径（lessons/ → ../assets/）",
      "../assets/s/p001.jpg" in html)
check("没有外链依赖（离线可用）",
      not re.search(r'(src|href)="https?://', html),
      str(re.findall(r'(?:src|href)="https?://[^"]+', html)[:3]))
check("有自评按钮（学习记录的唯一来源）",
      'data-grade="ok"' in html and 'data-grade="no"' in html)
check("有追问框", 'id="asktext"' in html and 'id="addask"' in html)
check("自评状态存 localStorage 且做了容错",
      "localStorage" in html and "catch(e)" in html)

# 没有自测题时不能崩，也要给出替代指引
bare = kc(1, "没有题目")
bare["self_test"] = []
h2 = L.build_lesson("测试课", ch, "P", [bare], lambda k: (None, 0))
check("知识点没有自测题时不炸、且给出替代指引",
      len(re.findall(r'class="q" data-q', h2)) == 0 and "试着说出它的定义" in h2)

# 图片缺失时不该输出 <img>
check("没有页图时不输出破图标签",
      "<img src=" not in h2)

# ---------------------------------------------------------------- 4 幂等

h_a = L.build_lesson("测试课", ch, "P", one, lambda k: ("../assets/s/p001.jpg", 1))
h_b = L.build_lesson("测试课", ch, "P", one, lambda k: ("../assets/s/p001.jpg", 1))
check("同样输入生成字节一致的 HTML（幂等）", h_a == h_b)

check("文件名净化掉了 Windows 非法字符",
      L.safe_filename('a/b\\c:d*e?f"g<h>i|j') == "a-b-c-d-e-f-g-h-i-j",
      L.safe_filename('a/b\\c:d*e?f"g<h>i|j'))
check("空名字有兜底", L.safe_filename("   ") == "lesson")

# ---------------------------------------------------------------- 5 真实库：一个不许少

picked = _pick.pick(PROJ)
if not picked:
    PASSES.append("（跳过真实库核对：没有可用的课程）")
else:
    course, croot, _ = picked
    import json
    kp = os.path.join(croot, ".ledger", "kcs.json")
    if not os.path.exists(kp):
        PASSES.append(f"（跳过真实库核对：{course} 还没有 kcs.json）")
    else:
        data = json.load(open(kp, encoding="utf-8"))
        for chx in data.get("chapters", []):
            total = len(chx.get("kcs") or [])
            parts = [p["label"] for p in (chx.get("outline") or {}).get("parts") or []]
            if not parts:
                continue
            covered = 0
            for p in parts:
                covered += len(L.kcs_of_part(chx, p))
            check(f"★ {course} · {chx.get('label')}：各节加起来 == 全部知识点"
                  f"（{covered}/{total}）", covered == total,
                  f"差了 {total - covered} 个")

# ---------------------------------------------------------------- 6 代码里不留截断

# 只看**代码行**，不要把文档字符串里引用的反例当违规
# （第一版就踩了这个：注释里写了「早先直接 [:limit] 截断」，结果断言自己被自己绊倒）
_code = "\n".join(l for l in inspect.getsource(L.kcs_of_part).splitlines()
                  if not l.strip().startswith(("#", '"""', "*", "★")))
check("kcs_of_part 里不再有截断写法",
      "[:limit]" not in _code and "[: limit]" not in _code, _code.strip()[:120])
check("  而且参数表里也没有 limit 了（免得留着让人以为还能截断）",
      "limit" not in inspect.signature(L.kcs_of_part).parameters)

# ---------------------------------------------------------------- 汇总

for p in PASSES:
    print("PASS  " + p)
for f_ in FAILS:
    print("FAIL  " + f_)
print("=" * 60)
print(f"通过 {len(PASSES)} / 失败 {len(FAILS)}")
raise SystemExit(1 if FAILS else 0)
