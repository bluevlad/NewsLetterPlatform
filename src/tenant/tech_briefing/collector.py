"""TechBriefing 데이터 수집기 — 3 sources (GitHub Releases / NVD CVE / RSS).

각 소스는 독립적으로 fail-safe — 한쪽 실패해도 다른 섹션은 살아남는다.
LLM 호출은 MVP 범위 외 (raw description / RSS summary 그대로 사용, truncate).
"""

import asyncio
import logging
import re
import time
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

import httpx

from .config import (
    GITHUB_REPOS,
    GITHUB_PER_PAGE,
    NVD_KEYWORDS,
    NVD_LOOKBACK_DAYS,
    RSS_FEEDS,
)
from ...common.utils import retry_async
from ...config import settings

logger = logging.getLogger(__name__)

API_TIMEOUT = 30.0

# release name 기반 분류 — semver 메이저/마이너 변경 → potentially breaking.
_BREAKING_KEYWORDS = re.compile(
    r"\b(breaking|removed|deprecat|migration|major release|major\s+version)\b",
    re.IGNORECASE,
)
_DEPRECATION_KEYWORDS = re.compile(
    r"\b(deprecat|sunset|end[- ]of[- ]life|EOL|removed)\b",
    re.IGNORECASE,
)


def _parse_iso(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except Exception:
        return None


def _strip_html(text: str, max_len: int = 280) -> str:
    if not text:
        return ""
    # 단순 HTML 제거 — feedparser 가 전달하는 description 의 태그 제거.
    cleaned = re.sub(r"<[^>]+>", " ", text)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    if len(cleaned) > max_len:
        cleaned = cleaned[: max_len - 1].rstrip() + "…"
    return cleaned


class TechBriefingCollector:
    """3 sources 비동기 수집 → 정규화 → dict 반환."""

    def __init__(self):
        self._gh_token = settings.tech_github_token or ""
        self._metrics: list[dict] = []

    def drain_metrics(self) -> list[dict]:
        m, self._metrics = self._metrics, []
        return m

    @contextmanager
    def _track(self, *, data_type: str, api_path: str):
        started = time.monotonic()
        metric: dict = {
            "data_type": data_type,
            "api_path": api_path,
            "raw_count": 0,
            "final_count": 0,
            "excluded_by_ids": 0,
            "excluded_by_companies": 0,
            "effective_days": None,
            "fallback_used": False,
            "error": None,
        }
        try:
            yield metric
        except Exception as e:
            metric["error"] = str(e)[:480]
            raise
        finally:
            metric["latency_ms"] = int((time.monotonic() - started) * 1000)
            self._metrics.append(metric)

    # ─── GitHub Releases ──────────────────────────────────────────

    async def _fetch_github_releases(
        self, client: httpx.AsyncClient
    ) -> List[Dict[str, Any]]:
        """등록 repo 리스트 → 각 N건씩 최신 release 가져오기.

        Returns: 정규화된 release dict 목록.
        """
        headers = {
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }
        if self._gh_token:
            headers["Authorization"] = f"Bearer {self._gh_token}"

        async def _one(owner: str, repo: str, ecosystem: str, tier: str):
            url = f"https://api.github.com/repos/{owner}/{repo}/releases"
            try:
                async def _request():
                    response = await client.get(
                        url,
                        params={"per_page": GITHUB_PER_PAGE},
                        headers=headers,
                    )
                    response.raise_for_status()
                    return response.json()

                items = await retry_async(_request, max_retries=2, base_delay=1.5)
            except Exception as e:
                logger.warning(
                    f"GitHub releases 실패 [{owner}/{repo}]: {e}"
                )
                return []

            normalized = []
            for it in items or []:
                if it.get("draft") or it.get("prerelease"):
                    # MVP: prerelease 는 제외 (잡음 줄이기).
                    continue
                tag = it.get("tag_name") or ""
                name = it.get("name") or tag
                body = (it.get("body") or "").strip()
                published = _parse_iso(it.get("published_at"))

                is_breaking = bool(_BREAKING_KEYWORDS.search(body)) or \
                              bool(_BREAKING_KEYWORDS.search(name))
                has_deprecation = bool(_DEPRECATION_KEYWORDS.search(body))

                normalized.append({
                    "source": "github_release",
                    "project": f"{owner}/{repo}",
                    "project_short": repo,
                    "ecosystem": ecosystem,
                    "tier": tier,
                    "title": f"{repo} {tag}".strip(),
                    "release_name": name,
                    "tag": tag,
                    "url": it.get("html_url") or "",
                    "published_at": published,
                    "summary": _strip_html(body, max_len=320),
                    "is_breaking": is_breaking,
                    "has_deprecation": has_deprecation,
                    "raw_body": body[:1500],
                    "dedup_key": f"gh-release:{owner}/{repo}:{tag}",
                })
            return normalized

        # repo 별 병렬 호출 (semaphore 로 동시성 제한 → unauth rate 60/h 보호).
        semaphore = asyncio.Semaphore(4 if self._gh_token else 2)

        async def _bounded(args):
            async with semaphore:
                return await _one(*args)

        with self._track(
            data_type="github_releases",
            api_path="https://api.github.com/repos/{owner}/{repo}/releases",
        ) as m:
            try:
                results = await asyncio.gather(
                    *[_bounded(args) for args in GITHUB_REPOS],
                    return_exceptions=False,
                )
                flat: List[Dict[str, Any]] = []
                for r in results:
                    flat.extend(r)
                # raw_count: repo 호출 수, final_count: 정규화된 release 수
                m["raw_count"] = len(GITHUB_REPOS)
                m["final_count"] = len(flat)
                # 토큰 없는 경우 동시성 제약(2) 으로 풀 확장 fallback 신호
                if not self._gh_token:
                    m["fallback_used"] = True
                logger.info(f"GitHub releases 수집: {len(flat)}건")
                return flat
            except Exception as e:
                m["error"] = str(e)[:480]
                raise

    # ─── NVD CVE feed ─────────────────────────────────────────────

    async def _fetch_cves(
        self, client: httpx.AsyncClient
    ) -> List[Dict[str, Any]]:
        """NVD 2.0 keywordSearch — 키워드 풀 순차 호출.

        Rate limit (no key): 5 req / 30s, 50 req / 30s with key.
        """
        url = "https://services.nvd.nist.gov/rest/json/cves/2.0"
        end_dt = datetime.now(timezone.utc)
        start_dt = end_dt - timedelta(days=NVD_LOOKBACK_DAYS)

        # NVD 의 ISO 8601 요구 형식 (밀리초 + 타임존).
        def _fmt(dt: datetime) -> str:
            return dt.strftime("%Y-%m-%dT%H:%M:%S.000")

        seen_ids: set[str] = set()
        all_cves: List[Dict[str, Any]] = []
        cve_errors = 0

        with self._track(
            data_type="cves",
            api_path="https://services.nvd.nist.gov/rest/json/cves/2.0",
        ) as m:
            m["effective_days"] = NVD_LOOKBACK_DAYS
            for kw in NVD_KEYWORDS:
                params = {
                    "keywordSearch": kw,
                    "pubStartDate": _fmt(start_dt),
                    "pubEndDate": _fmt(end_dt),
                    "resultsPerPage": 20,
                }
                try:
                    async def _request():
                        response = await client.get(url, params=params, timeout=API_TIMEOUT)
                        if response.status_code == 403:
                            raise RuntimeError("NVD 403 — rate limit 초과 의심")
                        response.raise_for_status()
                        return response.json()

                    payload = await retry_async(_request, max_retries=2, base_delay=2.0)
                except Exception as e:
                    cve_errors += 1
                    logger.warning(f"NVD CVE 실패 [{kw}]: {e}")
                    continue

                for vuln in payload.get("vulnerabilities", []) or []:
                    cve = vuln.get("cve") or {}
                    cve_id = cve.get("id") or ""
                    if not cve_id or cve_id in seen_ids:
                        continue
                    seen_ids.add(cve_id)

                    descs = cve.get("descriptions") or []
                    en_desc = next(
                        (d.get("value") for d in descs if d.get("lang") == "en"),
                        "",
                    )

                    # CVSS v3.1 우선, 없으면 v3.0, 없으면 v2.
                    metrics = cve.get("metrics") or {}
                    cvss = None
                    severity = None
                    for key in ("cvssMetricV31", "cvssMetricV30", "cvssMetricV2"):
                        arr = metrics.get(key) or []
                        if arr:
                            item0 = arr[0]
                            cvss_data = item0.get("cvssData") or {}
                            cvss = cvss_data.get("baseScore")
                            severity = (
                                cvss_data.get("baseSeverity")
                                or item0.get("baseSeverity")
                                or ""
                            ).lower() or None
                            break

                    published = _parse_iso(cve.get("published"))

                    all_cves.append({
                        "source": "nvd_cve",
                        "project": kw,
                        "project_short": kw,
                        "ecosystem": _ecosystem_for_keyword(kw),
                        "tier": "S",  # CVE 는 자동으로 S 가중 (보안 신호 가장 큼)
                        "title": f"{cve_id} ({kw})",
                        "cve_id": cve_id,
                        "url": f"https://nvd.nist.gov/vuln/detail/{cve_id}",
                        "published_at": published,
                        "summary": _strip_html(en_desc, max_len=320),
                        "cvss": cvss,
                        "severity": severity,
                        "matched_keyword": kw,
                        "dedup_key": f"cve:{cve_id}",
                    })
                # rate limit 보호 — keyword 간 짧은 간격.
                await asyncio.sleep(0.7 if self._gh_token else 1.2)

            m["raw_count"] = len(NVD_KEYWORDS)
            m["final_count"] = len(all_cves)
            if cve_errors:
                # 일부 키워드 실패는 부분 fallback 으로 표시 (전부 실패 아니면 error 비움)
                if cve_errors >= len(NVD_KEYWORDS):
                    m["error"] = f"all keywords failed (n={cve_errors})"
                else:
                    m["fallback_used"] = True
        logger.info(f"NVD CVE 수집: {len(all_cves)}건 (unique)")
        return all_cves

    # ─── RSS 공식 블로그 ───────────────────────────────────────────

    async def _fetch_rss(
        self, client: httpx.AsyncClient
    ) -> List[Dict[str, Any]]:
        """공식 블로그 RSS 피드 병렬 fetch + feedparser 파싱."""
        # feedparser 는 동기 — fetch 만 비동기로 하고 파싱은 thread executor 로.
        try:
            import feedparser
        except ImportError:
            logger.warning("feedparser 미설치 — RSS 섹션 비활성")
            return []

        async def _one(label: str, url: str, ecosystem: str, tier: str):
            try:
                async def _request():
                    response = await client.get(
                        url,
                        timeout=API_TIMEOUT,
                        follow_redirects=True,
                        headers={"User-Agent": "NewsLetterPlatform/1.0"},
                    )
                    response.raise_for_status()
                    return response.text

                body = await retry_async(_request, max_retries=2, base_delay=1.5)
            except Exception as e:
                logger.warning(f"RSS fetch 실패 [{label}]: {e}")
                return []

            try:
                feed = await asyncio.to_thread(feedparser.parse, body)
            except Exception as e:
                logger.warning(f"RSS parse 실패 [{label}]: {e}")
                return []

            entries: List[Dict[str, Any]] = []
            for entry in (feed.entries or [])[:8]:
                published_struct = (
                    entry.get("published_parsed")
                    or entry.get("updated_parsed")
                )
                if published_struct:
                    published = datetime(
                        *published_struct[:6], tzinfo=timezone.utc
                    )
                else:
                    published = None

                summary_raw = (
                    entry.get("summary")
                    or entry.get("description")
                    or ""
                )
                title = entry.get("title") or "(제목 없음)"
                link = entry.get("link") or ""

                entries.append({
                    "source": "rss_blog",
                    "project": label,
                    "project_short": label,
                    "ecosystem": ecosystem,
                    "tier": tier,
                    "title": title,
                    "url": link,
                    "published_at": published,
                    "summary": _strip_html(summary_raw, max_len=320),
                    "blog_label": label,
                    "dedup_key": f"rss:{link or title}",
                })
            return entries

        with self._track(
            data_type="rss",
            api_path="(rss feeds)",
        ) as m:
            try:
                results = await asyncio.gather(
                    *[_one(label, url, eco, tier)
                      for label, url, eco, tier in RSS_FEEDS],
                    return_exceptions=False,
                )
                flat: List[Dict[str, Any]] = []
                for r in results:
                    flat.extend(r)
                # _one 은 실패도 [] 로 폴백하므로 fetch 실패와 본문 없음을 구분할 수 없다.
                # raw_count = feed 수, final_count = 모은 엔트리 수만 적재 (충분히 운영용).
                m["raw_count"] = len(RSS_FEEDS)
                m["final_count"] = len(flat)
                logger.info(f"RSS 수집: {len(flat)}건")
                return flat
            except Exception as e:
                m["error"] = str(e)[:480]
                raise

    # ─── 통합 수집 ─────────────────────────────────────────────────

    async def collect_daily(
        self,
        exclude_ids: Optional[List[int]] = None,  # base interface 호환 (미사용)
        exclude_companies: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        """3 sources 병렬 수집 → 단일 dict.

        Returns:
            {
                "tech_daily": {
                    "report_date": ISO,
                    "github_releases": [...],
                    "cves": [...],
                    "rss_articles": [...],
                    "stats": {"release_count": N, "cve_count": N, "rss_count": N},
                }
            }
            전부 실패시 빈 dict.
        """
        async with httpx.AsyncClient(timeout=API_TIMEOUT) as client:
            github_task = self._fetch_github_releases(client)
            cve_task = self._fetch_cves(client)
            rss_task = self._fetch_rss(client)

            results = await asyncio.gather(
                github_task, cve_task, rss_task,
                return_exceptions=True,
            )

        github_releases = results[0] if not isinstance(results[0], Exception) else []
        cves            = results[1] if not isinstance(results[1], Exception) else []
        rss_articles    = results[2] if not isinstance(results[2], Exception) else []

        for idx, label in enumerate(("GitHub", "NVD", "RSS")):
            if isinstance(results[idx], Exception):
                logger.error(f"{label} 수집 예외 — 빈 폴백: {results[idx]}")

        if not github_releases and not cves and not rss_articles:
            logger.warning("TechBriefing: 3 sources 모두 비어 있음")
            return {}

        now_iso = datetime.now(timezone.utc).isoformat()
        return {
            "tech_daily": {
                "report_date": now_iso,
                "github_releases": github_releases,
                "cves": cves,
                "rss_articles": rss_articles,
                "stats": {
                    "release_count": len(github_releases),
                    "cve_count": len(cves),
                    "rss_count": len(rss_articles),
                },
            }
        }


def _ecosystem_for_keyword(kw: str) -> str:
    """NVD 키워드 → ecosystem 라벨 매핑."""
    kw_l = kw.lower()
    if any(x in kw_l for x in ("spring", "tomcat", "log4j", "hibernate")):
        return "java-be"
    if "kotlin" in kw_l:
        return "language"
    if "typescript" in kw_l:
        return "language"
    if "next.js" in kw_l:
        return "react-meta"
    if "react" in kw_l:
        return "react-core"
    if "node" in kw_l:
        return "runtime"
    return "tooling"
