# -*- coding: utf-8 -*-
"""Chrome/Edge 无头浏览器：探测、执行、测高测宽、截图、打印 PDF（方式二/三共用）。"""
from __future__ import annotations

import re
import shutil
import subprocess
import tempfile
from pathlib import Path

# 浏览器可执行文件候选路径（Windows 优先，带 macOS / Linux 兜底）
CHROME_CANDIDATES = (
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    "/usr/bin/google-chrome",
    "/usr/bin/chromium",
    "/usr/bin/chromium-browser",
    "/usr/bin/microsoft-edge",
)

# 测高脚本（方式二用）：把整页高度写进 <title>，后面用 --dump-dom 读出来
MEASURE_JS_HEIGHT = """
(function () {
  function measure() {
    try {
      var d = document, b = d.body;
      var h = Math.max(
        d.documentElement.scrollHeight,
        b ? b.scrollHeight : 0,
        b ? Math.round(b.getBoundingClientRect().height) : 0
      );
      d.title = 'H=' + h;
    } catch (e) { d.title = 'H=0'; }
  }
  window.addEventListener('load', function () { measure(); setTimeout(measure, 600); });
  setTimeout(measure, 400);
})();
"""

# 测高 / 测宽脚本（方式三用）：
#   H = 整页高度
#   W = 内容右边界（所有元素 rect.right 的最大值）
#   B = body 左边界
# 之所以要 W/B：外壳里 body 是 width:760px + margin:auto 居中的，宽表格这类内容会
# 溢出到 body 右边，甚至顶到视口外被裁掉。有了 W-B 就能算出需要多宽的截图窗口。
MEASURE_JS_FULL = """
(function () {
  function measure() {
    try {
      var d = document, b = d.body, de = d.documentElement;
      var h = Math.max(
        de.scrollHeight,
        b ? b.scrollHeight : 0,
        b ? Math.round(b.getBoundingClientRect().height) : 0
      );
      var right = Math.max(de.scrollWidth, b ? b.scrollWidth : 0);
      var all = d.querySelectorAll('*');
      for (var i = 0; i < all.length; i++) {
        var r = all[i].getBoundingClientRect();
        if (r.width > 0 && r.right > right) { right = r.right; }
      }
      var left = b ? Math.round(b.getBoundingClientRect().left) : 0;
      d.title = 'W=' + Math.ceil(right) + ';B=' + left + ';H=' + h;
    } catch (e) { d.title = 'W=0;B=0;H=0'; }
  }
  window.addEventListener('load', function () { measure(); setTimeout(measure, 600); });
  setTimeout(measure, 400);
})();
"""

# 外壳样式默认字体
DEFAULT_FONT_FAMILY = '"Microsoft YaHei", "SimSun", sans-serif'


def find_chrome(explicit: str | None = None) -> str:
    """找浏览器可执行文件：优先显式路径（--chrome / CHROME_PATH），其次常见安装路径，最后 PATH。"""
    if explicit:
        p = Path(explicit)
        if not p.exists():
            raise FileNotFoundError(f"指定的浏览器路径不存在: {explicit}")
        return str(p)

    for cand in CHROME_CANDIDATES:
        if Path(cand).exists():
            return cand

    for name in ("chrome", "google-chrome", "chromium", "chromium-browser",
                 "msedge", "microsoft-edge"):
        found = shutil.which(name)
        if found:
            return found

    raise RuntimeError(
        "没有找到 Chrome/Edge。请安装 Chrome，或用 --chrome / CHROME_PATH 指定可执行文件路径。"
    )


def run_chrome(chrome: str, args: list[str], timeout: int):
    """跑一次 Chrome 无头。优先 --headless=new，失败退回旧 --headless。"""
    last = "unknown"
    for flag in ("--headless=new", "--headless"):
        with tempfile.TemporaryDirectory(prefix="chrome_profile_") as profile:
            cmd = [
                chrome, flag,
                "--disable-gpu",
                "--hide-scrollbars",
                "--no-first-run",
                "--no-default-browser-check",
                "--disable-extensions",
                f"--user-data-dir={profile}",
            ] + args
            try:
                proc = subprocess.run(cmd, capture_output=True, timeout=timeout)
            except subprocess.TimeoutExpired:
                last = f"{flag} 超时({timeout}s)"
                continue
            if proc.returncode == 0:
                return proc
            err = (proc.stderr or b"").decode("utf-8", "replace").strip().replace("\n", " ")
            last = f"{flag} exit={proc.returncode} {err[:240]}"
    raise RuntimeError(f"Chrome 执行失败: {last}")


def measure_page_height(chrome: str, html_path: Path, timeout: int) -> int | None:
    """用 --dump-dom 读回测高脚本写进 <title> 的整页高度（方式二用）。"""
    proc = run_chrome(
        chrome,
        ["--dump-dom", "--virtual-time-budget=8000", html_path.as_uri()],
        timeout,
    )
    dom = (proc.stdout or b"").decode("utf-8", "replace")
    m = re.search(r"<title>H=(\d+)</title>", dom) or re.search(r"H=(\d+)", dom)
    return int(m.group(1)) if m else None


def measure_page_size(chrome: str, html_path: Path, timeout: int,
                      width: int) -> tuple[int | None, int | None, int | None]:
    """用 --dump-dom 读回 (内容右边界, body 左边界, 整页高度)（方式三用）。

    必须把 width 通过 --window-size 传进去：headless 默认窗口是 800x600，
    宽度不一致会让"量出来的高度"和"截图用的宽度"对不上（折行不同）。
    """
    proc = run_chrome(
        chrome,
        [f"--window-size={int(width)},2000", "--dump-dom",
         "--virtual-time-budget=8000", html_path.as_uri()],
        timeout,
    )
    dom = (proc.stdout or b"").decode("utf-8", "replace")
    m = re.search(r"<title>W=(\d+);B=(-?\d+);H=(\d+)</title>", dom)
    if m:
        return int(m.group(1)), int(m.group(2)), int(m.group(3))
    m = re.search(r"H=(\d+)", dom)                  # 兜底：只读到高度
    return None, None, (int(m.group(1)) if m else None)


def take_screenshot(chrome: str, html_path: Path, out_png: Path,
                    width: int, height: int, timeout: int, dsf: float = 1.0) -> None:
    args = [
        f"--screenshot={out_png}",
        f"--window-size={width},{height}",
        "--default-background-color=FFFFFFFF",
        "--virtual-time-budget=10000",
        html_path.as_uri(),
    ]
    if dsf != 1.0:
        args.insert(2, f"--force-device-scale-factor={dsf}")
    run_chrome(chrome, args, timeout)
    if not out_png.exists() or out_png.stat().st_size == 0:
        raise RuntimeError("Chrome 没有产出截图文件")


def print_to_pdf(chrome: str, html_path: Path, out_pdf: Path, timeout: int) -> None:
    """page 模式：用 Chrome 把本地 html 打印成 PDF（分页由排版引擎完成）。

    页面尺寸由注入的 @page 规则决定，这里不传 --window-size。
    """
    args = [
        f"--print-to-pdf={out_pdf}",
        "--no-pdf-header-footer",          # 不要页眉页脚（URL/日期/页码）
        "--virtual-time-budget=10000",     # 等懒加载图片 / 字体
        html_path.as_uri(),
    ]
    run_chrome(chrome, args, timeout)
    if not out_pdf.exists() or out_pdf.stat().st_size == 0:
        raise RuntimeError("Chrome 没有产出 PDF")


def build_shell_css(font_family: str, font_size: int, line_height: float,
                    background: str, content_width: int) -> str:
    """外壳样式（正文套壳渲染用），改字体 / 底色就是改这里。"""
    return (
        f'body {{ font-family: {font_family}; font-size: {font_size}px; '
        f'line-height: {line_height}; background: {background}; '
        f'margin: 20px auto; width: {content_width}px; }}\n'
        # 外壳宽度是写死的，防止页内大图 / 宽表格撑破画布
        "img, video, canvas, table { max-width: 100% !important; height: auto !important; }\n"
        "* { animation: none !important; transition: none !important; }\n"
    )


def wrap_shell(content_html: str, base_url: str, css: str, measure_js: str | None = None) -> str:
    """把正文片段套进固定外壳。measure_js 不为 None 时注入测高/测宽脚本。"""
    head = [
        '<meta charset="utf-8">',
        f'<base href="{base_url}">',          # 本地临时文件靠 base 找回相对资源
        f"<style>{css}</style>",
    ]
    if measure_js:
        head.append(f"<script>{measure_js}</script>")
    return (
        "<html><head>" + "".join(head) + "</head><body>"
        + content_html + "</body></html>"
    )
