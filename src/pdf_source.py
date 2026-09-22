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

    三步（每一步都是踩坑踩出来的）：
      1. **数字替换成 #**：页脚常是「普通化学24」，页码每页都变，
         不归一化就永远匹配不上（第一版漏剥 45 处页脚的根因）。
      2. **去掉所有空白**：PPT 排版空格不稳定，「普通化学 13」与「普通化学13」
         必须视作同一条。
      3. **掐掉首尾的 # 与分隔符**：「普通化学 2」归一化后是「普通化学#」，
         而页首的「普通化学」是「普通化学」—— 不掐掉就仍然是两个键（GC02 的坑）。
    """
    s = re.sub(r"\d+", "#", line)
    s = re.sub(r"\s+", "", s)
    return s.strip("#·.、-—()（）[]【】")


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


# ---------------------------------------------------------------- 断行重排

def _needs_space(a: str, b: str) -> bool:
    """中文之间直接接；只要有一侧是拉丁字母/数字就补一个空格。"""
    if not a or not b:
        return False
    left_cjk = bool(_CJK.match(a[-1]))
    right_cjk = bool(_CJK.match(b[0]))
    return not (left_cjk and right_cjk)


def reflow(lines: Iterable[str]) -> list[str]:
    """把被 PPT 文本框切碎的行合并回段落。

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

def build_page(no: int, raw: str, boilerplate: dict[str, str]) -> dict:
    """单页 → 账本里的一个 page 记录。"""
    lines = split_lines(raw)
    kept = strip_boilerplate(lines, boilerplate)
    paras = reflow(kept)
    text = "\n".join(paras)
    return {
        "no": no,
        "chars_raw": len(raw.strip()),
        "chars_clean": len(text),
        "text": text,
        "lines_removed": len(lines) - len(kept),
        # 「没有文字层」和「字数很少」是两回事：封面只有几个字，但文字层是有的
        "has_text_layer": len(raw.strip()) > 0,
        "is_blank": len(text.strip()) < 3,
    }


def ingest(pdf_path: str, images_dir: str | None = None, scale: float = 1.6,
           render_images: bool = True) -> dict:
    """摄取一个讲义 PDF，返回账本用的数据结构（不落盘）。"""
    doc = pdfium.PdfDocument(pdf_path)
    try:
        n = len(doc)
        raw_pages = [doc[i].get_textpage().get_text_range() for i in range(n)]
        per_page_lines = [split_lines(t) for t in raw_pages]
        boilerplate = detect_boilerplate(per_page_lines)
        pages = [build_page(i + 1, raw_pages[i], boilerplate) for i in range(n)]

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
