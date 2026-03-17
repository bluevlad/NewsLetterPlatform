"""
EduFit 데이터 포매터
API 응답 → 템플릿 변수 변환

Data Sources:
  - daily_report: PeriodReportResponse (totalTeachers, totalMentions, avgSentimentScore, positiveRatio, teacherSummaries)
  - weekly_summary: WeeklySummary (totalMentions, totalTeachers, totalRecommendations, mentionChangeRate)
  - weekly_ranking: List[WeeklyTeacherReport] (teacherName, academyName, mentionCount, avgSentimentScore, recommendationCount, topKeywords)
  - analysis_summary: AnalysisSummary (totalMentions, totalRecommendations, totalTeachers, avgSentimentScore)
  - academy_stats: List[AcademyStats] (academyName, totalMentions, totalTeachersMentioned, avgSentimentScore, topTeacherName)
  - academies: List[AcademyResponse] (id, name, name_en, code, is_active)
"""

import json
import logging
from datetime import datetime
from typing import Any, Dict
from urllib.parse import quote

from .config import REPORT_BASE_URL

logger = logging.getLogger(__name__)


class EduFitFormatter:
    """EduFit 데이터 포매터"""

    def format(self, collected_data: Dict[str, Any]) -> Dict[str, Any]:
        """수집 데이터를 템플릿 컨텍스트로 변환"""
        daily_report = collected_data.get("daily_report", {})
        weekly_summary = collected_data.get("weekly_summary", {})
        weekly_ranking = collected_data.get("weekly_ranking", [])
        analysis_summary = collected_data.get("analysis_summary", {})

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
            "total_recommendations": (
                weekly_summary.get("totalRecommendations")
                or analysis_summary.get("totalRecommendations", 0)
            ),
        }

        # 감성 점수를 퍼센트로 변환 (0~1 → 0~100)
        if stats["avg_sentiment"] and stats["avg_sentiment"] <= 1:
            stats["avg_sentiment"] = round(stats["avg_sentiment"] * 100, 1)

        # 긍정 비율 (일일 리포트에서)
        positive_ratio = daily_report.get("positiveRatio", 0) or 0
        if positive_ratio and positive_ratio <= 1:
            positive_ratio = round(positive_ratio * 100, 1)
        stats["positive_ratio"] = positive_ratio

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
                "recommendation_count": teacher.get("recommendationCount", 0),
                "top_keywords": teacher.get("topKeywords", []),
                "trend": "up" if (teacher.get("mentionChangeRate") or 0) > 0 else "stable",
            })

        # 일일 리포트 하이라이트
        highlights = self._extract_highlights(daily_report, weekly_ranking, analysis_summary)

        report_details = {
            "period_start": daily_report.get("startDate", ""),
            "period_end": daily_report.get("endDate", ""),
            "highlights": highlights,
        }

        # 학원 데이터
        academy_stats = collected_data.get("academy_stats", [])
        academies = collected_data.get("academies", [])
        academy_ranking = self._format_academy_ranking(academy_stats)
        academy_list = self._format_academy_list(academies, academy_stats)

        # 학원 요약 통계
        academy_summary = {
            "total_academies": len(academies),
            "total_academy_mentions": sum(a.get("totalMentions", 0) for a in academy_stats),
            "total_teachers_mentioned": sum(a.get("totalTeachersMentioned", 0) for a in academy_stats),
        }

        return {
            "stats": stats,
            "top_teachers": top_teachers,
            "ranking": weekly_ranking[:10],
            "report": report_details,
            "academy_ranking": academy_ranking,
            "academy_list": academy_list,
            "academy_summary": academy_summary,
            "report_date": datetime.now(),
            "generated_at": datetime.now(),
        }

    @staticmethod
    def _format_academy_ranking(academy_stats: list) -> list:
        """학원 통계 데이터를 랭킹 형태로 변환"""
        ranking = []
        for academy in academy_stats[:10]:
            avg_sentiment = academy.get("avgSentimentScore", 0) or 0
            if avg_sentiment <= 1:
                avg_sentiment = round(avg_sentiment * 100, 1)
            ranking.append({
                "name": academy.get("academyName", ""),
                "mention_count": academy.get("totalMentions", 0),
                "teacher_count": academy.get("totalTeachersMentioned", 0),
                "avg_sentiment": avg_sentiment,
                "top_teacher": academy.get("topTeacherName", ""),
            })
        return ranking

    @staticmethod
    def _format_academy_list(academies: list, academy_stats: list) -> list:
        """등록 학원 목록을 통계와 결합"""
        stats_map = {a.get("academyName", ""): a for a in academy_stats}

        result = []
        for academy in academies:
            name = academy.get("name", "")
            stat = stats_map.get(name, {})
            avg_sentiment = stat.get("avgSentimentScore", 0) or 0
            if avg_sentiment and avg_sentiment <= 1:
                avg_sentiment = round(avg_sentiment * 100, 1)
            result.append({
                "name": name,
                "code": academy.get("code", ""),
                "total_mentions": stat.get("totalMentions", 0),
                "teacher_count": stat.get("totalTeachersMentioned", 0),
                "avg_sentiment": avg_sentiment,
                "top_teacher": stat.get("topTeacherName", ""),
                "is_active": academy.get("is_active", True),
            })
        return result

    def format_weekly(self, history_data: list, collected_data: dict = None) -> dict:
        """주간 요약 포매팅"""
        collected_data = collected_data or {}

        daily_stats = self._aggregate_daily_stats(history_data)

        weekly_summary = collected_data.get("weekly_summary", {})
        weekly_ranking = collected_data.get("weekly_ranking", [])
        analysis_summary = collected_data.get("analysis_summary", {})
        academy_stats = collected_data.get("academy_stats", [])
        academies = collected_data.get("academies", [])

        total_mentions_sum = sum(d.get("total_mentions", 0) for d in daily_stats)
        days_count = len(daily_stats)

        # 감성 집계
        total_positive = sum(d.get("positive", 0) for d in daily_stats)
        total_negative = sum(d.get("negative", 0) for d in daily_stats)
        total_mentions = weekly_summary.get("totalMentions", 0) or total_mentions_sum
        total_neutral = max(0, total_mentions - total_positive - total_negative)

        stats = {
            "total_teachers": weekly_summary.get("totalTeachers", 0),
            "total_mentions": total_mentions,
            "avg_sentiment": weekly_summary.get("avgSentimentScore", 0),
            "total_recommendations": (
                weekly_summary.get("totalRecommendations", 0)
                or analysis_summary.get("totalRecommendations", 0)
            ),
            "days_count": days_count,
            "mention_change_rate": weekly_summary.get("mentionChangeRate", 0),
            "positive": total_positive,
            "negative": total_negative,
            "neutral": total_neutral,
        }

        if stats["avg_sentiment"] and stats["avg_sentiment"] <= 1:
            stats["avg_sentiment"] = round(stats["avg_sentiment"] * 100, 1)

        # TOP 강사
        top_teachers = self._format_teachers(weekly_ranking, limit=5)

        # 학원 랭킹
        academy_ranking = self._format_academy_ranking(academy_stats)

        # 기간 계산 (금요일 발송 기준: 이번 주 월~금)
        from datetime import date, timedelta
        today = date.today()
        period_start = today - timedelta(days=today.weekday())
        period_end = today

        # 차트 URL 생성
        charts = self._generate_charts(daily_stats, top_teachers)

        return {
            "stats": stats,
            "top_teachers": top_teachers,
            "academy_ranking": academy_ranking,
            "daily_stats": daily_stats,
            "period_start": period_start,
            "period_end": period_end,
            "charts": charts,
            "report_url": f"{REPORT_BASE_URL}/weekly",
            "report_date": datetime.now(),
            "generated_at": datetime.now(),
        }

    def format_monthly(self, history_data: list, collected_data: dict = None) -> dict:
        """월간 요약 포매팅"""
        collected_data = collected_data or {}

        daily_stats = self._aggregate_daily_stats(history_data)

        weekly_summary = collected_data.get("weekly_summary", {})
        weekly_ranking = collected_data.get("weekly_ranking", [])
        analysis_summary = collected_data.get("analysis_summary", {})
        academy_stats = collected_data.get("academy_stats", [])
        academies = collected_data.get("academies", [])

        total_mentions_sum = sum(d.get("total_mentions", 0) for d in daily_stats)
        days_count = len(daily_stats)

        # 감성 집계
        total_positive = sum(d.get("positive", 0) for d in daily_stats)
        total_negative = sum(d.get("negative", 0) for d in daily_stats)
        total_mentions = analysis_summary.get("totalMentions", 0) or total_mentions_sum
        total_neutral = max(0, total_mentions - total_positive - total_negative)

        stats = {
            "total_teachers": analysis_summary.get("totalTeachers", 0),
            "total_mentions": total_mentions,
            "avg_sentiment": analysis_summary.get("avgSentimentScore", 0),
            "total_recommendations": analysis_summary.get("totalRecommendations", 0),
            "days_count": days_count,
            "positive": total_positive,
            "negative": total_negative,
            "neutral": total_neutral,
        }

        if stats["avg_sentiment"] and stats["avg_sentiment"] <= 1:
            stats["avg_sentiment"] = round(stats["avg_sentiment"] * 100, 1)

        top_teachers = self._format_teachers(weekly_ranking, limit=10)

        academy_ranking = self._format_academy_ranking(academy_stats)
        academy_list = self._format_academy_list(academies, academy_stats)

        # 기간 계산 (말일 발송 기준: 이번 달 전체)
        from datetime import date
        today = date.today()
        period_start = today.replace(day=1)
        period_end = today

        # 차트 URL 생성
        charts = self._generate_charts(daily_stats, top_teachers)

        return {
            "stats": stats,
            "top_teachers": top_teachers,
            "academy_ranking": academy_ranking,
            "academy_list": academy_list,
            "daily_stats": daily_stats,
            "period_start": period_start,
            "period_end": period_end,
            "charts": charts,
            "report_url": f"{REPORT_BASE_URL}/monthly",
            "report_date": datetime.now(),
            "generated_at": datetime.now(),
        }

    @staticmethod
    def _format_teachers(ranking: list, limit: int = 5) -> list:
        """강사 랭킹 데이터 포매팅"""
        teachers = []
        for teacher in ranking[:limit]:
            sentiment = teacher.get("avgSentimentScore", 0) or 0
            if sentiment <= 1:
                sentiment = round(sentiment * 100, 1)
            teachers.append({
                "name": teacher.get("teacherName", ""),
                "academy": teacher.get("academyName", ""),
                "mention_count": teacher.get("mentionCount", 0),
                "sentiment_score": sentiment,
                "recommendation_count": teacher.get("recommendationCount", 0),
                "top_keywords": teacher.get("topKeywords", []),
            })
        return teachers

    @staticmethod
    def _aggregate_daily_stats(history_data: list) -> list:
        """이력 데이터에서 일별 통계 집계"""
        from collections import defaultdict
        by_date = defaultdict(dict)
        for record in history_data:
            d = record["collected_date"]
            dtype = record["data_type"]
            data = record["data"]
            by_date[d][dtype] = data

        daily_stats = []
        for collected_date in sorted(by_date.keys()):
            day_data = by_date[collected_date]
            daily_report = day_data.get("daily_report", {})
            daily_stats.append({
                "date": collected_date,
                "total_mentions": daily_report.get("totalMentions", 0),
                "total_teachers": daily_report.get("totalTeachers", 0),
                "avg_sentiment": daily_report.get("avgSentimentScore", 0),
                "positive": daily_report.get("totalPositive", 0),
                "negative": daily_report.get("totalNegative", 0),
            })
        return daily_stats

    @staticmethod
    def _build_quickchart_url(config: dict, width: int = 560, height: int = 200) -> str:
        """QuickChart.io 차트 이미지 URL 생성"""
        chart_json = json.dumps(config, separators=(",", ":"), ensure_ascii=False)
        return f"https://quickchart.io/chart?w={width}&h={height}&bkg=%231e293b&c={quote(chart_json)}"

    def _generate_charts(self, daily_stats: list, top_teachers: list) -> dict:
        """QuickChart URL 생성"""
        charts = {}

        # 1. 일별 언급 추이 라인 차트
        if daily_stats:
            labels = []
            data = []
            for d in daily_stats:
                dt = d.get("date", "")
                if hasattr(dt, "strftime"):
                    labels.append(dt.strftime("%m/%d"))
                else:
                    labels.append(str(dt)[-5:])
                data.append(d.get("total_mentions", 0))

            config = {
                "type": "line",
                "data": {
                    "labels": labels,
                    "datasets": [{
                        "data": data,
                        "borderColor": "#10b981",
                        "backgroundColor": "rgba(16,185,129,0.15)",
                        "fill": True,
                        "tension": 0.3,
                        "pointRadius": 5,
                        "pointBackgroundColor": "#10b981",
                        "borderWidth": 2,
                    }],
                },
                "options": {
                    "plugins": {"legend": {"display": False}},
                    "scales": {
                        "y": {
                            "ticks": {"color": "#94a3b8", "stepSize": 1},
                            "grid": {"color": "rgba(148,163,184,0.15)"},
                        },
                        "x": {
                            "ticks": {"color": "#94a3b8"},
                            "grid": {"display": False},
                        },
                    },
                },
            }
            charts["trend_url"] = self._build_quickchart_url(config)

        # 2. 강사별 언급수 가로 바 차트
        if top_teachers:
            names = [t.get("name", "")[:6] for t in top_teachers[:5]]
            counts = [t.get("mention_count", 0) for t in top_teachers[:5]]
            colors = ["#10b981", "#34d399", "#6ee7b7", "#a7f3d0", "#d1fae5"]

            config = {
                "type": "bar",
                "data": {
                    "labels": names,
                    "datasets": [{
                        "data": counts,
                        "backgroundColor": colors[: len(counts)],
                        "borderRadius": 4,
                        "barThickness": 20,
                    }],
                },
                "options": {
                    "indexAxis": "y",
                    "plugins": {"legend": {"display": False}},
                    "scales": {
                        "x": {
                            "ticks": {"color": "#94a3b8", "stepSize": 1},
                            "grid": {"color": "rgba(148,163,184,0.15)"},
                        },
                        "y": {
                            "ticks": {"color": "#f1f5f9", "font": {"size": 13}},
                            "grid": {"display": False},
                        },
                    },
                },
            }
            charts["teacher_bar_url"] = self._build_quickchart_url(config, height=160)

        return charts

    @staticmethod
    def _extract_highlights(daily_report: dict, weekly_ranking: list, analysis_summary: dict) -> list:
        """데이터에서 주요 하이라이트 자동 생성"""
        highlights = []

        teacher_summaries = daily_report.get("teacherSummaries", [])
        if teacher_summaries:
            top = teacher_summaries[0]
            name = top.get("teacherName", "")
            mentions = top.get("mentionCount", 0)
            if name and mentions > 0:
                highlights.append(f"{name} 강사 오늘 최다 언급 ({mentions}건)")

        total_recs = analysis_summary.get("totalRecommendations", 0)
        if total_recs > 0:
            highlights.append(f"누적 추천 {total_recs}건 달성")

        if weekly_ranking:
            best_sentiment = max(
                weekly_ranking[:5],
                key=lambda t: t.get("avgSentimentScore", 0) or 0,
                default=None,
            )
            if best_sentiment:
                name = best_sentiment.get("teacherName", "")
                score = best_sentiment.get("avgSentimentScore", 0) or 0
                if score <= 1:
                    score = round(score * 100, 1)
                if name:
                    highlights.append(f"{name} 강사 감성 점수 {score}%로 최상위")

            top_ranked = weekly_ranking[0]
            name = top_ranked.get("teacherName", "")
            mentions = top_ranked.get("mentionCount", 0)
            academy = top_ranked.get("academyName", "")
            if name and mentions > 0:
                highlights.append(f"{academy} {name} 강사 주간 1위 (언급 {mentions}건)")

        return highlights
