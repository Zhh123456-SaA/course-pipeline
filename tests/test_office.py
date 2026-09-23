# -*- coding: utf-8 -*-
"""Office（PPTX/DOCX）摄取测试。

背景（用户实测发现）：R13 早说好复用 deepreader 的 extractor（PPTX/DOCX 能力），
但摄取层一直只认 PDF —— 用户放在逐页精读器里的**整门 PPTX 课程被挡在学习库门外**：
批注进了账本却不渲染，而源文件就算放进 source/ 也读不了。

跑法：python tests/test_office.py
"""
from __future__ import annotations

import os
import re
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
PROJ = os.path.dirname(HERE)
COURSE = "生物"
LIB = os.path.abspath(os.path.join(PROJ, "..", "学习库", COURSE))

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

sys.path.insert(0, os.path.join(PROJ, "vendor"))
sys.path.insert(0, os.path.join(PROJ, "src"))
import source_ingest as S  # noqa: E402

FAILS: list[str] = []
PASSES: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    (PASSES if ok else FAILS).append(f"{name}{(' — ' + detail) if detail else ''}")


# ---------------------------------------------------------------- 1 分派
check("PDF 被支持", S.supported("a.pdf"))
check("PPTX 被支持", S.supported("a.PPTX"), "大写扩展名也要认")
check("DOCX 被支持", S.supported("a.docx"))
check("老格式 .ppt/.doc 被支持", S.supported("a.ppt") and S.supported("a.doc"))
check("不支持的格式被拒", not S.supported("a.mp4") and not S.supported("a.txt"))
try:
    S.ingest_any("x.mp4")
    check("不支持的格式抛错", False, "居然没报错")
except ValueError:
    check("不支持的格式抛错", True)
except FileNotFoundError:
    check("不支持的格式抛错", True, "（先判扩展名即可）")

# ---------------------------------------------------------------- 2 真实产物
if not os.path.isdir(LIB):
    PASSES.append(f"（跳过真实产物检查：没有 {LIB}）")
else:
    src = os.path.join(LIB, "source")
    files = os.listdir(src) if os.path.isdir(src) else []
    pptx = [f for f in files if f.lower().endswith((".pptx", ".ppt"))]
    check("生物课的源是 Office 格式（这正是之前被挡住的那门课）", bool(pptx), str(files))

    led = os.path.join(LIB, ".ledger")
    pages_files = os.listdir(os.path.join(led, "pages")) if os.path.isdir(
        os.path.join(led, "pages")) else []
    if not pages_files:
        PASSES.append("（跳过：生物课还没 ingest）")
    else:
        import json
        d = json.load(open(os.path.join(led, "pages", pages_files[0]), encoding="utf-8"))
        check("账本标记了素材类型", d.get("kind") == "office", str(d.get("kind")))
        check("页数与 pptx 一致（132 页）", d["page_count"] == 132, str(d["page_count"]))
        check("每一页都有文字字段",
              all("text" in p for p in d["pages"]))
        check("文字确实抽到了（中位数字数 > 20）",
              sorted(p["chars_clean"] for p in d["pages"])[len(d["pages"]) // 2] > 20,
              str(sorted(p["chars_clean"] for p in d["pages"])[len(d["pages"]) // 2]))

        # ★ 页图字段必须是 pNNN.jpg —— 实测踩过：prepare_pages 返回的是**元组**，
        #   当列表遍历会把「整个列表」和「PDF 路径」写成 image，还误报页数不一致。
        bad = [p["no"] for p in d["pages"]
               if not (p.get("image") or "").startswith("p")]
        check("每页的 image 字段都是 pNNN.xxx（不是元组/PDF 路径）", not bad,
              f"异常页 {bad[:3]}")
        check("没有页数不一致的告警", not d.get("warn"), str(d.get("warn")))

        img_dir = os.path.join(LIB, "assets", pages_files[0][:-5])
        # 只数「页图」本身（pNNN.xxx）。目录里还可能有批注裁剪图子目录等，
        # 直接 listdir 计数会被别的东西顶掉 1 张（实测 133）。
        n_img = (len([f for f in os.listdir(img_dir)
                      if re.fullmatch(r"p\d+\.(jpg|jpeg|png)", f, re.I)])
                 if os.path.isdir(img_dir) else 0)
        check("页图真的落盘了（132 张）", n_img == 132, f"{n_img} 张")

        # 归档：本课程的批注挂上来了
        ann = os.path.join(led, "annotations.json")
        if os.path.exists(ann):
            a = json.load(open(ann, encoding="utf-8"))
            recs = list(a.get("by_sha1", {}).values())
            check("生物的批注归档到了生物（不是物理）",
                  bool(recs) and all(r.get("lecture_id") for r in recs),
                  str([r.get("lecture_id") for r in recs]))
            check("批注挂在 Office 讲次上",
                  any("细胞膜" in (r.get("lecture_id") or "") for r in recs))

# ---------------------------------------------------------------- 汇总
for p in PASSES:
    print("PASS  " + p)
for f_ in FAILS:
    print("FAIL  " + f_)
print("=" * 60)
print(f"通过 {len(PASSES)} / 失败 {len(FAILS)}")
raise SystemExit(1 if FAILS else 0)
