#!/usr/bin/env python3
"""Small JSONL state log for image calls; this tool never invokes an image tool."""
import argparse
import datetime as dt
import hashlib
import json
from pathlib import Path
import sys
import uuid


def sha256(path):
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b''):
            h.update(chunk)
    return h.hexdigest()


def now():
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec='seconds').replace('+00:00', 'Z')


def append(path, event):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('a', encoding='utf-8') as f:
        f.write(json.dumps(event, ensure_ascii=False, separators=(',', ':')) + '\n')


def events(path):
    if not path.exists(): return []
    out = []
    for line in path.read_text(encoding='utf-8').splitlines():
        if line.strip():
            row = json.loads(line)
            if not isinstance(row, dict) or not row.get('call_id') or row.get('event') not in {'begin', 'end', 'review'}:
                raise ValueError('legacy flat log format; migrate before use')
            out.append(row)
    return out


def begin(args):
    events(Path(args.log))  # Do not append new-format events to a legacy log.
    event = {'event':'begin', 'call_id':uuid.uuid4().hex, 'started_at':now(), 'stage':args.stage, 'country':args.country, 'retry':args.retry, 'executor':args.executor, 'tool':args.tool, 'prompt_file':str(args.prompt_file.resolve()), 'prompt_sha256':sha256(args.prompt_file)}
    if args.input_file: event.update(input_path=str(args.input_file.resolve()), input_sha256=sha256(args.input_file))
    if args.source_path: event['source_path'] = str(args.source_path.resolve())
    append(Path(args.log), event); print(json.dumps(event, ensure_ascii=False)); return 0


def end(args):
    path = Path(args.log); rows = events(path)
    begins = {x.get('call_id'): x for x in rows if x.get('event') == 'begin'}
    if args.call_id not in begins: raise ValueError('알 수 없는 call_id')
    if any(x.get('event') == 'end' and x.get('call_id') == args.call_id for x in rows): raise ValueError('이미 종료된 call_id')
    event = {'event':'end', 'call_id':args.call_id, 'ended_at':None if args.recovered else now(), 'recorded_at':now(), 'recovered':args.recovered, 'review':'PENDING', 'status':'needs-review'}
    if args.output_file:
        event.update(output_path=str(args.output_file.resolve()), output_sha256=sha256(args.output_file))
    if args.source_path: event['source_path'] = str(args.source_path.resolve())
    if args.reviewed_pass:
        if not args.output_file: raise ValueError('PASS 종료에는 --output-file 필요')
        event['review'] = 'PASS'
        event['reviewed_at'] = now()
    append(path, event); print(json.dumps(event, ensure_ascii=False)); return 0


def review(args):
    path = Path(args.log); rows = events(path)
    if not any(x.get('event') == 'begin' and x.get('call_id') == args.call_id for x in rows): raise ValueError('알 수 없는 call_id')
    if not any(x.get('event') == 'end' and x.get('call_id') == args.call_id and x.get('output_path') for x in rows): raise ValueError('review 전에 end --output-file로 실제 출력을 기록해야 함')
    append(path, {'event':'review', 'call_id':args.call_id, 'reviewed_at':now(), 'review':'PASS' if args.pass_review else 'FAIL'})
    return 0


def status(args):
    rows = events(Path(args.log)); latest = {}
    for row in rows:
        latest.setdefault(row.get('call_id'), {})
        latest[row.get('call_id')].update(row)
        if row.get('event') == 'review':
            latest[row.get('call_id')]['review'] = row.get('review')
            latest[row.get('call_id')]['reviewed_at'] = row.get('reviewed_at')
    result = []
    for call_id, row in latest.items():
        ended = any(x.get('event') == 'end' and x.get('call_id') == call_id for x in rows)
        if not ended: row['status'] = 'in-progress'
        elif row.get('review') != 'PASS': row['status'] = 'needs-review'
        else:
            valid = True
            for key, path_key in [('prompt_sha256','prompt_file'),('input_sha256','input_path'),('output_sha256','output_path')]:
                if path_key not in row or not Path(row[path_key]).is_file() or sha256(row[path_key]) != row.get(key): valid = False
            if args.prompt_file and sha256(args.prompt_file) != row.get('prompt_sha256'): valid = False
            row['status'] = 'completed' if valid else 'needs-review'
        result.append(row)
    print(json.dumps(result, ensure_ascii=False, indent=2)); return 0


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__); sub=p.add_subparsers(dest='command',required=True)
    b=sub.add_parser('begin'); b.add_argument('--log',required=True,type=Path); b.add_argument('--stage',required=True); b.add_argument('--country',required=True); b.add_argument('--retry',type=int,default=0); b.add_argument('--prompt-file',required=True,type=Path); b.add_argument('--input-file',type=Path); b.add_argument('--source-path',type=Path); b.add_argument('--executor',default='unknown'); b.add_argument('--tool',default='unknown'); b.set_defaults(func=begin)
    e=sub.add_parser('end'); e.add_argument('--log',required=True,type=Path); e.add_argument('--call-id',required=True); e.add_argument('--output-file',type=Path); e.add_argument('--reviewed-pass',action='store_true'); e.add_argument('--recovered',action='store_true'); e.add_argument('--source-path',type=Path); e.set_defaults(func=end)
    v=sub.add_parser('review'); v.add_argument('--log',required=True,type=Path); v.add_argument('--call-id',required=True); v.add_argument('--pass',dest='pass_review',action='store_true'); v.set_defaults(func=review)
    s=sub.add_parser('status'); s.add_argument('--log',required=True,type=Path); s.add_argument('--prompt-file',type=Path); s.set_defaults(func=status)
    args=p.parse_args(argv)
    try: return args.func(args)
    except (OSError,ValueError,json.JSONDecodeError) as exc: print('상태 실패: '+str(exc),file=sys.stderr); return 1
if __name__ == '__main__': sys.exit(main())
