#!/usr/bin/env python3
"""Validate a run and assemble deterministic country input files."""
import collections
import datetime as dt
import json
from pathlib import Path
import re
import sys


def _text(value):
    return json.dumps(value, ensure_ascii=False, indent=2) + '\n'


def _write(path, content, replace=False):
    if path.exists():
        old = path.read_text()
        if old != content and not replace:
            raise ValueError(f'기존 출력 불일치 (덮어쓰기에는 --replace 필요): {path.name}')
        if old == content:
            return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)


def _package_md(package):
    lines = ['# Package', '', '## Captions', '']
    for country, caption in package.get('captions', {}).items():
        lines += [f'### {country}', '', str(caption.get('text', '')), '', f'- Reply link: {caption.get("reply_link", "")}', f'- Tutorial: {caption.get("tutorial", "")}', '']
    lines += ['## Schedule', '', '| ID | Title | Date | Days | Time | Kind | Color |', '|---|---|---|---:|---|---|---:|']
    for x in package.get('schedule', []):
        lines.append('| ' + ' | '.join(str(x.get(k, '')) for k in ['id','title','date','days','time','kind','color']) + ' |')
    lines += ['', '## Todos', '']
    lines += [f'- {x.get("id", "")}: {x.get("title", "")}' for x in package.get('todos', [])]
    return '\n'.join(lines) + '\n'


def build(root, replace=False):
    root = Path(root)
    errors = []

    def read(name):
        try:
            return json.loads((root / name).read_text())
        except FileNotFoundError:
            errors.append(f'{name}: 파일 누락')
        except json.JSONDecodeError as exc:
            errors.append(f'{name}: JSON 형식 오류 (line {exc.lineno}, column {exc.colno})')
        except (OSError, UnicodeError) as exc:
            errors.append(f'{name}: 파일 읽기 실패 ({exc})')
        return {}

    config, package, localized = read('run.json'), read('package.json'), read('localization.json')
    if not isinstance(config, dict): errors.append('run.json: 객체 필요'); config = {}
    if not isinstance(package, dict): errors.append('package.json: 객체 필요'); package = {}
    if not isinstance(localized, dict): errors.append('localization.json: 객체 필요'); localized = {}
    def check(ok, msg):
        if not ok: errors.append(msg)
    # Reject malformed shapes before using IDs in messages, sets or rendering.
    for name in ('schedule', 'todos'):
        values = package.get(name, [])
        if isinstance(values, list):
            for i, item in enumerate(values):
                if not isinstance(item, dict) or not isinstance(item.get('id'), str) or not isinstance(item.get('title'), str):
                    errors.append(f'{name}[{i}]: 문자열 id/title 필요')
                elif name == 'schedule' and (not isinstance(item.get('kind'), str) or type(item.get('color')) is not int):
                    errors.append(f'{name}[{i}]: 문자열 kind/정수 color 필요')
    captions = package.get('captions')
    if not isinstance(captions, dict) or any(not isinstance(v, dict) or not isinstance(v.get('text'), str) or not isinstance(v.get('skeleton'), str) for v in captions.values()):
        errors.append('captions: 국가별 text/skeleton 문자열 필요')
    if errors:
        root.mkdir(parents=True, exist_ok=True)
        (root/'assembly-check.json').write_text(_text(dict(passed=False, errors=errors)))
        raise ValueError('; '.join(errors))

    countries = config.get('countries', [])
    if not isinstance(countries, list): errors.append('run.json: countries 배열 필요'); countries = []
    elif not all(isinstance(c, str) for c in countries): errors.append('run.json: countries 원소 문자열 필요')
    schedule, todos = package.get('schedule', []), package.get('todos', [])
    if not isinstance(schedule, list): errors.append('package.json: schedule 배열 필요'); schedule = []
    if not isinstance(todos, list): errors.append('package.json: todos 배열 필요'); todos = []
    try:
        date = dt.date.fromisoformat(config['device_date']); monday = date - dt.timedelta(days=date.weekday())
    except (KeyError, TypeError, ValueError):
        date = monday = None; errors.append('run.json: device_date ISO 날짜 필요')
    check(config.get('template') == 'T2', '현재 조립기는 T2만 지원')
    check(config.get('seasonal_replacements') is False, '계절 교체 지원 전')
    country_strings = [c for c in countries if isinstance(c, str)]
    check(len(country_strings) == len(set(country_strings)), '국가 중복')
    check(set(c for c in countries if isinstance(c, str)) <= {'kr', 'jp', 'tw'}, '지원하지 않는 국가')
    check(config.get('representative') in countries, '대표 국가 누락')
    if date:
        check(package.get('device_date') == date.isoformat(), '기기 날짜 불일치')
        check(package.get('week') == [monday.isoformat(), (monday + dt.timedelta(days=6)).isoformat()], '월~일 범위 불일치')
    check(isinstance(package.get('device_time'), str) and bool(re.fullmatch(r'(?:[0-9]|1[0-9]|2[0-3]):[0-5][0-9]', package.get('device_time', ''))), '기기 시각 형식')
    check(len(schedule) <= 22, '일정 최대 22행')
    check(len(schedule) >= 18 or (isinstance(package.get('schedule_exception'), str) and bool(package['schedule_exception'].strip())), '18행 미만은 기간 근거 부족 사유 schedule_exception 필요')
    check(8 <= len(todos) <= 12, '할일 8~12개')
    items = [x for x in schedule + todos if isinstance(x, dict)]
    ids = [x['id'] for x in items if isinstance(x.get('id'), str)]
    check(len(items) == len(schedule) + len(todos) and len(ids) == len(items), '항목 객체/id 필요')
    check(len(ids) == len(set(ids)), '항목 ID 중복')
    kinds = collections.Counter(x.get('kind') for x in schedule if isinstance(x, dict) and isinstance(x.get('kind'), str))
    check(7 <= kinds['리듬'] <= 9 and 6 <= kinds['사건'] <= 8 and 0 <= kinds['기간'] <= 4, '리듬·사건·기간 배분')
    check(set(kinds) <= {'리듬', '사건', '기간'}, '알 수 없는 일정 종류')
    check(sum(x.get('title') == '출근' for x in schedule if isinstance(x, dict)) == 1, '출근 1행')
    check(3 <= sum(x.get('time') != '종일' for x in schedule if isinstance(x, dict)) <= 5, '시각 3~5개')
    check(4 <= len({x.get('color') for x in schedule if isinstance(x, dict) and isinstance(x.get('color'), (int, str))}) <= 5, '색 4~5그룹')
    for x in schedule:
        if not isinstance(x, dict): continue
        try:
            day = dt.date.fromisoformat(x['date'])
            if monday: check(monday <= day <= monday + dt.timedelta(days=6), x.get('id', '?') + ': 주간 밖 시작일')
        except (KeyError, TypeError, ValueError): errors.append(x.get('id', '?') + ': 날짜 형식')
        check(type(x.get('days')) is int and (2 <= x['days'] <= 3 if x.get('kind') == '기간' else x.get('days') == 1), x.get('id', '?') + ': 기간 길이')
        check(x.get('time') == '종일' or (isinstance(x.get('time'), str) and bool(re.fullmatch(r'(?:[01][0-9]|2[0-3]):[0-5][0-9]', x['time']))), x.get('id', '?') + ': 시각 형식')
    outputs = {}
    for country in countries:
        if not isinstance(country, str):
            continue
        loc = localized.get(country)
        if not isinstance(loc, dict): errors.append(country + ': localization 객체 누락'); continue
        check(loc.get('language') == {'kr':'ko','jp':'ja','tw':'zh-TW'}.get(country), country + ': 언어')
        check(not loc.get('seasonal_changes'), country + ': 계절 교체 금지')
        captions = package.get('captions', {})
        check(isinstance(captions, dict) and country in captions, country + ': 캡션 누락')
        if isinstance(captions, dict) and isinstance(captions.get(country), dict): check(captions[country].get('skeleton') == config.get('skeleton'), country + ': 뼈대 불일치')
        for name, base in [('schedule', schedule), ('todos', todos)]:
            translated = loc.get(name)
            if not isinstance(translated, list): errors.append(country + ': ' + name + ' 배열 필요'); continue
            check(len(base) == len(translated), country + ': ' + name + ' 개수')
            check([x.get('id') for x in base if isinstance(x,dict)] == [x.get('id') for x in translated if isinstance(x,dict)], country + ': ' + name + ' ID·순서')
            for a,b in zip(base, translated):
                if not isinstance(a,dict) or not isinstance(b,dict): continue
                check({k:v for k,v in a.items() if k!='title'} == {k:v for k,v in b.items() if k!='title'}, country + ': ' + a.get('id','?') + ' 구조 변경')
                check(isinstance(b.get('title'),str) and bool(b['title'].strip()) and not re.search(r'[{}\n|]',b['title']), country + ': ' + a.get('id','?') + ' 미치환·표 형식')
                # A copied Korean title preserves structure but is not localization.
                if country in {'jp', 'tw'} and re.search(r'[가-힣ㄱ-ㅎㅏ-ㅣ]', a.get('title', '')):
                    check(b.get('title') != a['title'], country + ': ' + a.get('id','?') + ' 한국어 원문 그대로: 현지화 필요')
        headers = loc.get('headers', {})
        check(set(headers) == {'schedule','todos','tomorrow','all_day'} if isinstance(headers,dict) else False, country + ': 헤더')
        if date and isinstance(headers,dict) and isinstance(loc.get('schedule'),list) and isinstance(loc.get('todos'),list):
            meta = {k:config.get(k) for k in ['template','concept_card','motif','place']}; meta.update(concept=package.get('concept'),country=country,language=loc.get('language'),device_date=package.get('device_date'),device_time=package.get('device_time'),date_line=loc.get('date_line'),headers=headers)
            lines=['# 이미지 입력','','```json',json.dumps(meta,ensure_ascii=False,indent=2),'```','','## 주간 일정','','| ID | 제목 | 날짜 | 며칠 | 시각 | 종류 | 색 그룹 |','|---|---|---|---|---|---|---|']
            lines += ['| '+' | '.join(str(x.get(k,'')) for k in ['id','title','date','days','time','kind','color'])+' |' for x in loc['schedule'] if isinstance(x,dict)]
            lines += ['','## 할일',''] + [f"- {x.get('id','')}: {x.get('title','')}" for x in loc['todos'] if isinstance(x,dict)]
            outputs[country]='\n'.join(lines)+'\n'
    pending={root/'package.md':_package_md(package) if not errors else ''}; pending.update({root/c/'input.md':v for c,v in outputs.items()})
    conflicts = [str(path) for path, content in pending.items() if path.exists() and path.read_text() != content and not replace]
    errors.extend('출력 충돌 (덮어쓰기에는 --replace 필요): ' + path for path in conflicts)
    report = dict(passed=not errors, errors=errors, schedule_count=len(schedule), todo_count=len(todos), kinds=dict(kinds), scope='구조 및 JP/TW 한국어 원문 복사 검증. 번역 품질·치환 출처·날짜 표시·캡션 문체는 review-localization/review-caption에서 별도 검수')
    (root/'assembly-check.json').write_text(_text(report))
    if errors: raise ValueError('; '.join(errors))
    for path,content in pending.items():
        if path.exists() and path.read_text()!=content and not replace: raise ValueError(f'기존 출력 불일치 (덮어쓰기에는 --replace 필요): {path.name}')
    for path,content in pending.items(): _write(path,content,replace)
    return report


if __name__ == '__main__':
    import argparse
    p=argparse.ArgumentParser(); p.add_argument('root'); p.add_argument('--replace',action='store_true'); a=p.parse_args()
    try: print(json.dumps(build(a.root, a.replace), ensure_ascii=False, indent=2))
    except (ValueError,OSError) as exc: print('조립 실패: '+str(exc),file=sys.stderr); sys.exit(1)
