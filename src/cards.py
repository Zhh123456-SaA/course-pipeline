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


def render_front(transcript: str, question: str) -> str:
    """正面：原文（给足语境）+ 当时的问题。"""
    out: list[str] = []
    tr = (transcript or "").strip()
    if tr:
        out.append(f'<div class="ctx">{_esc(tr).replace(chr(10), "<br>")}</div>')
        out.append("<br>")
    q = (question or "").strip()
    out.append(f"<b>❓ {_esc(q) if q else '这一块讲的是什么？'}</b>")
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


def build_cards(course: str, lecture_id: str, source_file: str,
                annotations: list[dict], slug: str,
                crop_dir: str | None = None) -> list[dict]:
    """一组批注 → 卡片列表（不落盘、不联网）。"""
    out: list[dict] = []
    deck = f"课程::{course}"
    for ann in annotations:
        page = int(ann.get("page", 0))
        no = int(ann.get("no", 0))
        bbox = ann.get("bbox") or []
        bbox_s = "[" + ", ".join(f"{float(x):.3f}" for x in bbox) + "]" if bbox else ""
        tr = ann.get("transcript", "")
        expl = ann.get("explanation", "")
        source = {"lecture": lecture_id, "file": source_file, "page": page, "bbox": bbox_s}
        crop = os.path.join(crop_dir, f"p{page:03d}.jpg") if crop_dir else None

        out.append({
            "id": card_id(ann.get("_sha1", ""), page, no),
            "deck": deck,
            "fields": {
                "Front": render_front(tr, ann.get("question", "")),
                "Back": render_back(expl, ann.get("latex", ""), source),
            },
            "tags": ["course-pipeline", course, lecture_id, f"p{page}"],
            "source": source,
            "crop": crop if (crop and os.path.exists(crop)) else None,
        })

        # 追问也各成一张卡（用上一轮解答当语境，保证自足）
        for i, t in enumerate(ann.get("thread") or [], start=1):
            if not isinstance(t, dict):
                continue
            out.append({
                "id": card_id(ann.get("_sha1", ""), page, no, i),
                "deck": deck,
                "fields": {
                    "Front": render_front(tr, t.get("q", "")),
                    "Back": render_back(t.get("a", ""), "", source),
                },
                "tags": ["course-pipeline", course, lecture_id, f"p{page}", "追问"],
                "source": source,
                "crop": None,
            })
    return out


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
    """把新卡并入账本；**保留已有的 anki_note_id**（否则重跑会重复制卡）。"""
    data = load_cards(library_root)
    by_id = data.setdefault("by_id", {})
    report: list[str] = []
    added = updated = 0
    for c in new_cards:
        old = by_id.get(c["id"])
        if old is None:
            by_id[c["id"]] = c
            added += 1
        else:
            note_id = old.get("anki_note_id")
            if old.get("fields") != c["fields"] or old.get("deck") != c["deck"]:
                by_id[c["id"]] = c
                updated += 1
            else:
                c = old
            if note_id:
                by_id[c["id"]]["anki_note_id"] = note_id
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
