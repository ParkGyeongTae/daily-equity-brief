#!/usr/bin/env python3
"""Yahoo Finance 차트 API에서 OHLCV 원자료를 받아 파일로 저장한다.

이 저장소의 규칙(AGENTS.md "4단계 — 수집")상 **지표는 화면에 뜬 값이 아니라
저장된 원자료에서 계산한다.** 이 스크립트는 그 원자료를 만드는 단계만 담당하고,
지표 계산은 `technicals.py`가 맡는다.

사용법
------
    python3 scripts/fetch_ohlcv.py NVDA --range 2y --interval 1d -o "$SCRATCH/NVDA-1d.json"
    python3 scripts/fetch_ohlcv.py 005930.KS --range 5y --interval 1wk -o "$SCRATCH/005930-1wk.json"

티커 표기
    미국: NVDA, AAPL …
    한국: 유가증권 `005930.KS` / 코스닥 `247540.KQ`

저장되는 것
    Yahoo가 준 JSON 응답 **원문 그대로**. 가공하지 않는다 — 나중에 숫자가 의심스러울 때
    되짚을 수 있어야 하기 때문이다. 표준 출력에는 검증용 요약(행 수, 기간, 마지막 종가,
    조회 시각)만 찍는다.

수정주가 주의
    Yahoo 차트 API의 OHLC는 **주식분할은 반영, 배당은 미반영**이고,
    `adjclose`는 **분할·배당을 모두 반영**한 값이다. 어느 쪽으로 지표를 계산했는지는
    `technicals.py --price-field`로 정하고 보고서 "방법론 · 재현"에 반드시 남긴다.

한계
    - 무료·비공식 엔드포인트다. 429(요청 과다)나 스키마 변경으로 언제든 깨질 수 있다.
    - 상장폐지·티커 변경 종목은 빈 응답이 온다. 그때는 종목 코드부터 다시 확인한다.
    - 장중에 받으면 마지막 봉이 미완성이다. 아래 요약의 "마지막 봉"이 오늘 날짜면
      종가가 확정되지 않았다는 뜻이므로 보고서 기준일로 쓰지 않는다.
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

# 호스트 둘은 같은 데이터를 주는 미러다. 하나가 429면 다른 쪽을 시도한다.
CHART_HOSTS = ("https://query2.finance.yahoo.com", "https://query1.finance.yahoo.com")
CHART_PATH = "/v8/finance/chart/{ticker}"
# UA를 실제 브라우저처럼 길게 쓰면 쿠키·crumb를 요구하며 429로 막힌다. 짧은 UA가 통과한다.
UA = "Mozilla/5.0"
KST = timezone(timedelta(hours=9))

RANGES = ["1mo", "3mo", "6mo", "1y", "2y", "5y", "10y", "ytd", "max"]
INTERVALS = ["1d", "1wk", "1mo"]


def fetch(ticker: str, rng: str, interval: str) -> dict:
    query = f"?range={rng}&interval={interval}&events=div%2Csplit"
    payload = None
    errors = []
    for host in CHART_HOSTS:
        url = host + CHART_PATH.format(ticker=ticker) + query
        req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                payload = json.loads(resp.read().decode("utf-8"))
            break
        except urllib.error.HTTPError as e:
            body = e.read().decode("utf-8", "replace")[:200]
            errors.append(f"{host} → HTTP {e.code} {body}")
            if e.code == 404:
                break  # 티커 문제라 다른 호스트도 같다
        except urllib.error.URLError as e:
            errors.append(f"{host} → {e.reason}")
    if payload is None:
        sys.exit("[실패] 원자료를 받지 못했다:\n  " + "\n  ".join(errors)
                 + "\n(429면 잠시 뒤 재시도, 404면 티커 표기(.KS/.KQ)를 확인한다)")

    chart = payload.get("chart") or {}
    if chart.get("error"):
        sys.exit(f"[실패] Yahoo 오류 응답 — {json.dumps(chart['error'], ensure_ascii=False)}")
    results = chart.get("result") or []
    if not results:
        sys.exit("[실패] 결과가 비어 있다 — 티커 표기(.KS/.KQ 포함)를 확인한다.")
    return payload


def summarize(payload: dict) -> dict:
    r = payload["chart"]["result"][0]
    meta = r.get("meta", {})
    ts = r.get("timestamp") or []
    quote = (r.get("indicators", {}).get("quote") or [{}])[0]
    closes = quote.get("close") or []
    tz = meta.get("exchangeTimezoneName", "?")

    rows = [(t, c) for t, c in zip(ts, closes) if c is not None]
    if not rows:
        sys.exit("[실패] 종가가 모두 비어 있다 — 거래 이력이 없는 구간이다.")

    def day(epoch: int) -> str:
        return datetime.fromtimestamp(epoch, timezone.utc).astimezone(KST).strftime("%Y-%m-%d")

    has_adj = bool((r.get("indicators", {}).get("adjclose") or [{}])[0].get("adjclose"))
    return {
        "symbol": meta.get("symbol"),
        "currency": meta.get("currency"),
        "exchange": meta.get("fullExchangeName") or meta.get("exchangeName"),
        "timezone": tz,
        "rows": len(rows),
        "null_rows": len(ts) - len(rows),
        "first": day(rows[0][0]),
        "last": day(rows[-1][0]),
        "last_close": rows[-1][1],
        "has_adjclose": has_adj,
    }


def main() -> None:
    ap = argparse.ArgumentParser(description="Yahoo Finance OHLCV 원자료를 내려받아 저장한다.")
    ap.add_argument("ticker", help="예: NVDA, 005930.KS, 247540.KQ")
    ap.add_argument("--range", dest="rng", default="2y", choices=RANGES, help="기본 2y")
    ap.add_argument("--interval", default="1d", choices=INTERVALS, help="기본 1d")
    ap.add_argument("-o", "--out", required=True, help="저장 경로 (스크래치패드 권장)")
    ap.add_argument("--force", action="store_true", help="기존 파일 덮어쓰기")
    args = ap.parse_args()

    out = Path(args.out).expanduser()
    if out.exists() and not args.force:
        sys.exit(f"[중단] 이미 있는 파일이다: {out}\n덮어쓰려면 --force")

    payload = fetch(args.ticker, args.rng, args.interval)
    s = summarize(payload)

    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

    now = datetime.now(KST).strftime("%Y-%m-%d %H:%M KST")
    today = datetime.now(KST).strftime("%Y-%m-%d")
    print(f"저장: {out}")
    print(f"종목: {s['symbol']} · {s['exchange']} · {s['currency']} · 거래소 시간대 {s['timezone']}")
    print(f"구간: {s['first']} ~ {s['last']} ({args.interval}, {s['rows']}봉, 결측 {s['null_rows']}봉)")
    print(f"마지막 봉 종가: {s['last_close']}")
    print(f"adjclose 포함: {'예' if s['has_adjclose'] else '아니오'}  (OHLC는 분할 반영·배당 미반영)")
    print(f"조회 시각: {now}")
    if s["last"] == today:
        print("[주의] 마지막 봉이 오늘이다 — 장중이면 종가 미확정이므로 보고서 기준일로 쓰지 않는다.")


if __name__ == "__main__":
    main()
