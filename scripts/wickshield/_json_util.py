"""将 Decimal 等类型转为 Flask jsonify 可序列化结构。"""
from __future__ import annotations

from decimal import Decimal
from typing import Any


def json_safe(obj: Any) -> Any:
    if isinstance(obj, Decimal):
        return float(obj)
    if isinstance(obj, dict):
        return {str(k): json_safe(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [json_safe(v) for v in obj]
    if isinstance(obj, (int, float, str, bool)) or obj is None:
        return obj
    return str(obj)
