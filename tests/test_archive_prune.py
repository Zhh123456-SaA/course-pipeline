# -*- coding: utf-8 -*-
"""归档清理测试：账本里「串门」进来的脏数据必须能被清掉，又不能在没数据源时误清。

背景（真实事故）：用户的 `学习库\普通化学` 账本里躺着 **4 组不属于它的批注**
（物理 9 条、生物 3 条、还有一门 photoredox 1 条），全是早期「归档不按课程隔离」
留下的。修复后这些脏数据依然清不掉 —— 因为 cmd_archive 见 `by_sha1` 为空就
直接早退，永远不写账本。

于是要区分两种「空」：
  ① 一组批注源都没扫到（源目录被清了）→ **不许动账本**，否则用户攒的批注一锅端；
  ② 扫到了，但全不属于本课程 → **要用空结果覆盖**，把历史脏数据清掉。

这个测试用临时目录构造这两种情形，不碰任何真实课程。

跑法：python tests/test_archive_prune.py
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

HERE = os.path.dirname(os.path.abspath(__file__))
PROJ = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(PROJ, "src"))
import archive  # noqa: E402

FAILS: list[str] = []
PASSES: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    (PASSES if ok else FAILS).append(f"{name}{(' — ' + detail) if detail else ''}")


ROOT = os.path.join(tempfile.gettempdir(), "course-pipeline-prune", str(os.getpid()))


def _cleanup() -> None:
    shutil.rmtree(ROOT, ignore_errors=True)
    parent = os.path.dirname(ROOT)
    try:
        if os.path.isdir(parent) and not os.listdir(parent):
            os.rmdir(parent)
    except OSError:
        pass


import atexit  # noqa: E402
atexit.register(_cleanup)

LIB = os.path.join(ROOT, "lib")
COURSE = "测试课"
CROOT = os.path.join(LIB, COURSE)
SRC = os.path.join(CROOT, "source")
ANN_ROOT = os.path.join(ROOT, "ann")
PAGES = os.path.join(ANN_ROOT, "pages")

os.makedirs(SRC, exist_ok=True)
os.makedirs(PAGES, exist_ok=True)


def sha1_bytes(b: bytes) -> str:
    return hashlib.sha1(b).hexdigest()


def put_source(name: str, body: bytes) -> str:
    with open(os.path.join(SRC, name), "wb") as f:
        f.write(body)
    return sha1_bytes(body)


def put_ann(sha: str, n: int = 1) -> None:
    d = os.path.join(PAGES, sha)
    os.makedirs(d, exist_ok=True)
    with open(os.path.join(d, "annotations.json"), "w", encoding="utf-8") as f:
        json.dump([{"page": i + 1, "question": f"问题{i + 1}"} for i in range(n)], f)


def write_ledger(by_sha1: dict) -> None:
    led = os.path.join(CROOT, ".ledger")
    os.makedirs(led, exist_ok=True)
    with open(os.path.join(led, "annotations.json"), "w", encoding="utf-8") as f:
        json.dump({"version": 1, "by_sha1": by_sha1}, f)


def read_ledger() -> dict:
    p = os.path.join(CROOT, ".ledger", "annotations.json")
    if not os.path.exists(p):
        return {}
    with open(p, encoding="utf-8") as f:
        return json.load(f)


def run(*args: str) -> str:
    env = dict(os.environ, COURSE_LIB=LIB)
    r = subprocess.run([sys.executable, os.path.join(PROJ, "run.py"), *args],
                       capture_output=True, text=True, encoding="utf-8",
                       cwd=PROJ, env=env)
    return (r.stdout or "") + (r.stderr or "")


# ---------------------------------------------------------------- 1 本课程的批注能入账

mine = put_source("我的讲义.pdf", b"%PDF-1.4 fake mine")
put_ann(mine, 3)
# 另一门课的素材**不在**本课程 source/ 里
theirs = sha1_bytes(b"%PDF-1.4 fake theirs")
put_ann(theirs, 2)

data, rep = archive.build_archive(CROOT, [ANN_ROOT])
check("扫到的批注组数被如实报出（2 组）", data.get("scanned") == 2,
      str(data.get("scanned")))
check("只把属于本课程的收进 by_sha1", list(data["by_sha1"]) == [mine],
      str(list(data["by_sha1"])))
check("  本课程那条挂对了讲次", data["by_sha1"][mine].get("lecture_id") == "我的讲义",
      str(data["by_sha1"][mine].get("lecture_id")))
check("不属于本课程的只在报告里出现", any("[other]" in r for r in rep), str(rep))

# ---------------------------------------------------------------- 2 脏数据能清掉

# 模拟历史脏数据：账本里躺着两组根本没有 lecture_id 的外来记录
write_ledger({
    theirs: {"sha1": theirs, "lecture_id": None, "annotations": [{"page": 1}]},
    "deadbeef": {"sha1": "deadbeef", "lecture_id": None, "annotations": [{"page": 2}]},
})
check("测试前置：账本里确实有脏数据", len(read_ledger()["by_sha1"]) == 2)

# 让本课程也**没有任何**属于自己的批注 → 结果为空。
# 这正是原来清不掉的情形：结果为空 → 早退 → 脏数据永远留着。
os.remove(os.path.join(SRC, "我的讲义.pdf"))
out = run("--course", COURSE, "archive", "--ann-root", ANN_ROOT)
after = read_ledger()
check("结果为空但扫到了批注源 → 脏数据被清掉（不再早退）",
      after.get("by_sha1") == {}, str(list(after.get("by_sha1", {}))))
check("  报告说清了「扫到几组 / 几组入账」",
      "扫到 2 组" in out and "0 组属于本课程" in out, out.strip()[-160:])

# ---------------------------------------------------------------- 3 没有数据源时不许动账本

write_ledger({mine: {"sha1": mine, "lecture_id": "我的讲义",
                     "annotations": [{"page": 1}, {"page": 2}]}})
before = read_ledger()
# 指向一个不存在的批注源目录
out2 = run("--course", COURSE, "archive", "--ann-root",
           os.path.join(ROOT, "不存在的目录"))
check("一组批注源都没扫到 → 账本原样不动（不能误清）",
      read_ledger() == before, str(read_ledger()))
check("  并且明确警告「账本保持原样」", "账本保持原样" in out2, out2.strip()[-160:])

# ---------------------------------------------------------------- 4 幂等

run("--course", COURSE, "archive", "--ann-root", ANN_ROOT)
snap_a = read_ledger()
run("--course", COURSE, "archive", "--ann-root", ANN_ROOT)
check("再跑一遍账本字节不变（幂等）", read_ledger() == snap_a)

# ---------------------------------------------------------------- 汇总

for p in PASSES:
    print("PASS  " + p)
for f_ in FAILS:
    print("FAIL  " + f_)
print("=" * 60)
print(f"通过 {len(PASSES)} / 失败 {len(FAILS)}")
raise SystemExit(1 if FAILS else 0)
