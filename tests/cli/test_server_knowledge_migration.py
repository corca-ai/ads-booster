from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from ads_booster.cli import server, server_knowledge

if TYPE_CHECKING:
    from pathlib import Path


def test_existing_install_backfills_once_preserving_credentials(tmp_path: Path) -> None:
    config = tmp_path / "config"
    config.mkdir()
    environment = config / "agent.env"
    original = 'TRACE_MARKETING_TENANT="fixture"\nSLACK_SECRET="unchanged"\n'
    _ = environment.write_text(original)
    root = tmp_path / "managed"
    values = server_knowledge.migrate(root, config)
    assert len(values) == 3
    assert environment.read_text().startswith(original)
    snapshot = environment.read_bytes()
    policy = (config / "knowledge-policy.json").read_bytes()
    assert server_knowledge.migrate(root, config) == values
    assert environment.read_bytes() == snapshot
    assert (config / "knowledge-policy.json").read_bytes() == policy
    assert environment.stat().st_mode & 0o777 == 0o600


def test_partial_configuration_is_preserved(tmp_path: Path) -> None:
    environment = tmp_path / "agent.env"
    text = 'TRACE_MARKETING_TENANT="fixture"\nTRACE_MARKETING_KNOWLEDGE_ROOT="/custom"\n'
    _ = environment.write_text(text)
    with pytest.raises(ValueError, match="knowledge_configuration_partial"):
        _ = server_knowledge.migrate(tmp_path / "managed", tmp_path)
    assert environment.read_text() == text
    assert not (tmp_path / "knowledge-policy.json").exists()


def test_existing_custom_paths_are_not_initialized(tmp_path: Path) -> None:
    values = dict(
        zip(
            server_knowledge.KEYS,
            ("/custom/root", "/custom/control", "/custom/policy"),
            strict=True,
        )
    )
    text = "".join(f'{name}="{value}"\n' for name, value in values.items())
    _ = (tmp_path / "agent.env").write_text(text)
    assert server_knowledge.migrate(tmp_path / "managed", tmp_path) == values
    assert (tmp_path / "agent.env").read_text() == text


def test_interrupted_environment_publish_preserves_identity(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _ = (tmp_path / "agent.env").write_text('TRACE_MARKETING_TENANT="fixture"\n')
    write = server.private_write

    def interrupted(_path: Path, _text: str) -> None:
        message = "simulated interruption"
        raise OSError(message)

    monkeypatch.setattr(server_knowledge, "private_write", interrupted)
    with pytest.raises(OSError, match="simulated interruption"):
        _ = server_knowledge.migrate(tmp_path / "managed", tmp_path)
    identity = tmp_path / "knowledge-control/identity.json"
    snapshot = identity.read_bytes()
    monkeypatch.setattr(server_knowledge, "private_write", write)
    assert len(server_knowledge.migrate(tmp_path / "managed", tmp_path)) == 3
    assert identity.read_bytes() == snapshot
