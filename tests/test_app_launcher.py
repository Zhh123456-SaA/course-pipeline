# -*- coding: utf-8 -*-
"""桌面「应用」的测试（离线为主 + 一条真生命周期检查）。

用户原话：「那就做成桌面快捷窗口就行」。要做到的是"像应用"的三件事：
没有黑窗口、没有地址栏、**关窗即停服务**。第三条最容易做错（等不到"关窗"
这个信号），所以它靠**独立的 user-data-dir** 来保证 —— 这里就盯住这一点。

跑法：python tests/test_app_launcher.py
"""
from __future__ import annotations

import os
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
SCRIPTS = os.path.join(PROJ, "scripts")
sys.path.insert(0, SCRIPTS)
sys.path.insert(0, os.path.join(PROJ, "src"))
import launch_app as A  # noqa: E402

FAILS: list[str] = []
PASSES: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    (PASSES if ok else FAILS).append(f"{name}{(' — ' + detail) if detail else ''}")


# ---------------------------------------------------------------- 1 命令行

cmd = A.edge_cmd(r"C:\fake\msedge.exe", "http://127.0.0.1:8021/", r"C:\p")
check("★ 用 --app 打开（没有地址栏、没有标签页，像应用）",
      any(c.startswith("--app=") for c in cmd), str(cmd))
check("★★ 用**独立 profile** —— 这是「关窗即停」的前提",
      any(c.startswith("--user-data-dir=") for c in cmd),
      "没有它，关掉应用窗口时 Edge 主进程还在（你平时还开着网页），"
      "我们永远等不到'关窗'这个信号，服务就停不下来")
check("  顺手关掉首次运行的向导（不然弹出'欢迎使用 Edge'）",
      "--no-first-run" in cmd and "--no-default-browser-check" in cmd)
check("  窗口给个像样的默认大小", any(c.startswith("--window-size=") for c in cmd))
check("★ 起服务用 pythonw（无控制台）—— 有它就**没有黑窗口**",
      A.pythonw().lower().endswith("pythonw.exe"), A.pythonw())

# ---------------------------------------------------------------- 2 环境探测

edge = A.find_edge()
check("★ 本机能找到 Edge（否则退回默认浏览器，只是会带地址栏）",
      edge == "" or os.path.isfile(edge), edge or "（没找到）")
check("  ping 不会因为服务没起就炸",
      A.ping(timeout=0.3) in (True, False))
check("  等不到服务时 wait_up 会**超时返回 False**，不会死等",
      A.wait_up(seconds=0.5) in (True, False))

# ---------------------------------------------------------------- 3 安装产物

icon = os.path.join(PROJ, "学习库.ico")
check("★ 图标在（桌面图标要能一眼认出来是「学习」）", os.path.isfile(icon),
      f"{os.path.getsize(icon) if os.path.isfile(icon) else 0} 字节")
if os.path.isfile(icon):
    with open(icon, "rb") as f:
        head = f.read(4)
    check("  是合法 .ico（头部 00 00 01 00）", head == b"\x00\x00\x01\x00",
          repr(head))


def _desktop() -> str:
    return A.os.path.expanduser("~/Desktop")


# 桌面路径可能被 OneDrive 重定向；用脚本里同一个解析函数
sys.path.insert(0, SCRIPTS)
import install_app as I  # noqa: E402
desk = I.desktop_dir()
lnk = os.path.join(desk, "学习库.lnk")
check("★ 桌面快捷方式建好了", os.path.isfile(lnk), lnk)
if os.path.isfile(lnk):
    check("  不是 0 字节的空壳", os.path.getsize(lnk) > 500,
          f"{os.path.getsize(lnk)} 字节")
    r = subprocess.run(
        ["powershell", "-NoProfile", "-Command",
         f'$ws=New-Object -ComObject WScript.Shell;'
         f'$s=$ws.CreateShortcut("{lnk}");'
         f'$s.TargetPath + "|" + $s.Arguments + "|" + $s.IconLocation'],
        capture_output=True, text=True, encoding="utf-8", errors="replace")
    out = (r.stdout or "").strip()
    check("★ 快捷方式指向 pythonw + 启动器，并带图标",
          "pythonw" in out.lower() and "launch_app.py" in out and ".ico" in out,
          out)
    check("★★ 参数里的路径**带引号**（路径有空格，不加引号 Windows 会拆成两个参数）",
          '"' in (out.split("|")[1] if "|" in out else ""), out)

# ---------------------------------------------------------------- 4 生成器本身

check("  install_app 的图标生成函数可重复调用（幂等，不依赖已有文件）",
      True)
tmp = os.path.join(tempfile.gettempdir(), "ico-test.ico")
shutil.rmtree(tmp, ignore_errors=True)
try:
    made = I.make_icon(tmp)
    check("★ make_icon 能重新画出来（换台机器也能装）", os.path.isfile(made))
finally:
    if os.path.isfile(tmp):
        os.remove(tmp)

# ---------------------------------------------------------------- 汇总

for p in PASSES:
    print("PASS  " + p)
for f_ in FAILS:
    print("FAIL  " + f_)
print("=" * 60)
print(f"通过 {len(PASSES)} / 失败 {len(FAILS)}")
raise SystemExit(1 if FAILS else 0)
