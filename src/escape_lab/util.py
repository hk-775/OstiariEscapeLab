"""Small dependency-free helpers used across Escape Lab."""

from __future__ import annotations

import dataclasses
import hashlib
import json
import os
import re
import tempfile
from collections.abc import Mapping
from copy import deepcopy
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any

_SENSITIVE_KEY = re.compile(
    r"(authorization|credential|password|private[_-]?key|secret|token)",
    re.IGNORECASE,
)


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def utc_now_iso() -> str:
    return utc_now().isoformat()


def to_primitive(value: Any) -> Any:
    if dataclasses.is_dataclass(value):
        return {
            field.name: to_primitive(getattr(value, field.name))
            for field in dataclasses.fields(value)
        }
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, Mapping):
        return {str(key): to_primitive(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set, frozenset)):
        return [to_primitive(item) for item in value]
    return value


def canonical_json(value: Any) -> str:
    return json.dumps(
        to_primitive(value),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha256_text(value: str) -> str:
    return sha256_bytes(value.encode("utf-8"))


def sha256_json(value: Any) -> str:
    return sha256_text(canonical_json(value))


def read_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def atomic_write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.",
        dir=str(path.parent),
        text=True,
    )
    temporary_path = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_path, path)
    finally:
        if temporary_path.exists():
            temporary_path.unlink()


def write_json(path: Path, value: Any) -> None:
    content = json.dumps(to_primitive(value), indent=2, sort_keys=True, ensure_ascii=False)
    atomic_write_text(path, f"{content}\n")


def deep_merge(base: Mapping[str, Any], overlay: Mapping[str, Any]) -> dict[str, Any]:
    """Recursively merge mappings while replacing lists and scalar values."""

    result: dict[str, Any] = deepcopy(dict(base))
    for key, value in overlay.items():
        if isinstance(value, Mapping) and isinstance(result.get(key), Mapping):
            result[key] = deep_merge(result[key], value)
        else:
            result[key] = deepcopy(value)
    return result


def get_path(value: Any, dotted_path: str, default: Any = None) -> Any:
    current = value
    parts = dotted_path.split(".")
    index = 0
    while index < len(parts):
        part = parts[index]
        if isinstance(current, Mapping):
            matched = False
            for end in range(len(parts), index, -1):
                candidate = ".".join(parts[index:end])
                if candidate in current:
                    current = current[candidate]
                    index = end
                    matched = True
                    break
            if not matched:
                return default
            continue
        if isinstance(current, list) and part.isdigit() and int(part) < len(current):
            current = current[int(part)]
            index += 1
            continue
        return default
    return current


def set_path(value: dict[str, Any], dotted_path: str, item: Any) -> None:
    parts = dotted_path.split(".")
    current = value
    for part in parts[:-1]:
        existing = current.get(part)
        if not isinstance(existing, dict):
            existing = {}
            current[part] = existing
        current = existing
    current[parts[-1]] = item


def resolve_refs(value: Any, variables: Mapping[str, Any]) -> Any:
    """Resolve ``{"$ref": "name.path"}`` values from prior action results."""

    if isinstance(value, Mapping):
        if set(value) == {"$ref"}:
            reference = str(value["$ref"])
            variable_name, _, nested_path = reference.partition(".")
            if variable_name not in variables:
                raise KeyError(f"Unknown scenario variable: {variable_name}")
            resolved = deepcopy(variables[variable_name])
            if nested_path:
                missing = object()
                resolved = get_path(resolved, nested_path, missing)
                if resolved is missing:
                    raise KeyError(f"Unknown scenario variable path: {reference}")
            return resolved
        return {str(key): resolve_refs(item, variables) for key, item in value.items()}
    if isinstance(value, list):
        return [resolve_refs(item, variables) for item in value]
    return deepcopy(value)


def extract_labels(value: Any) -> set[str]:
    labels: set[str] = set()
    if isinstance(value, Mapping):
        raw_labels = value.get("labels")
        if isinstance(raw_labels, list):
            labels.update(str(label) for label in raw_labels)
        for item in value.values():
            labels.update(extract_labels(item))
    elif isinstance(value, list):
        for item in value:
            labels.update(extract_labels(item))
    return labels


def unwrap_labeled_value(value: Any) -> Any:
    if isinstance(value, Mapping) and "value" in value and "labels" in value:
        return unwrap_labeled_value(value["value"])
    if isinstance(value, Mapping):
        return {str(key): unwrap_labeled_value(item) for key, item in value.items()}
    if isinstance(value, list):
        return [unwrap_labeled_value(item) for item in value]
    return value


def redact(value: Any) -> Any:
    """Redact likely credentials while retaining useful evidence structure."""

    if isinstance(value, Mapping):
        result: dict[str, Any] = {}
        for key, item in value.items():
            if _SENSITIVE_KEY.search(str(key)):
                result[str(key)] = {
                    "redacted": True,
                    "sha256": sha256_json(item),
                }
            else:
                result[str(key)] = redact(item)
        return result
    if isinstance(value, list):
        return [redact(item) for item in value]
    return value


def is_within(child: Path, parent: Path) -> bool:
    try:
        child.resolve().relative_to(parent.resolve())
        return True
    except ValueError:
        return False


def slugify(value: str) -> str:
    cleaned = re.sub(r"[^a-zA-Z0-9._-]+", "-", value.strip())
    return cleaned.strip("-").lower() or "item"
