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
import shutil
import subprocess
import sys
import tempfile

# Windows 控制台默认 GBK：print 中文/emoji 会抛 UnicodeEncodeError 并让退出码变 1
# （明明全过却报失败）。强制 UTF-8 输出。
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

HERE = os.path.dirname(os.path.abspath(__file__))
PROJ = os.path.dirname(HERE)
sys.path.insert(0, HERE)
import _pick  # noqa: E402

# ★ 与 s1_idempotent 同一条铁律：测试不许把真实课程当测试床。
#   本测试要往讲次笔记里**写一段批注再重跑**，虽然末尾会还原，
#   但被打断（超时/崩溃）就会留残留 —— 实测已经污染过 s1_idempotent。
#   改成：复制到临时沙箱库跑，真实笔记一个字节都不碰。
_picked = _pick.pick(PROJ, prefer="普通化学")
if not _picked:
    print(f"{_pick.library_root(PROJ)} 下没有「素材+账本+笔记」齐全的课程，"
          f"先跑一次 run.py --course <课程> all")
    raise SystemExit(2)
COURSE, REAL_COURSE, FIRST_STEM = _picked
SANDBOX = os.path.abspath(os.path.join(
    tempfile.gettempdir(), "course-pipeline-annot", str(os.getpid())))
LIB = os.path.join(SANDBOX, COURSE)
NOTE = os.path.join(LIB, "notes", FIRST_STEM + ".md")

sys.path.insert(0, os.path.join(PROJ, "vendor"))
sys.path.insert(0, os.path.join(PROJ, "src"))
from render import extract_annotations, annotation_block, ANNOT_BEGIN  # noqa: E402

FAILS: list[str] = []
PASSES: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    (PASSES if ok else FAILS).append(f"{name}{(' — ' + detail) if detail else ''}")


def run(*args: str) -> str:
    env = dict(os.environ, COURSE_LIB=SANDBOX)
    r = subprocess.run([sys.executable, os.path.join(PROJ, "run.py"), *args],
                       capture_output=True, text=True, encoding="utf-8",
                       cwd=PROJ, env=env)
    return (r.stdout or "") + (r.stderr or "")


def read_note() -> str:
    with open(NOTE, encoding="utf-8") as f:
        return f.read()


# ---------------------------------------------------------------- 0 前置：搭沙箱

os.makedirs(LIB, exist_ok=True)
for sub in ("source", ".ledger"):
    s = os.path.join(REAL_COURSE, sub)
    if os.path.isdir(s):
        shutil.copytree(s, os.path.join(LIB, sub), dirs_exist_ok=True)
run("--course", COURSE, "all")

if not os.path.exists(NOTE):
    print(f"沙箱里没渲染出 {NOTE}")
    raise SystemExit(2)

# 页数从账本读，别写死（写死 69 只对某一门课成立）
import json  # noqa: E402
_pages = os.path.join(LIB, ".ledger", "pages")
_rec = json.load(open(os.path.join(_pages, os.listdir(_pages)[0]), encoding="utf-8"))
N_PAGES = int(_rec["page_count"])
TEST_PAGE = 4 if N_PAGES >= 5 else max(1, N_PAGES)

original = read_note()

# ---------------------------------------------------------------- 1 结构

n_slots = original.count("批注区 p")
check("每页都有一个批注位（开始+结束标记成对）",
      n_slots >= N_PAGES * 2, f"标记出现 {n_slots} 次")

pages_with_slot = [int(m) for m in
                   __import__("re").findall(r"批注区 p(\d+) 开始", original)]
check(f"批注位覆盖第 1 页到第 {N_PAGES} 页",
      pages_with_slot[:3] == [1, 2, 3] and len(pages_with_slot) == N_PAGES,
      f"共 {len(pages_with_slot)} 个，前几个 {pages_with_slot[:5]}")

# 批注位必须紧跟它所批注的那一页
P = TEST_PAGE
idx_p4 = original.find(f"### 第 {P} 页")
idx_slot4 = original.find(ANNOT_BEGIN.format(n=P))
idx_p5 = original.find(f"### 第 {P + 1} 页")
check(f"第 {P} 页的批注位在第 {P} 页与第 {P + 1} 页之间（就近，不用滚到底）",
      idx_p4 != -1 and idx_p4 < idx_slot4 < idx_p5,
      f"p4={idx_p4} slot={idx_slot4} p5={idx_p5}")

# ---------------------------------------------------------------- 2 写进去

MY_NOTE = f"这里是第 {P} 页的批注：敬业乐群 = 专心学业 + 和同学处好关系。"
my_block = annotation_block(P, f"> {MY_NOTE}\n>\n> ")
import re  # noqa: E402
patched = re.sub(
    rf"[ \t]*<!-- 批注区 p{P} 开始[^>]*-->.*?<!-- 批注区 p{P} 结束 -->",
    lambda _m: my_block, original, count=1, flags=re.S)
check("测试前置：批注已写入第 %d 页" % P, MY_NOTE in patched)

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
# 沙箱用完即删（含异常退出）
atexit.register(lambda: _pick.cleanup_sandbox(SANDBOX))

with open(NOTE, "w", encoding="utf-8", newline="\n") as f:
    f.write(patched)

# ---------------------------------------------------------------- 3 重跑

run("--course", COURSE, "all", "--force")
after = read_note()

check(f"整篇重渲染后，第 {P} 页的批注仍在", MY_NOTE in after,
      "批注被冲掉了" if MY_NOTE not in after else "")

ann = extract_annotations(after)
check(f"第 {P} 页的批注能被结构化读出", MY_NOTE in ann.get(P, ""),
      f"读出的是 {ann.get(P, '')[:40]!r}")

check("批注被放在了正确的页（没有串页）",
      MY_NOTE in ann.get(P, "") and MY_NOTE not in ann.get(P + 1, ""),
      f"串到第 {P + 1} 页了" if MY_NOTE in ann.get(P + 1, "") else "")

# 批注位不能重复长出来
n_slot4 = len(re.findall(rf"<!-- 批注区 p{P} 开始", after))
check(f"第 {P} 页的批注位没有重复", n_slot4 == 1, f"出现 {n_slot4} 次")

n_slots_after = len(re.findall(r"批注区 p\d+ 开始", after))
check("重跑后批注位总数不变", n_slots_after == N_PAGES, f"{n_slots_after} 个")

# ---------------------------------------------------------------- 4 幂等

run("--course", COURSE, "all", "--force")
again = read_note()
check("带批注的笔记再跑一遍字节不变", again == after,
      "重跑产生了差异" if again != after else "")

# ---------------------------------------------------------------- 5 还原
# 还原的是**沙箱里**那份；真实课程的笔记从头到尾没被打开过。

with open(NOTE, "w", encoding="utf-8", newline="\n") as f:
    f.write(original)
run("--course", COURSE, "all")

_real = os.path.join(REAL_COURSE, "notes", FIRST_STEM + ".md")
_real_txt = open(_real, encoding="utf-8").read()
check("真实课程的讲次笔记里没有测试批注（测试没动用户数据）",
      MY_NOTE not in _real_txt)

for p in PASSES:
    print("PASS  " + p)
for f_ in FAILS:
    print("FAIL  " + f_)
print("=" * 60)
print(f"通过 {len(PASSES)} / 失败 {len(FAILS)}")
raise SystemExit(1 if FAILS else 0)
