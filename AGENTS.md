# AGENTS.md — 课程流水线（course-pipeline）

> 位置：`D:\deepseek harness\course-pipeline`。AI 每次在这里开工前先读本文件。

## 这是什么

把课程素材（讲义 PDF / 视频 / 字幕）变成**可反复重跑的知识库**，并接上 Obsidian 与 Anki。
需求确认记录在 `../.ai-memory/course-pipeline-req.md`，**那是唯一判据**。

## 硬性规则（继承工作区根 AGENTS.md）

1. 每个功能完成并通过测试后必须 `git commit`（一次功能一个 commit，可回滚）。
2. 每次改动必须更新或新增测试。
3. 先保证可运行，再优化；不做无关重构。
4. 需求未收敛不写代码，确认未落盘视为未确认。
5. AI 自测通过 ≠ 人工验收通过；验收步骤必须交给人类执行。
6. 不主动 push / merge / 发布。

## 本项目的三条铁律

1. **账本是唯一真相源**（`.ledger/` 下的 JSON）。`assets/`、`out/` 随时可删，必须能从账本无损重建。
2. **`source/` 只读**。程序永远不写、不改、不删用户放进来的原始素材。
3. **幂等**：同样的输入跑两遍，产物必须字节完全一致。任何新功能都要配一条幂等断言。

## 目录约定

```
course-pipeline/          程序（代码）
  vendor/                 自带的第三方库（wheel 解包，见 .course-tools/setup_vendor.py）
  src/                    源码
  tests/                  测试
  run.py                  命令行入口

..\学习库\<课程名>\        数据（Obsidian 库）
  source/                 用户放的原始素材（只读）
  .ledger/                账本（Obsidian 默认忽略点开头目录）
  assets/                 从素材抽出的图片
  notes/                  生成的笔记
```

## 运行方式

```powershell
# 本机没有 bash / npm，Python 用 miniforge 基础解释器 + 项目自带 vendor
python run.py --help
```

## 环境事实（省得再摸一遍）

- 无 bash、无 npm、无 DSH_CHECKOUT。
- `pip install --target` 与 `python -m venv` 在本沙箱下都会失败 → 依赖一律用
  `python ..\.course-tools\setup_vendor.py` 的方式取 wheel 解包到 `vendor/`。
- 控制台是 GBK：**不要直接 print 中文/特殊符号**，一律写 UTF-8 文件再用读取工具看。
- `github.com` 时通时断；HuggingFace 不通，模型源走 ModelScope。
- ffmpeg / ffprobe 在 `..\.course-tools\bin`；yt-dlp 库版在 `..\.course-tools\pylibs`。
