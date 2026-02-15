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
            "top_academy_name": summary.get("topAcademy", {}).get("name", "-"),
            "top_academy_count": summary.get("topAcademy", {}).get("count", 0),
            "top_source_name": summary.get("topSource", {}).get("name", "-"),
            "top_source_type": summary.get("topSource", {}).get("sourceType", ""),
            "top_source_count": summary.get("topSource", {}).get("count", 0),
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
                "comment_count": post.get("commentCount", 0),
                "engagement": post.get("engagement", 0),
                "post_url": post.get("postUrl", ""),
            })

        # 학원 랭킹 (상위 10개)
        formatted_ranking = []
        for academy in academy_ranking[:10]:
            formatted_ranking.append({
                "name": academy.get("name", ""),
                "post_count": academy.get("weekCount", 0),
                "avg_sentiment": academy.get("avgSentiment", 0),
                "trend": academy.get("trend", "stable"),
                "change": academy.get("change", 0),
                "today_count": academy.get("todayCount", 0),
                "yesterday_count": academy.get("yesterdayCount", 0),
            })

        highlights = self._build_highlights(stats, formatted_posts)

        return {
            "stats": stats,
            "highlights": highlights,
            "trending_posts": formatted_posts,
            "academy_ranking": formatted_ranking,
            "report_date": datetime.now(),
            "generated_at": datetime.now(),
        }

    @staticmethod
    def _build_highlights(stats: dict, trending_posts: list) -> list:
        """데이터에서 주요 하이라이트 자동 생성"""
        highlights = []

        # topAcademy 하이라이트
        top_academy = stats.get("top_academy_name", "-")
        top_academy_count = stats.get("top_academy_count", 0)
        if top_academy != "-" and top_academy_count > 0:
            highlights.append(f"{top_academy} 오늘 최다 언급 ({top_academy_count}건)")

        # topSource 하이라이트
        top_source = stats.get("top_source_name", "-")
        if top_source != "-":
            highlights.append(f"{top_source}에서 가장 활발한 논의")

        # engagement 높은 게시글 하이라이트
        if trending_posts:
            top_engagement = max(
                trending_posts,
                key=lambda p: p.get("engagement", 0),
            )
            engagement = top_engagement.get("engagement", 0)
            title = top_engagement.get("title", "")
            if engagement > 0 and title:
                short_title = title[:30] + "..." if len(title) > 30 else title
                highlights.append(f"'{short_title}' 높은 관심 (참여도 {engagement})")

        return highlights
