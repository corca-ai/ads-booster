import hashlib
import json
import runpy
import shutil
import subprocess
import sys
from importlib import resources
from pathlib import Path
from typing import TYPE_CHECKING, TypedDict, cast

if TYPE_CHECKING:
    from collections.abc import Callable


EXPECTED_FILES = frozenset(
    {
        "concepts/cute.md",
        "context/POST-RUN.md",
        "context/RUN-POLICY.md",
        "context/core/CAPTION-RULES.md",
        "context/core/FACTS.md",
        "context/core/IMAGE-RULES.md",
        "context/templates/T2/spec.md",
        "context/templates/T2/template.png",
        "context/templates/geom_check.py",
        "context/templates/review-stage.md",
        "scripts/README.md",
        "scripts/build_input.py",
        "scripts/content_review.py",
        "scripts/image_call.py",
        "scripts/run_state.py",
        "skills/trace-post/SKILL.md",
        "skills/trace-post/scripts/manage_run.py",
    }
)
SOURCE_REPOSITORY = "https://github.com/corca-ai/trace-marketing-context"
SOURCE_COMMIT = "6f462377d7ecf18c92ed716eb2d8dc80d10eaca9"


class Provenance(TypedDict):
    source_repository: str
    source_commit: str
    files: dict[str, str]


class ManifestEntry(TypedDict):
    sha256: str
    snapshot: str


class Manifest(TypedDict):
    files: dict[str, ManifestEntry]


def _bundle_root() -> Path:
    return Path(str(resources.files("ads_booster").joinpath("trace_post_bundle")))


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write_json(path: Path, value: object) -> None:
    _ = path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def _fixture() -> tuple[dict[str, object], dict[str, object]]:
    schedule: list[dict[str, object]] = []
    kinds = ["리듬"] * 9 + ["사건"] * 7
    for index, kind in enumerate(kinds, start=1):
        schedule.append(
            {
                "id": f"s{index:02d}",
                "title": "출근" if index == 1 else f"일정 {index}",
                "date": f"2026-09-{7 + (index - 1) % 7:02d}",
                "days": 1,
                "time": f"{7 + index:02d}:00" if index <= 4 else "종일",
                "kind": kind,
                "color": 1 + (index - 1) % 5,
            }
        )
    todos = [{"id": f"t{index:02d}", "title": f"할 일 {index}"} for index in range(1, 9)]
    package: dict[str, object] = {
        "concept": "귀여움",
        "device_date": "2026-09-10",
        "device_time": "7:46",
        "week": ["2026-09-07", "2026-09-13"],
        "captions": {
            country: {"skeleton": "recommend", "text": f"{country} caption"}
            for country in ("kr", "jp", "tw")
        },
        "schedule": schedule,
        "todos": todos,
        "schedule_exception": "컨셉 카드에 여러 날 지속하는 일정의 근거가 없다.",
    }
    languages = {"kr": "ko", "jp": "ja", "tw": "zh-TW"}
    localization: dict[str, object] = {}
    for country, language in languages.items():
        prefix = "한국" if country == "kr" else country.upper()
        localization[country] = {
            "language": language,
            "date_line": f"{prefix} 9/10",
            "headers": {
                "schedule": f"{prefix} schedule",
                "todos": f"{prefix} todos",
                "tomorrow": f"{prefix} tomorrow",
                "all_day": f"{prefix} all day",
            },
            "schedule": [{**item, "title": f"{prefix} {item['id']}"} for item in schedule],
            "todos": [{**item, "title": f"{prefix} {item['id']}"} for item in todos],
            "seasonal_changes": [],
        }
    return package, localization


def _run(*args: object, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603 - the interpreter and bundle scripts are fixed test inputs.
        [sys.executable, *(str(arg) for arg in args)],
        check=check,
        capture_output=True,
        text=True,
    )


def test_packaged_bundle_matches_pinned_provenance_and_source_dependencies() -> None:
    bundle = _bundle_root()
    provenance = cast(
        "Provenance",
        json.loads((bundle / "provenance.json").read_text(encoding="utf-8")),
    )

    assert provenance["source_repository"] == SOURCE_REPOSITORY
    assert provenance["source_commit"] == SOURCE_COMMIT
    assert set(provenance["files"]) == EXPECTED_FILES
    assert {
        path.relative_to(bundle).as_posix()
        for path in bundle.rglob("*")
        if path.is_file() and path.name != "provenance.json"
    } == EXPECTED_FILES
    assert {relative: _sha256(bundle / relative) for relative in EXPECTED_FILES} == provenance[
        "files"
    ]

    namespace = runpy.run_path(
        str(bundle / "skills/trace-post/scripts/manage_run.py"),
        run_name="trace_post_manage_run",
    )
    source_files = cast(
        "Callable[[Path, Path, str], list[Path]]",
        namespace["source_files"],
    )
    dependencies = source_files(bundle, bundle / "concepts/cute.md", "T2")
    assert {path.relative_to(bundle).as_posix() for path in dependencies} == EXPECTED_FILES


def test_isolated_bundle_initializes_assembles_deterministically_and_enforces_gate(
    tmp_path: Path,
) -> None:
    repo = tmp_path / "trace-post"
    _ = shutil.copytree(_bundle_root(), repo)
    manage_run = repo / "skills/trace-post/scripts/manage_run.py"
    package, localization = _fixture()

    runs: list[Path] = []
    for _ in range(2):
        initialized = _run(
            manage_run,
            "init",
            "--repo",
            repo,
            "--card",
            "concepts/cute.md",
            "--motif",
            "미피",
            "--place",
            "카페 나무 테이블",
            "--date",
            "2026-09-10",
        )
        run = Path(initialized.stdout.strip())
        runs.append(run)
        _write_json(run / "package.json", package)
        _write_json(run / "localization.json", localization)
        built = _run(run / "source-snapshots/scripts/build_input.py", run)
        assert json.loads(built.stdout)["passed"] is True

        manifest = cast(
            "Manifest",
            json.loads((run / "document-manifest.json").read_text(encoding="utf-8")),
        )
        assert set(manifest["files"]) == EXPECTED_FILES
        for entry in manifest["files"].values():
            snapshot = run / entry["snapshot"]
            assert _sha256(snapshot) == entry["sha256"]

    generated = ["assembly-check.json", "package.md", "kr/input.md", "jp/input.md", "tw/input.md"]
    assert {relative: (runs[0] / relative).read_bytes() for relative in generated} == {
        relative: (runs[1] / relative).read_bytes() for relative in generated
    }

    run = runs[0]
    prompt = run / "kr/prompt-A.txt"
    _ = prompt.write_text("synthetic prompt", encoding="utf-8")
    image_call = run / "source-snapshots/scripts/image_call.py"
    template = run / "source-snapshots/context/templates/T2/template.png"
    prepare_args = (
        image_call,
        "prepare",
        "--run",
        run,
        "--stage",
        "A",
        "--country",
        "kr",
        "--prompt-file",
        prompt,
        "--input-file",
        template,
        "--executor",
        "test",
    )
    assert _run(*prepare_args, check=False).returncode == 1

    for name in ("review-caption.md", "review-localization.md"):
        _ = (run / name).write_text("synthetic semantic review", encoding="utf-8")
    content_review = run / "source-snapshots/scripts/content_review.py"
    _ = _run(
        content_review,
        "record",
        "--run",
        run,
        "--reviewer",
        "test-reviewer",
        "--caption-verdict",
        "pass",
        "--localization-verdict",
        "pass",
    )
    prepared = _run(*prepare_args)
    prepared_value = cast("dict[str, object]", json.loads(prepared.stdout))
    receipt = prepared_value["receipt"]
    assert isinstance(receipt, str)
    assert Path(receipt).is_file()

    _ = (run / "package.json").write_text(
        (run / "package.json").read_text(encoding="utf-8") + " ",
        encoding="utf-8",
    )
    stale = _run(content_review, "validate", "--run", run, check=False)
    assert stale.returncode == 1
    assert "content review is stale: package.json" in stale.stderr
