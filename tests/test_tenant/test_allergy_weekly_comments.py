"""주간 브리핑 Δ·자동 코멘트 규칙 테스트 (수집 실패 오탐 수정 검증).

배경: 주간 집계 창이 5일(평일)인데 규칙 4가 `days_with_data < 6` 고정
기준을 써서, 5일 모두 정상 수집돼도 뉴스 Δ ≤ -30% 이면 "수집 실패
가능성" 경고가 상습 발생했다 (2026-07-17 / 07-31 / 08-21 브리핑).
또 news_count 가 헤드라인 수 폴백(한 자릿수)일 때 % 비교가 노이즈였다.

WC-T1  창 길이 5일 & 5일 모두 수집 → 큰 감소여도 규칙 4 미발동
WC-T2  실제 결측일 + 유의미 표본 + 대폭 감소 → 규칙 4 발동 (상대 표기)
WC-T3  소표본(전주 유입 < 30) → 결측일이 있어도 규칙 4 미발동
WC-T4  Δ 는 total_news_inflow(실측 유입 합) 기준으로 계산
"""

from datetime import date, timedelta

from src.tenant.allergy_insight.formatter import AllergyInsightFormatter


def _history(start: date, daily_news_counts: list) -> list:
    """일별 news_count 로 daily_report 히스토리 레코드 생성.

    None 은 해당일 결측(daily_report 미수집)을 의미한다.
    """
    records = []
    for i, count in enumerate(daily_news_counts):
        if count is None:
            continue
        d = start + timedelta(days=i)
        records.append({
            "collected_date": d,
            "data_type": "daily_report",
            "data": {
                "stats": {"news_count": count, "paper_count": 0},
                "top_headlines": [],
                "company_digest": [],
                "papers": [],
            },
        })
    return records


def _weekly(curr_counts, prev_counts, window_days=5):
    fmt = AllergyInsightFormatter()
    curr_start = date(2026, 8, 17)
    prev_start = curr_start - timedelta(days=window_days)
    return fmt.format_weekly(
        _history(curr_start, curr_counts),
        {
            "_prev_history": _history(prev_start, prev_counts),
            "_window_days": window_days,
        },
    )


def _rule4_comments(result):
    return [
        c for c in result.get("auto_comments", [])
        if "수집 실패 가능성" in c["text"]
    ]


class TestWeeklyCollectionFailureRule:
    def test_wc_t1_full_window_no_false_positive(self):
        """5일 창 & 5일 모두 수집 → -42.9% 여도 경고 없음 (기존 오탐 재현 케이스)."""
        result = _weekly(
            curr_counts=[2, 2, 1, 2, 1],     # 합 8
            prev_counts=[3, 3, 2, 5, 1],     # 합 14 → -42.9%
        )
        assert result["deltas"]["news_pct"] <= -30
        assert _rule4_comments(result) == []

    def test_wc_t2_real_missing_day_fires(self):
        """실제 결측 2일 + 전주 유입 ≥ 30 + Δ ≤ -30% → 경고 발동."""
        result = _weekly(
            curr_counts=[10, 8, None, None, 9],   # 합 27, 수집 3/5일
            prev_counts=[15, 12, 14, 10, 9],      # 합 60 → -55%
        )
        comments = _rule4_comments(result)
        assert len(comments) == 1
        assert "3/5일" in comments[0]["text"]
        assert comments[0]["severity"] == "warning"

    def test_wc_t3_small_sample_skipped(self):
        """전주 유입 합 < 30 이면 결측일이 있어도 % 경고 스킵 (노이즈 억제)."""
        result = _weekly(
            curr_counts=[1, 1, None, None, 1],    # 합 3, 수집 3/5일
            prev_counts=[3, 3, 2, 5, 1],          # 합 14 (< 30)
        )
        assert result["deltas"]["news_pct"] <= -30
        assert _rule4_comments(result) == []

    def test_wc_t4_delta_uses_inflow(self):
        """news Δ 는 unique 헤드라인 수가 아닌 일별 news_count 합계 기준."""
        result = _weekly(
            curr_counts=[10, 10, 10, 10, 10],     # 유입 합 50
            prev_counts=[20, 20, 20, 20, 20],     # 유입 합 100
        )
        deltas = result["deltas"]
        assert deltas["news_curr_total"] == 50
        assert deltas["news_prev_total"] == 100
        assert deltas["news_pct"] == -50.0
        # 헤드라인은 양쪽 모두 0건 — unique 기준이었다면 pct 는 None 이 됐을 것
        assert result["summary"]["total_news"] == 0
