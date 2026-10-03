"""고쳤던 버그가 다시 생기지 않는지 보는 회귀 테스트.

    python3 -m unittest discover -s tests -v

네트워크를 쓰지 않는다. 각 테스트는 실제로 났던 버그 하나에 대응하고, docstring에 그 증상을 적는다.
표준 라이브러리만 쓴다(이 저장소의 스크립트와 같다).
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import fetch_macro as M  # noqa: E402
import fetch_ohlcv as F  # noqa: E402
import technicals as T  # noqa: E402
import validate_brief as V  # noqa: E402
import valuation_band as B  # noqa: E402

SEOUL = ZoneInfo("Asia/Seoul")


def at(day: str, hour: int = 7) -> datetime:
    """서울 현지 그 날 그 시각(UTC로)."""
    d = date.fromisoformat(day)
    return datetime(d.year, d.month, d.day, hour, tzinfo=SEOUL).astimezone(timezone.utc)


class WeeklyBarOnWeekend(unittest.TestCase):
    """토·일 실행에서 금요일에 끝난 주의 주봉을 '진행 중'으로 보고 빼던 버그 (2026-10-03 실행에서 관찰)."""

    bars = [{"date": "2026-09-21", "t": 0}, {"date": "2026-09-28", "t": 0}]

    def test_saturday_keeps_finished_week(self):
        self.assertIsNone(T.unconfirmed_reason(self.bars, "weekly", {}, SEOUL, at("2026-10-03")))

    def test_weekday_still_drops_running_week(self):
        self.assertEqual(T.unconfirmed_reason(self.bars, "weekly", {}, SEOUL, at("2026-10-01")),
                         "이번 주 진행 중인 봉")

    def test_duplicate_bar_dropped_even_on_weekend(self):
        dup = self.bars + [{"date": "2026-10-02", "t": 0}]
        self.assertEqual(T.unconfirmed_reason(dup, "weekly", {}, SEOUL, at("2026-10-03")),
                         "직전 봉과 같은 주를 담은 중복 봉")

    def test_fetch_side_agrees(self):
        last = int(at("2026-09-28", 9).timestamp())
        s = {"last_ts": last, "ex_tz": SEOUL, "session": None}
        self.assertIsNone(F.unconfirmed_reason(s, "1wk", at("2026-10-03").timestamp()))
        self.assertIsNotNone(F.unconfirmed_reason(s, "1wk", at("2026-10-01").timestamp()))


class JudgeAtFetchTime(unittest.TestCase):
    """미확정 봉을 계산 시각으로 판정해, 같은 파일을 다음 주에 다시 넣으면 값이 달라지던 버그."""

    def test_load_uses_sidecar_time(self):
        with tempfile.TemporaryDirectory() as d:
            out = Path(d) / "X-1d.json"
            ts = [int(at("2026-08-01").timestamp()) + 86400 * i for i in range(40)]
            payload = {"chart": {"result": [{
                "meta": {"exchangeTimezoneName": "Asia/Seoul", "currency": "KRW"},
                "timestamp": ts,
                "indicators": {"quote": [{k: [100.0] * 40 for k in ("open", "high", "low", "close")}
                                         | {"volume": [1] * 40}]},
            }]}}
            fetched = F.save(payload, out, "X", "3mo", "1d")
            data = T.load(out, "close")
            self.assertTrue(data["as_of_known"])
            self.assertEqual(data["as_of"], fetched.replace(microsecond=0))

            F.sidecar(out).unlink()
            self.assertFalse(T.load(out, "close")["as_of_known"])


class BandSegmentsByRow(unittest.TestCase):
    """연달아 나온 두 공시의 분모 값이 같으면 구간 표와 '직전 분모'가 섞이던 버그."""

    def test_equal_values_stay_separate(self):
        start = date(2026, 1, 1)
        bars = [{"date": (start + timedelta(days=i)).isoformat(), "close": 100.0 + i} for i in range(60)]
        denom = {"metric": "EPS", "series": [
            {"effective": "2026-01-01", "value": 2.0, "source": "A"},
            {"effective": "2026-01-21", "value": 2.0, "source": "B"},
            {"effective": "2026-02-10", "value": 4.0, "source": "C"},
        ]}
        with tempfile.TemporaryDirectory() as d:
            src = Path(d) / "p.json"
            src.write_text(json.dumps({"chart": {"result": [{}]}}))
            c = B.compute(denom, {"bars": bars, "ex_tz": SEOUL, "meta": {}, "dropped": []}, src, False)
        self.assertEqual([s["source"] for s in c["segments"]], ["A", "B", "C"])
        self.assertEqual([s["bars"] for s in c["segments"]], [20, 20, 20])
        self.assertEqual(c["prior"]["source"], "B")

    def test_non_positive_denominator_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "den.json"
            p.write_text(json.dumps({"metric": "EPS", "series": [
                {"effective": "2026-01-01", "value": -1.5, "source": "A"}]}))
            with self.assertRaises(SystemExit):
                B.load_denominator(p)


class ProseCheckSkipsQuotes(unittest.TestCase):
    """기사 제목(선정 계기)의 '전망이다' 때문에 본문이 멀쩡한 브리프의 커밋이 막히던 위험."""

    def errors(self, lines):
        rep = V.Report(Path("x.md"))
        V.check_prose(rep, lines + ["투자 권유가 아닙니다"])
        return rep.errors

    def test_news_title_and_code_are_quotes(self):
        lines = ["- (선정 계기) [매체] 반도체 수혜 전망이다 https://example.com",
                 "```", "echo 가능성이 높다", "```"]
        self.assertEqual(self.errors(lines), [])

    def test_body_still_checked(self):
        self.assertEqual(len(self.errors(["주가는 오를 것이다."])), 1)


class MacroLag(unittest.TestCase):
    """월별 계열을 관측 기간 첫날로 재서, 정상 일정인데도 매번 '발표 지연'을 찍던 버그."""

    def test_period_end(self):
        self.assertEqual(M.period_end(date(2026, 8, 1), "M"), date(2026, 8, 31))
        self.assertEqual(M.period_end(date(2026, 7, 1), "Q"), date(2026, 9, 30))
        self.assertEqual(M.period_end(date(2025, 1, 1), "A"), date(2025, 12, 31))
        self.assertEqual(M.period_end(date(2026, 12, 1), "M"), date(2026, 12, 31))

    def test_period_of(self):
        self.assertEqual([M.period_of(t) for t in ("2026-09-22", "20260922", "202608", "2026Q3", "2026")],
                         ["D", "D", "M", "Q", "A"])

    def test_quarter_compare_label(self):
        self.assertEqual(M.COMPARE["Q"][0][0], "1분기 전")


class CommitHook(unittest.TestCase):
    """`git add X && git commit`처럼 한 명령 안에서 스테이징하면 훅이 검사 없이 통과시키던 버그."""

    def setUp(self):
        self.repo = Path(tempfile.mkdtemp())
        (self.repo / "scripts" / "hooks").mkdir(parents=True)
        (self.repo / "briefs").mkdir()
        for rel in ("scripts/validate_brief.py", "scripts/hooks/block_unverified_commit.sh"):
            shutil.copy(ROOT / rel, self.repo / rel)
        subprocess.run(["git", "init", "-q"], cwd=self.repo, check=True)
        (self.repo / "briefs" / "2099-01-01-BAD.md").write_text("# bad\n")

    def tearDown(self):
        shutil.rmtree(self.repo)

    def hook(self, command: str) -> int:
        payload = json.dumps({"tool_input": {"command": command}})
        return subprocess.run(["bash", "scripts/hooks/block_unverified_commit.sh"], cwd=self.repo,
                              input=payload, text=True, capture_output=True).returncode

    def test_chained_add_commit_blocked(self):
        self.assertEqual(self.hook("git add briefs/2099-01-01-BAD.md && git commit -m x"), 2)

    def test_unrelated_commit_passes(self):
        self.assertEqual(self.hook("git commit -m 'docs: x'"), 0)

    def test_not_a_commit_passes(self):
        self.assertEqual(self.hook("git log --format=commit briefs/2099-01-01-BAD.md"), 0)


if __name__ == "__main__":
    unittest.main()
