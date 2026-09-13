# -*- coding: utf-8 -*-
"""run_id：把 url 归一化后取哈希，生成短目录名（代替又长又没意义的 URL 目录名）。

断点续传不依赖目录名：meta.json 里存了完整 url 和各方式完成状态，
重跑时按 run_id 找到目录、核对 url_hash、跳过已完成的方式即可。
"""
from __future__ import annotations

import hashlib
import time
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

# 这些查询参数纯属跟踪，剔除后同一页面的不同分享链接会命中同一个 run
TRACKING_PARAMS = ("utm_source", "utm_medium", "utm_campaign", "utm_term",
                   "utm_content", "spm", "from")


def normalize_url(url: str) -> str:
    """归一化：去 fragment、去跟踪参数、去路径尾部斜杠。"""
    p = urlparse(url.strip())
    query = [(k, v) for k, v in parse_qsl(p.query, keep_blank_values=True)
             if k.lower() not in TRACKING_PARAMS]
    path = p.path or "/"
    if len(path) > 1:
        path = path.rstrip("/")
    return urlunparse((p.scheme, p.netloc, path, p.params, urlencode(query), ""))


def url_hash(url: str) -> str:
    """归一化 url 的 sha1 前 8 位。"""
    return hashlib.sha1(normalize_url(url).encode("utf-8")).hexdigest()[:8]


def make_run_id(url: str) -> str:
    """目录名：日期前缀 + 8 位哈希，如 20260913_a1b2c3d4。同一页面重跑命中同一目录。"""
    return time.strftime("%Y%m%d") + "_" + url_hash(url)
