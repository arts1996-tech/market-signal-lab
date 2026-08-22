"""Tamper-evident chain for formal forward-account JSON audit exports.

PostgreSQL remains the source of truth.  This module adds a local append-only
index so a verifier can detect content changes, missing files, reordered or
removed chain records, and exports that were never registered.
"""

from __future__ import annotations

from contextlib import contextmanager
from hashlib import sha256
import fcntl
import json
import os
from pathlib import Path
from typing import Any, Iterable

from app.analysis.decision_tracks import DECISION_TRACKS


AUDIT_CHAIN_VERSION = "forward-audit-chain-v1"
AUDIT_DIRECTORY = "_audit"
AUDIT_CHAIN_FILE = "forward-account-chain.jsonl"
AUDIT_HEAD_FILE = "forward-account-chain-head.json"
AUDIT_LOCK_FILE = ".forward-account-chain.lock"


class AuditIntegrityError(RuntimeError):
    """Raised when a write would extend a damaged or ambiguous audit chain."""


def _canonical_bytes(payload: dict[str, Any]) -> bytes:
    return json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _payload_hash(payload: dict[str, Any]) -> str:
    return sha256(_canonical_bytes(payload)).hexdigest()


def file_sha256(path: str | Path) -> str:
    digest = sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _paths(output_dir: str | Path) -> tuple[Path, Path, Path, Path]:
    root = Path(output_dir)
    audit_dir = root / AUDIT_DIRECTORY
    return (
        root,
        audit_dir / AUDIT_CHAIN_FILE,
        audit_dir / AUDIT_HEAD_FILE,
        audit_dir / AUDIT_LOCK_FILE,
    )


def _formal_relative_path(root: Path, path: str | Path) -> Path:
    root_resolved = root.resolve()
    target = Path(path).resolve()
    try:
        relative = target.relative_to(root_resolved)
    except ValueError as exc:
        raise ValueError("audit export must be inside output_dir") from exc
    if len(relative.parts) != 3:
        raise ValueError("formal audit export path must be account/track/YYYY-MM-DD.json")
    if relative.parts[1] not in DECISION_TRACKS or relative.suffix != ".json":
        raise ValueError("unsupported formal audit export path")
    return relative


def discover_formal_exports(output_dir: str | Path) -> list[Path]:
    root = Path(output_dir)
    discovered: list[Path] = []
    if not root.exists():
        return discovered
    for path in root.glob("*/*/*.json"):
        try:
            _formal_relative_path(root, path)
        except ValueError:
            continue
        discovered.append(path)
    return sorted(discovered, key=lambda value: value.relative_to(root).as_posix())


@contextmanager
def _chain_lock(lock_path: Path):
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open("a+", encoding="utf-8") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def _read_records(chain_path: Path) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    if not chain_path.exists():
        return [], []
    records: list[dict[str, Any]] = []
    anomalies: list[dict[str, Any]] = []
    try:
        lines = chain_path.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        return [], [{"category": "chain_unreadable", "detail": type(exc).__name__}]
    for line_number, line in enumerate(lines, start=1):
        if not line.strip():
            anomalies.append({"category": "empty_chain_record", "line": line_number})
            continue
        try:
            value = json.loads(line)
        except (json.JSONDecodeError, UnicodeDecodeError):
            anomalies.append({"category": "invalid_chain_json", "line": line_number})
            continue
        if not isinstance(value, dict):
            anomalies.append({"category": "invalid_chain_record", "line": line_number})
            continue
        records.append(value)
    return records, anomalies


def _read_head(head_path: Path) -> tuple[dict[str, Any] | None, list[dict[str, Any]]]:
    if not head_path.exists():
        return None, []
    try:
        value = json.loads(head_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeDecodeError) as exc:
        return None, [{"category": "invalid_chain_head", "detail": type(exc).__name__}]
    if not isinstance(value, dict):
        return None, [{"category": "invalid_chain_head"}]
    return value, []


def _expected_head(records: list[dict[str, Any]]) -> dict[str, Any]:
    base = {
        "chain_version": AUDIT_CHAIN_VERSION,
        "record_count": len(records),
        "last_sequence": records[-1]["sequence"] if records else 0,
        "last_record_hash": records[-1]["record_hash"] if records else None,
    }
    return {**base, "head_hash": _payload_hash(base)}


def _write_head(head_path: Path, records: list[dict[str, Any]]) -> None:
    payload = _expected_head(records)
    temporary = head_path.with_suffix(".tmp")
    with temporary.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, sort_keys=True, indent=2)
        handle.flush()
        os.fsync(handle.fileno())
    temporary.replace(head_path)


def _verify_unlocked(
    output_dir: str | Path,
    *,
    include_untracked: bool = True,
) -> dict[str, Any]:
    root, chain_path, head_path, _ = _paths(output_dir)
    records, anomalies = _read_records(chain_path)
    head, head_anomalies = _read_head(head_path)
    anomalies.extend(head_anomalies)
    previous_hash: str | None = None
    tracked_paths: set[str] = set()
    checked_files = 0

    for index, record in enumerate(records, start=1):
        sequence = record.get("sequence")
        relative_text = record.get("path")
        if record.get("chain_version") != AUDIT_CHAIN_VERSION:
            anomalies.append({"category": "chain_version_mismatch", "sequence": sequence})
        if sequence != index:
            anomalies.append(
                {
                    "category": "sequence_gap_or_reorder",
                    "sequence": sequence,
                    "expected_sequence": index,
                }
            )
        if record.get("previous_record_hash") != previous_hash:
            anomalies.append({"category": "previous_hash_mismatch", "sequence": sequence})
        supplied_record_hash = record.get("record_hash")
        unsigned = {key: value for key, value in record.items() if key != "record_hash"}
        if supplied_record_hash != _payload_hash(unsigned):
            anomalies.append({"category": "record_hash_mismatch", "sequence": sequence})
        if not isinstance(relative_text, str) or not relative_text:
            anomalies.append({"category": "record_path_invalid", "sequence": sequence})
        elif relative_text in tracked_paths:
            anomalies.append(
                {"category": "duplicate_record_path", "sequence": sequence, "path": relative_text}
            )
        else:
            tracked_paths.add(relative_text)
            path = root / relative_text
            if not path.exists():
                anomalies.append(
                    {"category": "tracked_file_missing", "sequence": sequence, "path": relative_text}
                )
            else:
                try:
                    actual_hash = file_sha256(path)
                    checked_files += 1
                except OSError as exc:
                    anomalies.append(
                        {
                            "category": "tracked_file_unreadable",
                            "sequence": sequence,
                            "path": relative_text,
                            "detail": type(exc).__name__,
                        }
                    )
                else:
                    if actual_hash != record.get("file_sha256"):
                        anomalies.append(
                            {
                                "category": "file_hash_mismatch",
                                "sequence": sequence,
                                "path": relative_text,
                            }
                        )
                    if path.stat().st_size != record.get("file_size_bytes"):
                        anomalies.append(
                            {
                                "category": "file_size_mismatch",
                                "sequence": sequence,
                                "path": relative_text,
                            }
                        )
        previous_hash = supplied_record_hash if isinstance(supplied_record_hash, str) else None

    if records:
        expected_head = _expected_head(records)
        if head is None and not head_anomalies:
            anomalies.append({"category": "chain_head_missing"})
        elif head is not None and head != expected_head:
            anomalies.append({"category": "chain_head_mismatch"})
    elif head is not None:
        anomalies.append({"category": "orphaned_chain_head"})

    untracked_paths: list[str] = []
    if include_untracked:
        for path in discover_formal_exports(root):
            relative = path.relative_to(root).as_posix()
            if relative not in tracked_paths:
                untracked_paths.append(relative)
                anomalies.append({"category": "untracked_export", "path": relative})

    status = "invalid" if anomalies else "ok" if records else "empty"
    return {
        "chain_version": AUDIT_CHAIN_VERSION,
        "status": status,
        "record_count": len(records),
        "checked_file_count": checked_files,
        "untracked_file_count": len(untracked_paths),
        "last_sequence": records[-1].get("sequence") if records else 0,
        "last_record_hash": records[-1].get("record_hash") if records else None,
        "chain_path": str(chain_path),
        "head_path": str(head_path),
        "anomaly_count": len(anomalies),
        "anomalies": anomalies,
    }


def verify_audit_chain(output_dir: str | Path) -> dict[str, Any]:
    """Verify the complete chain and every formal export without changing state."""

    _, _, _, lock_path = _paths(output_dir)
    with _chain_lock(lock_path):
        return _verify_unlocked(output_dir)


def record_audit_export(output_dir: str | Path, export_path: str | Path) -> dict[str, Any]:
    """Append one file to the chain, or return its identical existing record."""

    root, chain_path, head_path, lock_path = _paths(output_dir)
    path = Path(export_path)
    relative = _formal_relative_path(root, path)
    if not path.exists():
        raise FileNotFoundError(path)
    relative_text = relative.as_posix()
    with _chain_lock(lock_path):
        current = _verify_unlocked(root, include_untracked=False)
        if current["status"] == "invalid":
            raise AuditIntegrityError(
                f"audit chain is invalid; verification required before append: {current['anomaly_count']}"
            )
        records, parse_anomalies = _read_records(chain_path)
        if parse_anomalies:
            raise AuditIntegrityError("audit chain cannot be parsed")
        current_hash = file_sha256(path)
        for record in records:
            if record.get("path") != relative_text:
                continue
            if (
                record.get("file_sha256") == current_hash
                and record.get("file_size_bytes") == path.stat().st_size
            ):
                return record
            raise AuditIntegrityError(f"tracked audit export changed: {relative_text}")

        sequence = len(records) + 1
        base = {
            "chain_version": AUDIT_CHAIN_VERSION,
            "sequence": sequence,
            "previous_record_hash": records[-1]["record_hash"] if records else None,
            "path": relative_text,
            "account_name": relative.parts[0],
            "decision_track": relative.parts[1],
            "session_date": relative.stem,
            "file_sha256": current_hash,
            "file_size_bytes": path.stat().st_size,
        }
        record = {**base, "record_hash": _payload_hash(base)}
        chain_path.parent.mkdir(parents=True, exist_ok=True)
        with chain_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        records.append(record)
        _write_head(head_path, records)
        return record


def initialize_audit_chain(
    output_dir: str | Path,
    export_paths: Iterable[str | Path],
) -> dict[str, Any]:
    """Register a caller-validated baseline in stable path order."""

    root = Path(output_dir)
    paths = sorted(
        (Path(path) for path in export_paths),
        key=lambda value: _formal_relative_path(root, value).as_posix(),
    )
    for path in paths:
        record_audit_export(root, path)
    return verify_audit_chain(root)
