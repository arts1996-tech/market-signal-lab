"""Cross-container process locks for scheduled jobs on one execution host."""

from __future__ import annotations

from contextlib import ExitStack, contextmanager
from datetime import UTC, datetime
from functools import wraps
import fcntl
import json
import os
from pathlib import Path
import re
import socket
import sys
from typing import Callable, Iterator, TypeVar


STANDARD_FORWARD_LOCK = "forward-standard"
SELECTED_FORWARD_LOCK = "forward-selected"
HEAVY_ANALYSIS_LOCK = "analysis-heavy"
TEMPORARY_FAILURE_EXIT_CODE = 75
_LOCK_NAME = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")
_Result = TypeVar("_Result")


class JobAlreadyRunning(RuntimeError):
    """Raised when another process on the same host owns a named job lock."""

    def __init__(self, lock_name: str, owner: dict | None = None):
        super().__init__(f"job lock is already held: {lock_name}")
        self.lock_name = lock_name
        self.owner = owner or {}


def _lock_directory() -> Path:
    return Path(os.environ.get("JOB_LOCK_DIR", "data/job_locks"))


def _read_owner(handle) -> dict:
    try:
        handle.seek(0)
        value = json.loads(handle.read() or "{}")
    except (OSError, ValueError, TypeError):
        return {}
    return value if isinstance(value, dict) else {}


def _write_owner(handle, payload: dict) -> None:
    handle.seek(0)
    handle.truncate()
    json.dump(payload, handle, ensure_ascii=True, sort_keys=True)
    handle.flush()


@contextmanager
def exclusive_job_lock(lock_name: str) -> Iterator[dict]:
    """Acquire a non-blocking host-local lock shared by Docker bind mounts."""

    if not _LOCK_NAME.fullmatch(lock_name):
        raise ValueError("job lock name must use lowercase letters, digits, '-' or '_'")
    directory = _lock_directory()
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{lock_name}.lock"
    with path.open("a+", encoding="utf-8") as handle:
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise JobAlreadyRunning(lock_name, _read_owner(handle)) from error
        owner = {
            "lock_name": lock_name,
            "pid": os.getpid(),
            "host": socket.gethostname(),
            "started_at": datetime.now(UTC).isoformat(),
            "status": "running",
        }
        _write_owner(handle, owner)
        try:
            yield owner
        finally:
            _write_owner(
                handle,
                {
                    **owner,
                    "finished_at": datetime.now(UTC).isoformat(),
                    "status": "released",
                },
            )
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def prevent_concurrent_runs(*lock_names: str) -> Callable:
    """Reject overlapping scheduled runs with a retryable exit code."""

    if not lock_names:
        raise ValueError("at least one job lock name is required")

    def decorator(function: Callable[..., _Result]) -> Callable[..., _Result]:
        @wraps(function)
        def wrapped(*args, **kwargs):
            try:
                with ExitStack() as stack:
                    for lock_name in sorted(set(lock_names)):
                        stack.enter_context(exclusive_job_lock(lock_name))
                    return function(*args, **kwargs)
            except JobAlreadyRunning as error:
                owner = error.owner
                owner_summary = {
                    key: owner.get(key)
                    for key in ("pid", "host", "started_at")
                    if owner.get(key) is not None
                }
                print(
                    f"Job skipped because {error.lock_name} is already running: "
                    f"{json.dumps(owner_summary, sort_keys=True)}",
                    file=sys.stderr,
                )
                raise SystemExit(TEMPORARY_FAILURE_EXIT_CODE) from error

        return wrapped

    return decorator
