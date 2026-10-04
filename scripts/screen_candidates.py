#!/usr/bin/env python3
"""공시 접수 목록에서 거른 회사들을 **거래대금으로 자르고 정해진 순서로 세워** 점수표에 올릴 후보를 고른다.

시장은 날짜로, 최종 선정은 점수표로 고정돼 있었지만 그 사이 — 접수 목록 수십~수백 건에서
3~5개를 추리는 단계 — 는 "눈으로 거르되"였다. 같은 날 다시 돌리면 다른 후보가 나올 수 있었고,
코스닥은 한 번도 후보에 오르지 않았다. 이 스크립트가 그 단계를 기계로 만든다.

사용법
------
    python3 scripts/screen_candidates.py <후보 TSV> -d "$SCRATCH/screen"
    python3 scripts/screen_candidates.py cand.tsv -d "$SCRATCH/screen" --exclude 003850,GIS

후보 TSV — 탭 구분, `#`로 시작하는 줄은 무시한다.

    티커        유형                     비율    접수일       메모
    034020.KS   단일판매ㆍ공급계약체결    4.91    2026-10-01   베트남 O Mon III EPC
    TSLA        8-K 2.02                         2026-10-02   3Q 생산·인도

    - **비율**은 공시 자체의 중요도 비율(%)이다. 어떻게 읽는지는 `daily-brief` 스킬 2단계
      "후보 추리기"가 마스터다. 비우면 비율 없는 행으로 정렬된다(미국은 비율을 쓰지 않는다).
    - 처음에는 비율을 비워 돌려 거래대금 컷부터 통과시키고, **통과한 행의 원문만 열어** 비율을
      채운 뒤 다시 돌린다. 원자료는 저장분을 다시 쓰므로 두 번째 실행은 네트워크를 쓰지 않는다.

판정 순서
    1. `--exclude`(7일 제외 목록)에 있으면 `7일 제외`. 티커의 `.KS`/`.KQ`는 떼고 비교한다.
    2. 3개월 일봉을 `<디렉터리>/<티커>-3mo.json`에 받는다(있으면 다시 받지 않는다).
       받지 못하면 `조회 실패`와 사유 — **조용히 빼지 않는다.**
    3. 미확정 봉을 `technicals.py`와 같은 판정으로 빼고, 20봉 평균 거래대금을 `technicals.compute`로
       잰다(점수표 ③과 같은 숫자다).
    4. 거래대금이 ③의 0점 경계 미만이면 `컷`. KRW 10억원 / USD $20M. 그 밖의 통화는 기준이 없어 `컷`.
    5. 남은 행을 **Item 순위 → 비율 내림차순 → 거래대금 내림차순 → 티커 오름차순**으로 세우고
       위에서 `--top`개(기본 5)를 `후보`로 표시한다. Item 순위는 미국 8-K에만 있다 —
       2.02(실적) → 2.01(인수·매각 완료) → 1.01(중요 계약). 한 회사가 여러 Item을 냈으면 가장 앞선
       것으로 센다. 1.01은 대개 신용한도·사채 계약이라 거래대금 순으로만 세우면 실적 공시를 밀어낸다.

출력은 표 그대로 브리프 1절 "선정 과정"과 `## 방법론 · 재현`에 붙여넣는다. 손으로 고치지 않는다.
"""

from __future__ import annotations

import argparse
import re
import sys
import time
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent))
import fetch_ohlcv as F  # noqa: E402  — 수집·저장 경로를 한곳에 둔다
import technicals as T  # noqa: E402  — 원자료 적재·미확정 봉 판정·거래대금 계산을 재사용한다

# 점수표 ③유동성의 경계. 0점 경계 미만은 점수표에 올리지 않는다(컷).
# 스킬 3단계 ③ 기준과 같은 값이다 — 한쪽만 바꾸지 않는다.
LIQUIDITY = {
    "KRW": {"cut": 1e9, "top": 1e10},   # 10억원 미만 0 / 10~100억원 1 / 100억원 이상 2
    "USD": {"cut": 20e6, "top": 100e6},  # $20M 미만 0 / $20~100M 1 / $100M 이상 2
}
ITEM_ORDER = ("2.02", "2.01", "1.01")  # 미국 8-K Item 순위 — 스킬 2단계 "후보 추리기"와 같다
RANGE = "3mo"
PAUSE = 0.3  # 연속 조회 사이 간격(초). Yahoo 429를 피한다.


def base(ticker: str) -> str:
    return ticker.split(".")[0].upper()


def read_tsv(path: Path) -> list[dict]:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except FileNotFoundError:
        sys.exit(f"[실패] 후보 파일이 없다: {path}")
    rows = []
    for n, line in enumerate(lines, start=1):
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        cols = (line.split("\t") + [""] * 5)[:5]
        ticker, kind, ratio, filed, memo = (c.strip() for c in cols)
        if not ticker:
            sys.exit(f"[실패] {n}행에 티커가 없다.")
        try:
            r = float(ratio.replace(",", "").rstrip("%")) if ratio else None
        except ValueError:
            sys.exit(f"[실패] {n}행 비율이 숫자가 아니다: {ratio!r}")
        rows.append({"ticker": ticker, "kind": kind, "ratio": r, "filed": filed, "memo": memo})
    if not rows:
        sys.exit("[실패] 후보 파일에 행이 없다.")
    tickers = [base(r["ticker"]) for r in rows]
    dup = sorted({t for t in tickers if tickers.count(t) > 1})
    if dup:
        sys.exit(f"[실패] 같은 회사가 두 줄이다: {', '.join(dup)} — 공시가 여럿이면 한 줄로 합치고 메모에 적는다.")
    return rows


def reason(e: SystemExit) -> str:
    """스크립트들이 sys.exit에 담은 여러 줄 사유를 표 한 칸에 들어가게 줄인다."""
    return " ".join(x.strip() for x in str(e.code).splitlines()[:2]).replace("|", "/")[:160]


def fmt_turnover(x: float | None, cur: str | None) -> str:
    """컷 경계 근처가 반올림으로 뒤집혀 보이지 않게 소수 한 자리까지 적는다(10억원 미만이 '10억원'으로 보이던 문제)."""
    if x is None:
        return "—"
    if cur == "KRW":
        return f"{x / 1e8:,.1f}억원"
    return f"{'$' if cur == 'USD' else ''}{x / 1e6:,.1f}M"


def ensure(ticker: str, out: Path) -> tuple[bool, str | None]:
    """원자료가 없으면 받는다. (새로 받았는가, 실패 사유)."""
    if out.exists() and F.sidecar(out).exists():
        return False, None
    try:
        payload = F.fetch(ticker, RANGE, "1d")
    except SystemExit as e:  # fetch는 실패 시 사유를 담아 종료한다
        return True, reason(e)
    F.save(payload, out, ticker, RANGE, "1d")
    return True, None


def turnover(out: Path) -> tuple[float | None, str | None, str | None]:
    """(20봉 평균 거래대금, 통화, 실패 사유)."""
    try:
        data = T.load(out, "close")
    except SystemExit as e:
        return None, None, reason(e)
    T.drop_unconfirmed(data, "daily", data["as_of"])
    preset = T.PRESETS["daily"]
    args = SimpleNamespace(window=preset["window"], tol=preset["tol"], min_touches=2, far_pct=0.25)
    c = T.compute(data, preset, args)
    cur = (data["meta"].get("currency") or "").upper()
    if c["turnover20"] is None:
        return None, cur, "20봉 거래량 결측"
    return c["turnover20"], cur, None


def item_rank(kind: str) -> int:
    """8-K Item 순위. 8-K가 아니거나 목록에 없는 Item이면 0(순위 구분 없음)."""
    if not kind.startswith("8-K"):
        return 0
    found = [ITEM_ORDER.index(i) for i in re.findall(r"\d\.\d\d", kind) if i in ITEM_ORDER]
    return min(found) if found else len(ITEM_ORDER)


def score3(value: float, cur: str) -> int:
    lim = LIQUIDITY[cur]
    return 2 if value >= lim["top"] else 1 if value >= lim["cut"] else 0


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("tsv", type=Path, help="후보 TSV (티커 / 유형 / 비율 / 접수일 / 메모)")
    ap.add_argument("-d", "--dir", type=Path, required=True, help="원자료 저장 디렉터리 (스크래치패드)")
    ap.add_argument("--exclude", default="", help="7일 제외 목록 — 쉼표로 구분한 종목코드·티커")
    ap.add_argument("--top", type=int, default=5, help="후보로 표시할 개수 (기본 5)")
    args = ap.parse_args()

    rows = read_tsv(args.tsv)
    excluded = {base(t) for t in args.exclude.split(",") if t.strip()}
    fetched = 0

    for r in rows:
        r.update(value=None, cur=None, s3=None)
        if base(r["ticker"]) in excluded:
            r["verdict"] = "7일 제외"
            continue
        out = args.dir / f"{r['ticker']}-{RANGE}.json"
        new, err = ensure(r["ticker"], out)
        if new:
            fetched += 1
            time.sleep(PAUSE)
        if err:
            r["verdict"] = f"조회 실패 — {err}"
            continue
        value, cur, err = turnover(out)
        r.update(value=value, cur=cur)
        if err:
            r["verdict"] = f"조회 실패 — {err}"
        elif cur not in LIQUIDITY:
            r["verdict"] = f"컷 — 통화 {cur or '?'}의 기준이 없다"
        else:
            r["s3"] = score3(value, cur)
            r["verdict"] = "컷 — ③ 0점 경계 미만" if r["s3"] == 0 else "통과"

    passed = [r for r in rows if r["verdict"] == "통과"]
    passed.sort(key=lambda r: (item_rank(r["kind"]), r["ratio"] is None, -(r["ratio"] or 0), -r["value"], r["ticker"]))
    for i, r in enumerate(passed, start=1):
        r["rank"] = i
        if i <= args.top:
            r["verdict"] = "후보"
    others = sorted((r for r in rows if r not in passed), key=lambda r: r["ticker"])

    print("| 순위 | 티커 | 유형 | 비율 | 20봉 평균 거래대금 | ③ | 판정 | 접수일 | 메모 |")
    print("|---|---|---|---|---|---|---|---|---|")
    for r in passed + others:
        tv = fmt_turnover(r["value"], r["cur"])
        ratio = f"{r['ratio']:g}%" if r["ratio"] is not None else "—"
        s3 = "—" if r["s3"] is None else str(r["s3"])
        print(f"| {r.get('rank', '—')} | {r['ticker']} | {r['kind'] or '—'} | {ratio} | {tv} | {s3} | "
              f"{r['verdict']} | {r['filed'] or '—'} | {r['memo'] or '—'} |")

    n_cand = sum(1 for r in rows if r["verdict"] == "후보")
    print()
    print(f"- 입력 {len(rows)}건 · 통과 {len(passed)}건 · 후보 {n_cand}건 · 새로 조회 시도 {fetched}건 "
          f"(원자료 {args.dir}, {RANGE} 일봉)")
    print("- 컷 기준(③ 0점 경계): KRW 10억원 / USD $20M · 정렬: 8-K Item(2.02→2.01→1.01) → 비율 ↓ → 20봉 거래대금 ↓ → 티커 ↑")
    if n_cand < 3:
        print("- [주의] 후보가 3건 미만이다 — 스킬 2단계 \"뉴스 보강\"으로 채우고 그 사실을 기록한다.")


if __name__ == "__main__":
    main()
