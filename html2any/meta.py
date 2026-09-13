# -*- coding: utf-8 -*-
"""运行元信息 meta.json 的读写（断点续传的依据）。"""
from __future__ import annotations

import json
from pathlib import Path


def load_meta(run_dir: Path) -> dict | None:
    """读 run 目录下的 meta.json；不存在或损坏返回 None。"""
    p = run_dir / "meta.json"
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None


def save_meta(run_dir: Path, meta: dict) -> None:
    """写 meta.json。每跑完一个方式就落盘一次，中途崩了也能续传。"""
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "meta.json").write_text(
        json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8"
    )
