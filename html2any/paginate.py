# -*- coding: utf-8 -*-
"""四向裁白 + 空白行智能分页（方式二/三共用）。

三条核心行为：
1. 四向裁白：把四周大片空白裁掉，再补 PADDING_* 的边距。
2. 空白行智能分页（**不会切断内容**）：单张图超过 MAX_PAGE_HEIGHT=720 时要切，
   但不是按固定高度硬切，而是在目标切割线 ±SEARCH_MARGIN 的范围内寻找
   **空白行**（该行白像素占比 >= BLANK_ROW_RATIO）；优先取目标线下方最近的
   空白行，没有就取上方最近的；若该范围内没有一行是空白行，则退化为取
   "最干净"（白像素最多）的那一行。因此切割线总是落在文字行之间、图片之间，
   不会把一行字或一张图劈成两半。
3. SEARCH_EXPAND 扩展项：±SEARCH_MARGIN 内找不到空白行时，把范围翻倍再找，
   最多翻 SEARCH_EXPAND 次；设为 1 则只在初始范围内找。
"""
from __future__ import annotations

from PIL import Image

# 分页参数
PADDING_TOP = 15
PADDING_BOTTOM = 20
PADDING_LEFT = 10
PADDING_RIGHT = 10

MAX_PAGE_HEIGHT = 720      # 单张图片最大高度
SEARCH_MARGIN = 80         # 寻找切割线的上下搜索范围
WHITE_THRESHOLD = 250      # 灰度值阈值，>= 此值视为白色
BLANK_ROW_RATIO = 0.995    # 一行中白像素占比达到此值即视为空白行
SEARCH_EXPAND = 3          # 找不到空白行时范围翻倍再找的最大次数


def is_blank_row(gray_img, y: int, width: int, threshold: int, ratio: float) -> bool:
    """判断灰度图像的第 y 行是否为空白行（像素几乎全白）。

    用 PIL 的 histogram 计数，比逐像素 Python 循环快，结果一致。
    """
    row = gray_img.crop((0, y, width, y + 1))
    white_count = sum(row.histogram()[threshold:])
    return (white_count / width) >= ratio


def find_best_cut(gray_img, width: int, target_y: int, margin: int,
                  threshold: int, ratio: float, expand: int = 1) -> int:
    """在 target_y ± margin 范围内寻找最佳切割线（空白行）。

    - 优先选 >= target_y 且最近的空白行；
    - 否则选 target_y 上方最近的空白行（即 blank_rows 里最大的那个）；
    - 若范围内没有任何空白行，则退化为"最干净"的一行（白像素最多，
      并列时取离 target_y 更近的，下方优先）。

    expand > 1 时：找不到空白行就把搜索范围翻倍再找，最多翻 expand 次，
    仍找不到才退化。expand=1 则只在初始范围内找。
    """
    height = gray_img.height
    m = margin
    for _ in range(max(1, expand)):
        start = max(0, target_y - m)
        end = min(height - 1, target_y + m)

        blank_rows = [y for y in range(start, end + 1)
                      if is_blank_row(gray_img, y, width, threshold, ratio)]
        if blank_rows:
            after = [y for y in blank_rows if y >= target_y]
            if after:
                return min(after)
            return max(blank_rows)

        m *= 2
        if m > height:                      # 搜索范围已覆盖整图，没必要再翻倍
            break

    # 没有空白行，找最干净的行（白像素比例最高）
    start = max(0, target_y - m)
    end = min(height - 1, target_y + m)
    best_y = target_y
    max_ratio = -1.0
    for y in range(start, end + 1):
        row = gray_img.crop((0, y, width, y + 1))
        white_cnt = sum(row.histogram()[threshold:])
        r = white_cnt / width
        if r > max_ratio or (r == max_ratio and abs(y - target_y) < abs(best_y - target_y)):
            max_ratio = r
            best_y = y
    return best_y


def trim_border(img: Image.Image, threshold: int,
                pad_top: int, pad_bottom: int, pad_left: int, pad_right: int):
    """四向裁白。全白图返回 None。"""
    gray = img.convert("L")
    width, height = img.size

    # 用 getbbox 得到与逐像素扫描完全相同的"非白"包围盒，纯 C 实现快很多
    mask = gray.point(lambda v: 255 if v < threshold else 0)
    box = mask.getbbox()
    if box is None:                        # 全白
        return None

    left, top, right, bottom = box
    right -= 1
    bottom -= 1

    left = max(0, left - pad_left)
    right = min(width - 1, right + pad_right)
    top = max(0, top - pad_top)
    bottom = min(height - 1, bottom + pad_bottom)

    return img.crop((left, top, right + 1, bottom + 1))


def plan_pages(total_h: int, max_page_height: int) -> list[int]:
    """算出各页的目标切割线（最后一条为图片底部），供分页循环使用。"""
    targets = []
    start_y = 0
    while start_y < total_h:
        target_y = min(start_y + max_page_height, total_h - 1)
        targets.append(target_y)
        if target_y >= total_h - 1:
            break
        start_y = target_y
    return targets
