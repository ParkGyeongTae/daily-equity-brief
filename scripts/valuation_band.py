#!/usr/bin/env python3
"""공시 분모와 저장된 가격 원자료로 **그 회사 자신의 이력 배수 밴드**를 계산한다.

`PER 44.45배`는 그 자체로 높은지 낮은지 알 수 없다. 기준점이 없으면 "가격이 이익 성장보다
앞서 있다"는 약세 논거가 **측정이 아니라 주장**이 된다. 동종업계 상대배수는 비교 기업의
분모를 원문 공시에서 확인해야 해서 하루 한 건 일정에 들어가지 않는다(그래서 계속 생략됐다).
그 자리를 **자기 이력 밴드**가 메운다 — 재료가 이미 브리프 안에 다 있다.

사용법
------
    python3 scripts/valuation_band.py <분모 JSON> <가격 원자료 JSON>
    python3 scripts/valuation_band.py denom.json "$SCRATCH/COST-1d.json" --emit table
    python3 scripts/valuation_band.py denom.json "$SCRATCH/COST-1d.json" --emit facts

분모 JSON — **직접 공시에서 읽어 적는다**
    이 스크립트는 분모를 절대 만들지 않는다. 아래 형식으로 적어 넣은 값만 쓴다.

        {
          "ticker": "COST",
          "metric": "희석 EPS (GAAP, TTM)",
          "series": [
            {"effective": "2024-10-09", "value": 16.56, "source": "FY2024 10-K / 2024-10-09"},
            {"effective": "2025-10-08", "value": 18.21, "source": "FY2025 10-K / 2025-10-08"},
            {"effective": "2026-09-24", "value": 20.76, "source": "4Q FY26 8-K EX-99.1 / 2026-09-24"}
          ]
        }

    `effective`는 **그 값을 알 수 있게 된 날, 즉 공시 제출일**이다. 결산일이 아니다.
    배수는 각 거래일에 대해 `effective <= 그 날`인 가장 최근 값을 분모로 쓴다 — 계단식이다.
    이렇게 해야 **미래 정보를 과거 가격에 쓰지 않는다**(FY2026 EPS로 2024년 배수를 내면
    밴드가 실제로 관찰될 수 없었던 숫자가 된다). 첫 `effective` 이전 봉은 계산에서 빠지고,
    몇 봉이 빠졌는지 출력에 남는다.

    `source`는 비워둘 수 없다. 보고서 `## 출처`에 그대로 옮길 문자열이다.

분할 주의 (이 스크립트가 멈추는 유일한 이유)
    Yahoo의 종가는 **분할이 소급 반영**되지만 공시 EPS·BPS는 **발표 당시 그대로**다.
    구간 안에 분할이 있으면 둘의 기준이 달라 배수가 무의미해지므로 **멈춘다.**
    계속하려면 분모를 분할 반영 기준으로 다시 적고 `--allow-split`을 준다.

출력
    밴드 분위(최소 / 25 / 중앙 / 75 / 최대)와 현재 배수의 분위, 그리고 **분위별 환산 가격**.
    환산 가격은 `분위 배수 × 현재 분모`이므로, 9절 2차 진입의 `[공시]` 근거로 쓸 수 있는
    유일한 가격이다(7절 클러스터는 `[기술]`이다).
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date, datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import technicals as T  # noqa: E402  — 원자료 적재·미확정 봉 판정·통화 포맷을 재사용한다


def load_denominator(path: Path) -> dict:
    try:
        d = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        sys.exit(f"[실패] 분모 파일이 없다: {path}")
    except json.JSONDecodeError as e:
        sys.exit(f"[실패] JSON이 아니다: {path} ({e})")

    for key in ("metric", "series"):
        if key not in d:
            sys.exit(f"[실패] 분모 JSON에 '{key}'가 없다 — docstring의 형식을 따른다.")
    series = d["series"]
    if not isinstance(series, list) or not series:
        sys.exit("[실패] 'series'가 비어 있다 — 공시에서 읽은 값을 최소 한 줄 적는다.")

    rows = []
    for i, r in enumerate(series, start=1):
        for key in ("effective", "value", "source"):
            if not r.get(key) and r.get(key) != 0:
                sys.exit(f"[실패] series[{i}]에 '{key}'가 없다 — "
                         "출처 없는 분모는 쓰지 않는다(AGENTS.md).")
        try:
            eff = date.fromisoformat(r["effective"]).isoformat()
        except ValueError:
            sys.exit(f"[실패] series[{i}] effective가 YYYY-MM-DD가 아니다: {r['effective']}")
        val = float(r["value"])
        if val == 0:
            sys.exit(f"[실패] series[{i}] value가 0이다 — 배수를 만들 수 없다. "
                     "적자 구간이면 그 방법론을 쓰지 않고 사유를 6절에 적는다.")
        rows.append({"effective": eff, "value": val, "source": str(r["source"])})

    rows.sort(key=lambda r: r["effective"])
    if len({r["effective"] for r in rows}) != len(rows):
        sys.exit("[실패] effective 날짜가 중복이다 — 한 날짜에 값 하나다.")
    d["series"] = rows
    return d


def splits_in_range(payload_path: Path, first: str, last: str, ex_tz) -> list[str]:
    """구간 안의 분할을 찾는다. 하나라도 있으면 배수의 기준이 무너진다."""
    payload = json.loads(payload_path.read_text(encoding="utf-8"))
    ev = (payload.get("chart", {}).get("result") or [{}])[0].get("events") or {}
    found = []
    for s in (ev.get("splits") or {}).values():
        when = datetime.fromtimestamp(s["date"], timezone.utc).astimezone(ex_tz).strftime("%Y-%m-%d")
        if first <= when <= last:
            found.append(f"{when} {s.get('splitRatio', '?')}")
    return sorted(found)


def effective_at(rows: list[dict], day: str) -> dict | None:
    """그 날 알 수 있었던 가장 최근 분모. 계단식 — 미래 값을 쓰지 않는다."""
    picked = None
    for r in rows:
        if r["effective"] <= day:
            picked = r
        else:
            break
    return picked


def quantile(xs: list[float], q: float) -> float:
    """선형보간 분위. numpy를 쓰지 않는다(이 저장소는 표준 라이브러리만 쓴다)."""
    s = sorted(xs)
    if len(s) == 1:
        return s[0]
    pos = q * (len(s) - 1)
    lo = int(pos)
    hi = min(lo + 1, len(s) - 1)
    return s[lo] + (s[hi] - s[lo]) * (pos - lo)


def compute(denom: dict, data: dict, src: Path, allow_split: bool) -> dict:
    bars = data["bars"]
    rows = denom["series"]
    first_eff = rows[0]["effective"]

    used, skipped = [], 0
    for b in bars:
        r = effective_at(rows, b["date"])
        if r is None:
            skipped += 1
            continue
        used.append({"date": b["date"], "close": b["close"],
                     "denom": r["value"], "multiple": b["close"] / r["value"]})

    if len(used) < 30:
        sys.exit(f"[실패] 배수를 낼 수 있는 봉이 {len(used)}개뿐이다 — 표본 부족이므로 밴드를 만들지 않는다. "
                 f"(첫 effective {first_eff} 이전 {skipped}봉은 계산에서 빠진다. "
                 "원자료 구간을 늘리거나 더 이른 공시를 series에 넣는다.)")

    splits = splits_in_range(src, used[0]["date"], used[-1]["date"], data["ex_tz"])
    if splits and not allow_split:
        sys.exit("[실패] 구간 안에 주식분할이 있다 — " + ", ".join(splits) + "\n"
                 "Yahoo 종가는 분할이 소급 반영되지만 공시 분모는 발표 당시 그대로다. "
                 "둘의 기준이 달라 배수가 무의미하다.\n"
                 "분모를 분할 반영 기준으로 다시 적고 --allow-split 을 준다.")

    ms = [u["multiple"] for u in used]
    cur = used[-1]
    below = sum(1 for m in ms if m <= cur["multiple"])

    # 분모가 방금 교체됐으면 배수 하락의 상당 부분이 **가격이 아니라 분모** 때문이다.
    # 그 구분을 안 하면 "싸졌다"로 잘못 읽힌다.
    prior = None
    cur_rows = [r for r in rows if r["value"] == cur["denom"]]
    cur_eff = cur_rows[0]["effective"] if cur_rows else None
    if cur_eff:
        earlier = [r for r in rows if r["effective"] < cur_eff]
        if earlier:
            pv = earlier[-1]["value"]
            pm = cur["close"] / pv
            prior = {"value": pv, "multiple": pm, "source": earlier[-1]["source"],
                     "percentile": sum(1 for m in ms if m <= pm) / len(ms) * 100}

    segments = []
    for r in rows:
        seg = [u for u in used if u["denom"] == r["value"]]
        if seg:
            segments.append({"source": r["source"], "effective": r["effective"], "value": r["value"],
                             "first": seg[0]["date"], "last": seg[-1]["date"], "bars": len(seg),
                             "lo": min(u["multiple"] for u in seg),
                             "hi": max(u["multiple"] for u in seg)})

    return {
        "metric": denom["metric"], "ticker": denom.get("ticker") or data["meta"].get("symbol"),
        "currency": (data["meta"].get("currency") or "").upper(),
        "first": used[0]["date"], "last": cur["date"], "bars": len(used), "skipped": skipped,
        "first_eff": first_eff, "splits": splits, "segments": segments, "src": src,
        "current": {"date": cur["date"], "close": cur["close"], "denom": cur["denom"],
                    "multiple": cur["multiple"]},
        "band": {k: quantile(ms, q) for k, q in
                 (("최소", 0.0), ("25분위", 0.25), ("중앙", 0.5), ("75분위", 0.75), ("최대", 1.0))},
        "percentile": below / len(ms) * 100,
        "at_extreme": ("구간 최저" if cur["multiple"] == min(ms)
                       else "구간 최고" if cur["multiple"] == max(ms) else None),
        "prior": prior,
        "dropped": data["dropped"],
    }


def emit_table(c: dict) -> str:
    fmt = T.make_fmt(c["currency"])
    cur = c["current"]
    out = [f"**자기 이력 배수 밴드** — {c['metric']} 기준, {c['first']} ~ {c['last']} "
           f"({c['bars']}거래일). 분모는 각 거래일에 **그날 공시로 알 수 있었던 값**을 쓴다(계단식).",
           "",
           "| 밴드 | 배수 | 환산 가격 (배수 × 현재 분모 " + f"{cur['denom']:,.2f})" + " |",
           "|---|---|---|"]
    for k, v in c["band"].items():
        out.append(f"| {k} | {v:,.2f}배 | {fmt(v * cur['denom'])} |")
    out.append(f"| **현재 ({cur['date']})** | **{cur['multiple']:,.2f}배** | {fmt(cur['close'])} |")
    extreme = f" — **{c['at_extreme']}**" if c["at_extreme"] else ""
    out += ["",
            f"현재 배수 {cur['multiple']:,.2f}배는 이 구간 {c['bars']}거래일 중 "
            f"**{c['percentile']:.1f}분위**다{extreme} (그 값 이하인 거래일의 비율).",
            f"계산식: {fmt(cur['close'])} ÷ {cur['denom']:,.2f} = {cur['multiple']:,.2f}배."]
    last_seg = c["segments"][-1] if c["segments"] else None
    if c["prior"] and last_seg and last_seg["bars"] < 20:
        pr = c["prior"]
        out += ["",
                f"**분모가 {last_seg['bars']}거래일 전에 교체됐다.** 같은 종가 {fmt(cur['close'])}를 "
                f"직전 분모 {pr['value']:,.2f}로 나누면 {pr['multiple']:,.2f}배({pr['percentile']:.1f}분위)다. "
                f"배수가 내려간 것의 상당 부분은 가격이 아니라 **분모가 커진 결과**다 — "
                "'싸졌다'로 읽지 않는다."]
    out += ["", "구간별 분모와 그 구간의 배수 범위:", ""]
    out += ["| 분모 적용 구간 | 분모 | 배수 범위 | 출처 |", "|---|---|---|---|"]
    for s in c["segments"]:
        out.append(f"| {s['first']} ~ {s['last']} ({s['bars']}봉) | {s['value']:,.2f} "
                   f"| {s['lo']:,.2f} ~ {s['hi']:,.2f}배 | {s['source']} |")
    out += ["",
            "밴드는 **미래의 배수가 이 범위에 머문다는 뜻이 아니다.** 지나간 구간에서 이 회사의 "
            "가격이 자기 이익 대비 어디에 있었는지만 잰 숫자다."]
    return "\n".join(out)


def emit_facts(c: dict, args) -> str:
    flags = f"--price-field {args.price_field}" + (" --allow-split" if args.allow_split else "")
    out = [f"- 배수 밴드: `python3 scripts/valuation_band.py {args.denominator} {args.price} {flags}`",
           f"- 분모: {c['metric']} · 계단식 적용(공시 제출일 기준) · 구간 {len(c['segments'])}개",
           f"- 배수 구간: {c['first']} ~ {c['last']} ({c['bars']}거래일). "
           f"첫 공시 {c['first_eff']} 이전 {c['skipped']}봉은 분모가 없어 제외했다."]
    if c["dropped"]:
        out.append("- 미확정 봉 제외: " + " / ".join(c["dropped"]))
    if c["splits"]:
        out.append(f"- [주의] 구간 내 분할 {', '.join(c['splits'])} — `--allow-split`으로 계속했다. "
                   "분모가 분할 반영 기준인지 확인할 것.")
    return "\n".join(out)


def main() -> None:
    ap = argparse.ArgumentParser(
        description="공시 분모와 저장된 가격 원자료로 자기 이력 배수 밴드를 계산한다.")
    ap.add_argument("denominator", type=Path, help="공시에서 읽은 분모 JSON (docstring 형식)")
    ap.add_argument("price", type=Path, help="fetch_ohlcv.py가 저장한 일봉 JSON")
    ap.add_argument("--price-field", choices=["close", "adjclose"], default="close",
                    help="기본 close. 공시 분모는 배당 조정이 없으므로 close가 기준을 맞춘다")
    ap.add_argument("--allow-split", action="store_true",
                    help="구간 내 분할이 있어도 계속한다 — 분모를 분할 반영 기준으로 다시 적었을 때만")
    ap.add_argument("--emit", choices=["all", "table", "facts"], default="all")
    args = ap.parse_args()

    denom = load_denominator(args.denominator)
    data = T.load(args.price, args.price_field)
    data["dropped"] = T.drop_unconfirmed(data, "daily", datetime.now(timezone.utc))
    for d in data["dropped"]:
        print(f"[제외] 미확정 봉을 계산에서 뺐다 — {d}", file=sys.stderr)

    if args.price_field == "adjclose":
        print("[주의] adjclose는 배당이 소급 반영된 값이다. 공시 분모는 그렇지 않으므로 "
              "배수의 기준이 어긋난다 — 사유를 6절에 적는다.", file=sys.stderr)

    c = compute(denom, data, args.price, args.allow_split)

    if args.emit in ("all", "table"):
        print(emit_table(c))
    if args.emit == "all":
        print()
    if args.emit in ("all", "facts"):
        print(emit_facts(c, args))


if __name__ == "__main__":
    main()
