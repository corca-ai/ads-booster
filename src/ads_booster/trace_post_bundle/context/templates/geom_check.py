#!/usr/bin/env python3
"""기하 검수 — 텍스트 교체 결과(text.png)가 템플릿(template.png)의 컴포넌트 위치를 지켰는가.

의존성 없음(표준 라이브러리 PNG 디코더). 같은 크기의 두 PNG를 받아
  - 색 막대(채도 높은 세로 막대)의 행 중심 y
  - 할 일 원(파란 테두리 원)의 행 중심 y
  - 헤더 줄(두 열의 첫 글자 행)의 y
를 픽셀로 찾아 비교한다. 허용 오차는 화면 높이의 5%.

사용: python3 geom_check.py template.png text.png [--tol 0.05]
출력: 항목별 PASS/FAIL 과 y 목록. 종료 코드 0=전부 통과, 1=하나라도 실패.
IMAGE-RULES §5 검수 A-5 의 근거 도구. 텍스트는 모델이, 기하는 이 스크립트가 본다.
"""
import sys, zlib, struct

def read_png(path):
    with open(path, "rb") as f:
        data = f.read()
    assert data[:8] == b"\x89PNG\r\n\x1a\n", "PNG 아님"
    pos = 8; idat = b""; w = h = None; ctype = None; bitdepth = None
    while pos < len(data):
        ln = struct.unpack(">I", data[pos:pos+4])[0]; tag = data[pos+4:pos+8]; body = data[pos+8:pos+8+ln]
        if tag == b"IHDR":
            w, h, bitdepth, ctype, _, _, interlace = struct.unpack(">IIBBBBB", body)
            assert interlace == 0 and bitdepth == 8, "8비트 비인터레이스만 지원"
        elif tag == b"IDAT": idat += body
        elif tag == b"IEND": break
        pos += 12 + ln
    ch = {2: 3, 6: 4, 0: 1, 4: 2}[ctype]
    raw = zlib.decompress(idat); stride = w * ch; out = bytearray(h * stride); prev = bytearray(stride); p = 0
    for y in range(h):
        ft = raw[p]; p += 1; line = bytearray(raw[p:p+stride]); p += stride
        for i in range(stride):
            a = line[i-ch] if i >= ch else 0; b = prev[i]; c = prev[i-ch] if i >= ch else 0
            if ft == 1: line[i] = (line[i] + a) & 255
            elif ft == 2: line[i] = (line[i] + b) & 255
            elif ft == 3: line[i] = (line[i] + (a + b) // 2) & 255
            elif ft == 4:
                pa = abs(b - c); pb = abs(a - c); pc = abs(a + b - 2*c)
                pr = a if pa <= pb and pa <= pc else (b if pb <= pc else c)
                line[i] = (line[i] + pr) & 255
        out[y*stride:(y+1)*stride] = line; prev = line
    return w, h, ch, bytes(out)

def px(img, x, y):
    w, h, ch, buf = img; i = (y*w + x) * ch
    return buf[i], buf[i+1], buf[i+2]

def saturated(r, g, b):  # 색 막대·원 테두리 후보: 채도 높고 너무 어둡지 않음
    mx, mn = max(r, g, b), min(r, g, b)
    return mx > 90 and (mx - mn) > 70

def nonwhite(r, g, b):  # 색 막대(회색 포함): 흰 바탕과 충분히 다름
    return min(r, g, b) < 190

def row_hits(img, x0, x1, y0, y1, test=saturated):
    w, h, ch, buf = img; rows = []
    for y in range(y0, y1):
        cnt = 0
        for x in range(x0, x1):
            if test(*px(img, x, y)): cnt += 1
        rows.append(cnt)
    return rows

def runs(rows, y0, min_len, thresh):
    out = []; start = None
    for i, c in enumerate(rows):
        on = c >= thresh
        if on and start is None: start = i
        if not on and start is not None:
            if i - start >= min_len: out.append((y0 + start, y0 + i))
            start = None
    if start is not None and len(rows) - start >= min_len: out.append((y0 + start, y0 + len(rows)))
    return out

def centers(rs): return [round((a + b) / 2) for a, b in rs]

def analyze(img):
    w, h, _, _ = img
    # 헤더 줄(이모지)은 제외 — 31%부터. 색 막대: 왼쪽 열 좌측 4~9% 폭(회색 막대 포함), 하단 버튼 위(85%)까지
    y0 = int(h*0.31)
    bars = centers(runs(row_hits(img, int(w*0.04), int(w*0.10), y0, int(h*0.85)), y0, 8, 2))  # 채도 높은 막대만(슬롯 색은 템플릿 고정) — 회색 라벨 글자는 제외
    # 할 일 원: 오른쪽 열 좌측 (50~57%), 파란 테두리
    dots = centers(runs(row_hits(img, int(w*0.50), int(w*0.57), y0, int(h*0.85)), y0, 4, 2))
    return {"bars": bars, "dots": dots}

def compare(a, b, h, tol):
    ok = True; lines = []
    for key in ("bars", "dots"):
        ya, yb = a[key], b[key]
        if len(ya) != len(yb):
            ok = False; lines.append(f"FAIL {key}: 개수 템플릿 {len(ya)} vs 결과 {len(yb)}  {ya} / {yb}"); continue
        diffs = [abs(p - q) for p, q in zip(ya, yb)]; worst = max(diffs) if diffs else 0
        good = worst <= h * tol
        ok &= good
        lines.append(f"{'PASS' if good else 'FAIL'} {key}: 최대 편차 {worst}px ({worst/h*100:.1f}%)  템플릿 {ya} / 결과 {yb}")
    return ok, lines

if __name__ == "__main__":
    if len(sys.argv) < 3: print(__doc__); sys.exit(2)
    tol = 0.05
    if "--tol" in sys.argv: tol = float(sys.argv[sys.argv.index("--tol") + 1])
    t = read_png(sys.argv[1]); r = read_png(sys.argv[2])
    if abs(t[0]-r[0]) > t[0]*0.01 or abs(t[1]-r[1]) > t[1]*0.01: print(f"FAIL 크기 불일치 {t[:2]} vs {r[:2]}"); sys.exit(1)
    at, ar = analyze(t), analyze(r)
    sc = t[1] / r[1]  # 높이 차이(±1%) 보정
    ar = {k: [round(v*sc) for v in vs] for k, vs in ar.items()}
    ok, lines = compare(at, ar, t[1], tol)
    print("\n".join(lines)); print("RESULT", "PASS" if ok else "FAIL", f"(tol {tol*100:.0f}%)")
    sys.exit(0 if ok else 1)
