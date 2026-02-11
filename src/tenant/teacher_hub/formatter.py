"""
TeacherHub 데이터 포매터
API 응답 → 템플릿 변수 변환
"""

import logging
from datetime import datetime
from typing import Any, Dict

logger = logging.getLogger(__name__)


class TeacherHubFormatter:
    """TeacherHub 데이터 포매터"""

    def format(self, collected_data: Dict[str, Any]) -> Dict[str, Any]:
        """수집 데이터를 템플릿 컨텍스트로 변환"""
        summary = collected_data.get("summary", {})
        daily_report = collected_data.get("daily_report", {})
        ranking = collected_data.get("ranking", [])

        # 통계 정보
        stats = {
            "total_teachers": summary.get("totalTeachers", 0),
            "total_mentions": summary.get("totalMentions", 0),
            "avg_sentiment": summary.get("avgSentiment", 0),
            "new_mentions_today": daily_report.get("newMentionsToday", 0),
        }

        # TOP 강사 (랭킹 상위 5명)
        top_teachers = []
        for teacher in ranking[:5]:
            top_teachers.append({
                "name": teacher.get("teacherName", ""),
                "academy": teacher.get("academyName", ""),
                "mention_count": teacher.get("mentionCount", 0),
                "sentiment_score": teacher.get("sentimentScore", 0),
                "trend": teacher.get("trend", "stable"),
            })

        # 일일 리포트 상세
        report_details = {
            "period_start": daily_report.get("periodStart", ""),
            "period_end": daily_report.get("periodEnd", ""),
            "highlights": daily_report.get("highlights", []),
        }

        return {
            "stats": stats,
            "top_teachers": top_teachers,
            "ranking": ranking[:10],
            "report": report_details,
            "report_date": datetime.now(),
            "generated_at": datetime.now(),
        }
