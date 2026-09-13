# -*- coding: utf-8 -*-
"""抓取模块：requests 拉取页面 + 显式重试 + 编码修正（三种方式共用）。"""
from __future__ import annotations

import time

import requests

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)


def fetch_html(url: str, timeout: int = 30, retries: int = 3):
    """抓取页面，返回 (html 文本, 最终 url)。最终 url 会跟随重定向。

    实测中遇到过瞬时的 SSLEOFError，这里做显式重试，避免网络抖一下整次任务就失败。
    """
    last_exc = None
    for attempt in range(1, retries + 1):
        try:
            resp = requests.get(url, headers={"User-Agent": UA}, timeout=timeout)
            resp.raise_for_status()
            if not resp.encoding or resp.encoding.lower() == "iso-8859-1":
                resp.encoding = resp.apparent_encoding or "utf-8"
            return resp.text, resp.url
        except requests.RequestException as exc:
            last_exc = exc
            if attempt < retries:
                print(f"  [warn] 第 {attempt} 次抓取失败，{0.8 * attempt:.1f}s 后重试: {exc}")
                time.sleep(0.8 * attempt)
    raise RuntimeError(f"抓取失败（已重试 {retries} 次）: {url} -> {last_exc}")
