# -*- coding: utf-8 -*-
"""用 **GBK** 写 `启动学习库.bat`。

★ 为什么必须 GBK（真实事故，用户截图报的 bug）：
cmd.exe 按**系统 ANSI 码页**（简中 = GBK/936）解析 .bat 文件。
我第一版把 .bat 写成了 UTF-8，于是每一行中文都变成乱码，
**而且乱码行被 cmd 当成命令去执行** —— 报

    '?鏍囪瘑' 不是内部或外部命令…
    '€?python' 不是内部或外部命令…

第二条说明连 `python run.py serve` 那一行都被前面的乱码啃掉了，
**服务压根没起来**，用户看到的就是"连不上"。
`chcp 65001` 救不了：cmd 是**先解析、后执行**的。
"""
import io
import os

HERE = os.path.dirname(os.path.abspath(__file__))
# ★ 必须放在**项目根**（跟 run.py 并排）—— .bat 里是 `cd /d "%~dp0"` 再跑 run.py，
#   放 scripts/ 里就会 cd 到 scripts/ 然后找不到 run.py（第一版就写错了）
OUT = os.path.join(os.path.dirname(HERE), "启动学习库.bat")

LINES = [
    "@echo off",
    "chcp 936 >nul",
    'cd /d "%~dp0"',
    "title 学习库",
    "",
    "echo ============================================================",
    "echo   学习库 —— 一键启动",
    "echo ============================================================",
    "echo.",
    "echo   起来后会自动打开浏览器，在里面：",
    "echo     - 看课件、拖框标记、框上提问",
    "echo     - 提问会先反问你，不直接给答案（阶梯式）",
    "echo     - 标记 / 提问 / 自评 直接进账本，不用再导出再导入",
    "echo.",
    "echo   关掉这个窗口就是停止服务。",
    "echo ============================================================",
    "echo.",
    "",
    "python run.py serve --port 8021 --open-browser",
    "set RC=%errorlevel%",
    "",
    'if not "%RC%"=="0" (',
    "  echo.",
    "  echo [出错] 服务没能起来，退出码 %RC%。常见原因：",
    "  echo   1) 没装 Python，或者 python 不在 PATH 上",
    "  echo   2) 8021 端口被占用 —— 换个端口试试：",
    "  echo        python run.py serve --port 8022 --open-browser",
    "  echo.",
    ")",
    "pause",
    "",
]

txt = "\r\n".join(LINES)
with io.open(OUT, "w", encoding="gbk", newline="") as f:
    f.write(txt)

# 自检：按 GBK 解回来必须逐字一致，且关键那行必须在。
# ★ 读回时必须也传 newline=""：默认的通用换行会把 \r\n 折成 \n，
#   于是比对假失败（第一版就这么被自己绊了一下）。
back = io.open(OUT, encoding="gbk", newline="").read()
assert back == txt, "GBK 往返不一致"
assert "python run.py serve" in back, "关键命令行没了"
assert "echo" in back

raw = open(OUT, "rb").read()
print(f"已写 {OUT}")
print(f"  {len(raw)} 字节 · 按 GBK 可完整解回 · 关键命令行在")
print(f"  前 3 字节 {list(raw[:3])}（UTF-8 BOM 会是 [239,187,191] —— 这里必须是 ASCII 的 @ec）")
for ln in txt.split("\r\n")[:4]:
    print("   |", ln)
