# -*- coding: utf-8 -*-
"""讲义 PDF 摄取：抽出文字层、剥页眉页脚、断行重排、导出页图。

为什么需要「剥页眉页脚」和「断行重排」：
  讲义是 PPT 导出的 PDF，每页都有相同的页眉（如「绪 论」），而且一句话会被
  PPT 的文本框切成好几行。不处理的话，账本里全是噪音、笔记读起来是断的。
"""
from __future__ import annotations

import os
import re
from collections import Counter
from typing import Iterable

import pypdfium2 as pdfium

# ---------------------------------------------------------------- 常量

#: 项目符号 / 编号开头 —— 命中就视为新段落
BULLET_RE = re.compile(
    r"^\s*(?:"
    r"[•·▪◦●○◆■□※✓✔\-\*–—]"
    r"|\(?\d{1,2}[\.\)、]"
    r"|[（(][一二三四五六七八九十\d]{1,3}[）)]"
    r"|[一二三四五六七八九十]{1,3}[、\.]"
    r"|[A-Za-z][\.\)]"
    r")\s*"
)

#: 强句末标点：上一行以它结尾 → 下一行开新段落
ENDS_STRONG = "。！？!?…"

#: 页眉页脚判定
BOILERPLATE_RATIO = 0.4
BOILERPLATE_MIN_PAGES = 3
#: 规则 B 用：短行（归一化后 3~24 字）在多页重复 → 也是页眉页脚
BOILERPLATE_MIN_LEN = 3
BOILERPLATE_MAX_LEN = 24
#: 规则 B 排除：含句读标点的行是正文，不是页眉页脚
#: （否则「第N页…，…」这类只差页码的模板句会被误判）
_SENTENCE_PUNCT = re.compile(r"[，。；：！？,;:!?]")

_CJK = re.compile(r"[\u3400-\u9fff\u3000-\u303f\uff00-\uffef]")

#: 路径里不能出现的字符：文件系统禁用字符 + markdown 链接定界符。
#: 尤其是**空格** —— `![x](../assets/01 GC01/p.png)` 在 CommonMark 里是非法链接，
#: VS Code 预览、GitHub 都显示不出图（只有 Obsidian 宽容才没暴露）。这个坑踩过。
_UNSAFE_PATH = re.compile(r'[\\/:*?"<>|#%\[\]()\s]+')


def slugify(name: str) -> str:
    """把讲义名变成安全的目录名：去禁用字符、空白换连字符、压缩连字符。

    `01 GC01` → `01-GC01`，于是 `../assets/01-GC01/p001.png` 在所有
    markdown 阅读器里都是合法链接。
    """
    s = _UNSAFE_PATH.sub("-", name.strip())
    s = re.sub(r"-{2,}", "-", s).strip("-")
    return s or "item"


# ---------------------------------------------------------------- 抽取

def extract_raw_pages(pdf_path: str) -> list[str]:
    """按页抽出原始文字层。"""
    doc = pdfium.PdfDocument(pdf_path)
    try:
        return [doc[i].get_textpage().get_text_range() for i in range(len(doc))]
    finally:
        doc.close()


def split_lines(text: str) -> list[str]:
    """拆行并去掉空白行。"""
    return [l.strip() for l in text.replace("\r\n", "\n").replace("\r", "\n").split("\n") if l.strip()]


# ---------------------------------------------------------------- 页眉页脚

def norm_key(line: str) -> str:
    """把一行归一化成「页眉页脚指纹」。

    四步（每一步都是踩坑踩出来的）：
      1. **数字替换成 #**：页脚常是「普通化学24」，页码每页都变，
         不归一化就永远匹配不上（第一版漏剥 45 处页脚的根因）。
      2. **去掉所有空白**：PPT 排版空格不稳定，「普通化学 13」与「普通化学13」
         必须视作同一条。
      3. **去掉所有标点/符号**：视觉行重建时，另一个文本框的句末「。」可能
         恰好落在同一 y 上被并进页脚行（实测 3 页踩到：
         `大学。物理（医学类）·第二章动力学05/57`）—— 不去标点就漏剥。
      4. **掐掉首尾的 #**：「普通化学 2」归一化后是「普通化学#」，
         而页首的「普通化学」是「普通化学」—— 不掐掉就仍是两个键。
    """
    s = re.sub(r"\d+", "#", line)
    s = re.sub(r"\s+", "", s)
    s = re.sub(r"[^\u4e00-\u9fffA-Za-z0-9#]", "", s)
    return s.strip("#")


def detect_boilerplate(pages: list[list[str]]) -> dict[str, str]:
    """找出在多页重复出现的行（页眉 / 页脚 / 固定角标）。

    用**两条规则取并集**——单用任何一条都会漏：

      规则 A（边缘 + 不看长度）：在多页的**前 2 行或后 2 行**出现。
         抓「绪 论」这种只有 2 个字、但每页都在页首的页眉。

      规则 B（不限位置 + 必须是短行）：归一化后 3~24 字、且在多页重复。
         抓「普通化学」这种位置飘忽（有时页首、有时页尾）的页脚。
         限长度是为了不误伤正文里常见的短词（如 2 字的「化学」）。

    返回 {归一化指纹: 代表原文}。
    """
    n = len(pages)
    if n < BOILERPLATE_MIN_PAGES:
        return {}

    edge_counter: Counter[str] = Counter()
    any_counter: Counter[str] = Counter()
    sample: dict[str, str] = {}

    for lines in pages:
        for l in set(lines):
            k = norm_key(l)
            if not k:
                continue
            any_counter[k] += 1
            sample.setdefault(k, l)
        # 「边缘」只在行数够多的页上才有意义：只有 2 行的页会让每行都是边缘行
        if len(lines) >= 4:
            for l in set(lines[:2]) | set(lines[-2:]):
                k = norm_key(l)
                if k:
                    edge_counter[k] += 1

    threshold = max(BOILERPLATE_MIN_PAGES, int(n * BOILERPLATE_RATIO))
    found: dict[str, str] = {}
    for k, c in edge_counter.items():           # 规则 A
        if c >= threshold and not _SENTENCE_PUNCT.search(sample[k]):
            found[k] = sample[k]
    for k, c in any_counter.items():            # 规则 B
        if c < threshold:
            continue
        if not (BOILERPLATE_MIN_LEN <= len(k) <= BOILERPLATE_MAX_LEN):
            continue
        if _SENTENCE_PUNCT.search(sample[k]):
            continue
        found[k] = sample[k]
    return found


def strip_boilerplate(lines: list[str], boilerplate: dict[str, str],
                      min_keep: int = 6) -> list[str]:
    """剥离页眉页脚。

    与第一版的关键差别：**不再限制在边缘位置**。规则 B 抓到的页脚位置是飘的
    （GC02 的「普通化学」有时在页首、有时在页尾），只在边缘找会漏一半。
    因为进这个集合的行必须满足「短 + 多页重复」，误伤正文的风险很低。

    安全阀：如果剥完这页就空了（而原本有内容），就把剥离结果退回。
    没有这一条，封面页会被整页剥光（GC02 第 1 页踩过）。
    """
    if not boilerplate:
        return list(lines)
    keys = set(boilerplate)
    keep = [l for l in lines if norm_key(l) not in keys]

    if len("".join(keep)) < min_keep <= len("".join(lines)):
        return list(lines)   # 退回过剥
    return keep


# ---------------------------------------------------------------- 视觉行（按坐标重建）
#
# 为什么需要它（实测踩到，用户报的 bug）：
#   PPT 导出的 PDF 里，文字是按**绘制顺序**存的，不是按视觉位置。
#   直接按抽取顺序断行重排，会把**好几个文本框粘成一大段**。物理课第 11 页实测：
#
#     绳微元 dm 受力分析图推理过程对微元用牛顿第二定律微元质量趋于零得到轻绳张力处处相等
#     𝑇𝐵 − 𝑇𝐴 = 𝑑𝑚 𝑎 𝑑𝑚 → 0 ⇒ 𝑇𝐴 = 𝑇𝐵 轻绳张力处处相等「轻绳」（质量可忽略）是一个理想模型…
#
#   —— 7 个文本框被接成了一句。而同一页还有图形标注碎片行：
#     `B A F dm  A B TA TA TB TB = −  = −    点和 点的张力： ， 𝑎 → 0 ⇒ 𝑇𝐴 = 𝑇𝐵`
#
# 解法：用 `get_charbox` 取每个字符的坐标 → 按 y 聚类成视觉行 → 行内按 x 排序。
# 再靠**左边界是否对齐**判断该不该把相邻两行接起来 —— 不同文本框的左边界会大跳
# （实测第 11 页相邻行 Δx 动辄 100~700pt），同一段落的换行则基本对齐。

#: 视觉行的 y 容差（pt）：字高约 20~30pt，6pt 内视为同一行
LINE_Y_TOL = 6.0
#: 左边界对齐容差（pt）：只有 Δx 在这么小范围内，才可能是同一段落的换行
LEFT_TOL = 6.0


def _charbox(tp, i: int):
    b = tp.get_charbox(i)
    if hasattr(b, "left"):
        return b.left, b.bottom, b.right, b.top
    return b[0], b[1], b[2], b[3]


def visual_lines(page) -> list[dict]:
    """按字符坐标重建「视觉行」。返回 [{text, x0, x1, y}]，已按阅读顺序（自上而下）排序。"""
    tp = page.get_textpage()
    items: list[tuple[float, float, float, float, str]] = []
    for i in range(tp.count_chars()):
        try:
            ch = tp.get_text_range(i, 1)
            x0, y0, x1, y1 = _charbox(tp, i)
        except Exception:  # noqa: BLE001 - 个别字符取不到就跳过
            continue
        if ch in ("\r", "\n"):
            continue
        if x0 == x1 and y0 == y1:
            continue                      # 零宽占位符
        items.append((x0, y0, x1, y1, ch))

    items.sort(key=lambda t: (-t[3], t[0]))
    lines: list[dict] = []
    for it in items:
        yc = (it[1] + it[3]) / 2
        for ln in lines:
            if abs(ln["yc"] - yc) <= LINE_Y_TOL:
                ln["chars"].append(it)
                ln["yc"] = sum((c[1] + c[3]) / 2 for c in ln["chars"]) / len(ln["chars"])
                break
        else:
            lines.append({"yc": yc, "chars": [it]})

    out: list[dict] = []
    for ln in lines:
        cs = sorted(ln["chars"], key=lambda t: t[0])
        out.append({
            "text": "".join(c[4] for c in cs).strip(),
            "x0": cs[0][0],
            "x1": max(c[2] for c in cs),
            "y": ln["yc"],
        })
    out.sort(key=lambda l: -l["y"])
    return out


def is_garbage_line(text: str) -> bool:
    """判断视觉行是不是「碎片」——纯标点/孤立符号，没有可读内容。

    实测第 11 页会产出 `，。`、`−=`、`""`、`。`、`F` 这类行：
    它们多半是公式里的上下标或图形标注被拆散后的残渣，留在正文里纯属噪音。
    """
    t = re.sub(r"\s+", "", text or "")
    if not t:
        return True
    if not re.search(r"[\u4e00-\u9fffA-Za-z0-9]", t):
        return True                       # 纯标点/符号（如 `，。`、`−=`）
    if len(t) <= 2 and not re.search(r"[\u4e00-\u9fff]", t):
        return True                       # 极短且无中文（如 `F`、`A B`）
    return False


def reflow_visual(lines: list[dict]) -> list[str]:
    """把视觉行合并回段落。

    开新段落的四种情况：
      1. 本行以项目符号 / 编号开头
      2. 上一行以强句末标点结尾
      3. **左边界与上一行不对齐**（＝换了一个文本框）  ← 这一条是本次修复的关键
      4. 没有上一行
    """
    paras: list[str] = []
    prev_x0: float | None = None
    for ln in lines:
        text = (ln.get("text") or "").strip()
        if is_garbage_line(text):
            continue
        x0 = float(ln.get("x0") or 0.0)
        if not paras:
            paras.append(text)
            prev_x0 = x0
            continue
        prev = paras[-1]
        aligned = prev_x0 is not None and abs(x0 - prev_x0) <= LEFT_TOL
        if BULLET_RE.match(text) or prev.endswith(tuple(ENDS_STRONG)) or not aligned:
            paras.append(text)
            prev_x0 = x0
        else:
            sep = " " if _needs_space(prev, text) else ""
            paras[-1] = prev + sep + text
    return paras


# ---------------------------------------------------------------- 断行重排

def _needs_space(a: str, b: str) -> bool:
    """中文之间直接接；只要有一侧是拉丁字母/数字就补一个空格。"""
    if not a or not b:
        return False
    left_cjk = bool(_CJK.match(a[-1]))
    right_cjk = bool(_CJK.match(b[0]))
    return not (left_cjk and right_cjk)


def reflow(lines: Iterable[str]) -> list[str]:
    """纯文本版本的断行重排（没有坐标时的退路）。

    开新段落的三种情况：
      1. 本行以项目符号 / 编号开头
      2. 上一行以强句末标点结尾
      3. 没有上一行
    """
    paras: list[str] = []
    for raw in lines:
        line = raw.strip()
        if not line:
            continue
        if not paras:
            paras.append(line)
            continue
        prev = paras[-1]
        if BULLET_RE.match(line) or prev.endswith(tuple(ENDS_STRONG)):
            paras.append(line)
        else:
            sep = " " if _needs_space(prev, line) else ""
            paras[-1] = prev + sep + line
    return paras


# ---------------------------------------------------------------- 页图

def render_page_image(doc, index: int, out_path: str, scale: float = 1.6) -> None:
    """把第 index 页渲染成 PNG。"""
    os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)
    bitmap = doc[index].render(scale=scale)
    pil = bitmap.to_pil()
    pil.save(out_path, "PNG", optimize=True)


# ---------------------------------------------------------------- 一页的组装

def build_page(no: int, vlines: list[dict], boilerplate: dict[str, str]) -> dict:
    """单页 → 账本里的一个 page 记录。

    `vlines` 是 `visual_lines()` 的产出（带坐标）。剥页眉页脚按**文字**匹配，
    但**合并段落按坐标**（左边界对齐），这样才不会把不同文本框粘成一段。
    """
    raw_text = "".join((l.get("text") or "") for l in vlines)
    keys = set(boilerplate or {})
    kept = [l for l in vlines if norm_key(l.get("text") or "") not in keys]

    # 安全阀：剥完这页就空了（而原本有内容）→ 退回过剥（封面页踩过）
    if len("".join(l.get("text") or "" for l in kept)) < 6 <= len(raw_text):
        kept = list(vlines)

    dropped = len(vlines) - len(kept)
    garbage = sum(1 for l in kept if is_garbage_line(l.get("text") or ""))
    paras = reflow_visual(kept)
    text = "\n".join(paras)
    return {
        "no": no,
        "chars_raw": len(raw_text.strip()),
        "chars_clean": len(text),
        "text": text,
        "lines_removed": dropped + garbage,
        # 「没有文字层」和「字数很少」是两回事：封面只有几个字，但文字层是有的
        "has_text_layer": bool(raw_text.strip()),
        "is_blank": len(text.strip()) < 3,
    }


def ingest(pdf_path: str, images_dir: str | None = None, scale: float = 1.6,
           render_images: bool = True) -> dict:
    """摄取一个讲义 PDF，返回账本用的数据结构（不落盘）。"""
    doc = pdfium.PdfDocument(pdf_path)
    try:
        n = len(doc)
        # 用**视觉行**（带坐标）而不是原始抽取顺序 —— 见文件上方「视觉行」一节的说明
        vpages = [visual_lines(doc[i]) for i in range(n)]
        per_page_lines = [[l["text"] for l in vp if not is_garbage_line(l["text"])]
                          for vp in vpages]
        boilerplate = detect_boilerplate(per_page_lines)
        pages = [build_page(i + 1, vpages[i], boilerplate) for i in range(n)]

        if render_images and images_dir:
            os.makedirs(images_dir, exist_ok=True)
            for i in range(n):
                pages[i]["image"] = f"p{i + 1:03d}.png"
                render_page_image(doc, i, os.path.join(images_dir, pages[i]["image"]), scale)
    finally:
        doc.close()

    # 页眉取「不含数字的最长那条」：带数字的是页脚（如「普通化学13」），
    # 拿去做标题会很难看。没有候选时才退回带数字的。
    cands = [v for v in boilerplate.values() if not re.search(r"\d", v)] \
        or list(boilerplate.values())
    running_head = max(cands, key=len).replace(" ", "") if cands else ""

    return {
        "pages": pages,
        "page_count": n,
        "boilerplate": sorted(boilerplate.values()),
        "running_head": running_head,
    }
