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
    return (f"【课件第 {page} 页原文】\n{(page_text or '')[:1500]}\n\n"
            f"【他框选的那一段】\n{(selection or '（他没有框选，只提了问题）')[:400]}\n\n"
            f"【他的提问】\n{question}\n"
            f"{hist}\n\n"
            f"【他已经连续卡壳 {stalls} 次】\n"
            f"（达到 3 次就给完整讲解 + 反向验证问题）\n\n"
            f"请给出你这一轮要说的话。")


#: 会话状态（追问的来回），只活在内存里 —— 服务重启就清空，符合"追问是临时的"语义
_SESSIONS: dict[str, list[dict]] = {}
_LOCK = threading.Lock()


def ask_once(library_root: str, payload: dict) -> dict:
    """一次追问。返回 `{ok, text, stalls, gave_answer}`。"""
    sid = str(payload.get("session") or "default")
    selection = str(payload.get("selection") or "")
    question = str(payload.get("question") or "").strip()
    page = int(payload.get("page") or 0)
    page_text = str(payload.get("page_text") or "")

    with _LOCK:
        hist = _SESSIONS.setdefault(sid, [])

    # 上一轮 AI 问完，他这轮回了一句 —— 先把这句记进历史并判卡壳
    if hist and hist[-1].get("role") == "assistant" and question:
        hist.append({"role": "user", "text": question,
                     "stall": is_stall(question)})

    if not question and hist:
        return {"ok": False, "msg": "空回复"}

    ok, why = engine.available()
    if not ok:
        return {"ok": False, "msg": f"引擎不可用：{why}"}

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
        study.append_ladder(library_root, str(payload.get("lesson") or ""),
                            page, question, text, stalls=stalls, gave_answer=gave)
    except Exception:  # noqa: BLE001 - 记账失败不该让追问本身失败
        traceback.print_exc()
    return {"ok": True, "text": text, "stalls": stalls,
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
            # ★ 用 `/c/<课>/…` **镜像课程目录结构**，页面里的相对路径才成立。
            #   实测事故：原来页面在 `/lessons/<课>/x.html`、图是 `../assets/…`，
            #   浏览器解析成 `/lessons/assets/…`，而路由是 `/assets/<课>/…` ——
            #   对不上，于是**课件图全部加载失败**（整页最要紧的那块是空白）。
            #   镜像之后：/c/生物/lessons/x.html 里的 ../assets/… → /c/生物/assets/… ✓
            m = re.match(r"^/c/([^/]+)/(.+)$", path)
            if m:
                return self._static(f"{m.group(1)}/{m.group(2)}", self.library_root)
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
            return self._json({"ok": False, "msg": "未知接口"}, 404)
        except Exception as exc:  # noqa: BLE001
            traceback.print_exc()
            return self._json({"ok": False, "msg": f"{type(exc).__name__}: {exc}"}, 500)


def serve(library_root: str, course: str = "", host: str = "127.0.0.1",
          port: int = 8021, open_browser: bool = False) -> None:
    Handler.library_root = os.path.abspath(library_root)
    Handler.course = course
    httpd = ThreadingHTTPServer((host, port), Handler)
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
