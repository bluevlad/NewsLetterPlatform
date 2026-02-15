"""
AcademyInsight 데이터 포매터
API 응답 → 템플릿 변수 변환
"""

import logging
from datetime import datetime
from typing import Any, Dict

logger = logging.getLogger(__name__)


class AcademyInsightFormatter:
    """AcademyInsight 데이터 포매터"""

    def format(self, collected_data: Dict[str, Any]) -> Dict[str, Any]:
        """수집 데이터를 템플릿 컨텍스트로 변환"""
        summary = collected_data.get("summary", {})
        trending_posts = collected_data.get("trending_posts", [])
        academy_ranking = collected_data.get("academy_ranking", [])

        # 통계 정보
        today = summary.get("todayPosts", 0)
        yesterday = summary.get("yesterdayPosts", 0)
        stats = {
            "today_posts": today,
            "post_change": today - yesterday,
            "active_academies": summary.get("activeAcademies", 0),
            "total_posts": summary.get("totalPosts", 0),
        }

        # 트렌딩 게시글 (상위 10개)
        formatted_posts = []
        for post in trending_posts[:10]:
            formatted_posts.append({
                "title": post.get("title", ""),
                "academy_name": post.get("academyName", ""),
                "source": post.get("sourceName", ""),
                "view_count": post.get("viewCount", 0),
                "created_at": post.get("postedAt", ""),
            })

        # 학원 랭킹 (상위 10개)
        formatted_ranking = []
        for academy in academy_ranking[:10]:
            formatted_ranking.append({
                "name": academy.get("name", ""),
                "post_count": academy.get("weekCount", 0),
                "avg_sentiment": academy.get("avgSentiment", 0),
                "trend": academy.get("trend", "stable"),
            })

        return {
            "stats": stats,
            "trending_posts": formatted_posts,
            "academy_ranking": formatted_ranking,
            "report_date": datetime.now(),
            "generated_at": datetime.now(),
        }
