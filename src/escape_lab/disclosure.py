"""Append-only post-run disclosure status ledger."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from escape_lab.util import sha256_json, utc_now_iso

DISCLOSURE_STATUSES = {
    "private",
    "acknowledged",
    "remediated",
    "coordinated",
    "publishable",
}


def append_disclosure_status(
    run_dir: Path,
    *,
    status: str,
    note: str,
    actor: str = "lab-owner",
) -> dict[str, Any]:
    normalized = status.lower()
    if normalized not in DISCLOSURE_STATUSES:
        raise ValueError(
            f"Unknown disclosure status {status!r}; expected one of "
            f"{', '.join(sorted(DISCLOSURE_STATUSES))}"
        )
    if not (run_dir / "result.json").exists():
        raise ValueError(f"Not an Escape Lab run directory: {run_dir}")
    ledger = run_dir / "disclosure.jsonl"
    previous_hash = "0" * 64
    sequence = 1
    if ledger.exists():
        with ledger.open("r", encoding="utf-8") as handle:
            records = [json.loads(line) for line in handle if line.strip()]
        if records:
            previous_hash = str(records[-1]["record_hash"])
            sequence = int(records[-1]["sequence"]) + 1

    record = {
        "sequence": sequence,
        "timestamp": utc_now_iso(),
        "status": normalized,
        "note": note,
        "actor": actor,
        "previous_hash": previous_hash,
    }
    record["record_hash"] = sha256_json(record)
    with ledger.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, sort_keys=True, ensure_ascii=False) + "\n")
        handle.flush()
        os.fsync(handle.fileno())
    return record


def verify_disclosure_ledger(path: Path) -> dict[str, Any]:
    previous_hash = "0" * 64
    count = 0
    errors: list[str] = []
    if not path.exists():
        return {"valid": True, "records": 0, "errors": []}
    with path.open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            count += 1
            record = json.loads(line)
            stored_hash = record.pop("record_hash", None)
            if record.get("previous_hash") != previous_hash:
                errors.append(f"line {line_number}: previous hash mismatch")
            if stored_hash != sha256_json(record):
                errors.append(f"line {line_number}: record hash mismatch")
            previous_hash = str(stored_hash or "")
    return {
        "valid": not errors,
        "records": count,
        "final_hash": previous_hash,
        "errors": errors,
    }

