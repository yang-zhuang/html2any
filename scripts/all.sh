#!/usr/bin/env bash
# 三种方式全跑（在仓库根目录执行，参数原样透传给 main.py）：sh scripts/all.sh <url>
python main.py "$@" --modes markdown,images,structure
