# -*- coding: utf-8 -*-
"""学习工作台：把「今天学什么」变成点一下，而不是回 DSH 说话。

用户原话：「你能否做一个强化的工作台，让我可以直接进入这个工作台，选择学习
什么东西而不是全都要回归 DSH？相当于就是做一个前端和 UI 的包装优化」

为什么要单独一个模块：现在 `/` 只是一张**课程索引**（一长串 lesson 链接），
等于把 DSH 当日常入口。工作台要回答的是三个每天都会问的问题：

  1. 我上次学到哪了？     → 「继续上次」（数据取账本里最近一次来回/标记）
  2. 我有什么可学的？     → 课程卡片（有课件）+ 专题卡片（没课件，AI 按骨架带）
  3. 学完的东西去哪了？   → 复习那一栏（卡片数量 + 去 Anki）

**数据全部来自账本**（唯一真相源），本模块不写任何账本、不联网，
纯函数 → 可离线测试。渲染风格与课件页保持一致（同一套配色/字号）。

「课程」和「专题」怎么分（不用新增文件格式）：
  - 有 `source.json`（有原件）→ **课程**
  - 有 `kcs.json` 但没有 `source.json` → **专题**（骨架是 AI 生成的，没有原件）
这样专题天然继承账本、笔记、知识点、卡片的全套能力。
"""
from __future__ import annotations

import json
import os
import urllib.parse

MODE_TAG = {"survey": "📖 过课件", "ask-first": "❓ 先问后看",
            "teach-first": "✍️ 先教后考"}


def _load(path: str) -> dict:
    """读账本 JSON。**坏数据一律当"没有"** —— 工作台不能因为一门的账本烂掉就打不开。

    实测踩到：文件里是合法 JSON 但**不是对象**（比如一个字符串/数组）时，
    后面 `.get(...)` 直接 AttributeError，整个首页 500。所以这里连类型一起校验。
    """
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _lessons_of(course_dir: str) -> list[dict]:
    """这节课生成了哪些 HTML 课（按课型排序：过课件排最前）。

    ★ 必须**整份读**再找 `data-mode`：实测踩到 —— 只读前 4000 字符的话，
    那段还在 `<style>`/`<script>` 里打转，`<body data-mode=...>` 根本还没出现，
    于是每一节都被认成"未知课型"（老的索引页因此给每一节都标了「?」）。
    """
    import re
    ld = os.path.join(course_dir, "lessons")
    if not os.path.isdir(ld):
        return []
    out: list[dict] = []
    for f in sorted(os.listdir(ld)):
        if not f.endswith(".html"):
            continue
        text = ""
        try:
            with open(os.path.join(ld, f), encoding="utf-8", errors="replace") as fh:
                text = fh.read()
        except OSError:
            pass
        m = re.search(r'data-mode="([^"]+)"', text)
        out.append({"file": f, "title": f[:-5], "mode": m.group(1) if m else ""})
    out.sort(key=lambda x: (x["mode"] != "survey", x["title"]))
    return out


def course_stats(library_root: str, course: str) -> dict:
    """一门课（或专题）的账本统计。**只读**。"""
    cdir = os.path.join(library_root, course)
    led = os.path.join(cdir, ".ledger")
    src = _load(os.path.join(led, "source.json"))
    kcs = _load(os.path.join(led, "kcs.json"))
    study = _load(os.path.join(led, "study.json"))
    cards = _load(os.path.join(led, "cards.json"))

    n_pages = sum(int(v.get("page_count") or 0)
                  for v in (src.get("sources") or {}).values())
    n_kcs = sum(len(c.get("kcs") or []) for c in (kcs.get("chapters") or []))
    threads, turns, last_at, last_page = 0, 0, "", 0
    for v in (study.get("lessons") or {}).values():
        rows = v.get("ladder") or []
        turns += len(rows)
        tids = {}
        for x in rows:
            tids.setdefault((x.get("tid") or "").strip()
                            or f"@p{x.get('p') or 0}", []).append(x)
        threads += len(tids)
        for x in rows:
            if (x.get("at") or "") > last_at:
                last_at, last_page = x.get("at") or "", int(x.get("p") or 0)
    return {
        "name": course,
        "dir": cdir,
        "kind": "topic" if (n_kcs and not n_pages) else "course",
        "lessons": _lessons_of(cdir),
        "pages": n_pages,
        "kcs": n_kcs,
        "threads": threads,
        "turns": turns,
        "cards": len((cards.get("by_id") or {})),
        "last_at": last_at,
        "last_page": last_page,
    }


def scan(library_root: str) -> dict:
    """扫库：所有课程 + 专题。跳过没有账本、也没有课页的目录。"""
    courses: list[dict] = []
    topics: list[dict] = []
    if os.path.isdir(library_root):
        for name in sorted(os.listdir(library_root)):
            if name.startswith(".") or name.startswith("_"):
                continue
            d = os.path.join(library_root, name)
            if not os.path.isdir(d):
                continue
            has_ledger = os.path.isdir(os.path.join(d, ".ledger"))
            st = course_stats(library_root, name)
            if not (has_ledger or st["lessons"]):
                continue
            (topics if st["kind"] == "topic" else courses).append(st)
    return {"courses": courses, "topics": topics,
            "cards": sum(c["cards"] for c in courses + topics)}


_CSS = """
*{box-sizing:border-box}
body{margin:0;background:#16181c;color:#e8e6e3;min-height:100vh;
  font:15px/1.7 -apple-system,"Segoe UI","PingFang SC","Microsoft YaHei",sans-serif}
.wb{max-width:900px;margin:0 auto;padding:28px 20px 60px}
h1{font-size:20px;margin:0 0 4px;display:flex;align-items:center;gap:10px}
.sub{color:#9aa0a6;font-size:13px;margin:0 0 22px}
h2{font-size:14px;color:#9aa0a6;font-weight:600;margin:26px 0 10px;
  letter-spacing:.06em}
a{color:#7fc9a0;text-decoration:none}
a:hover{text-decoration:underline}
.card{display:block;background:#1c1f24;border:1px solid #2c3036;border-radius:12px;
  padding:14px 16px;margin:8px 0;color:inherit}
.card:hover{border-color:#3a4048;text-decoration:none;background:#20242a}
.card .t{font-size:16px;font-weight:600;display:block}
.card .m{color:#9aa0a6;font-size:12.5px;margin-top:5px;
  font-variant-numeric:tabular-nums}
.row{display:flex;gap:12px;flex-wrap:wrap}
.row .card{flex:1 1 260px;margin:0}
.today{background:#1b2a22;border:1px solid #2f6f4e;border-radius:12px;padding:16px}
.today .t{font-size:17px;font-weight:600}
.today .m{color:#a9c9b6;font-size:13px;margin:6px 0 12px}
.btn{display:inline-block;background:#2f6f4e;color:#fff;border-radius:8px;
  padding:7px 16px;font-size:14px;border:0;cursor:pointer}
.btn:hover{background:#3a8560;text-decoration:none}
.btn.ghost{background:transparent;border:1px solid #3a4048;color:#cfd4da}
.pill{display:inline-block;background:#232830;border:1px solid #2c3036;
  border-radius:99px;padding:1px 9px;font-size:11.5px;color:#9aa0a6;margin-left:6px}
.empty{color:#7c8288;font-size:13.5px;border:1px dashed #2c3036;border-radius:12px;
  padding:16px}
.dim{color:#7c8288}
ul.units{margin:8px 0 0;padding-left:20px}
ul.units li{margin:5px 0}
"""


def _esc(s) -> str:
    return (str(s or "").replace("&", "&amp;").replace("<", "&lt;")
            .replace(">", "&gt;").replace('"', "&quot;"))


def _card(c: dict) -> str:
    bits = []
    if c["pages"]:
        bits.append(f"{c['pages']} 页")
    if c["kcs"]:
        bits.append(f"{c['kcs']} 个知识点")
    if c["lessons"]:
        bits.append(f"{len(c['lessons'])} 节课页")
    bits.append(f"对话 {c['threads']} 段" if c["threads"] else "还没开始")
    if c["cards"]:
        bits.append(f"卡 {c['cards']} 张")
    last = f" · 上次 {_esc(c['last_at'])}" if c["last_at"] else ""
    # 有课页 → 点进课程页；专题还没做学习入口 → 只显示状态
    href = (f"/_c/{urllib.parse.quote(c['name'])}" if c["lessons"] else "")
    inner = (f'<span class="t">{_esc(c["name"])}</span>'
             f'<span class="m">{" · ".join(bits)}{last}</span>')
    if href:
        return f'<a class="card" href="{href}">{inner}</a>'
    return f'<div class="card">{inner}</div>'


def render_home(d: dict) -> str:
    """第一屏：继续上次 → 我的课程 → 专题学习 → 复习。"""
    out = ['<!DOCTYPE html><html lang=zh-CN><head><meta charset=utf-8>',
           '<meta name=viewport content="width=device-width,initial-scale=1">',
           '<title>学习工作台</title>', f'<style>{_CSS}</style></head><body>',
           '<div class="wb">',
           '<h1>学习工作台<span class="pill">本机 · 离线</span></h1>',
           '<p class="sub">今天学什么，从这里点进去就行。</p>']

    r = d.get("resume")
    if r:
        out.append('<div class="today">')
        out.append(f'<span class="t">▶ 继续上次：{_esc(r["course"])} · '
                   f'第 {r["page"]} 页</span>')
        out.append(f'<div class="m">上次动手：{_esc(r["at"])}'
                   f'（这门课一共聊过 {r["turns"]} 轮）</div>')
        if r.get("url"):
            out.append(f'<a class="btn" href="{_esc(r["url"])}">继续</a>')
        out.append('</div>')
    else:
        out.append('<div class="empty">还没有学习记录。下面挑一门课开始。</div>')

    out.append('<h2>📚 我的课程</h2>')
    if d["courses"]:
        out.append('<div class="row">')
        out += [_card(c) for c in d["courses"]]
        out.append('</div>')
    else:
        out.append('<div class="empty">库里还没有课程。把讲义（PDF/PPTX）'
                   '拖进来就会出现在这里。</div>')

    out.append('<h2>🧠 专题学习<span class="pill">没有课件，AI 按骨架带你学</span></h2>')
    if d["topics"]:
        out.append('<div class="row">')
        out += [_card(c) for c in d["topics"]]
        out.append('</div>')
    else:
        out.append('<div class="empty">还没有专题。<br>'
                   '下一个就是「<b>架构设计</b>」—— 8 周骨架已经定好'
                   '（24 章切 8 个单元），正在接进来。</div>')

    out.append('<h2>🔁 复习</h2>')
    if d["cards"]:
        out.append(f'<div class="card"><span class="t">卡片 {d["cards"]} 张</span>'
                   f'<span class="m">间隔重复交给 Anki（它干了十几年了，'
                   f'我们不重造）· 打开 Anki 复习</span></div>')
    else:
        out.append('<div class="empty">还没有卡片。学过一轮、'
                   'AI 给过完整讲解之后就会自动出卡。</div>')

    out.append('</div></body></html>')
    return "\n".join(out)


def render_course(d: dict, c: dict) -> str:
    """一门课的页面：统计 + 各节课页入口（就是以前那个索引，现在归到课程卡里）。"""
    out = ['<!DOCTYPE html><html lang=zh-CN><head><meta charset=utf-8>',
           '<meta name=viewport content="width=device-width,initial-scale=1">',
           f'<title>{_esc(c["name"])}</title>', f'<style>{_CSS}</style></head><body>',
           '<div class="wb">',
           f'<h1>{_esc(c["name"])}</h1>',
           '<p class="sub"><a href="/">← 回工作台</a></p>',
           '<div class="card"><span class="t">这门课的家底</span>'
           f'<span class="m">{c["pages"]} 页 · {c["kcs"]} 个知识点 · '
           f'对话 {c["threads"]} 段（{c["turns"]} 轮） · 卡 {c["cards"]} 张'
           f'{" · 上次 " + _esc(c["last_at"]) if c["last_at"] else ""}</span></div>',
           '<h2>📖 开始学</h2>']
    if c["lessons"]:
        out.append('<div class="row">')
        for one in c["lessons"]:
            tag = MODE_TAG.get(one["mode"], one["mode"] or "课页")
            href = (f"/c/{urllib.parse.quote(c['name'])}/lessons/"
                    f"{urllib.parse.quote(one['file'])}")
            out.append(f'<a class="card" href="{href}"><span class="t">'
                       f'{_esc(one["title"])}</span>'
                       f'<span class="m">{tag}</span></a>')
        out.append('</div>')
    else:
        out.append('<div class="empty">这门课还没有课页。</div>')
    out.append('</div></body></html>')
    return "\n".join(out)


def home_data(library_root: str) -> dict:
    """工作台第一屏要的全部数据（**从账本现算**，不缓存）。"""
    data = scan(library_root)
    # 「继续上次」：账本里最近一次对话/标记落在哪门课的哪一页。
    # 为什么用它而不是"最后打开的文件"：文件时间戳会被重跑覆盖，
    # 而账本里的时间是**你真正动手**的时间。
    best = None
    for c in data["courses"] + data["topics"]:
        if c["last_at"] and (best is None or c["last_at"] > best["last_at"]):
            best = c
    data["resume"] = None
    if best:
        lesson = None
        # 过课件那一节覆盖全部页；没有就退回第一节
        for one in best["lessons"]:
            if one["mode"] == "survey":
                lesson = one
                break
        if lesson is None and best["lessons"]:
            lesson = best["lessons"][0]
        data["resume"] = {
            "course": best["name"], "page": best["last_page"],
            "at": best["last_at"], "turns": best["turns"],
            "url": (f"/c/{urllib.parse.quote(best['name'])}/lessons/"
                    f"{urllib.parse.quote(lesson['file'])}#p{best['last_page']}"
                    if lesson else ""),
        }
    return data
