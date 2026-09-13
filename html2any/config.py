# -*- coding: utf-8 -*-
"""运行配置：读 .env（可选），提供全局默认值。CLI 参数优先于这里的值。"""
from __future__ import annotations

import os
from pathlib import Path

try:
    from dotenv import load_dotenv

    # 依次找：当前工作目录的 .env -> 仓库根目录的 .env
    for _cand in (Path.cwd() / ".env", Path(__file__).resolve().parent.parent / ".env"):
        if _cand.exists():
            load_dotenv(_cand)
            break
except ImportError:          # 没装 python-dotenv 也能跑，全部走默认值
    pass

OUTPUT_DIR = os.getenv("OUTPUT_DIR", "outputs")
CHROME_PATH = os.getenv("CHROME_PATH") or None
HTTP_TIMEOUT = int(os.getenv("HTTP_TIMEOUT", "30"))
HTTP_RETRIES = int(os.getenv("HTTP_RETRIES", "3"))
