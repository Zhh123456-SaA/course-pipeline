# -*- coding: utf-8 -*-
"""学习记录入账的测试。

关键要守的三件事（每条都对应一个真实需求）：

1. **重复导入不产生重复**。用户原话：「更新很烦，之前所有内容均重复」——
   他每次导出都带全部内容，不做内容哈希去重就会越导越乱。
2. **自评的变化要留痕**。`grade_log` 记下"什么时候从 ❌ 变成 ✅"——
   这是"先攒数据"（Q17）里唯一能看出进步的东西。
3. **坏数据不许炸整轮**。导出的 JSON 是浏览器生成的，一张图坏掉
   不该让整批学习记录都进不来。

测试全程在临时目录里跑，**不碰 D:\\学习库**。
跑法：python tests/test_study.py
"""
from __future__ import annotations

import base64
import json
import os
import shutil
import sys
import tempfile
import zlib

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

HERE = os.path.dirname(os.path.abspath(__file__))
PROJ = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(PROJ, "src"))
import study as S  # noqa: E402

FAILS: list[str] = []
PASSES: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    (PASSES if ok else FAILS).append(f"{name}{(' — ' + detail) if detail else ''}")


ROOT = os.path.join(tempfile.gettempdir(), "course-pipeline-study", str(os.getpid()))


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

LIB = os.path.join(ROOT, "某课程")
os.makedirs(LIB, exist_ok=True)


def png(color: tuple[int, int, int]) -> str:
    """造一张 1x1 真 PNG 的 data URL（不糊弄解码）。"""
    def chunk(tag: bytes, data: bytes) -> bytes:
        c = tag + data
        return len(data).to_bytes(4, "big") + c + zlib.crc32(c).to_bytes(4, "big")
    raw = b"\x00" + bytes(color)
    body = (b"\x89PNG\r\n\x1a\n"
            + chunk(b"IHDR", (1).to_bytes(4, "big") + (1).to_bytes(4, "big")
                    + bytes([8, 2, 0, 0, 0]))
            + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b""))
    return "data:image/png;base64," + base64.b64encode(body).decode()


IMG_A, IMG_B, IMG_C = png((200, 20, 20)), png((20, 20, 200)), png((20, 200, 20))

# ---------------------------------------------------------------- 1 基本合并

d = S.load_study(LIB)
check("空账本读出默认结构", d == {"version": 1, "lessons": {}}, str(d))

rep = S.import_export(LIB, d, {
    "lesson": "L1", "title": "第一节", "mode": "teach-first",
    "exported_at": "2026-01-01T10:00:00",
    "grades": {"L1.1#0": "no", "L1.1#1": "mid", "L1.2#0": "ok"},
    "asks": [{"q": "问题甲"}, {"q": "问题乙"}],
    "shots": [{"d": IMG_A, "n": "第1题"}, {"d": IMG_B}],
})
S.save_study(LIB, d)
rec = d["lessons"]["L1"]
check("自评进账本", rec["grades"] == {"L1.1#0": "no", "L1.1#1": "mid", "L1.2#0": "ok"})
check("追问进账本（按文字去重）",
      [x["q"] for x in rec["asks"]] == ["问题甲", "问题乙"])
check("手写图进账本", len(rec["shots"]) == 2)
check("图真的落盘了", os.path.isdir(S.shots_dir(LIB))
      and len(os.listdir(S.shots_dir(LIB))) == 2)
check("账本里的 file 字段指向真实文件",
      all(os.path.exists(os.path.join(LIB, x["file"])) for x in rec["shots"]))
check("报告说清了新增数量", any("新增 3 条" in r for r in rep), str(rep))

# ---------------------------------------------------------------- 2 重复导入

n_before = len(os.listdir(S.shots_dir(LIB)))
rep2 = S.import_export(LIB, d, {
    "lesson": "L1", "title": "第一节", "mode": "teach-first",
    "exported_at": "2026-01-02T10:00:00",
    "grades": {"L1.1#0": "no", "L1.1#1": "mid", "L1.2#0": "ok"},   # 一模一样
    "asks": [{"q": "问题甲"}, {"q": "问题乙"}],
    "shots": [{"d": IMG_A, "n": "第1题"}, {"d": IMG_B}],
})
check("★ 同一份重复导入：自评 0 新增 0 变化",
      any("新增 0 条、变化 0 条" in r for r in rep2), str(rep2))
check("★ 同一份重复导入：图全部按重复跳过",
      any("新增 0 张、重复跳过 2 张" in r for r in rep2), str(rep2))
check("★ 重复导入后账本条数不变", len(d["lessons"]["L1"]["shots"]) == 2)
check("★ 重复导入后磁盘文件数不变",
      len(os.listdir(S.shots_dir(LIB))) == n_before, str(n_before))
check("★ 同一句话问两遍只留一条",
      len([x for x in d["lessons"]["L1"]["asks"] if x["q"] == "问题甲"]) == 1)

# ---------------------------------------------------------------- 3 变化留痕

S.import_export(LIB, d, {
    "lesson": "L1", "exported_at": "2026-01-03T10:00:00",
    "grades": {"L1.1#0": "ok", "L1.1#1": "mid", "L1.2#0": "ok"},   # 第一条 ❌→✅
    "shots": [{"d": IMG_A, "n": "重写了一遍"}, {"d": IMG_C, "n": "第3题"}],
})
rec = d["lessons"]["L1"]
check("★ 自评变化被记下（❌→✅）",
      any(x.get("from") == "no" and x.get("to") == "ok" for x in rec["grade_log"]),
      json.dumps(rec["grade_log"], ensure_ascii=False))
check("当前值以最新为准", rec["grades"]["L1.1#0"] == "ok")
check("  没变的不会重复记进日志",
      len([x for x in rec["grade_log"] if x["q"] == "L1.1#1"]) == 1)
check("新图入库、旧图仍只一份",
      len(rec["shots"]) == 3 and len(os.listdir(S.shots_dir(LIB))) == 3)
check("  重复那张只更新标注，不重复入库",
      any(x["sha1"].startswith("") and x["note"] == "重写了一遍" for x in rec["shots"]))

# ---------------------------------------------------------------- 4 坏数据不炸

rep4 = S.import_export(LIB, d, {
    "lesson": "L2", "exported_at": "2026-01-04T10:00:00",
    "grades": {"L2.1#0": "乱写", "L2.1#1": "ok"},     # 非法档位要忽略
    "shots": ["data:image/jpeg;base64,@@@不是base64@@@", {"d": None}, "裸字符串"],
})
check("非法自评档位被忽略（只收 ok/mid/no）",
      d["lessons"]["L2"]["grades"] == {"L2.1#1": "ok"},
      str(d["lessons"]["L2"]["grades"]))
check("坏图不让整轮失败（新小节照样建起来了）", "L2" in d["lessons"])
# 三条都该被拒：坏 base64 / d 为空 / 不是 data URL 的裸字符串
# （裸字符串 base64 解出来是空字节 —— 空的不算图，不能当"老格式"收下）
check("★ 三条坏图全部如实报出、且一条都没入库",
      any("坏数据 3 条" in r for r in rep4) and d["lessons"]["L2"]["shots"] == [],
      f"{rep4} | shots={d['lessons']['L2']['shots']}")

# 老格式兼容：数组里直接放 data URL 字符串（不带 {d,n} 包装）
rep4b = S.import_export(LIB, d, {
    "lesson": "L2b", "exported_at": "2026-01-04T11:00:00",
    "shots": [IMG_A, IMG_B],
})
check("老格式（数组里直接放 data URL）照样能收",
      len(d["lessons"]["L2b"]["shots"]) == 2, str(rep4b))

# 缺 lesson 字段
rep5 = S.import_export(LIB, d, {"grades": {"x": "ok"}})
check("缺 lesson 字段时明确跳过、不炸", any("没有 lesson 字段" in r for r in rep5), str(rep5))

# ---------------------------------------------------------------- 5 文件 / 目录导入

TMPD = os.path.join(ROOT, "exports")
os.makedirs(TMPD, exist_ok=True)
for nm, lesson in (("study-a.json", "L3"), ("study-b.json", "L4"), ("other.json", "L9")):
    with open(os.path.join(TMPD, nm), "w", encoding="utf-8") as f:
        json.dump({"lesson": lesson, "grades": {lesson + ".1#0": "ok"}}, f)
check("目录里只认 study-*.json（别把别的 json 也吞了）",
      len(S.find_exports(TMPD)) == 2, str(S.find_exports(TMPD)))
S.merge_file(LIB, TMPD)
fresh = S.load_study(LIB)          # ★ merge_file 是自己 load/save 的，d 是旧的
check("目录导入把两份都并进来了",
      "L3" in fresh["lessons"] and "L4" in fresh["lessons"],
      str(sorted(fresh["lessons"])))
check("  没吞 other.json", "L9" not in fresh["lessons"])

with open(os.path.join(TMPD, "study-bad.json"), "w", encoding="utf-8") as f:
    f.write("{ 这不是 json")
rep6 = S.merge_file(LIB, TMPD)
check("★ 坏 JSON 只跳过那一个文件，不炸整轮",
      any("读不出来" in r for r in rep6), str(rep6))
fresh = S.load_study(LIB)
check("  而且好的那几份照样并进来了",
      "L3" in fresh["lessons"] and "L4" in fresh["lessons"], str(sorted(fresh["lessons"])))

# ---------------------------------------------------------------- 6b 课件标记（划线）

# ★ 先 reload 拿最新的（`merge_file` 是自己 load→改→save 的，内存里的 d 早已过期），
#   改完再 save。少了 reload 会抹掉别人写的；少了 save 会丢掉自己写的。
d = S.load_study(LIB)

# 用户原话：「我希望我可以先看课件，**划线记笔记问问题**」。
# 划线与提问是两种信号（划线 = 这里重要、零成本、量大；提问 = 这里不懂、要打字、量小）。
rep7 = S.import_export(LIB, d, {
    "lesson": "L5", "mode": "survey", "exported_at": "2026-02-01T10:00:00",
    "marks": [
        {"p": 25, "r": [10.0, 20.0, 30.0, 8.0], "q": ""},
        {"p": 27, "r": [5.0, 40.0, 44.0, 12.0], "q": "脂筏算不算细胞器？"},
        {"p": 27, "r": [8.5, 60.1, 50.2, 9.9], "q": ""},
    ],
})
check("★ 课件标记进账本（划线 + 提问两种都收）",
      len(d["lessons"]["L5"]["marks"]) == 3, str(d["lessons"]["L5"]["marks"]))
check("  坐标被规整到一位小数", d["lessons"]["L5"]["marks"][2]["r"] == [8.5, 60.1, 50.2, 9.9])
check("  报告区分了「共几处」与「其中几条提问」",
      any("新增 3 处" in r and "累计提问 1 条" in r for r in rep7), str(rep7))

# 重复导入（他整本重导是常态）
rep8 = S.import_export(LIB, d, {
    "lesson": "L5", "mode": "survey", "exported_at": "2026-02-02T10:00:00",
    "marks": [
        {"p": 25, "r": [10.0, 20.0, 30.0, 8.0], "q": ""},
        {"p": 27, "r": [5.0, 40.0, 44.0, 12.0], "q": "脂筏算不算细胞器？"},
    ],
})
check("★ 同一批标记重复导入：0 新增、全部跳过",
      any("新增 0 处" in r and "重复跳过 2 处" in r for r in rep8), str(rep8))
check("★ 重复导入后标记数不变", len(d["lessons"]["L5"]["marks"]) == 3)

# 同一个框但改了问题 → 应当**算新的一条**（问题是不同的信号）
S.import_export(LIB, d, {
    "lesson": "L5", "mode": "survey", "exported_at": "2026-02-03T10:00:00",
    "marks": [{"p": 27, "r": [5.0, 40.0, 44.0, 12.0], "q": "换了个问法：为什么？"}],
})
check("★ 同一个框改了问题 → 算新的一条（问题不同就是不同信号）",
      len(d["lessons"]["L5"]["marks"]) == 4, str(len(d["lessons"]["L5"]["marks"])))

# 坏数据
rep9 = S.import_export(LIB, d, {
    "lesson": "L6", "mode": "survey", "exported_at": "2026-02-04T10:00:00",
    "marks": ["裸字符串", {"p": "不是数字", "r": [1, 2, 3, 4]},
              {"p": 3, "r": [1, 2, 3]}, {"p": 3, "r": [1, 2, 3, 4], "q": "好的这条"}],
})
check("★ 坏标记全部拒收且如实报数（只有一条有效）",
      len(d["lessons"]["L6"]["marks"]) == 1 and any("坏数据 3 条" in r for r in rep9),
      f"{d['lessons']['L6']['marks']} | {rep9}")

# ---------------------------------------------------------------- 6c 按页汇总 + 汇总文案

S.save_study(LIB, d)        # 6b 的三次 import_export 改在内存 d 上，这里落盘
bp = S.marks_by_page(LIB)
check("★ marks_by_page 按页汇总（第 27 页有 3 处、其中 2 条提问）",
      bp.get(27, {}).get("n") == 3 and bp.get(27, {}).get("q") == 2, str(bp.get(27)))
check("  跨小节汇总（L5 与 L6 都算进来）", 3 in bp and bp[3]["n"] == 1, str(sorted(bp)))
sm = S.summarize(LIB)
check("★ 汇总里单列课件标记（不与自评混在一起）",
      any("课件标记" in x for x in sm), str(sm))
check("  并能报出标记最多的页",
      any("标记最多的页" in x for x in sm), str(sm))

# ---------------------------------------------------------------- 7 幂等 + 汇总

before = open(S.study_ledger_path(LIB), encoding="utf-8").read()
S.merge_file(LIB, TMPD)
check("同样的导入再跑一遍，账本字节不变（幂等）",
      open(S.study_ledger_path(LIB), encoding="utf-8").read() == before)

summ = S.summarize(LIB)
check("汇总能报出节数/自评数/掌握比例",
      summ and "自评" in summ[0] and "%" in summ[0], summ[0] if summ else "")
check("  会/不确定/不会 分别计数",
      all(k in summ[0] for k in ("会", "不确定", "不会")), summ[0])

empty = os.path.join(ROOT, "空的")
os.makedirs(empty, exist_ok=True)
check("没有记录时给出可操作的提示（不是空白）",
      "还没有" in S.summarize(empty)[0] and "导出" in S.summarize(empty)[0])

# ---------------------------------------------------------------- 汇总

for p in PASSES:
    print("PASS  " + p)
for f_ in FAILS:
    print("FAIL  " + f_)
print("=" * 60)
print(f"通过 {len(PASSES)} / 失败 {len(FAILS)}")
raise SystemExit(1 if FAILS else 0)
