# -*- coding: utf-8 -*-
"""学习库根目录解析的测试。

为什么值得单独测：这个"库在哪"的解析一旦错了，后果是**程序写到别的地方去**，
或者更糟 —— 测试以为在看真实库，其实在看空气（所有断言静默通过）。

真实事故：最早写死成 `../学习库`，于是讲义和 Obsidian 笔记被放在
**插件开发工作区内部**。用户要求挪到 D 盘独立目录，代码里就必须有一个
正经的"库位置"配置，而不是散落各处的相对路径拼接。

跑法：python tests/test_libroot.py
"""
from __future__ import annotations

import os
import shutil
import sys
import tempfile

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

HERE = os.path.dirname(os.path.abspath(__file__))
PROJ = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(PROJ, "src"))
import libroot  # noqa: E402

FAILS: list[str] = []
PASSES: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    (PASSES if ok else FAILS).append(f"{name}{(' — ' + detail) if detail else ''}")


ROOT = os.path.join(tempfile.gettempdir(), "course-pipeline-libroot", str(os.getpid()))


def _cleanup() -> None:
    shutil.rmtree(ROOT, ignore_errors=True)
    parent = os.path.dirname(ROOT)
    try:
        if os.path.isdir(parent) and not os.listdir(parent):
            os.rmdir(parent)
    except OSError:
        pass


import atexit  # noqa: E402
atexit.register(_cleanup)

# 伪装成两个"程序目录"
P1 = os.path.join(ROOT, "proj1")
P2 = os.path.join(ROOT, "proj2")
os.makedirs(P1, exist_ok=True)
os.makedirs(P2, exist_ok=True)

_SAVED = os.environ.pop(libroot.ENV_NAME, None)


def restore_env() -> None:
    if _SAVED is None:
        os.environ.pop(libroot.ENV_NAME, None)
    else:
        os.environ[libroot.ENV_NAME] = _SAVED


atexit.register(restore_env)

# ---------------------------------------------------------------- 1 兜底

os.environ.pop(libroot.ENV_NAME, None)
check("没有配置也没有环境变量 → 退回 <程序目录>/../学习库",
      libroot.resolve(P1) == os.path.abspath(os.path.join(P1, "..", "学习库")),
      libroot.resolve(P1))

# ---------------------------------------------------------------- 2 配置文件

libroot.write_config(P1, r"D:\学习库")
check("配置文件被写出来了", os.path.isfile(libroot.config_file(P1)))
check("写了配置就读配置", libroot.resolve(P1) == os.path.abspath(r"D:\学习库"),
      libroot.resolve(P1))
check("  没写配置的那个程序目录不受影响（各用各的）",
      libroot.resolve(P2) == os.path.abspath(os.path.join(P2, "..", "学习库")))

# 注释与空行要能容忍；配置是**人手改的**文件，不能一碰就崩
with open(libroot.config_file(P1), "w", encoding="utf-8", newline="\n") as f:
    f.write("# 注释行\n\n   \nD:\\另一处\\学习库\n")
check("配置文件里的注释/空行被跳过",
      libroot.resolve(P1) == os.path.abspath(r"D:\另一处\学习库"),
      libroot.resolve(P1))

# 记事本/VS Code 存 UTF-8 带 BOM 是常态
with open(libroot.config_file(P1), "w", encoding="utf-8-sig", newline="\n") as f:
    f.write(r"D:\带BOM的库")
check("带 BOM 的配置文件也能读（记事本存的）",
      libroot.resolve(P1) == os.path.abspath(r"D:\带BOM的库"),
      libroot.resolve(P1))

# 相对路径：按"相对程序目录"理解
with open(libroot.config_file(P1), "w", encoding="utf-8", newline="\n") as f:
    f.write("我的库\n")
check("相对路径按程序目录解析",
      libroot.resolve(P1) == os.path.abspath(os.path.join(P1, "我的库")),
      libroot.resolve(P1))

# ---------------------------------------------------------------- 3 环境变量优先

with open(libroot.config_file(P1), "w", encoding="utf-8", newline="\n") as f:
    f.write(r"D:\配置里的库")
os.environ[libroot.ENV_NAME] = os.path.join(ROOT, "沙箱库")
check("环境变量优先于配置文件（测试就是靠它导沙箱）",
      libroot.resolve(P1) == os.path.abspath(os.path.join(ROOT, "沙箱库")),
      libroot.resolve(P1))
os.environ.pop(libroot.ENV_NAME, None)
check("  拿掉环境变量又回到配置文件",
      libroot.resolve(P1) == os.path.abspath(r"D:\配置里的库"))

# 空的环境变量不算数（有些工具会设个空串）
os.environ[libroot.ENV_NAME] = "   "
check("环境变量是空白串时忽略它",
      libroot.resolve(P1) == os.path.abspath(r"D:\配置里的库"),
      libroot.resolve(P1))
os.environ.pop(libroot.ENV_NAME, None)

# ---------------------------------------------------------------- 4 与真实程序一致

# 这条是**命门**：测试看到的库必须和程序写的库是同一个。
# 两处各写一份解析逻辑必然漂移，所以测试侧直接复用 src/libroot.py。
sys.path.insert(0, PROJ)
import run as R  # noqa: E402
check("run.py 与测试用的是同一套解析（不会各看各的库）",
      os.path.normcase(R.library_root()) == os.path.normcase(libroot.resolve(PROJ)),
      f"{R.library_root()} vs {libroot.resolve(PROJ)}")

# ---------------------------------------------------------------- 汇总

for p in PASSES:
    print("PASS  " + p)
for f_ in FAILS:
    print("FAIL  " + f_)
print("=" * 60)
print(f"通过 {len(PASSES)} / 失败 {len(FAILS)}")
raise SystemExit(1 if FAILS else 0)
