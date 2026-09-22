# -*- coding: utf-8 -*-
"""归档通道测试：ppt-deepreader 的框选追问 → 学习库账本 → Obsidian 笔记。

为什么要这些断言（每条都对应一个真实约束）：
  1. 匹配靠 sha1 —— 对不上就不知道该批注属于哪一讲，整条通道是空的
  2. 批注必须进**账本**（真相源），笔记由账本渲染 —— 否则重跑会丢批注
  3. 重跑必须幂等（铁律 3）
  4. 未匹配的批注**不能丢**，只是暂不渲染（用户以后加了讲义就该出现）
  5. 归档不得破坏用户手写的 ✍️ 批注位
  6. 裁剪图链接必须合法（无空格）且文件真实存在

跑法：python tests/test_archive.py
"""
from __future__ import annotations

import hashlib
import os
import re
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
COURSE = "物理"
LIB = os.path.abspath(os.path.join(PROJ, "..", "学习库", COURSE))
NOTE = os.path.join(LIB, "notes", "05第五讲-动力学1_2026.md")
ANN_ROOTS = [r"D:\1\ppt-deepreader\.pdw_work"]

sys.path.insert(0, os.path.join(PROJ, "vendor"))
sys.path.insert(0, os.path.join(PROJ, "src"))
import archive  # noqa: E402

FAILS: list[str] = []
PASSES: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    (PASSES if ok else FAILS).append(f"{name}{(' — ' + detail) if detail else ''}")


def run(*args: str) -> str:
    r = subprocess.run([sys.executable, os.path.join(PROJ, "run.py"), *args],
                       capture_output=True, text=True, encoding="utf-8", cwd=PROJ)
    return (r.stdout or "") + (r.stderr or "")


def sha1(p: str) -> str:
    return hashlib.sha1(open(p, "rb").read()).hexdigest()


# ---------------------------------------------------------------- 0 前置

if not any(os.path.isdir(r) for r in ANN_ROOTS):
    print(f"跳过：批注数据源不存在 {ANN_ROOTS}")
    raise SystemExit(0)
if not os.path.isdir(os.path.join(LIB, "source")):
    print(f"跳过：找不到 {LIB}\\source")
    raise SystemExit(2)

# ---------------------------------------------------------------- 1 匹配

src_dir = os.path.join(LIB, "source")
sha_to_file = {}
for fn in os.listdir(src_dir):
    sha_to_file[sha1(os.path.join(src_dir, fn))] = fn

ann_dirs = archive.scan_annotation_dirs(ANN_ROOTS)
check("能扫到批注目录", len(ann_dirs) > 0, f"{len(ann_dirs)} 个")

matched = [s for s in ann_dirs if s in sha_to_file]
check("源讲义 sha1 能对上批注目录（归档的命门）", len(matched) >= 2,
      f"匹配 {len(matched)} 个：{[sha_to_file[s] for s in matched]}")

# ---------------------------------------------------------------- 2 归档落账本

run("--course", COURSE, "archive")
data = archive.load_archive(LIB)
by_sha = data.get("by_sha1", {})
check("批注已进账本 .ledger/annotations.json", len(by_sha) >= 3, f"{len(by_sha)} 组")
check("账本是落盘的 JSON 文件", os.path.exists(archive.archive_ledger_path(LIB)))

lec = "05第五讲-动力学1_2026"
target = [r for r in by_sha.values() if r.get("lecture_id") == lec]
check(f"能定位到「{lec}」的批注", len(target) == 1, str([r.get("lecture_id") for r in by_sha.values()]))
if target:
    rec = target[0]
    check("该讲批注条数为 6", rec["count"] == 6, str(rec["count"]))
    check("批注页号被记录", rec["pages"] == sorted(rec["pages"]), str(rec["pages"]))

unmatched = [r for r in by_sha.values() if not r.get("lecture_id")]
check("学习库里没有对应讲义的批注**不丢**（仍留在账本，只是不渲染）",
      len(unmatched) >= 1, f"{len(unmatched)} 组未匹配")

# ---------------------------------------------------------------- 3 渲染进笔记

run("--course", COURSE, "render")
with open(NOTE, encoding="utf-8") as f:
    text = f.read()

check("笔记已生成", os.path.exists(NOTE))
check("笔记里出现「追问记录」", "🤖 追问记录" in text)
check("第 10 页的原始问题被带进笔记", "怎么定义" in text and "收缩的方向" in text)

# 定位第 10 页小节
i10 = text.find("### 第 10 页")
i11 = text.find("### 第 11 页")
check("追问记录挂在**第 10 页**（不是别处）",
      i10 != -1 and i11 != -1 and "🤖 追问记录" in text[i10:i11])

# 顺序：讲义原文 → AI 追问 → 我的批注位
i_ann = text.find("🤖 追问记录", i10)
i_mine = text.find("批注区 p10 开始", i10)
check("顺序正确：原文 → AI 追问 → 我的手写位", i10 < i_ann < i_mine,
      f"page={i10} ann={i_ann} mine={i_mine}")

# ---------------------------------------------------------------- 4 图片

crops = re.findall(r"!\[[^\]]*\]\((\.\./assets/[^)]*ann/[^)]*)\)", text)
check("笔记里引用了裁剪图", len(crops) > 0, f"{len(crops)} 处")
bad = [u for u in crops if re.search(r"\s", u)]
check("裁剪图链接不含空格（合法 CommonMark）", not bad, str(bad[:2]))

# 全部图片链接都要指向真实文件
all_links = re.findall(r"!\[[^\]]*\]\(([^)]*)\)", text)
missing = [u for u in all_links
           if not os.path.exists(os.path.normpath(os.path.join(os.path.dirname(NOTE), u)))]
check("所有图片链接指向的文件都存在", not missing,
      f"{len(missing)} 个缺失，例如 {missing[:2]}")

# ---------------------------------------------------------------- 5 幂等

run("--course", COURSE, "archive")
run("--course", COURSE, "render")
with open(NOTE, encoding="utf-8") as f:
    text2 = f.read()
check("重跑 archive+render 后笔记字节不变", text2 == text,
      "重跑产生了差异" if text2 != text else "")

d2 = archive.load_archive(LIB)
check("重跑后账本内容不变",
      d2.get("by_sha1", {}).keys() == by_sha.keys() and
      all(d2["by_sha1"][k]["count"] == by_sha[k]["count"] for k in by_sha))

# 重复归档不能重复渲染
n_ann_block = len(re.findall(r"🤖 追问记录 #1", text2))
check("第 10 页的追问记录没有重复", n_ann_block == 1, f"{n_ann_block} 次")

# ---------------------------------------------------------------- 6 手写批注位共存

MARK = "\n\n我自己在第 10 页写的批注，不许被冲掉。\n"
with open(NOTE, "w", encoding="utf-8", newline="\n") as f:
    f.write(text.rstrip() + MARK)
run("--course", COURSE, "all")
with open(NOTE, encoding="utf-8") as f:
    after = f.read()
check("我手写的内容在重跑后仍在", "不许被冲掉" in after)
check("AI 追问记录也仍在", "🤖 追问记录" in after)
with open(NOTE, "w", encoding="utf-8", newline="\n") as f:
    f.write(text)   # 还原

# ---------------------------------------------------------------- 汇总

for p in PASSES:
    print("PASS  " + p)
for f_ in FAILS:
    print("FAIL  " + f_)
print("=" * 60)
print(f"通过 {len(PASSES)} / 失败 {len(FAILS)}")
raise SystemExit(1 if FAILS else 0)
