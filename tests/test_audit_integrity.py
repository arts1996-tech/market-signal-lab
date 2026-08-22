import json

import pytest

from app.services.audit_integrity import (
    AUDIT_CHAIN_FILE,
    AUDIT_DIRECTORY,
    AuditIntegrityError,
    initialize_audit_chain,
    record_audit_export,
    verify_audit_chain,
)


def _export(tmp_path, account: str, day: str, content: str | None = None):
    path = tmp_path / account / "delayed_historical" / f"{day}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content or json.dumps({"account": account, "day": day}), encoding="utf-8")
    return path


def _chain_path(tmp_path):
    return tmp_path / AUDIT_DIRECTORY / AUDIT_CHAIN_FILE


def test_audit_chain_appends_sequence_and_is_idempotent(tmp_path):
    first = _export(tmp_path, "short_term", "2026-08-18")
    second = _export(tmp_path, "mid_term", "2026-08-18")

    first_record = record_audit_export(tmp_path, first)
    replay = record_audit_export(tmp_path, first)
    second_record = record_audit_export(tmp_path, second)
    result = verify_audit_chain(tmp_path)

    assert first_record == replay
    assert first_record["sequence"] == 1
    assert first_record["previous_record_hash"] is None
    assert second_record["sequence"] == 2
    assert second_record["previous_record_hash"] == first_record["record_hash"]
    assert result["status"] == "ok"
    assert result["record_count"] == result["checked_file_count"] == 2


def test_audit_chain_detects_manual_file_change_and_refuses_append(tmp_path):
    path = _export(tmp_path, "short_term", "2026-08-18", "original")
    record_audit_export(tmp_path, path)
    path.write_text("modified", encoding="utf-8")

    result = verify_audit_chain(tmp_path)

    assert result["status"] == "invalid"
    assert "file_hash_mismatch" in {item["category"] for item in result["anomalies"]}
    with pytest.raises(AuditIntegrityError, match="invalid"):
        record_audit_export(tmp_path, _export(tmp_path, "mid_term", "2026-08-18"))


def test_audit_chain_detects_deleted_export(tmp_path):
    path = _export(tmp_path, "short_term", "2026-08-18")
    record_audit_export(tmp_path, path)
    path.unlink()

    result = verify_audit_chain(tmp_path)

    assert result["status"] == "invalid"
    assert "tracked_file_missing" in {item["category"] for item in result["anomalies"]}


def test_audit_chain_detects_removed_middle_record_and_sequence_gap(tmp_path):
    paths = [
        _export(tmp_path, "short_term", "2026-08-18"),
        _export(tmp_path, "mid_term", "2026-08-18"),
        _export(tmp_path, "short_term", "2026-08-19"),
    ]
    initialize_audit_chain(tmp_path, paths)
    lines = _chain_path(tmp_path).read_text(encoding="utf-8").splitlines()
    _chain_path(tmp_path).write_text("\n".join((lines[0], lines[2])) + "\n", encoding="utf-8")

    result = verify_audit_chain(tmp_path)
    categories = {item["category"] for item in result["anomalies"]}

    assert result["status"] == "invalid"
    assert "sequence_gap_or_reorder" in categories
    assert "previous_hash_mismatch" in categories
    assert "chain_head_mismatch" in categories


def test_audit_chain_head_detects_tail_truncation(tmp_path):
    paths = [
        _export(tmp_path, "short_term", "2026-08-18"),
        _export(tmp_path, "mid_term", "2026-08-18"),
    ]
    initialize_audit_chain(tmp_path, paths)
    first_line = _chain_path(tmp_path).read_text(encoding="utf-8").splitlines()[0]
    _chain_path(tmp_path).write_text(first_line + "\n", encoding="utf-8")

    result = verify_audit_chain(tmp_path)

    assert result["status"] == "invalid"
    assert "chain_head_mismatch" in {item["category"] for item in result["anomalies"]}


def test_audit_chain_detects_export_without_chain_record(tmp_path):
    _export(tmp_path, "short_term", "2026-08-18")

    result = verify_audit_chain(tmp_path)

    assert result["status"] == "invalid"
    assert result["untracked_file_count"] == 1
    assert result["anomalies"] == [
        {
            "category": "untracked_export",
            "path": "short_term/delayed_historical/2026-08-18.json",
        }
    ]


def test_empty_audit_directory_is_valid_without_claiming_records(tmp_path):
    result = verify_audit_chain(tmp_path)

    assert result["status"] == "empty"
    assert result["record_count"] == 0
    assert result["anomaly_count"] == 0
