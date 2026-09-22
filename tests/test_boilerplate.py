# -*- coding: utf-8 -*-
"""页眉页脚剥离的回归测试 —— 把踩过的坑钉死。

四个真实的坑（都在 GC01/GC02 上踩过）：
  1. 页脚带页码（普通化学24）→ 必须靠「数字归一化」才能匹配
  2. 页脚位置飘忽（有时页首有时页尾）→ 只查边缘会漏一半
  3. 封面页只有一行 → 剥完变空，必须退回过剥
  4. 正文里的短词（如「化学」）→ 不能被误判成页眉

跑法：python tests/test_boilerplate.py
"""
from __future__ import annotations

import os
import re
import sys

# Windows 控制台默认 GBK：print 中文/emoji 会抛 UnicodeEncodeError 并让退出码变 1
# （明明全过却报失败）。强制 UTF-8 输出。
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

HERE = os.path.dirname(os.path.abspath(__file__))
PROJ = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(PROJ, "vendor"))
sys.path.insert(0, os.path.join(PROJ, "src"))

import pdf_source as P  # noqa: E402

FAILS: list[str] = []
PASSES: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    (PASSES if ok else FAILS).append(f"{name}{(' — ' + detail) if detail else ''}")


# ---------------------------------------------------------------- 1 norm_key

cases = [
    ("普通化学24", "普通化学", "页码接在名字后"),
    ("普通化学 13", "普通化学", "空格 + 页码"),
    ("普通化学", "普通化学", "无页码"),
    ("绪 论", "绪论", "2 字页眉、中间有空格"),
    ("2", "", "纯页码 → 空键"),
    ("第2章 原子结构", "第#章原子结构", "正文里的编号不该被掐掉"),
]
for raw, want, why in cases:
    got = P.norm_key(raw)
    check(f"norm_key({raw!r}) = {want!r}", got == want, f"{why}；实得 {got!r}")


# ---------------------------------------------------------------- 2 检出

def mk(pages_lines):
    return [P.split_lines("\n".join(ls)) for ls in pages_lines]


# 场景 A：2 字页眉固定在每页首行（GC01 的「绪 论」）
# 注意每页要有足够多的行 —— 只有 2 行的页会让每行都成为「边缘行」，
# 那不是真实页面。这里用 5 行的真实形态。
def page_with(header, i, extra_head=None, extra_tail=None):
    lines = []
    if extra_head:
        lines.append(extra_head)
    if header:
        lines.append(header)
    lines += [f"第{i}页的第一段正文，讲一个独特的内容 {i}*7",
              f"第{i}页的第二段正文，与其它页都不一样 {i}*13",
              f"第{i}页的第三段结论 {i}*29"]
    if extra_tail:
        lines.append(extra_tail)
    return lines


pagesA = [page_with("绪 论", i) for i in range(1, 11)]
bpA = P.detect_boilerplate(mk(pagesA))
check("规则A：固定的 2 字页眉被检出", P.norm_key("绪 论") in bpA, str(list(bpA)))

# 场景 B：4 字页脚位置飘忽（GC02 的「普通化学」）
pagesB = []
for i in range(1, 11):
    if i % 2:
        pagesB.append(page_with("物质的聚集状态", i, extra_head="普通化学"))
    else:
        pagesB.append(page_with("物质的聚集状态", i, extra_tail="普通化学"))
bpB = P.detect_boilerplate(mk(pagesB))
check("规则B：位置飘忽的页脚被检出", "普通化学" in bpB, str(list(bpB)))
check("规则B：正常页眉也被检出", "物质的聚集状态" in bpB, str(list(bpB)))

# 场景 C：只差页码的模板句（归一化后各页完全相同）不该被当成页脚 ——
# 靠「含句读标点」这条判别拦下
pagesC = []
for i in range(1, 11):
    pagesC.append([f"第{i}页 化学 是一门中心科学，这一段说明化学很重要",
                   f"这是第{i}页的第二行正文，内容各不相同"])
bpC = P.detect_boilerplate(mk(pagesC))
check("正文里的短词/模板句不被误判", not bpC, str(list(bpC)))

# ---------------------------------------------------------------- 3 剥离

# 封面页：只有一行，且它恰好在 boilerplate 里 → 必须退回，不能剥成空
bp = {"物质的聚集状态": "物质的聚集状态"}
kept = P.strip_boilerplate(["物质的聚集状态"], bp)
check("封面页不会被剥成空（退回过剥）", kept == ["物质的聚集状态"], str(kept))

# 正常页：页首页眉该被剥掉
kept = P.strip_boilerplate(["物质的聚集状态", "这一页有真实内容，足够长了"],
                           bp)
check("正常页的页眉被剥掉", P.norm_key("物质的聚集状态") not in [P.norm_key(x) for x in kept],
      str(kept))

# 位置飘忽的页脚：无论在首还是尾都要剥掉
bp2 = {"普通化学": "普通化学"}
for pos in ("head", "tail"):
    lines = ["普通化学", "正文内容足够长以通过最小长度校验"] if pos == "head" \
        else ["正文内容足够长以通过最小长度校验", "普通化学"]
    got = P.strip_boilerplate(lines, bp2)
    check(f"页脚在 {pos} 时被剥掉", "普通化学" not in got, str(got))

# ---------------------------------------------------------------- 4 真实产物回归

LIB = os.path.abspath(os.path.join(PROJ, "..", "学习库", "普通化学", "notes"))
if os.path.isdir(LIB):
    for fn in sorted(os.listdir(LIB)):
        if not fn.lower().endswith(".md") or not fn[0].isdigit():
            continue
        with open(os.path.join(LIB, fn), encoding="utf-8") as f:
            text = f.read()
        # 去掉笔记自己的元信息行（那里本来就会出现「页眉：普通化学13」）
        body = "\n".join(l for l in text.split("\n")
                         if not l.startswith("> 来源：") and not l.startswith("> 已自动剔除"))
        hits = re.findall(r"普通化学\s*\d+", body)
        check(f"{fn} 正文里没有页脚残留", not hits, f"残留 {len(hits)} 处 {hits[:3]}")

        # 图片链接必须合法（不含空格）+ 目标文件真的存在。
        # `![x](../assets/01 GC01/p.png)` 这种带空格的链接在 CommonMark 里是非法链接，
        # VS Code 预览与 GitHub 都显示不出图 —— 这个坑已经踩过一次。
        links = re.findall(r"!\[[^\]]*\]\(([^)]*)\)", text)
        bad = [u for u in links if re.search(r"\s", u)]
        check(f"{fn} 图片链接不含空格（合法 CommonMark）", not bad, str(bad[:2]))
        missing = [u for u in links if not os.path.exists(
            os.path.normpath(os.path.join(LIB, u)))]
        check(f"{fn} 图片链接指向的文件都存在", not missing,
              f"{len(missing)} 个缺失，例如 {missing[:2]}")
else:
    PASSES.append("（跳过真实产物回归：还没生成笔记）")

# ---------------------------------------------------------------- 汇总

for p in PASSES:
    print("PASS  " + p)
for f in FAILS:
    print("FAIL  " + f)
print("=" * 60)
print(f"通过 {len(PASSES)} / 失败 {len(FAILS)}")
raise SystemExit(1 if FAILS else 0)
