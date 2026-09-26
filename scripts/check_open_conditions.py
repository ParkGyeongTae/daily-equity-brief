#!/usr/bin/env python3
"""과거 브리프 9절의 **가격 조건**이 그 뒤 실제로 충족됐는지 종가로 대조한다.

이 저장소는 매일 조건을 쓰지만, 그 조건이 충족됐는지 되짚는 절차가 없었다. 그러면
1년에 365건의 가설이 쌓이고 그중 몇 건이 맞았는지 알 수 없다 — 점수표 ③⑤의 기준도
클러스터 파라미터도 교정할 근거가 없어진다. 이 스크립트가 그 되짚기를 맡는다.

사용법
------
    python3 scripts/check_open_conditions.py                       # briefs/*.md 전부
    python3 scripts/check_open_conditions.py briefs/2026-09-23-003850.md
    python3 scripts/check_open_conditions.py --since 2026-09-01    # 그 날짜 이후 작성분만
    python3 scripts/check_open_conditions.py --emit ledger > briefs/ledger.md
    python3 scripts/check_open_conditions.py --window-days 0       # 창을 열어 지금까지 전부

**판정하는 것과 하지 않는 것**

판정한다 — 9절 표에서 **가격 칸에 숫자가 있는 행**. 방향은 `구분` 어휘가 정한다
(진입·정리는 상방 돌파, 가격 무효화는 하방 이탈). 이건 템플릿이 고정한 어휘이므로
조건 문장을 해석하지 않고도 방향이 정해진다.

판정하지 않는다 — **논거 무효화**처럼 가격이 없는 행. 공시를 읽어야 하는 조건이라
기계가 판정할 수 없다. `미판정`으로 남기고 조건 문장을 함께 찍어 사람이 확인하게 한다.
가격 조건에 공시·일정이 함께 걸린 행(`2차 진입`의 10-K 제출 조건 같은 것)은
`[가격 외 조건]`을 붙인다 — **종가 돌파는 필요조건일 뿐 충족이 아니다.**

그러므로 이 출력은 "매매 성과"가 아니라 **"내가 쓴 가격 조건이 관찰됐는가"의 사후 기록**이다.

추적 창을 고정한다
    기준 종가일 이후 **90일까지만** 본다(`--window-days`, 0이면 무제한). 창을 열어두면
    어제 '미결'이던 브리프가 오늘 다른 판정으로 바뀌어 **원장이 기록이 아니라 그때그때의
    스냅샷**이 된다. 창이 같아야 브리프끼리 비교도 되고, 창이 닫힌 브리프의 원자료는 더
    바뀌지 않으므로 한 번 받아 두고 다시 받지 않는다(`conditions/settled/`).
    90일은 10절 재평가 시점이 대개 다음 분기 공시라는 데서 온 값이다.

원자료
    가격은 `fetch_ohlcv.py`와 같은 경로로 받아 **스크래치패드에 파일로 저장한 뒤 그 파일에서**
    판정한다(AGENTS.md "4단계"). 파일명에 range를 넣어 짧은 구간 캐시가 긴 요청을 덮어쓰지
    않게 한다. `--refresh`를 주면 다시 받는다. 미확정 봉은 `technicals.py`의 판정을 그대로 써서
    제외한다 — 조건은 모두 종가 기준이므로 장중 봉으로 판정하면 안 된다.
    기준 종가를 원자료와 대조해 어긋나면 `[주의]`로 찍는다. 티커 해석이 틀렸는지를 여기서 잡는다.

한계
    - 브리프 작성 후 **주식분할·병합**이 있었다면 Yahoo의 과거 종가가 소급 조정돼 브리프에
      적힌 레벨과 기준이 달라진다. 기준 종가 대조가 어긋나면 그때는 레벨을 다시 계산해야 하고
      이 판정을 쓰지 않는다.
    - 종가 기준만 본다. 장중에 스쳤는지는 보지 않는다(9절 조건이 종가 기준으로 쓰였기 때문이다).
    - **판정은 조건이 관찰됐는지까지다.** 실제로 매매했는지, 얼마를 벌거나 잃었는지는 이 저장소가
      기록하지 않는다(AGENTS.md "하지 않는 것" — 포지션 크기·수익률을 쓰지 않는다).
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import unicodedata
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import fetch_ohlcv as F       # noqa: E402  — 네트워크 경로를 하나로 둔다
import technicals as T        # noqa: E402  — 미확정 봉 판정과 통화 포맷을 재사용한다
import validate_brief as V    # noqa: E402  — 섹션·표 파서를 재사용해 판정 기준을 갈라지지 않게 한다

KST = timezone(timedelta(hours=9))

BASE_CLOSE_RE = re.compile(r"기준 종가\s*([₩$€¥])?\s*([\d,]+(?:\.\d+)?)\s*\((\d{4}-\d{2}-\d{2})\)")
PRICE_RE = re.compile(r"([₩$€¥])?\s*([\d,]+(?:\.\d+)?)")
MARKET_RE = re.compile(r"\|\s*티커\s*/\s*시장\s*\|([^|]*)\|")

# `구분` → (방향, 종류). workflow.md 6단계 표가 고정한 어휘다.
# 방향을 조건 문장이 아니라 이 어휘에서 가져오는 것이 이 스크립트가 성립하는 이유다.
ROW_KINDS: dict[str, tuple[str | None, str]] = {
    "1차 진입": ("up", "entry"),
    "2차 진입": ("up", "entry2"),
    "진입 보류": (None, "hold"),
    "가격 무효화": ("down", "stop"),
    "논거 무효화": (None, "thesis"),
    "일부 정리": ("up", "take"),
    "전량 정리": ("up", "take_all"),
}

# 가격 하나로 판정할 수 없다는 표지. 완전한 판별이 아니라 **사람에게 넘기는 신호**다.
EXTRA_COND = ("공시", "보고서", "8-K", "10-K", "10-Q", "EX-99", "제출", "발표",
              "이후", "직전", "확인되", "나온다", "선언", "목표")


# ---------------------------------------------------------------- 브리프 파싱


class Skip(Exception):
    """이 브리프는 판정하지 않는다 — 사유를 들고 올라간다."""


def parse_brief(path: Path) -> dict:
    lines = path.read_text(encoding="utf-8").splitlines()
    m = V.FILE_RE.match(path.name)
    if not m:
        raise Skip(f"파일명이 규칙과 다르다: {path.name}")
    file_date, code = m.group(1), m.group(2)

    h1 = next((V.H1_RE.match(t) for t in lines if t.startswith("# ")), None)
    name = h1.group(1) if h1 else code

    sections = V.split_sections(lines)
    raw = "\n".join(lines)

    # 기준 종가일은 7절·출처에 적히고 검사기가 브리프 안에서 유일함을 이미 강제한다.
    dates = {mm.group(1) for mm in V.CLOSE_DATE_RE.finditer(raw)}
    if not dates:
        raise Skip("기준 종가일을 찾지 못했다")
    if len(dates) > 1:
        raise Skip(f"기준 종가일이 여러 개다 — {', '.join(sorted(dates))}")
    base_date = dates.pop()

    bc = BASE_CLOSE_RE.search(raw)
    base_close = float(bc.group(2).replace(",", "")) if bc else None
    base_sym = bc.group(1) if bc else None

    mk = MARKET_RE.search(raw)
    market = (mk.group(1) if mk else "").strip()

    plan = V.section_by_number(sections, 9)
    if plan is None:
        raise Skip("9절이 없다")

    rows = []
    for ln, row in V.table_rows(plan):
        cells = [c.strip() for c in row.strip("|").split("|")]
        if len(cells) < 3:
            continue
        head, cond, price_cell = cells[0], cells[1], cells[2]
        head = re.sub(r"[*`]", "", head).strip()
        kind = next((v for k, v in ROW_KINDS.items() if head.startswith(k)), None)
        if kind is None:
            rows.append({"line": ln, "head": head, "cond": cond, "price": None,
                         "dir": None, "kind": "unknown"})
            continue
        direction, sort = kind
        extra = [w for w in EXTRA_COND if w in cond]
        if len(PRICE_RE.findall(price_cell.replace("**", ""))) > 1:
            extra.append("가격 칸에 값이 둘 이상")
        rows.append({"line": ln, "head": head, "cond": cond,
                     "price": parse_price(price_cell), "dir": direction, "kind": sort,
                     "extra": extra})
    if not rows:
        raise Skip("9절에 매매 조건 표가 없다")

    return {"path": path, "date": file_date, "code": code, "name": name,
            "market": market, "base_date": base_date, "base_close": base_close,
            "base_sym": base_sym, "rows": rows}


def parse_price(cell: str) -> float | None:
    cell = cell.replace("**", "").strip()
    if not cell or cell in {"—", "-", "–"}:
        return None
    m = PRICE_RE.search(cell)
    if not m:
        return None
    try:
        return float(m.group(2).replace(",", ""))
    except ValueError:
        return None


def yahoo_ticker(code: str, market: str) -> tuple[str, str | None]:
    """브리프의 코드·시장 표기를 Yahoo 티커로. 판정할 수 없으면 사유를 함께 돌려준다."""
    if not re.fullmatch(r"\d{6}", code):
        return code, None
    up = market.upper()
    if "코스닥" in market or "KOSDAQ" in up:
        return code + ".KQ", None
    if "코스피" in market or "KOSPI" in up or "유가증권" in market:
        return code + ".KS", None
    return code + ".KS", f"0절 '티커 / 시장'({market or '없음'})에서 시장을 못 읽어 .KS로 가정했다"


# ---------------------------------------------------------------- 가격 원자료


# 달력 일수 → 그 구간을 덮을 만한 range. load()가 30봉 미만이면 멈추므로 하한은 3mo다.
RANGE_STEPS = (("3mo", 45), ("6mo", 120), ("1y", 250), ("2y", 600), ("5y", 1600), ("max", None))


def pick_range(days: int) -> str:
    for span, limit in RANGE_STEPS:
        if limit is None or days <= limit:
            return span
    return "max"


def fetch_bars(ticker: str, rng: str, scratch: Path, refresh: bool) -> dict:
    """원자료를 받아 **파일로 저장한 뒤 그 파일에서** 읽는다(AGENTS.md 4단계).

    파일명에 range를 넣는다 — 넣지 않으면 같은 티커의 짧은 구간 캐시가 긴 요청을 덮어써
    **추적 구간이 조용히 짧아진다.** 그 어긋남은 출력만 보고는 찾을 수 없다.
    """
    out = scratch / f"{ticker}-1d-{rng}.json"
    if refresh or not out.exists():
        payload = F.fetch(ticker, rng, "1d")
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    data = T.load(out, "close")
    data["dropped"] = T.drop_unconfirmed(data, "daily", datetime.now(timezone.utc))
    data["src"] = out
    data["range"] = rng
    return data


def price_bars(ticker: str, base_date: str, elapsed: int, scratch: Path, refresh: bool) -> dict:
    """기준 종가일을 덮을 때까지 range를 늘린다. 덮지 못하면 판정하지 않는다."""
    spans = [s for s, _ in RANGE_STEPS]
    start = spans.index(pick_range(elapsed))
    for rng in spans[start:]:
        data = fetch_bars(ticker, rng, scratch, refresh)
        if data["bars"][0]["date"] <= base_date:
            return data
    raise Skip(f"원자료가 기준 종가일({base_date})까지 닿지 않는다 — 상장 이력을 확인한다")


def window_of(bars: list[dict], base_date: str, window_days: int) -> tuple[list[dict], str | None]:
    """추적 구간과, 그 구간이 닫혔는지.

    창을 **고정**한다. 열어두면 어제 '미결'이던 브리프가 오늘 다른 판정으로 바뀌어,
    원장이 기록이 아니라 그때그때의 스냅샷이 된다. 창이 같아야 브리프끼리 비교도 된다.
    """
    after = [b for b in bars if b["date"] > base_date]
    if window_days <= 0:
        return after, None
    end = (date.fromisoformat(base_date) + timedelta(days=window_days)).isoformat()
    closed = datetime.now(KST).date().isoformat() > end
    return [b for b in after if b["date"] <= end], (end if closed else None)


def first_touch(bars: list[dict], price: float, direction: str) -> dict | None:
    for b in bars:
        if (direction == "up" and b["close"] >= price) or (direction == "down" and b["close"] <= price):
            return b
    return None


# ---------------------------------------------------------------- 판정


def evaluate(brief: dict, scratch: Path, refresh: bool, window_days: int) -> dict:
    ticker, guess = yahoo_ticker(brief["code"], brief["market"])
    notes: list[str] = []
    if guess:
        notes.append(guess)

    today = datetime.now(KST).date()
    base = date.fromisoformat(brief["base_date"])
    elapsed = (today - base).days
    # 창이 닫힌 브리프의 원자료는 더 바뀌지 않는다 — 한 번 받아 두고 다시 받지 않는다.
    closed = window_days > 0 and elapsed > window_days
    # 창은 끝을 자를 뿐이고, 원자료는 기준 종가일까지 **거슬러** 닿아야 한다.
    data = price_bars(ticker, brief["base_date"], elapsed + 10,
                      scratch / ("settled" if closed else "open"), refresh)

    cur = (data["meta"].get("currency") or "").upper()
    fmt = T.make_fmt(cur)
    sym = {"USD": "$", "KRW": "₩", "EUR": "€", "JPY": "¥"}.get(cur, "")
    if brief["base_sym"] and sym and brief["base_sym"] != sym:
        notes.append(f"통화가 어긋난다 — 브리프는 {brief['base_sym']}, 원자료는 {sym}({cur}). 티커를 확인한다")

    bars = data["bars"]
    base_bar = next((b for b in bars if b["date"] == brief["base_date"]), None)
    if base_bar is None:
        notes.append(f"기준 종가일({brief['base_date']})의 봉이 원자료에 없다 — 기준 종가를 대조하지 못했다")
    elif brief["base_close"]:
        gap = abs(base_bar["close"] - brief["base_close"]) / brief["base_close"]
        if gap > 0.005:
            notes.append(
                f"기준 종가가 어긋난다 — 브리프 {fmt(brief['base_close'])} vs 원자료 "
                f"{fmt(base_bar['close'])} ({gap:.1%}). 분할·병합으로 과거 종가가 소급 조정됐을 수 있다. "
                "그렇다면 레벨을 다시 계산해야 하고 아래 판정을 쓰지 않는다")

    window, closed_on = window_of(bars, brief["base_date"], window_days)

    results = []
    for r in brief["rows"]:
        out = dict(r)
        if r["kind"] == "unknown":
            out["verdict"] = "미판정"
            out["why"] = f"'{r['head']}'은 템플릿의 구분 어휘가 아니다 — 방향을 정할 수 없다"
        elif r["kind"] == "hold":
            out["verdict"] = "판정 안 함"
            out["why"] = "진입 보류 조건이다 — 가격 조건이 아니다"
        elif r["dir"] is None:
            out["verdict"] = "미판정"
            out["why"] = "공시를 확인해야 판정된다"
        elif r["price"] is None:
            out["verdict"] = "미판정"
            out["why"] = "가격 칸에 숫자가 없다"
        elif not window:
            out["verdict"] = "추적 전"
            out["why"] = "기준 종가일 이후 확정 봉이 없다"
        else:
            hit = first_touch(window, r["price"], r["dir"])
            out["hit"] = hit
            if hit:
                if r["extra"]:
                    out["verdict"] = "가격 조건 관찰"
                else:
                    out["verdict"] = "충족" if r["dir"] == "up" else "이탈"
            else:
                ext = (max(window, key=lambda b: b["close"]) if r["dir"] == "up"
                       else min(window, key=lambda b: b["close"]))
                out["verdict"] = "가격 조건 미관찰" if r["extra"] else "미충족"
                out["ext"] = ext
                out["gap"] = (ext["close"] - r["price"]) / r["price"] * 100
        results.append(out)

    return {"brief": brief, "ticker": ticker, "fmt": fmt, "currency": cur,
            "data": data, "window": window, "closed_on": closed_on, "rows": results,
            "notes": notes, "verdict": verdict_of(results, window)}


def verdict_of(rows: list[dict], window: list[dict]) -> dict:
    """진입과 무효화 중 무엇이 먼저 관찰됐는가 — 계획 한 건의 결말이다."""
    if not window:
        return {"code": "추적 전", "text": "기준 종가일 이후 확정 봉이 없다"}

    entry = next((r for r in rows if r["kind"] == "entry"), None)
    stop = next((r for r in rows if r["kind"] == "stop"), None)
    e_hit = (entry or {}).get("hit")
    s_hit = (stop or {}).get("hit")

    if not e_hit and not s_hit:
        return {"code": "미결", "text": "1차 진입도 가격 무효화도 관찰되지 않았다"}
    if s_hit and (not e_hit or s_hit["date"] < e_hit["date"]):
        return {"code": "진입 없이 무효화",
                "text": f"{s_hit['date']} 가격 무효화 이탈 — 진입 없이 계획 종료"}
    if e_hit and not s_hit:
        out = {"code": "진입 가격 조건 충족",
               "text": f"{e_hit['date']} 1차 진입 가격 조건 충족, 무효화 미관찰"}
    else:
        out = {"code": "충족 후 무효화",
               "text": f"{e_hit['date']} 1차 진입 가격 조건 충족 → {s_hit['date']} 가격 무효화 이탈"}
    if entry.get("extra"):
        out["text"] += " · 1차 진입에 가격 외 조건이 있어 진입 성립은 별도 확인이 필요하다"

    # 진입 이후 종가의 최대 유리·불리 진폭을 R로 적는다. 성과가 아니라 사실 기록이다.
    if entry and stop and entry["price"] and stop["price"]:
        R = abs(entry["price"] - stop["price"])
        after = [b for b in window if b["date"] >= e_hit["date"]]
        if R > 0 and after:
            hi = max(b["close"] for b in after)
            lo = min(b["close"] for b in after)
            out["mfe"] = (hi - entry["price"]) / R
            out["mae"] = (lo - entry["price"]) / R
            out["R"] = R
    return out


# ---------------------------------------------------------------- 출력


def width(s: str) -> int:
    """동아시아 문자는 폭이 두 칸이다 — 라벨을 눈으로 맞추려면 글자 수가 아니라 폭으로 센다."""
    return sum(2 if unicodedata.east_asian_width(c) in "WF" else 1 for c in s)


def pad(s: str, n: int) -> str:
    return s + " " * max(1, n - width(s))


def emit_plain(res: dict) -> str:
    b, fmt = res["brief"], res["fmt"]
    w = res["window"]
    out = [f"■ {b['date']} · {b['name']} ({res['ticker']}) — 기준 종가 "
           f"{fmt(b['base_close'])} ({b['base_date']})"]
    if w:
        tail = f" · 추적 종료 {res['closed_on']}" if res["closed_on"] else " · 추적 중"
        out.append(f"  추적 구간: {w[0]['date']} ~ {w[-1]['date']} ({len(w)}봉, 종가 기준){tail}")
    else:
        out.append("  추적 구간: 없음 — 기준 종가일 이후 확정 봉이 없다")
    for d in res["data"]["dropped"]:
        out.append(f"  [제외] 미확정 봉 — {d}")
    for n in res["notes"]:
        out.append(f"  [주의] {n}")

    judged = {"충족", "이탈", "미충족", "가격 조건 관찰", "가격 조건 미관찰"}
    for r in [r for r in res["rows"] if r["verdict"] in judged]:
        arrow = "상회" if r["dir"] == "up" else "이탈"
        if r["verdict"] in {"미충족", "가격 조건 미관찰"}:
            tail = (f"{r['verdict']} (구간 {'최고' if r['dir'] == 'up' else '최저'} 종가 "
                    f"{fmt(r['ext']['close'])}, {r['gap']:+.1f}%)")
        else:
            tail = f"{r['verdict']} {r['hit']['date']} (종가 {fmt(r['hit']['close'])})"
        out.append(f"  {pad(r['head'], 20)}{fmt(r['price'])} {arrow} → {tail}")
        if r.get("extra"):
            out.append(f"  {'':20}[가격 외 조건] {', '.join(r['extra'])}"
                       " — 종가 돌파만으로 충족이 아니다")

    waiting = [r for r in res["rows"] if r["verdict"] == "추적 전" and r["price"]]
    if waiting:
        out.append(f"  {pad('추적 대기', 20)}"
                   + " · ".join(f"{r['head']} {fmt(r['price'])}" for r in waiting))

    for r in [r for r in res["rows"] if r["kind"] == "thesis"]:
        out.append(f"  {pad(r['head'], 20)}미판정 — 공시를 확인한다")
        out.append(f"  {'':20}조건: {r['cond'][:110]}")

    skipped = [r for r in res["rows"] if r["kind"] in {"hold", "unknown"}]
    if skipped:
        heads = ", ".join(sorted({r["head"] for r in skipped}))
        out.append(f"  {pad('판정 안 함', 20)}{heads} {len(skipped)}행 — 가격 조건이 아니다")
    for r in [r for r in res["rows"] if r["kind"] == "unknown"]:
        out.append(f"  [주의] {r['why']}")

    v = res["verdict"]
    out.append(f"  판정: {v['code']} — {v['text']}")
    if "mfe" in v:
        out.append(f"        진입 이후 종가 진폭: 최대 유리 {v['mfe']:+.2f}R · 최대 불리 {v['mae']:+.2f}R"
                   f" (R = {fmt(v['R'])} · 사후 기록이며 매매 성과가 아니다)")
    return "\n".join(out)


def emit_ledger(results: list[dict], failures: list[tuple[Path, str]], window_days: int) -> str:
    now = datetime.now(KST)
    out = ["---", "title: 조건 원장", "---", "",
           "# 조건 원장", "",
           "브리프 9절에 적은 **가격 조건**이 그 뒤 종가로 관찰됐는지의 기록이다.",
           "`scripts/check_open_conditions.py --emit ledger`가 생성하며 손으로 고치지 않는다.",
           "",
           "판정은 **가격 조건에 한정된다.** 논거 무효화처럼 공시를 읽어야 하는 조건은 `미판정`으로 남고,",
           "가격 조건에 공시·일정이 함께 걸린 행은 종가 돌파가 필요조건일 뿐이다. 이 표는 매매 성과가 아니라",
           "**내가 쓴 조건이 관찰됐는가의 사후 기록**이다.", "",
           "| 작성일 | 종목 | 기준 종가 | 1차 진입 | 가격 무효화 | 판정 | 추적 |",
           "|---|---|---|---|---|---|---|"]
    for res in results:
        b, fmt = res["brief"], res["fmt"]
        e = next((r for r in res["rows"] if r["kind"] == "entry"), None)
        s = next((r for r in res["rows"] if r["kind"] == "stop"), None)

        def cell(r):
            if r is None or r["price"] is None:
                return "—"
            mark = {"충족": "✓", "이탈": "✓", "가격 조건 관찰": "✓",
                    "미충족": "·", "가격 조건 미관찰": "·", "추적 전": "…"}.get(r["verdict"], "?")
            date = f" {r['hit']['date']}" if r.get("hit") else ""
            return f"{fmt(r['price'])} {mark}{date}"

        if not res["window"]:
            track = "—"
        elif res["closed_on"]:
            track = f"종료 {res['closed_on']}"
        else:
            track = f"~{res['window'][-1]['date']}"
        link = f"[{b['name']}](./{b['path'].stem})"
        out.append(f"| {b['date']} | {link} | {fmt(b['base_close'])} | {cell(e)} | {cell(s)} "
                   f"| {res['verdict']['code']} | {track} |")

    pend = [r for res in results for r in res["rows"] if r["kind"] == "thesis"]
    out += ["", f"`✓` 관찰됨 · `·` 미관찰 · `…` 추적 전 · `?` 미판정. "
            f"논거 무효화 {len(pend)}건은 공시 확인이 남아 있다.", ""]
    if failures:
        out += ["**판정하지 못한 브리프**", ""]
        out += [f"- `{p.name}` — {why}" for p, why in failures] + [""]
    out += ["---", "",
            f"*생성: {now:%Y-%m-%d %H:%M KST} · 추적 창은 기준 종가일 이후 "
            f"{'무제한' if window_days <= 0 else str(window_days) + '일'} · "
            "가격은 Yahoo Finance 일봉 종가(분할 반영·배당 미반영), 미확정 봉 제외.*"]
    return "\n".join(out)


# ---------------------------------------------------------------- main


def main() -> None:
    ap = argparse.ArgumentParser(
        description="과거 브리프 9절의 가격 조건이 그 뒤 종가로 관찰됐는지 대조한다.")
    ap.add_argument("paths", nargs="*", type=Path, help="브리프 경로 (없으면 briefs/*.md 전부)")
    ap.add_argument("--since", metavar="YYYY-MM-DD", help="이 날짜 이후 작성분만")
    ap.add_argument("--emit", choices=["plain", "ledger"], default="plain")
    ap.add_argument("--scratch", type=Path, help="원자료 저장 폴더 (기본: 스크래치패드 아래 conditions/, open·settled로 나뉜다)")
    ap.add_argument("--window-days", type=int, default=90, metavar="N",
                    help="기준 종가일 이후 N일까지만 추적한다 (기본 90, 0이면 무제한). "
                         "창을 고정해야 과거 판정이 매일 달라지지 않는다")
    ap.add_argument("--refresh", action="store_true", help="저장된 원자료를 무시하고 다시 받는다")
    args = ap.parse_args()

    root = Path(__file__).resolve().parent.parent
    paths = args.paths or sorted((root / "briefs").glob("[0-9]*.md"))
    if args.since:
        paths = [p for p in paths if p.name[:10] >= args.since]
    if not paths:
        sys.exit("[중단] 대조할 브리프가 없다.")

    scratch = args.scratch or (Path(os.environ.get("TMPDIR", "/tmp")) / "equity-brief" / "conditions")

    results, failures = [], []
    for p in paths:
        try:
            results.append(evaluate(parse_brief(p), scratch, args.refresh, args.window_days))
        except Skip as e:
            failures.append((p, str(e)))
        except SystemExit as e:  # load()·fetch()가 sys.exit로 멈추는 경우
            failures.append((p, str(e).replace("\n", " ")))

    if args.emit == "ledger":
        print(emit_ledger(results, failures, args.window_days))
    else:
        print("\n\n".join(emit_plain(r) for r in results))
        if results:
            tally: dict[str, int] = {}
            for r in results:
                tally[r["verdict"]["code"]] = tally.get(r["verdict"]["code"], 0) + 1
            print("\n― 요약: " + " · ".join(f"{k} {v}건" for k, v in tally.items())
                  + f" (총 {len(results)}건)")
        for p, why in failures:
            print(f"[판정 불가] {p.name} — {why}", file=sys.stderr)

    sys.exit(1 if failures else 0)


if __name__ == "__main__":
    main()
