# -*- coding: utf-8 -*-
"""HTML 课程生成器 —— 把一节知识点变成**可交互的网页**，而不是要读的 Markdown。

## 为什么不沿用 Markdown 笔记（真实事故）

用户库里有 122 篇知识点 Markdown 笔记、267 个手写批注位，
**一个字都没写过、一篇都没读过**。同期他在逐页精读器里框选提问 12 次。

结论不是"他不想学"，是**形态错了**：
Markdown 把答案直接摊开 → 你读完就以为自己会了（评论区那句「虚假的自信」）；
网页可以**先问你、等你答、再揭晓**，这才是检索练习（retrieval practice）。

## 一节的组织（用户确认：以问题为第一等公民）

    先问 → 你答 → 想看再看展开（要点 + 原片那一页）→ 对照 → 自评「会/不会」

自评不是走过场：它是**唯一能自动产出的学习记录**。
用户的"今日学习清单"被砍掉了，根因就是系统没有任何"你学过什么、掌握到哪"的数据；
自评把这份数据补上，而且不需要他手工打卡。

## 设计约束

- **单文件、离线、双击即开**：不引 CDN、不依赖 Obsidian 插件、不要求起服务。
- **幂等**：同样的输入生成字节一致的 HTML。
- **不碰用户的库**：只写 `<课程>/lessons/`，那是程序独占目录（和 notes/ 同级）。
"""
from __future__ import annotations

import html
import os
import re

from ledger import atomic_write_text

#: 一节课最多放几个知识点（太多就不是一课了）
MAX_KCS = 6

#: 自评的两种状态
GRADED_OK = "ok"
GRADED_NO = "no"


# ---------------------------------------------------------------- 工具

def esc(s: object) -> str:
    return html.escape(str(s if s is not None else ""), quote=True)


def safe_filename(name: str) -> str:
    """把部分名变成安全文件名（去掉 Windows 非法字符）。"""
    s = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "-", str(name or "").strip())
    s = re.sub(r"\s+", " ", s).strip(" .")
    return s or "lesson"


def topo_order(kcs: list[dict]) -> list[dict]:
    """按依赖顺序排（前置在前）。环或缺失一律退化为原顺序，不炸。

    为什么要有：一节课里的知识点必须**先讲地基**。
    直接按页序排会在"前置还没出现就先问它"的地方卡住。
    """
    by_id = {k["id"]: k for k in kcs}
    ids = [k["id"] for k in kcs]
    out: list[str] = []
    state: dict[str, int] = {}      # 0=访问中 1=已完成

    def visit(i: str) -> None:
        if state.get(i) == 1:
            return
        if state.get(i) == 0:       # 环：放弃排序，直接收
            return
        state[i] = 0
        for d in by_id[i].get("deps") or []:
            if d in by_id:
                visit(d)
        state[i] = 1
        out.append(i)

    for i in ids:
        visit(i)
    return [by_id[i] for i in out if i in by_id]


def kcs_of_part(chapter: dict, part: str) -> list[dict]:
    """取某个「部分」下的知识点，按依赖排序，**超过一节课就切成多节**。

    ★ 回归（真实事故）：早先这里直接按上限截断 —— 生物 66 个知识点、5 个部分，
    结果只生成了 30 个，**36 个被静默丢掉**（物理也丢了 8 个）。
    产出少了一半点都不报错，这种"看起来正常的丢数据"最危险。
    正确做法：一节装不下就拆成 1/2、2/2，一个都不许少（见 split_lessons）。
    """
    picked = [k for k in chapter.get("kcs", []) if (k.get("part") or "") == part]
    if not picked:
        return []
    return topo_order(picked)


def split_lessons(ordered: list[dict], limit: int = MAX_KCS) -> list[list[dict]]:
    """把一串（已排好序的）知识点切成若干节，保证**一个都不丢**。"""
    if not ordered:
        return []
    n = max(1, limit)
    return [ordered[i:i + n] for i in range(0, len(ordered), n)]


def lesson_names(part: str, n_chunks: int) -> list[str]:
    """一节时用原名；拆成多节时标 1/2、2/2（文件名与标题都用它）。"""
    if n_chunks <= 1:
        return [part]
    return [f"{part}（{i}/{n_chunks}）" for i in range(1, n_chunks + 1)]


# ---------------------------------------------------------------- 渲染

_CSS = """
:root{
  --bg:#fbfaf7; --fg:#1f2328; --dim:#6b7280; --line:#e5e3dd;
  --card:#fff; --acc:#2f6f4e; --acc2:#eef5f0; --warn:#b45309; --ok:#15803d;
  --shadow:0 1px 2px rgba(0,0,0,.05),0 8px 24px -12px rgba(0,0,0,.18);
}
@media (prefers-color-scheme:dark){
  :root{ --bg:#16181c; --fg:#e8e6e3; --dim:#9aa0a6; --line:#2c3036;
         --card:#1e2126; --acc:#7fc9a0; --acc2:#1d2a23; --shadow:none; }
}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--fg);
  font:16px/1.75 -apple-system,"Segoe UI","PingFang SC","Microsoft YaHei",sans-serif;}
.wrap{max-width:820px;margin:0 auto;padding:32px 20px 96px}
a{color:var(--acc)}
header.top{border-bottom:1px solid var(--line);padding-bottom:20px;margin-bottom:28px}
.crumb{font-size:13px;color:var(--dim);letter-spacing:.02em}
h1{font-size:27px;line-height:1.35;margin:8px 0 12px}
.goal{background:var(--acc2);border-left:3px solid var(--acc);border-radius:0 8px 8px 0;
  padding:12px 16px;font-size:15px;margin:14px 0}
.goal b{color:var(--acc)}
.bar{height:6px;background:var(--line);border-radius:99px;overflow:hidden;margin-top:16px}
.bar>i{display:block;height:100%;width:0;background:var(--acc);transition:width .3s}
.stat{font-size:13px;color:var(--dim);margin-top:8px;display:flex;justify-content:space-between}

section.kc{background:var(--card);border:1px solid var(--line);border-radius:14px;
  padding:22px 24px;margin:22px 0;box-shadow:var(--shadow)}
.kcno{font-size:12px;color:var(--dim);letter-spacing:.08em;text-transform:uppercase}
section.kc h2{font-size:20px;margin:6px 0 16px;line-height:1.4}
.tag{display:inline-block;font-size:12px;padding:1px 9px;border-radius:99px;
  background:var(--acc2);color:var(--acc);margin-left:8px;vertical-align:2px}
.tag.hub{background:#fff4e5;color:var(--warn)}
@media (prefers-color-scheme:dark){.tag.hub{background:#33260f}}

.q{border:1px solid var(--line);border-radius:10px;padding:14px 16px;margin:14px 0;
  background:var(--bg)}
.q .ask{font-weight:600;margin-bottom:10px}
.q .ask::before{content:"❓ ";color:var(--acc)}
textarea{width:100%;min-height:66px;padding:10px 12px;border:1px solid var(--line);
  border-radius:8px;background:var(--card);color:var(--fg);font:inherit;font-size:15px;
  resize:vertical}
textarea:focus{outline:2px solid var(--acc);outline-offset:1px;border-color:transparent}
.row{display:flex;gap:10px;align-items:center;flex-wrap:wrap;margin-top:10px}
button{font:inherit;font-size:14px;padding:7px 15px;border-radius:8px;cursor:pointer;
  border:1px solid var(--line);background:var(--card);color:var(--fg)}
button:hover{border-color:var(--acc);color:var(--acc)}
button.primary{background:var(--acc);border-color:var(--acc);color:#fff}
button.primary:hover{opacity:.9;color:#fff}
button.on-ok{background:var(--ok);border-color:var(--ok);color:#fff}
button.on-no{background:var(--warn);border-color:var(--warn);color:#fff}
.hint{font-size:13px;color:var(--dim)}
.answer{display:none;margin-top:12px;padding:12px 14px;border-left:3px solid var(--acc);
  background:var(--acc2);border-radius:0 8px 8px 0;font-size:15px}
.answer.show{display:block}
.answer .lb{font-size:12px;color:var(--dim);letter-spacing:.06em;text-transform:uppercase}
.answer ol{margin:8px 0 0;padding-left:22px}
.answer li{margin:5px 0}
.graded{font-size:13px;margin-left:auto}

figure{margin:18px 0}
figure img{width:100%;border:1px solid var(--line);border-radius:10px;cursor:zoom-in;
  display:block;background:#fff}
figcaption{font-size:13px;color:var(--dim);margin-top:6px;text-align:center}
ul.points{margin:14px 0 0;padding-left:20px}
ul.points li{margin:7px 0}

details.expand{margin-top:14px;border-top:1px dashed var(--line);padding-top:12px}
details.expand>summary{cursor:pointer;font-size:14px;color:var(--acc);list-style:none}
details.expand>summary::-webkit-details-marker{display:none}
details.expand>summary::before{content:"▸ "}
details.expand[open]>summary::before{content:"▾ "}

section.askbox{background:var(--card);border:1px solid var(--line);border-radius:14px;
  padding:22px 24px;margin:26px 0;box-shadow:var(--shadow)}
section.askbox h2{font-size:18px;margin:0 0 6px}
.mine{margin-top:14px}
.mine .item{border-left:3px solid var(--acc);padding:6px 0 6px 12px;margin:8px 0;
  font-size:14px;white-space:pre-wrap}
footer{color:var(--dim);font-size:13px;text-align:center;margin-top:40px;
  border-top:1px solid var(--line);padding-top:18px}

#lb{position:fixed;inset:0;background:rgba(0,0,0,.88);display:none;z-index:99;
  cursor:zoom-out;padding:24px}
#lb.on{display:flex;align-items:center;justify-content:center}
#lb img{max-width:100%;max-height:100%;border-radius:8px}
"""

_JS = """
(function(){
  var KEY = "c2md:" + document.body.dataset.lesson;
  var st = {};
  try { st = JSON.parse(localStorage.getItem(KEY) || "{}") || {}; } catch(e) { st = {}; }
  function save(){
    try { localStorage.setItem(KEY, JSON.stringify(st)); } catch(e) {}
  }

  var total = document.querySelectorAll(".q").length;
  var bar = document.querySelector(".bar > i");
  var txt = document.querySelector(".stat .done");

  function refresh(){
    var n = 0;
    document.querySelectorAll(".q").forEach(function(q){
      var g = st[q.dataset.q];
      if (g) n++;
      q.querySelectorAll("button[data-grade]").forEach(function(b){
        b.classList.toggle(b.dataset.grade === "ok" ? "on-ok" : "on-no",
                           g === b.dataset.grade);
      });
      var lab = q.querySelector(".graded");
      if (lab) lab.textContent = g === "ok" ? "✅ 已标记：会"
                              : g === "no" ? "❌ 已标记：还不会" : "";
    });
    if (bar) bar.style.width = (total ? Math.round(n * 100 / total) : 0) + "%";
    if (txt) txt.textContent = n + " / " + total + " 题已自评";
    renderMine();
  }

  document.addEventListener("click", function(e){
    var t = e.target;
    if (t.dataset && t.dataset.reveal !== undefined){
      var a = t.closest(".q").querySelector(".answer");
      a.classList.toggle("show");
      t.textContent = a.classList.contains("show") ? "收起答案" : "看答案";
      return;
    }
    if (t.dataset && t.dataset.grade){
      var q = t.closest(".q");
      st[q.dataset.q] = (st[q.dataset.q] === t.dataset.grade) ? null : t.dataset.grade;
      if (!st[q.dataset.q]) delete st[q.dataset.q];
      save(); refresh();
      return;
    }
    if (t.id === "addask"){
      var box = document.querySelector("#asktext");
      var v = (box.value || "").trim();
      if (!v) { box.focus(); return; }
      var list = st.__asks || (st.__asks = []);
      list.push({ q: v, t: new Date().toISOString().slice(0,16).replace("T"," ") });
      box.value = ""; save(); renderMine();
      return;
    }
    if (t.id === "copyask"){
      var list = st.__asks || [];
      var text = list.map(function(x){ return "- " + x.q; }).join("\\n");
      if (!text) return;
      navigator.clipboard && navigator.clipboard.writeText(text);
      t.textContent = "已复制"; setTimeout(function(){ t.textContent = "复制全部"; }, 1200);
      return;
    }
    if (t.tagName === "IMG" && t.closest("figure")){
      var lb = document.querySelector("#lb");
      lb.querySelector("img").src = t.src; lb.classList.add("on");
      return;
    }
    if (t.id === "lb" || t.closest("#lb")){
      document.querySelector("#lb").classList.remove("on");
    }
  });

  function renderMine(){
    var box = document.querySelector("#minelist");
    if (!box) return;
    var list = st.__asks || [];
    box.innerHTML = "";
    list.forEach(function(x){
      var d = document.createElement("div");
      d.className = "item"; d.textContent = "· " + x.q;
      box.appendChild(d);
    });
    var c = document.querySelector("#askcount");
    if (c) c.textContent = list.length ? ("已记下 " + list.length + " 条") : "";
  }

  document.addEventListener("keydown", function(e){
    if (e.key === "Escape") document.querySelector("#lb").classList.remove("on");
  });

  refresh();
})();
"""


def _answer_block(k: dict) -> str:
    pts = k.get("points") or []
    if not pts:
        return ""
    lis = "".join(f"<li>{esc(p)}</li>" for p in pts)
    return (f'<div class="answer"><div class="lb">参考答案</div>'
            f"<ol>{lis}</ol></div>")


def _question(qtext: str, qid: str, answer_html: str) -> str:
    """一道题 = 先答 → 揭晓 → 自评。答案默认藏起来（这是检索练习的关键）。"""
    return f"""<div class="q" data-q="{esc(qid)}">
  <div class="ask">{esc(qtext)}</div>
  <textarea placeholder="先自己答一遍（哪怕只写关键词）—— 直接看答案等于没学"></textarea>
  <div class="row">
    <button data-reveal>看答案</button>
    <span class="hint">答完再点。想不起来也算正常，那正是你该复习的地方。</span>
  </div>
  {answer_html}
  <div class="row">
    <span class="hint">刚才那题：</span>
    <button data-grade="ok">✅ 我答对了</button>
    <button data-grade="no">❌ 我答不上来</button>
    <span class="graded"></span>
  </div>
</div>"""


def _kc_section(i: int, k: dict, img_rel: str | None, page: int) -> str:
    label = esc(k.get("label"))
    tags = []
    imp = {"must": "必须掌握", "key": "重点", "freq": "常考"}.get(k.get("importance") or "")
    if imp:
        tags.append(f'<span class="tag">{esc(imp)}</span>')
    if k.get("is_hub"):
        tags.append('<span class="tag hub">枢纽</span>')

    qs = list(k.get("self_test") or [])
    ans = _answer_block(k)
    qhtml = "".join(_question(q, f"{k['id']}#{n}", ans) for n, q in enumerate(qs))
    if not qhtml:
        qhtml = ('<div class="hint">这一条暂时没有自测题 —— 你自己试着说出它的定义，'
                 '再展开对答案。</div>')

    fig = ""
    if img_rel:
        fig = (f'<figure><img src="{esc(img_rel)}" alt="第 {page} 页" loading="lazy">'
               f'<figcaption>课件第 {page} 页 · 点图放大 · 答案以这一页为准</figcaption></figure>')

    return f"""<section class="kc" id="kc{i}">
  <div class="kcno">第 {i} 个知识点 · {esc(k.get('type') or '')}</div>
  <h2>{label}{''.join(tags)}</h2>
  {qhtml}
  <details class="expand">
    <summary>展开：课件原页 + 要点（想不出来的时候再看）</summary>
    {fig}
    <ul class="points">{''.join(f'<li>{esc(p)}</li>' for p in (k.get('points') or []))}</ul>
  </details>
</section>"""


def build_lesson(course_label: str, chapter: dict, part: str,
                 kc_list: list[dict], img_rel_of) -> str:
    """生成一节 HTML 课。`img_rel_of(kc) -> (相对路径 or None, 页号)`。"""
    outline = chapter.get("outline") or {}
    title = f"{part}"
    goals = outline.get("objectives") or []
    n_q = sum(len(k.get("self_test") or []) for k in kc_list)
    lesson_id = f"{course_label}:{chapter.get('id')}:{part}"

    goal_html = ""
    if goals:
        goal_html = ('<div class="goal"><b>这一讲课件写明要你会：</b>'
                     + esc("；".join(goals)) + "</div>")

    secs = []
    for i, k in enumerate(kc_list, 1):
        rel, page = img_rel_of(k)
        secs.append(_kc_section(i, k, rel, page))

    # 收尾：把这一节的问题再列一遍（问题为第一等公民）
    all_q = [(k.get("label"), q) for k in kc_list for q in (k.get("self_test") or [])]
    checklist = "".join(
        f'<li><b>{esc(lb)}</b>：{esc(q)}</li>' for lb, q in all_q)

    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{esc(title)} · {esc(course_label)}</title>
<style>{_CSS}</style>
</head>
<body data-lesson="{esc(lesson_id)}">
<div class="wrap">

<header class="top">
  <div class="crumb">{esc(course_label)} · {esc(outline.get('title') or chapter.get('label') or '')}</div>
  <h1>{esc(title)}</h1>
  <div class="hint">
    {len(kc_list)} 个知识点 · {n_q} 道题。
    <b>顺序是先问后看</b> —— 先自己答，答完再揭晓；答不上来的地方才是你真正要学的地方。
  </div>
  {goal_html}
  <div class="bar"><i></i></div>
  <div class="stat"><span class="done">0 / {n_q} 题已自评</span>
    <span>进度只存在本机浏览器里</span></div>
</header>

{''.join(secs)}

<section class="askbox">
  <h2>还是要问？</h2>
  <div class="hint">这一节里没讲清楚、或者你想深挖的地方，写在这里。
  它会存在本机；连上网后可以让程序把它并进你的账本（和你在逐页精读器里的框选追问同一套）。</div>
  <textarea id="asktext" placeholder="例如：脂筏既然是动态的，那它算不算一种细胞器？"></textarea>
  <div class="row">
    <button id="addask" class="primary">记下这个问题</button>
    <button id="copyask">复制全部</button>
    <span class="hint" id="askcount"></span>
  </div>
  <div class="mine" id="minelist"></div>
</section>

<section class="kc">
  <div class="kcno">收尾</div>
  <h2>这一节学完，你应该能答出这 {len(all_q)} 个问题</h2>
  <ul class="points">{checklist}</ul>
  <div class="hint" style="margin-top:12px">
    任何一条答不上来，就回到上面那一条重做一遍 —— 不用重读整节。
  </div>
</section>

<footer>
  由 course-pipeline 生成 · 这一页完全离线，双击即可打开<br>
  内容来自课件原文与程序提取，<b>以课件原页为准</b>
</footer>

</div>
<div id="lb"><img alt=""></div>
<script>{_JS}</script>
</body>
</html>
"""


def write_lesson(course_root: str, filename: str, html_text: str) -> str:
    """写到 `<课程>/lessons/<filename>.html`，返回路径。"""
    d = os.path.join(course_root, "lessons")
    os.makedirs(d, exist_ok=True)
    p = os.path.join(d, filename + ".html")
    atomic_write_text(p, html_text)
    return p
