"""Append-only, hash-chained evidence storage."""

from __future__ import annotations

import json
import os
from collections import Counter
from pathlib import Path
from typing import Any

from escape_lab.util import redact, sha256_json, to_primitive, utc_now_iso, write_json


class EvidenceStore:
    """Write ordered events outside the synthetic agent's administrative boundary."""

    def __init__(self, artifact_dir: Path, run_id: str) -> None:
        self.artifact_dir = artifact_dir
        self.run_id = run_id
        self.events_path = artifact_dir / "events.jsonl"
        self.snapshots_dir = artifact_dir / "snapshots"
        self.artifact_dir.mkdir(parents=True, exist_ok=False)
        self.snapshots_dir.mkdir(parents=True, exist_ok=False)
        self._handle = self.events_path.open("a", encoding="utf-8")
        self._sequence = 0
        self._previous_hash = "0" * 64
        self._counts: Counter[str] = Counter()
        self._closed = False

    @property
    def sequence(self) -> int:
        return self._sequence

    @property
    def counts(self) -> dict[str, int]:
        return dict(self._counts)

    @property
    def final_hash(self) -> str:
        return self._previous_hash

    def record(self, event_type: str, data: dict[str, Any] | None = None) -> str:
        if self._closed:
            raise RuntimeError("Evidence store is closed")
        self._sequence += 1
        event = {
            "event_id": f"{self.run_id}:{self._sequence:06d}",
            "sequence": self._sequence,
            "timestamp": utc_now_iso(),
            "run_id": self.run_id,
            "event_type": event_type,
            "data": redact(to_primitive(data or {})),
            "previous_hash": self._previous_hash,
        }
        record_hash = sha256_json(event)
        event["record_hash"] = record_hash
        self._handle.write(json.dumps(event, sort_keys=True, ensure_ascii=False) + "\n")
        self._handle.flush()
        os.fsync(self._handle.fileno())
        self._previous_hash = record_hash
        self._counts[event_type] += 1
        return str(event["event_id"])

    def snapshot(self, name: str, state: dict[str, Any]) -> Path:
        path = self.snapshots_dir / f"{name}.json"
        write_json(
            path,
            {
                "run_id": self.run_id,
                "name": name,
                "captured_at": utc_now_iso(),
                "state": state,
                "state_digest": sha256_json(state),
            },
        )
        self.record(
            "state_snapshot",
            {
                "name": name,
                "path": str(path.relative_to(self.artifact_dir)),
                "state_digest": sha256_json(state),
            },
        )
        return path

    def flush(self) -> None:
        if not self._closed:
            self._handle.flush()
            os.fsync(self._handle.fileno())

    def close(self) -> None:
        if self._closed:
            return
        self.flush()
        self._handle.close()
        self._closed = True

    def verify(self) -> dict[str, Any]:
        self.flush()
        previous_hash = "0" * 64
        errors: list[str] = []
        count = 0
        try:
            with self.events_path.open("r", encoding="utf-8") as handle:
                for line_number, line in enumerate(handle, start=1):
                    count += 1
                    try:
                        event = json.loads(line)
                    except json.JSONDecodeError as error:
                        errors.append(f"line {line_number}: invalid JSON: {error}")
                        continue
                    stored_hash = event.pop("record_hash", None)
                    expected_hash = sha256_json(event)
                    if event.get("previous_hash") != previous_hash:
                        errors.append(f"line {line_number}: previous hash mismatch")
                    if stored_hash != expected_hash:
                        errors.append(f"line {line_number}: record hash mismatch")
                    previous_hash = str(stored_hash or "")
        except OSError as error:
            errors.append(f"cannot read evidence: {error}")

        return {
            "valid": not errors,
            "algorithm": "sha256-chain-v1",
            "event_count": count,
            "final_hash": previous_hash,
            "errors": errors,
        }

    def write_integrity_record(self, integrity: dict[str, Any]) -> Path:
        path = self.artifact_dir / "integrity.json"
        write_json(path, integrity)
        return path


def verify_evidence(events_path: Path) -> dict[str, Any]:
    """Verify an existing event stream without opening it for writes."""

    previous_hash = "0" * 64
    errors: list[str] = []
    count = 0
    with events_path.open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            count += 1
            try:
                event = json.loads(line)
            except json.JSONDecodeError as error:
                errors.append(f"line {line_number}: invalid JSON: {error}")
                continue
            stored_hash = event.pop("record_hash", None)
            expected_hash = sha256_json(event)
            if event.get("previous_hash") != previous_hash:
                errors.append(f"line {line_number}: previous hash mismatch")
            if stored_hash != expected_hash:
                errors.append(f"line {line_number}: record hash mismatch")
            previous_hash = str(stored_hash or "")
    return {
        "valid": not errors,
        "algorithm": "sha256-chain-v1",
        "event_count": count,
        "final_hash": previous_hash,
        "errors": errors,
    }
