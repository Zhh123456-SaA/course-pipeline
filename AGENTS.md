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
  vendor/                 自带的第三方库（脚本生成，不入库）
  scripts/setup_vendor.py 建依赖库（取 wheel 解包）
  src/                    源码
  tests/                  测试
  run.py                  命令行入口
  library.path            本机配置：学习库在哪（不入库）

<学习库>\<课程名>\          数据（Obsidian 库）
  source/                 用户放的原始素材（只读）
  .ledger/                账本（唯一真相源；Obsidian 默认忽略点开头目录）
  assets/                 从素材抽出的图片
  notes/                  生成的笔记
  cards/                  Anki 卡片导出
```

**库的位置不要写死。** 一律走 `src/libroot.py` 解析：
环境变量 `COURSE_LIB` → 程序目录下的 `library.path` → 兜底 `<程序目录>\..\学习库`。
本机当前是 `D:\学习库`（独立目录，不放在开发工作区里面）。
测试侧用 `tests/_pick.py` 的 `library_root()`，它直接复用同一个 `libroot`，
**不要自己拼相对路径** —— 两处各写一份必然漂移，测试就会看错库。

## 运行方式

```powershell
# 本机没有 bash / npm，Python 用 miniforge 基础解释器 + 项目自带 vendor
python run.py --help
```

## 环境事实（省得再摸一遍）

- 无 bash、无 npm、无 DSH_CHECKOUT。
- `pip install --target` 与 `python -m venv` 在本沙箱下都会失败 → 依赖一律用
  `python scripts\setup_vendor.py` 取 wheel 解包到 `vendor/`（仓库自包含，克隆即可重建）。
- 控制台是 GBK：**不要直接 print 中文/特殊符号**，一律写 UTF-8 文件再用读取工具看。
- `github.com` 实测：**`http.sslBackend=schannel` 会报 `SEC_E_NO_CREDENTIALS`**，
  改 `git config http.sslBackend openssl` 即通；走本地代理 `127.0.0.1:7897`。
- **git 调编辑器会失败**（沙箱里 MSYS 的 `sh.exe`/`true.exe` 起不来，`Win32 error 5`）
  → 任何需要编辑器的 git 操作都要用 `-m` / `-C` / `-F` 提供信息，或改走
  「手动 commit + `git rebase --quit`」。
- HuggingFace 不通，模型源走 ModelScope。
