#!/usr/bin/env python3
"""Prepare image_gen requests and bind completion/review to durable receipts."""
import argparse
import contextlib
import datetime as dt
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile

SCHEMA = 1


def sha256(path):
    h = hashlib.sha256()
    with Path(path).open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def load_module(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    if not spec or not spec.loader: raise ValueError("cannot load helper: " + str(path))
    mod = importlib.util.module_from_spec(spec); spec.loader.exec_module(mod); return mod


def inside(path, parent):
    try: Path(path).resolve().relative_to(Path(parent).resolve()); return True
    except ValueError: return False


def canonical(run, stage, country):
    data = json.loads((run / "run.json").read_text(encoding="utf-8"))
    countries = data.get("countries", [])
    if country not in countries: raise ValueError("country is outside run")
    if stage == "A" and country == "kr": return run / "source-snapshots/context/templates/T2/template.png", run / "kr/text.png"
    if stage == "B" and country == "kr": return run / "kr/text.png", run / "kr/final.png"
    if stage == "L" and country != "kr": return run / "kr/final.png", run / country / "final.png"
    if stage == "C": return run / country / "final.png", run / country / "scene.png"
    raise ValueError("unsupported stage/country")


def modules(run):
    scripts = run / "source-snapshots/scripts"
    return load_module(scripts / "run_state.py", "receipt_run_state"), load_module(scripts / "content_review.py", "receipt_content_review")


def write_receipt(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def prepare(run, stage, country, prompt_file, input_file, executor, retry=0):
    run = Path(run).resolve(); prompt = Path(prompt_file).resolve(); supplied_input = Path(input_file).resolve()
    if not inside(prompt, run) or not prompt.is_file(): raise ValueError("prompt file must be inside run")
    expected_input, output = canonical(run, stage, country)
    if supplied_input != expected_input.resolve() or not supplied_input.is_file(): raise ValueError("input is not canonical or missing")
    state, reviews = modules(run); reviews.validate(run)
    rows = state.events(run / "calls.jsonl")
    ended = {r.get("call_id") for r in rows if r.get("event") == "end"}
    if any(r.get("event") == "begin" and r.get("stage") == stage and r.get("country") == country and r.get("call_id") not in ended for r in rows):
        raise ValueError("an in-progress call already exists for stage/country")
    if stage == "C" and prompt != (run / "prompt-C.txt").resolve(): raise ValueError("C must use common prompt-C.txt")
    if stage in {"L", "C"}: validate_upstream(run, supplied_input)
    if output.exists(): raise ValueError("canonical output already exists; preserve it and use a new retry path")
    log = run / "calls.jsonl"
    args = ["begin", "--log", str(log), "--stage", stage, "--country", country, "--retry", str(retry), "--prompt-file", str(prompt), "--input-file", str(supplied_input), "--executor", executor, "--tool", "image_gen"]
    capture = io.StringIO()
    with contextlib.redirect_stdout(capture):
        if state.main(args) != 0: raise ValueError("could not record begin")
    begin = json.loads(capture.getvalue())
    receipt = run / "receipts" / f"{stage}-{country}-{begin['call_id']}.json"
    value = {"schema":SCHEMA, "status":"prepared", "run":str(run), "receipt":str(receipt), "call_id":begin["call_id"], "stage":stage, "country":country, "retry":retry, "log":str(log.resolve()), "prompt_file":str(prompt), "prompt_sha256":begin["prompt_sha256"], "input_path":str(supplied_input), "input_sha256":begin["input_sha256"], "output_path":str(output.resolve()), "review_report_path":str((run / "reviews" / f"{begin['call_id']}.md").resolve()), "error_evidence_path":str((run / "errors" / f"{begin['call_id']}.md").resolve()), "request":{"prompt":prompt.read_bytes().decode("utf-8"), "referenced_image_paths":[str(supplied_input)]}}
    write_receipt(receipt, value)
    print(json.dumps({"receipt":str(receipt), "request":value["request"]}, ensure_ascii=False))
    return value


def load_receipt(path, validate_current=True):
    path = Path(path).resolve()
    try: value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc: raise ValueError("receipt is missing or invalid") from exc
    run = Path(value.get("run", "")).resolve()
    if value.get("schema") != SCHEMA or Path(value.get("receipt", "")).resolve() != path or not inside(path, run / "receipts"):
        raise ValueError("receipt identity is invalid")
    expected_input, expected_output = canonical(run, value.get("stage"), value.get("country"))
    if Path(value.get("input_path", "")).resolve() != expected_input.resolve() or Path(value.get("output_path", "")).resolve() != expected_output.resolve():
        raise ValueError("receipt paths are not canonical")
    state, reviews = modules(run)
    if validate_current: reviews.validate(run)
    if Path(value.get("log", "")).resolve() != (run / "calls.jsonl").resolve(): raise ValueError("receipt log path is invalid")
    rows = state.events(Path(value["log"])); begins = [r for r in rows if r.get("event") == "begin" and r.get("call_id") == value.get("call_id")]
    if len(begins) != 1: raise ValueError("receipt call_id is stale or unknown")
    begin = begins[0]
    for key in ("stage", "country", "prompt_file", "prompt_sha256", "input_path", "input_sha256"):
        if begin.get(key) != value.get(key): raise ValueError("receipt differs from begin event: " + key)
    request = value.get("request")
    if not isinstance(request, dict) or set(request) != {"prompt", "referenced_image_paths"} or request.get("referenced_image_paths") != [value["input_path"]] or not isinstance(request.get("prompt"), str) or hashlib.sha256(request["prompt"].encode("utf-8")).hexdigest() != value["prompt_sha256"]:
        raise ValueError("receipt request differs from begin event")
    if validate_current and (sha256(value["prompt_file"]) != value["prompt_sha256"] or sha256(value["input_path"]) != value["input_sha256"]):
        raise ValueError("prompt or input changed after prepare")
    if value.get("review_report_path") != str((run / "reviews" / f"{value['call_id']}.md").resolve()): raise ValueError("receipt review report path is invalid")
    if value.get("error_evidence_path") != str((run / "errors" / f"{value['call_id']}.md").resolve()): raise ValueError("receipt error evidence path is invalid")
    if value.get("status") in {"completed", "reviewed"}:
        output = Path(value["output_path"])
        if not output.is_file() or sha256(output) != value.get("output_sha256"): raise ValueError("receipt output is stale")
    if value.get("status") in {"error", "unresolved"}:
        evidence = Path(value.get("error_evidence", ""))
        if evidence != Path(value["error_evidence_path"]) or not evidence.is_file() or sha256(evidence) != value.get("error_evidence_sha256"):
            raise ValueError("receipt error evidence is stale")
    return path, value, state


def complete(receipt, source_path):
    path, value, state = load_receipt(receipt)
    if value.get("status") not in {"prepared", "unresolved"}: raise ValueError("receipt is not completable")
    was_unresolved = value.get("status") == "unresolved"
    source = Path(source_path).resolve(); output = Path(value["output_path"])
    if not source.is_file(): raise ValueError("generated source is missing")
    rows = state.events(Path(value["log"])); ends = [r for r in rows if r.get("event") == "end" and r.get("call_id") == value["call_id"]]
    recovered = was_unresolved
    if output.exists():
        if sha256(output) != sha256(source): raise ValueError("canonical output already exists and differs; refusing overwrite")
        recovered = True
    else:
        output.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(dir=output.parent, delete=False) as f: temp = Path(f.name)
        try:
            shutil.copy2(source, temp); os.replace(temp, output)
        finally:
            if temp.exists(): temp.unlink()
    if ends:
        if len(ends) != 1 or Path(ends[0].get("output_path", "")).resolve() != output.resolve() or ends[0].get("output_sha256") != sha256(output):
            raise ValueError("existing end event differs from receipt output")
    else:
        end_args = ["end", "--log", value["log"], "--call-id", value["call_id"], "--output-file", str(output), "--source-path", str(source)]
        if recovered: end_args.append("--recovered")
        with contextlib.redirect_stdout(io.StringIO()):
            if state.main(end_args) != 0: raise ValueError("could not record end")
    value.update(status="completed", source_path=str(source), output_sha256=sha256(output)); write_receipt(path, value)
    print(str(output)); return value


def record_problem(receipt, evidence_file, unresolved):
    path, value, state = load_receipt(receipt, validate_current=False)
    evidence = Path(evidence_file).resolve()
    if value.get("status") != "prepared": raise ValueError("only a prepared receipt can record a tool problem")
    if evidence != Path(value["error_evidence_path"]) or not evidence.is_file() or not evidence.read_text(encoding="utf-8").strip():
        raise ValueError("nonempty receipt-specific error evidence is required")
    issue = "unresolved" if unresolved else "error"
    if not unresolved:
        with contextlib.redirect_stdout(io.StringIO()):
            if state.main(["end", "--log", value["log"], "--call-id", value["call_id"]]) != 0:
                raise ValueError("could not close confirmed no-output call")
    value.update(status=issue, issue_recorded_at=dt.datetime.now(dt.timezone.utc).isoformat(), error_evidence=str(evidence), error_evidence_sha256=sha256(evidence))
    write_receipt(path, value); print(str(path)); return value


def review(receipt, report_file, reviewer, passed):
    path, value, state = load_receipt(receipt)
    report = Path(report_file).resolve()
    if value.get("status") != "completed" or report != Path(value["review_report_path"]) or not report.is_file() or not report.read_text(encoding="utf-8").strip():
        raise ValueError("completed receipt and review report inside run are required")
    with contextlib.redirect_stdout(io.StringIO()):
        if state.main(["review", "--log", value["log"], "--call-id", value["call_id"]] + (["--pass"] if passed else [])) != 0:
            raise ValueError("could not record review")
    value.update(status="reviewed", review="PASS" if passed else "FAIL", reviewer=reviewer, review_report=str(report), review_report_sha256=sha256(report)); write_receipt(path, value)
    print(str(path)); return value


def validate_reviewed_receipt(run, call_id):
    run = Path(run).resolve(); matches = list((run / "receipts").glob(f"*-{call_id}.json"))
    if len(matches) != 1: raise ValueError("reviewed receipt missing for call_id: " + call_id)
    _, value, _ = load_receipt(matches[0])
    if value.get("status") != "reviewed" or value.get("review") != "PASS": raise ValueError("image receipt is not approved")
    state, _ = modules(run); rows = state.events(run / "calls.jsonl")
    reviews = [row for row in rows if row.get("event") == "review" and row.get("call_id") == call_id]
    if not reviews or reviews[-1].get("review") != "PASS": raise ValueError("latest image log review is not PASS")
    report = Path(value.get("review_report", ""))
    if not report.is_file() or sha256(report) != value.get("review_report_sha256"): raise ValueError("image review report is stale")
    return value


def validate_upstream(run, input_path):
    state, _ = modules(run); rows = state.events(run / "calls.jsonl")
    candidates = [r for r in rows if r.get("event") == "end" and Path(r.get("output_path", "")).resolve() == Path(input_path).resolve()]
    for row in reversed(candidates):
        try: return validate_reviewed_receipt(run, row["call_id"])
        except ValueError: continue
    raise ValueError("input-producing image stage lacks a current approved receipt")


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__); sub=p.add_subparsers(dest="command",required=True)
    a=sub.add_parser("prepare"); a.add_argument("--run",required=True); a.add_argument("--stage",required=True,choices=("A","B","L","C")); a.add_argument("--country",required=True); a.add_argument("--prompt-file",required=True); a.add_argument("--input-file",required=True); a.add_argument("--executor",required=True); a.add_argument("--retry",type=int,default=0)
    c=sub.add_parser("complete"); c.add_argument("--receipt",required=True); c.add_argument("--source-path",required=True)
    for command in ("error", "unresolved"):
        e=sub.add_parser(command); e.add_argument("--receipt",required=True); e.add_argument("--evidence-file",required=True)
    v=sub.add_parser("review"); v.add_argument("--receipt",required=True); v.add_argument("--report-file",required=True); v.add_argument("--reviewer",required=True); v.add_argument("--pass",dest="passed",action="store_true")
    args=p.parse_args(argv)
    try:
        if args.command=="prepare": prepare(args.run,args.stage,args.country,args.prompt_file,args.input_file,args.executor,args.retry)
        elif args.command=="complete": complete(args.receipt,args.source_path)
        elif args.command in {"error", "unresolved"}: record_problem(args.receipt,args.evidence_file,args.command=="unresolved")
        else: review(args.receipt,args.report_file,args.reviewer,args.passed)
        return 0
    except (OSError,ValueError,json.JSONDecodeError) as exc: print("image call failed: "+str(exc),file=sys.stderr); return 1


if __name__=="__main__": sys.exit(main())
