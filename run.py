# -*- coding: utf-8 -*-
"""课程流水线入口（S1：讲义 PDF → 账本 → 笔记）。

用法：
    python run.py --course <课程名> ingest     # 扫描 source\\*.pdf → 账本 + 页图
    python run.py --course <课程名> render     # 账本 → notes\\*.md
    python run.py --course <课程名> all        # 两步都做
    python run.py --course <课程名> check      # 体检：账本/图片/笔记 一致性

设计要点：
  - `source\\` 只读；账本 `.ledger\\` 是唯一真相源；`notes\\`、`assets\\` 可删可重建
  - 源 PDF 指纹没变就跳过重算（缓存）；页图缺了就补渲染
"""
from __future__ import annotations

import argparse
import glob
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "vendor"))
sys.path.insert(0, os.path.join(HERE, "src"))

try:  # 控制台是 GBK，中文会炸 —— 这里兜个底，保证不崩
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

from ledger import Ledger, atomic_write_text, sha256_file  # noqa: E402
import pdf_source  # noqa: E402
import render  # noqa: E402

LIBRARY_ROOT = os.path.abspath(os.path.join(HERE, "..", "学习库"))


def course_root(course: str) -> str:
    return os.path.join(LIBRARY_ROOT, course)


def ledger_of(course: str) -> Ledger:
    return Ledger(os.path.join(course_root(course), ".ledger"))


def images_ok(assets_dir: str, page_count: int) -> bool:
    if not os.path.isdir(assets_dir):
        return False
    have = len([f for f in os.listdir(assets_dir) if f.endswith(".png")])
    return have >= page_count


# ---------------------------------------------------------------- ingest

def cmd_ingest(course: str, force: bool = False, images: bool = True,
               scale: float = 1.6) -> list[str]:
    root = course_root(course)
    src_dir = os.path.join(root, "source")
    if not os.path.isdir(src_dir):
        raise SystemExit(f"找不到素材目录：{src_dir}\n请把讲义 PDF 放进这个文件夹。")

    led = ledger_of(course)
    sources = led.load_sources()
    report: list[str] = []

    pdfs = sorted(glob.glob(os.path.join(src_dir, "*.pdf")))
    if not pdfs:
        raise SystemExit(f"{src_dir} 里没有 PDF。")

    for pdf in pdfs:
        stem = os.path.splitext(os.path.basename(pdf))[0]
        slug = pdf_source.slugify(stem)
        digest = sha256_file(pdf)
        rec = sources["sources"].get(stem)
        assets_dir = os.path.join(root, "assets", slug)

        unchanged = (rec is not None and rec.get("sha256") == digest
                     and led.load_pages(stem) is not None)
        need_imgs = images and not images_ok(assets_dir, rec.get("page_count", 0) if rec else 0)

        if unchanged and not force and not need_imgs:
            report.append(f"[skip] {stem}  源未变化（{rec['page_count']} 页）")
            continue

        data = pdf_source.ingest(pdf, images_dir=assets_dir, scale=scale,
                                 render_images=images)
        data["id"] = stem
        data["slug"] = slug
        data["file"] = os.path.basename(pdf)
        data["sha256"] = digest
        led.save_pages(stem, data)

        sources["sources"][stem] = {
            "file": os.path.basename(pdf),
            "slug": slug,
            "sha256": digest,
            "page_count": data["page_count"],
            "boilerplate": data["boilerplate"],
            "running_head": data["running_head"],
        }
        report.append(f"[ingest] {stem}  {data['page_count']} 页，"
                      f"剥掉页眉行 {sum(p['lines_removed'] for p in data['pages'])} 处")

    led.save_sources(sources)
    return report


# ---------------------------------------------------------------- render

def cmd_render(course: str, images: bool = True) -> list[str]:
    root = course_root(course)
    led = ledger_of(course)
    sources = led.load_sources()
    if not sources["sources"]:
        raise SystemExit("账本是空的，先跑 ingest。")

    notes_dir = os.path.join(root, "notes")
    os.makedirs(notes_dir, exist_ok=True)
    report: list[str] = []
    index_items: list[dict] = []

    for stem in sorted(sources["sources"]):
        data = led.load_pages(stem)
        if data is None:
            report.append(f"[warn] {stem} 账本缺 pages 记录，跳过")
            continue
        body = render.render_lecture_body(course, data, include_images=images)
        note_name = f"{stem}.md"
        title = stem
        if data.get("running_head"):
            title = f"{stem} · {data['running_head']}"
        render.write_note(os.path.join(notes_dir, note_name), title, body)
        index_items.append({
            "title": title,
            "note_name": note_name,
            "page_count": data["page_count"],
            "running_head": data.get("running_head", ""),
        })
        report.append(f"[note] {note_name}  {data['page_count']} 页")

    render.write_note(
        os.path.join(notes_dir, "_课程索引.md"),
        f"{course} · 课程索引",
        render.render_index_body(course, index_items),
    )
    report.append(f"[note] _课程索引.md  {len(index_items)} 讲")
    return report


# ---------------------------------------------------------------- check

def cmd_check(course: str) -> list[str]:
    root = course_root(course)
    led = ledger_of(course)
    sources = led.load_sources()
    out: list[str] = []
    out.append(f"课程目录：{root}")
    out.append(f"账本讲数：{len(sources['sources'])}")
    for stem in sorted(sources["sources"]):
        rec = sources["sources"][stem]
        data = led.load_pages(stem)
        note = os.path.join(root, "notes", f"{stem}.md")
        slug = rec.get("slug", pdf_source.slugify(stem))
        imgs = os.path.join(root, "assets", slug)
        n_img = len([f for f in os.listdir(imgs) if f.endswith(".png")]) if os.path.isdir(imgs) else 0
        ok_pages = "OK " if data else "MISS"
        ok_note = "OK " if os.path.exists(note) else "MISS"
        ok_img = "OK " if n_img >= rec["page_count"] else "MISS"
        out.append(f"  {stem:16s} pages={ok_pages} note={ok_note} "
                   f"images={ok_img}({n_img}/{rec['page_count']})")
    return out


# ---------------------------------------------------------------- clean

def cmd_clean(course: str) -> list[str]:
    """删掉 assets/ 与 notes/。

    这两个目录按铁律 1 是「可删可重建」的：内容全部来自账本。
    图片目录名规则变过（空格 → 连字符）时会留下孤儿目录，用这个清掉。
    **绝不碰 source/ 与 .ledger/**。
    """
    import shutil
    root = course_root(course)
    out: list[str] = []
    for sub in ("assets", "notes"):
        p = os.path.join(root, sub)
        if os.path.isdir(p):
            n = sum(len(f) for _d, _s, f in os.walk(p))
            shutil.rmtree(p)
            out.append(f"[clean] 删除 {sub}/（{n} 个文件），重跑可无损重建")
        else:
            out.append(f"[clean] {sub}/ 不存在，跳过")
    return out


# ---------------------------------------------------------------- main

def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="课程流水线：讲义 PDF → 账本 → 笔记")
    ap.add_argument("--course", required=True, help="课程名（对应 学习库\\<课程名>）")
    ap.add_argument("--no-images", action="store_true", help="不渲染页图")
    ap.add_argument("--scale", type=float, default=1.6, help="页图缩放（默认 1.6）")
    ap.add_argument("--force", action="store_true", help="忽略缓存，强制重算")
    ap.add_argument("--report", default=None, help="把报告写到这个 UTF-8 文件")
    ap.add_argument("action", choices=["ingest", "render", "all", "check", "clean"])
    args = ap.parse_args(argv)

    lines: list[str] = []
    if args.action == "clean":
        lines += cmd_clean(args.course)
    if args.action in ("ingest", "all"):
        lines += cmd_ingest(args.course, force=args.force,
                            images=not args.no_images, scale=args.scale)
    if args.action in ("render", "all"):
        lines += cmd_render(args.course, images=not args.no_images)
    if args.action == "check":
        lines += cmd_check(args.course)

    text = "\n".join(lines) + "\n"
    if args.report:
        atomic_write_text(args.report, text)
    print(text, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
