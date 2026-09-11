#!/usr/bin/env python3
"""Record and verify human/model content-review decisions without inferring semantics."""
import argparse
import datetime as dt
import hashlib
import json
from pathlib import Path
import sys

SCHEMA = 1


def sha256(path):
    h = hashlib.sha256()
    with Path(path).open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def expected_files(run):
    data = json.loads((run / "run.json").read_text(encoding="utf-8"))
    countries = data.get("countries")
    if not isinstance(countries, list) or not countries:
        raise ValueError("run.json countries is invalid")
    rels = ["run.json", "package.json", "localization.json", "review-caption.md", "review-localization.md"]
    rels += [f"{country}/input.md" for country in countries]
    return rels


def record(run, reviewer, caption_verdict, localization_verdict):
    run = Path(run).resolve()
    rels = expected_files(run)
    missing = [rel for rel in rels if not (run / rel).is_file() or (rel.startswith("review-") and not (run / rel).read_text(encoding="utf-8").strip())]
    if missing:
        raise ValueError("content review inputs missing: " + ", ".join(missing))
    value = {
        "schema": SCHEMA,
        "decision": "approved" if caption_verdict == localization_verdict == "pass" else "rejected",
        "caption_verdict": caption_verdict,
        "localization_verdict": localization_verdict,
        "reviewer": reviewer,
        "reviewed_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "files": {rel: sha256(run / rel) for rel in rels},
    }
    (run / "content-review.json").write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(str(run / "content-review.json"))
    return value


def validate(run):
    run = Path(run).resolve()
    path = run / "content-review.json"
    if not path.is_file():
        raise ValueError("content-review.json is required")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError("content-review.json is invalid") from exc
    if value.get("schema") != SCHEMA or value.get("decision") != "approved":
        raise ValueError("content review is not approved")
    if value.get("caption_verdict") != "pass" or value.get("localization_verdict") != "pass":
        raise ValueError("content review verdict is not pass")
    rels = expected_files(run)
    if set(value.get("files", {})) != set(rels):
        raise ValueError("content review file set differs")
    for rel in rels:
        path = run / rel
        if not path.is_file() or (rel.startswith("review-") and not path.read_text(encoding="utf-8").strip()) or value["files"].get(rel) != sha256(path):
            raise ValueError("content review is stale: " + rel)
    return value


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    sub = p.add_subparsers(dest="command", required=True)
    r = sub.add_parser("record")
    r.add_argument("--run", required=True)
    r.add_argument("--reviewer", required=True)
    r.add_argument("--caption-verdict", choices=("pass", "fail"), required=True)
    r.add_argument("--localization-verdict", choices=("pass", "fail"), required=True)
    v = sub.add_parser("validate"); v.add_argument("--run", required=True)
    args = p.parse_args(argv)
    try:
        if args.command == "record": record(args.run, args.reviewer, args.caption_verdict, args.localization_verdict)
        else: validate(args.run); print("approved")
        return 0
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print("content review failed: " + str(exc), file=sys.stderr); return 1


if __name__ == "__main__": sys.exit(main())
