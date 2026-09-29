@echo off
chcp 936 >nul
cd /d "%~dp0"
title 学习库

echo ============================================================
echo   学习库 —— 一键启动
echo ============================================================
echo.
echo   起来后会自动打开浏览器，在里面：
echo     - 看课件、拖框标记、框上提问
echo     - 提问会先反问你，不直接给答案（阶梯式）
echo     - 标记 / 提问 / 自评 直接进账本，不用再导出再导入
echo.
echo   关掉这个窗口就是停止服务。
echo ============================================================
echo.

python run.py serve --port 8021 --open-browser
set RC=%errorlevel%

if not "%RC%"=="0" (
  echo.
  echo [出错] 服务没能起来，退出码 %RC%。常见原因：
  echo   1) 没装 Python，或者 python 不在 PATH 上
  echo   2) 8021 端口被占用 —— 换个端口试试：
  echo        python run.py serve --port 8022 --open-browser
  echo.
)
pause
