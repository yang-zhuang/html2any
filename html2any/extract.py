# -*- coding: utf-8 -*-
"""正文抽取与图片本地化（三种方式共用）。

两个 DROP 标签清单（差异是有意的）：
  - DROP_TAGS_TEXT  markdown / structure 用：canvas/svg 一并丢掉（对文本产物无意义）；
  - DROP_TAGS_RENDER images 用：只丢 script/style 等，**保留 canvas**——canvas 上
    画的图表是要截进图里的，丢了会缺内容。
"""
from __future__ import annotations

from pathlib import Path
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup

from html2any.fetch import UA

# 文本类产物（markdown / structure）要丢的标签
# （link / meta / base 是 head 标签，但 MediaWiki 会把模板样式表塞进正文里）
DROP_TAGS_TEXT = ("script", "style", "noscript", "iframe", "template", "canvas", "svg",
                  "link", "meta", "base")

# 渲染类产物（images）要丢的标签：保留 canvas（图表要截进图里）
DROP_TAGS_RENDER = ("script", "style", "noscript", "iframe", "template", "svg")

# 从页面里挑正文的候选选择器（按顺序尝试，命中且文本量够多即用）
MAIN_SELECTORS = (
    "main", "article", "[role=main]", "#content", "#main",
    ".content", ".main", ".post", ".article", ".markdown-body",
)

IMG_EXTS = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".svg"}

# 懒加载属性，命中优先级高于 src
LAZY_ATTRS = ("data-src", "data-original", "data-lazy-src", "data-actualsrc", "data-echo")


def pick_main(soup: BeautifulSoup, main_selector: str | None = None,
              min_text: int = 200):
    """挑正文节点，返回 (node, 实际用的选择器)。

    优先用户指定的选择器（没匹配到直接报错），其次常见正文容器
    （文本量 >= min_text 才算数），最后退回 body。
    """
    if main_selector:
        node = soup.select_one(main_selector)
        if node is None:
            raise ValueError(f"--main 选择器没匹配到任何节点: {main_selector}")
        return node, main_selector

    for sel in MAIN_SELECTORS:
        node = soup.select_one(sel)
        if node and len(node.get_text(strip=True)) >= min_text:
            return node, sel
    return (soup.body or soup), "body"


def best_image_url(img) -> str | None:
    """取图片真实地址：懒加载属性优先，其次 src，最后 srcset 第一项。"""
    for attr in LAZY_ATTRS:
        val = (img.get(attr) or "").strip()
        if val and not val.startswith("data:"):
            return val

    src = (img.get("src") or "").strip()
    if src and not src.startswith("data:"):
        return src

    srcset = (img.get("srcset") or "").strip()
    if srcset:
        cand = srcset.split(",")[0].strip().split(" ")[0]
        if cand and not cand.startswith("data:"):
            return cand
    return None


def localize_images(scope, base_url: str, img_dir: Path, enabled: bool, timeout: int) -> int:
    """遍历 <img>：下载到 img_dir，并把 src 改成相对路径 imgs/00N.ext。

    enabled=False 时不下载，只把 src 补成绝对地址。
    返回成功保存的图片数量。同一张图（绝对 url 相同）只下一次。
    """
    if enabled:
        img_dir.mkdir(parents=True, exist_ok=True)

    saved: dict[str, str] = {}   # 绝对 url -> 相对路径
    count = 0

    with requests.Session() as session:
        for img in scope.find_all("img"):
            remote = best_image_url(img)
            if not remote:
                img.decompose()
                continue

            abs_url = urljoin(base_url, remote)

            if not enabled:
                img["src"] = abs_url
                continue

            if abs_url in saved:
                img["src"] = saved[abs_url]
                continue

            count += 1
            ext = Path(urlparse(abs_url).path).suffix.lower()
            if ext not in IMG_EXTS:
                ext = ".png"
            name = f"{count:03d}{ext}"

            try:
                resp = session.get(abs_url, headers={"User-Agent": UA}, timeout=timeout)
                resp.raise_for_status()
                (img_dir / name).write_bytes(resp.content)
            except Exception as exc:                      # noqa: BLE001
                count -= 1
                print(f"  [warn] 图片下载失败，已跳过: {abs_url} ({exc})")
                img.decompose()
                continue

            rel = f"imgs/{name}"
            saved[abs_url] = rel
            img["src"] = rel
            img["alt"] = (img.get("alt") or "").strip()

    return count
