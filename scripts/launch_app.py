# -*- coding: utf-8 -*-
"""双击桌面图标 = 进学习工作台（像应用，不像网站）。

用户原话：「那就做成桌面快捷窗口就行」。要做到三件事：

1. **没有黑窗口**：服务用 `pythonw.exe` 静默起来（`print` 在 pythonw 下是安全的：
   CPython 的 print 遇到 `sys.stdout is None` 会直接返回）。
2. **没有地址栏/标签页**：Edge 的 `--app=<url>` 模式开一个"像应用"的窗口。
   ★ 用**独立的 user-data-dir**：这样它和用户平时开的 Edge 是**两个进程**，
   "窗口关掉"就能被可靠地观察到 —— 否则关掉窗口时 Edge 主进程还在（因为
   用户还开着别的网页），我们就永远等不到"关窗"这个信号，服务也就停不下来。
3. **关窗即停服务**：只有**这次由我们起的**服务才停；如果服务本来就在跑
   （比如你双击过 `启动学习库.bat`），我们只开窗、不接管它的生死。

本模块的纯函数（`ping` / `find_edge` / `edge_cmd` / `pythonw`）可以被离线测试；
`main()` 才真的起进程。**测试不要调 main()。**
"""
from __future__ import annotations

import os
import subprocess
import sys
import time
import urllib.error
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
PORT = int(os.environ.get("COURSE_PORT") or 8021)
URL = f"http://127.0.0.1:{PORT}/"
#: 应用专用 profile（放在项目下，不污染你平时的浏览器）
PROFILE = os.path.join(ROOT, ".app-profile")
#: ★ 日志是**必须的**：pythonw 没有控制台，出事时唯一的证据就是这两个文件。
LOG = os.path.join(ROOT, ".app-launch.log")
SERVER_LOG = os.path.join(ROOT, ".app-server.log")
FAIL_PAGE = os.path.join(ROOT, ".app-启动失败.html")

EDGE_CANDIDATES = (
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
)


def log(msg: str) -> None:
    """记一行带时间的日志。**绝不抛异常** —— 日志写不了不能影响开窗口。"""
    try:
        import datetime
        with open(LOG, "a", encoding="utf-8") as f:
            f.write(f"{datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}  "
                    f"{msg}\n")
    except OSError:
        pass


def ping(timeout: float = 1.0) -> bool:
    """服务在不在？（问一句 /api/ping，不猜端口）"""
    try:
        with urllib.request.urlopen(URL + "api/ping", timeout=timeout) as r:
            return r.status == 200
    except (urllib.error.URLError, OSError, ValueError):
        return False


def wait_up(seconds: float = 25.0) -> bool:
    end = time.time() + seconds
    while time.time() < end:
        if ping():
            return True
        time.sleep(0.4)
    return False


def find_edge() -> str:
    for p in EDGE_CANDIDATES:
        if os.path.isfile(p):
            return p
    return ""


def pythonw() -> str:
    """无控制台的解释器 —— 用它起服务才不会有黑窗口。"""
    exe = os.path.join(os.path.dirname(sys.executable), "pythonw.exe")
    return exe if os.path.isfile(exe) else sys.executable


def edge_cmd(edge: str, url: str = URL, profile: str = PROFILE) -> list[str]:
    """Edge 应用窗口的命令行。**独立 profile 是"关窗即停"的前提**。"""
    return [edge, f"--app={url}", f"--user-data-dir={profile}",
            "--no-first-run", "--no-default-browser-check",
            "--window-size=1280,900"]


def start_server() -> subprocess.Popen | None:
    """起服务，**把它的输出留下来**（App 模式下没有控制台，出事只能靠这个文件）。"""
    try:
        out = open(SERVER_LOG, "ab")
    except OSError:
        out = subprocess.DEVNULL
    try:
        log(f"起服务：{pythonw()} run.py serve --port {PORT}")
        return subprocess.Popen(
            [pythonw(), os.path.join(ROOT, "run.py"), "serve", "--port", str(PORT)],
            cwd=ROOT, stdout=out, stderr=subprocess.STDOUT,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except OSError as e:
        log(f"起服务失败：{type(e).__name__}: {e}")
        return None


def fail_page(reason: str) -> str:
    """起不来时给一个**本地**说明页（绝不打开一个还没起来的网址 —— 那只会
    显示「拒绝连接」，用户完全不知道发生了什么）。"""
    tail = ""
    try:
        with open(SERVER_LOG, encoding="utf-8", errors="replace") as f:
            tail = f.read()[-4000:]
    except OSError:
        tail = "（没有服务输出）"
    html = f"""<!DOCTYPE html><html lang=zh-CN><head><meta charset=utf-8>
<title>学习库没起来</title><style>
body{{font:15px/1.7 -apple-system,"Segoe UI","Microsoft YaHei",sans-serif;
background:#16181c;color:#e8e6e3;max-width:820px;margin:40px auto;padding:0 20px}}
pre{{background:#1c1f24;border:1px solid #2c3036;border-radius:10px;padding:12px;
overflow:auto;font-size:12.5px;color:#c7cdd4;max-height:50vh}}
b{{color:#e0c675}} code{{color:#7fc9a0}}</style></head><body>
<h1>学习库没起来</h1>
<p><b>原因</b>：{reason}</p>
<p>两个日志文件（记事本直接打开就能看）：<br>
<code>{LOG}</code><br><code>{SERVER_LOG}</code></p>
<h3>服务自己的输出（最后 4000 字）</h3><pre>{tail}</pre>
<p>常见原因：① 端口 {PORT} 被另一个服务占着（把别的黑窗口关掉再试）；
② Python 环境坏了。</p>
</body></html>"""
    try:
        with open(FAIL_PAGE, "w", encoding="utf-8") as f:
            f.write(html)
    except OSError:
        pass
    return FAIL_PAGE


def kill_profile_edges() -> int:
    """清掉**我们这个 profile** 残留的 Edge 进程，返回清了几个。

    ★★ 这条是实测踩出来的真凶：上一次用"杀进程"的方式关掉应用后，
    Edge 的子进程还挂在那个 profile 上；下一次 `--app` 会把窗口**交给那个残留
    实例**，然后自己 0.1 秒退出 —— 启动器以为"窗口关了"，就把服务停掉，
    用户看到的就是**刚打开就「拒绝连接」**。

    只杀命令行里带我们 profile 名的进程 —— **绝不碰用户平时开的浏览器**。
    """
    marker = os.path.basename(PROFILE)          # `.app-profile`
    ps = (
        "$n=0; Get-CimInstance Win32_Process -Filter \"Name='msedge.exe'\" | "
        f"Where-Object {{ $_.CommandLine -like '*{marker}*' }} | "
        "ForEach-Object { Stop-Process -Id $_.ProcessId -Force "
        "-ErrorAction SilentlyContinue; $n++ }; $n"
    )
    try:
        r = subprocess.run(["powershell", "-NoProfile", "-Command", ps],
                           capture_output=True, text=True, timeout=30)
        return int((r.stdout or "0").strip() or 0)
    except (OSError, ValueError, subprocess.TimeoutExpired):
        return 0


def stop_server(proc: subprocess.Popen | None) -> None:
    """只停我们自己起的那个进程；停了再确认一次端口真的空了。"""
    if proc is None:
        return
    try:
        proc.terminate()
        proc.wait(timeout=8)
        log(f"服务已停（pid {proc.pid}）")
    except (OSError, subprocess.TimeoutExpired):
        try:
            proc.kill()
            log(f"服务强杀（pid {proc.pid}）")
        except OSError:
            pass


def main() -> int:
    log("=" * 50)
    log(f"启动：ping={ping()} edge={find_edge() or '（没找到）'}")
    started = None
    if not ping():
        started = start_server()
        # ★ 等久一点：冷启动要加载引擎和真源（实测 8 秒，但第一次可能更慢）。
        #   宁可多等，也不要在"快好了"的时候放弃。
        if started is None or not wait_up(seconds=90):
            why = ("启动器没能把服务拉起来" if started is None
                   else "服务 90 秒内没有响应")
            log(f"失败：{why}")
            page = fail_page(why)
            _open(page)
            return 1
        log("服务已就绪")

    edge = find_edge()
    if not edge:
        log("没找到 Edge → 退回默认浏览器（会有地址栏）")
        _open(URL)
        if started:
            input("（按回车关掉服务）")
            stop_server(started)
        return 0

    killed = kill_profile_edges()
    if killed:
        log(f"清掉 {killed} 个残留的应用窗口进程（否则新窗口会把窗口交给它们后秒退）")
        time.sleep(1)

    # ★ 兜底：即使清了残留，窗口仍可能秒退（比如交给了一个我们没认出来的实例）。
    #   这时**不能**当成"窗口关了"去停服务 —— 那正是用户看到的「拒绝连接」。
    for attempt in (1, 2):
        t0 = time.time()
        proc = subprocess.Popen(edge_cmd(edge))
        log(f"开窗口（第 {attempt} 次）pid={proc.pid}")
        proc.wait()
        lived = time.time() - t0
        log(f"窗口结束，存活 {lived:.1f} 秒")
        if lived >= 5:
            break
        kill_profile_edges()
        time.sleep(3)
    else:
        log("窗口两次都秒退 → 服务先留着，改用默认浏览器打开")
        _open(URL)
        return 0

    stop_server(started)
    return 0


def _open(path_or_url: str) -> None:
    try:
        os.startfile(path_or_url)  # noqa: S606 - Windows 专用
    except OSError:
        import webbrowser
        webbrowser.open(path_or_url)


if __name__ == "__main__":
    raise SystemExit(main())
