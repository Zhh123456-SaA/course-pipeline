# -*- coding: utf-8 -*-
"""S1 自测：幂等 + 手写内容不被覆盖。

跑法：
    python tests/s1_idempotent.py
退出码 0 = 全部通过。
"""
from __future__ import annotations

import atexit
import hashlib
import os
import re
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

# ★ 铁律：测试**绝不允许**把真实课程当测试床。
#   真实事故：本测试原来直接对着用户的 学习库\普通化学 跑，还在第 6 节
#   rmtree 掉它的 notes/ 与 assets/。虽然那些目录可重建，但「测试去删用户的
#   真实数据」本身就是设计错误 —— 万一哪天清理逻辑写错，删掉的就不是可重建的了。
#
#   现在：① 只把来源课程当**只读素材**；② 复制一份到临时沙箱库；
#        ③ 全部操作走 COURSE_LIB 指向沙箱；④ 跑完（含异常退出）删掉沙箱。
SRC_COURSE = "普通化学"
REAL_LIB = os.path.abspath(os.path.join(PROJ, "..", "学习库"))
SANDBOX = os.path.abspath(os.path.join(
    tempfile.gettempdir(), "course-pipeline-selftest", os.getpid().__str__()))
_picked = _pick.pick(PROJ, prefer=SRC_COURSE)
if not _picked:
    print(f"{REAL_LIB} 下没有「素材+账本+笔记」齐全的课程，"
          f"先跑一次 run.py --course <课程> all")
    raise SystemExit(2)
COURSE, REAL_COURSE, FIRST_STEM = _picked
LIB = os.path.join(SANDBOX, COURSE)
FAILS: list[str] = []
PASSES: list[str] = []


def _cleanup() -> None:
    _pick.cleanup_sandbox(SANDBOX)


atexit.register(_cleanup)


def check(name: str, ok: bool, detail: str = "") -> None:
    (PASSES if ok else FAILS).append(f"{name}{(' — ' + detail) if detail else ''}")


def snapshot(root: str, subdirs=("notes", ".ledger", "assets")) -> dict[str, str]:
    out: dict[str, str] = {}
    for sd in subdirs:
        base = os.path.join(root, sd)
        for dirpath, _dirs, files in os.walk(base):
            for fn in files:
                p = os.path.join(dirpath, fn)
                rel = os.path.relpath(p, root)
                with open(p, "rb") as f:
                    out[rel] = hashlib.sha256(f.read()).hexdigest()
    return out


def run(*args: str) -> str:
    env = dict(os.environ, COURSE_LIB=SANDBOX)
    r = subprocess.run([sys.executable, os.path.join(PROJ, "run.py"), *args],
                       capture_output=True, text=True, encoding="utf-8",
                       cwd=PROJ, env=env)
    return (r.stdout or "") + (r.stderr or "")


def diff(a: dict, b: dict) -> tuple[list, list, list]:
    added = sorted(set(b) - set(a))
    removed = sorted(set(a) - set(b))
    changed = sorted(k for k in set(a) & set(b) if a[k] != b[k])
    return added, removed, changed


# ---------------------------------------------------------------- 0 前置：搭沙箱

# 只复制「只读素材 source/」与「账本 .ledger/」；assets/ 与 notes/ 让程序自己重建。
# 真实课程到这里为止：后面所有写操作都打在 SANDBOX 里。
os.makedirs(LIB, exist_ok=True)
for sub in ("source", ".ledger"):
    s = os.path.join(REAL_COURSE, sub)
    if os.path.isdir(s):
        shutil.copytree(s, os.path.join(LIB, sub), dirs_exist_ok=True)
check("沙箱库建好了（不是真实课程）", os.path.isdir(LIB) and SANDBOX != REAL_LIB, LIB)
check("沙箱里搬来了账本", bool(os.listdir(os.path.join(LIB, ".ledger"))))
_real_before = snapshot(REAL_COURSE, (".ledger", "source"))

# ---------------------------------------------------------------- 1 第一次跑

run("--course", COURSE, "all")
snap1 = snapshot(LIB)
check("第一次产出非空", len(snap1) > 0, f"{len(snap1)} 个文件")

# ---------------------------------------------------------------- 2 再跑一遍（走缓存）

out2 = run("--course", COURSE, "all")
snap2 = snapshot(LIB)
a, r, c = diff(snap1, snap2)
check("第二次跑：没有新增文件", not a, str(a[:5]))
check("第二次跑：没有删除文件", not r, str(r[:5]))
check("第二次跑：没有任何文件内容变化", not c, str(c[:5]))
check("第二次跑：命中缓存（日志里有 skip）", "[skip]" in out2)

# ---------------------------------------------------------------- 3 强制重算（不走缓存）

run("--course", COURSE, "all", "--force")
snap3 = snapshot(LIB)
a, r, c = diff(snap2, snap3)
check("强制重算：账本字节不变", not any(k.startswith(".ledger") for k in a + r + c),
      f"changed={[k for k in c if k.startswith('.ledger')]}")
check("强制重算：笔记字节不变", not any(k.startswith("notes") for k in a + r + c),
      f"changed={[k for k in c if k.startswith('notes')]}")
check("强制重算：页图字节不变（PNG 编码确定性）",
      not any(k.startswith("assets") for k in a + r + c),
      f"changed={[k for k in c if k.startswith('assets')][:3]}")

# ---------------------------------------------------------------- 4 手写内容保护

note = os.path.join(LIB, "notes", FIRST_STEM + ".md")
with open(note, "r", encoding="utf-8") as f:
    original = f.read()
MARK = "\n\n我在这一行写了自己的笔记，程序不许动它。\n"
with open(note, "w", encoding="utf-8", newline="\n") as f:
    f.write(original.rstrip() + MARK)

run("--course", COURSE, "all", "--force")
with open(note, "r", encoding="utf-8") as f:
    after = f.read()
check("手写内容在重跑后仍在", "程序不许动它" in after)
check("生成块被刷新了（marker 仍在）",
      "<!-- gen:begin -->" in after and "<!-- gen:end -->" in after)

# 还原
with open(note, "w", encoding="utf-8", newline="\n") as f:
    f.write(original)

# ---------------------------------------------------------------- 5 拒绝写无标记文件

no_marker = os.path.join(LIB, "notes", "_测试无标记.md")
with open(no_marker, "w", encoding="utf-8") as f:
    f.write("# 这是我自己的文件，没有标记\n")
sys.path.insert(0, os.path.join(PROJ, "src"))
from render import merge_gen_block  # noqa: E402

try:
    merge_gen_block("# 我自己的文件\n没标记\n", "t", "body")
    check("无标记文件应当被拒绝改写", False, "居然没报错")
except ValueError:
    check("无标记文件被拒绝改写（不会覆盖用户文件）", True)
os.remove(no_marker)

# ---------------------------------------------------------------- 6 账本可重建

# 比什么：**生成块**是否可完全重建。
# 不比什么：批注槽里的内容 —— 那是用户写的，住在文件里、不在账本里，
# 删掉 notes/ 后本来就该丢。早先版本直接比整个文件，只要有手写批注就必然失败
# （实测：test_annotation 被超时打断留下残留，把这里带红）。
GEN_RE = re.compile(r"<!-- gen:begin -->(.*?)<!-- gen:end -->", re.S)
ANN_RE = re.compile(r"(<!-- 批注区 p\d+ 开始[^>]*-->)(.*?)(<!-- 批注区 p\d+ 结束 -->)", re.S)


def generated(view: str) -> str:
    m = GEN_RE.search(view)
    if not m:
        return ""
    # 把批注槽的内容抹掉再比：只比"程序生成的那部分"
    return ANN_RE.sub(lambda mm: mm.group(1) + mm.group(3), m.group(1))


with open(note, "r", encoding="utf-8") as f:
    before_view = f.read()

shutil.rmtree(os.path.join(LIB, "notes"), ignore_errors=True)
shutil.rmtree(os.path.join(LIB, "assets"), ignore_errors=True)
run("--course", COURSE, "render")
rebuilt_notes = os.path.isdir(os.path.join(LIB, "notes"))
check("删掉 notes/ 后能从账本重建笔记", rebuilt_notes)
run("--course", COURSE, "all")
with open(note, "r", encoding="utf-8") as f:
    after_view = f.read()
check("重建后生成块与原来完全一致（可从账本无损重建）",
      generated(before_view) == generated(after_view),
      f"gen 长度 {len(generated(before_view))} vs {len(generated(after_view))}")
check("批注槽仍然存在（结构没丢）",
      f"批注区 p1 开始" in after_view and f"批注区 p1 结束" in after_view)

# ---------------------------------------------------------------- 7 真实课程没被动过
#
# ★ 这条是本文件最重要的断言。测试存在的意义是「证明程序对」，
#   不是「顺手把用户的库改一遍」。整场跑完，素材课程的账本与素材必须字节不变。

_real_after = snapshot(REAL_COURSE, (".ledger", "source"))
_a, _r, _c = diff(_real_before, _real_after)
check("真实课程的账本/素材全程零改动（测试没动用户数据）",
      not (_a or _r or _c),
      f"新增{_a[:3]} 删除{_r[:3]} 改动{_c[:3]}")

# ---------------------------------------------------------------- 汇总

print("=" * 66)
for p in PASSES:
    print("PASS  " + p)
for f in FAILS:
    print("FAIL  " + f)
print("=" * 66)
print(f"通过 {len(PASSES)} / 失败 {len(FAILS)}")
raise SystemExit(1 if FAILS else 0)
