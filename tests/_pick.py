# -*- coding: utf-8 -*-
"""测试共用：挑一门「可用的课程」，而不是把某门课写死。

为什么要有这个：测试原来把课程名写死（`COURSE = "普通化学"`）。
好处是确定，坏处是**那门课一旦不在，一批测试就集体崩**，
而且逼着人为了跑测试去造一门课。真实事故：普通化学目录从库中消失后，
s1_idempotent / test_annotation 直接退出码 2，全量回归跑不完整。

现在：优先用你指定的课，其次按「素材 + 账本 + 笔记都齐」自动挑一门。
挑不到就明确跳过（返回空），不假装通过。
"""
from __future__ import annotations

import os

#: 优先顺序 —— 这些课在就优先用（结果稳定，日志好对比）
PREFERRED = ("普通化学", "物理", "生物")


def library_root(proj: str) -> str:
    """知识库根目录（与 run.py 的 course_root 同一套规则）。"""
    return os.path.abspath(os.environ.get("COURSE_LIB")
                           or os.path.join(proj, "..", "学习库"))


def _ready(root: str, course: str) -> str | None:
    """这门课是否「齐活」：有素材、有账本页、有渲染出的讲次笔记。"""
    d = os.path.join(root, course)
    notes = os.path.join(d, "notes")
    pages = os.path.join(d, ".ledger", "pages")
    if not (os.path.isdir(os.path.join(d, "source"))
            and os.path.isdir(pages) and os.listdir(pages)
            and os.path.isdir(notes)):
        return None
    stems = [f[:-3] for f in os.listdir(notes)
             if f.endswith(".md") and not f.startswith("_")]
    return stems[0] if stems else None


def cleanup_sandbox(sandbox: str) -> None:
    """删掉沙箱，并顺手收走空了的外层目录（否则 TEMP 里会堆一地空壳）。"""
    import shutil
    shutil.rmtree(sandbox, ignore_errors=True)
    parent = os.path.dirname(os.path.abspath(sandbox))
    try:
        if os.path.isdir(parent) and not os.listdir(parent):
            os.rmdir(parent)
    except OSError:
        pass


def pick(proj: str, prefer: str | None = None) -> tuple[str, str, str] | None:
    """挑一门可用课程。

    返回 `(课程名, 课程目录, 首个讲次笔记的文件名主干)`；挑不到返回 None。
    """
    root = library_root(proj)
    order = ([prefer] if prefer else []) + [c for c in PREFERRED if c != prefer]
    seen = set(order)
    if os.path.isdir(root):
        order += [d for d in sorted(os.listdir(root))
                  if d not in seen and os.path.isdir(os.path.join(root, d))
                  and not d.startswith(".")]
    for course in order:
        if not course:
            continue
        stem = _ready(root, course)
        if stem:
            return course, os.path.join(root, course), stem
    return None
