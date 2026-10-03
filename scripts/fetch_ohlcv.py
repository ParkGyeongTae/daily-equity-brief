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

    원자료 옆에 **조회 기록**(`<이름>.fetch.json` — 조회 시각·요청)을 함께 남긴다. 마지막 봉이
    확정이었는지는 "받은 순간"에 정해지는 사실인데 응답 원문에는 그 시각이 없다. 계산 시각으로
    판정하면 같은 파일을 다음 주에 다시 넣었을 때 판정이 바뀌어 값이 달라진다. 그래서
    `technicals.py`는 이 기록의 시각으로 판정한다.

수정주가 주의
    Yahoo 차트 API의 OHLC는 **주식분할은 반영, 배당은 미반영**이고,
    `adjclose`는 **분할·배당을 모두 반영**한 값이다. 어느 쪽으로 지표를 계산했는지는
    `technicals.py --price-field`로 정하고 보고서 "방법론 · 재현"에 반드시 남긴다.

한계
    - 무료·비공식 엔드포인트다. 429(요청 과다)나 스키마 변경으로 언제든 깨질 수 있다.
      429는 호스트마다 한 번 쉬었다가 다시 시도한다.
    - 상장폐지·티커 변경 종목은 빈 응답이 온다. 그때는 종목 코드부터 다시 확인한다.
    - 장중에 받으면 마지막 봉이 미완성이다. 일봉의 확정 여부는 **거래소의 정규장 시간**으로
      판정해 `[주의] 마지막 봉이 미확정이다`를 찍는다. 그 경고가 뜨면 보고서 기준일로 쓰지 않는다.
      (날짜 비교로는 미국 장이 KST 자정을 넘겨 열려 있는 00:00~05:00을 놓친다.)
    - `1wk`는 **장이 닫혀 있어도** 진행 중인 주의 봉이 미완성이므로 정규장 시간이 아니라
      봉이 속한 주로 판정한다. 거래소 현지 토·일이면 그 주가 끝난 것으로 본다.
      게다가 Yahoo는 진행 중인 주의 봉을 하나 더 덧붙여 같은 주를 두 번 담아 보내는 일이 있다 — 그 중복은 `technicals.py --drop-unconfirmed`가 뺀다.
      **원자료 JSON을 손으로 잘라내지 않는다.**
    - 월봉(`1mo`)은 받지 않는다. `technicals.py`에 월봉 프리셋이 없어 받아도 계산할 수 없고,
      스킬도 쓰지 않는다.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo
from pathlib import Path

# 호스트 둘은 같은 데이터를 주는 미러다. 하나가 429면 다른 쪽을 시도한다.
CHART_HOSTS = ("https://query2.finance.yahoo.com", "https://query1.finance.yahoo.com")
CHART_PATH = "/v8/finance/chart/{ticker}"
# UA를 실제 브라우저처럼 길게 쓰면 쿠키·crumb를 요구하며 429로 막힌다. 짧은 UA가 통과한다.
UA = "Mozilla/5.0"
KST = timezone(timedelta(hours=9))

# 429일 때 같은 호스트를 다시 시도하기 전에 쉬는 초.
RETRY_WAIT = 3.0

RANGES = ["1mo", "3mo", "6mo", "1y", "2y", "5y", "10y", "ytd", "max"]
INTERVALS = ["1d", "1wk"]


def fetch(ticker: str, rng: str, interval: str) -> dict:
    query = f"?range={rng}&interval={interval}&events=div%2Csplit"
    payload = None
    errors = []
    stop = False
    for host in CHART_HOSTS:
        url = host + CHART_PATH.format(ticker=ticker) + query
        req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/json"})
        for attempt in (1, 2):
            try:
                with urllib.request.urlopen(req, timeout=30) as resp:
                    body = resp.read().decode("utf-8", "replace")
                payload = json.loads(body)
                break
            except json.JSONDecodeError:
                # 차단 페이지(HTML)가 200으로 오는 일이 있다. 트레이스백 대신 사유를 남긴다.
                errors.append(f"{host} → JSON이 아닌 응답: {body[:120]!r}")
                break
            except urllib.error.HTTPError as e:
                errors.append(f"{host} → HTTP {e.code} {e.read().decode('utf-8', 'replace')[:200]}")
                if e.code == 404:
                    stop = True  # 티커 문제라 다른 호스트도 같다
                    break
                if e.code == 429 and attempt == 1:
                    time.sleep(RETRY_WAIT)
                    continue
                break
            except urllib.error.URLError as e:
                errors.append(f"{host} → {e.reason}")
                break
        if payload is not None or stop:
            break
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


def sidecar(out: Path) -> Path:
    """원자료 옆의 조회 기록 경로 — `X-1d.json` → `X-1d.fetch.json`."""
    return out.with_name(out.stem + ".fetch.json")


def save(payload: dict, out: Path, ticker: str, rng: str, interval: str) -> datetime:
    """원자료를 원문 그대로 저장하고, 조회 시각을 옆 파일에 남긴다. 조회 시각을 돌려준다."""
    now = datetime.now(timezone.utc)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    sidecar(out).write_text(json.dumps({
        "fetched_at": now.isoformat(timespec="seconds"),
        "ticker": ticker, "range": rng, "interval": interval,
    }, ensure_ascii=False), encoding="utf-8")
    return now


def fetched_at(out: Path) -> datetime | None:
    """조회 기록의 시각. 기록이 없거나 읽지 못하면 None — 호출자가 그 사실을 남긴다."""
    try:
        return datetime.fromisoformat(json.loads(sidecar(out).read_text(encoding="utf-8"))["fetched_at"])
    except (OSError, ValueError, KeyError, TypeError):
        return None


def week_closed(day) -> bool:
    """거래소 현지 날짜가 토·일이면 그 주의 거래는 끝났다."""
    return day.weekday() >= 5


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

    try:
        ex_tz = ZoneInfo(tz)
    except Exception:  # tzdata에 없는 이름이면 UTC로 떨어뜨린다
        ex_tz = timezone.utc

    def day(epoch: int) -> str:
        # 거래일은 거래소의 날짜다. KST로 환산하면 거래소마다 하루가 밀릴 수 있다.
        return datetime.fromtimestamp(epoch, timezone.utc).astimezone(ex_tz).strftime("%Y-%m-%d")

    period = (meta.get("currentTradingPeriod") or {}).get("regular") or {}
    session = (period["start"], period["end"]) if {"start", "end"} <= period.keys() else None

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
        "last_ts": rows[-1][0],
        "session": session,
        "ex_tz": ex_tz,
        "has_adjclose": has_adj,
    }


def unconfirmed_reason(s: dict, interval: str, now_epoch: float) -> str | None:
    """마지막 봉이 아직 확정되지 않았으면 사유를, 확정이면 None을 돌려준다.

    일봉은 거래소의 **정규장 구간**(meta.currentTradingPeriod.regular)으로 판정한다.
    거래소 날짜와 KST 날짜를 비교하는 방식은 미국 장이 KST 자정을 넘겨 열려 있는
    00:00~05:00에서 장중인데도 '어제 봉'으로 보여 경고를 놓쳤다.

    주봉은 **장이 닫혀 있어도** 진행 중인 주의 봉이 미확정이므로 정규장 구간으로
    판정하면 안 된다(그렇게 하면 장 마감 후 받은 진행 중 주봉을 확정으로 오판한다).
    봉이 속한 주가 아직 끝나지 않았는지를 거래소 현지 날짜로 본다.
    """
    last_local = datetime.fromtimestamp(s["last_ts"], timezone.utc).astimezone(s["ex_tz"]).date()
    today_local = datetime.fromtimestamp(now_epoch, timezone.utc).astimezone(s["ex_tz"]).date()

    if interval == "1wk":
        same_week = last_local.isocalendar()[:2] == today_local.isocalendar()[:2]
        return "이번 주 진행 중인 봉" if same_week and not week_closed(today_local) else None

    start, end = s["session"]
    if not start <= now_epoch < end:
        return None  # 정규장 밖 — 마지막 봉은 확정된 값이다
    # 장이 열렸어도 오늘 봉이 아직 안 생겼으면 마지막 봉은 직전 거래일의 확정 봉이다.
    return "오늘 장중 봉" if s["last_ts"] >= start else None


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

    now_dt = save(payload, out, args.ticker, args.rng, args.interval)
    now = (f"{now_dt.astimezone(KST):%Y-%m-%d %H:%M KST}"
           f" (거래소 현지 {now_dt.astimezone(s['ex_tz']):%Y-%m-%d %H:%M})")
    print(f"저장: {out} (조회 기록 {sidecar(out).name})")
    print(f"종목: {s['symbol']} · {s['exchange']} · {s['currency']} · 거래소 시간대 {s['timezone']}")
    print(f"구간: {s['first']} ~ {s['last']} ({args.interval}, {s['rows']}봉, 결측 {s['null_rows']}봉)")
    print(f"마지막 봉 종가: {s['last_close']}")
    print(f"adjclose 포함: {'예' if s['has_adjclose'] else '아니오'}  (OHLC는 분할 반영·배당 미반영)")
    print(f"조회 시각: {now}")
    # 주봉은 기간으로 판정하므로 정규장 시간이 없어도 된다. 일봉만 그 정보가 필요하다.
    if args.interval == "1d" and s["session"] is None:
        print("[주의] 응답에 정규장 시간이 없어 마지막 봉의 확정 여부를 판정하지 못했다 — 직접 확인한다.")
    elif (reason := unconfirmed_reason(s, args.interval, now_dt.timestamp())) is not None:
        print(f"[주의] 마지막 봉이 미확정이다 ({reason}) — 보고서 기준일로 쓰지 말고 "
              "`technicals.py --drop-unconfirmed`로 제외하고 계산한다.")


if __name__ == "__main__":
    main()
