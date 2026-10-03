"""PDF-Enhance 配置加载与路径解析。"""

import json
import os
from pathlib import Path
from typing import Any, Dict, Optional

_DEFAULT_CONFIG: Dict[str, Any] = {
    "general": {
        "dpi": 200,
        "max_ocr_workers": 4,
    },
    "ai_toc": {
        "enabled": False,
        "base_url": "https://api.openai.com/v1",
        "api_key": "",
        "model": "gpt-4o-mini",
    },
}


def find_config_path() -> Optional[Path]:
    """按环境变量、当前目录、项目目录和用户配置目录查找配置文件。"""
    candidates = []
    env_path = os.getenv("PDF_ENHANCE_CONFIG", "").strip()
    if env_path:
        candidates.append(Path(env_path).expanduser())
    candidates.extend([
        Path.cwd() / "config.json",
        Path(__file__).resolve().parents[1] / "config.json",
        Path.home() / ".config" / "pdf-enhance" / "config.json",
    ])
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    return None


def load_config() -> Dict[str, Any]:
    """加载配置并与安全默认值合并；格式错误时返回默认配置。"""
    config = {
        "general": dict(_DEFAULT_CONFIG["general"]),
        "ai_toc": dict(_DEFAULT_CONFIG["ai_toc"]),
    }
    path = find_config_path()
    if path:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            for section in ("general", "ai_toc"):
                if isinstance(data.get(section), dict):
                    config[section].update(data[section])
        except (OSError, json.JSONDecodeError):
            pass

    general = config["general"]
    try:
        general["dpi"] = max(96, min(int(general["dpi"]), 600))
    except (TypeError, ValueError):
        general["dpi"] = 200
    try:
        general["max_ocr_workers"] = max(1, int(general["max_ocr_workers"]))
    except (TypeError, ValueError):
        general["max_ocr_workers"] = 4

    ai = config["ai_toc"]
    ai["enabled"] = bool(ai.get("enabled", False))
    ai["base_url"] = os.getenv("AI_BASE_URL", str(ai.get("base_url", "")))
    ai["api_key"] = os.getenv("AI_API_KEY", str(ai.get("api_key", "")))
    ai["model"] = os.getenv("AI_MODEL", str(ai.get("model", "")))
    return config


def load_general_config() -> Dict[str, Any]:
    return load_config()["general"]


def load_ai_config() -> Dict[str, Any]:
    return load_config()["ai_toc"]
