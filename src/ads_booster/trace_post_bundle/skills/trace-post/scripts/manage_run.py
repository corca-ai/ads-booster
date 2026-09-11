#!/usr/bin/env python3
"""Create, inspect, and close Trace post runs (standard library only)."""
import argparse
import contextlib
import datetime as dt
import hashlib
import importlib.util
import io
import json
import re
import shutil
import sys
import tempfile
from pathlib import Path

SUPPORTED = ("kr", "jp", "tw")
CARD_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")


def sha256(path):
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def repo_path(repo):
    return Path(repo).expanduser().resolve()


def repo_arg(root, value):
    p = Path(value).expanduser()
    return (p if p.is_absolute() else root / p).resolve()


def inside(path, parent):
    try:
        path.relative_to(parent)
        return True
    except ValueError:
        return False


def json_write(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def device_date(value):
    if value:
        try:
            dt.date.fromisoformat(value)
        except ValueError as e:
            raise ValueError("--date must be ISO YYYY-MM-DD") from e
        return value
    try:
        from zoneinfo import ZoneInfo
        return dt.datetime.now(ZoneInfo("Asia/Seoul")).date().isoformat()
    except Exception:
        return dt.datetime.now().date().isoformat()


def source_files(root, card, template):
    candidates = [
        card,
        root / "context" / "POST-RUN.md",
        root / "context" / "RUN-POLICY.md",
        root / "context" / "core" / "CAPTION-RULES.md",
        root / "context" / "core" / "IMAGE-RULES.md",
        root / "context" / "core" / "FACTS.md",
        root / "context" / "templates" / template / "spec.md",
        root / "context" / "templates" / template / "template.png",
        root / "context" / "templates" / "geom_check.py",
        root / "context" / "templates" / "review-stage.md",
        root / "scripts" / "README.md",
        root / "scripts" / "run_state.py",
        root / "scripts" / "build_input.py",
        root / "scripts" / "content_review.py",
        root / "scripts" / "image_call.py",
        root / "skills" / "trace-post" / "SKILL.md",
        root / "skills" / "trace-post" / "scripts" / "manage_run.py",
    ]
    missing = [str(p.relative_to(root)) for p in candidates if not p.is_file()]
    if missing:
        raise ValueError("required source documents missing: " + ", ".join(missing))
    return candidates


def init_run(args):
    root = repo_path(args.repo)
    card = repo_arg(root, args.card)
    concepts = (root / "concepts").resolve()
    if not card.is_file() or not inside(card, concepts):
        raise ValueError("card must be an existing file under repo/concepts")
    countries = tuple(c for item in (args.countries or SUPPORTED) for c in item.split(","))
    if "kr" not in countries or any(c not in SUPPORTED for c in countries) or len(set(countries)) != len(countries):
        raise ValueError("countries must be unique values from kr, jp, tw")
    if args.representative != "kr":
        raise ValueError("current supported scope requires representative kr")
    if args.template != "T2":
        raise ValueError("current supported scope is T2")
    if not args.motif or not args.motif.strip() or not args.place or not args.place.strip():
        raise ValueError("motif and place must be nonempty")
    date = device_date(args.date)
    stem = card.stem.lower()
    if not CARD_RE.match(stem):
        raise ValueError("card filename has no safe stem")
    posts = root / "output" / "posts"
    posts.mkdir(parents=True, exist_ok=True)
    for n in range(1, 1000):
        run = posts / f"{date}-{stem}-{n:02d}"
        try:
            run.mkdir()
            break
        except FileExistsError:
            continue
    else:
        raise ValueError("could not allocate a fresh run directory")

    selected = {
        "concept_card": str(card.relative_to(root)), "template": "T2",
        "device_date": date, "countries": list(countries), "representative": "kr",
        "motif": args.motif, "place": args.place or "", "skeleton": "recommend",
        "seasonal_replacements": False,
    }
    json_write(run / "run.json", selected)
    snapshot_root = run / "source-snapshots"
    manifest = {"captured_at": dt.datetime.now(dt.timezone.utc).isoformat(), "files": {}}
    for src in source_files(root, card, "T2"):
        rel = src.relative_to(root)
        dest = snapshot_root / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dest)
        manifest["files"][str(rel)] = {"sha256": sha256(src), "snapshot": str(dest.relative_to(run))}
    manifest["run_json_sha256"] = sha256(run / "run.json")
    json_write(run / "document-manifest.json", manifest)
    print(str(run))
    return 0


def load_run(run):
    if not run.is_dir(): raise ValueError("run directory does not exist")
    try: return json.loads((run / "run.json").read_text(encoding="utf-8"))
    except Exception as e: raise ValueError("run.json is missing or invalid") from e


def history(args):
    root = repo_path(args.repo); card = repo_arg(root, args.card)
    wanted = str(card.relative_to(root)) if inside(card, root) else str(card)
    found = []
    posts = root / "output" / "posts"
    if posts.is_dir():
        for run in sorted(posts.iterdir()):
            if not run.is_dir(): continue
            try: data = load_run(run)
            except ValueError: continue
            if data.get("concept_card") != wanted: continue
            summary = run / "run-summary.json"
            if not summary.is_file(): continue
            try: s = json.loads(summary.read_text(encoding="utf-8"))
            except Exception: continue
            if s.get("status") != "completed" or not verify_summary(run, s): continue
            entry = {k: data.get(k) for k in ("device_date", "motif", "place", "skeleton", "template", "countries", "representative")}
            entry.update(run=str(run), completed_at=s["completed_at"])
            found.append(entry)
    found.sort(key=lambda x: x["completed_at"])
    print(json.dumps(found, ensure_ascii=False, indent=2))
    return 0


def verify_summary(run, summary):
    """Only treat a summary as history when every recorded output is present and intact."""
    try:
        run_data = load_run(run)
        if summary.get("run_json_sha256") != sha256(run / "run.json"): return False
        if not isinstance(summary.get("completed_at"), str): return False
        countries = run_data["countries"]
        assets = summary["assets"]
        if set(assets) != set(countries): return False
        for country in countries:
            if set(assets[country]) != {"final.png", "scene.png"}: return False
            for name, digest in assets[country].items():
                p = run / country / name
                if not isinstance(digest, str) or not p.is_file() or sha256(p) != digest: return False
        return True
    except (KeyError, TypeError):
        return False


def call_status(run):
    log = run / "calls.jsonl"
    if not log.is_file(): raise ValueError("new calls.jsonl is required")
    spec = importlib.util.spec_from_file_location("trace_run_state", run / "source-snapshots" / "scripts" / "run_state.py")
    if not spec or not spec.loader: raise ValueError("cannot load run_state.py")
    mod = importlib.util.module_from_spec(spec); spec.loader.exec_module(mod)
    out = io.StringIO()
    try:
        with contextlib.redirect_stdout(out): mod.main(["status", "--log", str(log)])
        rows = json.loads(out.getvalue())
    except Exception as e: raise ValueError("calls.jsonl is legacy or invalid") from e
    return rows


def finish(args):
    root = repo_path(args.repo); run = repo_arg(root, args.run)
    if not inside(run, root / "output" / "posts"): raise ValueError("run must be under repo/output/posts")
    data = load_run(run); countries = data.get("countries")
    if data.get("template") != "T2" or data.get("representative") != "kr" or data.get("skeleton") != "recommend":
        raise ValueError("run is outside current supported scope")
    for name in ("package.json", "localization.json", "assembly-check.json", "review.md", "POST.md"):
        if not (run / name).is_file(): raise ValueError(f"{name} is required")
    try: assembly = json.loads((run / "assembly-check.json").read_text(encoding="utf-8"))
    except Exception as e: raise ValueError("assembly-check.json is invalid") from e
    if assembly.get("passed") is not True: raise ValueError("assembly report has not passed")
    review_helper = run / "source-snapshots" / "scripts" / "content_review.py"
    if not review_helper.is_file(): raise ValueError("content_review.py is required; frozen runs are not auto-migrated")
    review_mod = load_helper(review_helper, "trace_content_review")
    try: review_mod.validate(run)
    except Exception as e: raise ValueError("content review missing, rejected, or stale") from e
    # Rebuild in isolation so a stale assembly-check.json cannot certify changed inputs.
    build_file = run / "source-snapshots" / "scripts" / "build_input.py"
    if not build_file.is_file(): raise ValueError("scripts/build_input.py is required")
    spec = importlib.util.spec_from_file_location("trace_build_input", build_file)
    mod = importlib.util.module_from_spec(spec); spec.loader.exec_module(mod)
    with tempfile.TemporaryDirectory() as td:
        probe = Path(td)
        for name in ("run.json", "package.json", "localization.json"):
            shutil.copy2(run / name, probe / name)
        try: mod.build(probe)
        except Exception as e: raise ValueError("package/localization validation failed") from e
        fresh = json.loads((probe / "assembly-check.json").read_text(encoding="utf-8"))
        if fresh.get("passed") is not True: raise ValueError("package/localization validation failed")
    required = {("A", "kr"), ("B", "kr")} | {("L", c) for c in countries if c != "kr"} | {("C", c) for c in countries}
    rows = call_status(run)
    completed = {(r.get("stage"), r.get("country")) for r in rows if r.get("status") == "completed"}
    missing = required - completed
    if missing: raise ValueError("image calls incomplete: " + ", ".join(f"{a}/{b}" for a,b in sorted(missing)))
    receipt_helper = run / "source-snapshots" / "scripts" / "image_call.py"
    if not receipt_helper.is_file(): raise ValueError("image_call.py is required; frozen runs are not auto-migrated")
    receipt_mod = load_helper(receipt_helper, "trace_image_call")
    def selected(stage, country, output, input_path=None):
        candidates = [r for r in rows if r.get("stage") == stage and r.get("country") == country and r.get("status") == "completed"]
        target = (run / output).resolve()
        for row in candidates:
            if Path(row.get("output_path", "")).resolve() != target: continue
            if input_path is not None and Path(row.get("input_path", "")).resolve() != (run / input_path).resolve(): continue
            try: receipt_mod.validate_reviewed_receipt(run, row["call_id"])
            except Exception as e: raise ValueError(f"{stage}/{country} receipt missing, rejected, or stale") from e
            return row
        raise ValueError(f"{stage}/{country} does not record the canonical input/output chain")
    selected("A", "kr", "kr/text.png", "source-snapshots/context/templates/T2/template.png")
    selected("B", "kr", "kr/final.png", "kr/text.png")
    common_prompt = (run / "prompt-C.txt").resolve()
    if not common_prompt.is_file(): raise ValueError("common prompt-C.txt is required")
    for c in countries:
        selected("C", c, f"{c}/scene.png", f"{c}/final.png")
    for c in countries:
        if c == "kr": continue
        selected("L", c, f"{c}/final.png", "kr/final.png")
    # Country prompt copies, when present, must remain byte-identical to the common prompt.
    for c in countries:
        copy = run / c / "prompt-C.txt"
        if copy.is_file() and copy.read_bytes() != common_prompt.read_bytes():
            raise ValueError(f"{c}/prompt-C.txt differs from common prompt-C.txt")
    for row in rows:
        if row.get("stage") == "C" and row.get("status") == "completed" and Path(row.get("prompt_file", "")).resolve() != common_prompt:
            raise ValueError("C call does not use common prompt-C.txt")
    assets = {}
    for c in countries:
        for filename in ("final.png", "scene.png"):
            p = run / c / filename
            if not p.is_file(): raise ValueError(f"missing asset: {c}/{filename}")
            assets.setdefault(c, {})[filename] = sha256(p)
    summary = {"status": "completed", "completed_at": dt.datetime.now(dt.timezone.utc).isoformat(), "chosen": {k:data.get(k) for k in ("concept_card","template","device_date","countries","representative","motif","place","skeleton")}, "assets": assets, "run_json_sha256": sha256(run / "run.json")}
    json_write(run / "run-summary.json", summary)
    print(str(run / "run-summary.json")); return 0


def main(argv=None):
    p = argparse.ArgumentParser(); sub = p.add_subparsers(dest="command", required=True)
    i = sub.add_parser("init"); i.add_argument("--repo", required=True); i.add_argument("--card", required=True); i.add_argument("--motif", required=True); i.add_argument("--place"); i.add_argument("--date"); i.add_argument("--countries", nargs="+", default=list(SUPPORTED)); i.add_argument("--representative", default="kr"); i.add_argument("--template", default="T2"); i.set_defaults(func=init_run)
    h = sub.add_parser("history"); h.add_argument("--repo", required=True); h.add_argument("--card", required=True); h.set_defaults(func=history)
    f = sub.add_parser("finish"); f.add_argument("--repo", required=True); f.add_argument("--run", required=True); f.set_defaults(func=finish)
    try: return args_func(p.parse_args(argv))
    except ValueError as e: p.error(str(e)); return 2


def args_func(args): return args.func(args)


def load_helper(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    if not spec or not spec.loader: raise ValueError("cannot load helper: " + str(path))
    mod = importlib.util.module_from_spec(spec); spec.loader.exec_module(mod); return mod
if __name__ == "__main__": sys.exit(main())
