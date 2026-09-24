#!/usr/bin/env python3
"""거시 지표 원계열을 FRED(미국)·ECOS(한국)에서 받아 파일로 저장하고, 저장한 파일에서 계산한다.

이 저장소에서 **거시는 기본값이 "안 씀"이다.** 쓰는 조건은 `daily-brief` 스킬 4단계
"거시 지표"에 있다 — 회사의 공시가 그 변수를 언급했거나 사업 구조상 기계적으로 연결될
때만 쓴다. 연결고리를 공시에서 찾지 못하면 이 스크립트를 부르지 않는다.
거시는 어느 종목에나 갖다 붙일 수 있어서, 게이트가 없으면 매수 서사에 맞는 지표만 고르게 된다.

사용법
------
    # 미국 — FRED 시리즈 ID로
    python3 scripts/fetch_macro.py fred DGS10 --start 2023-09-24 -o "$SCRATCH/macro-DGS10.json"

    # 한국 — ECOS 통계표/주기/항목 (슬래시로 붙인다. 항목은 4개까지)
    python3 scripts/fetch_macro.py ecos 722Y001/D/0101000 --start 2023-09-24 \
        -o "$SCRATCH/macro-baserate.json"

    # 코드를 모를 때 — 네트워크만 쓰고 파일은 만들지 않는다
    python3 scripts/fetch_macro.py fred --find "mortgage rate"
    python3 scripts/fetch_macro.py ecos --find "환율"          # 통계표 찾기
    python3 scripts/fetch_macro.py ecos --find 731Y001         # 그 표의 항목 코드 찾기

    # 이미 받아둔 원자료에서 다시 계산 (네트워크 없음)
    python3 scripts/fetch_macro.py fred --from-file "$SCRATCH/macro-DGS10.json"

**코드를 기억에서 쓰지 않는다.** 시리즈 ID·항목 코드는 `--find`로 확인한 것만 쓴다.
ECOS 항목 코드는 통계표마다 달라 추측이 특히 위험하다.

API 키
------
`FRED_API_KEY` / `ECOS_API_KEY`를 **환경변수에서 먼저** 찾고, 없으면 저장소 루트의 `.env`에서
읽는다. 무인 실행 환경에는 `.env`가 없고 키가 환경변수로 주입되며, 사람이 쓰는 로컬에는
`.env`만 있다 — 양쪽에서 같은 명령이 돌게 하려고 둘 다 본다.
`.env`는 값에 공백이 있어 셸로 `source` 할 수 없으므로 직접 파싱한다.

**키가 없으면 그 경로를 포기한다.** 거시 없이 브리프를 쓰고, 그 사실을 "방법론 · 재현"에 적는다.

저장되는 것
    `raw` 키 아래는 API 응답 **원문 그대로**다. 가공하지 않는다. 그 바깥은 수집 메타
    (조회 시각, 호출 URL — 키는 가린다)로, 나중에 값이 의심스러울 때 되짚기 위한 것이다.
    ECOS 응답에는 조회 시각도 개정 이력도 없어서, 메타 없이는 언제 받은 값인지 알 방법이 없다.

개정(revision) 주의
    **거시 지표는 나중에 값이 바뀐다.** 같은 관측일의 값이 다음 달에 달라질 수 있다.
    그래서 보고서에는 **관측일과 조회일을 함께** 적는다 — 가격 데이터에 기준 종가일을
    적는 것과 같은 이유다.
    FRED는 `realtime_start`/`realtime_end`(vintage)와 `last_updated`를 주므로 이 스크립트가
    요약에 찍는다. **ECOS는 그 정보를 주지 않는다** — 조회일만이 유일한 단서다.

한계
    - 값을 변환하지 않는다. FRED의 `units`(pch, pc1 …)를 쓰지 않고 원계열만 받는다.
      증감률은 저장된 원자료에서 이 스크립트가 계산한다 — 남이 계산한 값을 받아 쓰지 않는
      이 저장소의 규칙(AGENTS.md "5단계")은 거시에도 같이 적용된다.
    - 발표 지연이 있다. 일별 계열도 07:00 KST 실행 시점엔 1~3영업일 전이 최신이고,
      월별 계열은 한 달 이상 밀린다. 요약의 `[주의] 최신 관측이 N일 전`을 보고 판단한다.
    - ECOS 주기별 날짜 형식은 D=YYYYMMDD, M=YYYYMM, Q=YYYYQ1, A=YYYY다.
      `--start/--end`에 `YYYY-MM-DD`를 주면 주기에 맞춰 변환하고, 그 밖의 주기(SM 등)는
      변환하지 않으므로 해당 주기의 형식 그대로 넘긴다.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

KST = timezone(timedelta(hours=9))
UA = "daily-equity-brief/1.0"
ENV_PATH = Path(__file__).resolve().parent.parent / ".env"

FRED_BASE = "https://api.stlouisfed.org/fred"
ECOS_BASE = "https://ecos.bok.or.kr/api"

# ECOS 주기 → --start/--end 의 YYYY-MM-DD 를 바꿀 형식. 없는 주기는 변환하지 않고 그대로 넘긴다.
ECOS_CYCLE_FMT = {"D": "day", "M": "month", "Q": "quarter", "A": "year"}


def read_key(name: str) -> str:
    """환경변수 우선, 없으면 .env. 둘 다 없으면 사유를 적고 멈춘다."""
    if (v := os.environ.get(name, "").strip()):
        return v
    if ENV_PATH.exists():
        for line in ENV_PATH.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line.startswith(f"{name}=") and not line.startswith("#"):
                if (v := line.split("=", 1)[1].strip()):
                    return v
    sys.exit(f"[중단] {name}가 환경변수에도 {ENV_PATH}에도 없다.\n"
             "거시 없이 브리프를 쓰고, 키가 없어 생략했다는 사실을 '방법론 · 재현'에 적는다.")


def get_json(url: str, what: str) -> dict:
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", "replace")[:300]
        sys.exit(f"[실패] {what} — HTTP {e.code}\n  {body}")
    except urllib.error.URLError as e:
        sys.exit(f"[실패] {what} — {e.reason}")


def redact(url: str, key: str) -> str:
    return url.replace(key, "REDACTED") if key else url


# ─────────────────────────────── FRED ───────────────────────────────

def fred_fetch(series_id: str, start: str, end: str, key: str) -> tuple[dict, list[str]]:
    meta_url = f"{FRED_BASE}/series?" + urllib.parse.urlencode(
        {"series_id": series_id, "api_key": key, "file_type": "json"})
    obs_url = f"{FRED_BASE}/series/observations?" + urllib.parse.urlencode(
        {"series_id": series_id, "api_key": key, "file_type": "json",
         "observation_start": start, "observation_end": end, "sort_order": "asc"})

    meta = get_json(meta_url, f"FRED 시리즈 메타 {series_id}")
    if "error_message" in meta:
        sys.exit(f"[실패] FRED — {meta['error_message']}\n(시리즈 ID는 --find 로 확인한다)")
    obs = get_json(obs_url, f"FRED 관측치 {series_id}")
    if "error_message" in obs:
        sys.exit(f"[실패] FRED — {obs['error_message']}")

    return ({"series": meta, "observations": obs},
            [redact(meta_url, key), redact(obs_url, key)])


def fred_find(text: str, key: str) -> None:
    url = f"{FRED_BASE}/series/search?" + urllib.parse.urlencode(
        {"search_text": text, "api_key": key, "file_type": "json",
         "limit": 15, "order_by": "popularity", "sort_order": "desc"})
    d = get_json(url, "FRED 시리즈 검색")
    rows = d.get("seriess") or []
    if not rows:
        sys.exit(f"[없음] '{text}'로 찾은 시리즈가 없다.")
    print(f"FRED 시리즈 검색 '{text}' — 인기순 상위 {len(rows)}건\n")
    for r in rows:
        print(f"  {r['id']:<18} {r.get('frequency_short','?'):<3} "
              f"{r.get('units_short','?'):<12} {r.get('seasonal_adjustment_short','?'):<4} "
              f"~{r.get('observation_end','?')}")
        print(f"  {'':<18} {r.get('title','')}")
    print("\n계절조정 여부(SA/NSA)와 단위를 확인하고 고른다. 보고서에는 시리즈 ID를 그대로 적는다.")


def fred_extract(raw: dict) -> dict:
    s = (raw["series"].get("seriess") or [{}])[0]
    pts = [(o["date"], float(o["value"]))
           for o in raw["observations"].get("observations", []) if o.get("value") not in (".", "", None)]
    return {
        "title": s.get("title", "?"),
        "units": s.get("units", "?"),
        "freq": s.get("frequency", "?"),
        "sa": s.get("seasonal_adjustment", "?"),
        "last_updated": s.get("last_updated"),
        "vintage": (raw["observations"].get("realtime_start"), raw["observations"].get("realtime_end")),
        "points": pts,
        "missing": sum(1 for o in raw["observations"].get("observations", []) if o.get("value") == "."),
    }


# ─────────────────────────────── ECOS ───────────────────────────────

def ecos_spec(spec: str) -> tuple[str, str, list[str]]:
    parts = [p for p in spec.split("/") if p]
    if len(parts) < 2:
        sys.exit("[중단] ECOS 시리즈는 `통계표/주기[/항목1[/항목2…]]` 형식이다 (예: 722Y001/D/0101000).\n"
                 "항목 코드는 `--find <통계표코드>`로 확인한다.")
    return parts[0], parts[1].upper(), parts[2:]


def ecos_date(s: str, cycle: str) -> str:
    """YYYY-MM-DD를 주기 형식으로. 이미 주기 형식이면 그대로 둔다."""
    try:
        d = datetime.strptime(s, "%Y-%m-%d").date()
    except ValueError:
        return s  # 주기 고유 형식(예: 2025Q1)을 사람이 직접 준 경우
    kind = ECOS_CYCLE_FMT.get(cycle)
    if kind == "day":
        return d.strftime("%Y%m%d")
    if kind == "month":
        return d.strftime("%Y%m")
    if kind == "quarter":
        return f"{d.year}Q{(d.month - 1) // 3 + 1}"
    if kind == "year":
        return str(d.year)
    sys.exit(f"[중단] 주기 '{cycle}'의 날짜 형식을 모른다 — --start/--end 를 그 주기의 형식 그대로 준다.")


def ecos_check(d: dict, what: str) -> dict:
    if "RESULT" in d:
        r = d["RESULT"]
        sys.exit(f"[실패] {what} — ECOS {r.get('CODE')}: {r.get('MESSAGE')}")
    return d


def ecos_fetch(spec: str, start: str, end: str, rows: int, key: str) -> tuple[dict, list[str]]:
    stat, cycle, items = ecos_spec(spec)
    bgn, fin = ecos_date(start, cycle), ecos_date(end, cycle)
    path = "/".join([ECOS_BASE, "StatisticSearch", key, "json", "kr", "1", str(rows), stat, cycle, bgn, fin] + items)
    d = ecos_check(get_json(path, f"ECOS {spec}"), f"ECOS {spec}")
    return {"search": d}, [redact(path, key)]


def ecos_find(q: str, key: str) -> None:
    # 통계표 코드처럼 생겼으면 그 표의 항목을, 아니면 통계표 이름을 찾는다.
    if q[:3].isdigit() and "Y" in q.upper():
        url = f"{ECOS_BASE}/StatisticItemList/{key}/json/kr/1/100/{q.upper()}"
        d = ecos_check(get_json(url, f"ECOS 항목 목록 {q}"), f"ECOS 항목 목록 {q}")
        rows = d.get("StatisticItemList", {}).get("row") or []
        print(f"ECOS 통계표 {q.upper()} 항목 {len(rows)}건 (주기별로 같은 코드가 여러 줄 나온다)\n")
        for r in rows[:60]:
            print(f"  {r.get('ITEM_CODE',''):<12} {r.get('CYCLE',''):<3} "
                  f"{r.get('UNIT_NAME',''):<10} {r.get('ITEM_NAME','')}")
        print(f"\n시리즈 지정: {q.upper()}/<주기>/<항목코드>")
        return

    url = f"{ECOS_BASE}/StatisticTableList/{key}/json/kr/1/844/"
    d = ecos_check(get_json(url, "ECOS 통계표 목록"), "ECOS 통계표 목록")
    rows = [r for r in d.get("StatisticTableList", {}).get("row") or []
            if q in (r.get("STAT_NAME") or "") and r.get("SRCH_YN") == "Y"]
    if not rows:
        sys.exit(f"[없음] '{q}'를 이름에 담은 검색 가능 통계표가 없다.")
    print(f"ECOS 통계표 검색 '{q}' — {len(rows)}건\n")
    for r in rows[:40]:
        print(f"  {r.get('STAT_CODE',''):<12} {r.get('CYCLE') or '?':<4} {r.get('STAT_NAME','')}")
    print("\n항목 코드는 `--find <통계표코드>`로 다시 찾는다.")


def ecos_extract(raw: dict) -> dict:
    rows = raw["search"].get("StatisticSearch", {}).get("row") or []
    pts = [(r["TIME"], float(r["DATA_VALUE"])) for r in rows if r.get("DATA_VALUE") not in (None, "")]
    first = rows[0] if rows else {}
    name = " / ".join(filter(None, (first.get(f"ITEM_NAME{i}") for i in range(1, 5))))
    return {
        "title": f"{first.get('STAT_NAME','?')} — {name}" if name else first.get("STAT_NAME", "?"),
        "units": first.get("UNIT_NAME", "?"),
        "freq": "?", "sa": "?", "last_updated": None, "vintage": (None, None),
        "points": pts,
        "missing": len(rows) - len(pts),
    }


# ───────────────────────── 요약 · 계산 ─────────────────────────

def norm_day(t: str) -> date | None:
    """관측 시점 문자열을 날짜로. 월·분기·연 계열은 해당 기간의 첫날로 본다.

    **길이로 먼저 가른다.** strptime은 자릿수에 관대해서 `%Y%m%d`가 "202512"를
    2025-01-02로 읽어버린다 — 그러면 월별 계열의 전년 대비가 엉뚱한 달과 비교된다.
    """
    if len(t) == 10 and t[4] == "-":                      # 2026-09-22 (FRED)
        fmt = "%Y-%m-%d"
    elif len(t) == 8 and t.isdigit():                     # 20260922 (ECOS D)
        fmt = "%Y%m%d"
    elif len(t) == 6 and t.isdigit():                     # 202608 (ECOS M)
        fmt = "%Y%m"
    elif len(t) == 6 and t[4].upper() == "Q":             # 2026Q3 (ECOS Q)
        return date(int(t[:4]), (int(t[5]) - 1) * 3 + 1, 1)
    elif len(t) == 4 and t.isdigit():                     # 2026 (ECOS A)
        return date(int(t), 1, 1)
    else:
        return None
    try:
        return datetime.strptime(t, fmt).date()
    except ValueError:
        return None


def pct(new: float, old: float) -> str:
    return f"{(new - old) / abs(old) * 100:+.2f}%" if old else "계산 불가(기준값 0)"


def report(env: dict) -> None:
    ex = env["extract"]
    pts = ex["points"]
    if not pts:
        sys.exit("[실패] 관측치가 비어 있다 — 기간이나 코드를 다시 확인한다.")

    last_t, last_v = pts[-1]
    print(f"계열: {ex['title']}")
    print(f"단위: {ex['units']}" + (f" · 주기 {ex['freq']} · 계절조정 {ex['sa']}" if ex["freq"] != "?" else ""))
    print(f"구간: {pts[0][0]} ~ {last_t} ({len(pts)}개 관측, 결측 {ex['missing']}개)")
    print(f"최신 관측: {last_t} = {last_v:g}")

    # 변화 — 저장된 원자료에서 직접 계산한다. API가 계산해 준 값을 받지 않는다.
    lastd = norm_day(last_t)
    if lastd:
        for label, days in (("1개월 전", 30), ("3개월 전", 91), ("1년 전", 365)):
            target = lastd - timedelta(days=days)
            prior = [(t, v) for t, v in pts[:-1] if (d := norm_day(t)) and d <= target]
            if prior:
                pt, pv = prior[-1]
                print(f"  {label:<7} 대비: {pt} {pv:g} → {last_v - pv:+g} ({pct(last_v, pv)})")

    vals = [v for _, v in pts]
    lo, hi = min(vals), max(vals)
    lo_t = next(t for t, v in pts if v == lo)
    hi_t = next(t for t, v in pts if v == hi)
    pos = (last_v - lo) / (hi - lo) * 100 if hi > lo else 0.0
    print(f"  구간 최저: {lo_t} {lo:g} · 최고: {hi_t} {hi:g} · 현재 위치 {pos:.1f}%")

    if ex["last_updated"]:
        print(f"계열 갱신: {ex['last_updated']}")
    if all(ex["vintage"]):
        print(f"vintage(realtime): {ex['vintage'][0]} ~ {ex['vintage'][1]}")
    else:
        print("[주의] 이 출처는 개정 이력(vintage)을 주지 않는다 — 조회일만이 단서다.")
    print(f"조회 시각: {env['fetched_at_kst']}")

    if lastd and (lag := (datetime.now(KST).date() - lastd).days) > 7:
        print(f"[주의] 최신 관측이 {lag}일 전({last_t})이다 — 발표 지연이다. "
              "보고서에는 조회일이 아니라 **관측일**을 기준으로 적는다.")
    print("\n보고서 표기: 값 · 관측일 · 조회일 · 출처(시리즈 ID)를 함께 적는다. "
          "이 값은 나중에 개정될 수 있다.")


def main() -> None:
    ap = argparse.ArgumentParser(description="FRED·ECOS 거시 원계열을 받아 저장하고 계산한다.")
    ap.add_argument("provider", choices=["fred", "ecos"])
    ap.add_argument("series", nargs="?", help="FRED: 시리즈 ID · ECOS: 통계표/주기/항목")
    ap.add_argument("--find", metavar="Q", help="코드 찾기 (파일을 만들지 않는다)")
    ap.add_argument("--from-file", metavar="PATH", help="저장된 원자료에서 다시 계산 (네트워크 없음)")
    ap.add_argument("--start", default=(date.today() - timedelta(days=365 * 3)).isoformat(),
                    help="YYYY-MM-DD (기본 3년 전)")
    ap.add_argument("--end", default=date.today().isoformat(), help="YYYY-MM-DD (기본 오늘)")
    ap.add_argument("--rows", type=int, default=10000, help="ECOS 최대 행 수 (기본 10000)")
    ap.add_argument("-o", "--out", help="저장 경로 (스크래치패드 권장)")
    ap.add_argument("--force", action="store_true", help="기존 파일 덮어쓰기")
    args = ap.parse_args()

    extract = fred_extract if args.provider == "fred" else ecos_extract

    if args.from_file:
        env = json.loads(Path(args.from_file).expanduser().read_text(encoding="utf-8"))
        env["extract"] = extract(env["raw"])
        print(f"원자료: {args.from_file}")
        report(env)
        return

    key = read_key("FRED_API_KEY" if args.provider == "fred" else "ECOS_API_KEY")

    if args.find:
        (fred_find if args.provider == "fred" else ecos_find)(args.find, key)
        return

    if not args.series or not args.out:
        ap.error("시리즈와 -o 가 필요하다 (코드를 찾으려면 --find, 재계산은 --from-file)")

    out = Path(args.out).expanduser()
    if out.exists() and not args.force:
        sys.exit(f"[중단] 이미 있는 파일이다: {out}\n덮어쓰려면 --force")

    if args.provider == "fred":
        raw, urls = fred_fetch(args.series, args.start, args.end, key)
    else:
        raw, urls = ecos_fetch(args.series, args.start, args.end, args.rows, key)

    env = {
        "provider": args.provider,
        "series": args.series,
        "requested": {"start": args.start, "end": args.end},
        "fetched_at_kst": f"{datetime.now(KST):%Y-%m-%d %H:%M KST}",
        "requests": urls,          # 키는 가려져 있다
        "raw": raw,                # 여기부터는 응답 원문 그대로다
    }
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(env, ensure_ascii=False), encoding="utf-8")

    env["extract"] = extract(raw)
    print(f"저장: {out}")
    report(env)


if __name__ == "__main__":
    main()
