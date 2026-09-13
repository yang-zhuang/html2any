# -*- coding: utf-8 -*-
"""html2any 统一入口：一个 URL，一次抓取，三种产物。

用法：
    python main.py <url>                            # 三种方式全跑
    python main.py <url> --modes markdown,structure # 只跑选中的方式
    python main.py <url> --force                    # 忽略断点续传，重跑选中方式
    python main.py list                             # 列出已有运行记录

产物目录（run_id = 日期 + 归一化 url 的 8 位哈希）：
    outputs/20260913_a1b2c3d4/
        meta.json       运行元信息 + 各方式完成状态（断点续传依据）
        source.html     抓下来的原始 html（三种方式共用一次网络请求）
        markdown/       article.md + imgs/
        images/         001.png ...（--image-mode page 时另有 page.pdf）
        structure/      result.json + snapshots/（--images 时另有 imgs/）

断点续传：重跑同一 URL 自动命中同一 run 目录；meta.json 里 status=done 的方式
直接跳过（--force 可强制重跑）；structure 方式内部另有 part 文件级断点续传。
"""
from __future__ import annotations

import argparse
import re
import sys
import time
from pathlib import Path

from bs4 import BeautifulSoup

from html2any import config
from html2any.chrome import DEFAULT_FONT_FAMILY
from html2any.fetch import fetch_html
from html2any.meta import load_meta, save_meta
from html2any.runid import make_run_id, url_hash

RUN_ID_PATTERN = re.compile(r"^\d{8}_[0-9a-f]{8}$")

MODE_ALIASES = {
    "md": "markdown", "markdown": "markdown",
    "img": "images", "image": "images", "images": "images",
    "struct": "structure", "structure": "structure",
}


# ============================================================
#  各方式的调用封装（把 CLI 参数接到对应模块的 convert）
# ============================================================

def run_markdown(args, html, final_url, run_dir) -> dict:
    from html2any.modes import to_markdown
    return to_markdown.convert(
        args.url, html, final_url, run_dir / "markdown",
        with_images=not args.no_images,
        main_selector=args.main_selector,
        timeout=args.timeout,
    )


def run_images(args, html, final_url, run_dir) -> dict:
    from html2any.modes import to_images
    return to_images.convert(
        args.url, html, final_url, run_dir / "images",
        image_mode=args.image_mode,
        main_selector=args.main_selector,
        font_family=args.font_family,
        font_size=args.font_size,
        line_height=args.line_height,
        background=args.background,
        content_width=args.content_width,
        viewport_width=args.viewport_width,
        viewport_height=args.viewport_height,
        max_page_height=args.max_page_height,
        search_margin=args.search_margin,
        search_expand=args.search_expand,
        scale_factor=args.scale_factor,
        page_width=args.page_width,
        page_height=args.page_height,
        margin=args.margin,
        dpi=args.dpi,
        timeout=args.timeout,
        chrome_path=args.chrome or config.CHROME_PATH,
        keep_html=args.keep_html,
        keep_full=args.keep_full,
        keep_pdf=not args.no_pdf,
    )


def run_structure(args, html, final_url, run_dir) -> dict:
    from html2any.modes import to_structure
    return to_structure.convert(
        args.url, html, final_url, run_dir / "structure",
        main_selector=args.main_selector,
        levels_spec=args.levels,
        drop_selectors=tuple(args.drop_selectors or ()),
        with_images=args.images,
        snapshot=not args.no_snapshot,
        font_family=args.font_family,
        font_size=args.font_size,
        line_height=args.line_height,
        background=args.background,
        content_width=args.content_width,
        viewport_width=args.viewport_width,
        max_page_height=args.max_page_height,
        search_margin=args.search_margin,
        search_expand=args.search_expand,
        timeout=args.timeout,
        chrome_path=args.chrome or config.CHROME_PATH,
    )


MODE_RUNNERS = {"markdown": run_markdown, "images": run_images, "structure": run_structure}


def print_mode_summary(mode: str, meta: dict) -> None:
    if mode == "markdown":
        print(f"[ok] markdown  : article.md（{meta['markdown_chars']} 字符），"
              f"图片 {meta['images_downloaded']} 张 -> markdown/imgs/")
    elif mode == "images":
        print(f"[ok] images    : {meta['image_mode']} 模式（{meta['pagination']}），"
              f"{meta['page_count']} 张 -> images/")
        for i, (w, h) in enumerate(meta["page_sizes"], start=1):
            print(f"       {i:03d}.png  {w}x{h} px")
    elif mode == "structure":
        print(f"[ok] structure : {meta['node_count']} 个节点，最深 {meta['max_depth']} 层"
              f" -> structure/result.json")
        print(f"       截图 {meta['snapshot_count']} 张 -> structure/snapshots/"
              f"（清掉噪音节点 {meta['noise_removed']['nodes']} 个 / "
              f"元数据属性 {meta['noise_removed']['attrs']} 处）")


# ============================================================
#  子命令：convert（默认）与 list
# ============================================================

def cmd_convert(args) -> int:
    modes = []
    for part in str(args.modes).split(","):
        name = MODE_ALIASES.get(part.strip().lower())
        if not name:
            print(f"[error] 未知方式: {part}（可选 markdown / images / structure）")
            return 1
        if name not in modes:
            modes.append(name)
    if not modes:
        print("[error] --modes 为空")
        return 1

    out_root = Path(args.out or config.OUTPUT_DIR)
    run_id = make_run_id(args.url)
    run_dir = out_root / run_id

    meta = load_meta(run_dir)
    if meta and meta.get("url_hash") != url_hash(args.url):
        print(f"[error] run_id 哈希冲突: {run_id} 已属于 {meta.get('url')}")
        return 1

    resume = meta is not None and run_dir.exists()
    # ---- 抓取（或复用上次抓好的 source.html）----
    source_path = run_dir / "source.html"
    if resume and source_path.exists() and source_path.stat().st_size > 0:
        html = source_path.read_text(encoding="utf-8")
        final_url = meta.get("final_url") or args.url
        print(f"[ok] 复用已抓取的 source.html（{source_path.stat().st_size} 字节），不重新请求")
    else:
        html, final_url = fetch_html(args.url, timeout=config.HTTP_TIMEOUT,
                                     retries=config.HTTP_RETRIES)
        run_dir.mkdir(parents=True, exist_ok=True)
        source_path.write_text(html, encoding="utf-8")

    if meta is None:
        soup = BeautifulSoup(html, "html.parser")
        title = soup.title.get_text(strip=True) if soup.title else ""
        meta = {
            "run_id": run_id,
            "url": args.url,
            "url_hash": url_hash(args.url),
            "final_url": final_url,
            "title": title,
            "fetched_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            "source_html": source_path.name,
            "modes": {},
        }

    # ---- 逐个方式执行：done 跳过，error 重试 ----
    failed = []
    for mode in modes:
        state = meta["modes"].get(mode, {})
        if state.get("status") == "done" and not args.force:
            print(f"[skip] {mode:<10} 已完成（--force 可强制重跑）")
            continue

        print(f"[run ] {mode:<10} ...")
        try:
            mode_meta = MODE_RUNNERS[mode](args, html, final_url, run_dir)
        except Exception as exc:                           # noqa: BLE001
            print(f"[error] {mode} 失败: {exc}")
            meta["modes"][mode] = {**state, "status": "error", "error": str(exc)}
            save_meta(run_dir, meta)
            failed.append(mode)
            continue

        state.pop("error", None)          # 清掉上次失败残留的错误信息
        meta["modes"][mode] = {**state, "status": "done", **mode_meta}
        save_meta(run_dir, meta)          # 每跑完一个方式立刻落盘，中途崩了也能续传
        print_mode_summary(mode, mode_meta)

    print(f"[ok] 运行目录   : {run_dir}")
    if failed:
        print(f"[warn] 以下方式失败（重跑同一命令会自动重试）: {', '.join(failed)}")
        return 1
    return 0


def cmd_list(args) -> int:
    out_root = Path(args.out or config.OUTPUT_DIR)
    if not out_root.exists():
        print(f"（{out_root} 不存在，还没有运行记录）")
        return 0

    rows = []
    for run_dir in sorted(out_root.iterdir()):
        if not run_dir.is_dir() or not RUN_ID_PATTERN.match(run_dir.name):
            continue
        meta = load_meta(run_dir)
        if not meta:
            rows.append((run_dir.name, "?", "?", "", "meta.json 缺失/损坏"))
            continue
        statuses = " ".join(
            f"{m}:{s.get('status', '?')}" for m, s in meta.get("modes", {}).items()
        ) or "(尚未跑任何方式)"
        title = (meta.get("title") or "")[:24]
        url = (meta.get("final_url") or meta.get("url") or "")[:60]
        rows.append((run_dir.name, title, url, statuses, ""))

    if not rows:
        print(f"（{out_root} 下没有运行记录）")
        return 0

    width = max(len(r[0]) for r in rows)
    for run_id, title, url, statuses, note in rows:
        line = f"{run_id:<{width}}  {statuses}"
        if title:
            line += f"  {title}"
        if note:
            line += f"  [{note}]"
        print(line)
        if url:
            print(f"{'':<{width}}  {url}")
    return 0


# ============================================================
#  CLI
# ============================================================

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="html2any",
        description="一个 URL，一次抓取，三种产物：markdown / 分页长图 / 结构化 JSON 树",
    )
    parser.add_argument("url", help="要处理的 html 网址；特殊值 list = 列出已有运行记录")
    parser.add_argument("--modes", default="markdown,images,structure",
                        help="要跑的方式，逗号分隔：markdown,images,structure（默认全部）")
    parser.add_argument("--out", default=None,
                        help="产物根目录，默认 .env 的 OUTPUT_DIR 或 ./outputs")
    parser.add_argument("--force", action="store_true",
                        help="忽略断点续传，重跑选中的方式")

    g = parser.add_argument_group("共享参数")
    g.add_argument("--main", default=None, dest="main_selector",
                   help="正文容器 CSS 选择器，如 '.article-body'；默认自动挑选")
    g.add_argument("--timeout", type=int, default=120,
                   help="HTTP 请求 / Chrome 的超时秒数，默认 120")
    g.add_argument("--chrome", default=None, help="Chrome/Edge 可执行文件路径（默认自动探测）")
    g.add_argument("--font-family", default=DEFAULT_FONT_FAMILY,
                   help=f"渲染字体（images/structure），默认 {DEFAULT_FONT_FAMILY}")
    g.add_argument("--font-size", type=int, default=14, help="字号(px)，默认 14")
    g.add_argument("--line-height", type=float, default=1.6, help="行高，默认 1.6")
    g.add_argument("--background", default="white", help="底色，默认 white")
    g.add_argument("--content-width", type=int, default=760, help="正文宽度(px)，默认 760")
    g.add_argument("--viewport-width", type=int, default=1000,
                   help="截图视口宽(px)，默认 1000")
    g.add_argument("--max-page-height", type=int, default=720,
                   help="单张图最大高度(px)，默认 720")
    g.add_argument("--search-margin", type=int, default=80,
                   help="找空白行的上下搜索范围(px)，默认 80")
    g.add_argument("--search-expand", type=int, default=3,
                   help="范围内找不到空白行时把范围翻倍再找的最大次数，默认 3（设为 1 等同原版）")

    g = parser.add_argument_group("markdown 方式")
    g.add_argument("--no-images", action="store_true",
                   help="markdown：不下载图片，只保留绝对地址")

    g = parser.add_argument_group("images 方式")
    g.add_argument("--image-mode", choices=("auto", "page"), default="auto",
                   help="auto=空白行智能分页（默认）；page=排版引擎分页（尺寸严格固定）")
    g.add_argument("--viewport-height", type=int, default=10000,
                   help="auto 模式截图视口高(px)，默认 10000")
    g.add_argument("--scale-factor", type=float, default=1.0,
                   help="像素密度倍数（2 表示 2 倍图），默认 1.0")
    g.add_argument("--page-width", type=int, default=1200, help="page 模式每页宽(px)，默认 1200")
    g.add_argument("--page-height", type=int, default=1600, help="page 模式每页高(px)，默认 1600")
    g.add_argument("--margin", type=int, default=0, help="page 模式页边距(px)，默认 0")
    g.add_argument("--dpi", type=int, default=None,
                   help="page 模式输出分辨率，默认 96 * --scale-factor")
    g.add_argument("--keep-html", action="store_true", help="images：把注入后的 html 一并存下")
    g.add_argument("--keep-full", action="store_true",
                   help="images auto 模式：保留裁白前的整页长图 full.png")
    g.add_argument("--no-pdf", action="store_true", help="images page 模式：不保留中间 PDF")

    g = parser.add_argument_group("structure 方式")
    g.add_argument("--levels", default="1-6",
                   help="用哪几级标题建树，如 '2-4' 或 '2,3'；默认 1-6")
    g.add_argument("--drop", default=None, dest="drop_selectors", action="append",
                   metavar="SELECTOR", help="额外要丢掉的噪音选择器（可重复）")
    g.add_argument("--images", action="store_true",
                   help="structure：把页面图片下载到本地 imgs/（默认关闭，src 保持远程地址）")
    g.add_argument("--no-snapshot", action="store_true",
                   help="structure：只输出 result.json，不截图")
    return parser


def main(argv=None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.url == "list":
        return cmd_list(args)
    return cmd_convert(args)


if __name__ == "__main__":
    raise SystemExit(main())
