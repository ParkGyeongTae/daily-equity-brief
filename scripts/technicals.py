#!/usr/bin/env python3
"""저장된 OHLCV 원자료에서 기술적 지표와 지지/저항 클러스터를 계산한다.

입력은 `fetch_ohlcv.py`가 저장한 Yahoo 차트 API JSON이다. 이 스크립트는 **네트워크를
쓰지 않는다** — 같은 파일을 다시 넣으면 언제나 같은 값이 나와야 보고서가 재현 가능해지기
때문이다(AGENTS.md "5단계 — 지표 계산 규칙").

사용법
------
    python3 scripts/technicals.py "$SCRATCH/NVDA-1d.json"                  # 전체 출력
    python3 scripts/technicals.py "$SCRATCH/NVDA-1d.json" --emit table     # 지표 표만
    python3 scripts/technicals.py "$SCRATCH/NVDA-1d.json" --emit levels    # 지지/저항 표만
    python3 scripts/technicals.py "$SCRATCH/NVDA-1d.json" --emit facts     # 방법론 4줄
    python3 scripts/technicals.py "$SCRATCH/005930-1wk.json" --interval-preset weekly

`--emit table|levels|facts` 출력은 **손대지 말고 그대로** 보고서에 붙여넣는다. 값을 손으로
고치면 원자료와 어긋나고, 그 어긋남은 나중에 찾을 수 없다.

지표 정의 (파라미터는 AGENTS.md가 고정한 값이다 — 종목마다 바꾸지 않는다)
    SMA          단순이동평균 20 / 60 / 120 / 200
    RSI          14, Wilder 평활 (첫 평균은 최초 14개 변화량의 단순평균)
    MACD         EMA12 − EMA26, 시그널은 그 값의 EMA9 (EMA 시드는 해당 구간 SMA)
    ATR          14, Wilder 평활. TR = max(고−저, |고−전일종가|, |저−전일종가|)
    볼린저        20일 SMA ± 2σ, **모집단 표준편차**(ddof=0)
    52주 위치     최근 365일 고·저 대비 (현재가−저)÷(고−저)
    기간 수익률   1M/3M/6M/12M — 달력 기준 30/90/180/365일 **이전 날짜 이하의 마지막 봉** 대비
    거래량 배수   마지막 봉 거래량 ÷ 직전 20봉(마지막 봉 제외) 평균

지지/저항
    스윙 포인트: 고가/저가가 전후 w봉(총 2w+1봉) 내 최고/최저와 같으면 스윙 고점/저점.
    클러스터링: 스윙 가격을 오름차순 정렬해, 기존 클러스터 중심과 ±tol% 이내면 합치고
    중심을 재계산(산술평균). 터치 횟수는 클러스터에 묶인 스윙 포인트 개수다.
    프리셋: daily = w5 / ±2% / 최근 1년,  weekly = w5 / ±3% / 최근 5년.

한계
    후행 지표다. 특정 가격에서 지지·저항이 작동하는 것을 보장하지 않으며, w와 tol을 바꾸면
    레벨과 터치 횟수가 달라진다(최적화된 값이 아니다). 거래량 프로파일·추세선은 쓰지 않는다.
    상장 직후처럼 표본이 부족하면 레벨을 만들지 말고 "표본 부족"으로 남긴다.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

KST = timezone(timedelta(hours=9))

PRESETS = {
    "daily": {"window": 5, "tol": 0.02, "lookback_days": 365, "unit": "거래일", "ma_unit": "일", "label": "일봉·1년"},
    "weekly": {"window": 5, "tol": 0.03, "lookback_days": 365 * 5, "unit": "주", "ma_unit": "주", "label": "주봉·5년"},
}

# ---------------------------------------------------------------- 원자료 적재


def load(path: Path, price_field: str) -> dict:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        sys.exit(f"[실패] 파일이 없다: {path}")
    except json.JSONDecodeError as e:
        sys.exit(f"[실패] JSON이 아니다: {path} ({e})")

    try:
        r = payload["chart"]["result"][0]
    except (KeyError, IndexError, TypeError):
        sys.exit("[실패] Yahoo 차트 응답 형식이 아니다 — fetch_ohlcv.py가 저장한 파일인지 확인한다.")

    meta = r.get("meta", {})
    ts = r.get("timestamp") or []
    q = (r.get("indicators", {}).get("quote") or [{}])[0]
    adj = (r.get("indicators", {}).get("adjclose") or [{}])[0].get("adjclose")

    if price_field == "adjclose" and not adj:
        sys.exit("[실패] adjclose가 없다 — --price-field close로 계산하거나 원자료를 다시 받는다.")

    bars = []
    for i, t in enumerate(ts):
        row = {
            "t": t,
            "date": datetime.fromtimestamp(t, timezone.utc).astimezone(KST).strftime("%Y-%m-%d"),
            "open": _at(q.get("open"), i),
            "high": _at(q.get("high"), i),
            "low": _at(q.get("low"), i),
            "close": _at(q.get("close"), i),
            "volume": _at(q.get("volume"), i),
            "adjclose": _at(adj, i),
        }
        if None in (row["high"], row["low"], row["close"]):
            continue  # 휴장·결측 봉은 버린다
        if price_field == "adjclose":
            if row["adjclose"] is None:
                continue
            ratio = row["adjclose"] / row["close"] if row["close"] else 1.0
            row["high"] *= ratio
            row["low"] *= ratio
            row["close"] = row["adjclose"]
        bars.append(row)

    if len(bars) < 30:
        sys.exit(f"[실패] 유효 봉이 {len(bars)}개뿐이다 — 표본 부족이므로 지표를 만들지 않는다.")
    return {"meta": meta, "bars": bars}


def _at(seq, i):
    if not seq or i >= len(seq):
        return None
    return seq[i]


# ---------------------------------------------------------------- 지표


def sma(xs: list[float], n: int) -> float | None:
    if len(xs) < n:
        return None
    return sum(xs[-n:]) / n


def ema_series(xs: list[float], n: int) -> list[float | None]:
    if len(xs) < n:
        return [None] * len(xs)
    out: list[float | None] = [None] * (n - 1)
    seed = sum(xs[:n]) / n
    out.append(seed)
    k = 2 / (n + 1)
    prev = seed
    for x in xs[n:]:
        prev = x * k + prev * (1 - k)
        out.append(prev)
    return out


def rsi_wilder(closes: list[float], n: int = 14) -> float | None:
    if len(closes) < n + 1:
        return None
    gains, losses = [], []
    for a, b in zip(closes, closes[1:]):
        d = b - a
        gains.append(max(d, 0.0))
        losses.append(max(-d, 0.0))
    avg_g = sum(gains[:n]) / n
    avg_l = sum(losses[:n]) / n
    for g, l in zip(gains[n:], losses[n:]):
        avg_g = (avg_g * (n - 1) + g) / n
        avg_l = (avg_l * (n - 1) + l) / n
    if avg_l == 0:
        return 100.0
    rs = avg_g / avg_l
    return 100 - 100 / (1 + rs)


def atr_wilder(bars: list[dict], n: int = 14) -> float | None:
    if len(bars) < n + 1:
        return None
    trs = [bars[0]["high"] - bars[0]["low"]]
    for prev, cur in zip(bars, bars[1:]):
        trs.append(max(cur["high"] - cur["low"], abs(cur["high"] - prev["close"]), abs(cur["low"] - prev["close"])))
    atr = sum(trs[1 : n + 1]) / n
    for tr in trs[n + 1 :]:
        atr = (atr * (n - 1) + tr) / n
    return atr


def bollinger(closes: list[float], n: int = 20, k: float = 2.0):
    if len(closes) < n:
        return None, None, None
    window = closes[-n:]
    mid = sum(window) / n
    var = sum((x - mid) ** 2 for x in window) / n  # 모집단 기준
    sd = math.sqrt(var)
    return mid - k * sd, mid, mid + k * sd


def lookback_close(bars: list[dict], days: int) -> dict | None:
    target = bars[-1]["t"] - days * 86400
    prior = [b for b in bars if b["t"] <= target]
    return prior[-1] if prior else None


# ---------------------------------------------------------------- 지지/저항


def swings(bars: list[dict], w: int):
    highs, lows = [], []
    for i in range(w, len(bars) - w):
        win = bars[i - w : i + w + 1]
        if bars[i]["high"] == max(b["high"] for b in win):
            highs.append((bars[i]["high"], bars[i]["date"]))
        if bars[i]["low"] == min(b["low"] for b in win):
            lows.append((bars[i]["low"], bars[i]["date"]))
    return highs, lows


def cluster(points: list[tuple[float, str]], tol: float):
    out = []
    for price, date in sorted(points, key=lambda p: p[0]):
        if out and abs(price - out[-1]["center"]) / out[-1]["center"] <= tol:
            c = out[-1]
            c["prices"].append(price)
            c["dates"].append(date)
            c["center"] = sum(c["prices"]) / len(c["prices"])
        else:
            out.append({"center": price, "prices": [price], "dates": [date]})
    for c in out:
        c["touches"] = len(c["prices"])
        c["first"] = min(c["dates"])
        c["last"] = max(c["dates"])
    return out


# ---------------------------------------------------------------- 출력 서식


def make_fmt(currency: str | None):
    krw = (currency or "").upper() == "KRW"
    sym = {"USD": "$", "KRW": "₩", "EUR": "€", "JPY": "¥"}.get((currency or "").upper(), "")

    def fmt(x: float | None) -> str:
        if x is None:
            return "—"
        return f"{sym}{x:,.0f}" if krw else f"{sym}{x:,.2f}"

    return fmt


def pct(x: float | None, digits: int = 1) -> str:
    return "—" if x is None else f"{x:+.{digits}f}%"


# ---------------------------------------------------------------- 계산 본체


def compute(data: dict, preset: dict, args) -> dict:
    bars = data["bars"]
    meta = data["meta"]
    closes = [b["close"] for b in bars]
    last = bars[-1]
    price = last["close"]

    # 지지/저항은 프리셋 구간으로 잘라서 본다
    cutoff = last["t"] - preset["lookback_days"] * 86400
    win = [b for b in bars if b["t"] >= cutoff]
    sh, sl = swings(win, args.window)
    clusters = [c for c in cluster(sh + sl, args.tol) if c["touches"] >= args.min_touches]
    res = sorted([c for c in clusters if c["center"] > price], key=lambda c: c["center"])
    sup = sorted([c for c in clusters if c["center"] <= price], key=lambda c: -c["center"])

    yr = [b for b in bars if b["t"] >= last["t"] - 365 * 86400]
    hi52 = max(b["high"] for b in yr)
    lo52 = min(b["low"] for b in yr)

    macd_line = None
    signal = None
    e12, e26 = ema_series(closes, 12), ema_series(closes, 26)
    if e12[-1] is not None and e26[-1] is not None:
        diffs = [a - b for a, b in zip(e12, e26) if a is not None and b is not None]
        macd_line = diffs[-1]
        sig_series = ema_series(diffs, 9)
        signal = sig_series[-1]

    vols = [b["volume"] for b in bars if b["volume"] is not None]
    vol_ratio = None
    if len(vols) >= 21 and last["volume"]:
        base = sum(vols[-21:-1]) / 20
        vol_ratio = last["volume"] / base if base else None

    rets = {}
    for label, days in (("1M", 30), ("3M", 90), ("6M", 180), ("12M", 365)):
        ref = lookback_close(bars, days)
        rets[label] = ((price / ref["close"] - 1) * 100, ref["date"]) if ref else (None, None)

    lower, mid, upper = bollinger(closes)
    atr = atr_wilder(bars)

    return {
        "meta": meta,
        "bars": bars,
        "window_bars": win,
        "price": price,
        "date": last["date"],
        "sma": {n: sma(closes, n) for n in (20, 60, 120, 200)},
        "rsi": rsi_wilder(closes),
        "macd": macd_line,
        "macd_signal": signal,
        "atr": atr,
        "atr_pct": (atr / price * 100) if atr else None,
        "bb": (lower, mid, upper),
        "hi52": hi52,
        "lo52": lo52,
        "pos52": (price - lo52) / (hi52 - lo52) * 100 if hi52 > lo52 else None,
        "returns": rets,
        "vol_ratio": vol_ratio,
        "resistances": res,
        "supports": sup,
        "swing_counts": (len(sh), len(sl)),
    }


# ---------------------------------------------------------------- emit


def emit_table(c: dict, fmt, preset: dict) -> str:
    p = c["price"]
    mu = preset["ma_unit"]
    rows = []
    for n in (20, 60, 120, 200):
        v = c["sma"][n]
        gap = f"{(p / v - 1) * 100:+.1f}%" if v else "—"
        rows.append(f"| SMA {n}{mu} | {fmt(v)} | 현재가 대비 {gap} |")

    order = [c["sma"][n] for n in (20, 60, 120)]
    if all(order):
        arrange = "정배열(20>60>120)" if order[0] > order[1] > order[2] else (
            "역배열(20<60<120)" if order[0] < order[1] < order[2] else "혼조")
    else:
        arrange = "표본 부족"

    lower, mid, upper = c["bb"]
    bb_pos = "—"
    if lower is not None and upper is not None and upper > lower:
        bb_pos = f"%B {(p - lower) / (upper - lower) * 100:.0f}"

    macd_state = "—"
    if c["macd"] is not None and c["macd_signal"] is not None:
        macd_state = "시그널 상회" if c["macd"] > c["macd_signal"] else "시그널 하회"

    ret_rows = []
    for k in ("1M", "3M", "6M", "12M"):
        v, ref = c["returns"][k]
        ret_rows.append(f"| 수익률 {k} | {pct(v)} | 기준 {ref or '—'} 종가 대비 |")

    body = "\n".join(
        [f"| 지표 | 값 | 비고 |", "|---|---|---|"]
        + rows
        + [
            f"| 이동평균 배열 | {arrange} | 20/60/120{mu} |",
            f"| RSI(14) | {c['rsi']:.1f} | Wilder |" if c["rsi"] is not None else "| RSI(14) | — | 표본 부족 |",
            f"| MACD(12,26,9) | {c['macd']:.2f} / 시그널 {c['macd_signal']:.2f} | {macd_state} |"
            if c["macd"] is not None and c["macd_signal"] is not None
            else "| MACD(12,26,9) | — | 표본 부족 |",
            f"| ATR(14) | {fmt(c['atr'])} | 현재가의 {c['atr_pct']:.1f}% |"
            if c["atr"] is not None
            else "| ATR(14) | — | 표본 부족 |",
            f"| 볼린저(20,2σ) | {fmt(lower)} ~ {fmt(upper)} | 중심 {fmt(mid)} · {bb_pos} |",
            f"| 52주 최고/최저 | {fmt(c['hi52'])} / {fmt(c['lo52'])} | 밴드 내 위치 {c['pos52']:.0f}% |"
            if c["pos52"] is not None
            else f"| 52주 최고/최저 | {fmt(c['hi52'])} / {fmt(c['lo52'])} | — |",
        ]
        + ret_rows
        + [
            f"| 거래량 배수 | {c['vol_ratio']:.2f}배 | 직전 20봉 평균 대비 |"
            if c["vol_ratio"] is not None
            else "| 거래량 배수 | — | 표본 부족 |"
        ]
    )
    return f"기준 종가일: {c['date']} · 현재가 {fmt(c['price'])}\n\n{body}"


def emit_levels(c: dict, fmt, far: float) -> str:
    price = c["price"]

    def row(name: str, cl: dict) -> str:
        d = (cl["center"] / price - 1) * 100
        tag = " · **원거리**" if abs(d) > far * 100 else ""
        return f"| {name} | {fmt(cl['center'])} | {cl['touches']} | 현재가 대비 {d:+.1f}%{tag} · {cl['first']} ~ {cl['last']} 스윙 |"

    lines = ["| 레벨 | 가격 | 터치 횟수 | 비고 |", "|---|---|---|---|"]
    res, sup = c["resistances"][:3], c["supports"][:3]
    for i, cl in enumerate(reversed(res), start=1):
        lines.append(row(f"R{len(res) - i + 1}", cl))
    lines.append(f"| **현재가** | **{fmt(price)}** ({c['date']} 종가) | — | {_between(c)} |")
    for i, cl in enumerate(sup, start=1):
        lines.append(row(f"S{i}", cl))
    lines.append(f"| 참고선 | {fmt(c['hi52'])} / {fmt(c['lo52'])} | — | 52주 최고 / 최저 |")

    notes = []
    if not res or not sup:
        notes.append("유효 클러스터가 한쪽에만 잡혔다 — 없는 쪽은 레벨을 지어내지 말고 그대로 비운다.")
    for label, group in (("저항", res), ("지지", sup)):
        if group:
            nearest = min(abs(g["center"] / price - 1) for g in group)
            if nearest > far:
                notes.append(
                    f"현재가에서 ±{far * 100:g}% 안에 {label} 클러스터가 없다 — **{label} 공백 구간**이다. "
                    f"가장 가까운 것도 {nearest * 100:.0f}% 떨어져 있어 무효화 조건의 근거로 쓰기 어렵다"
                    f"(ATR 배수 등 다른 기준을 쓰고 그 사유를 남긴다)."
                )
    if notes:
        return "\n".join(lines) + "\n\n" + "\n".join(f"> {n}" for n in notes)
    return "\n".join(lines)


def _between(c: dict) -> str:
    r = c["resistances"][0]["center"] if c["resistances"] else None
    s = c["supports"][0]["center"] if c["supports"] else None
    if r and s:
        return "R1과 S1 사이"
    if r:
        return "S1 없음 — R1 아래"
    if s:
        return "R1 없음 — S1 위"
    return "유효 클러스터 없음"


def emit_facts(c: dict, preset: dict, args, src: Path) -> str:
    bars, win = c["bars"], c["window_bars"]
    meta = c["meta"]
    unit = preset["unit"]
    adj = "수정주가(분할·배당 반영, adjclose 기준)" if args.price_field == "adjclose" else "분할 반영·배당 미반영(close 기준)"
    now = datetime.now(KST).strftime("%Y-%m-%d")
    sh, sl = c["swing_counts"]
    return "\n".join(
        [
            f"- **데이터**: Yahoo Finance {meta.get('symbol')} OHLCV, {len(bars)}봉, "
            f"{bars[0]['date']}~{bars[-1]['date']}. 레벨 산출 구간 {win[0]['date']}~{win[-1]['date']}({len(win)}봉). "
            f"수집 시점: {now}. {adj}.",
            f"- **스윙 포인트 탐지**: 고가/저가가 전후 {args.window}{unit}(총 {2 * args.window + 1}{unit} 창) 내 "
            f"최고/최저값과 같으면 스윙 고점/저점으로 분류 — 고점 {sh}개, 저점 {sl}개.",
            f"- **클러스터링**: 스윙 포인트를 가격 오름차순으로 정렬한 뒤, 기존 클러스터 중심과 ±{args.tol * 100:g}% "
            f"이내면 합산하고 중심을 재계산. 터치 {args.min_touches}회 미만 클러스터는 제외.",
            f"- **생성**: `python3 scripts/technicals.py {src.name} --interval-preset {args.interval_preset} "
            f"--window {args.window} --tol {args.tol} --price-field {args.price_field}`",
            "- **한계**: 후행 지표이며 특정 가격의 지지·저항 작동을 보장하지 않는다. 거래량 프로파일·추세선은 포함하지 "
            "않은 단순 모델이고, 창·허용오차를 바꾸면 레벨과 터치 횟수가 달라진다(최적화된 값이 아니다).",
        ]
    )


# ---------------------------------------------------------------- main


def main() -> None:
    ap = argparse.ArgumentParser(description="저장된 OHLCV 원자료에서 지표·지지/저항을 계산한다.")
    ap.add_argument("path", type=Path, help="fetch_ohlcv.py가 저장한 JSON")
    ap.add_argument("--interval-preset", choices=list(PRESETS), default="daily")
    ap.add_argument("--window", type=int, help="스윙 탐지 창 (기본: 프리셋 값)")
    ap.add_argument("--tol", type=float, help="클러스터 허용오차 비율 (기본: 프리셋 값)")
    ap.add_argument("--min-touches", type=int, default=2, help="레벨로 인정할 최소 터치 횟수 (기본 2)")
    ap.add_argument("--far-pct", type=float, default=0.25,
                    help="현재가에서 이 비율을 넘게 떨어진 레벨은 원거리로 표시 (기본 0.25)")
    ap.add_argument("--price-field", choices=["close", "adjclose"], default="close")
    ap.add_argument("--emit", choices=["all", "table", "levels", "facts", "json"], default="all")
    args = ap.parse_args()

    preset = PRESETS[args.interval_preset]
    if args.window is None:
        args.window = preset["window"]
    if args.tol is None:
        args.tol = preset["tol"]

    data = load(args.path, args.price_field)
    c = compute(data, preset, args)
    fmt = make_fmt(data["meta"].get("currency"))

    if c["date"] == datetime.now(KST).strftime("%Y-%m-%d"):
        print(
            f"[주의] 마지막 봉({c['date']})이 오늘이다 — 장중이면 종가·거래량이 미확정이므로 "
            "보고서 기준일로 쓰지 않는다. 직전 거래일 확정 종가로 다시 받는다.",
            file=sys.stderr,
        )

    if args.emit == "json":
        out = {k: v for k, v in c.items() if k not in ("bars", "window_bars", "meta")}
        for key in ("resistances", "supports"):
            out[key] = [{"center": x["center"], "touches": x["touches"], "first": x["first"], "last": x["last"]} for x in c[key]]
        print(json.dumps(out, ensure_ascii=False, indent=2, default=str))
        return

    if args.emit in ("all", "table"):
        print(emit_table(c, fmt, preset))
    if args.emit == "all":
        print()
    if args.emit in ("all", "levels"):
        print(emit_levels(c, fmt, args.far_pct))
    if args.emit == "all":
        print()
    if args.emit in ("all", "facts"):
        print(emit_facts(c, preset, args, args.path))


if __name__ == "__main__":
    main()
