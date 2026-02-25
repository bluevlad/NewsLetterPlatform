"""
TeacherHub 데이터 포매터
API 응답 → 템플릿 변수 변환

Data Sources:
  - daily_report: PeriodReportDTO (totalTeachers, totalMentions, avgSentimentScore, teacherSummaries)
  - weekly_summary: WeeklySummaryDTO (totalMentions, totalTeachers, avgSentimentScore, weekLabel)
  - weekly_ranking: List<WeeklyReportDTO> (teacherName, academyName, mentionCount, avgSentimentScore)
  - academy_stats: List (academyName, postCount/mentionCount, avgSentiment) — AcademyInsight 통합
"""

import logging
from datetime import datetime
from typing import Any, Dict

logger = logging.getLogger(__name__)


class TeacherHubFormatter:
    """TeacherHub 데이터 포매터"""

    def format(self, collected_data: Dict[str, Any]) -> Dict[str, Any]:
        """수집 데이터를 템플릿 컨텍스트로 변환"""
        daily_report = collected_data.get("daily_report", {})
        weekly_summary = collected_data.get("weekly_summary", {})
        weekly_ranking = collected_data.get("weekly_ranking", [])

        # 통계: 주간 요약 우선, 없으면 일일 리포트에서
        stats = {
            "total_teachers": (
                weekly_summary.get("totalTeachers")
                or daily_report.get("totalTeachers", 0)
            ),
            "total_mentions": (
                weekly_summary.get("totalMentions")
                or daily_report.get("totalMentions", 0)
            ),
            "avg_sentiment": (
                weekly_summary.get("avgSentimentScore")
                or daily_report.get("avgSentimentScore", 0)
            ),
            "new_mentions_today": daily_report.get("totalMentions", 0),
        }

        # 감성 점수를 퍼센트로 변환 (0~1 → 0~100)
        if stats["avg_sentiment"] and stats["avg_sentiment"] <= 1:
            stats["avg_sentiment"] = round(stats["avg_sentiment"] * 100, 1)

        # TOP 강사 (주간 랭킹 상위 5명)
        top_teachers = []
        for teacher in weekly_ranking[:5]:
            sentiment = teacher.get("avgSentimentScore", 0) or 0
            if sentiment <= 1:
                sentiment = round(sentiment * 100, 1)
            top_teachers.append({
                "name": teacher.get("teacherName", ""),
                "academy": teacher.get("academyName", ""),
                "mention_count": teacher.get("mentionCount", 0),
                "sentiment_score": sentiment,
                "trend": "up" if (teacher.get("mentionChangeRate") or 0) > 0 else "stable",
            })

        # 주간 라벨
        week_label = weekly_summary.get("weekLabel", "")

        # 일일 리포트 하이라이트 (teacherSummaries에서 추출)
        highlights = self._extract_highlights(daily_report, weekly_ranking)

        report_details = {
            "period_start": daily_report.get("startDate", ""),
            "period_end": daily_report.get("endDate", ""),
            "highlights": highlights,
        }

        # 학원 랭킹 (academy_stats에서 변환)
        academy_stats = collected_data.get("academy_stats", [])
        academy_ranking = self._format_academy_ranking(academy_stats)

        return {
            "stats": stats,
            "top_teachers": top_teachers,
            "ranking": weekly_ranking[:10],
            "report": report_details,
            "academy_ranking": academy_ranking,
            "week_label": week_label,
            "report_date": datetime.now(),
            "generated_at": datetime.now(),
        }

    @staticmethod
    def _format_academy_ranking(academy_stats: list) -> list:
        """학원 통계 데이터를 랭킹 형태로 변환"""
        ranking = []
        for academy in academy_stats[:10]:
            mention_count = (
                academy.get("mentionCount")
                or academy.get("postCount", 0)
            )
            avg_sentiment = academy.get("avgSentiment", 0) or 0
            if avg_sentiment <= 1:
                avg_sentiment = round(avg_sentiment * 100, 1)
            ranking.append({
                "name": academy.get("academyName", ""),
                "mention_count": mention_count,
                "avg_sentiment": avg_sentiment,
                "trend": academy.get("trend", "stable"),
            })
        return ranking

    @staticmethod
    def _extract_highlights(daily_report: dict, weekly_ranking: list) -> list:
        """데이터에서 주요 하이라이트 자동 생성"""
        highlights = []

        # 일일 리포트에서 언급 많은 강사
        teacher_summaries = daily_report.get("teacherSummaries", [])
        if teacher_summaries:
            top = teacher_summaries[0]
            name = top.get("teacherName", "")
            mentions = top.get("mentionCount", 0)
            if name and mentions > 0:
                highlights.append(f"{name} 강사 오늘 최다 언급 ({mentions}건)")

        # 주간 랭킹에서 감성 점수 높은 강사
        if weekly_ranking:
            best_sentiment = max(
                weekly_ranking[:5],
                key=lambda t: t.get("avgSentimentScore", 0) or 0,
                default=None
            )
            if best_sentiment:
                name = best_sentiment.get("teacherName", "")
                score = best_sentiment.get("avgSentimentScore", 0) or 0
                if score <= 1:
                    score = round(score * 100, 1)
                if name:
                    highlights.append(f"{name} 강사 감성 점수 {score}%로 최상위")

            # 1위 강사 언급량
            top_ranked = weekly_ranking[0]
            name = top_ranked.get("teacherName", "")
            mentions = top_ranked.get("mentionCount", 0)
            academy = top_ranked.get("academyName", "")
            if name and mentions > 0:
                highlights.append(f"{academy} {name} 강사 주간 1위 (언급 {mentions}건)")

        return highlights
