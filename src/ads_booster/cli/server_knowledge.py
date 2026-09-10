"""Backfill Knowledge on startup of an existing managed installation."""

from __future__ import annotations

import fcntl
import json
import os
import shlex
import sys
import tempfile
from pathlib import Path

from ads_booster.cli.server import env_value, private_write
from ads_booster.knowledge.configuration import (
    KnowledgeSettings,
    initialize_knowledge_store,
    initialize_local_configuration,
    load_local_actor,
)
from ads_booster.knowledge.erase_ledger import EraseLedger

KEYS = (
    "TRACE_MARKETING_KNOWLEDGE_ROOT",
    "TRACE_MARKETING_KNOWLEDGE_CONTROL_ROOT",
    "TRACE_MARKETING_KNOWLEDGE_POLICY",
)


def migrate(root: Path, config: Path) -> dict[str, str]:
    """Publish settings last so interrupted initialization can resume safely."""
    with open(config / "knowledge-migration.lock", "a", opener=_private_open) as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        environment = config / "agent.env"
        text = environment.read_text()
        values: dict[str, str] = {}
        for line in text.splitlines():
            name, separator, value = line.strip().partition("=")
            if separator and name in (*KEYS, "TRACE_MARKETING_TENANT"):
                parsed = shlex.split(value, comments=False)
                if len(parsed) != 1:
                    message = "managed_environment_value_invalid"
                    raise ValueError(message)
                values[name] = parsed[0]
        if any(key in values for key in KEYS) and not all(values.get(key) for key in KEYS):
            message = "knowledge_configuration_partial"
            raise ValueError(message)
        configured = KnowledgeSettings.from_env(values)
        if configured.enabled:
            return {key: values[key] for key in KEYS}
        tenant = values["TRACE_MARKETING_TENANT"]
        paths = (root / "knowledge", config / "knowledge-control", config / "knowledge-policy.json")
        knowledge, control, policy = paths
        knowledge.mkdir(parents=True, exist_ok=True, mode=0o700)
        control.mkdir(parents=True, exist_ok=True, mode=0o700)
        with tempfile.TemporaryDirectory(prefix="knowledge-init-", dir=config) as directory:
            staging = Path(directory)
            _ = initialize_local_configuration(
                staging / "root", staging / "control", staging / "policy.json", workspace_id=tenant
            )
            for source, destination in (
                (staging / "control/identity.json", control / "identity.json"),
                (staging / "policy.json", policy),
            ):
                if not destination.exists():
                    os.link(source, destination)
                    _sync(destination.parent)
        settings = KnowledgeSettings(knowledge, control, policy)
        actor = load_local_actor(settings)
        if actor.workspace_id != tenant:
            message = "managed_knowledge_workspace_mismatch"
            raise ValueError(message)
        _ = EraseLedger(control).initialize()
        _ = initialize_knowledge_store(settings)
        result = {key: str(path) for key, path in zip(KEYS, paths, strict=True)}
        suffix = "" if text.endswith("\n") else "\n"
        suffix += "".join(f"{key}={env_value(value)}\n" for key, value in result.items())
        private_write(environment, text + suffix)
        _sync(config)
        return result


def _private_open(path: str, flags: int) -> int:
    return os.open(path, flags | os.O_NOFOLLOW, 0o600)


def _sync(directory: Path) -> None:
    descriptor = os.open(directory, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


if __name__ == "__main__":
    print(json.dumps(migrate(Path(sys.argv[1]), Path(sys.argv[2]))))  # noqa: T201
