# -*- coding: utf-8 -*-
"""方式二：html -> 多张图片。

auto 模式（默认，照搬原始实现）：外壳样式 -> 整页截图 -> 四向裁白 -> 空白行智能分页。
page 模式（原版没有，额外提供）：Chrome --print-to-pdf + PyMuPDF 逐页栅格化，
页边界由排版引擎决定，同样不会切断内容，且每张图尺寸严格固定。

移植自 examples/html_to_images/html_to_images.py（原始文件头部的对齐说明
保存在 docs/alignment-notes.md）。随包内统一的两处变化：
  - 正文抽取改用共享 pick_main（阈值 80 -> 200 字符，候选选择器为并集）；
  - 显式 --main 未命中时与另两个方式一致：直接报错（原实现静默退回整页 body）。
"""
from __future__ import annotations

import shutil
import tempfile
import time
from pathlib import Path

from bs4 import BeautifulSoup
from PIL import Image

from html2any.chrome import (DEFAULT_FONT_FAMILY, MEASURE_JS_HEIGHT, build_shell_css,
                             find_chrome, measure_page_height, print_to_pdf,
                             take_screenshot, wrap_shell)
from html2any.extract import DROP_TAGS_RENDER, pick_main
from html2any.paginate import (BLANK_ROW_RATIO, MAX_PAGE_HEIGHT, PADDING_BOTTOM,
                               PADDING_LEFT, PADDING_RIGHT, PADDING_TOP,
                               SEARCH_EXPAND, SEARCH_MARGIN, WHITE_THRESHOLD,
                               find_best_cut, plan_pages, trim_border)


def build_print_css(page_width: int, page_height: int, margin: int) -> str:
    """page 模式：@page 尺寸 + break-inside:avoid，让排版引擎在块间隙断页。"""
    return (
        "@media print {\n"
        "html{-webkit-print-color-adjust:exact !important;print-color-adjust:exact !important;}\n"
        "img,figure,pre,blockquote,tr,li{break-inside:avoid !important;"
        "page-break-inside:avoid !important;}\n"
        "h1,h2,h3,h4,h5,h6{break-after:avoid !important;page-break-after:avoid !important;}\n"
        f"@page{{size:{page_width}px {page_height}px;margin:{margin}px;}}\n"
        "}\n"
    )


def render_by_blank_rows(html_path: Path, out_dir: Path, chrome: str, timeout: int,
                         viewport_width: int, viewport_height: int, dsf: float,
                         max_page_height: int, search_margin: int, search_expand: int,
                         keep_full: bool) -> dict:
    """auto 模式：截图整页 -> 四向裁白 -> 空白行智能分页。"""
    page_h = measure_page_height(chrome, html_path, timeout) or viewport_height
    shot_h = max(viewport_height, int(page_h) + 40)

    shot = html_path.parent / "full.png"
    take_screenshot(chrome, html_path, shot, viewport_width, shot_h, timeout, dsf)

    img = Image.open(shot)
    img.load()
    full_size = img.size
    if dsf != 1.0:
        img = img.resize((int(full_size[0] / dsf), int(full_size[1] / dsf)), Image.LANCZOS)
    img = img.convert("RGB")

    if keep_full:
        img.save(out_dir / "full.png", format="PNG")

    # 四向裁白
    trimmed = trim_border(img, WHITE_THRESHOLD, PADDING_TOP, PADDING_BOTTOM,
                          PADDING_LEFT, PADDING_RIGHT)
    if trimmed is None:
        raise RuntimeError("整页都是空白，没有可输出的内容")
    compact_w, compact_h = trimmed.size
    gray = trimmed.convert("L")

    # 空白行智能分页：先按最大高度定出各页的目标切割线，再逐条找最近的空白行
    pages = []
    start_y = 0
    targets = plan_pages(compact_h, max_page_height)
    for i, target_y in enumerate(targets):
        if i == len(targets) - 1:                       # 最后一页：切到底
            cut_y = compact_h - 1
        else:
            cut_y = find_best_cut(gray, compact_w, target_y, search_margin,
                                  WHITE_THRESHOLD, BLANK_ROW_RATIO, search_expand)
            cut_y = max(start_y + 1, min(cut_y, compact_h - 1))
        pages.append((start_y, cut_y + 1))
        start_y = cut_y + 1

    files = []
    for idx, (top, bottom) in enumerate(pages, start=1):
        name = f"{idx:03d}.png"
        trimmed.crop((0, top, compact_w, bottom)).save(out_dir / name, format="PNG")
        files.append(name)

    img.close()
    trimmed.close()

    return {
        "pagination": "blank-row",
        "full_image_size": list(full_size),
        "compact_size": [compact_w, compact_h],
        "page_count": len(files),
        "page_sizes": [[compact_w, b - a] for a, b in pages],
        "files": files,
    }


def render_by_pdf_pages(html_path: Path, out_dir: Path, chrome: str, timeout: int,
                        dpi: int, keep_pdf: bool) -> dict:
    """page 模式：Chrome --print-to-pdf -> PyMuPDF 逐页栅格化。"""
    import fitz  # PyMuPDF（只有这个模式需要）

    pdf_tmp = html_path.parent / "page.pdf"
    print_to_pdf(chrome, html_path, pdf_tmp, timeout)

    files = []
    doc = fitz.open(str(pdf_tmp))
    try:
        page_count = doc.page_count
        sizes = []
        for i in range(page_count):
            pix = doc.load_page(i).get_pixmap(dpi=dpi)
            name = f"{i + 1:03d}.png"
            pix.save(str(out_dir / name))
            files.append(name)
            sizes.append([pix.width, pix.height])
    finally:
        doc.close()                        # 必须关，否则 Windows 上临时 PDF 删不掉

    if keep_pdf:
        shutil.copyfile(pdf_tmp, out_dir / "page.pdf")

    return {
        "pagination": "pdf-page",
        "page_count": page_count,
        "page_sizes": sizes,
        "dpi": dpi,
        "files": files,
    }


def convert(url: str, html: str, final_url: str, mode_dir: Path,
            image_mode: str = "auto",
            main_selector: str | None = None,
            font_family: str = DEFAULT_FONT_FAMILY,
            font_size: int = 14,
            line_height: float = 1.6,
            background: str = "white",
            content_width: int = 760,
            viewport_width: int = 1000,
            viewport_height: int = 10000,
            max_page_height: int = MAX_PAGE_HEIGHT,
            search_margin: int = SEARCH_MARGIN,
            search_expand: int = SEARCH_EXPAND,
            scale_factor: float = 1.0,
            page_width: int = 1200, page_height: int = 1600,
            margin: int = 0, dpi: int | None = None,
            timeout: int = 120, chrome_path: str | None = None,
            keep_html: bool = False, keep_full: bool = False,
            keep_pdf: bool = True) -> dict:
    """把已抓取的 html 渲染成多张图片，产物写进 mode_dir，返回该方式的元信息。"""
    chrome = find_chrome(chrome_path)
    mode_dir.mkdir(parents=True, exist_ok=True)
    for old in mode_dir.glob("*.png"):
        old.unlink()
    for old in (mode_dir / "page.pdf",):
        if old.exists():
            old.unlink()

    if image_mode == "auto":
        soup = BeautifulSoup(html, "html.parser")
        for tag in soup(DROP_TAGS_RENDER):
            tag.decompose()
        scope, used_selector = pick_main(soup, main_selector)
        content_html = str(scope)
        css = build_shell_css(font_family, font_size, line_height, background, content_width)
        shell = wrap_shell(content_html, final_url, css, measure_js=MEASURE_JS_HEIGHT)
    else:  # page：真实整页渲染，保留站点自身样式
        used_selector = "整页(站点原样式)"
        shell = wrap_shell(html, final_url,
                           build_print_css(page_width, page_height, margin),
                           measure_js=None)

    with tempfile.TemporaryDirectory(prefix="html2img_") as work:
        work = Path(work)
        html_path = work / "page.html"
        html_path.write_text(shell, encoding="utf-8")

        if image_mode == "auto":
            result = render_by_blank_rows(
                html_path, mode_dir, chrome, timeout,
                viewport_width, viewport_height, scale_factor,
                max_page_height, search_margin, search_expand, keep_full,
            )
        else:
            result = render_by_pdf_pages(
                html_path, mode_dir, chrome, timeout,
                dpi or round(96 * scale_factor), keep_pdf,
            )

        if keep_html:
            (mode_dir / "page.html").write_text(shell, encoding="utf-8")

    return {
        "image_mode": image_mode,
        "main_selector": used_selector,
        "chrome": chrome,
        "style": {
            "font_family": font_family,
            "font_size": font_size,
            "line_height": line_height,
            "background": background,
            "content_width": content_width,
        },
        "pagination_params": {
            "max_page_height": max_page_height,
            "search_margin": search_margin,
            "search_expand": search_expand,
            "white_threshold": WHITE_THRESHOLD,
            "blank_row_ratio": BLANK_ROW_RATIO,
            "padding": [PADDING_TOP, PADDING_BOTTOM, PADDING_LEFT, PADDING_RIGHT],
        },
        "rendered_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        **result,
    }
