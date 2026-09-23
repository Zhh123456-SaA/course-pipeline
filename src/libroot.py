# -*- coding: utf-8 -*-
"""学习库根目录的解析 —— 一处定义，程序与测试共用。

为什么单独拎出来：库的位置是**用户的决定**，不该埋在代码的相对路径里。
最早写死成 `../学习库`（即 `D:\\deepseek harness\\学习库`），
也就是把讲义和 Obsidian 笔记塞在**插件开发工作区内部** —— 位置本身就摆错了。

解析优先级（先命中先用）：

1. 环境变量 `COURSE_LIB`（测试用它把产物导进临时沙箱）
2. 程序目录下的 `library.path` 文件（一行路径，**本机配置，不入库**）
3. 兜底：`<程序目录>/../学习库`（保持仓库克隆下来就能跑）

把库放到别处之后，`library.path` 里写一行即可，不用改代码、不用每次设环境变量。
"""
from __future__ import annotations

import os

#: 配置文件的名字（放在程序目录下）
CONFIG_NAME = "library.path"

#: 环境变量名
ENV_NAME = "COURSE_LIB"


def config_file(project_dir: str) -> str:
    return os.path.join(project_dir, CONFIG_NAME)


def read_config(project_dir: str) -> str:
    """读 `library.path` 里的路径；没有/空/注释行 → 返回 ""。"""
    p = config_file(project_dir)
    if not os.path.isfile(p):
        return ""
    try:
        with open(p, encoding="utf-8-sig") as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#"):
                    return line
    except OSError:
        return ""
    return ""


def resolve(project_dir: str) -> str:
    """按优先级解析出学习库根目录（绝对路径）。"""
    v = os.environ.get(ENV_NAME)
    if v and v.strip():
        return os.path.abspath(v.strip())
    c = read_config(project_dir)
    if c:
        # 支持相对路径（相对程序目录），但推荐写绝对路径
        return os.path.abspath(os.path.join(project_dir, c)
                               if not os.path.isabs(c) else c)
    return os.path.abspath(os.path.join(project_dir, "..", "学习库"))


def write_config(project_dir: str, path: str) -> str:
    """把库位置写进 `library.path`，返回写好的文件路径。"""
    p = config_file(project_dir)
    with open(p, "w", encoding="utf-8", newline="\n") as f:
        f.write("# 学习库（讲义 + Obsidian 笔记 + Anki 账本）的根目录。\n")
        f.write("# 本机配置，不入库。改这里即可换库位置，不用改代码。\n")
        f.write(os.path.abspath(path) + "\n")
    return p
