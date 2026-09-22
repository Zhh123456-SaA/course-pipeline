# course-pipeline · 课程流水线

把课程素材（讲义 PDF / 视频 / 字幕）变成**可反复重跑的知识库**，并接上 Obsidian 与 Anki。

> 需求确认记录在 `../.ai-memory/course-pipeline-req.md`，**那是唯一判据**。

## 它解决什么问题

网课学完就忘、讲义看完就散、Anki 卡片不知道从哪来。这个项目的目标是一条闭环：

```
① 摄取   素材 → 清洗 → 账本
② 结构化 账本 → 知识点（带出处）
③ 学习   一轮轮交互，状态写回
④ 固化   知识点/漏洞 → Anki 卡
⑤ 复习   Anki 间隔重复
⑥ 回流   低分卡 → 调优先级 → 回到 ②
```

**账本（JSON）是唯一真相源**；笔记、图片、卡片都是可以由它重建出来的视图。

## 现状

| 切片 | 内容 | 状态 |
|---|---|---|
| **S1** | 讲义 PDF → 清洗 → 账本 → Obsidian 笔记（含每页批注位） | ✅ 完成 |
| **S3** | **归档通道**：ppt-deepreader 的框选追问自动进学习库 | ✅ 完成 |
| S2 | 用 AI 从讲义页提炼知识点（KC 骨架） | 🔜 下一步 |
| S4 | Anki 卡片（带出处，可跳回原页） | ⬜ |
| S5 | 学习循环 + 今日学习清单 | ⬜ |
| S6 | 视频那一半（抽帧 + 字幕段落 ↔ 画面配对） | ⬜ |

## 归档通道（S3）

把 [ppt-deepreader](../ppt-deepreader) 的「框选追问」搬进学习库 —— 数据原本落在
`<项目>/.pdw_work/pages/<sha1>/annotations.json`，而 `.pdw_work` 被 git 忽略，**删目录即丢**。

```powershell
python run.py --course 物理 archive                               # 只归档
python run.py --course 物理 archive --ann-root "D:\别的\.pdw_work" # 换数据源
python run.py --course 物理 all                                   # ingest → archive → render
```

**怎么知道一条批注属于哪一讲**：ppt-deepreader 用 `sha1(源文件全部字节)` 当页图/批注的目录名，
所以对学习库 `source/` 里的 PDF 算 sha1 就能对上。对不上的批注**不会丢**，留在账本里，
等你把对应讲义放进 `source/` 后重跑即可渲染。

归档后每条追问挂在它所批注的**那一页**下面：

```
#### 🤖 追问记录 #1
> **原文**：接触力③：张力…
**❓ 我的问题**：怎么定义"收缩的方向？"
<AI 的解答>
![框选区域](../assets/…/ann/p010.jpg)
*（模型 … · 时间 · token 数）*
```

## 快速开始

```powershell
# 1. 建依赖库（只需一次；不出网环境的镜像可用 PIP_MIRROR 覆盖）
python scripts\setup_vendor.py

# 2. 把讲义 PDF 放进学习库的 source\ 目录
#    ..\学习库\<课程名>\source\*.pdf

# 3. 摄取 → 生成笔记
python run.py --course 普通化学 all

# 4. 体检（账本 / 图片 / 笔记 三者是否一致）
python run.py --course 普通化学 check
```

其他命令：

```powershell
python run.py --course <课程名> ingest           # 只摄取
python run.py --course <课程名> archive          # 只归档批注（S3）
python run.py --course <课程名> render           # 只渲染笔记
python run.py --course <课程名> clean            # 删 assets/ 与 notes/（可从账本重建）
python run.py --course <课程名> all --force      # 忽略缓存强制重算
```

## 测试

```powershell
python tests\s1_idempotent.py    # 幂等 + 手写内容保护 + 账本可重建
python tests\test_boilerplate.py # 页眉页脚剥离 + 图片链接合法性
python tests\test_annotation.py  # 每页批注位 + 重渲染后批注不丢
python tests\test_archive.py     # 归档通道：sha1 匹配 + 落账本 + 渲染 + 幂等
```

**四条都必须全绿才算通过**（当前 64 项）。测试**不需要联网、不需要 API Key**。
`test_archive.py` 在批注数据源不存在时会自动跳过（不算失败）。

## 目录

```
course-pipeline/          程序
├── run.py                命令行入口
├── src/
│   ├── ledger.py         账本读写（稳定序列化 + 原子写 + 内容指纹）
│   ├── pdf_source.py     讲义提取（页眉页脚剥离 / 断行重排 / 页图渲染）
│   ├── archive.py        归档通道（ppt-deepreader 批注 → 账本）
│   └── render.py         账本 → Obsidian 笔记（生成块 + 每页批注位）
├── scripts/setup_vendor.py  建依赖库
├── tests/                四个测试入口
└── vendor/               自带的第三方库（脚本生成，不入库）

..\学习库\<课程名>\        数据（Obsidian 库）
├── source/               原始素材（程序只读，永不修改）
├── .ledger/              账本（唯一真相源）
├── assets/               从素材抽出的页图
└── notes/                生成的笔记
```

## 三条铁律

1. **账本是唯一真相源**。`assets/`、`notes/` 随时可删，必须能从账本无损重建。
2. **`source/` 只读**。程序永远不写、不改、不删你放进来的原始素材。
3. **幂等**。同样的输入跑两遍，产物字节完全一致。

## 你写的东西不会被覆盖

生成的笔记里有两类「你的地盘」：

- 每一页下面紧跟一个 `<!-- 批注区 pN 开始 -->` … `结束` 的批注位；
- 文件末尾的 `## 我的笔记` 一节。

程序重跑时会把批注**抠出来、重新渲染后再按页号塞回去**，一个字都不会动。
遇到没有生成块标记的文件，程序**拒绝改写**（那是你的文件）。

## 与 ppt-deepreader 的关系

两个**独立程序**，各自保留主流程与出口：

- `ppt-deepreader` = 讲解器（逐页 8 模块精读、四层质量防线、PPTX/DOCX/OCR）
- `course-pipeline` = 学习系统（账本、状态、卡片、复习）

共享的是**引擎**（从真源 import，不复制代码）：LLM 客户端（截断/重试/剥思考链）、
内容质量校验、多格式提取。**依赖真源 `../ppt-deepreader`，不依赖任何交付副本。**

## 环境事实（省得再摸一遍）

- 无 bash、无 npm、无 DSH_CHECKOUT；Python 用系统解释器 + `vendor/`。
- 控制台是 GBK：**不要直接 print 中文/特殊符号**，一律写 UTF-8 文件再看。
- `github.com` 时通时断（走本地代理 127.0.0.1:7897）；HuggingFace 不通，模型源走 ModelScope。
