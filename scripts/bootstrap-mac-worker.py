#!/usr/bin/env python3
"""Bootstrap one verified trace-marketing release into the managed Mac layout."""

import argparse
import hashlib
import json
import os
import platform
import plistlib
import shlex
import shutil
import subprocess
import sys
import tarfile
import tempfile
import uuid
from pathlib import Path, PurePosixPath

MANIFEST_NAME = "trace-marketing-release.json"
BOOTSTRAP_NAME = "trace-marketing-bootstrap.py"
PACKAGE_NAME = "trace-appium-capture"
REPOSITORY = "corca-ai/ads-booster"
WORKER_LABELS = (
    "com.corca.trace-marketing-worker",
    "com.corca.trace-agent",
    "com.corca.trace-ads",
)
MAX_FILES = 512
MAX_MEMBER_BYTES = 256 * 1024 * 1024
MAX_BUNDLE_BYTES = 1024 * 1024 * 1024


class BootstrapError(RuntimeError):
    """A sanitized bootstrap failure."""


def parse_arguments():
    parser = argparse.ArgumentParser(
        description="Install a provenance-verified trace-marketing release on an Apple Silicon Mac."
    )
    parser.add_argument("--manifest", required=True, type=Path)
    parser.add_argument("--bundle", required=True, type=Path)
    parser.add_argument("--home", type=Path, default=Path.home() / ".trace-agent")
    parser.add_argument(
        "--install-root",
        type=Path,
        default=Path.home() / ".local" / "share" / "trace-marketing",
    )
    parser.add_argument("--uv", type=Path)
    parser.add_argument("--gh", type=Path)
    parser.add_argument("--interval-seconds", type=int, default=3600)
    return parser.parse_args()


def run(command, operation, capture=True):
    completed = subprocess.run(
        tuple(str(value) for value in command),
        check=False,
        capture_output=capture,
        text=True,
    )
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout or "").strip().splitlines()
        suffix = ": " + detail[-1][:300] if detail else ""
        raise BootstrapError("{} failed{}".format(operation, suffix))
    return completed


def load_manifest(path):
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise BootstrapError("release manifest is invalid") from error
    required = {
        "schema_version",
        "version",
        "tag",
        "commit_sha",
        "platform",
        "python",
        "package",
        "bundle",
        "bootstrap",
    }
    if not isinstance(payload, dict) or set(payload) != required:
        raise BootstrapError("release manifest has an unexpected shape")
    version = payload.get("version")
    if (
        payload.get("schema_version") != "trace.marketing-release.v1"
        or payload.get("tag") != "v{}".format(version)
        or payload.get("platform") != "macos-arm64"
        or payload.get("python") != "3.14"
        or payload.get("package") != PACKAGE_NAME
        or not _strict_version(version)
        or not _hex_digest(payload.get("commit_sha"), 40)
    ):
        raise BootstrapError("release manifest identity is invalid")
    expected_bundle = "trace-marketing-macos-arm64-v{}.tar.gz".format(version)
    _validate_file(payload.get("bundle"), expected_bundle)
    _validate_file(payload.get("bootstrap"), BOOTSTRAP_NAME)
    return payload


def _strict_version(value):
    if not isinstance(value, str):
        return False
    pieces = value.split(".")
    return len(pieces) == 3 and all(
        piece.isdigit() and (piece == "0" or not piece.startswith("0")) for piece in pieces
    )


def _hex_digest(value, length=64):
    return (
        isinstance(value, str)
        and len(value) == length
        and all(character in "0123456789abcdef" for character in value)
    )


def _validate_file(value, expected_name):
    if (
        not isinstance(value, dict)
        or set(value) != {"name", "sha256", "size"}
        or value.get("name") != expected_name
        or not _hex_digest(value.get("sha256"))
        or not isinstance(value.get("size"), int)
        or isinstance(value.get("size"), bool)
        or value.get("size") <= 0
        or value.get("size") > MAX_BUNDLE_BYTES
    ):
        raise BootstrapError("release file metadata is invalid")


def verify_local_file(path, expected):
    try:
        size = path.stat().st_size
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError as error:
        raise BootstrapError("release file could not be read") from error
    if path.name != expected["name"] or size != expected["size"] or digest != expected["sha256"]:
        raise BootstrapError("release file does not match the manifest")


def gh_json(gh, arguments, operation):
    completed = run((gh,) + tuple(arguments), operation)
    try:
        payload = json.loads(completed.stdout)
    except ValueError as error:
        raise BootstrapError("{} returned invalid JSON".format(operation)) from error
    if not isinstance(payload, dict):
        raise BootstrapError("{} returned an invalid object".format(operation))
    return payload


def verify_github_release(manifest, manifest_path, bundle_path, repository, gh):
    tag = manifest["tag"]
    bootstrap_path = Path(__file__).resolve()
    for path in (manifest_path, bundle_path, bootstrap_path):
        run(
            (
                gh,
                "attestation",
                "verify",
                str(path),
                "--repo",
                repository,
                "--signer-workflow",
                repository + "/.github/workflows/release-mac-worker.yml",
                "--source-ref",
                "refs/heads/main",
                "--source-digest",
                manifest["commit_sha"],
                "--deny-self-hosted-runners",
            ),
            "release artifact attestation",
        )
    release = gh_json(
        gh,
        (
            "api",
            "-H",
            "X-GitHub-Api-Version: 2026-03-10",
            "repos/{}/releases/tags/{}".format(repository, tag),
        ),
        "release metadata verification",
    )
    if (
        release.get("draft") is not False
        or release.get("prerelease") is not False
        or release.get("tag_name") != tag
        or release.get("target_commitish") != manifest["commit_sha"]
    ):
        raise BootstrapError("GitHub Release is not stable and commit-pinned")
    try:
        manifest_bytes = manifest_path.read_bytes()
    except OSError as error:
        raise BootstrapError("release manifest could not be read") from error
    expected_files = {
        MANIFEST_NAME: {
            "name": MANIFEST_NAME,
            "size": len(manifest_bytes),
            "sha256": hashlib.sha256(manifest_bytes).hexdigest(),
        },
        manifest["bundle"]["name"]: manifest["bundle"],
        BOOTSTRAP_NAME: manifest["bootstrap"],
    }
    assets = release.get("assets")
    if not isinstance(assets, list) or {item.get("name") for item in assets} != set(expected_files):
        raise BootstrapError("GitHub Release asset envelope is invalid")
    for asset in assets:
        expected = expected_files[asset["name"]]
        digest = asset.get("digest")
        if not isinstance(digest, str) or not digest.startswith("sha256:"):
            raise BootstrapError("GitHub Release asset has no SHA-256 digest")
        if expected is not None and (
            asset.get("size") != expected["size"]
            or digest.removeprefix("sha256:") != expected["sha256"]
        ):
            raise BootstrapError("GitHub Release asset metadata differs from the manifest")
    ref = gh_json(
        gh,
        ("api", "repos/{}/git/ref/tags/{}".format(repository, tag)),
        "release tag verification",
    )
    raw_object = ref.get("object")
    if not isinstance(raw_object, dict):
        raise BootstrapError("release tag reference is invalid")
    if raw_object.get("type") == "tag":
        annotated = gh_json(
            gh,
            ("api", "repos/{}/git/tags/{}".format(repository, raw_object.get("sha"))),
            "annotated tag verification",
        )
        raw_object = annotated.get("object")
    if (
        not isinstance(raw_object, dict)
        or raw_object.get("type") != "commit"
        or raw_object.get("sha") != manifest["commit_sha"]
    ):
        raise BootstrapError("release tag does not resolve to the manifest commit")


def require_platform_and_tools(uv_override, gh_override):
    if platform.system() != "Darwin" or platform.machine() != "arm64":
        raise BootstrapError("bootstrap requires an Apple Silicon Mac")
    uv = str(uv_override) if uv_override is not None else shutil.which("uv")
    if uv is None or not Path(uv).is_file() or not os.access(uv, os.X_OK):
        raise BootstrapError("uv is required and must already be installed")
    gh = str(gh_override) if gh_override is not None else shutil.which("gh")
    if gh is None or not Path(gh).is_file() or not os.access(gh, os.X_OK):
        raise BootstrapError("GitHub CLI is required for release verification")
    completed = run((gh, "attestation", "verify", "--help"), "GitHub CLI capability check")
    required_flags = (
        "--deny-self-hosted-runners",
        "--signer-workflow",
        "--source-digest",
        "--source-ref",
    )
    help_text = "{}\n{}".format(completed.stdout, completed.stderr)
    if any(flag not in help_text for flag in required_flags):
        raise BootstrapError("GitHub CLI lacks required artifact attestation flags")
    return Path(uv).resolve(), Path(gh).resolve()


def require_drained_worker():
    domain = "gui/{}".format(os.getuid())
    for label in WORKER_LABELS:
        completed = subprocess.run(
            ("/bin/launchctl", "print", "{}/{}".format(domain, label)),
            check=False,
            capture_output=True,
            text=True,
        )
        if completed.returncode == 0:
            template = "operator must drain and stop existing LaunchAgent {} before bootstrap"
            message = template.format(label)
            raise BootstrapError(message)


def extract_bundle(bundle, destination):
    if bundle.stat().st_size <= 0 or bundle.stat().st_size > MAX_BUNDLE_BYTES:
        raise BootstrapError("release bundle size is invalid")
    total = 0
    try:
        with tarfile.open(bundle, "r:gz") as archive:
            members = archive.getmembers()
            if not members or len(members) > MAX_FILES:
                raise BootstrapError("release bundle file count is invalid")
            for member in members:
                relative = PurePosixPath(member.name)
                if (
                    relative.is_absolute()
                    or ".." in relative.parts
                    or not relative.parts
                    or relative.parts[0] not in {"wheelhouse", "requirements.lock"}
                    or member.issym()
                    or member.islnk()
                    or member.isdev()
                    or member.size > MAX_MEMBER_BYTES
                ):
                    raise BootstrapError("release bundle contains an unsafe member")
                target = destination.joinpath(*relative.parts)
                if member.isdir():
                    target.mkdir(parents=True, exist_ok=True, mode=0o700)
                    continue
                if not member.isfile():
                    raise BootstrapError("release bundle contains an unsupported member")
                total += member.size
                if total > MAX_BUNDLE_BYTES:
                    raise BootstrapError("release bundle expands beyond its limit")
                target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
                source = archive.extractfile(member)
                if source is None:
                    raise BootstrapError("release bundle member could not be read")
                with source, target.open("wb") as stream:
                    shutil.copyfileobj(source, stream)
                target.chmod(0o600)
    except (OSError, tarfile.TarError) as error:
        raise BootstrapError("release bundle archive is invalid") from error


def atomic_text(path, value):
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temporary = path.with_name("{}.{}.tmp".format(path.name, uuid.uuid4().hex))
    temporary.write_text(value, encoding="utf-8")
    temporary.chmod(0o600)
    os.replace(str(temporary), str(path))


def install_release(manifest, bundle, root, uv):
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    root.chmod(0o700)
    releases = root / "releases"
    staging = root / "staging"
    releases.mkdir(parents=True, exist_ok=True, mode=0o700)
    staging.mkdir(parents=True, exist_ok=True, mode=0o700)
    current = root / "current"
    if current.exists() or current.is_symlink():
        raise BootstrapError("managed current release already exists; use worker update")
    attempt = Path(tempfile.mkdtemp(prefix="bootstrap-", dir=str(staging)))
    bundle_root = attempt / "bundle"
    candidate = attempt / "release"
    destination = releases / manifest["version"]
    try:
        bundle_root.mkdir(mode=0o700)
        extract_bundle(bundle, bundle_root)
        wheelhouse = bundle_root / "wheelhouse"
        project_wheels = tuple(
            wheelhouse.glob("trace_appium_capture-{}-*.whl".format(manifest["version"]))
        )
        if len(project_wheels) != 1:
            raise BootstrapError("release wheelhouse does not contain one project wheel")
        run(
            (
                uv,
                "venv",
                "--python",
                "3.14",
                "--no-python-downloads",
                "--relocatable",
                candidate,
            ),
            "staged environment creation",
        )
        run(
            (
                uv,
                "pip",
                "install",
                "--python",
                candidate / "bin" / "python",
                "--no-index",
                "--find-links",
                wheelhouse,
                "{}=={}".format(PACKAGE_NAME, manifest["version"]),
            ),
            "offline staged release installation",
        )
        executable = candidate / "bin" / "trace-marketing"
        completed = run((executable, "version", "--json"), "installed version probe")
        try:
            probed = json.loads(completed.stdout).get("version")
        except (AttributeError, ValueError) as error:
            raise BootstrapError("installed version probe returned invalid JSON") from error
        if probed != manifest["version"]:
            raise BootstrapError("installed version differs from the manifest")
        atomic_text(candidate / "release-receipt.json", json.dumps(manifest, indent=2) + "\n")
        if destination.exists():
            if (destination / "release-receipt.json").read_text(encoding="utf-8") != (
                candidate / "release-receipt.json"
            ).read_text(encoding="utf-8"):
                raise BootstrapError("existing release directory has a different receipt")
            shutil.rmtree(candidate)
        else:
            os.replace(str(candidate), str(destination))
        temporary_link = root / ".current-{}".format(uuid.uuid4().hex)
        temporary_link.symlink_to(destination.resolve(), target_is_directory=True)
        os.replace(str(temporary_link), str(current))
        return destination
    finally:
        shutil.rmtree(attempt, ignore_errors=True)


def finalize_services(release, home, root, uv, gh, interval_seconds):
    executable = release / "bin" / "trace-marketing"
    run(
        (
            executable,
            "worker",
            "finish-bootstrap",
            "--home",
            home,
            "--install-root",
            root,
            "--uv",
            uv,
            "--gh",
            gh,
            "--interval-seconds",
            str(interval_seconds),
        ),
        "managed worker service bootstrap",
    )


def finish_bootstrap_command(executable, home, root, uv, gh):
    return shlex.join(
        str(value)
        for value in (
            executable,
            "worker",
            "finish-bootstrap",
            "--home",
            home,
            "--install-root",
            root,
            "--uv",
            uv,
            "--gh",
            gh,
        )
    )


def remove_owned_legacy_plists():
    launch_agents = Path.home() / "Library" / "LaunchAgents"
    owned = {
        "com.corca.trace-agent": "trace-agent",
        "com.corca.trace-ads": "trace-ads",
    }
    removed = []
    for label, executable_name in owned.items():
        path = launch_agents / "{}.plist".format(label)
        if not path.is_file():
            continue
        try:
            payload = plistlib.loads(path.read_bytes())
        except OSError:
            continue
        except plistlib.InvalidFileException:
            continue
        if not isinstance(payload, dict):
            continue
        arguments = payload.get("ProgramArguments")
        if (
            payload.get("Label") != label
            or not isinstance(arguments, list)
            or not arguments
            or not all(isinstance(item, str) for item in arguments)
            or Path(arguments[0]).name != executable_name
        ):
            continue
        try:
            path.unlink()
        except OSError as error:
            template = "owned legacy LaunchAgent {} could not be removed"
            raise BootstrapError(template.format(label)) from error
        removed.append(label)
    return tuple(removed)


def main():
    arguments = parse_arguments()
    manifest_path = arguments.manifest.expanduser().resolve()
    bundle_path = arguments.bundle.expanduser().resolve()
    home = arguments.home.expanduser().resolve()
    root = arguments.install_root.expanduser().resolve()
    uv, gh = require_platform_and_tools(arguments.uv, arguments.gh)
    manifest = load_manifest(manifest_path)
    verify_local_file(bundle_path, manifest["bundle"])
    verify_local_file(Path(__file__).resolve(), manifest["bootstrap"])
    verify_github_release(manifest, manifest_path, bundle_path, REPOSITORY, gh)
    require_drained_worker()
    release = install_release(manifest, bundle_path, root, uv)
    credential = home / "marketing-worker" / "credential.json"
    configuration = home / "marketing-worker" / "config.json"
    if not credential.is_file() or not configuration.is_file():
        executable = root / "current" / "bin" / "trace-marketing"
        print("trace-marketing {} installed but not started".format(manifest["version"]))
        print("next step: create a one-time enrollment in the protected workspace Mac manager")
        print("after enrollment, finish bootstrap with:")
        print(finish_bootstrap_command(executable, home, root, uv, gh))
        return
    try:
        finalize_services(
            release,
            home,
            root,
            uv,
            gh,
            arguments.interval_seconds,
        )
    except BootstrapError:
        current = root / "current"
        if current.is_symlink() and current.resolve() == release.resolve():
            current.unlink()
        raise
    removed = remove_owned_legacy_plists()
    if removed:
        print("removed owned legacy LaunchAgents: {}".format(", ".join(removed)))
    print("trace-marketing {} bootstrapped under {}".format(manifest["version"], root))


if __name__ == "__main__":
    try:
        main()
    except BootstrapError as error:
        print("trace-marketing bootstrap: {}".format(error), file=sys.stderr)
        sys.exit(1)
