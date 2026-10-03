# -*- coding: utf-8 -*-
"""装「学习库」桌面应用：生成图标 + 建桌面快捷方式。

用户原话：「那就做成桌面快捷窗口就行」。这个脚本**只做一次性安装**，
平时你双击的是它建出来的那个桌面图标。

设计取舍（为什么不打包成 .exe）：
  打包 Electron/Tauri 要 2~3 天、100~200MB，而且它做的事就是"套一个浏览器
  窗口指向 127.0.0.1:8021" —— 而这套东西的铁律是**零构建、账本可重建**。
  所以这里走"启动器 + 无地址栏窗口"，成本一小时，体验拿到 85 分。

用法：python scripts\\install_app.py
"""
from __future__ import annotations

import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
# Pillow 在项目自带的 vendor/ 里（本机规矩：依赖用 scripts\setup_vendor.py 解包，
# 不装进系统 Python），所以要先把 vendor 加进 sys.path。
sys.path.insert(0, os.path.join(ROOT, "vendor"))
ICON = os.path.join(ROOT, "学习库.ico")
LAUNCHER = os.path.join(HERE, "launch_app.py")
NAME = "学习库"

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass


def make_icon(path: str = ICON) -> str:
    """画一个图标：深底 + 绿框 + 「学」字。

    为什么自己画：图标要能一眼认出来是"学习"，而 Windows 没有现成的。
    中文字形靠系统字体（msyh.ttc），拿不到就退化成纯几何图形（宁可丑，不能没有）。
    """
    from PIL import Image, ImageDraw, ImageFont
    size = 256
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle([8, 8, size - 8, size - 8], radius=48,
                        fill=(22, 24, 28, 255), outline=(47, 111, 78, 255), width=10)
    # 一本书的形状（底部）
    d.rounded_rectangle([52, 168, 204, 206], radius=10, fill=(127, 201, 160, 255))
    font = None
    for cand in (r"C:\Windows\Fonts\msyh.ttc", r"C:\Windows\Fonts\msyhbd.ttc",
                 r"C:\Windows\Fonts\simhei.ttf"):
        if os.path.isfile(cand):
            try:
                font = ImageFont.truetype(cand, 130)
                break
            except OSError:
                continue
    if font is not None:
        d.text((size // 2, 96), "学", font=font, fill=(232, 230, 227, 255),
               anchor="mm")
    else:
        d.ellipse([78, 46, 178, 146], outline=(232, 230, 227, 255), width=10)
    img.save(path, sizes=[(16, 16), (32, 32), (48, 48), (64, 64), (128, 128),
                          (256, 256)])
    return path


def make_shortcut(desktop: str, target: str, args: str, icon: str,
                  workdir: str = ROOT) -> str:
    """建桌面快捷方式（.lnk 只能靠 COM 建）。

    **注意引号**：.lnk 的参数里带空格的路径必须自己加引号，否则 Windows 会
    把它拆成两个参数 —— 而 `pythonw` 会拿第一个当脚本名，直接报"找不到文件"。
    （本文件路径里就有空格：`D:\\deepseek harness\\...`。）
    """
    lnk = os.path.join(desktop, f"{NAME}.lnk")
    ps = (
        "$ws = New-Object -ComObject WScript.Shell; "
        f'$s = $ws.CreateShortcut("{lnk}"); '
        f'$s.TargetPath = "{target}"; '
        f"$s.Arguments = '{args}'; "
        f'$s.WorkingDirectory = "{workdir}"; '
        f'$s.IconLocation = "{icon}"; '
        f'$s.Description = "学习库 —— 看课件、问 AI、复习"; '
        "$s.Save(); "
        f'if (Test-Path "{lnk}") {{ "OK" }} else {{ "FAIL" }}'
    )
    r = subprocess.run(["powershell", "-NoProfile", "-Command", ps],
                       capture_output=True, text=True, encoding="utf-8",
                       errors="replace")
    if "OK" not in (r.stdout or ""):
        raise SystemExit(f"快捷方式没建起来：{(r.stdout or '') + (r.stderr or '')}")
    return lnk


def desktop_dir() -> str:
    r = subprocess.run(
        ["powershell", "-NoProfile", "-Command",
         "[Environment]::GetFolderPath('Desktop')"],
        capture_output=True, text=True, encoding="utf-8", errors="replace")
    d = (r.stdout or "").strip()
    return d if d and os.path.isdir(d) else os.path.expanduser("~/Desktop")


def main() -> int:
    icon = make_icon()
    print(f"图标：{icon}")
    target = os.path.join(os.path.dirname(sys.executable), "pythonw.exe")
    if not os.path.isfile(target):
        target = sys.executable
    lnk = make_shortcut(desktop_dir(), target, f'"{LAUNCHER}"', icon)
    print(f"桌面快捷方式：{lnk}")
    print(f"  指向：{target} \"{LAUNCHER}\"")
    print("双击它就会：静默起服务 → 开一个没有地址栏的窗口 → 关掉窗口自动停服务。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
