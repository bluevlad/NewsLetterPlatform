# NewsLetterPlatform 구현·설계·공개 저장소 보안 검증 보고서

- 검증일: 2026-08-15 (KST)
- 구현 저장소: `bluevlad/NewsLetterPlatform`, `main` @ `4614402`
- 설계 정본: 로컬 `Claude-Opus-bluevlad/main`의 `services/newsletterplatform`, `standards/newsletterplatform`, `services/allergyinsight/plans`
- 검증 방식: 코드/설정/Git 전체 도달 가능 이력 정적 검토, 설계-구현 추적, 전체 테스트와 컴파일 실행

## 1. 결론

현재 구현은 주요 뉴스레터 기능과 최근 P0/P1/P1b/P2 회귀 테스트가 정상이며, `main`과 `prod`도 같은 커밋이다. 그러나 공개 운영 가능한 상태로 승인하기에는 차단 이슈가 남아 있다.

가장 중요한 문제는 다음과 같다.

1. 현재 파일에서 제거된 과거 관리자 기본 비밀번호가 공개 Git의 **도달 가능한 이력**에 남아 있다.
2. 이메일 링크의 GET 요청이 즉시 구독 해지를 수행하므로 메일 보안 스캐너/프리페처가 사용자의 의사 없이 해지할 수 있다.
3. 관리자 비밀번호 로그인에 rate limit이 없고 세션 쿠키에 `Secure`가 설정되지 않는다.
4. “PostgreSQL 준비” 검증은 실제 PostgreSQL 호환성을 입증하지 않는다. 마이그레이션은 SQLite `PRAGMA`에 고정되어 있고 테스트도 SQLite만 사용한다.
5. 설계 정본의 상태표와 현재 구현이 동기화되지 않았다. 구현된 N2/Engagement를 문서가 미착수로 표시하고, 구현 토큰은 설계의 만료 요구사항을 충족하지 않는다.

따라서 판정은 **조건부 부적합(보안 P0/P1 조치 전 공개 운영 승인 보류)** 이다.

## 2. 검증 결과 요약

| 영역 | 결과 | 근거 |
|---|---|---|
| Git 상태 | 통과 | 작업 시작 시 clean, `main == origin/main == origin/prod == prod` |
| Python 컴파일 | 통과 | `python -m compileall -q src tests` |
| 전체 테스트 | 통과 | 142 passed, 154 warnings, 0 failed |
| 최근 구조 개선 | 대체로 통과 | SendPlan, repository 분해, tenant 자동 등록, 헬스/경보/engagement 회귀 테스트 존재 |
| 공개 저장소 현재 HEAD 비밀 | 통과 | `.env`, `.env.production`, DB, 로그, 키 파일은 미추적 및 ignore 적용 |
| 공개 Git 과거 이력 | 실패 | 하드코딩 관리자 기본 비밀번호 커밋 `576adf8`이 모든 주요 브랜치에서 도달 가능 |
| 웹 보안 경계 | 실패 | GET 구독 해지, 로그인 무제한 시도, `Secure` 없는 관리자 쿠키 |
| PostgreSQL 준비 | 미입증 | 실제 PG 테스트 없음, `PRAGMA` 기반 수제 마이그레이션 |
| 설계-구현 정합성 | 부분 통과 | 주요 구조는 반영됐으나 상태표/계약/운영 설정 드리프트 존재 |

## 3. 상세 발견사항

### SEC-01 — 공개 Git 이력에 과거 관리자 기본 비밀번호 잔존

- 심각도: **P0 / Critical**
- 근거:
  - `git cat-file -t 576adf8` 결과가 `commit`이다.
  - `git branch -a --contains 576adf8` 결과에 로컬/원격 `main`, `prod`, Dependabot 브랜치가 포함된다.
  - `git log -S<과거값>`으로 도입 `576adf8`과 제거 `d6779f2`가 모두 검색된다.
  - 비공개 설계 문서 `SUBSCRIPTION_ABUSE_HARDENING.md`도 해당 노출과 운영 비밀번호 회전을 P0로 기록한다.
- 영향: HEAD에서 값을 삭제해도 공개 커밋 URL로 복구 가능하다. 같은 값 또는 파생값을 현재도 사용한다면 관리자 탈취로 이어질 수 있다.
- 권고:
  1. ADMIN_PASSWORD 및 재사용/파생 가능 자격증명을 즉시 회전한다.
  2. GitHub의 민감정보 제거 절차에 따라 전체 브랜치/태그 이력을 재작성하고 force update한다.
  3. 열린 PR/포크/캐시도 별도 폐기 또는 GitHub Support 제거 요청 대상으로 관리한다.
  4. GitHub secret scanning/push protection과 로컬 pre-commit secret scan을 활성화한다.
- 비고: 로컬에는 `git filter-repo` 실행 흔적과 unreachable object가 있다. 로컬 GC만으로 GitHub의 공개 이력이 정리되지는 않는다.

### SEC-02 — GET 링크 접근만으로 구독 상태 변경

- 심각도: **P1 / High**
- 위치: `src/web/app.py:715-737`, `src/common/subscription/manager.py:284-299`
- 현상: `GET /{tenant_id}/unsubscribe/token/{token}`이 즉시 `is_active=False`를 커밋한다.
- 영향: 이메일 보안 제품, 링크 미리보기, 크롤러가 링크를 자동 방문하면 수신자 의사 없이 해지될 수 있다. 같은 프로젝트의 persona GET 라우트는 스캐너 안전성을 이유로 부작용을 금지하고 있어 정책도 일관되지 않다.
- 권고: GET은 확인 화면만 렌더하고, 실제 해지는 CSRF 보호된 POST에서 수행한다. 원클릭 해지 표준을 지원해야 한다면 `List-Unsubscribe-Post: List-Unsubscribe=One-Click` 전용 POST를 분리하고 일반 링크 GET과 구분한다.

### SEC-03 — 관리자 인증 brute-force 방어 및 쿠키 전송 보호 부족

- 심각도: **P1 / High**
- 위치: `src/web/admin/auth.py:93-101`, `src/web/admin/auth.py:131-163`
- 현상:
  - `/admin/login` 비밀번호 로그인에 IP/계정 rate limit, 지연, 잠금이 없다.
  - 관리자 세션 쿠키가 `HttpOnly`, `SameSite=Lax`는 사용하지만 `Secure=True`가 아니다.
- 영향: 공개 엔드포인트에 대한 무제한 비밀번호 대입과 HTTP 구간 쿠키 노출 가능성이 생긴다.
- 권고: 비밀번호 로그인을 운영에서 제거하거나 강한 rate limit/잠금/감사를 추가한다. 운영 HTTPS에서는 `Secure=True`를 강제하고 필요 시 쿠키 path/domain도 최소화한다.

### SEC-04 — 프록시 외 직접 접근 시 X-Forwarded-For 위조로 rate limit 우회 가능

- 심각도: **P1 / High** (웹 컨테이너가 게이트웨이 외부에서 직접 접근 가능할 때)
- 위치: `src/common/security/abuse_guard.py:85-103`, `docker-compose.prod.yml:49-50`
- 현상: 요청 peer가 신뢰 프록시인지 확인하지 않고 모든 `X-Forwarded-For`를 신뢰한다. 동시에 운영 compose는 `4050:4050`을 모든 인터페이스에 publish한다.
- 영향: 포트가 직접 도달 가능하면 공격자가 XFF 값을 바꿔 구독/페르소나/피드백 rate limit을 우회할 수 있다.
- 권고: 포트를 `127.0.0.1:4050:4050` 등 게이트웨이 전용으로 바인딩하고, 신뢰 프록시 CIDR/peer 검증 후에만 forwarded header를 사용한다. 게이트웨이에서 수신 XFF를 덮어쓰는 설정도 확인한다.

### SEC-05 — Engagement 서명 토큰에 만료/발송 식별자가 없음

- 심각도: **P2 / Medium**
- 위치: `src/common/engagement.py:35-46`, `src/web/routes_engagement.py:66-113`
- 설계 차이: Freshness 플랜은 `exp` 필수(30일), `iat` 권장으로 명시하지만 구현은 `kind:tenant_id:subscriber_id:extra`의 영구 HMAC만 사용한다.
- 영향: 링크 유출 후 무기한 replay가 가능하며, 동일 구독자의 어느 발송에서 발생한 이벤트인지 구분할 수 없다. 오픈/피드백 통계가 장기간 오염될 수 있다.
- 권고: 서명 payload에 `send_history_id` 또는 campaign id, `iat`, `exp`를 포함한다. 이벤트 멱등 키도 두어 스캐너/반복 클릭을 별도로 집계한다.

### SEC-06 — 현재 HEAD의 비밀 제외는 양호하나 공개 메타데이터 최소화가 불완전

- 심각도: **P3 / Low**
- 양호:
  - `.env`, `.env.production`, `data/*.db*`, `logs/*.log`, credential/key 파일은 `.gitignore` 대상이다.
  - `.dockerignore`가 운영 데이터와 환경 파일을 이미지 컨텍스트에서 제외한다.
  - Actions는 비밀값을 `${{ secrets.* }}`로 참조한다.
- 잔여:
  - `README.md`의 `License: Private`는 public repository 정책과 모순된다.
  - compose/CLAUDE/service profile에 운영 도메인, 포트, 네트워크/컨테이너명, 다른 서비스의 상세 스택과 부채가 공개된다.
  - 배포가 generic `self-hosted` runner를 사용해 다른 저장소와 runner를 공유할 경우 격리 위험이 커진다.
- 권고: 공개에 필요한 정보와 내부 운영 토폴로지를 분리하고, self-hosted runner에 저장소 전용 label/group을 사용한다. README에는 실제 공개 라이선스 또는 “license not granted” 정책을 명확히 한다.

### ARC-01 — PostgreSQL 준비 완료 주장은 현재 검증 범위를 초과

- 심각도: **P1 / High**
- 위치: `src/common/database/migrations.py:16-221`, `tests/test_common/test_p2_evolution.py:202-223`
- 현상:
  - 모든 수제 마이그레이션이 SQLite `PRAGMA table_info`를 실행한다.
  - 예외를 warning으로 삼키므로 기존 PostgreSQL 스키마 업그레이드 실패가 기동 성공처럼 보일 수 있다.
  - “PG 준비” 테스트는 SQLite DB에서 `article_key` round-trip만 검사한다.
  - `SentArticle.article_id`는 여전히 non-null Integer이고 유니크 제약도 `article_id` 기준이라 문자열 ID 테넌트를 완전히 지원하지 않는다.
- 영향: 신규 빈 PostgreSQL은 `create_all`로 동작할 수 있어도 기존 스키마 승격, 제약/인덱스, 실제 쿼리 방언 호환성은 입증되지 않는다.
- 권고: 문서 상태를 “PG 사전 정리”로 낮추고, Alembic 또는 dialect별 migration을 도입한다. CI에 PostgreSQL service container를 추가해 마이그레이션 전/후, repository, scheduler transaction을 검증한다.

### ARC-02 — 설계 문서 상태와 구현 상태가 불일치

- 심각도: **P2 / Medium**
- 근거:
  - `PERSONA_ADAPTIVE_NEWSLETTER_N1_N2_DESIGN.md` 머리말은 N2 미착수로 표시하지만 현재 라우터/클라이언트/포매터/테스트가 구현되어 있다.
  - `NEWSLETTER_FRESHNESS_IMPROVEMENT_PLAN.md`는 L1/L2를 Todo로 표시하지만 P2에서 통합 engagement 이벤트/라우터/릴레이가 구현됐다.
  - `ARCHITECTURE.md`의 TODO 일부는 이미 구현됐거나 현재 구조와 맞지 않는다.
  - `TENANT_ONBOARDING.md`는 수동 registry/config 수정 절차를 설명하지만 현재는 자동 discovery와 테넌트 소유 설정 구조다.
  - `VERIFICATION_CRITERIA.md`는 0바이트다.
- 영향: 이후 구현자가 이미 구현된 기능을 재설계하거나, 실제 보안 계약(토큰 만료 등)을 누락할 가능성이 높다.
- 권고: 설계 문서에 구현 커밋, 상태, 대체된 endpoint/schema, 잔여 DoD를 갱신하고 0바이트 검증 기준을 실질적인 추적표로 교체한다.

### ARC-03 — 운영 배포 설정이 신규 설정 계약을 명시적으로 보장하지 않음

- 심각도: **P2 / Medium**
- 위치: `.github/workflows/deploy-prod.yml:22-77`, `docker-compose.prod.yml:9-16,47-56`
- 현상: Actions가 생성하는 `.env.production`에는 `SESSION_SECRET`, Google allowlist, Turnstile, tenant newsletter keys, Slack alert, LLMOps 등의 최근 설정이 없다. 대신 compose가 별도 `.env`도 함께 mount하고 애플리케이션이 이를 다시 읽는 혼합 계약이다.
- 영향: 신규 서버/재배포 시 기능이 조용히 비활성화되거나 랜덤 세션 키로 동작할 수 있다. 어떤 파일이 운영 정본인지 불명확하다.
- 권고: 운영 설정 정본을 하나로 통합하고, 기동 시 운영 필수값을 fail-fast 검증한다. 생성 파일은 heredoc 문자열 치환보다 runner 권한/로그/백업 정책까지 포함해 관리한다.

### OPS-01 — 의존성 감사가 실패를 차단하지 않고 로컬 재현도 고정되지 않음

- 심각도: **P2 / Medium**
- 위치: `.github/workflows/dependency-audit.yml:28-36`, `requirements.txt`
- 현상: `pip-audit` 발견 결과가 report-only이며 모든 직접 의존성이 하한 위주로 열려 있다. 로컬 venv에는 `pip-audit`가 없어 이번 검증에서 취약점 DB 대조를 재현하지 못했다.
- 영향: 알려진 취약점이 있어도 배포가 계속되고, 설치 시점에 따라 서로 다른 transitive dependency가 배포된다.
- 권고: lock/constraints와 hash를 도입하고, 고위험 취약점은 CI를 차단한다. 예외는 만료일과 근거가 있는 allowlist로 관리한다.

### QUAL-01 — 경고 154건과 broad exception 처리 누적

- 심각도: **P3 / Low**
- 근거: 전체 테스트에서 Pydantic v2 구식 `Field(env=...)`/class Config, `datetime.utcnow()`, Starlette TemplateResponse 구식 호출 등의 경고 154건이 발생했다. 코드에는 다수의 broad `except Exception`이 있다.
- 영향: Pydantic v3/Python 3.16 등 차기 업그레이드에서 실패 가능성이 있으며, 일부 운영 실패가 warning/일반 응답으로 축소될 수 있다.
- 권고: 경고를 유형별로 제거하고 CI에서 신규 경고 증가를 차단한다. 반드시 격리해야 하는 best-effort 경로만 broad catch를 허용하고 구조화된 오류 지표를 남긴다.

## 4. 설계 요구사항 추적 결과

| 설계 축 | 구현 판정 | 비고 |
|---|---|---|
| 멀티테넌트 BaseTenant/자동 등록 | 충족 | registry discovery 및 테넌트 패키지 소유 설정 |
| 발송 유형/모드 분리 | 충족 | `newsletter_type`과 `send_mode`, SendPlan으로 중앙화 |
| 주말/공휴일 관리자 테스트 격리 | 충족 | admin-only, dedup/archive/article pool 비오염 |
| 발송 슬롯 | 충족 | 슬롯 모델/스케줄/UI/테스트 존재 |
| 신선도 dedup 및 stale alert | 대체로 충족 | sent_articles, collector exclude, stale/duplicate admin alert |
| Persona N1 | 충족 | subscriber 메타, signup_meta, migration/repository/UI |
| Persona N2 | 구현됨/문서 미갱신 | select/transform/callback/poll/소유권 검사와 테스트 존재 |
| Engagement L1/L2/N4 통합 | 부분 충족 | HMAC, open/click/feedback, relay 구현. 만료/캠페인 식별/운영 수락 데이터 부족 |
| Digest Efficacy V1/V2 | 미완료 | 문서상 Todo, 운영 데이터 기반 검증 없음 |
| PostgreSQL 이행 | 미입증 | 라이브러리와 일부 중립 키만 준비, 실제 migration/CI 없음 |
| 구독 어뷰즈 하드닝 | 부분 충족 | captcha/rate limit/honeypot/email limit/bounce 존재. XFF 및 로그인 경계 보완 필요 |

## 5. 검증 명령과 관찰

```text
venv/bin/python -m pytest -q
=> 142 passed, 154 warnings in 0.90s

venv/bin/python -m compileall -q src tests
=> PASS

git status --short --branch
=> ## main...origin/main

git log -1 --oneline
=> 4614402 merge: feature/p2-evolution ...
```

테스트의 한계:

- 외부 AllergyInsight/SkillRadar/SMTP/Google/Turnstile/Slack과 실제 통합 호출은 수행하지 않았다.
- 운영 DB의 PII 내용은 검증 범위에서 읽지 않았다.
- PostgreSQL 인스턴스가 제공되지 않아 실제 PG migration 테스트는 수행하지 않았다.
- `pip-audit`, gitleaks/trufflehog가 로컬 환경에 없어 알려진 CVE/고급 entropy secret scan은 완료하지 못했다. 대신 추적 파일, ignore, Git 도달 가능 이력과 알려진 비밀 도입 커밋을 검사했다.

## 6. 권장 조치 순서

### 배포/공개 운영 전

1. SEC-01 자격증명 회전 및 GitHub 전체 이력 제거를 완료한다.
2. SEC-02를 확인 GET + 실행 POST로 분리한다.
3. SEC-03 관리자 로그인 rate limit과 Secure cookie를 적용한다.
4. SEC-04의 4050 직접 접근을 차단하고 trusted proxy 정책을 강제한다.

### 1주 이내

5. Engagement 토큰에 만료/발송 식별자/멱등성을 추가한다.
6. 운영 필수 설정 fail-fast와 단일 설정 정본을 마련한다.
7. PostgreSQL 실제 CI와 migration 전략을 추가하거나 “PG 준비” 표기를 낮춘다.
8. Claude-Opus-bluevlad의 N2/L 트랙/온보딩/검증기준 문서를 현재 구현과 동기화한다.

### 지속 개선

9. 의존성 lock/hash 및 고위험 CVE 차단 게이트를 도입한다.
10. Pydantic/datetime/Starlette 경고를 제거하고 신규 경고 증가를 차단한다.
11. 공개 README의 라이선스와 공개 범위, 내부 토폴로지 노출 기준을 정리한다.

## 7. 최종 승인 기준

아래가 모두 충족되면 재검증 후 공개 운영 승인 가능하다.

- 과거 비밀번호가 GitHub 도달 가능 브랜치/태그/PR에서 검색되지 않고 운영 비밀번호가 회전됨
- GET 구독 해지 회귀 테스트가 “상태 변경 없음”을 보장함
- 관리자 로그인 rate limit 및 `Secure` cookie 테스트 통과
- 직접 포트 접근 차단 또는 신뢰 프록시 검증 테스트 통과
- 운영 필수 설정 누락 시 기동 실패 테스트 통과
- PostgreSQL을 지원한다고 표기할 경우 실제 PG migration/repository 통합 테스트 통과
- 설계 상태표와 구현 커밋/DoD가 동기화됨
