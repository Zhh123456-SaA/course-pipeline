# -*- coding: utf-8 -*-
"""归档通道：把 ppt-deepreader 的「框选追问」收进学习库账本。

背景
----
ppt-deepreader 的批注形态是：在讲义页图上框一块 → 问 AI → 得到解释 + LaTeX + 转写，
还可以继续追问。数据落在 `<项目>/.pdw_work/pages/<源文件完整 sha1>/annotations.json`，
而 `.pdw_work` 被 git 忽略 —— **删目录即丢**。

这一模块把它搬进学习库的账本（唯一真相源），于是：
  - 批注不再会丢；
  - 能在 Obsidian 笔记里按页显示（附精确出处：第几页 + 哪个框）；
  - 成为后续知识点 / Anki 卡的原料。

怎么知道一条批注属于哪一讲
--------------------------
源于源码事实：`extractor.py` 用 `sha1(源文件全部字节)` 当指纹，
`pageref.py` 用这个指纹做页图/批注目录名。
所以只要对学习库 `source/` 里的 PDF 算 sha1，就能和批注目录名对上。
（`uploads/` 里存着上传原件，是第二条匹配依据。）

依赖边界（对应 R14 + C-4）
------------------------
  - **代码**只依赖真源 `ppt-deepreader`（本模块目前甚至不需要 import 它）；
  - **批注数据**源可配置，默认指向用户实际在用的那份。
"""
from __future__ import annotations

import hashlib
import os
import shutil
from typing import Any, Iterable

from ledger import atomic_write_json, load_json

#: 批注数据源（用户实际在用的那份）。可被 --ann-root 覆盖。
DEFAULT_ANN_ROOTS = [
    r"D:\1\ppt-deepreader\.pdw_work",
    os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "ppt-deepreader", ".pdw_work")),
]

CROP_SUFFIX = "_crop.jpg"


# ---------------------------------------------------------------- 基础

def sha1_file(path: str, chunk: int = 1 << 20) -> str:
    h = hashlib.sha1()
    with open(path, "rb") as f:
        while True:
            b = f.read(chunk)
            if not b:
                break
            h.update(b)
    return h.hexdigest()


def scan_annotation_dirs(roots: Iterable[str]) -> dict[str, str]:
    """扫描批注源目录 → {源文件 sha1: 批注目录绝对路径}。

    目录名就是源文件的完整 sha1，所以不需要读文件内容就能建索引。
    """
    found: dict[str, str] = {}
    for root in roots:
        if not root or not os.path.isdir(root):
            continue
        pages = os.path.join(root, "pages")
        if not os.path.isdir(pages):
            continue
        for name in sorted(os.listdir(pages)):
            d = os.path.join(pages, name)
            if not os.path.isdir(d):
                continue
            if not os.path.exists(os.path.join(d, "annotations.json")):
                continue
            found.setdefault(name, d)
    return found


def build_source_index(dirs: Iterable[str]) -> dict[str, str]:
    """对给定目录里的文件算 sha1 → {sha1: 文件绝对路径}。

    用途：把批注目录的 sha1 反推成「是哪一份讲义」。
    """
    index: dict[str, str] = {}
    for d in dirs:
        if not d or not os.path.isdir(d):
            continue
        for fn in sorted(os.listdir(d)):
            p = os.path.join(d, fn)
            if not os.path.isfile(p):
                continue
            # 只认讲义类文件，跳过我们自己写出去的 HTML 报告
            if os.path.splitext(fn)[1].lower() not in (".pdf", ".pptx", ".docx", ".ppt", ".doc"):
                continue
            try:
                index.setdefault(sha1_file(p), p)
            except OSError:
                continue
    return index


def load_annotations(ann_dir: str) -> list[dict[str, Any]]:
    """读一个批注目录里的 annotations.json。坏文件返回空表（不炸整轮）。"""
    p = os.path.join(ann_dir, "annotations.json")
    data = load_json(p, default=[])
    if not isinstance(data, list):
        return []
    return [a for a in data if isinstance(a, dict)]


# ---------------------------------------------------------------- 归档

def archive_ledger_path(library_root: str) -> str:
    return os.path.join(library_root, ".ledger", "annotations.json")


def build_archive(library_root: str, ann_roots: Iterable[str],
                  extra_source_dirs: Iterable[str] = (),
                  match_only: bool = True) -> tuple[dict, list[str]]:
    """扫描批注源 → 返回 (归档数据, 报告行)。

    `match_only=True`（默认）：**只归档属于本课程的批注**（源文件 sha1 落在本课程
    `source/` 里）。不属于的只在报告里列出来，不写进本课程的账本。

    为什么必须这样（实测踩到）：`archive` 是按课程跑的，早先会把**所有**批注源的记录
    都塞进当前课程的账本 —— 于是用户那门生物课的批注被归到了「物理」下面，
    既渲染不出来（源文件不在物理的 source/），又污染了物理的账本。
    """
    report: list[str] = []
    ann_dirs = scan_annotation_dirs(ann_roots)

    # 建 sha1 → 文件 的索引：学习库的 source/ + 批注源的 uploads/
    src_dirs = [os.path.join(library_root, "source")]
    for r in ann_roots:
        src_dirs.append(os.path.join(r, "uploads"))
    src_dirs += list(extra_source_dirs)
    src_index = build_source_index(src_dirs)

    # 学习库里的讲次：源文件 sha1 → 讲次 id
    lecture_by_sha: dict[str, str] = {}
    lib_src = os.path.abspath(os.path.join(library_root, "source"))
    for sha, path in src_index.items():
        if os.path.dirname(path) == lib_src:
            lecture_by_sha[sha] = os.path.splitext(os.path.basename(path))[0]

    by_sha: dict[str, dict] = {}
    foreign: list[str] = []
    for sha, d in sorted(ann_dirs.items()):
        anns = load_annotations(d)
        if not anns:
            continue
        src_path = src_index.get(sha)
        lec = lecture_by_sha.get(sha)
        if match_only and not lec:
            foreign.append(f"{os.path.basename(src_path) if src_path else sha[:12]}"
                           f"（{len(anns)} 条）")
            continue
        by_sha[sha] = {
            "sha1": sha,
            "ann_dir": d,
            "source_file": os.path.basename(src_path) if src_path else "",
            "lecture_id": lec,
            "count": len(anns),
            "pages": sorted({int(a.get("page", 0)) for a in anns}),
            "annotations": anns,
        }
        report.append(f"[scan ] {sha[:12]}  {len(anns)} 条批注  → {lec}")
    if foreign:
        report.append(f"[other] 有 {len(foreign)} 组批注**不属于本课程**，未入库："
                      + "、".join(foreign[:5])
                      + ("…" if len(foreign) > 5 else "")
                      + "。把对应素材放进 source/ 再跑本命令即可接管。")

    data = {"version": 1, "by_sha1": by_sha}
    return data, report


def copy_crops(archive: dict, library_root: str, slug_by_lecture: dict[str, str]) -> int:
    """把批注用到的裁剪图复制进学习库 assets，返回复制张数。

    源数据里每页只有一张 crop（`pNNN_crop.jpg`，同一页多条批注共用最后一张），
    这是 ppt-deepreader 的既有行为，这里如实搬运、不掩盖。
    """
    n = 0
    for sha, rec in archive["by_sha1"].items():
        slug = slug_by_lecture.get(rec.get("lecture_id") or "")
        if not slug:
            continue
        dst_dir = os.path.join(library_root, "assets", slug, "ann")
        os.makedirs(dst_dir, exist_ok=True)
        for a in rec["annotations"]:
            page = int(a.get("page", 0))
            src = os.path.join(rec["ann_dir"], f"p{page:03d}{CROP_SUFFIX}")
            if not os.path.exists(src):
                # 有的版本用不同命名，退一步扫一下
                cands = [f for f in os.listdir(rec["ann_dir"])
                         if f.endswith(CROP_SUFFIX) and f.startswith(f"p{page:03d}")]
                if not cands:
                    a["crop_local"] = ""
                    continue
                src = os.path.join(rec["ann_dir"], cands[0])
            dst = os.path.join(dst_dir, f"p{page:03d}.jpg")
            if not (os.path.exists(dst) and os.path.getsize(dst) == os.path.getsize(src)):
                shutil.copyfile(src, dst)
            a["crop_local"] = f"p{page:03d}.jpg"
            n += 1
    return n


def save_archive(library_root: str, archive: dict) -> None:
    atomic_write_json(archive_ledger_path(library_root), archive)


def load_archive(library_root: str) -> dict:
    d = load_json(archive_ledger_path(library_root), None)
    return d if d else {"version": 1, "by_sha1": {}}


# ---------------------------------------------------------------- 渲染

def annotations_for_lecture(archive: dict, lecture_id: str) -> list[dict]:
    """取某一讲的全部批注（按页、按序号排序）。"""
    out: list[dict] = []
    for rec in archive.get("by_sha1", {}).values():
        if rec.get("lecture_id") == lecture_id:
            out.extend(rec["annotations"])
    return sorted(out, key=lambda a: (int(a.get("page", 0)), int(a.get("no", 0))))


def render_annotation_md(ann: dict, slug: str) -> str:
    """一条批注 → markdown。"""
    out: list[str] = []
    q = (ann.get("question") or "").strip()
    expl = (ann.get("explanation") or "").strip()
    latex = (ann.get("latex") or "").strip()
    tr = (ann.get("transcript") or "").strip()

    if tr:
        out.append(f"> **原文**：{tr}")
        out.append("")
    if q:
        out.append(f"**❓ 我的问题**：{q}")
    else:
        out.append("**❓ 这一块讲什么**")
    out.append("")
    if expl:
        out.append(expl)
        out.append("")
    if latex:
        out.append("**公式**：")
        out.append("")
        out.append(f"$${latex}$$")
        out.append("")
    if ann.get("crop_local"):
        out.append(f"![框选区域](../assets/{slug}/ann/{ann['crop_local']})")
        out.append("")
    for t in ann.get("thread", []) or []:
        if not isinstance(t, dict):
            continue
        out.append(f"**↳ 追问**：{t.get('q','')}")
        out.append("")
        out.append(str(t.get("a", "")).strip())
        out.append("")
    meta = []
    if ann.get("model"):
        meta.append(f"模型 `{ann['model']}`")
    if ann.get("created_at"):
        meta.append(ann["created_at"])
    if ann.get("tokens"):
        meta.append(f"{ann['tokens']} token")
    if meta:
        out.append(f"*（{' · '.join(meta)}）*")
    return "\n".join(out).rstrip()
