from __future__ import annotations

import os
import sqlite3
import subprocess
from hashlib import sha256
from typing import TYPE_CHECKING

from pydantic import TypeAdapter

from ads_booster.transport.json_types import JsonObject
from tests.marketing.agent_service.installed_completion_report import write_json

if TYPE_CHECKING:
    from pathlib import Path

_ROWS = TypeAdapter(list[tuple[str]])
_COUNT = TypeAdapter(tuple[int])
_JSON: TypeAdapter[JsonObject] = TypeAdapter(JsonObject)

OLD_READER = """
import json,sqlite3,sys
from hashlib import sha256
from pathlib import Path
import ads_booster
from ads_booster.contracts.agent_run import AgentRun,AgentRecord,contract_sha256
with sqlite3.connect('file:'+sys.argv[1]+'?mode=ro', uri=True) as db:
    rows=db.execute('SELECT run_json FROM agent_runs ORDER BY run_id')
    runs=[AgentRun.model_validate_json(row[0]) for row in rows]
    rows=db.execute('SELECT record_json FROM agent_records ORDER BY rowid')
    records=[AgentRecord.model_validate_json(row[0]) for row in rows]
    print(json.dumps({'package':ads_booster.__file__,'read_only':True,
      'run_digests':[contract_sha256(run) for run in runs],
      'record_digests':[contract_sha256(record) for record in records]}))
"""


def rollback_check(root: Path, phase: str, baseline_python: Path | None) -> None:
    database = root / "restart-state" / "agent.sqlite3"
    with sqlite3.connect(f"file:{database}?mode=ro", uri=True) as connection:
        active = _COUNT.validate_python(
            connection.execute("SELECT COUNT(*) FROM agent_drive_work").fetchone()
        )[0]
        notification = _COUNT.validate_python(
            connection.execute(
                """SELECT COUNT(*) FROM slack_message_jobs
                WHERE notification_state IN ('pending','sending','unknown')"""
            ).fetchone()
        )[0]
    permitted = active == 0 and notification == 0
    if phase == "rollback-active":
        write_json(
            root / "rollback-active.json",
            {
                "pid": os.getpid(),
                "runnable": active,
                "unsettled_notifications": notification,
                "manual_old_reader_permitted": permitted,
            },
        )
        assert not permitted
        return
    assert permitted
    backup_root = root / "rollback-backup"
    backup_root.mkdir(exist_ok=False)
    target = backup_root / "service.sqlite3"
    with (
        sqlite3.connect(f"file:{database}?mode=ro", uri=True) as source,
        sqlite3.connect(target) as copied,
    ):
        source.backup(copied)
        integrity = _ROWS.validate_python(copied.execute("PRAGMA integrity_check").fetchall())
        expected_runs = _ROWS.validate_python(
            copied.execute("SELECT run_json FROM agent_runs ORDER BY run_id").fetchall()
        )
        expected_records = _ROWS.validate_python(
            copied.execute("SELECT record_json FROM agent_records ORDER BY rowid").fetchall()
        )
    assert integrity == [("ok",)]
    from ads_booster.contracts.agent_run import (  # noqa: PLC0415
        AgentRecord,
        AgentRun,
        contract_sha256,
    )

    expected = {
        "run_digests": [
            contract_sha256(AgentRun.model_validate_json(row[0])) for row in expected_runs
        ],
        "record_digests": [
            contract_sha256(AgentRecord.model_validate_json(row[0])) for row in expected_records
        ],
    }
    before = sha256(target.read_bytes()).hexdigest()
    if baseline_python is not None:
        command = [str(baseline_python), "-I", "-c", OLD_READER, str(target)]
        result = subprocess.run(command, text=True, capture_output=True, timeout=30, check=False)  # noqa: S603 - supplied isolated baseline interpreter, fixed read-only program.
        _ = (root / "rollback-old-reader.stdout.txt").write_text(result.stdout)
        _ = (root / "rollback-old-reader.stderr.txt").write_text(result.stderr)
        assert result.returncode == 0
        observed = _JSON.validate_json(result.stdout)
        assert observed["run_digests"] == expected["run_digests"]
        assert observed["record_digests"] == expected["record_digests"]
    assert before == sha256(target.read_bytes()).hexdigest()
    write_json(
        root / "rollback-safe.json",
        {
            "pid": os.getpid(),
            "runnable": active,
            "unsettled_notifications": notification,
            "backup": str(target),
            "sha256": before,
            "integrity": "ok",
            "old_reader_exercised": baseline_python is not None,
            "baseline_python": str(baseline_python),
            **expected,
            "scope": "Manual drain/backup/read-only rehearsal; no automatic downgrade or restore.",
        },
    )
