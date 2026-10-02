# -*- coding: utf-8 -*-
"""本地小服务 —— 让学习页**一键式**工作，并且能做交互式追问。

## 为什么必须有它（用户实测反馈）

用户原话：「**什么意思，我导出了之后需要做什么**，我希望这是一键式的，
同时**交互式必须要做**」。

两句话指的是同一个根因：**HTML 是离线的，所以一切都得靠"导出再手动导入"，
而 AI 追问压根做不成**（交互式追问 = AI 反问 → 你答 → 它再答，必须有人在线等着）。

隔壁 `ppt-deepreader` 早就是这么解的（标准库 `ThreadingHTTPServer`，
`run.py serve`，本地端口），这里照它的写法来 —— 不引框架、不引依赖。

## 路由

    GET  /                      首页：列出所有课程与它们的学习页
    GET  /lessons/<课>/<文件>    学习页（过课件 / ask-first / teach-first）
    GET  /assets/<课>/<路径>     课件页图
    GET  /api/ping              页面用它探测"服务在不在"
    POST /api/mark              标记 / 提问 → **直接进账本**（不再需要导出）
    POST /api/grade             三档自评 → 直接进账本
    POST /api/ask               **交互式追问（阶梯）**

## 阶梯（抄 Flagrare/llm-tutor 的五级提示阶梯）

默认**不直接给答案**：先反问他该往哪想；他连续卡壳 3 次、或明说「别问了直接讲」，
才给完整讲解，且给完必须再问一个**反向验证**的问题。
实测教训：他原来那 12 次框选提问每一次都是"直接给答案"，
读完很爽但**不产生任何回忆痕迹**（只产出 13 张卡、0 条笔记）。
"""
from __future__ import annotations

import json
import os
import posixpath
import re
import threading
import traceback
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import engine
import lesson_html
import study

HERE = os.path.dirname(os.path.abspath(__file__))

#: 卡壳判定：命中这些就算"答不上来"（他要是回了实质内容，立刻重置计数）
_STALL_RE = re.compile(
    r"不知道|不清楚|不明白|不懂|不会|答不上|想不出|没思路|放弃|跳过|不知道啊|"
    r"^[\s。，、？?!.]*$|^.{0,2}$|太难|算了|直接讲|别问了|给答案"
)


def is_stall(text: str) -> bool:
    """这句话算不算"卡壳"。空/极短/表达挫败/明说放弃都算。"""
    t = (text or "").strip()
    return bool(_STALL_RE.search(t))


def wants_answer(text: str) -> bool:
    """他明确说"别问了直接讲" —— 硬开关，立刻给答案。"""
    return bool(re.search(r"直接讲|别问了|给答案|告诉我吧|别绕", text or ""))


# ---------------------------------------------------------------- 阶梯

LADDER_SYSTEM = """你是一个**只提问、不直接给答案**的助教。

学生框选了课件上的一段并提问。你的任务是**逼他自己想**，而不是替他讲。
这很重要：他以前每次提问都直接拿到完整答案，读完就忘，什么都没留下。

铁律：
1. **只输出你要对他说的那句话**。不要复述任务、不要"好的/当然"、不要标题、
   不要 markdown 围栏、不要 JSON。
2. 默认**先反问**：一句话点出他该从哪个方向想，或问一个具体的小问题。
   **不要顺手把答案给出来** —— 哪怕你觉得很简单。
3. 只有在这两种情况下才给完整讲解：
   （a）他已经**连续卡壳 3 次**（我会告诉你卡了几次）；
   （b）他明说"别问了直接讲"。
   给完讲解后**必须**再问一个**反向验证**的小问题（换个角度考他有没有真懂）。
4. **不许假肯定**（"这是个好问题"这种），不道歉。
5. 一次只问**一个**问题。
6. 用中文，口语一点，像坐在旁边的助教。别超过 120 字（除非在给完整讲解）。"""


def build_ask_prompt(page_text: str, selection: str, question: str,
                     history: list[dict], page: int) -> str:
    hist = ""
    if history:
        rows = []
        for h in history[-8:]:
            who = "他答" if h.get("role") == "user" else "你说"
            rows.append(f"- {who}：{(h.get('text') or '').strip()[:200]}")
        hist = "\n【之前的来回】\n" + "\n".join(rows)
    stalls = sum(1 for h in history if h.get("role") == "user" and h.get("stall"))
    sel = (selection or "").strip()
    sel_block = (f"\n【他框选的那一块 —— **这是他真正在问的地方，请以它为主**】\n{sel[:900]}\n"
                 if sel else "\n（他没框选，只提了问题）\n")
    return (f"【他框的那一块的所在页·整页原文（仅供背景，别拿它当重点）】\n"
            f"{(page_text or '')[:1200]}\n"
            f"{sel_block}\n"
            f"【他的提问】\n{question}\n"
            f"{hist}\n\n"
            f"【他已经连续卡壳 {stalls} 次】\n"
            f"（达到 3 次就给完整讲解 + 反向验证问题）\n\n"
            f"请给出你这一轮要说的话。")


#: 会话状态（追问的来回），只活在内存里 —— 服务重启就清空。
#: 键是**页面会话**；值是 `{tid, hist}`：tid 一变（点了「接着问」另一段对话），
#: hist 就从账本重新拼回来，见 `seed_hist`。
_SESSIONS: dict[str, dict] = {}
_LOCK = threading.Lock()


def kcs_flat(library_root: str, course: str) -> list[dict]:
    """把知识点骨架摊平成列表（带上页码），给侧栏的「知识点」页用。"""
    import json as _json
    p = os.path.join(library_root, course, ".ledger", "kcs.json")
    if not os.path.isfile(p):
        return []
    try:
        d = _json.load(open(p, encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return []
    out: list[dict] = []
    for ch in d.get("chapters") or []:
        for k in ch.get("kcs") or []:
            out.append({
                "id": k.get("id"), "label": k.get("label"),
                "page": k.get("page"), "part": k.get("part"),
                "type": k.get("type"), "importance": k.get("importance"),
                "hub": bool(k.get("is_hub")),
                "points": (k.get("points") or [])[:3],
                "questions": [q.get("q") for q in (k.get("questions") or []) if q.get("q")],
                "lecture": ch.get("label") or ch.get("id"),
            })
    out.sort(key=lambda x: (x.get("page") or 0))
    return out


def page_image(library_root: str, course: str, page: int) -> str:
    """找第 N 页的页图绝对路径（从账本的 pages 记录里取，不猜文件名/扩展名）。"""
    led = os.path.join(library_root, course, ".ledger")
    src = os.path.join(led, "source.json")
    if not (os.path.isfile(src) and os.path.isdir(os.path.join(led, "pages"))):
        return ""
    try:
        s = json.load(open(src, encoding="utf-8")).get("sources") or {}
        for stem, rec in s.items():
            slug = rec.get("slug") or ""
            pf = os.path.join(led, "pages", stem + ".json")
            if not (slug and os.path.isfile(pf)):
                continue
            d = json.load(open(pf, encoding="utf-8"))
            for p in d.get("pages") or []:
                if int(p.get("no") or 0) == int(page):
                    img = p.get("image") or ""
                    if img:
                        full = os.path.join(library_root, course, "assets", slug, img)
                        if os.path.isfile(full):
                            return full
    except Exception:  # noqa: BLE001
        pass
    return ""


def read_region(library_root: str, course: str, page: int, rect: list,
                page_text: str, question: str) -> dict:
    """**把框里那一小块裁出来，交给视觉模型读**。

    用户原话：「问的确实很好，**但你的问题和我的划线没关系啊**」。

    根因：他画的框在程序眼里只是**图片上的四个百分比数字** ——
    「框里是什么」程序根本不知道，所以只能拿整页文字去问，自然跟他的框对不上。
    隔壁 deepreader 早就是这么解的（裁剪 → 视觉模型），这里直接复用它的
    `VisionClient.explain_region()`，不重写。

    失败一律返回 `{"ok": False, ...}` —— 读不出来不该让追问整条断掉。
    """
    if not (isinstance(rect, list) and len(rect) == 4) or not page:
        return {"ok": False, "msg": "没有框选区域"}
    full = page_image(library_root, course, page)
    if not full:
        return {"ok": False, "msg": "找不到这一页的页图"}
    try:
        from PIL import Image                      # noqa: PLC0415
        im = Image.open(full)
        w, h = im.size
        x, y, bw, bh = [float(v) for v in rect]
        box = (max(0, int(x / 100 * w)), max(0, int(y / 100 * h)),
               min(w, int((x + bw) / 100 * w)), min(h, int((y + bh) / 100 * h)))
        if box[2] - box[0] < 8 or box[3] - box[1] < 8:
            return {"ok": False, "msg": "框太小了"}
        crop = im.crop(box)
        tmp_dir = os.path.join(library_root, course, "study", "crops")
        os.makedirs(tmp_dir, exist_ok=True)
        cp = os.path.join(tmp_dir, f"p{page}_{box[0]}_{box[1]}_{box[2]}_{box[3]}.png")
        if not os.path.isfile(cp):
            crop.save(cp)
    except Exception as e:  # noqa: BLE001
        traceback.print_exc()
        return {"ok": False, "msg": f"裁剪失败：{type(e).__name__}: {e}"}

    try:
        # ★ 必须**先**拿 settings —— 这一步会把真源 ppt-deepreader 加进 sys.path。
        #   反过来先 import `src.vision` 会 ModuleNotFoundError（实测踩到：
        #   "读框里内容"整条静默失败，AI 只能拿整页文字瞎问）。
        settings = engine.settings()
        from src.vision import VisionClient        # noqa: PLC0415
        vc = VisionClient(settings)
        if not vc.enabled:
            return {"ok": False, "msg": "没有配置视觉后端（读不了框里的内容）"}
        r = vc.explain_region(cp, page_text=page_text[:1500],
                              question=question or "这里在讲什么？", page_no=page)
        return {"ok": True, "crop": cp,
                "transcript": (r.get("transcript") or "").strip(),
                "latex": (r.get("latex") or "").strip(),
                "explanation": (r.get("explanation") or "").strip()}
    except Exception as e:  # noqa: BLE001
        traceback.print_exc()
        return {"ok": False, "msg": f"{type(e).__name__}: {e}"}


def seed_hist(library_root: str, lesson: str, tid: str) -> list[dict]:
    """从**账本**把一段子对话的来回读回来。

    这是"以后再调出来读 / 接着往下问"的关键：内存里的 `_SESSIONS` 重启就没了，
    只要 `tid` 还躺在账本里，隔几个月点开同一段对话，AI 照样知道前面聊过什么。
    """
    if not tid:
        return []
    out: list[dict] = []
    for th in study.threads_of(library_root, lesson):
        if (th.get("tid") or "") != tid:
            continue
        for t in th.get("turns") or []:
            q = (t.get("q") or "").strip()
            if q:
                out.append({"role": "user", "text": q,
                            "stall": bool(t.get("stall")) or is_stall(q)})
            if (t.get("a") or "").strip():
                out.append({"role": "assistant", "text": t["a"]})
    return out


def ask_once(library_root: str, payload: dict) -> dict:
    """一次追问。返回 `{ok, text, stalls, gave_answer, tid}`。"""
    sid = str(payload.get("session") or "default")
    lesson = str(payload.get("lesson") or "")
    selection = str(payload.get("selection") or "")
    question = str(payload.get("question") or "").strip()
    page = int(payload.get("page") or 0)
    page_text = str(payload.get("page_text") or "")
    # 子对话编号：客户端给（同一个框 = 同一段对话）；没给就按"节+页"兜底，
    # 这样旧版页面照样能用，只是粒度粗一点。
    tid = str(payload.get("tid") or "").strip() or f"{sid}:p{page}"

    with _LOCK:
        sess = _SESSIONS.get(sid)
        # 换了 tid = 换了一段对话（点「接着问」）→ 从账本把那段接回来
        if sess is None or sess.get("tid") != tid:
            sess = {"tid": tid, "hist": seed_hist(library_root, lesson, tid)}
            _SESSIONS[sid] = sess
        hist = sess["hist"]

    # 上一轮 AI 问完，他这轮回了一句 —— 先把这句记进历史并判卡壳
    if hist and hist[-1].get("role") == "assistant" and question:
        hist.append({"role": "user", "text": question,
                     "stall": is_stall(question)})

    if not question and hist:
        return {"ok": False, "msg": "空回复"}

    ok, why = engine.available()
    if not ok:
        return {"ok": False, "msg": f"引擎不可用：{why}"}

    # ★ 他画了框 → **先把框里那一小块读出来**，再拿它去追问。
    #   不做这一步，AI 只能拿整页文字瞎问 ——
    #   用户原话：「问的确实很好，**但你的问题和我的划线没关系啊**」。
    region = {}
    rect = payload.get("rect")
    course = str(payload.get("course") or "")
    if rect and page and course:
        region = read_region(library_root, course, page, rect,
                             page_text, question)
        if region.get("ok"):
            parts = [x for x in (region.get("transcript"),
                                 region.get("latex"),
                                 region.get("explanation")) if x]
            selection = "【框里读出来的内容】\n" + "\n".join(parts)
        with _LOCK:
            hist.append({"role": "user", "text":
                         f"[框选区域] 第 {page} 页 rect={rect}"})

    prompt = build_ask_prompt(page_text, selection, question, hist, page)
    try:
        text, _meta = engine.chat(LADDER_SYSTEM, prompt, max_tokens=1600)
    except Exception as e:  # noqa: BLE001
        traceback.print_exc()
        return {"ok": False, "msg": f"{type(e).__name__}: {e}"}

    text = engine.strip_think(text or "").strip()
    stalls = sum(1 for h in hist if h.get("role") == "user" and h.get("stall"))
    gave = stalls >= 3 or wants_answer(question) or "反向验证" in text or "再问你一个" in text
    with _LOCK:
        hist.append({"role": "assistant", "text": text})
        if len(hist) > 24:
            del hist[:-24]
    # ★ 记进账本 —— 否则这些反问和你的回答**关掉页面就没了**。
    #   用户原话：「那我在哪里查看我和 ai 的交互和反问呢」
    try:
        study.append_ladder(library_root, lesson, page, question, text,
                            stalls=stalls, gave_answer=gave, tid=tid,
                            stall=is_stall(question),
                            # ★ 把「框里读出来的原文」也存进账本 ——
                            #   它是事后出卡时最有价值的语境（你当时到底在看哪一块），
                            #   以前只活在这一次请求里，关掉就没了。
                            sel=selection)
    except Exception:  # noqa: BLE001 - 记账失败不该让追问本身失败
        traceback.print_exc()
    return {"ok": True, "text": text, "stalls": stalls, "tid": tid,
            "gave_answer": bool(gave), "turn": len(hist)}


# ---------------------------------------------------------------- 账本写入

def record(path: str, kind: str, payload: dict, library_root: str) -> dict:
    """把一条交互直接写进账本 —— 页面不再需要"导出"这一步。"""
    lesson = str(payload.get("lesson") or "").strip()
    if not lesson:
        return {"ok": False, "msg": "缺 lesson"}
    data = study.load_study(library_root)
    if kind == "mark":
        one = {"lesson": lesson, "mode": "survey",
               "exported_at": payload.get("at") or "",
               "marks": [payload.get("mark") or {}]}
        rep = study.import_export(library_root, data, one)
    elif kind == "grade":
        one = {"lesson": lesson, "mode": payload.get("mode") or "",
               "exported_at": payload.get("at") or "",
               "grades": {str(payload.get("qid")): payload.get("grade")}}
        rep = study.import_export(library_root, data, one)
    else:
        return {"ok": False, "msg": f"未知类型 {kind}"}
    study.save_study(library_root, data)
    return {"ok": True, "report": rep}


# ---------------------------------------------------------------- HTTP

class Handler(BaseHTTPRequestHandler):
    server_version = "CoursePipeline/1.0"
    library_root = ""
    course = ""

    def log_message(self, fmt, *args):  # noqa: A003
        pass          # 静音默认请求日志（太吵）

    # ---- 通用 ----
    def _send(self, code: int, body: bytes, ctype: str) -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def _json(self, data, code: int = 200) -> None:
        self._send(code, json.dumps(data, ensure_ascii=False).encode("utf-8"),
                   "application/json; charset=utf-8")

    def _body(self) -> dict:
        try:
            n = int(self.headers.get("Content-Length") or 0)
            raw = self.rfile.read(n) if n else b""
            return json.loads(raw.decode("utf-8")) if raw else {}
        except Exception:  # noqa: BLE001
            return {}

    # ---- 静态 ----
    def _static(self, rel: str, root: str) -> None:
        """把 `<root>/<rel>` 发出去。**防目录穿越**（rel 里不许有 ..）。"""
        rel = posixpath.normpath(rel).lstrip("/")
        if rel.startswith("..") or os.path.isabs(rel):
            return self._json({"ok": False, "msg": "非法路径"}, 400)
        p = os.path.join(root, *rel.split("/"))
        if not os.path.isfile(p):
            return self._json({"ok": False, "msg": "找不到 " + rel}, 404)
        ext = os.path.splitext(p)[1].lower()
        ctype = {".html": "text/html; charset=utf-8",
                 ".json": "application/json; charset=utf-8",
                 ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
                 ".css": "text/css; charset=utf-8",
                 ".js": "application/javascript; charset=utf-8"}.get(ext,
                                                                     "application/octet-stream")
        with open(p, "rb") as f:
            self._send(200, f.read(), ctype)

    def _index(self) -> None:
        root = self.course_root()
        rows = []
        if os.path.isdir(self.library_root):
            for c in sorted(os.listdir(self.library_root)):
                cd = os.path.join(self.library_root, c)
                ld = os.path.join(cd, "lessons")
                if not os.path.isdir(ld) or c.startswith("."):
                    continue
                files = sorted(f for f in os.listdir(ld) if f.endswith(".html"))
                if not files:
                    continue
                items = []
                for f in files:
                    try:
                        head = open(os.path.join(ld, f), encoding="utf-8",
                                    errors="replace").read(4000)
                    except OSError:
                        continue
                    m = re.search(r'data-mode="([^"]+)"', head)
                    mode = m.group(1) if m else "?"
                    tag = {"survey": "📖 过课件", "ask-first": "❓ 先问后看",
                           "teach-first": "✍️ 先教后考"}.get(mode, mode)
                    items.append((tag, f, mode))
                items.sort(key=lambda x: (x[2] != "survey", x[1]))
                lis = "".join(
                    f'<li><span class="tag">{t}</span>'
                    f'<a href="/c/{urllib.parse.quote(c)}/lessons/{urllib.parse.quote(f)}">'
                    f'{f[:-5]}</a></li>' for t, f, _m in items)
                rows.append(f"<h2>{c}</h2><ul>{lis}</ul>")
        body = ("<!DOCTYPE html><html lang=zh-CN><head><meta charset=utf-8>"
                "<title>学习库</title><style>"
                "body{font:16px/1.7 -apple-system,'Segoe UI','PingFang SC',sans-serif;"
                "max-width:900px;margin:40px auto;padding:0 20px;color:#1f2328}"
                "a{color:#2f6f4e;text-decoration:none}a:hover{text-decoration:underline}"
                "li{margin:6px 0}.tag{display:inline-block;font-size:12px;padding:1px 8px;"
                "border-radius:99px;background:#eef5f0;color:#2f6f4e;margin-right:8px}"
                "h2{margin:28px 0 8px;font-size:19px}"
                ".note{background:#eef5f0;border-radius:10px;padding:12px 16px;font-size:14px}"
                "</style></head><body>"
                "<h1>学习库</h1>"
                '<div class="note">服务在跑 —— 页面上划的线、提的问题、自评，'
                "<b>都会直接进账本，不用再导出再导入</b>。"
                "在这个页面里点开任何一页即可。</div>"
                + ("".join(rows) or "<p>还没有生成过学习页。</p>")
                + "</body></html>")
        self._send(200, body.encode("utf-8"), "text/html; charset=utf-8")

    def course_root(self) -> str:
        return os.path.join(self.library_root, self.course) if self.course \
            else self.library_root

    # ---- 路由 ----
    def do_GET(self) -> None:  # noqa: N802
        parsed = urllib.parse.urlparse(self.path)
        path = urllib.parse.unquote(parsed.path)
        try:
            if path in ("/", "/index.html"):
                return self._index()
            if path == "/api/ping":
                ok, why = engine.available()
                return self._json({"ok": True, "engine": ok, "why": why,
                                   "library": self.library_root})
            # 「我问过 AI 的」—— 用户问「我在哪里查看我和 ai 的交互和反问呢」
            if path == "/api/ladder":
                q = urllib.parse.parse_qs(parsed.query)
                course2 = (q.get("course") or [self.course or ""])[0]
                lesson2 = (q.get("lesson") or [""])[0]
                root2 = os.path.join(self.library_root, course2) if course2 \
                    else self.library_root
                return self._json({"ok": True,
                                   "ladder": study.ladder_of(root2, lesson2)})
            # 账本里的**框**（课件标记）—— 页面打开时读回来画上。
            # 评审结论 A2：以前框只存在浏览器 localStorage，换浏览器/清缓存就全没了，
            # 而右边的对话还在（对话在账本里）→「对话在，但不知道当时框的是哪儿」。
            if path == "/api/marks":
                q = urllib.parse.parse_qs(parsed.query)
                course4 = (q.get("course") or [self.course or ""])[0]
                lesson4 = (q.get("lesson") or [""])[0]
                root4 = os.path.join(self.library_root, course4) if course4 \
                    else self.library_root
                return self._json({"ok": True,
                                   "marks": study.marks_of(root4, lesson4)})
            # 子对话：用户原话「我希望制作成**子对话**的形式……可以之后再调出来读」。
            # 同一段对话的来回拼回一起，连标题（第一个问题）和轮数一起给出去。
            if path == "/api/threads":
                q = urllib.parse.parse_qs(parsed.query)
                course3 = (q.get("course") or [self.course or ""])[0]
                lesson3 = (q.get("lesson") or [""])[0]
                root3 = os.path.join(self.library_root, course3) if course3 \
                    else self.library_root
                return self._json({"ok": True,
                                   "threads": study.threads_of(root3, lesson3)})
            # ★ 用 `/c/<课>/…` **镜像课程目录结构**，页面里的相对路径才成立。
            #   实测事故：原来页面在 `/lessons/<课>/x.html`、图是 `../assets/…`，
            #   浏览器解析成 `/lessons/assets/…`，而路由是 `/assets/<课>/…` ——
            #   对不上，于是**课件图全部加载失败**（整页最要紧的那块是空白）。
            #   镜像之后：/c/生物/lessons/x.html 里的 ../assets/… → /c/生物/assets/… ✓
            m = re.match(r"^/c/([^/]+)/(.+)$", path)
            if m:
                return self._static(f"{m.group(1)}/{m.group(2)}", self.library_root)
            # 侧栏「知识点」页要用：这门课的知识点骨架（含页码，点了能跳回那一页）
            if path == "/api/kcs":
                q = urllib.parse.parse_qs(parsed.query)
                c3 = (q.get("course") or [self.course or ""])[0]
                return self._json({"ok": True, "kcs": kcs_flat(self.library_root, c3)})
            # 兼容旧链接（/lessons/<课>/<文件> 与 /assets/<课>/<路径>）
            m = re.match(r"^/lessons/([^/]+)/(.+)$", path)
            if m:
                return self._static(f"{m.group(1)}/lessons/{m.group(2)}",
                                    self.library_root)
            m = re.match(r"^/assets/([^/]+)/(.+)$", path)
            if m:
                return self._static(f"{m.group(1)}/assets/{m.group(2)}",
                                    self.library_root)
            return self._json({"ok": False, "msg": "未知接口"}, 404)
        except Exception as exc:  # noqa: BLE001
            traceback.print_exc()
            return self._json({"ok": False, "msg": f"{type(exc).__name__}: {exc}"}, 500)

    def do_POST(self) -> None:  # noqa: N802
        path = urllib.parse.urlparse(self.path).path
        payload = self._body()
        course = str(payload.get("course") or self.course or "")
        try:
            if path == "/api/mark":
                return self._json(record(path, "mark", payload,
                                         os.path.join(self.library_root, course)))
            if path == "/api/grade":
                return self._json(record(path, "grade", payload,
                                         os.path.join(self.library_root, course)))
            if path == "/api/ask":
                return self._json(ask_once(os.path.join(self.library_root, course),
                                           payload))
            # 删掉一段对话 / 一轮来回（用户：「框能删、对话反而不能删，很奇怪」）。
            # 服务端**先备份再删**（见 study._backup_study）—— 账本删错了没法重建。
            if path in ("/api/thread-del", "/api/turn-del"):
                root5 = os.path.join(self.library_root, course) if course \
                    else self.library_root
                lesson5 = str(payload.get("lesson") or "")
                tid5 = str(payload.get("tid") or "")
                page5 = int(payload.get("page") or 0)
                if not (lesson5 and tid5):
                    return self._json({"ok": False, "msg": "缺 lesson/tid"})
                if path == "/api/thread-del":
                    n = study.delete_thread(root5, lesson5, tid5, page5)
                else:
                    n = study.delete_turn(root5, lesson5, tid5,
                                          int(payload.get("index") or 0), page5)
                # 内存里的会话也清掉，免得"删了还接着聊"（历史会从账本重读）
                with _LOCK:
                    for k in list(_SESSIONS):
                        if _SESSIONS[k].get("tid") == tid5:
                            del _SESSIONS[k]
                return self._json({"ok": bool(n), "removed": n,
                                   "msg": "" if n else "没找到那一段（可能已经删过了）"})
            return self._json({"ok": False, "msg": "未知接口"}, 404)
        except Exception as exc:  # noqa: BLE001
            traceback.print_exc()
            return self._json({"ok": False, "msg": f"{type(exc).__name__}: {exc}"}, 500)


class _Server(ThreadingHTTPServer):
    """**不许两个服务抢同一个端口。**

    实测踩到（评审时发现的运行事故）：Windows 上 `SO_REUSEADDR` 允许
    **多个进程同时 LISTEN 同一个端口**，于是"重启一下服务"根本没生效 ——
    端口上同时挂着 3 个进程，请求随机落到老进程上，我改的代码不出现，
    页面还是旧样子（当时以为是代码没生效，查了半天）。
    这里关掉 reuse（Windows 上 TIME_WAIT 本来也不挡重新 bind），
    第二次启动会**明确报错**，而不是悄悄退化成"两个服务各答各的"。
    """
    allow_reuse_address = False
    daemon_threads = True


def serve(library_root: str, course: str = "", host: str = "127.0.0.1",
          port: int = 8021, open_browser: bool = False) -> None:
    Handler.library_root = os.path.abspath(library_root)
    Handler.course = course
    try:
        httpd = _Server((host, port), Handler)
    except OSError as e:
        raise SystemExit(
            f"端口 {port} 已经有一个服务在跑了（{e}）。\n"
            f"  · 想关掉它：把那个开着「学习库服务」的黑窗口关掉，或重启电脑；\n"
            f"  · 或者换个端口：python run.py serve --port 8022\n"
            f"（**不能**两个一起跑：请求会随机落到老的那个上，你会以为"
            f"「改了没生效」。）") from e
    url = f"http://{host}:{port}/"
    print(f"学习库服务已启动：{url}")
    print(f"  库目录：{Handler.library_root}")
    print("  Ctrl+C 停止")
    if open_browser:
        import webbrowser
        threading.Timer(0.6, lambda: webbrowser.open(url)).start()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n已停止")
    finally:
        httpd.server_close()
