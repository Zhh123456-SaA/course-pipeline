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
import shutil
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
check("页图说明写的是「PDF 第 N 页」（避免与课件自印页码混淆）",
      "PDF 第 1 页" in html and "课件第" not in html)
check("没有外链依赖（离线可用）",
      not re.search(r'(src|href)="https?://', html),
      str(re.findall(r'(?:src|href)="https?://[^"]+', html)[:3]))
check("有自评按钮（学习记录的唯一来源）",
      'data-grade="ok"' in html and 'data-grade="no"' in html)
# ★ 三档而不是两档：「不知道」永不算错（抄 amosblomqvist/learn 的
#   correct|wrong|dont_know）。两档判不出"猜对"—— 而猜对恰恰是要提前复习的红旗。
check("自评是三档（会 / 不确定 / 不会）",
      'data-grade="mid"' in html and "on-mid" in html)
check("三档各自有文案", "不确定" in html)
# ★ 用户真实的行为是「框选一段 + 提问」（他在逐页精读器里就这么干了 12 次），
#   而不是对着空文本框打字。所以提问入口必须长在答案的文字上。
check("选中答案里的词就能提问（浮层 + 鼠标松开设监听）",
      'pop.id = "askpop"' in html and 'addEventListener("mouseup"' in html
      and "getSelection" in html and "takeSelection" in html)
check("  且只在知识点区内生效（不在页头/追问区乱弹）",
      'closest("section.kc")' in html)
check("有导出按钮（数据不能困在 localStorage 里）",
      'id="expbtn"' in html and "exportLog" in html)
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

# ---------------------------------------------------------------- 3b 两种课型

# 用户把两条管道说清楚了：
#   记忆型（生物）「先问 → 你答 → 看答案」   = ask-first
#   数理（物理）  「你教授我 → 我记笔记 → 你出题考我 → 我用平板做答，有过程」 = teach-first
h_ask = L.build_lesson("测试课", ch, "P", one, lambda k: ("../assets/s/p001.jpg", 1),
                       mode=L.MODE_ASK)
h_tch = L.build_lesson("测试课", ch, "P", one, lambda k: ("../assets/s/p001.jpg", 1),
                       mode=L.MODE_TEACH)

check("ask-first：作答区是文本框", "<textarea" in h_ask)
check("ask-first：讲解是折叠的（先问你，不先给）",
      "<details" in h_ask and 'class="teachbox"' not in h_ask)
check("teach-first：讲解默认展开（先教，不折叠）",
      'class="teachbox"' in h_tch and "<details" not in h_tch)
check("teach-first：作答区是**贴手写图**，不是打字",
      "data-drop" in h_tch and "平板" in h_tch)
check("teach-first：没有 textarea（数理解答打字打不出来）",
      "<textarea" not in h_tch.split('class="askbox"')[0])
check("课型写进了 data-mode（两种课型互不覆盖）",
      'data-mode="ask-first"' in h_ask and 'data-mode="teach-first"' in h_tch)

# ★ 回归（真实事故）：改作答区时把 answer_html 只放进了 teach 分支，
#   结果**背记课的「看答案」点开是空的** —— 答案块整个没生成。
#   实测靠产出端核对抓到：`class="answer"` 在 ask-first 文件里出现 0 次。
for nm, h in (("ask-first", h_ask), ("teach-first", h_tch)):
    check(f"★ {nm}：答案块必须存在（两种课型都要有参考答案）",
          'class="answer"' in h and "参考答案" in h)
    check(f"★ {nm}：答案默认是隐藏的（要点已经展示的那些除外）",
          'class="answer show"' not in h)

check("未实现的课型名会被兜回 ask-first",
      'data-mode="ask-first"' in L.build_lesson("测试课", ch, "P", one,
                                                lambda k: (None, 0), mode="乱写"))

# ★ 回归（用户实测反馈）：「我希望所有问题我可以在最后一次上传，
#   每个问题平板导出很烦很影响效率」—— 每题一个上传框 = 每做一题从平板导出一次，
#   把连续的学习切成碎步。所以整节课只留**一个**上传区，放在末尾。
_n_drop = h_tch.count('class="drop" data-drop')
check("★ teach-first：整节课**只有一个**上传区（不是每题一个）", _n_drop == 1, f"{_n_drop} 个")
check("★ 上传区排在所有知识点之后（末尾的「收尾」之前）",
      h_tch.find('id="mywork"') > h_tch.rfind('id="kc'), "位置不对")
check("★ 写明「一次性」且支持多选多张", "一次性" in h_tch and 'multiple' in h_tch)
check("每张图有可选标注框（不强制与题号对应）",
      "shotnote" in h_tch and "可不填" in h_tch)
check("ask-first 课里没有这个上传区", 'id="mywork"' not in h_ask)

# ---------------------------------------------------------------- 3c 过课件（survey）

# 用户原话：「我希望我可以**先看课件，划线记笔记问问题**，之后经历现在的流程」。
# 关键技术决定：课件页是图片、选不中文字 → 划线只能是**在图上拖一个矩形**，
# 而这恰好就是他框选提问的同一个动作。
_plist = [{"no": 1, "src": "../assets/s/p001.jpg"},
          {"no": 2, "src": "../assets/s/p002.jpg"}]
h_sv = L.build_survey("测试课", ch, _plist, html_name="T")
check("survey：课型标记正确", 'data-mode="survey"' in h_sv)
check("survey：页图列表嵌进去了（翻页靠它）", "__PAGES__" in h_sv and "p002.jpg" in h_sv)
check("survey：有可拖框的覆盖层（覆盖层由 JS 逐页生成，不是单个静态节点）",
      'className = "layer"' in h_sv and "pointerdown" in h_sv)
check("survey：拖框后会生成标记（坐标按百分比，与分辨率无关）",
      "pointerup" in h_sv and 'style.left = l + "%"' in h_sv)
check("survey：框上能提问", "❓" in h_sv and "askmk" in h_sv)
check("survey：**不出题、不给答案**（它只是「过一遍」）",
      "data-q=" not in h_sv and 'class="answer"' not in h_sv
      and "看答案" not in h_sv and "data-grade" not in h_sv)
check("survey：能导出（标记要能进账本）", "exportLog" in h_sv and "marks" in h_sv)

# ★ 回归（用户实测反馈）：「我希望课件能通过**滚轮下移**显示，而不是点击翻页」
check("★ survey：连续滚动（一次性铺出所有页，不再点翻页）",
      "buildAll" in h_sv and 'id="stage"' in h_sv
      and 'id="prev"' not in h_sv and 'id="next"' not in h_sv)
check("★ survey：每页都有自己的可拖框层（data-layer=N）",
      'layer.dataset.layer' in h_sv or 'dataset.layer = String(p.n)' in h_sv)
check("★ survey：当前页靠滚动位置判定（IntersectionObserver），不靠按钮",
      "IntersectionObserver" in h_sv)
check("★ survey：拖框后有明确反馈（用户原话「我也不知道成没成功」）",
      'id="toast"' in h_sv and "已记下" in h_sv)
check("★ survey：拖框即写账本（不等导出）", '"/api/mark"' in h_sv)
check("survey：没有页图时不炸（退回一个占位页）",
      'data-mode="survey"' in L.build_survey("测试课", ch, []))

# ★ 回归（真实事故）：孤儿清理按"本次没产出的一律删"，跑一次 `--mode survey`
#   （整讲只出 1 页）把 15 节 ask-first 课**全删了**。多种课型是并存设计，
#   清理必须按课型分开。
import tempfile as _tf  # noqa: E402
_d = os.path.join(_tf.gettempdir(), "lesson-orphan-test")
shutil.rmtree(_d, ignore_errors=True)
os.makedirs(_d, exist_ok=True)
for nm, md in (("a.html", "ask-first"), ("b.html", "ask-first"), ("c.html", "survey"),
               ("keep.html", "survey")):
    with open(os.path.join(_d, nm), "w", encoding="utf-8") as f:
        f.write(f'<body data-mode="{md}">x</body>')
_keep = os.path.normcase(os.path.abspath(os.path.join(_d, "keep.html")))
_n = L.clean_orphans(_d, {_keep}, "survey")
check("★ clean_orphans 只删同课型的孤儿（survey 不能删掉 ask-first）",
      _n == 1 and not os.path.exists(os.path.join(_d, "c.html"))
      and os.path.exists(os.path.join(_d, "a.html")) and os.path.exists(os.path.join(_d, "b.html")),
      f"删了 {_n} 个；剩下 {sorted(os.listdir(_d))}")
check("  被产出的文件不会被删", os.path.exists(os.path.join(_d, "keep.html")))
check("  目录不存在时返回 0（不炸）", L.clean_orphans(os.path.join(_d, "没有"), set(), "survey") == 0)
shutil.rmtree(_d, ignore_errors=True)

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

    # ★ 回归（真实事故，静默失效）：页图扩展名在两条摄取路径上不一样 ——
    #   PDF 出 `.png`、Office 出 `.jpg`，而生成器一度写死 `.jpg`。
    #   结果**物理 15 节 56 张、线代 5 节 16 张全是破图**，不报错、不失败，
    #   用户打开只看到"课件第 N 页"底下空着。
    #   光断言"没有外链"是不够的 —— 必须断言**每个 src 都能解析到真实文件**。
    ldir = os.path.join(croot, "lessons")
    if os.path.isdir(ldir):
        n_img = n_bad = 0
        bad_sample = []
        for fn in sorted(os.listdir(ldir)):
            if not fn.endswith(".html"):
                continue
            hh = open(os.path.join(ldir, fn), encoding="utf-8").read()
            for src in set(re.findall(r'<img\s+src="([^"]+)"', hh)):
                if src.startswith(("http://", "https://", "data:")):
                    continue
                n_img += 1
                tgt = os.path.normpath(os.path.join(ldir, src))
                if not os.path.exists(tgt):
                    n_bad += 1
                    if len(bad_sample) < 3:
                        bad_sample.append(src)
        check(f"★ {course} 的课里每个页图都真的存在（{n_img - n_bad}/{n_img}）",
              n_bad == 0, f"破图 {n_bad} 个，例如 {bad_sample}")
        check(f"  {course} 的课里至少有一个页图引用（不是空跑）", n_img > 0, str(n_img))

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
