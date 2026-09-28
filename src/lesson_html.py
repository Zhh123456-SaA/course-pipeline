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

#: 课型（对应用户的两条管道 + 一个前置的"过课件"）
#: - MODE_SURVEY（过课件）：**先看原件**，划线 + 提问，不出题、不给答案
#: - MODE_ASK  （记忆型，如生物）：先问 → 你答 → 看答案   （检索练习）
#: - MODE_TEACH（数理，如物理）：先教 → 你记 → 考你 → 你手写答（有过程）
MODE_SURVEY = "survey"
MODE_ASK = "ask-first"
MODE_TEACH = "teach-first"
MODES = (MODE_SURVEY, MODE_ASK, MODE_TEACH)


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
button.on-mid{background:#6b7280;border-color:#6b7280;color:#fff}

/* 选中答案里的词 → 浮出「问这个」。
   ★ 用户真实的行为是「框选一段 + 提问」（他在逐页精读器里就这么干了 12 次），
   而不是对着空文本框打字。所以提问入口必须长在**答案的文字上**。 */
#askpop{position:absolute;display:none;z-index:60}
#askpop.on{display:block}
#askpop button{background:var(--acc);color:#fff;border-color:var(--acc);
  box-shadow:0 4px 14px rgba(0,0,0,.25);font-size:13px;padding:6px 12px;max-width:320px;
  overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.answer::selection,.answer *::selection,ul.points li::selection{background:var(--acc);
  color:#fff}
.selchip{display:inline-block;background:var(--acc2);color:var(--acc);border-radius:6px;
  padding:1px 7px;font-size:13px;margin-right:6px}

/* ---- 数理管道：先教后考 + 手写作答 ----------------------------------------
   用户原话：「你教授我——我记笔记——你出题考我——我用平板做答，有过程」。
   所以数理知识点的讲解**默认展开**（先教），作答区是**贴手写**而不是打字。 */
section.kc.teach h2{margin-bottom:8px}
.lesson-note{font-size:14px;color:var(--dim);margin:6px 0 14px}
.teachbox{border:1px solid var(--line);border-radius:10px;padding:14px 16px;
  margin:14px 0;background:var(--bg)}
.teachbox .lb{font-size:12px;color:var(--dim);letter-spacing:.06em;margin-bottom:8px}
.drop{border:2px dashed var(--line);border-radius:10px;padding:16px;text-align:center;
  color:var(--dim);font-size:14px;cursor:pointer;background:var(--bg);margin-top:10px}
.drop:hover,.drop.over{border-color:var(--acc);color:var(--acc)}
.drop input{display:none}
.shot{margin-top:12px;position:relative}
.shot img{max-width:100%;border:1px solid var(--line);border-radius:8px;cursor:zoom-in;
  display:block;background:#fff}
.shot .del{position:absolute;top:6px;right:6px;font-size:12px;padding:3px 9px;
  background:rgba(0,0,0,.6);color:#fff;border:0;border-radius:6px;cursor:pointer}
.shot .del:hover{background:rgba(180,0,0,.85);color:#fff}
.shotnote{width:100%;margin-top:6px;padding:7px 10px;border:1px solid var(--line);
  border-radius:8px;background:var(--card);color:var(--fg);font:inherit;font-size:14px}
.shotnote:focus{outline:2px solid var(--acc);outline-offset:1px;border-color:transparent}

/* 你在过课件时自己划过的线 —— 与程序判定的"必须掌握/枢纽"并列显示 */
.minebadge{background:#fff8e6;border:1px solid #f0d9a0;border-radius:8px;
  padding:7px 12px;font-size:13px;margin:10px 0;color:#8a6d1f}
@media (prefers-color-scheme:dark){.minebadge{background:#33290f;border-color:#5c4a1a;
  color:#e0c675}}
.minebadge b{color:#b45309}
.mineq{font-size:13px;color:var(--acc);margin:4px 0 0 14px}
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
  var GRADE_CLASS = { ok: "on-ok", mid: "on-mid", no: "on-no" };
  var GRADE_TEXT  = { ok: "✅ 已标记：会", mid: "🤔 已标记：不确定", no: "❌ 已标记：不会" };

  var total = document.querySelectorAll(".q").length;
  var bar = document.querySelector(".bar > i");
  var txt = document.querySelector(".stat .done");

  function refresh(){
    var n = 0;
    document.querySelectorAll(".q").forEach(function(q){
      var g = st[q.dataset.q];
      if (g) n++;
      q.querySelectorAll("button[data-grade]").forEach(function(b){
        b.classList.toggle(GRADE_CLASS[b.dataset.grade] || "on-no",
                           g === b.dataset.grade);
      });
      var lab = q.querySelector(".graded");
      if (lab) lab.textContent = GRADE_TEXT[g] || "";
    });
    if (bar) bar.style.width = (total ? Math.round(n * 100 / total) : 0) + "%";
    if (txt) txt.textContent = n + " / " + total + " 题已自评";
    renderShots();
    renderMine();
  }

  // ---- 选中答案里的词 → 提问 -------------------------------------------------
  // 用户真实的行为是「框选一段 + 提问」（他在逐页精读器里就这么干了 12 次），
  // 而不是对着空文本框打字。所以提问入口必须长在**答案的文字上**。
  var pop = document.createElement("div");
  pop.id = "askpop";
  pop.innerHTML = '<button type="button" id="askpopbtn"></button>';
  document.body.appendChild(pop);

  function hidePop(){ pop.classList.remove("on"); }
  function showPop(x, y, text, secId, kcLabel){
    var b = pop.querySelector("button");
    b.textContent = "问这个：「" + (text.length > 16 ? text.slice(0, 16) + "…" : text) + "」";
    pop.dataset.text = text; pop.dataset.sec = secId; pop.dataset.kc = kcLabel;
    pop.style.left = Math.max(8, Math.min(x, window.innerWidth - 260)) + "px";
    pop.style.top  = (y + window.scrollY + 10) + "px";
    pop.classList.add("on");
  }

  document.addEventListener("mouseup", function(e){
    if (e.target.closest && e.target.closest("#askpop")) return;
    setTimeout(function(){
      var sel = window.getSelection();
      var t = sel ? String(sel.toString() || "").replace(/\\s+/g, " ").trim() : "";
      if (!t || t.length < 2 || t.length > 300) { hidePop(); return; }
      var node = sel.anchorNode;
      var el = node && node.nodeType === 1 ? node : (node && node.parentElement);
      if (!el || !el.closest) { hidePop(); return; }
      // 只在「知识点区」里允许框选提问，避免在页头/追问区乱弹
      var sec = el.closest("section.kc");
      if (!sec || sec.id === "wrapup") { hidePop(); return; }
      var q = el.closest(".q");
      var h2 = sec.querySelector("h2");
      pop.dataset.qid = q ? (q.dataset.q || "") : "";
      showPop(e.clientX, e.clientY, t, sec.id, h2 ? h2.textContent.trim() : "");
    }, 10);
  });

  document.addEventListener("mousedown", function(e){
    if (!e.target.closest || !e.target.closest("#askpop")) hidePop();
  });

  // 点了「问这个」→ 把选中的词**带进追问框**（保留出处），光标定位好等用户补一句话。
  // 不是替他生成问题：他自己在精读器里也是「框选 + 自己打字」。
  function takeSelection(){
    var text = pop.dataset.text || "";
    var kc = pop.dataset.kc || "";
    if (!text) return;
    var box = document.querySelector("#asktext");
    var cur = (box.value || "").trim();
    var prefix = "关于「" + text + "」（" + kc + "）：";
    box.value = cur ? (cur + "\\n" + prefix) : prefix;
    hidePop();
    box.scrollIntoView({ behavior: "smooth", block: "center" });
    setTimeout(function(){
      box.focus();
      try { box.setSelectionRange(box.value.length, box.value.length); } catch(err) {}
    }, 260);
  }

  // ---- 导出：数据不能困在浏览器里 -------------------------------------------
  // 自评是「下一步学什么」的唯一数据来源（Q17 决定先攒数据），
  // 而 localStorage 出不去。给一个导出按钮，落到下载目录再由程序并回账本。
  function exportLog(){
    var lesson = document.body.dataset.lesson || "lesson";
    var out = { lesson: lesson, title: document.title, mode: document.body.dataset.mode || "",
                exported_at: new Date().toISOString(),
                grades: {}, answers: {}, asks: st.__asks || [] };
    document.querySelectorAll(".q").forEach(function(q){
      var id = q.dataset.q;
      if (st[id]) out.grades[id] = st[id];
      var ta = q.querySelector("textarea");
      var v = ta && ta.value ? ta.value.trim() : "";
      if (v) out.answers[id] = v;
    });
    // 解答图：一个列表，每张带可选标注（"这张是哪几题"）
    var sp = shots();
    if (sp.length) out.shots = sp;
    var blob = new Blob([JSON.stringify(out, null, 1)], { type: "application/json" });
    var a = document.createElement("a");
    a.href = URL.createObjectURL(blob);
    a.download = "study-" + lesson.replace(/[^0-9A-Za-z\\u4e00-\\u9fff]+/g, "_") + ".json";
    document.body.appendChild(a); a.click(); document.body.removeChild(a);
    setTimeout(function(){ URL.revokeObjectURL(a.href); }, 2000);
    var b = document.querySelector("#expbtn");
    if (b) { b.textContent = "已导出（在你浏览器下载目录）";
             setTimeout(function(){ b.textContent = "导出我的作答"; }, 2000); }
  }

  document.addEventListener("click", function(e){
    var t = e.target;
    if (!t.dataset) { t = t.parentElement || t; }
    if (t.id === "askpopbtn" || (t.closest && t.closest("#askpop"))){
      takeSelection(); return;
    }
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
      if (navigator.clipboard) navigator.clipboard.writeText(text);
      t.textContent = "已复制"; setTimeout(function(){ t.textContent = "复制全部"; }, 1200);
      return;
    }
    if (t.id === "expbtn" || t.id === "expbtn2"){ exportLog(); return; }
    if (t.dataset && t.dataset.delshot){
      var arr = shots();
      arr.splice(parseInt(t.dataset.delshot, 10), 1);
      save(); renderShots();
      return;
    }
    if (t.closest && t.closest("[data-drop]")){
      var inp = t.closest("[data-drop]").querySelector("input[type=file]");
      if (inp) inp.click();
      return;
    }
    if (t.tagName === "IMG" && t.closest("figure")){
      var lb = document.querySelector("#lb");
      lb.querySelector("img").src = t.src; lb.classList.add("on");
      return;
    }
    if (t.id === "lb" || (t.closest && t.closest("#lb"))){
      document.querySelector("#lb").classList.remove("on");
    }
  });

  // ---- 手写解答贴图（数理管道）---------------------------------------------
  // 用户原话：「我用平板做答，**有过程**」。数学物理的解答打字打不出来，
  // 而且**过程本身就是要被看见的东西** —— 所以作答区是贴图，不是文本框。
  //
  // 图存在 localStorage（base64）。有 5MB 上限，所以先压到 ≤1400px / JPEG，
  // 并在接近上限时明确提示先导出，而不是悄悄丢数据。
  var SHOT_BUDGET = 4.2 * 1024 * 1024;

  function usedBytes(){
    var n = 0;
    (st.__shots || []).forEach(function(it){
      n += String(it.d || "").length + String(it.n || "").length;
    });
    return n;
  }

  function shrink(file, cb){
    var fr = new FileReader();
    fr.onload = function(){
      var im = new Image();
      im.onload = function(){
        var max = 1400, w = im.width, h = im.height;
        if (w > max || h > max){
          var s = Math.min(max / w, max / h);
          w = Math.round(w * s); h = Math.round(h * s);
        }
        var cv = document.createElement("canvas");
        cv.width = w; cv.height = h;
        cv.getContext("2d").drawImage(im, 0, 0, w, h);
        cb(cv.toDataURL("image/jpeg", 0.72));
      };
      im.onerror = function(){ cb(null); };
      im.src = fr.result;
    };
    fr.onerror = function(){ cb(null); };
    fr.readAsDataURL(file);
  }

  // ★ 全部解答**一次传完**（用户原话：「我希望所有问题我可以在最后一次上传，
  //   每个问题平板导出很烦很影响效率」）。所以不是每题一个上传框，
  //   而是页面末尾一个统一区，图存成**一个列表**，每张可选用文字标一下对应哪几题。
  function shots(){ return st.__shots || (st.__shots = []); }

  function addShot(dataUrl, note){
    if (!dataUrl) return;
    if (usedBytes() + dataUrl.length > SHOT_BUDGET){
      alert("本机存储快满了（浏览器 5MB 上限）。先点「导出我的作答」把已贴的图导出去，再继续。");
      return;
    }
    shots().push({ d: dataUrl, n: note || "" });
    save(); renderShots();
  }

  function renderShots(){
    var box = document.querySelector("#shotlist");
    if (!box) return;
    var list = shots();
    box.innerHTML = "";
    list.forEach(function(it, i){
      var wrap = document.createElement("div");
      wrap.className = "shot";
      var im = document.createElement("img");
      im.src = it.d; wrap.appendChild(im);
      var del = document.createElement("button");
      del.className = "del"; del.textContent = "删掉这张";
      del.dataset.delshot = String(i);
      wrap.appendChild(del);
      // 可选的标注：不强制对应，他在纸上自己会写题号
      var cap = document.createElement("input");
      cap.type = "text"; cap.className = "shotnote";
      cap.placeholder = "这张是哪几题？（可不填）";
      cap.value = it.n || "";
      cap.dataset.shotnote = String(i);
      wrap.appendChild(cap);
      box.appendChild(wrap);
    });
    var c = document.querySelector("#shotcount");
    if (c) c.textContent = list.length ? ("已贴 " + list.length + " 张") : "";
  }

  function handleFiles(files){
    var n = (files || []).length;
    Array.prototype.forEach.call(files || [], function(f){
      if (!f || !/^image\\//.test(f.type || "")) return;
      shrink(f, function(u){ addShot(u, ""); });   // 注意：不传题号，一次全收
    });
    var tip = document.querySelector("#shottip");
    if (tip && n) tip.textContent = "正在处理 " + n + " 张…";
  }

  // 拖进来 / 粘进来
  document.addEventListener("dragover", function(e){
    var d = e.target.closest && e.target.closest("[data-drop]");
    if (!d) return;
    e.preventDefault(); d.classList.add("over");
  });
  document.addEventListener("dragleave", function(e){
    var d = e.target.closest && e.target.closest("[data-drop]");
    if (d) d.classList.remove("over");
  });
  document.addEventListener("drop", function(e){
    var d = e.target.closest && e.target.closest("[data-drop]");
    if (!d) return;
    e.preventDefault(); d.classList.remove("over");
    handleFiles(e.dataTransfer && e.dataTransfer.files);
  });

  document.addEventListener("paste", function(e){
    var items = (e.clipboardData && e.clipboardData.items) || [];
    var files = [];
    for (var i = 0; i < items.length; i++){
      if (items[i].kind === "file"){
        var f = items[i].getAsFile(); if (f) files.push(f);
      }
    }
    if (!files.length) return;
    if (!document.querySelector("[data-drop]")) return;   // 记忆型课没有上传区
    e.preventDefault();
    handleFiles(files);
  });

  document.addEventListener("change", function(e){
    var inp = e.target;
    if (inp && inp.type === "file" && inp.closest && inp.closest("[data-drop]")){
      handleFiles(inp.files);
      inp.value = "";     // 允许再次选同一张
    }
    if (inp && inp.dataset && inp.dataset.shotnote !== undefined){
      var list = shots();
      var i = parseInt(inp.dataset.shotnote, 10);
      if (list[i]) { list[i].n = inp.value; save(); }
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
    if (e.key === "Escape"){ hidePop();
      document.querySelector("#lb").classList.remove("on"); }
  });

  refresh();
})();
"""


# ---------------------------------------------------------------- 过课件（MODE_SURVEY）
#
# 用户原话：「我还想集成原本那个看课件问问题 —— 我希望我可以**先看课件，划线记笔记
# 问问题**，之后经历现在的流程」。
#
# 为什么这一步重要（有行为数据支撑）：
#   他真实做的只有两件事 —— 框选提问 12 次、平板手写 14/17 页；
#   而我产出的 122 篇 Markdown 笔记、267 个要打字的批注位，**他一个字都没用过**。
#   所以我一直在让他做他不做的事。"先看原件 + 划线 + 提问"才是他的真实动作。
#
# 关键技术决定：**课件页是图片，选不中文字** —— 所以划线只能是**在图上拖一个矩形**。
# 这恰好就是他框选提问的同一个动作，肌肉记忆直接复用：
#     拖一个框        = 标记「这里重要」（零成本，所以他真的会做）
#     框上再点 ❓     = 提问「这里我不懂」（要打字，所以会少很多）
# 两种信号都记下来 —— 划线数量远多于提问，是更密的信号。

_SURVEY_CSS = """
*{box-sizing:border-box}
body{margin:0;background:#16181c;color:#e8e6e3;
  font:16px/1.7 -apple-system,"Segoe UI","PingFang SC","Microsoft YaHei",sans-serif}
.bar{position:sticky;top:0;z-index:20;display:flex;gap:12px;align-items:center;
  padding:10px 18px;background:#1e2126;border-bottom:1px solid #2c3036;flex-wrap:wrap}
.bar b{font-variant-numeric:tabular-nums}
button{font:inherit;font-size:14px;padding:6px 14px;border-radius:8px;cursor:pointer;
  border:1px solid #2c3036;background:#262a30;color:#e8e6e3}
button:hover{border-color:#7fc9a0;color:#7fc9a0}
button.primary{background:#2f6f4e;border-color:#2f6f4e;color:#fff}
.hint{font-size:13px;color:#9aa0a6}
.stage{max-width:1100px;margin:22px auto;padding:0 18px}
.pg{position:relative;line-height:0;border-radius:10px;overflow:hidden;
  box-shadow:0 10px 40px -12px rgba(0,0,0,.6)}
.pg img{width:100%;display:block;background:#fff;user-select:none;-webkit-user-drag:none}
.layer{position:absolute;inset:0;cursor:crosshair}
.mk{position:absolute;border:2px solid #f5c451;background:rgba(245,196,81,.22);
  border-radius:3px;cursor:pointer}
.mk.q{border-color:#7fc9a0;background:rgba(127,201,160,.22)}
.mk .qbtn{position:absolute;right:-2px;bottom:-2px;transform:translateY(100%);
  font-size:12px;padding:2px 7px;background:#2f6f4e;border-color:#2f6f4e;color:#fff;
  border-radius:0 0 6px 6px;line-height:1.4}
.mk .del{position:absolute;left:-2px;bottom:-2px;transform:translateY(100%);
  font-size:12px;padding:2px 7px;background:#7f1d1d;border-color:#7f1d1d;color:#fff;
  border-radius:0 0 6px 6px;line-height:1.4}
.mk .qtext{position:absolute;left:0;top:100%;margin-top:20px;font-size:12px;
  background:#1e2126;border:1px solid #2c3036;border-radius:6px;padding:3px 8px;
  color:#7fc9a0;white-space:nowrap;max-width:420px;overflow:hidden;
  text-overflow:ellipsis;line-height:1.6}
#editor{position:fixed;z-index:40;display:none;background:#1e2126;border:1px solid #2c3036;
  border-radius:10px;padding:10px;box-shadow:0 12px 40px -10px rgba(0,0,0,.7);width:340px}
#editor.on{display:block}
#editor textarea{width:100%;min-height:64px;padding:8px 10px;border-radius:8px;
  border:1px solid #2c3036;background:#16181c;color:#e8e6e3;font:inherit;font-size:14px;
  resize:vertical}
#editor .row{display:flex;gap:8px;justify-content:flex-end;margin-top:8px}
.mkbox{max-width:1100px;margin:26px auto 80px;padding:0 18px}
.mkbox h2{font-size:17px;margin:0 0 10px}
.mkitem{display:flex;gap:10px;align-items:flex-start;font-size:14px;padding:7px 0;
  border-bottom:1px solid #2c3036}
.mkitem .p{color:#9aa0a6;white-space:nowrap;font-variant-numeric:tabular-nums}
.mkitem .q{color:#7fc9a0}
.mkitem a{color:#f5c451;cursor:pointer;text-decoration:none}
"""

_SURVEY_JS = """
(function(){
  var KEY = "c2md:" + document.body.dataset.lesson;
  var st = {};
  try { st = JSON.parse(localStorage.getItem(KEY) || "{}") || {}; } catch(e) { st = {}; }
  function save(){ try { localStorage.setItem(KEY, JSON.stringify(st)); } catch(e) {} }
  function marks(){ return st.__marks || (st.__marks = []); }

  var PAGES = window.__PAGES__ || [];
  var cur = 0;
  var img = document.getElementById("pimg");
  var layer = document.getElementById("layer");
  var pno = document.getElementById("pno");
  var drag = null, editorFor = -1;

  function clamp(v, a, b){ return Math.max(a, Math.min(b, v)); }

  function go(i){
    cur = clamp(i, 0, PAGES.length - 1);
    img.src = PAGES[cur].src;
    pno.textContent = (cur + 1) + " / " + PAGES.length;
    hideEditor();
    render();
    try { history.replaceState(null, "", "#p" + (cur + 1)); } catch(e) {}
    window.scrollTo({ top: 0, behavior: "smooth" });
  }

  function pageMarks(){
    var p = cur + 1;
    return marks().map(function(m, i){ return { m: m, i: i }; })
                  .filter(function(x){ return x.m.p === p; });
  }

  function render(){
    layer.innerHTML = "";
    pageMarks().forEach(function(x){
      var m = x.m, r = m.r;
      var d = document.createElement("div");
      d.className = "mk" + (m.q ? " q" : "");
      d.style.left = r[0] + "%"; d.style.top = r[1] + "%";
      d.style.width = r[2] + "%"; d.style.height = r[3] + "%";
      d.dataset.mi = String(x.i);
      var qb = document.createElement("button");
      qb.className = "qbtn"; qb.textContent = m.q ? "❓ 改问题" : "❓ 提问";
      qb.dataset.askmk = String(x.i);
      d.appendChild(qb);
      var db = document.createElement("button");
      db.className = "del"; db.textContent = "🗑";
      db.dataset.delmk = String(x.i);
      d.appendChild(db);
      if (m.q){
        var t = document.createElement("div");
        t.className = "qtext"; t.textContent = m.q;
        d.appendChild(t);
      }
      layer.appendChild(d);
    });
    renderList();
  }

  function renderList(){
    var box = document.getElementById("mklist");
    var all = marks();
    document.getElementById("mkcount").textContent =
      all.length ? ("已标记 " + all.length + " 处（其中提问 " +
                    all.filter(function(m){ return m.q; }).length + " 条）") : "";
    if (!all.length){ box.innerHTML = '<div class="hint">还没有标记。在课件上拖一个框试试。</div>'; return; }
    box.innerHTML = "";
    all.slice().sort(function(a, b){ return a.p - b.p; }).forEach(function(m){
      var row = document.createElement("div");
      row.className = "mkitem";
      var a = document.createElement("a");
      a.textContent = "第 " + m.p + " 页"; a.dataset.gopage = String(m.p);
      var p = document.createElement("span"); p.className = "p"; p.appendChild(a);
      var s = document.createElement("span");
      s.innerHTML = m.q ? ('<span class="q">❓ ' + esc(m.q) + "</span>")
                        : "⭐ 标记（未提问）";
      row.appendChild(p); row.appendChild(s);
      box.appendChild(row);
    });
  }

  function esc(s){
    return String(s).replace(/[&<>"]/g, function(c){
      return ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" })[c];
    });
  }

  function hideEditor(){ document.getElementById("editor").classList.remove("on"); editorFor = -1; }

  function showEditor(i, x, y){
    var m = marks()[i]; if (!m) return;
    editorFor = i;
    var ed = document.getElementById("editor");
    var ta = ed.querySelector("textarea");
    ta.value = m.q || "";
    ed.style.left = Math.min(x, window.innerWidth - 360) + "px";
    ed.style.top = Math.min(y, window.innerHeight - 200) + "px";
    ed.classList.add("on");
    ta.focus();
  }

  // ---- 拖框 ----
  layer.addEventListener("pointerdown", function(e){
    if (e.target !== layer) return;
    var b = layer.getBoundingClientRect();
    drag = { x0: (e.clientX - b.left) / b.width * 100,
             y0: (e.clientY - b.top) / b.height * 100, el: null };
    layer.setPointerCapture(e.pointerId);
  });
  layer.addEventListener("pointermove", function(e){
    if (!drag) return;
    var b = layer.getBoundingClientRect();
    var x = (e.clientX - b.left) / b.width * 100;
    var y = (e.clientY - b.top) / b.height * 100;
    if (!drag.el){
      drag.el = document.createElement("div");
      drag.el.className = "mk";
      layer.appendChild(drag.el);
    }
    var l = clamp(Math.min(drag.x0, x), 0, 100), t = clamp(Math.min(drag.y0, y), 0, 100);
    var w = clamp(Math.abs(x - drag.x0), 0, 100 - l), h = clamp(Math.abs(y - drag.y0), 0, 100 - t);
    drag.el.style.left = l + "%"; drag.el.style.top = t + "%";
    drag.el.style.width = w + "%"; drag.el.style.height = h + "%";
    drag.rect = [l, t, w, h];
  });
  layer.addEventListener("pointerup", function(e){
    if (!drag) return;
    var r = drag.rect;
    // 太小的框多半是误触，丢掉（课件页上真实的标记区总是有点面积）
    if (r && r[2] > 1.5 && r[3] > 1.5){
      marks().push({ p: cur + 1, r: r.map(function(v){ return Math.round(v * 10) / 10; }),
                     q: "", t: new Date().toISOString().slice(0, 16).replace("T", " ") });
      save();
    }
    drag = null; render();
  });

  document.addEventListener("click", function(e){
    var t = e.target;
    if (t.dataset && t.dataset.askmk !== undefined){
      var r = t.closest(".mk").getBoundingClientRect();
      showEditor(parseInt(t.dataset.askmk, 10), r.left, r.bottom + 8);
      return;
    }
    if (t.dataset && t.dataset.delmk !== undefined){
      marks().splice(parseInt(t.dataset.delmk, 10), 1);
      save(); render(); return;
    }
    if (t.dataset && t.dataset.gopage !== undefined){
      go(parseInt(t.dataset.gopage, 10) - 1); return;
    }
    if (t.id === "savemk"){ commitEditor(); return; }
    if (t.id === "cancelmk"){ hideEditor(); return; }
    if (t.id === "prev"){ go(cur - 1); return; }
    if (t.id === "next"){ go(cur + 1); return; }
    if (t.id === "expbtn"){ exportLog(); return; }
    if (t.id === "clrpage"){
      var p = cur + 1;
      st.__marks = marks().filter(function(m){ return m.p !== p; });
      save(); render(); return;
    }
  });

  function commitEditor(){
    if (editorFor < 0) return;
    var v = document.getElementById("editor").querySelector("textarea").value.trim();
    marks()[editorFor].q = v;
    save(); hideEditor(); render();
  }

  document.addEventListener("keydown", function(e){
    if (e.target.tagName === "TEXTAREA") {
      if (e.key === "Escape") hideEditor();
      if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) commitEditor();
      return;
    }
    if (e.key === "ArrowLeft") go(cur - 1);
    if (e.key === "ArrowRight") go(cur + 1);
  });

  function exportLog(){
    var lesson = document.body.dataset.lesson || "lesson";
    var out = { lesson: lesson, title: document.title, mode: "survey",
                exported_at: new Date().toISOString(), marks: marks() };
    var blob = new Blob([JSON.stringify(out, null, 1)], { type: "application/json" });
    var a = document.createElement("a");
    a.href = URL.createObjectURL(blob);
    a.download = "study-" + lesson.replace(/[^0-9A-Za-z\\u4e00-\\u9fff]+/g, "_") + ".json";
    document.body.appendChild(a); a.click(); document.body.removeChild(a);
    setTimeout(function(){ URL.revokeObjectURL(a.href); }, 2000);
    var b = document.getElementById("expbtn");
    if (b){ b.textContent = "已导出"; setTimeout(function(){ b.textContent = "导出我的标记与提问"; }, 1800); }
  }

  window.addEventListener("load", function(){
    var m = /^#p(\\d+)$/.exec(location.hash || "");
    go(m ? parseInt(m[1], 10) - 1 : 0);
  });
  if (document.readyState === "complete") {
    var m0 = /^#p(\\d+)$/.exec(location.hash || "");
    go(m0 ? parseInt(m0[1], 10) - 1 : 0);
  }
})();
"""


def build_survey(course_label: str, chapter: dict, pages: list[dict],
                 html_name: str = "") -> str:
    """生成「过课件」页：翻页看原件 + 拖框标记 + 框上提问。

    这一页**不出题、不给答案、不提炼** —— 它的唯一目的是让你产生两种信号：
    「这里重要」（划线，零成本）与「这里我不懂」（提问，要打字）。
    这两种信号随后会随导出的 JSON 进账本，并**支配**后面两步：
    知识点会标注"你在这一页标记过 N 处"，问题清单位居库的主入口。
    """
    import json as _json
    plist = [{"n": p["no"], "src": p["src"]} for p in pages if p.get("src")]
    if not plist:
        plist = [{"n": 0, "src": ""}]
    lesson_id = f"{course_label}:{chapter.get('id')}:{html_name or 'survey'}:survey"
    outline = chapter.get("outline") or {}
    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>过课件 · {esc(outline.get('title') or chapter.get('label') or course_label)}</title>
<style>{_SURVEY_CSS}</style>
</head>
<body data-lesson="{esc(lesson_id)}" data-mode="survey">

<div class="bar">
  <button id="prev">‹ 上一页</button>
  <b id="pno">1 / {len(plist)}</b>
  <button id="next">下一页 ›</button>
  <span class="hint">← → 翻页 · <b>在课件上拖一个框 = 标记</b> · 框上点 <b>❓</b> = 提问</span>
  <span class="hint" id="mkcount"></span>
  <button id="clrpage">清空本页标记</button>
  <button id="expbtn" class="primary">导出我的标记与提问</button>
</div>

<div class="stage">
  <div class="pg">
    <img id="pimg" alt="课件页">
    <div class="layer" id="layer"></div>
  </div>
  <div class="hint" style="margin-top:10px">
    这一页**不问你、不给答案** —— 就是把课件过一遍，看到重要的拖个框，不懂的点 ❓。
    全过完之后，导出去，程序会把你的标记和问题带进后面的知识点与课里。
  </div>
</div>

<div class="mkbox">
  <h2>我标记过的（点页码跳过去）</h2>
  <div id="mklist"></div>
</div>

<div id="editor">
  <textarea placeholder="这里你想问什么？（Ctrl+Enter 保存，Esc 取消）"></textarea>
  <div class="row">
    <button id="cancelmk">取消</button>
    <button id="savemk" class="primary">保存问题</button>
  </div>
</div>

<script>window.__PAGES__ = {_json.dumps(plist, ensure_ascii=False)};</script>
<script>{_SURVEY_JS}</script>
</body>
</html>
"""


def clean_orphans(lessons_dir: str, produced: set[str], mode: str) -> int:
    """删掉 `lessons/` 里**同一课型**的孤儿课文件，返回删除个数。

    ★ 回归（真实事故）：早先的逻辑是"本次没产出的一律删"。跑一次
    `--mode survey`（整讲只出 1 页）时，它**把 15 节 ask-first 课全删了**。

    多种课型是**并存互不覆盖**的设计（文件名与 localStorage 键都带课型），
    所以清理也必须按课型分开 —— 课型写在每个文件自己的 `data-mode` 上。
    只读文件头 4KB 判断，不去解析整个 HTML。
    """
    n = 0
    if not os.path.isdir(lessons_dir):
        return 0
    tag = f'data-mode="{mode}"'
    for fn in sorted(os.listdir(lessons_dir)):
        fp = os.path.join(lessons_dir, fn)
        if not fn.lower().endswith(".html"):
            continue
        if os.path.normcase(os.path.abspath(fp)) in produced:
            continue
        try:
            head = open(fp, encoding="utf-8", errors="replace").read(4000)
        except OSError:
            continue
        if tag in head:
            os.remove(fp)
            n += 1
    return n


def _answer_block(k: dict) -> str:
    pts = k.get("points") or []
    if not pts:
        return ""
    lis = "".join(f"<li>{esc(p)}</li>" for p in pts)
    return (f'<div class="answer"><div class="lb">参考答案</div>'
            f"<ol>{lis}</ol></div>")


def _question(qtext: str, qid: str, answer_html: str, mode: str = MODE_ASK) -> str:
    """一道题。

    两种形态（对应两条管道）：

    - `ask-first`（记忆型，如生物）：**先答 → 揭晓 → 自评**。
      检索练习：答案默认藏起来，逼你先回忆。
    - `teach-first`（数理）：讲解已经先展开了，这里是**出题考你**，
      作答区是**贴手写解答**而不是打字 ——
      用户原话：「我用平板做答，**有过程**」。
      数学物理的解答打字打不出来，而且过程本身就是要被看见的东西。

    「不知道/不确定」永不算错（抄 amosblomqvist/learn 的 correct|wrong|dont_know）。
    """
    if mode == MODE_TEACH:
        # 数理：作答**不在这里贴**。用户原话：「我希望所有问题我可以在最后一次上传，
        # 每个问题平板导出很烦很影响效率」——
        # 每题一个上传框 = 每做一题就得从平板导出一次，把学习切成碎步。
        # 所以贴图区移到页面末尾，**全部写完一次性传**（见 _upload_section）。
        answer_area = answer_html
    else:
        # ★ 这里必须带上 answer_html。第一版只放了 textarea，
        #   结果背记课的「看答案」点开是空的 —— 答案块整个没生成（实测抓到）。
        answer_area = ('<textarea placeholder="先自己答一遍（哪怕只写关键词）'
                       '—— 直接看答案等于没学"></textarea>\n  ' + answer_html)

    return f"""<div class="q" data-q="{esc(qid)}">
  <div class="ask">{esc(qtext)}</div>
  {answer_area}
  <div class="row">
    <button data-reveal>看答案</button>
    <span class="hint">答完再点。想不起来也算正常，那正是你该复习的地方。</span>
  </div>
  <div class="row">
    <span class="hint">刚才那题：</span>
    <button data-grade="ok">✅ 会</button>
    <button data-grade="mid">🤔 不确定</button>
    <button data-grade="no">❌ 不会</button>
    <span class="graded"></span>
  </div>
  <div class="hint" style="margin-top:6px">
    💡 <b>答案里看到不懂的词，直接用鼠标选中它</b> —— 会浮出一个「问这个」按钮。
  </div>
</div>"""


def _kc_section(i: int, k: dict, img_rel: str | None, page: int,
                mode: str = MODE_ASK, mk: dict | None = None) -> str:
    label = esc(k.get("label"))
    tags = []
    imp = {"must": "必须掌握", "key": "重点", "freq": "常考"}.get(k.get("importance") or "")
    if imp:
        tags.append(f'<span class="tag">{esc(imp)}</span>')
    if k.get("is_hub"):
        tags.append('<span class="tag hub">枢纽</span>')

    # ★ 第一遍的产出支配这一步：你在过课件时对这一页划过线 / 提过问，
    #   就显示在这里。让他一眼看到"这块是我自己觉得重要的"，
    #   而不是只有程序判定的"必须掌握/枢纽"。
    badge = ""
    if mk and mk.get("n"):
        q = mk.get("q") or 0
        badge = (f'<div class="minebadge">✍️ 你在第 {page} 页标记过 '
                 f'<b>{mk["n"]}</b> 处'
                 + (f'，其中提问 <b>{q}</b> 条' if q else "")
                 + "</div>")
        for m in (mk.get("marks") or []):
            if m.get("q"):
                badge += f'<div class="mineq">❓ {esc(m["q"])}</div>'

    qs = list(k.get("self_test") or [])
    ans = _answer_block(k)
    qhtml = "".join(_question(q, f"{k['id']}#{n}", ans, mode) for n, q in enumerate(qs))
    if not qhtml:
        qhtml = ('<div class="hint">这一条暂时没有自测题 —— 你自己试着说出它的定义，'
                 '再展开对答案。</div>')

    fig = ""
    if img_rel:
        # 说明里写"PDF 第 N 页"而不是"课件第 N 页"：课件自己印的页码常与 PDF
        # 物理页序差 1（封面无页码）。实测用户看到"课件第 19 页"而图上写着 18，
        # 会以为程序找错了页。
        fig = (f'<figure><img src="{esc(img_rel)}" alt="PDF 第 {page} 页" loading="lazy">'
               f'<figcaption>原课件 PDF 第 {page} 页 · 点图放大 · 答案以这一页为准'
               f'</figcaption></figure>')
    points = ''.join(f'<li>{esc(p)}</li>' for p in (k.get('points') or []))

    if mode == MODE_TEACH:
        # 数理：**先教**（讲解默认展开）→ 再考。用户原话「你教授我——我记笔记——
        # 你出题考我——我用平板做答」。所以不能像记忆型那样一上来就出题。
        body = f"""<div class="teachbox">
    <div class="lb">先看这一节要讲什么</div>
    {fig}
    <ul class="points">{points}</ul>
  </div>
  <div class="lesson-note">
    看完先别急着往下 —— <b>在平板上把这一页自己推一遍 / 记一遍</b>，再回来做题。
  </div>
  <div class="lb" style="font-size:12px;color:var(--dim);letter-spacing:.06em">
    下面考你（把解答过程写在平板上，再贴上来）</div>
  {qhtml}"""
    else:
        body = f"""{qhtml}
  <details class="expand">
    <summary>展开：课件原页 + 要点（想不出来的时候再看）</summary>
    {fig}
    <ul class="points">{points}</ul>
  </details>"""

    cls = "kc teach" if mode == MODE_TEACH else "kc"
    return f"""<section class="{cls}" id="kc{i}">
  <div class="kcno">第 {i} 个知识点 · {esc(k.get('type') or '')}</div>
  <h2>{label}{''.join(tags)}</h2>
  {badge}
  {body}
</section>"""


def _upload_section(n_kc: int, n_q: int) -> str:
    """数理课的**统一作答上传区** —— 放在页面末尾，全部写完一次传。

    ★ 用户原话：「我希望所有问题我可以在最后一次上传，**每个问题平板导出很烦
    很影响效率**」。所以不做"每题一个上传框"：从平板导出一次已经够烦，
    让他每做一题导出一次，等于把连续的学习切成碎步。

    每张图可以**可选**地标一下对应哪几题（不标也能用）—— 他在纸上自己会写题号，
    程序不强制建立对应关系（用户也明确说过"我觉得没必要对应"）。
    """
    return f"""<section class="askbox" id="mywork">
  <h2>✍️ 我的解答 —— 写完了再一次性传上来</h2>
  <div class="hint">
    这一节共 <b>{n_kc} 个知识点、{n_q} 道题</b>。建议：<b>在平板上按顺序把解答写完整</b>
    （写清题号、保留推导过程），全部写完后再回到这里，<b>一次性</b>把图传上来。
    不用一题一题传。
  </div>
  <div class="drop" data-drop>
    📎 <b>点这里选图</b>，或把图<b>拖进这个框</b>，也可以直接 <b>Ctrl+V</b> 粘贴<br>
    <span style="font-size:13px">可以一次选多张；分几次传也行，都会攒在这里。</span>
    <input type="file" accept="image/*" multiple>
  </div>
  <div class="hint" id="shottip" style="margin-top:8px"></div>
  <div class="shotlist" id="shotlist"></div>
  <div class="row">
    <span class="hint" id="shotcount"></span>
    <button id="expbtn2">导出我的作答</button>
  </div>
</section>"""


def build_lesson(course_label: str, chapter: dict, part: str,
                 kc_list: list[dict], img_rel_of, mode: str = MODE_ASK,
                 marks_by_page: dict | None = None) -> str:
    """生成一节 HTML 课。`img_rel_of(kc) -> (相对路径 or None, 页号)`。"""
    if mode not in MODES:
        mode = MODE_ASK
    outline = chapter.get("outline") or {}
    title = f"{part}"
    goals = outline.get("objectives") or []
    n_q = sum(len(k.get("self_test") or []) for k in kc_list)
    lesson_id = f"{course_label}:{chapter.get('id')}:{part}:{mode}"

    goal_html = ""
    if goals:
        goal_html = ('<div class="goal"><b>这一讲课件写明要你会：</b>'
                     + esc("；".join(goals)) + "</div>")

    secs = []
    n_marked = 0
    for i, k in enumerate(kc_list, 1):
        rel, page = img_rel_of(k)
        mk = (marks_by_page or {}).get(int(page or 0))
        if mk:
            n_marked += 1
        secs.append(_kc_section(i, k, rel, page, mode, mk))

    # 你自己划过线的页 —— 放在最上面。第一遍的产出**支配**这一步，
    # 而不是被埋在笔记第 40 行（这正是用户说"先看课件"时真正想要的东西）。
    mine_html = ""
    if marks_by_page:
        rows = []
        for k in kc_list:
            _rel, page = img_rel_of(k)
            mk = marks_by_page.get(int(page or 0))
            if mk:
                rows.append((page, k.get("label"), mk))
        if rows:
            mine_html = ('<div class="goal" style="border-left-color:#b45309">'
                         '<b>你自己划过的线（过课件时标记的）：</b>'
                         + "；".join(f'第 {p} 页 {esc(lb)}（{m["n"]} 处'
                                     + (f'，提问 {m["q"]} 条' if m.get("q") else "")
                                     + "）" for p, lb, m in rows)
                         + "</div>")

    # 收尾：把这一节的问题再列一遍（问题为第一等公民）
    all_q = [(k.get("label"), q) for k in kc_list for q in (k.get("self_test") or [])]
    checklist = "".join(
        f'<li><b>{esc(lb)}</b>：{esc(q)}</li>' for lb, q in all_q)

    if mode == MODE_TEACH:
        howto = (f"{len(kc_list)} 个知识点 · {n_q} 道题。<b>顺序是先教后考</b> —— "
                 f"先把每节的讲解看一遍、在平板上自己推一遍，再看下面的题，"
                 f"把解答过程写在平板上贴上来。")
    else:
        howto = (f"{len(kc_list)} 个知识点 · {n_q} 道题。"
                 f"<b>顺序是先问后看</b> —— 先自己答，答完再揭晓；"
                 f"答不上来的地方才是你真正要学的地方。")

    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{esc(title)} · {esc(course_label)}</title>
<style>{_CSS}</style>
</head>
<body data-lesson="{esc(lesson_id)}" data-mode="{esc(mode)}">
<div class="wrap">

<header class="top">
  <div class="crumb">{esc(course_label)} · {esc(outline.get('title') or chapter.get('label') or '')}</div>
  <h1>{esc(title)}</h1>
  <div class="hint">{howto}</div>
  {goal_html}
  {mine_html}
  <div class="bar"><i></i></div>
  <div class="stat"><span class="done">0 / {n_q} 题已自评</span>
    <span>进度只存在本机浏览器里</span></div>
</header>

{''.join(secs)}

{_upload_section(len(kc_list), n_q) if mode == MODE_TEACH else ""}

<section class="askbox">
  <h2>还是要问？</h2>
  <div class="hint">这一节里没讲清楚、或者你想深挖的地方，写在这里。
  <b>答案里看到不懂的词，直接用鼠标选中它</b>，会浮出一个「问这个」按钮，点了就把那个词带到这里来。
  它会存在本机；导出后可以让程序把它并进你的账本（和你在逐页精读器里的框选追问同一套）。</div>
  <textarea id="asktext" placeholder="例如：质点在圆弧内表面下滑时，法向方程里的 N 为什么不能直接写成 mg？"></textarea>
  <div class="row">
    <button id="addask" class="primary">记下这个问题</button>
    <button id="copyask">复制全部</button>
    <button id="expbtn">导出我的作答</button>
    <span class="hint" id="askcount"></span>
  </div>
  <div class="mine" id="minelist"></div>
</section>

<section class="kc" id="wrapup">
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
