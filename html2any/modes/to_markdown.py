# -*- coding: utf-8 -*-
"""方式一：html -> markdown，页面图片下载到 imgs/ 并改写相对路径。"""
from __future__ import annotations

import re
from pathlib import Path

from bs4 import BeautifulSoup
from markdownify import MarkdownConverter

from html2any.extract import DROP_TAGS_TEXT, localize_images, pick_main


class KeepAllImagesConverter(MarkdownConverter):
    """修复 markdownify 默认行为导致的丢图。

    markdownify 原生 convert_img 有这一段：
        if ('_inline' in parent_tags
                and el.parent.name not in self.options['keep_inline_images_in']):
            return alt          # 只留 alt 文本，图片本身被丢掉
    也就是说图片只要出现在表格单元格、链接、<span> 等 inline 上下文里，
    且直接父节点不在 keep_inline_images_in（默认只有 th/td）里，就会被降级成纯文本。
    实测 Wikipedia 正文里的 4 张图全部落在 <td><a>/<span> 里，因此全部丢失。

    这里的图片已经被下载到本地，目标就是"一定要出现在 markdown 里"，
    所以直接覆盖成任何上下文都输出 ![alt](src)。
    """

    def convert_img(self, el, text, parent_tags):
        alt = (el.attrs.get("alt") or "").strip()
        src = el.attrs.get("src") or ""
        title = (el.attrs.get("title") or "").strip()
        title_part = f' "{title}"' if title else ""
        return f"![{alt}]({src}{title_part})"


def convert(url: str, html: str, final_url: str, mode_dir: Path,
            with_images: bool = True, main_selector: str | None = None,
            timeout: int = 30) -> dict:
    """把已抓取的 html 转成 markdown，产物写进 mode_dir，返回该方式的元信息。"""
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(DROP_TAGS_TEXT):
        tag.decompose()

    title = soup.title.get_text(strip=True) if soup.title else ""

    scope, used_selector = pick_main(soup, main_selector)
    # 重新解析一次，把正文节点从整页 DOM 里摘出来，避免带上兄弟节点
    scope = BeautifulSoup(str(scope), "html.parser")

    img_count = localize_images(scope, final_url, mode_dir / "imgs",
                                enabled=with_images, timeout=timeout)

    # 注意：不要用 strip=["span"]，markdownify 的 strip 会连同内容一起删掉，
    # 而大量站点的正文文字都包在 <span> 里，删了会丢正文。
    # 也不能直接用 markdownify()，表格里的图片会被降级成 alt 文本（见 KeepAllImagesConverter）。
    body = KeepAllImagesConverter(heading_style="ATX", bullets="-").convert(str(scope))
    body = re.sub(r"\n{3,}", "\n\n", body).strip()

    if title and not body.lstrip().startswith("# "):
        body = f"# {title}\n\n{body}"
    body += "\n"

    mode_dir.mkdir(parents=True, exist_ok=True)
    md_path = mode_dir / "article.md"
    md_path.write_text(body, encoding="utf-8")

    return {
        "main_selector": used_selector,
        "markdown_file": md_path.name,
        "markdown_chars": len(body),
        "images_downloaded": img_count,
        "images_localized": with_images,
    }
