# -*- coding: utf-8 -*-
"""页面里的 JS **真的跑一遍**（不只是看字符串在不在）。

为什么非要有这个文件：这套页面已经两次被"JS 静默报错"坑过 ——
① `ladderAsk` 调了**另一个 `<script>` 块里**的 `esc`，ReferenceError，
   AI 的回复根本不渲染（用户看到的症状是「AI 回复消失了」）；
② 嵌在 Python 三引号里的 `\\n` 变成了真换行，JS 语法错，整块脚本不执行。
这两种问题**字符串断言全都会 PASS**（代码明明写着），只有真跑才会炸。
所以这里：语法用 `node --check` 逐个 `<script>` 块过一遍，
再把子对话那三个函数抠出来，配一套最小 DOM **真调用**一次。

跑法：python tests/test_page_js.py
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
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
import lesson_html as L  # noqa: E402

FAILS: list[str] = []
PASSES: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    (PASSES if ok else FAILS).append(f"{name}{(' — ' + detail) if detail else ''}")


def node(*args: str, **kw):
    exe = shutil.which("node")
    if not exe:
        return None
    return subprocess.run([exe, *args], capture_output=True, text=True,
                          encoding="utf-8", errors="replace", **kw)


# ---------------------------------------------------------------- 0 有 node 吗

if node("--version") is None:
    print("SKIP  这台机器上没有 node —— 跑不了页面 JS 的实跑检查")
    print("=" * 60)
    print("通过 0 / 失败 0")
    raise SystemExit(0)

# ---------------------------------------------------------------- 1 页面样本

kc1 = {"id": "T.1", "label": "细胞膜", "part": "P", "page": 1, "type": "concept",
       "importance": "key", "points": ["要点"], "self_test": ["问题？"], "deps": []}
ch = {"id": "测试讲", "label": "测试讲", "kcs": [kc1],
      "outline": {"title": "T", "objectives": ["目标"], "parts": [
          {"label": "P", "from": 1, "to": 20, "summary": "s"}]}}
plist = [{"n": 1, "t": "整页原文", "img": "../assets/s/p001.jpg"}]
survey = L.build_survey("测试课", ch, plist, html_name="T")
lesson = L.build_lesson("测试课", ch, "P", [kc1],
                        lambda k: ("../assets/s/p001.jpg", 1), mode="ask-first")

blocks = re.findall(r"<script>(.*?)</script>", survey, re.S)
check("survey 页里有多个 <script> 块", len(blocks) >= 2, str(len(blocks)))

# ---------------------------------------------------------------- 2 语法：node --check

# 临时文件放**工作区内**（沙箱下系统 temp 目录可能不允许写）
tmp = os.path.join(PROJ, ".tmp-js")
shutil.rmtree(tmp, ignore_errors=True)
os.makedirs(tmp, exist_ok=True)
bad: list[str] = []
for i, body in enumerate(blocks):
    p = os.path.join(tmp, f"blk{i}.js")
    with open(p, "w", encoding="utf-8") as f:
        f.write(body)
    r = node("--check", p)
    if r.returncode != 0:
        bad.append(f"块{i}: {(r.stderr or '').strip().splitlines()[:2]}")
check("★ 每个 <script> 块都能过 node --check（语法错 = 整块不执行）",
      not bad, "; ".join(bad))

# 两种课型的脚本都要过一遍（ask-first 的 _JS 是另一段大户）
bad2: list[str] = []
for i, body in enumerate(re.findall(r"<script>(.*?)</script>", lesson, re.S)):
    p = os.path.join(tmp, f"les{i}.js")
    with open(p, "w", encoding="utf-8") as f:
        f.write(body)
    r = node("--check", p)
    if r.returncode != 0:
        bad2.append(f"块{i}: {(r.stderr or '').strip().splitlines()[:2]}")
check("  ask-first 课的脚本也一样（另一种课型）", not bad2, "; ".join(bad2))


# ---------------------------------------------------------------- 3 实跑：子对话

def grab(js: str, name: str) -> str:
    """按大括号配对，把 `function <name>(...) {...}` 整段抠出来。"""
    i = js.index("function " + name + "(")
    j = js.index("{", i)
    depth = 0
    for k in range(j, len(js)):
        if js[k] == "{":
            depth += 1
        elif js[k] == "}":
            depth -= 1
            if depth == 0:
                return js[i:k + 1]
    raise AssertionError("大括号没配对：" + name)


_svjs = next(b for b in blocks if "function threadCard" in b)
parts = [grab(_svjs, n) for n in ("esc", "threadCard", "toggleThread",
                                  "resumeThread", "rectOfTid")]
harness = """
// ---- 最小 DOM 桩：只够这几个函数用 ----
var __els = {};
function El(tag){
  this.tagName = String(tag || "div").toUpperCase(); this.children = [];
  this.dataset = {}; this.className = ""; this._html = ""; this._text = "";
  this.classList = { add: function(){}, remove: function(){}, toggle: function(){} };
}
Object.defineProperty(El.prototype, "innerHTML",
  { set: function(v){ this._html = String(v); }, get: function(){ return this._html; } });
Object.defineProperty(El.prototype, "textContent",
  { set: function(v){ this._text = String(v); this.children = []; },
    get: function(){ return this._text; } });
El.prototype.appendChild = function(c){ this.children.push(c); return c; };
El.prototype.focus = function(){ global.__focused = this; };
El.prototype.scrollIntoView = function(){};
global.window = global;
global.document = {
  body: { dataset: {} },
  createElement: function(t){ return new El(t); },
  getElementById: function(id){ return __els[id] || (__els[id] = new El("div")); }
};
function showTab(){ global.__tab = arguments[0]; }
function toast(m){ global.__toast = m; }

""" + "\n\n".join(parts) + """

// ---- 真调用 ----
var th = { tid: "m27-1_2_3_4", page: 27, n: 2,
           title: "脂筏<为什么>能当信号平台？", at: "2026-09-29 22:23",
           at_end: "2026-09-29 22:31", gave_answer: true,
           turns: [ { q: "脂筏为什么？", a: "你先说成分。" },
                    { q: "不知道", a: "那我们缩小一点。" } ] };
var card = threadCard(th, false);
var out = { cls: card.className, head: card.children[0].innerHTML,
            meta: card.children[1].textContent,
            bodyN: card.children[2].children.length,
            turn0: card.children[2].children[0].innerHTML,
            btn: card.children[2].children[card.children[2].children.length - 1]
                     .children.map(function(b){ return b.textContent; }) };
var card2 = threadCard(th, true);
out.clsOpen = card2.className;

global.__openTids = {};
toggleThread("m27-1_2_3_4");
out.toggle1 = !!__openTids["m27-1_2_3_4"];
toggleThread("m27-1_2_3_4");
out.toggle2 = !!__openTids["m27-1_2_3_4"];

global.__threads = [th];
resumeThread("m27-1_2_3_4");
var flow = __els["ladderflow"];
out.flowKids = flow.children.length;
out.flowTid = flow.dataset.tid;
out.curTid = __curTid;
out.resumeTid = __resumeTid;
out.tab = global.__tab;
out.firstKid = flow.children[0].textContent;
out.lastKid = flow.children[flow.children.length - 1].textContent;

// 框坐标藏在对话编号里（「回到这一页」靠它跳到那个框）
out.rectBox = rectOfTid("m27-12_34_56_78");
out.rectFull = rectOfTid("m40-0_0_100_100");
out.rectLegacy = rectOfTid("@p27");
out.rectJunk = rectOfTid("t27-abc");

console.log(JSON.stringify(out));
"""
hp = os.path.join(tmp, "harness.js")
with open(hp, "w", encoding="utf-8") as f:
    f.write(harness)
r = node(hp)
if r.returncode != 0:
    check("★ 子对话的三个函数能真跑起来（不报 ReferenceError）", False,
          (r.stderr or "").strip()[:300])
    got = {}
else:
    check("★ 子对话的三个函数能真跑起来（不报 ReferenceError）", True)
    got = json.loads(r.stdout.strip().splitlines()[-1])

if got:
    check("★ 卡片：收起 / 展开两个状态（class 上有 on）",
          got.get("cls") == "thread" and got.get("clsOpen") == "thread on",
          f"{got.get('cls')} / {got.get('clsOpen')}")
    check("★ 标题里带页码和第一问",
          "第 27 页" in (got.get("head") or "")
          and "脂筏" in (got.get("head") or ""), str(got.get("head"))[:120])
    # 这一条是**上次事故的同类**：另一个 <script> 块里的 esc 一旦取不到，
    # 这里就会是 ReferenceError / 原样吐出尖括号（等于把用户的问题当 HTML 执行）
    check("★ 标题里的尖括号被转义（不是把用户的话当 HTML 执行）",
          "&lt;为什么&gt;" in (got.get("head") or "")
          and "<为什么>" not in (got.get("head") or ""),
          str(got.get("head"))[:160])
    check("★ 元信息带轮数、末次时间和「给了完整讲解」",
          "2 轮" in (got.get("meta") or "") and "22:31" in (got.get("meta") or "")
          and "完整讲解" in (got.get("meta") or ""), str(got.get("meta")))
    check("★ 展开后有 2 轮来回 = 2×2 个气泡 + 1 条按钮栏",
          got.get("bodyN") == 5, str(got.get("bodyN")))
    check("  气泡里带「我问」标注",
          "我问" in (got.get("turn0") or ""), str(got.get("turn0"))[:80])
    check("★ 卡片上有「回到这一页」「接着问」「删这段」三个按钮",
          got.get("btn") == ["回到这一页", "接着问", "删这段"], str(got.get("btn")))
    check("★ 展开/收起是翻转（点一次开、再点一次关）",
          got.get("toggle1") is True and got.get("toggle2") is False,
          f"{got.get('toggle1')} → {got.get('toggle2')}")
    check("★ 「接着问」把老对话摆回面板（1 行说明 + 2 轮来回 = 5 个气泡）",
          got.get("flowKids") == 5, str(got.get("flowKids")))
    check("  「接着问」之后这一段就成了当前对话（下一句接在这一段上）",
          got.get("curTid") == "m27-1_2_3_4" and got.get("resumeTid") == "m27-1_2_3_4",
          f"{got.get('curTid')} / {got.get('resumeTid')}")
    check("  面板切到「问 AI」那一页、光标进输入框",
          got.get("tab") == "ai" and "账本读回来" in (got.get("firstKid") or ""),
          f"{got.get('tab')} / {str(got.get('firstKid'))[:60]}")
    check("  面板里最后一句是那段对话的末尾（不是空面板）",
          "缩小一点" in (got.get("lastKid") or ""), str(got.get("lastKid"))[:80])
    # ★ 真跑一遍：对话编号 → 框坐标（「回到这一页」靠它闪那个框）
    check("★★ 对话编号能还原成框坐标（`m<页>-<x>_<y>_<宽>_<高>`）",
          got.get("rectBox") == [12, 34, 56, 78]
          and got.get("rectFull") == [0, 0, 100, 100],
          f"{got.get('rectBox')} / {got.get('rectFull')}")
    check("  老对话编号（`@p27`）与杂号解析不出来 → 退回只跳页，不炸",
          got.get("rectLegacy") is None and got.get("rectJunk") is None,
          f"{got.get('rectLegacy')} / {got.get('rectJunk')}")

shutil.rmtree(tmp, ignore_errors=True)

# ---------------------------------------------------------------- 汇总

for p in PASSES:
    print("PASS  " + p)
for f_ in FAILS:
    print("FAIL  " + f_)
print("=" * 60)
print(f"通过 {len(PASSES)} / 失败 {len(FAILS)}")
raise SystemExit(1 if FAILS else 0)
