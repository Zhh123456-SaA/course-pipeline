# -*- coding: utf-8 -*-
"""把归档进来的「框选追问」变成 Anki 卡片。

为什么从追问做卡（而不是让 AI 凭空出题）
--------------------------------------
追问是用户**自己真正问过的问题**，附着他当时看不懂的原文与 AI 的解答。
这是质量最高的卡片来源：有真实困惑、有确定答案、有精确出处。

卡片身份（幂等的前提）
--------------------
`<源文件sha1前12>:p<页号>:n<序号>[:t<追问序号>]` —— 全部来自源数据，不依赖题干文字。
所以重跑不会重复制卡、也不怕你改题面。

出处
----
每张卡背面都带出处：《讲次》第 N 页 + 框选区域坐标。以后可以一键跳回原讲义。
"""
from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from typing import Any

from ledger import atomic_write_text, load_json

ANKI_URL = "http://127.0.0.1:8765"
ANKI_VERSION = 6


# ---------------------------------------------------------------- 卡片身份与内容

def card_id(sha1: str, page: int, no: int, thread_idx: int | None = None) -> str:
    """稳定卡片身份：只由源数据决定，与题干文字无关。"""
    base = f"{sha1[:12]}:p{page:03d}:n{no}"
    return base if thread_idx is None else f"{base}:t{thread_idx}"


def _esc(s: str) -> str:
    return (str(s or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))


def render_front(transcript: str, question: str, latex: str = "") -> str:
    """正面：语境（原文，或退而用公式）+ 当时的问题。

    **绝不再伪造通用问题**。第一版在 question 为空时塞了一句
    「❓ 这一块讲的是什么？」，配上模型对模糊图片的描述 →
    做出无法作答的垃圾卡（用户实测发现）。现在 question 为空直接报错，
    由 build_cards 的闸门提前挡掉。
    """
    q = (question or "").strip()
    if not q:
        raise ValueError(
            "render_front 收到空问题：没有题目的卡片没有训练意义，"
            "应当在 build_cards 里就被 has_question() 挡掉。"
        )
    out: list[str] = []
    ctx = (transcript or "").strip()
    if ctx:
        out.append(f'<div class="ctx">{_esc(ctx).replace(chr(10), "<br>")}</div>')
        out.append("<br>")
    elif (latex or "").strip():
        # 转录不可用（是画面描述）时的退路：直接给出公式当语境
        out.append(f'<div class="ctx">\\[{_esc(latex)}\\]</div>')
        out.append("<br>")
    out.append(f"<b>❓ {_esc(q)}</b>")
    return "\n".join(out)


def render_back(explanation: str, latex: str, source: dict, thread_qa: dict | None = None) -> str:
    """背面：解答（+ 公式 + 出处）。追问卡把上一轮的解答也带上，保持自足。"""
    out: list[str] = []
    if thread_qa and thread_qa.get("prefix_answer"):
        out.append(f'<div class="ctx">{_esc(thread_qa["prefix_answer"])}</div><br>')
    out.append(_esc(explanation).replace("\n", "<br>"))
    if latex:
        out.append("<br><br>")
        out.append(f"\\[{_esc(latex)}\\]")
    out.append("<br><br><hr>")
    out.append(f'<small>出处：《{_esc(source.get("lecture", ""))}》'
               f'第 {source.get("page", "?")} 页'
               + (f' · 框选 {_esc(source.get("bbox", ""))}' if source.get("bbox") else "")
               + f'</small>')
    return "\n".join(out)


#: 「这段 transcript 其实是模型在描述画面、而不是在转写原文」的信号词。
#: 实测样本：第 34 页的 transcript 是「画面上部是前序内容的截断局部…无法辨认完整公式…」——
#: 模型自己都说读不出来，把它当语境放进正面毫无意义。
_DESC_MARKERS = (
    "无法辨认", "无法完整辨认", "无法看清", "残段", "截断局部",
    "画面最右", "画面左上", "可见文字为", "露出",
)


def is_descriptive(transcript: str) -> bool:
    """判断转录是不是「对画面的描述」而非原文。"""
    t = transcript or ""
    return any(m in t for m in _DESC_MARKERS)


def has_question(ann: dict) -> bool:
    """这条批注有没有**真问题**。

    ppt-deepreader 允许留空提问（＝"解释这一块"）。那种批注没有题目，
    硬做成卡就会出现「❓ 这一块讲的是什么？」配一段模糊图片描述 —— 无法作答。
    实测踩过：9 张卡里有 2 张是这种垃圾卡（question 均为空）。
    """
    return bool((ann.get("question") or "").strip())


def build_cards(course: str, lecture_id: str, source_file: str,
                annotations: list[dict], slug: str,
                crop_dir: str | None = None,
                question_provider=None) -> tuple[list[dict], list[dict]]:
    """一组批注 → (卡片列表, 被跳过的批注列表)。**本函数不联网**。

    两类批注两种处理：

      1. **有真问题**（用户自己问的）→ 直接用他的问题，语境优先用讲义原文。
      2. **没有真问题**（「留空＝解释这块」）→ 若给了 `question_provider`，
         就**用 AI 从解答反推一道题**；反推失败（或没给 provider）就跳过。

    `question_provider(ann) -> dict | None`，应返回 `{"question": str, ...}`。
    把它做成注入的回调而不是在这里直接调 API，是为了让 build_cards 保持纯函数、
    测试可以离线跑。
    """
    out: list[dict] = []
    skipped: list[dict] = []
    deck = f"课程::{course}"

    for ann in annotations:
        page = int(ann.get("page", 0))
        no = int(ann.get("no", 0))
        bbox = ann.get("bbox") or []
        bbox_s = "[" + ", ".join(f"{float(x):.3f}" for x in bbox) + "]" if bbox else ""
        tr = ann.get("transcript", "")
        expl = ann.get("explanation", "")
        latex = ann.get("latex", "")
        source = {"lecture": lecture_id, "file": source_file, "page": page, "bbox": bbox_s}
        crop = os.path.join(crop_dir, f"p{page:03d}.jpg") if crop_dir else None

        # 转录是「画面描述」时不当语境（否则会把模型对模糊图的描述塞进题面）
        context = "" if is_descriptive(tr) else tr

        if has_question(ann):
            # ---- 用户自己问的 ----
            out.append({
                "id": card_id(ann.get("_sha1", ""), page, no),
                "deck": deck,
                "fields": {
                    "Front": render_front(context, ann.get("question", "")),
                    "Back": render_back(expl, latex, source),
                },
                "tags": ["course-pipeline", course, lecture_id, f"p{page}"],
                "source": {**source, "kind": "user"},
                "crop": crop if (crop and os.path.exists(crop)) else None,
            })
        elif question_provider is not None:
            # ---- 没提问 → 用 AI 从解答反推 ----
            gen = question_provider(ann) or {}
            q = (gen.get("question") or "").strip()
            if not q:
                skipped.append({"page": page, "no": no, "lecture": lecture_id,
                                "reason": "AI 反推题目失败：" + (gen.get("reason") or "未知")})
                continue
            out.append({
                "id": card_id(ann.get("_sha1", ""), page, no),
                "deck": deck,
                "fields": {
                    "Front": render_front("", q),        # 出题卡不给语境，避免泄题
                    "Back": render_back(expl, latex, source),
                },
                "tags": ["course-pipeline", course, lecture_id, f"p{page}", "AI出题"],
                "source": {**source, "kind": "ai",
                           "model": gen.get("model", ""), "tokens": gen.get("tokens", 0)},
                "crop": None,
            })
        else:
            skipped.append({"page": page, "no": no, "lecture": lecture_id,
                            "reason": "无提问（留空＝解释这块）且未启用 AI 出题"})
            continue

        # 追问也各成一张卡（有真问题的才做）
        for i, t in enumerate(ann.get("thread") or [], start=1):
            if not isinstance(t, dict):
                continue
            if not (t.get("q") or "").strip():
                skipped.append({"page": page, "no": no, "lecture": lecture_id,
                                "reason": f"追问 #{i} 无提问"})
                continue
            out.append({
                "id": card_id(ann.get("_sha1", ""), page, no, i),
                "deck": deck,
                "fields": {
                    "Front": render_front(context, t.get("q", "")),
                    "Back": render_back(t.get("a", ""), "", source),
                },
                "tags": ["course-pipeline", course, lecture_id, f"p{page}", "追问"],
                "source": {**source, "kind": "user"},
                "crop": None,
            })
    return out, skipped


# ---------------------------------------------------------------- 账本

def cards_ledger_path(library_root: str) -> str:
    return os.path.join(library_root, ".ledger", "cards.json")


def load_cards(library_root: str) -> dict:
    d = load_json(cards_ledger_path(library_root), None)
    return d if d else {"version": 1, "by_id": {}}


def save_cards(library_root: str, data: dict) -> None:
    data["version"] = 1
    from ledger import atomic_write_json
    atomic_write_json(cards_ledger_path(library_root), data)


def merge_cards(library_root: str, new_cards: list[dict]) -> tuple[dict, list[str]]:
    """把新卡并入账本。

    两条铁律：
      - **保留已有的 `anki_note_id`**（否则重跑会重复制卡）；
      - **其余字段一律以新构建的为准** —— 早先版本在"字段没变"时保留旧条目，
        结果新加的元数据（如 source.kind）永远进不去，账本和代码脱节（踩过）。
    """
    data = load_cards(library_root)
    by_id = data.setdefault("by_id", {})
    report: list[str] = []
    added = updated = 0
    for c in new_cards:
        old = by_id.get(c["id"])
        note_id = (old or {}).get("anki_note_id")
        merged = dict(c)
        if note_id:
            merged["anki_note_id"] = note_id
        if old is None:
            added += 1
        elif old != merged:
            updated += 1
        else:
            continue
        by_id[c["id"]] = merged
    report.append(f"[cards] 新增 {added} 张，更新 {updated} 张，账本共 {len(by_id)} 张")
    return data, report


def export_tsv(cards: list[dict], path: str, notetype: str = "Basic",
               deck: str = "课程") -> None:
    """导出成 Anki 可直接导入的文本（制表符分隔）。

    手工导入是 AnkiConnect 不可用时的退路 —— 不联网、不开 Anki 也能用。

    `notetype` 必须与目标 Anki 里真实存在的笔记类型一致：中文版 Anki 没有
    `Basic` 这个名字（叫「问答题」），表头写错导入会失败或建出错误的类型。
    调用方应先用 AnkiConnect 探测；探测不到才退回 `Basic`。
    """
    lines = ["#separator:tab", "#html:true",
             f"#notetype:{notetype}",
             f"#deck:{deck}",
             "#tags column:4"]
    for c in cards:
        front = c["fields"]["Front"].replace("\t", " ").replace("\n", " ")
        back = c["fields"]["Back"].replace("\t", " ").replace("\n", " ")
        tags = " ".join(c["tags"]).replace("\t", " ")
        lines.append(f"{front}\t{back}\t{c['deck']}\t{tags}")
    atomic_write_text(path, "\n".join(lines) + "\n")


def prune_cards(library_root: str, valid_ids: set[str]) -> list[dict]:
    """把「按当前规则不该存在」的卡从账本里删掉，返回被删的条目。

    为什么需要：规则会演进（例：实测发现「没有提问的批注不该制卡」，
    于是 2 张垃圾卡必须退场）。不清理的话账本和 Anki 会永远留着它们。
    """
    data = load_cards(library_root)
    by_id = data.get("by_id", {})
    removed = [by_id[k] for k in list(by_id) if k not in valid_ids]
    for k in [k for k in by_id if k not in valid_ids]:
        del by_id[k]
    if removed:
        save_cards(library_root, data)
    return removed


def reset_deck(library_root: str, deck: str) -> int:
    """清空牌组并重置账本里的 note id，返回删除的卡片数。

    用途：制卡规则变更后做**彻底重建**。因为 Anki 的 note id 只在推送成功那一刻
    才写进账本 —— 如果账本里的 id 中途丢了（实测被一个写坏的测试毁掉过一条），
    就没法按 id 精确删除，只能整组重建。
    """
    ac = AnkiConnect()
    if not ac.available():
        raise AnkiError("AnkiConnect 不可用，无法重建牌组")
    ids = ac.invoke("findNotes", query=f'"deck:{deck}"') or []
    ac.delete_notes(ids)
    data = load_cards(library_root)
    for v in data.get("by_id", {}).values():
        v.pop("anki_note_id", None)
    save_cards(library_root, data)
    return len(ids)


# ---------------------------------------------------------------- AnkiConnect

class AnkiError(RuntimeError):
    pass


class AnkiConnect:
    """AnkiConnect 的极简客户端（标准库，无依赖）。

    只在 Anki 运行时可用 —— 它监听 127.0.0.1:8765。
    """

    def __init__(self, url: str = ANKI_URL, timeout: float = 15.0):
        self.url = url
        self.timeout = timeout

    def invoke(self, action: str, **params: Any) -> Any:
        payload = json.dumps({"action": action, "version": ANKI_VERSION,
                              "params": params}).encode("utf-8")
        req = urllib.request.Request(self.url, data=payload,
                                     headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as r:
                res = json.loads(r.read().decode("utf-8"))
        except (urllib.error.URLError, OSError) as e:
            raise AnkiError(f"连不上 AnkiConnect（{self.url}）：{e}。Anki 开了吗？") from e
        if res.get("error"):
            raise AnkiError(f"AnkiConnect 报错：{res['error']}")
        return res.get("result")

    def available(self) -> bool:
        try:
            self.invoke("version")
            return True
        except AnkiError:
            return False

    def model_names(self) -> list[str]:
        return self.invoke("modelNames") or []

    def model_field_names(self, model: str) -> list[str]:
        return self.invoke("modelFieldNames", modelName=model) or []

    def pick_basic_model(self, preferred: str | None = None) -> tuple[str | None, dict[str, str]]:
        """挑一个「正/反两面」的笔记类型，并返回**字段名映射**。

        两个实测踩到的坑（中文版 Anki）：
          1. **类型名不是 Basic** —— 叫「问答题」，还有「填空题」「图片遮盖」等；
          2. **字段名也不是 Front/Back** —— 是「正面/背面」。

        所以：按**字段**判断（中英都认），并把我们的 Front/Back 映射到实际字段名。
        返回 (类型名, {"Front": 实际字段, "Back": 实际字段})；找不到返回 (None, {})。
        """
        #: 逻辑字段 → 各语言下的实际字段名（按优先级）
        aliases = {
            "Front": ("Front", "正面"),
            "Back": ("Back", "背面"),
        }

        names = self.model_names()
        if not names:
            return None, {}

        order = ([preferred] if preferred and preferred in names else []) + names
        for cand in order:
            try:
                fns = set(self.model_field_names(cand))
            except AnkiError:
                continue
            mapping: dict[str, str] = {}
            for logical, alts in aliases.items():
                for a in alts:
                    if a in fns:
                        mapping[logical] = a
                        break
            if len(mapping) == 2:
                return cand, mapping
        return None, {}

    def ensure_deck(self, name: str) -> None:
        self.invoke("createDeck", deck=name)

    def store_media(self, filename: str, path: str) -> bool:
        try:
            self.invoke("storeMediaFile", filename=filename, path=path)
            return True
        except AnkiError:
            return False

    def add_notes(self, notes: list[dict]) -> list:
        return self.invoke("addNotes", notes=notes) or []

    def delete_notes(self, note_ids: list[int]) -> None:
        if note_ids:
            self.invoke("deleteNotes", notes=list(note_ids))


def to_anki_note(card: dict, model: str, field_map: dict[str, str],
                 media_name: str | None = None) -> dict:
    """卡片 → AnkiConnect 的 note。字段名按 field_map 映射（中文版是 正面/背面）。"""
    front = card["fields"]["Front"]
    back = card["fields"]["Back"]
    if media_name:
        back = back + f'<br><img src="{media_name}">'
    fields = {
        field_map["Front"]: front,
        field_map["Back"]: back,
    }
    return {
        "deckName": card["deck"],
        "modelName": model,
        "fields": fields,
        "tags": card["tags"],
        "options": {"allowDuplicate": False, "duplicateScope": "deck"},
    }


def sync_to_anki(library_root: str, cards: list[dict], model: str | None = None,
                 with_images: bool = True) -> list[str]:
    """把还没推过的卡送进 Anki。返回报告行。"""
    report: list[str] = []
    ac = AnkiConnect()
    if not ac.available():
        return ["[warn] AnkiConnect 不可用 —— 请先打开 Anki（它监听 127.0.0.1:8765）"]

    models = ac.model_names()
    use_model, field_map = ac.pick_basic_model(model)
    if not use_model:
        return ["[error] Anki 里找不到「有正反两面字段」的笔记类型"
                f"（现有：{', '.join(models) or '无'}）—— 先在 Anki 里建一个"]
    report.append(f"[anki ] 笔记类型：**{use_model}**"
                  f"（字段映射 {'/'.join(field_map.values())}）"
                  f"  ← 按字段自动挑，不靠名字猜（中文版没有 Basic 这个名）")

    data = load_cards(library_root)
    by_id = data["by_id"]

    pending = [c for c in cards if not by_id.get(c["id"], {}).get("anki_note_id")]
    if not pending:
        report.append("[anki ] 没有新卡要推送（都已同步过）")
        return report

    for deck in sorted({c["deck"] for c in pending}):
        ac.ensure_deck(deck)

    notes = []
    for c in pending:
        media = None
        if with_images and c.get("crop"):
            fname = f"cp_{c['id'].replace(':', '_')}.jpg"
            if ac.store_media(fname, c["crop"]):
                media = fname
        notes.append(to_anki_note(c, use_model, field_map, media))

    try:
        ids = ac.add_notes(notes)
    except AnkiError as e:
        return report + [f"[error] 推送失败：{e}"]

    ok = fail = 0
    for c, nid in zip(pending, ids):
        if nid:
            by_id[c["id"]]["anki_note_id"] = nid
            ok += 1
        else:
            fail += 1
    save_cards(library_root, data)
    report.append(f"[anki ] 成功推送 {ok} 张" + (f"，失败 {fail} 张" if fail else ""))
    return report
