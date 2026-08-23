"""Access immutable resources included in an installed Escape Lab wheel."""

from __future__ import annotations

import json
from importlib.resources import files
from typing import Any

_PACKAGE = "escape_lab.data"


def resource_text(relative_path: str) -> str:
    resource = files(_PACKAGE)
    for part in relative_path.split("/"):
        resource = resource.joinpath(part)
    return resource.read_text(encoding="utf-8")


def resource_json(relative_path: str) -> Any:
    return json.loads(resource_text(relative_path))
