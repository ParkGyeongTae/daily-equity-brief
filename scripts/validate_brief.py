#!/usr/bin/env python3
"""브리프가 AGENTS.md의 형식·조건 규칙을 지켰는지 기계적으로 검사한다.

    python3 scripts/validate_brief.py briefs/2026-09-23-005930.md
    python3 scripts/validate_brief.py briefs/*.md          # 전부 검사
    python3 scripts/validate_brief.py --strict briefs/*.md # 경고도 실패로 취급

**이 스크립트가 검사하지 못하는 것**이 검사하는 것보다 중요하다. 숫자가 공시 원문과
일치하는지, 논거가 타당한지, 출처 URL이 실제로 그 숫자를 담고 있는지는 사람과
`brief-verifier` 서브에이전트의 몫이다. 여기서 보는 것은 **기계가 판정할 수 있는 것뿐**이다
— 파일명, 사이트가 파싱하는 머리 두 줄, 빈 절, 면책, 금지 표현, 1절의 후보 점수표, 9절의 구조적
요건(가격·논거 무효화 쌍, R 계산식, 근거 태그), 출처 절의 필수 항목, 기준 종가일 일관성,
거시를 쓴 경우의 관측일·조회일 표기.

통과(exit 0)는 "이 브리프가 옳다"가 아니라 "형식 때문에 틀릴 일은 없다"는 뜻이다.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

FILE_RE = re.compile(r"^(\d{4}-\d{2}-\d{2})-(.+)\.md$")
H1_RE = re.compile(r"^#\s+(.+?)\s*\((.+?)\)\s*—\s*(\d{4}-\d{2}-\d{2})\s*$")
QUOTE_RE = re.compile(r"^>\s*한 줄 요약\s*[:：]\s*(.+)$")
FENCE_RE = re.compile(r"^\s*(`{3,}|~{3,})")
SEP_ROW_RE = re.compile(r"^\|[\s:|-]+\|$")
DATE_RE = re.compile(r"\d{4}-\d{2}-\d{2}")
CLOSE_DATE_RE = re.compile(r"기준 종가일[^0-9]{0,10}(\d{4}-\d{2}-\d{2})")
OBS_DATE_RE = re.compile(r"관측일[^0-9]{0,10}(\d{4}-\d{2}-\d{2})")
# 거시를 **썼을 때만** 검사한다 — 거시는 선택이고, 기본값은 쓰지 않는 것이다.
MACRO_RE = re.compile(r"\b(FRED|ECOS)\b")
TAGS = ("[공시]", "[기술]", "[이벤트]")

# 절 번호 → 제목 일부. 템플릿(AGENTS.md "보고서 템플릿")과 같은 순서다.
NUMBERED = {
    0: "스냅샷", 1: "왜 지금 이 종목인가", 2: "사업 구조", 3: "연혁", 4: "핵심 지표",
    5: "재무 해석", 6: "밸류에이션", 7: "기술적 위치", 8: "논점", 9: "매매 계획",
    10: "확인해야 할 것",
}
# 사유를 적어도 지울 수 없는 절 — 이 저장소가 존재하는 이유에 해당한다.
MANDATORY = (1, 7, 9)

# 예측 서술·권유 표현. AGENTS.md "하지 않는 것"을 문자열로 옮긴 것이다.
BANNED = [
    (r"가능성이 (높|크)", "확률 서술"),
    (r"(으로|것으로) 보인다", "예측 서술"),
    (r"(으로|것으로) 예상", "예측 서술"),
    (r"전망(이다|된다|이며|입니다)", "예측 서술"),
    (r"기대된다", "예측 서술"),
    (r"(오를|내릴|상승할|하락할) 것", "예측 서술"),
    (r"추천", "권유 표현"),
    (r"사야 한다|사세요|팔아야 한다|파세요", "권유 표현"),
    (r"매수를 권|매도를 권", "권유 표현"),
]
# 템플릿을 채우지 않고 남긴 흔적
PLACEHOLDERS = [r"<종목명>", r"<코드/티커>", r"<조건>", r"<있으면>", r"\bTBD\b", r"\bTODO\b"]

# 한 줄 요약 첫 줄의 상한 (AGENTS.md "읽는 사람을 전제한다").
SUMMARY_MAX = 60
# 0절 "현재 스탠스"가 고르는 값. 요약 둘째 줄도 이 중 하나로 시작한다.
STANCES = ("진입 대기", "조건 충족", "관심", "관망", "보류")

# 처음 나올 때 괄호로 뜻을 풀어야 하는 용어.
# **목록에 없다고 안 풀어도 된다는 뜻이 아니다** — 브리프마다 반복되는 것만 기계가 잡는다.
GLOSS_TERMS = [
    "적응증", "현금창출단위", "부문간 제거", "영업권", "순차입금", "무상증자", "물적분할",
    "공정공시", "미확정 봉", "TTM", "FCF",
]
# 7절 지표 표에서 "비고" 칸을 비워둘 수 없는 약어.
INDICATOR_ROWS = re.compile(r"^(SMA|RSI|MACD|ATR|볼린저|%B)")


class Report:
    def __init__(self, path: Path):
        self.path = path
        self.errors: list[tuple[int, str]] = []
        self.warns: list[tuple[int, str]] = []

    def err(self, line: int, msg: str) -> None:
        self.errors.append((line, msg))

    def warn(self, line: int, msg: str) -> None:
        self.warns.append((line, msg))


def split_sections(lines: list[str]) -> list[dict]:
    """코드펜스 안의 '## '는 헤딩으로 세지 않는다 (템플릿·명령 예시가 들어 있다)."""
    sections: list[dict] = []
    fence: str | None = None
    for i, raw in enumerate(lines, start=1):
        m = FENCE_RE.match(raw)
        if m:
            marker = m.group(1)[0]
            if fence is None:
                fence = marker
            elif marker == fence:
                fence = None
            continue
        if fence is None and raw.startswith("## "):
            sections.append({"line": i, "title": raw[3:].strip(), "body": []})
        elif sections:
            sections[-1]["body"].append((i, raw))
    return sections


def section_by_number(sections: list[dict], n: int) -> dict | None:
    for s in sections:
        if re.match(rf"^{n}\.\s", s["title"]):
            return s
    return None


def section_by_name(sections: list[dict], name: str) -> dict | None:
    for s in sections:
        if s["title"].startswith(name):
            return s
    return None


def body_text(section: dict | None) -> str:
    return "\n".join(t for _, t in section["body"]) if section else ""


def check_head(rep: Report, lines: list[str], file_date: str, file_code: str) -> None:
    """사이트 목록(.vitepress/briefs.mjs)이 파싱하는 머리 두 줄."""
    meaningful = [(i, t) for i, t in enumerate(lines, start=1) if t.strip()]
    if not meaningful:
        rep.err(0, "파일이 비어 있다")
        return

    i, first = meaningful[0]
    m = H1_RE.match(first)
    if not m:
        rep.err(i, "첫 h1이 템플릿 형식이 아니다 — `# <종목명> (<코드>) — YYYY-MM-DD` (사이트 목록이 이 줄을 파싱한다)")
    else:
        _, code, date = m.groups()
        if date != file_date:
            rep.err(i, f"h1 날짜({date})가 파일명 날짜({file_date})와 다르다")
        if file_code.lower() not in code.lower():
            rep.err(i, f"h1의 코드({code})에 파일명 코드({file_code})가 없다")

    if len(meaningful) < 2:
        rep.err(i, "한 줄 요약 인용문이 없다")
        return
    j, second = meaningful[1]
    q = QUOTE_RE.match(second)
    if not q:
        rep.err(j, "둘째 줄이 `> 한 줄 요약: ...` 형식이 아니다 (사이트 목록이 이 줄을 파싱한다)")
        return

    head = q.group(1).strip()
    if len(head) < 10:
        rep.warn(j, "한 줄 요약이 지나치게 짧다")
    if len(head) > SUMMARY_MAX:
        rep.err(
            j,
            f"한 줄 요약 첫 줄이 {len(head)}자다 ({SUMMARY_MAX}자 이내) — "
            "결론 한 문장만 남기고 조건은 9절로 내린다",
        )

    # 셋째 줄: 스탠스와 그 가격. 사이트 목록은 두 줄을 이어 붙인다.
    if len(meaningful) < 3 or not meaningful[2][1].lstrip().startswith(">"):
        rep.err(j, "한 줄 요약 둘째 줄이 없다 — `> <스탠스> — <가격 조건>` 한 줄을 인용문에 잇는다")
        return
    k, third = meaningful[2]
    stance = third.lstrip().lstrip(">").strip()
    if not any(stance.startswith(w) for w in STANCES):
        rep.err(k, f"요약 둘째 줄이 스탠스로 시작하지 않는다 — {' / '.join(STANCES)} 중 하나")
        return

    # 0절 "현재 스탠스"와 어긋나면 둘 중 하나가 낡은 것이다.
    head_w = next((w for w in STANCES if stance.startswith(w)), None)
    for _, t in lines_with_stance(lines):
        cells = [c.strip() for c in t.strip().strip("|").split("|")]
        if len(cells) >= 2:
            snap_w = next((w for w in STANCES if cells[1].startswith(w)), None)
            if snap_w and head_w and snap_w != head_w:
                rep.err(k, f"요약 둘째 줄의 스탠스({head_w})가 0절 '현재 스탠스'({snap_w})와 다르다")
        break


def lines_with_stance(lines: list[str]) -> list[tuple[int, str]]:
    return [(i, t) for i, t in enumerate(lines, start=1)
            if t.lstrip().startswith("|") and "현재 스탠스" in t]


def check_sections(rep: Report, sections: list[dict]) -> None:
    titles = [s["title"] for s in sections]
    if not sections:
        rep.err(0, "`##` 절이 하나도 없다")
        return

    method = section_by_name(sections, "방법론")
    omitted_line = ""
    for _, t in (method["body"] if method else []):
        if "생략한 절" in t:
            omitted_line = t

    missing = []
    for n, name in NUMBERED.items():
        if section_by_number(sections, n) is None:
            missing.append((n, name))
    for n, name in missing:
        if n in MANDATORY:
            rep.err(0, f"{n}절({name})은 생략할 수 없다")
        elif not re.search(rf"(?<!\d){n}(?!\d)", omitted_line):
            rep.err(0, f"{n}절({name})이 없는데 `## 방법론 · 재현`의 '생략한 절' 줄에 사유가 없다")

    for want in ("출처", "방법론"):
        if section_by_name(sections, want) is None:
            rep.err(0, f"`## {want}` 절이 없다")

    for s in sections:
        if not any(t.strip() for _, t in s["body"]):
            rep.err(s["line"], f"빈 절 — `## {s['title']}` (해당 없으면 절을 지우고 사유를 방법론에 남긴다)")

    seen = set()
    for s in sections:
        if s["title"] in seen:
            rep.err(s["line"], f"절 제목이 중복된다 — `## {s['title']}`")
        seen.add(s["title"])
    _ = titles


def check_prose(rep: Report, lines: list[str]) -> None:
    for i, raw in enumerate(lines, start=1):
        if raw.lstrip().startswith("<!--"):
            continue
        for pat, label in BANNED:
            m = re.search(pat, raw)
            if m:
                rep.err(i, f"{label} 금지 — \"{m.group(0)}\" (조건문으로 바꾸거나 지운다)")
        for pat in PLACEHOLDERS:
            if re.search(pat, raw):
                rep.err(i, f"템플릿 자리표시자가 남아 있다 — {pat}")

    text = "\n".join(lines)
    if "투자 권유가 아닙니다" not in text:
        rep.err(0, "면책 문구가 없다 — 템플릿 마지막 줄")


def check_glossary(rep: Report, lines: list[str], sections: list[dict]) -> None:
    """처음 나온 전문 용어를 풀었는가 (AGENTS.md "읽는 사람을 전제한다").

    기계가 볼 수 있는 것은 "괄호가 붙었는가"뿐이다. 괄호 안의 설명이 실제로 도움이 되는지는
    `brief-verifier`가 본다. 그래서 전부 경고다 — 커밋을 막지 않고 눈에만 띄게 한다.
    """
    for term in GLOSS_TERMS:
        for i, raw in enumerate(lines, start=1):
            if term not in raw:
                continue
            # 첫 등장 줄에서 용어 바로 뒤가 여는 괄호여야 한다.
            after = raw.split(term, 1)[1].lstrip()
            if not after.startswith(("(", "（")):
                rep.warn(i, f"'{term}' — 처음 나올 때 뜻 풀이가 없다. 괄호로 한 번 푼다")
            break  # 첫 등장만 본다

    sec = section_by_number(sections, 7)
    if sec is None:
        return
    for i, row in table_rows(sec):
        cells = [c.strip() for c in row.strip("|").split("|")]
        if len(cells) < 3 or not INDICATOR_ROWS.match(cells[0]):
            continue
        if not cells[-1]:
            rep.warn(i, f"7절 '{cells[0]}' 행의 비고가 비었다 — 무엇을 잰 숫자인지 한 구절로 적는다")


def table_rows(section: dict) -> list[tuple[int, str]]:
    """표의 데이터 행만 — 헤더와 구분선은 뺀다."""
    rows, after_sep = [], False
    for i, t in section["body"]:
        s = t.strip()
        if not s.startswith("|"):
            after_sep = False
            continue
        if SEP_ROW_RE.match(s):
            after_sep = True
            continue
        if after_sep:
            rows.append((i, s))
    return rows


def check_plan(rep: Report, sections: list[dict]) -> None:
    sec = section_by_number(sections, 9)
    if sec is None:
        return
    text = body_text(sec)
    ln = sec["line"]

    for want, why in (("가격 무효화", "가격으로 쓰는 손절 조건"), ("논거 무효화", "공시로 쓰는 손절 조건")):
        if want not in text:
            rep.err(ln, f"9절에 '{want}' 행이 없다 — {why}이며, 진입 조건과 같은 정밀도로 반드시 적는다")

    if not re.search(r"\bR\s*=", text):
        rep.err(ln, "9절에 R 계산식이 없다 — `R = 진입가 − 무효화가` 한 줄")
    if "우선" not in text:
        rep.err(ln, "9절에 조건 충돌 시 우선순위 줄이 없다 (기본값: 논거 무효화 > 가격 무효화 > 진입)")

    rows = table_rows(sec)
    if not rows:
        rep.err(ln, "9절에 매매 조건 표가 없다")
    for i, row in rows:
        if not any(tag in row for tag in TAGS):
            cells = [c.strip() for c in row.strip("|").split("|")]
            head = cells[0] if cells else row
            rep.err(i, f"9절 '{head}' 행에 근거 태그가 없다 — [공시]/[기술]/[이벤트] 중 하나")


def check_selection(rep: Report, sections: list[dict]) -> None:
    """선정을 사용자에게 묻지 않으므로, 점수표가 1절에 남아야 근거를 되짚을 수 있다."""
    sec = section_by_number(sections, 1)
    if sec is None:
        return
    rows = table_rows(sec)
    text = body_text(sec)
    if not rows or "합계" not in text:
        rep.err(sec["line"], "1절에 후보 점수표가 없다 — ①~⑤와 합계를 탈락 후보까지 싣는다(선정을 묻지 않는 대신 남기는 근거다)")
    elif len(rows) < 2:
        rep.warn(sec["line"], "1절 점수표에 후보가 하나뿐이다 — 탈락 후보도 함께 남긴다")


def check_sources(rep: Report, sections: list[dict], file_date: str) -> None:
    sec = section_by_name(sections, "출처")
    if sec is None:
        return
    text = body_text(sec)
    ln = sec["line"]

    if "http" not in text:
        rep.err(ln, "출처 절에 URL이 없다")
    if "(선정 계기)" not in text:
        rep.err(ln, "출처 절에 `(선정 계기)` 뉴스가 없다 — 선정 근거와 분석 근거는 구분해 적는다")

    market = [t for _, t in sec["body"] if "(시장 데이터)" in t]
    if not market:
        rep.err(ln, "출처 절에 `(시장 데이터)` 줄이 없다")
    else:
        line = market[0]
        if "KST" not in line:
            rep.err(ln, "`(시장 데이터)` 줄에 조회 시각(KST)이 없다")
        if not CLOSE_DATE_RE.search(line):
            rep.err(ln, "`(시장 데이터)` 줄에 `기준 종가일 YYYY-MM-DD`가 없다")

    dates = {m.group(1) for m in CLOSE_DATE_RE.finditer("\n".join(t for _, t in sec["body"]))}
    if dates and max(dates) > file_date:
        rep.err(ln, f"기준 종가일({max(dates)})이 브리프 날짜({file_date})보다 미래다")


def check_close_date(rep: Report, lines: list[str], sections: list[dict]) -> None:
    found: dict[str, int] = {}
    for i, raw in enumerate(lines, start=1):
        for m in CLOSE_DATE_RE.finditer(raw):
            found.setdefault(m.group(1), i)
    if len(found) > 1:
        where = ", ".join(f"{d}(L{n})" for d, n in sorted(found.items()))
        rep.err(0, f"기준 종가일이 여러 개다 — {where}. 보고서의 모든 가격은 같은 기준일을 쓴다")

    tech = section_by_number(sections, 7)
    if tech is not None and "기준 종가일" not in body_text(tech):
        rep.err(tech["line"], "7절에 기준 종가일이 없다")


def check_method(rep: Report, sections: list[dict]) -> None:
    sec = section_by_name(sections, "방법론")
    if sec is None:
        return
    text = body_text(sec)
    ln = sec["line"]
    if "technicals.py" not in text:
        rep.err(ln, "방법론 절에 technicals.py 커맨드가 없다 — `--emit facts` 출력을 그대로 붙인다")
    if "fetch_ohlcv.py" not in text:
        rep.err(ln, "방법론 절에 원자료 수집 커맨드(fetch_ohlcv.py)가 없다")
    if "--price-field" not in text:
        rep.err(ln, "방법론 절에 수정주가 기준(--price-field)이 없다")


def check_macro(rep: Report, lines: list[str], sections: list[dict], file_date: str) -> None:
    """거시를 쓴 브리프만 검사한다.

    거시는 개정(revision)되므로 **관측일과 조회일을 함께** 적어야 되짚을 수 있다.
    가격에 기준 종가일을 요구하는 것과 같은 이유다. 게이트(공시에 연결고리가 있는가)는
    기계가 판정하지 못한다 — 그건 `brief-verifier`와 사람의 몫이고, 여기서는 **표기만** 본다.
    """
    if not MACRO_RE.search("\n".join(lines)):
        return

    sec = section_by_name(sections, "출처")
    if sec is not None:
        ln = sec["line"]
        macro = [t for _, t in sec["body"] if "(거시)" in t]
        if not macro:
            rep.err(ln, "본문이 FRED/ECOS를 쓰는데 출처 절에 `(거시)` 줄이 없다")
        for line in macro:
            if not (m := OBS_DATE_RE.search(line)):
                rep.err(ln, "`(거시)` 줄에 `관측일 YYYY-MM-DD`가 없다 — 거시는 개정되므로 조회일만으로는 되짚지 못한다")
            elif m.group(1) > file_date:
                rep.err(ln, f"`(거시)` 줄의 관측일({m.group(1)})이 브리프 날짜({file_date})보다 미래다")
            if "KST" not in line:
                rep.err(ln, "`(거시)` 줄에 조회 시각(KST)이 없다")

    sec = section_by_name(sections, "방법론")
    if sec is not None and "거시" not in body_text(sec):
        rep.err(sec["line"], "거시를 썼는데 방법론 절에 `거시 지표:` 항목이 없다 "
                             "— 쓴 계열과 게이트를 통과한 근거(공시 문장)를 적는다")


def validate(path: Path) -> Report:
    rep = Report(path)
    m = FILE_RE.match(path.name)
    if not m:
        rep.err(0, "파일명이 규칙에 맞지 않는다 — `YYYY-MM-DD-<종목코드 또는 티커>.md` (사이트 목록이 이 이름을 파싱한다)")
        return rep
    file_date, file_code = m.groups()

    lines = path.read_text(encoding="utf-8").splitlines()
    sections = split_sections(lines)

    check_head(rep, lines, file_date, file_code)
    check_sections(rep, sections)
    check_prose(rep, lines)
    check_glossary(rep, lines, sections)
    check_plan(rep, sections)
    check_selection(rep, sections)
    check_sources(rep, sections, file_date)
    check_close_date(rep, lines, sections)
    check_method(rep, sections)
    check_macro(rep, lines, sections, file_date)
    return rep


def main() -> int:
    ap = argparse.ArgumentParser(description="브리프 형식·조건 규칙 검사 (AGENTS.md 검증 체크리스트의 기계 검사 부분)")
    ap.add_argument("paths", nargs="+", type=Path)
    ap.add_argument("--strict", action="store_true", help="경고도 실패로 취급")
    args = ap.parse_args()

    failed = False
    for path in args.paths:
        if not path.exists():
            print(f"✗ {path} — 파일이 없다")
            failed = True
            continue
        # briefs/*.md 로 부르면 목록 페이지까지 잡힌다. 사이트 파서(.vitepress/briefs.mjs)도
        # index.md 를 브리프로 보지 않으므로 여기서도 조용히 넘긴다.
        if path.name == "index.md":
            continue
        rep = validate(path)
        for line, msg in sorted(rep.errors):
            print(f"✗ {path}:{line or '-'} {msg}")
        for line, msg in sorted(rep.warns):
            print(f"! {path}:{line or '-'} {msg}")
        if rep.errors or (args.strict and rep.warns):
            failed = True
        elif not rep.warns:
            print(f"✓ {path} — 형식 검사 통과 (숫자·논거의 정확성은 검사하지 않는다)")
        else:
            print(f"✓ {path} — 오류 없음, 경고 {len(rep.warns)}건")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
