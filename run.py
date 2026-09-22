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
import archive  # noqa: E402
import cards as cards_mod  # noqa: E402
import engine  # noqa: E402
import kcs as kcs_mod  # noqa: E402
import pdf_source  # noqa: E402
import qa  # noqa: E402
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

    arch = archive.load_archive(root)
    kcs_data = kcs_mod.load_kcs(root)
    kcs_by_lecture = {c.get("id"): (c.get("kcs") or [])
                      for c in kcs_data.get("chapters", [])}

    for stem in sorted(sources["sources"]):
        data = led.load_pages(stem)
        if data is None:
            report.append(f"[warn] {stem} 账本缺 pages 记录，跳过")
            continue

        # 归档进来的框选追问，按页号分组后交给渲染器
        ann_by_page: dict[int, list[dict]] = {}
        for a in archive.annotations_for_lecture(arch, stem):
            ann_by_page.setdefault(int(a.get("page", 0)), []).append(a)

        lec_kcs = kcs_by_lecture.get(stem) or []
        body = render.render_lecture_body(course, data, include_images=images,
                                          annotations_by_page=ann_by_page,
                                          kcs=lec_kcs)
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
        ann_note = f"，含归档追问 {sum(len(v) for v in ann_by_page.values())} 条" if ann_by_page else ""
        kc_note = f"，{len(lec_kcs)} 个知识点" if lec_kcs else ""
        report.append(f"[note] {note_name}  {data['page_count']} 页{ann_note}{kc_note}")

    render.write_note(
        os.path.join(notes_dir, "_课程索引.md"),
        f"{course} · 课程索引",
        render.render_index_body(course, index_items),
    )
    report.append(f"[note] _课程索引.md  {len(index_items)} 讲")

    # 知识点总览（只在真有骨架时才写）
    chapters = [c for c in kcs_data.get("chapters", []) if c.get("kcs")]
    if chapters:
        render.write_note(
            os.path.join(notes_dir, "_知识点总览.md"),
            f"{course} · 知识点总览",
            render.render_overview_body(course, chapters),
        )
        total = sum(len(c.get("kcs") or []) for c in chapters)
        report.append(f"[note] _知识点总览.md  {total} 个知识点")
    return report


# ---------------------------------------------------------------- archive

def cmd_archive(course: str, ann_roots: list[str] | None = None) -> list[str]:
    """把 ppt-deepreader 的框选追问归档进学习库账本。

    数据源默认为用户实际在用的那份（D:\\1\\ppt-deepreader\\.pdw_work）；
    代码依赖仍只指向真源（见 R14 / 冲突 C-4 的裁决）。
    """
    root = course_root(course)
    led = ledger_of(course)
    sources = led.load_sources()
    roots = ann_roots or archive.DEFAULT_ANN_ROOTS

    data, report = archive.build_archive(root, roots)
    if not data["by_sha1"]:
        report.append(f"[warn] 在 {roots} 下没找到任何批注（annotations.json）")
        return report

    slug_by_lecture = {stem: (rec.get("slug") or pdf_source.slugify(stem))
                       for stem, rec in sources["sources"].items()}
    n_crop = archive.copy_crops(data, root, slug_by_lecture)
    archive.save_archive(root, data)

    matched = sum(1 for r in data["by_sha1"].values() if r.get("lecture_id"))
    report.append(f"[done ] {len(data['by_sha1'])} 组批注入账，"
                  f"其中 {matched} 组匹配到学习库讲次；复制裁剪图 {n_crop} 张")
    return report


# ---------------------------------------------------------------- cards

def cmd_cards(course: str, sync: bool = False, rebuild: bool = False,
              ai: bool = True, prune: bool = False) -> list[str]:
    """把归档的追问变成 Anki 卡片。

    - **有提问**的批注 → 直接制卡（题面 = 你自己问过的问题）
    - **没提问**的批注 → 用 AI 从**解答**反推一道题（复用真源 deepreader 的引擎）
    - 卡片身份稳定（源自 sha1+页号+序号），所以重跑不会重复制卡；
    - `anki_note_id` 存在账本里，已推过的不会再推；
    - 不加 --sync 时只生成 + 导出 TSV，不碰 Anki。
    """
    root = course_root(course)
    led = ledger_of(course)
    sources = led.load_sources()
    arch = archive.load_archive(root)

    report: list[str] = []

    # 无提问的批注：用 AI 从解答反推题目（引擎来自真源 ppt-deepreader，兑现 R13）
    provider = None
    if ai:
        ok, why = qa.engine_available()
        if ok:
            def provider(ann, _root=root):  # type: ignore[misc]
                return qa.generate_question(_root, ann.get("explanation", ""),
                                            ann.get("latex", ""))
            report.append("[ai   ] 无提问的批注将用 AI 从解答反推题目"
                          "（引擎复用真源 ppt-deepreader，结果有缓存）")
        else:
            report.append(f"[warn] AI 出题不可用：{why} —— 无提问的批注会被跳过")

    all_cards: list[dict] = []
    all_skipped: list[dict] = []
    for sha, rec in sorted(arch.get("by_sha1", {}).items()):
        lec = rec.get("lecture_id")
        if not lec:
            continue
        slug = (sources["sources"].get(lec, {}).get("slug")
                or pdf_source.slugify(lec))
        crop_dir = os.path.join(root, "assets", slug, "ann")
        anns = []
        for a in rec["annotations"]:
            b = dict(a)
            b["_sha1"] = sha
            anns.append(b)
        made, skipped = cards_mod.build_cards(course, lec, rec.get("source_file", ""),
                                              anns, slug, crop_dir,
                                              question_provider=provider)
        all_cards += made
        all_skipped += skipped

    if not all_cards:
        msg = ["[warn] 没有可制卡的追问 —— 先跑 archive（需账本里有匹配到讲次的批注）"]
        for s in all_skipped:
            msg.append(f"[skip ] {s['lecture']} 第 {s['page']} 页：{s['reason']}")
        return msg

    data, report = cards_mod.merge_cards(root, all_cards)
    cards_mod.save_cards(root, data)

    # 剔除「按当前规则不该存在」的卡 —— **必须显式 --prune**。
    # 为什么不再自动：踩过两次 ——
    #   ① 保存顺序写错，把剔除结果又写回去了；
    #   ② 加 --no-ai 跑一次就会把 AI 卡全删掉（这次压根没构建它们）。
    # 自动删除用户数据太危险，改成显式开关。
    removed: list[dict] = []
    if prune:
        removed = cards_mod.prune_cards(root, {c["id"] for c in all_cards})
        if removed:
            report.append(f"[prune] 剔除 {len(removed)} 张不合规的卡："
                          + "、".join(f"{r['source'].get('page')}页#{r['id'].split(':')[-1]}"
                                      for r in removed))
        else:
            report.append("[prune] 没有需要剔除的卡")
    else:
        _valid = {c["id"] for c in all_cards}
        stale = [c for c in cards_mod.load_cards(root).get("by_id", {}).values()
                 if c["id"] not in _valid]
        if stale:
            report.append(f"[tip  ] 账本里有 {len(stale)} 张本次未构建的卡"
                          f"（例如 {stale[0]['id']}）。确认要删就加 --prune")

    if all_skipped:
        report.append(f"[skip ] 跳过 {len(all_skipped)} 条无提问的批注（不制卡，但仍在账本里）："
                      + "、".join(f"{s['page']}页" for s in all_skipped[:6]))

    # 探测目标 Anki 的笔记类型：中文版没有 Basic 这个名字（叫「问答题」），
    # 表头写错会导致导入失败或建出错误的类型。探测不到才退回 Basic。
    notetype = "Basic"
    ac = cards_mod.AnkiConnect()
    if ac.available():
        picked, _fmap = ac.pick_basic_model()
        if picked:
            notetype = picked
            report.append(f"[anki ] 探测到笔记类型：**{notetype}**")

    out_dir = os.path.join(root, "cards")
    os.makedirs(out_dir, exist_ok=True)
    tsv = os.path.join(out_dir, "anki_import.tsv")
    cards_mod.export_tsv(sorted(data["by_id"].values(), key=lambda c: c["id"]),
                         tsv, notetype=notetype, deck=f"课程::{course}")
    report.append(f"[export] 已导出到 {os.path.relpath(tsv, root)}"
                  f"（笔记类型 {notetype}，可直接用 Anki 导入）")

    if sync:
        if rebuild:
            try:
                n = cards_mod.reset_deck(root, f"课程::{course}")
                report.append(f"[anki ] 已清空牌组并重置账本（删除 {n} 张卡），准备重建")
            except cards_mod.AnkiError as e:
                report.append(f"[warn] 重建失败：{e}")
        report += cards_mod.sync_to_anki(root, all_cards)
        # 被剔除的卡如果之前已经推给 Anki，这里一并删掉，别让它留在你的复习队列里
        stale_ids = [r["anki_note_id"] for r in removed if r.get("anki_note_id")]
        if stale_ids:
            ac2 = cards_mod.AnkiConnect()
            if ac2.available():
                try:
                    ac2.delete_notes(stale_ids)
                    report.append(f"[anki ] 已从 Anki 删除 {len(stale_ids)} 张不合规的卡")
                except cards_mod.AnkiError as e:
                    report.append(f"[warn] 删除旧卡失败：{e}")
    else:
        report.append("[tip  ] 想直接推进 Anki：加 --sync（需 Anki 已打开）")
    return report


# ---------------------------------------------------------------- kcs

def cmd_kcs(course: str, window: int = 8, ai: bool = True) -> list[str]:
    """从讲义页 + 你的追问里提炼知识点骨架（S2）。

    - 按「页窗口」分批喂给模型（默认 8 页一批）；
    - 每批**带上你在这个区间的追问**，并明确要求优先把它们提炼成知识点；
    - 结果按窗口内容哈希缓存，没变就不重算（也不花钱）；
    - 把追问按页号挂到对应知识点上（知识点 ← 你真正问过的问题）。
    """
    root = course_root(course)
    led = ledger_of(course)
    sources = led.load_sources()
    if not sources["sources"]:
        raise SystemExit("账本是空的，先跑 ingest。")
    arch = archive.load_archive(root)

    if not ai:
        return ["[warn] --no-ai：知识点提取需要模型，已跳过"]
    ok, why = engine.available()
    if not ok:
        return [f"[warn] 引擎不可用：{why}"]

    data = kcs_mod.load_kcs(root)
    data["title"] = f"{course} · 知识骨架"
    report: list[str] = []

    for stem in sorted(sources["sources"]):
        pages_data = led.load_pages(stem)
        if not pages_data:
            report.append(f"[warn] {stem} 账本缺 pages 记录，跳过")
            continue
        anns = archive.annotations_for_lecture(arch, stem)
        label = pages_data.get("running_head") or stem
        found, rep = kcs_mod.extract_lecture(root, stem, label,
                                             pages_data["pages"], anns,
                                             window=window)
        report += rep
        n = kcs_mod.link_questions(found, anns)
        if n:
            report.append(f"[link ] {stem}：{n} 条追问挂到了知识点上")
        kcs_mod.put_lecture(data, stem, label, found)

    kcs_mod.save_kcs(root, data)
    total = sum(len(c.get("kcs") or []) for c in data["chapters"])
    report.append(f"[done ] 骨架共 {total} 个知识点，"
                  f"已写入 .ledger/kcs.json（跑 render 会渲染进笔记）")
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
    ap.add_argument("--ann-root", action="append", default=None,
                    help="批注数据源目录（可多次；默认扫 D:\\1\\ppt-deepreader\\.pdw_work）")
    ap.add_argument("--window", type=int, default=8,
                    help="kcs 动作：每批喂给模型多少页（默认 8）")
    ap.add_argument("--prune", action="store_true",
                    help="cards 动作：删掉本次未构建的卡（默认不删，避免误删）")
    ap.add_argument("--no-ai", action="store_true",
                    help="cards 动作：不用 AI 给无提问的批注出题")
    ap.add_argument("--rebuild", action="store_true",
                    help="cards 动作：先清空牌组并重置账本，再全部重建（规则变更后用）")
    ap.add_argument("--sync", action="store_true",
                    help="cards 动作：把卡片推进 Anki（需 Anki 已打开）")
    ap.add_argument("action",
                    choices=["ingest", "render", "archive", "cards", "kcs",
                             "all", "check", "clean"])
    args = ap.parse_args(argv)

    lines: list[str] = []
    if args.action == "clean":
        lines += cmd_clean(args.course)
    if args.action in ("ingest", "all"):
        lines += cmd_ingest(args.course, force=args.force,
                            images=not args.no_images, scale=args.scale)
    if args.action in ("archive", "all"):
        lines += cmd_archive(args.course, args.ann_root)
    if args.action in ("kcs", "all"):
        lines += cmd_kcs(args.course, window=args.window, ai=not args.no_ai)
    if args.action in ("cards", "all"):
        lines += cmd_cards(args.course, sync=args.sync, rebuild=args.rebuild, ai=not args.no_ai, prune=args.prune)
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
