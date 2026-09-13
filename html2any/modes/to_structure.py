# -*- coding: utf-8 -*-
"""方式三：html -> JSON 递归树（result.json）+ 每个节点对应的快照图。

按页面自身 h1~h6 层级递归建树；每个节点带 content / clean_content /
snapshot_path；逐节点截图，表格单独分段，支持文件级断点续传
（已有 {base}_part*.png 的节点直接跳过）。
"""
from __future__ import annotations

import json
import os
import re
import tempfile
import time
from pathlib import Path
from urllib.parse import urlparse

from bs4 import BeautifulSoup
from PIL import Image

from html2any.chrome import (DEFAULT_FONT_FAMILY, MEASURE_JS_FULL, build_shell_css,
                             find_chrome, measure_page_size, take_screenshot, wrap_shell)
from html2any.extract import DROP_TAGS_TEXT, localize_images, pick_main
from html2any.paginate import (BLANK_ROW_RATIO, MAX_PAGE_HEIGHT, PADDING_BOTTOM,
                               PADDING_LEFT, PADDING_RIGHT, PADDING_TOP,
                               SEARCH_EXPAND, SEARCH_MARGIN, WHITE_THRESHOLD,
                               find_best_cut, trim_border)

LEVEL_NAMES = {1: "一级", 2: "二级", 3: "三级", 4: "四级", 5: "五级", 6: "六级"}
HEADING_TAGS = ["h1", "h2", "h3", "h4", "h5", "h6"]

# 内容比正文宽度还宽时（宽表格、不折行的 pre 等），把截图窗口一起加宽，
# 否则右半边会被视口裁掉。上限防止个别页面把窗口撑到离谱的尺寸。
MAX_SHOT_WIDTH = 5000

# 不可见噪音选择器：编辑链接、占位空元素、跳转链接（MediaWiki 系站点常见）。
# .mw-editsection 会真的被画进截图（实测 Wikipedia 每张图顶部都有一个 [edit]）；
# .mw-empty-elt 按 MediaWiki 的定义就是"渲染不出东西"的占位元素，
# 但它内部常挂着整坨 data-mw 的 JSON，是 result.json 体积的大头。
NOISE_SELECTORS = (".mw-editsection", ".mw-empty-elt", ".mw-jump-link")

# 不可见元数据属性：MediaWiki 的 RDFa 标注和模板源码，渲染和阅读都用不到。
# 实测在 Wikipedia 上 data-mw 11,378 + typeof 6,805 + about 5,720 字符，占 content 9.4%。
META_ATTRS = ("data-mw", "data-mw-ts", "typeof", "about")


def parse_levels(spec: str) -> set[int]:
    """解析 --levels，支持 "1-6" / "2,3,4" / "2-4,6" 三种写法。"""
    out: set[int] = set()
    for part in str(spec).split(","):
        part = part.strip()
        if not part:
            continue
        if "-" in part:
            a, b = part.split("-", 1)
            out.update(range(int(a), int(b) + 1))
        else:
            out.add(int(part))
    picked = {lv for lv in out if 1 <= lv <= 6}
    return picked or {1, 2, 3, 4, 5, 6}


def safe_filename(title: str) -> str:
    """把标题转成安全的文件名片段。"""
    return re.sub(r"[^\w\u4e00-\u9fff\-_ ]", "", title).strip().replace(" ", "_")[:50]


def strip_noise(scope, extra_selectors=()) -> dict:
    """去掉正文里不可见 / 无意义的网页噪音，返回清理统计。

    做三件事（网页正文常见、但渲染和阅读都不需要的噪音）：
      1. 丢掉编辑链接、占位空元素等选择器命中的节点；
      2. 丢掉不可见的元数据属性（data-mw / typeof / about ...）；
      3. 丢掉 rel 值里带 mw: 的媒体维基专用 rel。

    这一步必须在 localize_images 之前做：否则会给 [edit] 图标也下一个文件。
    """
    stats = {"nodes": 0}
    for sel in tuple(NOISE_SELECTORS) + tuple(extra_selectors):
        for node in scope.select(sel):
            node.decompose()
            stats["nodes"] += 1

    attrs = 0
    for node in scope.find_all(True):
        for attr in META_ATTRS:
            if node.attrs.pop(attr, None) is not None:
                attrs += 1
        rel = node.attrs.get("rel")
        if isinstance(rel, list) and any("mw:" in str(r) for r in rel):
            node.attrs.pop("rel", None)
            attrs += 1
    stats["attrs"] = attrs
    return stats


# ============================================================
#  建树：把正文 DOM 拍平成块，再按标题层级递归
# ============================================================

def build_block_list(scope):
    """把正文 DOM 拍平成一维块列表：标题单独成块，不含标题的子树整体成块。

    网页的正文常常嵌在很多层 <div> 里，直接取 body.children 往往只剩一个
    容器、一个标题都找不到，所以先做一次 DFS 拍平。

    这里做一次 DFS：
      - 命中标题标签        -> 单独输出一块
      - 子树里还有标题      -> 继续往下拆
      - 子树里一个标题都没有 -> 整棵当一块（保持段落 / 表格 / 图片不被拆散）
    这样既保证标题都被分离出来，又不会把普通段落切碎。
    """
    blocks = []

    def walk(node):
        for child in node.children:
            name = getattr(child, "name", None)
            if name is None:                       # NavigableString
                continue
            if name in HEADING_TAGS:
                blocks.append(child)
            elif child.find(HEADING_TAGS) is not None:
                walk(child)
            else:
                blocks.append(child)

    walk(scope)
    return blocks


def build_tree(blocks, positions, levels):
    """按标题层级递归建树。

    栈逻辑：层级比栈顶深就是栈顶的子节点，
    否则弹栈直到遇到更浅的一层。返回 (root, flat)，flat 是前序遍历结果——
    顺序与 blocks 里的标题顺序完全相同。
    """
    root: list[dict] = []
    flat: list[dict] = []
    stack: list[tuple[int, dict]] = []

    for pos, lv in zip(positions, levels):
        title = re.sub(r"\s+", " ", blocks[pos].get_text(" ", strip=True)).strip()
        node = {
            "level": LEVEL_NAMES.get(lv, f"{lv}级"),
            "raw_title": title,
            "children": [],
        }
        flat.append(node)

        if not stack:
            root.append(node)
            stack.append((lv, node))
        elif lv > stack[-1][0]:
            stack[-1][1]["children"].append(node)
            stack.append((lv, node))
        else:
            while stack and stack[-1][0] >= lv:
                stack.pop()
            if not stack:
                root.append(node)
            else:
                stack[-1][1]["children"].append(node)
            stack.append((lv, node))

    return root, flat


def slice_contents(blocks, positions) -> list[str]:
    """切出每个节点的 content：本标题块之后、下一个标题块之前的所有块。

    end_idx 取下一个**任意层级**标题的下标，所以父节点的 content 不包含子章节正文。
    """
    contents = []
    for k, pos in enumerate(positions):
        end = positions[k + 1] if k + 1 < len(positions) else len(blocks)
        contents.append("".join(str(b) for b in blocks[pos + 1:end]).strip())
    return contents


def make_clean_content(content_html: str) -> str:
    """去掉 <table>，把 <img> 的 src 清空、alt 置为「图片」。"""
    if not content_html:
        return ""
    soup = BeautifulSoup(content_html, "html.parser")
    for table in soup.find_all("table"):
        table.decompose()
    for img in soup.find_all("img"):
        img["src"] = ""
        img["alt"] = "图片"
    return str(soup).strip()


# ============================================================
#  表格单独分段（长表格不被页高限制切断）
# ============================================================

def split_html_to_segments(content_html: str) -> list[dict]:
    """把 HTML 拆成「表格段」和「非表格段」的有序列表。

    表格单独成段，各段分别截图再连续编号 —— 这样一张长表格不会被页高限制切断。

    除 table 特殊处理外，其余标签（pre / blockquote / figure 等）一律保留；
    若只收 p/div/ul/ol/h1~h6，网页正文里常见的这些标签会被静默丢弃。
    """
    soup = BeautifulSoup(content_html, "html.parser")
    body = soup.body if soup.body else soup

    segments: list[dict] = []
    pending: list = []

    for child in body.children:
        name = getattr(child, "name", None)
        if name is None:
            if str(child).strip():
                pending.append(child)
            continue
        if name == "table":
            if pending:
                segments.append({"html": "".join(str(c) for c in pending), "is_table": False})
                pending = []
            segments.append({"html": str(child), "is_table": True})
        else:
            pending.append(child)

    if pending:
        segments.append({"html": "".join(str(c) for c in pending), "is_table": False})
    return segments


# ============================================================
#  Chrome 无头截图（逐节点）
# ============================================================

def _absolutize_local_images(fragment_html: str, assets_root: Path) -> str:
    """把片段里 imgs/001.png 这类相对路径临时换成 file:// 绝对地址。

    result.json 里保留相对路径（整个输出目录可以打包搬走），
    但渲染时临时 html 落在系统临时目录，相对路径指不到 imgs/，所以渲染前换一次。
    """
    if "imgs/" not in fragment_html:
        return fragment_html
    soup = BeautifulSoup(fragment_html, "html.parser")
    for img in soup.find_all("img"):
        src = (img.get("src") or "").strip()
        if not src or re.match(r"^(https?:|data:|file:|//)", src, re.I):
            continue
        local = assets_root / src
        if local.exists():
            img["src"] = local.resolve().as_uri()
    return str(soup)


def render_fragment(fragment_html: str, temp_prefix: str, snap_dir: Path, assets_root: Path,
                    base_url: str, chrome: str, timeout: int,
                    font_family: str, font_size: int, line_height: float,
                    background: str, content_width: int, viewport_width: int,
                    max_page_height: int, search_margin: int, search_expand: int) -> list[str]:
    """把一个 HTML 片段渲染并切成若干张 PNG，写进 snap_dir，返回临时文件名列表。

    流程：套外壳 -> 截图 -> 四向裁白 ->
    超过 max_page_height 就按空白行分页。文件名带 temp_prefix，调用方随后会统一改名。
    """
    fragment_html = _absolutize_local_images(fragment_html, assets_root)
    css = build_shell_css(font_family, font_size, line_height, background, content_width)
    shell = wrap_shell(fragment_html, base_url, css, measure_js=MEASURE_JS_FULL)

    with tempfile.TemporaryDirectory(prefix="h2s_") as work:
        work = Path(work)
        html_path = work / "frag.html"
        html_path.write_text(shell, encoding="utf-8")

        page_h = measure_page_size(chrome, html_path, timeout, viewport_width)

        # 内容比正文宽度还宽（宽表格 / 不折行的 pre）-> 加宽截图窗口，避免右侧被裁掉。
        # 门槛用 content_width 而不是 viewport_width：body 固定 760px 且居中，
        # 只有内容真的超出正文宽度才算溢出。
        shot_w = viewport_width
        right, body_left, height = page_h
        if right is not None and body_left is not None:
            extent = right - body_left                 # 内容自 body 左边缘往右的延伸
            if extent > content_width + 2:
                need = int(2 * extent - content_width) + 20
                if need > MAX_SHOT_WIDTH:
                    print(f"  [warn] 内容宽约 {extent}px，超过窗口上限 {MAX_SHOT_WIDTH}px，"
                          f"右侧仍可能被裁")
                    need = MAX_SHOT_WIDTH
                shot_w = max(viewport_width, need)
                if shot_w != viewport_width:
                    # 换了宽度要重新量高度：宽度变了折行就变了
                    _, _, height2 = measure_page_size(chrome, html_path, timeout, shot_w)
                    if height2:
                        height = height2

        shot_h = int(height) + 40 if height else 10000
        shot_h = max(shot_h, 200)

        shot = work / "shot.png"
        take_screenshot(chrome, html_path, shot, shot_w, shot_h, timeout)

        img = Image.open(shot)
        img.load()
        img = img.convert("RGB")

        trimmed = trim_border(img, WHITE_THRESHOLD, PADDING_TOP, PADDING_BOTTOM,
                              PADDING_LEFT, PADDING_RIGHT)
        if trimmed is None:                      # 整段全白
            img.close()
            return []

        compact_w, compact_h = trimmed.size
        gray = trimmed.convert("L")
        names: list[str] = []

        if compact_h <= max_page_height:
            name = f"{temp_prefix}_001.png"
            trimmed.save(snap_dir / name, format="PNG")
            names.append(name)
        else:
            start_y = 0
            page_num = 1
            while start_y < compact_h:
                target_y = min(start_y + max_page_height, compact_h - 1)
                if start_y + max_page_height >= compact_h:
                    cut_y = compact_h - 1
                else:
                    cut_y = find_best_cut(gray, compact_w, target_y, search_margin,
                                          WHITE_THRESHOLD, BLANK_ROW_RATIO, search_expand)
                    cut_y = max(start_y, min(cut_y, compact_h - 1))
                piece = trimmed.crop((0, start_y, compact_w, cut_y + 1))
                name = f"{temp_prefix}_{page_num:03d}.png"
                piece.save(snap_dir / name, format="PNG")
                piece.close()
                names.append(name)
                page_num += 1
                start_y = cut_y + 1

        img.close()
        trimmed.close()
        return names


def _rename_with_retry(old: Path, new: Path) -> None:
    """Windows 上文件可能被别的进程短暂占用，os.replace 失败时重试。"""
    for attempt in range(5):
        try:
            os.replace(old, new)
            return
        except OSError:
            if attempt < 4:
                time.sleep(0.1 * (attempt + 1))
            else:
                raise


def render_node(content_html: str, base_name: str, snap_dir: Path, assets_root: Path,
                base_url: str, chrome: str, timeout: int,
                font_family: str, font_size: int, line_height: float,
                background: str, content_width: int, viewport_width: int,
                max_page_height: int, search_margin: int, search_expand: int) -> list[str]:
    """渲染一个节点：表格单独分段，各段分别渲染，再连续编号。

    最终文件名统一为 {base_name}_part{N}.png，N 在本节点内从 1 连续递增。
    """
    segments = split_html_to_segments(content_html)
    names: list[str] = []
    counter = 0

    for seg in segments:
        if not seg["html"].strip():
            continue
        produced = render_fragment(
            seg["html"], "_tmp_render", snap_dir, assets_root, base_url, chrome, timeout,
            font_family, font_size, line_height, background, content_width,
            viewport_width, max_page_height, search_margin, search_expand,
        )
        for tmp_name in produced:
            counter += 1
            final_name = f"{base_name}_part{counter}.png"
            _rename_with_retry(snap_dir / tmp_name, snap_dir / final_name)
            names.append(final_name)

    return names


# ============================================================
#  主流程
# ============================================================

def convert(url: str, html: str, final_url: str, mode_dir: Path,
            main_selector: str | None = None,
            levels_spec: str = "1-6",
            drop_selectors=(),
            with_images: bool = False,
            snapshot: bool = True,
            font_family: str = DEFAULT_FONT_FAMILY,
            font_size: int = 14,
            line_height: float = 1.6,
            background: str = "white",
            content_width: int = 760,
            viewport_width: int = 1000,
            max_page_height: int = MAX_PAGE_HEIGHT,
            search_margin: int = SEARCH_MARGIN,
            search_expand: int = SEARCH_EXPAND,
            timeout: int = 120,
            chrome_path: str | None = None) -> dict:
    """把已抓取的 html 结构化成 JSON 树 + 快照图，产物写进 mode_dir，返回元信息。"""
    allowed_levels = parse_levels(levels_spec)
    chrome = find_chrome(chrome_path) if snapshot else None

    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(DROP_TAGS_TEXT):
        tag.decompose()
    page_title = soup.title.get_text(strip=True) if soup.title else ""

    scope, used_selector = pick_main(soup, main_selector)
    # 重新解析一次，把正文节点从整页 DOM 里摘出来，避免带上兄弟节点
    scope = BeautifulSoup(str(scope), "html.parser")
    noise = strip_noise(scope, drop_selectors)

    snap_dir = mode_dir / "snapshots"
    mode_dir.mkdir(parents=True, exist_ok=True)

    img_count = localize_images(
        scope, final_url, mode_dir / "imgs", enabled=with_images, timeout=timeout
    )

    # ---- 建树：正文 DOM -> 一维块列表 -> 按 h1~h6 层级递归 ----
    blocks = build_block_list(scope)
    positions: list[int] = []
    levels: list[int] = []
    for i, block in enumerate(blocks):
        if block.name in HEADING_TAGS:
            lv = int(block.name[1])
            if lv in allowed_levels:
                positions.append(i)
                levels.append(lv)

    if not positions:
        print(f"  [warn] 正文里没找到任何标题（--levels {levels_spec}），树是空的。"
              "试试 --main 指定正文容器，或放宽 --levels。")

    tree, flat = build_tree(blocks, positions, levels)
    contents = slice_contents(blocks, positions)

    # ---- 逐节点：clean_content + 截图 ----
    if snapshot:
        snap_dir.mkdir(parents=True, exist_ok=True)
    snapshot_total = 0
    rendered = 0

    for i, node in enumerate(flat):
        content_html = contents[i]
        node["content"] = content_html
        node["clean_content"] = make_clean_content(content_html)

        if not content_html:
            node["snapshot_path"] = []
            continue

        base_name = f"{i}_{safe_filename(node['raw_title'])}" if safe_filename(node["raw_title"]) \
            else f"{i}_unnamed"

        if not snapshot:
            node["snapshot_path"] = []
            continue

        # 断点续传：已有 {base}_part*.png 就跳过
        existing = sorted(
            f for f in os.listdir(snap_dir)
            if f.startswith(f"{base_name}_part") and f.endswith(".png")
        )
        if existing:
            node["snapshot_path"] = existing
            snapshot_total += len(existing)
            print(f"  [skip] {i:>3} {node['raw_title'][:34]}  已有 {len(existing)} 张")
            continue

        # 清掉上次可能残留的临时文件
        for stale in snap_dir.glob("_tmp_render_*.png"):
            stale.unlink(missing_ok=True)

        try:
            paths = render_node(
                content_html, base_name, snap_dir, mode_dir, final_url, chrome, timeout,
                font_family, font_size, line_height, background, content_width,
                viewport_width, max_page_height, search_margin, search_expand,
            )
        except Exception as exc:                       # noqa: BLE001
            print(f"  [warn] 节点截图失败，snapshot_path 置空: {node['raw_title'][:30]} ({exc})")
            paths = []

        node["snapshot_path"] = paths
        snapshot_total += len(paths)
        rendered += 1
        print(f"  [ok]   {i:>3} {node['raw_title'][:34]:<36} content={len(content_html):>6} "
              f"-> {len(paths)} 张")

    # ---- 落盘 ----
    result_path = mode_dir / "result.json"
    result_path.write_text(
        json.dumps({"tree": tree}, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    def max_depth(nodes):
        if not nodes:
            return 0
        return 1 + max(max_depth(n.get("children") or []) for n in nodes)

    return {
        "page_title": page_title,
        "main_selector": used_selector,
        "levels": sorted(allowed_levels),
        "images_downloaded": img_count,
        "images_localized": with_images,
        "snapshot_enabled": snapshot,
        "node_count": len(flat),
        "max_depth": max_depth(tree),
        "block_count": len(blocks),
        "noise_removed": noise,
        "snapshot_count": snapshot_total,
        "rendered_nodes": rendered,
        "result_file": result_path.name,
        "snapshots_dir": snap_dir.name if snapshot else None,
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
        "converted_at": time.strftime("%Y-%m-%d %H:%M:%S"),
    }


def json_dumps_tree(tree) -> str:
    return json_dumps({"tree": tree})


def json_dumps(obj) -> str:
    import json
    return json.dumps(obj, ensure_ascii=False, indent=2)
