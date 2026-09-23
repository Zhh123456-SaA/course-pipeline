# -*- coding: utf-8 -*-
"""批注位测试：每页的批注在整篇重渲染后必须原样还在。

为什么需要这条测试：第一版把唯一的批注区放在 69 页笔记的最末尾，
用户直接指出「这等于没有批注功能」。现在改成每页紧跟一个批注位，
而批注位在生成块**内部** —— 如果没有「抠出来再塞回去」这一步，
重跑就会把用户写的字冲掉。

跑法：python tests/test_annotation.py
"""
from __future__ import annotations

import os
import subprocess
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
COURSE = "普通化学"
LIB = os.path.abspath(os.path.join(PROJ, "..", "学习库", COURSE))
NOTE = os.path.join(LIB, "notes", "01 GC01.md")

sys.path.insert(0, os.path.join(PROJ, "vendor"))
sys.path.insert(0, os.path.join(PROJ, "src"))
from render import extract_annotations, annotation_block, ANNOT_BEGIN  # noqa: E402

FAILS: list[str] = []
PASSES: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    (PASSES if ok else FAILS).append(f"{name}{(' — ' + detail) if detail else ''}")


def run(*args: str) -> str:
    r = subprocess.run([sys.executable, os.path.join(PROJ, "run.py"), *args],
                       capture_output=True, text=True, encoding="utf-8", cwd=PROJ)
    return (r.stdout or "") + (r.stderr or "")


def read_note() -> str:
    with open(NOTE, encoding="utf-8") as f:
        return f.read()


# ---------------------------------------------------------------- 0 前置

if not os.path.exists(NOTE):
    print(f"找不到 {NOTE}，先跑一次 run.py --course {COURSE} all")
    raise SystemExit(2)

original = read_note()

# ---------------------------------------------------------------- 1 结构

n_slots = original.count("批注区 p")
check("每页都有一个批注位（开始+结束标记成对）",
      n_slots >= 69 * 2, f"标记出现 {n_slots} 次")

pages_with_slot = [int(m) for m in
                   __import__("re").findall(r"批注区 p(\d+) 开始", original)]
check("批注位覆盖第 1 页到第 69 页",
      pages_with_slot[:3] == [1, 2, 3] and len(pages_with_slot) == 69,
      f"共 {len(pages_with_slot)} 个，前几个 {pages_with_slot[:5]}")

# 批注位必须紧跟它所批注的那一页
idx_p4 = original.find("### 第 4 页")
idx_slot4 = original.find(ANNOT_BEGIN.format(n=4))
idx_p5 = original.find("### 第 5 页")
check("第 4 页的批注位在第 4 页与第 5 页之间（就近，不用滚到底）",
      idx_p4 != -1 and idx_p4 < idx_slot4 < idx_p5,
      f"p4={idx_p4} slot={idx_slot4} p5={idx_p5}")

# ---------------------------------------------------------------- 2 写进去

MY_NOTE = "这里是第 4 页的批注：敬业乐群 = 专心学业 + 和同学处好关系。"
my_block = annotation_block(4, f"> {MY_NOTE}\n>\n> ")
import re  # noqa: E402
patched = re.sub(
    r"[ \t]*<!-- 批注区 p4 开始[^>]*-->.*?<!-- 批注区 p4 结束 -->",
    lambda _m: my_block, original, count=1, flags=re.S)
check("测试前置：批注已写入第 4 页", MY_NOTE in patched)

# 还原钩子：**必须保证被打断（超时/崩溃/Ctrl+C）也会还原**。
# 踩过：上一次跑被超时杀掉，残留的测试批注把 s1_idempotent 的「重建一致」断言带红。
import atexit  # noqa: E402


def _restore() -> None:
    try:
        with open(NOTE, "w", encoding="utf-8", newline="\n") as f:
            f.write(original)
    except OSError:
        pass


atexit.register(_restore)

with open(NOTE, "w", encoding="utf-8", newline="\n") as f:
    f.write(patched)

# ---------------------------------------------------------------- 3 重跑

run("--course", COURSE, "all", "--force")
after = read_note()

check("整篇重渲染后，第 4 页的批注仍在", MY_NOTE in after,
      "批注被冲掉了" if MY_NOTE not in after else "")

ann = extract_annotations(after)
check("第 4 页的批注能被结构化读出", MY_NOTE in ann.get(4, ""),
      f"读出的是 {ann.get(4, '')[:40]!r}")

check("批注被放在了正确的页（没有串页）",
      MY_NOTE in ann.get(4, "") and MY_NOTE not in ann.get(5, ""),
      "串到第 5 页了" if MY_NOTE in ann.get(5, "") else "")

# 批注位不能重复长出来
n_slot4 = len(re.findall(r"<!-- 批注区 p4 开始", after))
check("第 4 页的批注位没有重复", n_slot4 == 1, f"出现 {n_slot4} 次")

n_slots_after = len(re.findall(r"批注区 p\d+ 开始", after))
check("重跑后批注位总数不变", n_slots_after == 69, f"{n_slots_after} 个")

# ---------------------------------------------------------------- 4 幂等

run("--course", COURSE, "all", "--force")
again = read_note()
check("带批注的笔记再跑一遍字节不变", again == after,
      "重跑产生了差异" if again != after else "")

# ---------------------------------------------------------------- 5 还原

with open(NOTE, "w", encoding="utf-8", newline="\n") as f:
    f.write(original)
run("--course", COURSE, "all")

for p in PASSES:
    print("PASS  " + p)
for f_ in FAILS:
    print("FAIL  " + f_)
print("=" * 60)
print(f"通过 {len(PASSES)} / 失败 {len(FAILS)}")
raise SystemExit(1 if FAILS else 0)
