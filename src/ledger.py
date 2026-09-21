# -*- coding: utf-8 -*-
"""账本读写 —— 本项目的唯一真相源。

设计要点（对应需求 R6 / 铁律 1、3）：

1. **稳定序列化**：所有 JSON 写入都走 `dumps()`，固定 sort_keys + 固定缩进 +
   ensure_ascii=False。这样「同样的输入跑两遍」得到的是字节完全相同的文件，
   幂等测试才有意义。
2. **原子写**：先写 `.tmp` 再 `os.replace`，避免中途失败留下半个文件。
3. **内容哈希**：`content_hash()` 用于缓存失效判断 —— 只有输入真的变了才重算。
"""
from __future__ import annotations

import hashlib
import json
import os
from typing import Any


def dumps(obj: Any) -> str:
    """稳定序列化：保证同样内容永远产出同样字节。"""
    return json.dumps(obj, ensure_ascii=False, indent=2, sort_keys=True) + "\n"


def atomic_write_text(path: str, text: str) -> None:
    """原子写文本。统一用 \\n 换行，避免 Windows/Linux 产出差异。"""
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)
    os.replace(tmp, path)


def atomic_write_json(path: str, obj: Any) -> None:
    atomic_write_text(path, dumps(obj))


def load_json(path: str, default: Any = None) -> Any:
    if not os.path.exists(path):
        return default
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: str, chunk: int = 1 << 20) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while True:
            b = f.read(chunk)
            if not b:
                break
            h.update(b)
    return h.hexdigest()


def content_hash(obj: Any) -> str:
    """对任意可序列化对象算指纹，用于缓存失效。"""
    return sha256_bytes(dumps(obj).encode("utf-8"))[:16]


class Ledger:
    """一个课程的账本目录。"""

    def __init__(self, root: str):
        self.root = os.path.abspath(root)
        os.makedirs(self.root, exist_ok=True)

    def path(self, *parts: str) -> str:
        return os.path.join(self.root, *parts)

    # ---- source.json：源清单 ----
    def load_sources(self) -> dict:
        d = load_json(self.path("source.json"), None)
        if d is None:
            return {"version": 1, "sources": {}}
        return d

    def save_sources(self, d: dict) -> None:
        d["version"] = 1
        atomic_write_json(self.path("source.json"), d)

    # ---- pages/<lecture_id>.json：逐页内容 ----
    def load_pages(self, lecture_id: str) -> dict | None:
        return load_json(self.path("pages", f"{lecture_id}.json"), None)

    def save_pages(self, lecture_id: str, d: dict) -> None:
        d["version"] = 1
        atomic_write_json(self.path("pages", f"{lecture_id}.json"), d)
