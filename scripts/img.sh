#!/usr/bin/env bash
# 只跑 images 方式（在仓库根目录执行，参数原样透传给 main.py）：sh scripts/img.sh <url>
python main.py "$@" --modes images
