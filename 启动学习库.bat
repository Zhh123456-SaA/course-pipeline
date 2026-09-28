@echo off
chcp 65001 >nul
cd /d "%~dp0"
title 学习库

echo ============================================================
echo   学习库 —— 一键启动
echo ============================================================
echo.
echo   起来之后会自动打开浏览器，在里面：
echo     · 看课件、拖框标记、框上提问（提问会先反问你，不直接给答案）
echo     · 标记 / 提问 / 自评 **直接进账本**，不用再导出再导入
echo.
echo   关掉这个黑窗口就是停止服务。
echo ============================================================
echo.

python run.py serve --port 8021 --open-browser

if errorlevel 1 (
  echo.
  echo [出错] 服务没能起来。常见原因：
  echo   1) 没装 Python，或者 python 不在 PATH 上
  echo   2) 8021 端口被占用 —— 换个端口：python run.py serve --port 8022
  echo.
)
pause
