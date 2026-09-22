# -*- coding: utf-8 -*-
"""建项目自带的依赖库：从镜像取 wheel 直接解包到 vendor/。

为什么不用 pip / venv：
  本机沙箱下 `pip install --target` 的临时目录清理会失败、`python -m venv`
  的 ensurepip 也会失败。取 wheel 手动解包是实测唯一稳定的路子。

用法（在 course-pipeline 目录下）：
    python scripts/setup_vendor.py

依赖：只需能访问 Python 镜像（默认清华源），不需要 pip / npm / bash。
"""
from __future__ import annotations

import io
import os
import re
import ssl
import sys
import socket
import urllib.parse
import urllib.request
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
VENDOR = os.path.abspath(os.path.join(HERE, "..", "vendor"))
MIRROR = os.environ.get("PIP_MIRROR", "https://pypi.tuna.tsinghua.edu.cn/simple/")

socket.setdefaulttimeout(180)
CTX = ssl.create_default_context()

#: 包名 → 按优先级尝试的平台标签
WANT = [
    ("pypdfium2", ["py3-none-win_amd64", "py3-none-manylinux", "py3-none-any"]),
    ("pillow", [
        f"cp{sys.version_info.major}{sys.version_info.minor}-cp{sys.version_info.major}{sys.version_info.minor}-win_amd64",
        f"cp{sys.version_info.major}{sys.version_info.minor}-cp{sys.version_info.major}{sys.version_info.minor}-manylinux",
        "py3-none-any",
    ]),
]


def pick(pkg: str, tags: list[str]) -> str | None:
    """在镜像的 simple 索引里挑一个匹配当前平台的 wheel。"""
    url = MIRROR + pkg + "/"
    page = urllib.request.urlopen(url, context=CTX).read().decode("utf-8", "ignore")
    hrefs = re.findall(r'href="([^"]+\.whl[^"]*)"', page)
    urls = [urllib.parse.urljoin(url, h.split("#")[0]) for h in hrefs]
    stable = [u for u in urls if "dev" not in os.path.basename(u).lower()]
    pool = stable or urls
    for tag in tags:
        hit = [u for u in pool if tag in os.path.basename(u)]
        if hit:
            return hit[-1]
    return None


def main() -> int:
    os.makedirs(VENDOR, exist_ok=True)
    ok = True
    for pkg, tags in WANT:
        try:
            u = pick(pkg, tags)
        except Exception as e:
            print(f"[FAIL] {pkg}: 取索引失败 {type(e).__name__}: {e}")
            ok = False
            continue
        if not u:
            print(f"[FAIL] {pkg}: 没找到匹配 {tags} 的 wheel")
            ok = False
            continue
        name = os.path.basename(u)
        print(f"[get ] {pkg} <- {name}")
        try:
            data = urllib.request.urlopen(u, context=CTX).read()
            with zipfile.ZipFile(io.BytesIO(data)) as z:
                z.extractall(VENDOR)
            print(f"[ ok ] {len(data)/1048576:.1f} MB")
        except Exception as e:
            print(f"[FAIL] {pkg}: 下载/解包失败 {type(e).__name__}: {e}")
            ok = False

    sys.path.insert(0, VENDOR)
    print("\n=== 验证 ===")
    for m in ("pypdfium2", "PIL"):
        try:
            mod = __import__(m)
            print(f"  {m:12s} OK  {getattr(mod, '__version__', '?')}")
        except Exception as e:
            print(f"  {m:12s} FAIL {type(e).__name__}: {e}")
            ok = False
    print("\nvendor 目录：" + VENDOR)
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
