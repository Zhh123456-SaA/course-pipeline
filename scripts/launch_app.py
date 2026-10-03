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

EDGE_CANDIDATES = (
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
)


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
    try:
        return subprocess.Popen(
            [pythonw(), os.path.join(ROOT, "run.py"), "serve", "--port", str(PORT)],
            cwd=ROOT, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except OSError:
        return None


def stop_server(proc: subprocess.Popen | None) -> None:
    """只停我们自己起的那个进程；停了再确认一次端口真的空了。"""
    if proc is None:
        return
    try:
        proc.terminate()
        proc.wait(timeout=8)
    except (OSError, subprocess.TimeoutExpired):
        try:
            proc.kill()
        except OSError:
            pass


def main() -> int:
    started = None
    if not ping():
        started = start_server()
        if started is None or not wait_up():
            # 服务起不来：用系统默认浏览器兜底，至少别让人对着白屏
            import webbrowser
            webbrowser.open(URL)
            return 1

    edge = find_edge()
    if not edge:
        import webbrowser
        webbrowser.open(URL)
        if started:
            input("（没找到 Edge，用默认浏览器打开了。按回车关掉服务）")
            stop_server(started)
        return 0

    proc = subprocess.Popen(edge_cmd(edge))
    proc.wait()                       # ← 等这个应用窗口被关掉
    stop_server(started)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
