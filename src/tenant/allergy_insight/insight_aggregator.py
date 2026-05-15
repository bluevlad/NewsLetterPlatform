"""AllergyInsight Weekly Insight Aggregator (Phase 4 스켈레톤).

진단키트 담당부서 주간회의용 1페이지 인사이트 브리프의 데이터 가공 레이어.
`collected_data_history` 의 84일치(12주) 일별 스냅샷을 받아 주차 단위로 집계하고,
부서 watch_list 기준의 키워드 매트릭스와 핵심 지표 카드를 생성한다.

Phase 5(이상 신호 + 엔터티 트렌드), Phase 6(weekly_insight.html + cron) 는 본 모듈을
호출만 한다 — 발송 로직과는 의도적으로 분리.
"""

from __future__ import annotations

import logging
from collections import Counter
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path
from statistics import mean, pstdev
from typing import Any, Dict, Iterable, List, Optional

import yaml

logger = logging.getLogger(__name__)

CONFIG_PATH = Path(__file__).resolve().parents[3] / "config" / "tenants.yaml"


@dataclass
class WeeklyBucket:
    """월요일~일요일 1주 단위 집계 버킷.

    period_start/period_end 는 폐구간(inclusive). days_with_data 는 실제로
    daily_report 가 있던 날 수 — anomaly 판정 시 분모로 사용.
    """

    period_start: date
    period_end: date
    news_count: int = 0
    paper_count: int = 0
    days_with_data: int = 0
    importance_high_count: int = 0
    importance_total_count: int = 0
    keyword_counter: Counter = field(default_factory=Counter)
    company_counter: Counter = field(default_factory=Counter)
    category_counter: Counter = field(default_factory=Counter)

    @property
    def importance_high_ratio(self) -> float:
        if self.importance_total_count == 0:
            return 0.0
        return round(
            self.importance_high_count / self.importance_total_count, 3
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "period_start": self.period_start.isoformat(),
            "period_end": self.period_end.isoformat(),
            "news_count": self.news_count,
            "paper_count": self.paper_count,
            "days_with_data": self.days_with_data,
            "importance_high_count": self.importance_high_count,
            "importance_total_count": self.importance_total_count,
            "importance_high_ratio": self.importance_high_ratio,
            "top_keywords": [
                {"keyword": k, "count": c}
                for k, c in self.keyword_counter.most_common(10)
            ],
            "top_companies": [
                {"company": k, "count": c}
                for k, c in self.company_counter.most_common(10)
            ],
            "top_categories": [
                {"category": k, "count": c}
                for k, c in self.category_counter.most_common()
            ],
        }


def load_insight_brief_config(
    tenant_id: str, path: Optional[Path] = None
) -> Dict[str, Any]:
    """config/tenants.yaml 에서 `tenants.{tenant_id}.insight_brief` 추출.

    파일/섹션 결측 시 빈 dict + INFO 로그. yaml 로딩 실패는 warning 후 빈 dict 반환
    (서비스 흐름 중단 방지).
    """
    yaml_path = path or CONFIG_PATH
    if not yaml_path.exists():
        logger.info(
            "[insight_brief] config 파일 없음: %s — 빈 설정 반환", yaml_path
        )
        return {}
    try:
        with yaml_path.open("r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
    except yaml.YAMLError as e:
        logger.warning("[insight_brief] yaml 파싱 실패 %s: %s", yaml_path, e)
        return {}
    tenant_block = (data.get("tenants") or {}).get(tenant_id) or {}
    return tenant_block.get("insight_brief") or {}


class WeeklyInsightAggregator:
    """12주 누적 인사이트 데이터 가공 — Phase 4 스켈레톤.

    Usage:
        agg = WeeklyInsightAggregator(watch_list={"keywords": [...]})
        buckets = agg.aggregate_weekly_buckets(history_data, weeks=12)
        keyword_matrix = agg.compute_keyword_matrix(
            buckets, agg.watch_list.get("keywords", [])
        )
        summary = agg.compute_summary_metrics(buckets)

    history_data 형식은 `CollectedDataRepository.get_history_range()` 와 동일:
        [{"collected_date": date, "data_type": "daily_report", "data": {...}}, ...]
    """

    HIGH_IMPORTANCE_THRESHOLD = 0.7

    def __init__(self, watch_list: Optional[Dict[str, List[str]]] = None) -> None:
        self.watch_list = watch_list or {}

    # ------------------------------------------------------------------
    # 주차 버킷 집계
    # ------------------------------------------------------------------
    def aggregate_weekly_buckets(
        self,
        history_data: List[Dict[str, Any]],
        weeks: int = 12,
        anchor: Optional[date] = None,
    ) -> List[WeeklyBucket]:
        """history_data 를 월요일~일요일 단위 weeks 개 버킷으로 집계.

        Args:
            history_data: get_history_range() 결과.
            weeks: 버킷 개수 (기본 12).
            anchor: 가장 최근 버킷의 종료일 기준. 미지정 시 오늘이 속한 주의 일요일.

        Returns:
            기간 오름차순으로 정렬된 weeks 개의 WeeklyBucket. 데이터가 없는 주도
            빈 버킷을 만들어 시계열의 빈 자리(0)가 보존되게 한다.
        """
        if anchor is None:
            today = date.today()
            anchor = today + timedelta(days=(6 - today.weekday()))  # 이번주 일요일
        # 최신 버킷의 [월, 일]
        latest_end = anchor
        latest_start = latest_end - timedelta(days=6)

        buckets: List[WeeklyBucket] = []
        for i in range(weeks - 1, -1, -1):  # 오래된 → 최신
            end = latest_end - timedelta(days=7 * i)
            start = latest_start - timedelta(days=7 * i)
            buckets.append(WeeklyBucket(period_start=start, period_end=end))

        # 일별 record 를 버킷에 분배
        for record in history_data:
            d = record.get("collected_date")
            if not isinstance(d, date):
                continue
            if record.get("data_type") != "daily_report":
                continue
            bucket = self._find_bucket(buckets, d)
            if bucket is None:
                continue  # weeks 범위 밖
            self._merge_day_into_bucket(bucket, record.get("data") or {})

        return buckets

    @staticmethod
    def _find_bucket(buckets: List[WeeklyBucket], d: date) -> Optional[WeeklyBucket]:
        for b in buckets:
            if b.period_start <= d <= b.period_end:
                return b
        return None

    def _merge_day_into_bucket(
        self, bucket: WeeklyBucket, daily_report: Dict[str, Any]
    ) -> None:
        stats = daily_report.get("stats") or {}
        bucket.news_count += stats.get("news_count", 0) or 0
        bucket.paper_count += stats.get("paper_count", 0) or 0
        bucket.days_with_data += 1

        # headlines 누적: 키워드/카테고리/중요도/기업 카운터
        headlines = daily_report.get("top_headlines") or daily_report.get(
            "top_news", []
        )
        for news in headlines:
            score = news.get("importance_score") or 0
            bucket.importance_total_count += 1
            if score >= self.HIGH_IMPORTANCE_THRESHOLD:
                bucket.importance_high_count += 1

            kw = news.get("keyword") or news.get("search_keyword")
            if kw:
                bucket.keyword_counter[kw] += 1

            cat = news.get("category")
            if cat:
                bucket.category_counter[cat] += 1

        company_digest = daily_report.get("company_digest") or daily_report.get(
            "company_news", []
        )
        for c in company_digest:
            name = c.get("company_name") or c.get("name")
            if name:
                bucket.company_counter[name] += 1

    # ------------------------------------------------------------------
    # 키워드 매트릭스
    # ------------------------------------------------------------------
    def compute_keyword_matrix(
        self, buckets: List[WeeklyBucket], watch_keywords: Iterable[str]
    ) -> List[Dict[str, Any]]:
        """watch_keywords × 12주 시계열 + 4주/12주 합계·평균·트렌드.

        반환 항목 (keyword별 1행):
            {
                "keyword": "면역글로불린E",
                "series": [0, 1, 2, 0, ...],  # 길이 = len(buckets)
                "total_12w": 7,
                "total_4w": 3,
                "avg_12w": 0.58,
                "avg_4w": 0.75,
                "trend_ratio": 1.29,  # avg_4w / avg_12w (1 초과 = 상승 추세)
            }

        series 가 모두 0 인 키워드도 포함 — 매트릭스 일관성을 위해(부서가
        "신호가 비어있다" 자체를 인지할 수 있도록).
        """
        watch_keywords = list(watch_keywords)
        if not buckets or not watch_keywords:
            return []

        n = len(buckets)
        recent4_slice = buckets[-4:] if n >= 4 else buckets

        rows: List[Dict[str, Any]] = []
        for kw in watch_keywords:
            series = [b.keyword_counter.get(kw, 0) for b in buckets]
            total_12w = sum(series)
            total_4w = sum(b.keyword_counter.get(kw, 0) for b in recent4_slice)
            avg_12w = round(total_12w / n, 2) if n else 0.0
            avg_4w = (
                round(total_4w / len(recent4_slice), 2) if recent4_slice else 0.0
            )
            if avg_12w > 0:
                trend_ratio = round(avg_4w / avg_12w, 2)
            else:
                trend_ratio = None  # 분모 0 → 신호 없음
            rows.append(
                {
                    "keyword": kw,
                    "series": series,
                    "total_12w": total_12w,
                    "total_4w": total_4w,
                    "avg_12w": avg_12w,
                    "avg_4w": avg_4w,
                    "trend_ratio": trend_ratio,
                }
            )

        # 정렬: 최근 4주 빈도 내림차순 → 12주 합계 → 알파벳
        rows.sort(
            key=lambda r: (-r["total_4w"], -r["total_12w"], r["keyword"])
        )
        return rows

    # ------------------------------------------------------------------
    # 핵심 지표 카드
    # ------------------------------------------------------------------
    def compute_summary_metrics(
        self, buckets: List[WeeklyBucket]
    ) -> Dict[str, Any]:
        """4개 KPI 카드용 데이터 — 이번주값 · 12주 평균 · z-score · sparkline.

        z-score 는 (current - mean) / pstdev. 표본 표준편차가 0 이면 None.
        Phase 5 의 anomaly_detector 가 같은 통계량을 +2σ 임계로 재사용.
        """
        if not buckets:
            return {"buckets_count": 0, "metrics": []}

        latest = buckets[-1]
        history_series = {
            "news_count": [b.news_count for b in buckets],
            "paper_count": [b.paper_count for b in buckets],
            "importance_high_count": [
                b.importance_high_count for b in buckets
            ],
            "importance_high_ratio": [
                b.importance_high_ratio for b in buckets
            ],
        }

        def _stat(series: List[float], current: float) -> Dict[str, Any]:
            avg_12w = round(mean(series), 2) if series else 0.0
            std = pstdev(series) if len(series) > 1 else 0.0
            z = round((current - avg_12w) / std, 2) if std else None
            return {
                "current": current,
                "avg_12w": avg_12w,
                "z_score": z,
                "sparkline": series,
            }

        return {
            "buckets_count": len(buckets),
            "period_start": buckets[0].period_start.isoformat(),
            "period_end": buckets[-1].period_end.isoformat(),
            "metrics": {
                "news_count": _stat(
                    history_series["news_count"], latest.news_count
                ),
                "paper_count": _stat(
                    history_series["paper_count"], latest.paper_count
                ),
                "importance_high_count": _stat(
                    history_series["importance_high_count"],
                    latest.importance_high_count,
                ),
                "importance_high_ratio": _stat(
                    history_series["importance_high_ratio"],
                    latest.importance_high_ratio,
                ),
            },
            "data_quality": {
                "days_with_data_latest": latest.days_with_data,
                "weeks_with_zero_data": sum(
                    1 for b in buckets if b.days_with_data == 0
                ),
            },
        }
