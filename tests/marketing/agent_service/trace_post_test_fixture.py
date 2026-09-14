from __future__ import annotations

import datetime as dt
import json
import subprocess
import sys
from pathlib import Path
from typing import TypedDict, cast

from PIL import Image


class _PreparedReceipt(TypedDict):
    receipt: str
    review_report_path: str


def _write_json(path: Path, value: object) -> None:
    _ = path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def _run(script: Path, *arguments: object) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603 - only frozen fixture scripts run in a temporary repo.
        [sys.executable, str(script), *(str(argument) for argument in arguments)],
        check=True,
        capture_output=True,
        text=True,
    )


def _content(device_date: str) -> tuple[dict[str, object], dict[str, object]]:
    selected_date = dt.date.fromisoformat(device_date)
    monday = selected_date - dt.timedelta(days=selected_date.weekday())
    schedule: list[dict[str, object]] = []
    kinds = ["리듬"] * 9 + ["사건"] * 7
    for index, kind in enumerate(kinds, start=1):
        schedule.append(
            {
                "id": f"s{index:02d}",
                "title": "출근" if index == 1 else f"일정 {index}",
                "date": (monday + dt.timedelta(days=(index - 1) % 7)).isoformat(),
                "days": 1,
                "time": f"{7 + index:02d}:00" if index <= 4 else "종일",
                "kind": kind,
                "color": 1 + (index - 1) % 5,
            }
        )
    todos = [{"id": f"t{index:02d}", "title": f"할 일 {index}"} for index in range(1, 9)]
    package: dict[str, object] = {
        "concept": "귀여움",
        "device_date": device_date,
        "device_time": "7:46",
        "week": [monday.isoformat(), (monday + dt.timedelta(days=6)).isoformat()],
        "captions": {
            country: {
                "skeleton": "recommend",
                "text": f"{country} synthetic caption",
                "reply_link": "https://example.invalid/reply",
                "tutorial": "synthetic five-step tutorial",
            }
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
            "date_line": f"{prefix} {device_date}",
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


def _synthetic_png(path: Path, index: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    color = ((index * 31) % 256, (index * 67) % 256, (index * 101) % 256)
    with Image.new("RGB", (64, 64), color) as image:
        image.save(path, format="PNG")


def build_completed_run(repo: Path, motif: str, place: str, date: str) -> Path:
    """Build one finished seven-call run using only frozen CLI helpers and synthetic PNGs."""
    manage_run = repo / "skills/trace-post/scripts/manage_run.py"
    initialized = _run(
        manage_run,
        "init",
        "--repo",
        repo,
        "--card",
        "concepts/cute.md",
        "--motif",
        motif,
        "--place",
        place,
        "--date",
        date,
    )
    run = Path(initialized.stdout.strip())
    package, localization = _content(date)
    _write_json(run / "package.json", package)
    _write_json(run / "localization.json", localization)

    frozen_scripts = run / "source-snapshots/scripts"
    _ = _run(frozen_scripts / "build_input.py", run)
    for filename in ("review-caption.md", "review-localization.md"):
        _ = (run / filename).write_text(
            "Synthetic fixture content review passed.",
            encoding="utf-8",
        )
    _ = _run(
        frozen_scripts / "content_review.py",
        "record",
        "--run",
        run,
        "--reviewer",
        "synthetic-fixture-reviewer",
        "--caption-verdict",
        "pass",
        "--localization-verdict",
        "pass",
    )

    common_prompt = run / "prompt-C.txt"
    _ = common_prompt.write_text("Synthetic common scene prompt.", encoding="utf-8")
    chain = [
        ("A", "kr", run / "source-snapshots/context/templates/T2/template.png"),
        ("B", "kr", run / "kr/text.png"),
        ("L", "jp", run / "kr/final.png"),
        ("L", "tw", run / "kr/final.png"),
        ("C", "kr", run / "kr/final.png"),
        ("C", "jp", run / "jp/final.png"),
        ("C", "tw", run / "tw/final.png"),
    ]
    image_call = frozen_scripts / "image_call.py"
    for index, (stage, country, input_path) in enumerate(chain, start=1):
        prompt = common_prompt if stage == "C" else run / country / f"prompt-{stage}.txt"
        if stage != "C":
            prompt.parent.mkdir(parents=True, exist_ok=True)
            _ = prompt.write_text(f"Synthetic {stage}/{country} prompt.", encoding="utf-8")
        prepared_output = _run(
            image_call,
            "prepare",
            "--run",
            run,
            "--stage",
            stage,
            "--country",
            country,
            "--prompt-file",
            prompt,
            "--input-file",
            input_path,
            "--executor",
            "synthetic-fixture-generator",
        )
        prepared_wire = cast("dict[str, object]", json.loads(prepared_output.stdout))
        receipt_value = prepared_wire["receipt"]
        if not isinstance(receipt_value, str):
            message = "synthetic fixture receipt missing"
            raise TypeError(message)
        receipt_path = Path(receipt_value)
        receipt = cast(
            "_PreparedReceipt",
            json.loads(receipt_path.read_text(encoding="utf-8")),
        )
        generated = run / "synthetic-generated" / f"{index}-{stage}-{country}.png"
        _synthetic_png(generated, index)
        _ = _run(image_call, "complete", "--receipt", receipt_path, "--source-path", generated)
        review_report = Path(receipt["review_report_path"])
        review_report.parent.mkdir(parents=True, exist_ok=True)
        _ = review_report.write_text(
            f"Synthetic fixture visual review passed for {stage}/{country}.",
            encoding="utf-8",
        )
        _ = _run(
            image_call,
            "review",
            "--receipt",
            receipt_path,
            "--report-file",
            review_report,
            "--reviewer",
            "synthetic-fixture-reviewer",
            "--pass",
        )

    for filename in ("review.md", "POST.md"):
        _ = (run / filename).write_text("Synthetic fixture deliverable.", encoding="utf-8")
    _ = _run(manage_run, "finish", "--repo", repo, "--run", run)
    return run


__all__ = ["build_completed_run"]
