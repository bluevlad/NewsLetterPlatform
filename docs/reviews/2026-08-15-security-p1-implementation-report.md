# 보안 P1 검토의견 반영보고서

- 작성일: 2026-08-15 (KST)
- 대상 검증 보고서: `docs/reviews/2026-08-15-project-validation-report.md`
- 반영 커밋: `2a7fbd4` (fix/security-p1) → main/prod merge `76c66eb`
- 배포: GitHub Actions `deploy-prod.yml` run 31875060453 — **success**, 운영 컨테이너 2종 healthy 확인

## 1. 요약

검증 보고서의 발견사항에 대한 검토 의견을 수립하고, 그중 **코드로 즉시 수용 가능한 항목(SEC-02, SEC-03, SEC-04)** 을 `fix/security-p1` 브랜치로 구현하여 운영 배포까지 완료했다. 전체 테스트 152건 통과(기존 142 + 신규 회귀 10), 운영 게이트웨이/loopback 바인딩/해지 확인 페이지 동작을 실환경에서 확인했다.

운영자 절차가 필요한 SEC-01(자격증명 회전), 설계 결정이 필요한 SEC-05·ARC 계열은 미반영 보류 항목으로 정리했다(§5).

## 2. 검증 보고서에 대한 검토 의견 (사전 검토 결과)

코드·Git 교차 검증 결과 보고서의 사실관계(SEC-01~05, ARC-01)는 **모두 정확**했고 판정(조건부 부적합)도 타당했다. 다만 아래 보완 의견을 제시했다.

| # | 의견 | 처리 |
|---|---|---|
| 1 | SEC-04 서술 부정확 — 코드는 이미 `trusted_proxy_hops` 기반 오른쪽-n번째 XFF 파싱을 구현. 실제 결함은 "peer 미검증 + `4050:4050` 전체 인터페이스 publish"의 결합뿐이며, compose 한 줄 수정으로 해결 가능 → 최우선 처리 제안 | **수용·구현** |
| 2 | SEC-02 권고에 마이그레이션 경로 누락 — 기존 발송 메일의 링크(같은 URL·토큰)가 계속 동작해야 하며, `List-Unsubscribe-Post`(RFC 8058)는 Gmail/Yahoo 벌크 발신자 요건이므로 선택이 아닌 필수 | **수용·구현** |
| 3 | SEC-01 이력 재작성은 실효성 낮음 — 공개 시점에 유출로 간주해야 하므로 실질 방어는 회전뿐. 재작성은 force push 금지 원칙과 충돌하므로 별도 운영 결정으로 분리, 회전 완료 시 P0→P2 강등 | **수용(운영자 이관)** |
| 4 | ARC-01은 "PG 준비" 표기 강등 + migration 예외 삼킴 수정만 단기 처리, Alembic/PG CI는 실제 이행 결정 시점으로 연기 | **보류(별도 트랙)** |
| 5 | `unsubscribe_by_token`의 broad except가 오류를 정상 페이지로 렌더한다는 지적 | **정정** — 재확인 결과 except 분기는 `error` 컨텍스트로 오류 화면을 렌더하고 있어 결함 아님 (QUAL-01 일반론만 유효) |
| 6 | SEC-03 구현 시 `Secure=True` 무조건 적용은 로컬 http 개발을 깨뜨림 — 조건부 적용 필요. rate limit은 단일 엔드포인트이므로 in-memory로 충분 | **수용·구현** |

실행 순서도 보고서의 ①→②→③→④ 대신 **④(한 줄 수정) → ①(운영 절차) → ③ → ②(설계 검토 필요)** 로 재배치하여 진행했다.

## 3. 구현 내역

### SEC-04 — 운영 포트 loopback 바인딩 (수용)

- `docker-compose.prod.yml`: 웹 서비스 publish를 `"4050:4050"` → `"127.0.0.1:4050:4050"`.
- 게이트웨이(nginx)는 `unmong-network` 로 컨테이너에 직접 도달하므로 외부 publish 불필요. 외부 직접 접근이 차단되면 XFF 위조에 의한 rate limit 우회 전제가 사라진다.
- 애플리케이션의 기존 `get_client_ip`(trusted_proxy_hops 오른쪽-n번째 파싱)는 수정 불필요 판단 — 보고서가 권고한 "신뢰 프록시 CIDR 검증"은 바인딩 차단으로 위협 모델이 해소되어 보류.
- **운영 검증**: 배포 후 `docker ps` 에서 `127.0.0.1:4050->4050/tcp` 확인, 게이트웨이 경유(`https://newsletter.unmong.com`) 200 정상.

### SEC-02 — GET 구독 해지 부작용 제거 + RFC 8058 (수용)

- `src/web/app.py`:
  - `GET /{tenant}/unsubscribe/token/{token}` — **조회 전용**으로 변경. 유효 토큰이면 확인 페이지(`unsubscribe_confirm.html`, 신규) 렌더, 무효 토큰이면 오류 화면. 상태 변경 없음 → 메일 보안 스캐너/프리페처 자동 방문에 안전.
  - `POST` 동일 URL — 실제 해지 실행. 확인 페이지 form POST와 RFC 8058 one-click POST(`List-Unsubscribe=One-Click` body, Origin/쿠키 없음 → 기존 CSRF origin 미들웨어 통과) 두 경로 모두 처리.
  - **기존 발송 메일 호환**: 과거 메일의 링크는 같은 URL이므로 확인 페이지를 거쳐 계속 해지 가능.
- `src/common/scheduler/jobs.py`: `_unsubscribe_url` / `_unsubscribe_headers` 헬퍼 신설. 모든 뉴스레터 발송 경로(정규 배치·adhoc·웰컴)에 `List-Unsubscribe: <url>` + `List-Unsubscribe-Post: List-Unsubscribe=One-Click` 헤더 부여.
- `src/common/delivery/gmail_sender.py`: `send()`/`send_batch_efficient()` 메시지에 커스텀 헤더 지원 추가.

### SEC-03 — 관리자 로그인 rate limit + Secure 쿠키 (수용)

- `src/web/admin/auth.py`:
  - 비밀번호 로그인 in-memory 실패 카운터 — **10분 윈도 내 5회 실패 → 429 잠금**(잠금 중에는 정답도 거부), 성공 시 카운터 초기화. IP는 기존 `get_client_ip`(신뢰 프록시 XFF 파싱) 사용. 단일 워커 운영 전제를 코드 주석으로 명시.
  - 세션 쿠키 `Secure` — `WEB_BASE_URL` 이 https일 때 자동 적용(운영), http 로컬 개발은 비적용. 별도 환경변수 불필요 → ARC-03(설정 계약 드리프트)을 악화시키지 않음.
- `src/web/static/help/admin-guide.html`: 로그인 잠금 정책 안내 추가 (헬프 페이지 동시 갱신 규칙 준수).

### 회귀 테스트 (신규 10건 — `tests/test_common/test_security_p1.py`)

검증 보고서 §7 "최종 승인 기준"의 테스트 요구를 직접 충족:

- GET 링크 방문·반복 방문 시 **상태 변경 없음** + 확인 페이지 렌더
- POST 해지 성공(기존 URL 호환) / one-click POST 해지 / 무효 토큰 GET·POST 안전 처리
- `List-Unsubscribe`/`List-Unsubscribe-Post` 헤더 내용 검증
- 실패 한도 초과 → 429 잠금(정답 포함), 성공 시 카운터 초기화
- https base URL → `Secure` 플래그 존재, http → 부재 (`HttpOnly` 는 양쪽 유지)

## 4. 검증 결과

| 항목 | 결과 |
|---|---|
| 전체 pytest | **152 passed** (142 → 152, 0 failed) |
| compileall | 통과 |
| main/prod 상태 | `main == prod == 76c66eb`, 작업 브랜치 삭제 완료 |
| 배포 | deploy-prod.yml success (pytest 게이트 포함) |
| 운영 확인 | 컨테이너 2종 healthy, loopback 바인딩 적용, 게이트웨이 200, 무효 토큰 GET → 오류 화면 정상 렌더 |

## 5. 미반영·보류 항목과 사유

| 항목 | 상태 | 사유 / 다음 단계 |
|---|---|---|
| SEC-01 자격증명 회전 | **운영자 액션 필요** | 코드로 해결 불가. `ADMIN_PASSWORD`(GitHub Secrets) 및 파생 자격증명 즉시 회전 필요 — 본 배포와 무관하게 최우선 |
| SEC-01 Git 이력 재작성 | 보류 | 회전 완료 후 실효성 낮음(유출 간주 원칙). force push 금지 원칙과 충돌 — 별도 운영 결정(백업→재작성→재클론 공지)으로 진행 시에만 |
| SEC-01 secret scanning/push protection | 미착수 | GitHub 저장소 설정 — 운영자 활성화 권장 |
| SEC-05 Engagement 토큰 만료/캠페인 식별 | 보류 | 서명 payload 변경은 기발송 링크 호환·통계 스키마 영향 검토 필요 — 1주 내 별도 브랜치 권장 |
| ARC-01 PG 표기 강등·migration 예외 처리 | 보류 | 설계 문서는 Claude-Opus-bluevlad 저장소 소관. migration warning 삼킴 수정은 후속 브랜치 권장 |
| ARC-02 설계 문서 동기화 | 보류 | Claude-Opus-bluevlad 저장소 작업 — 본 저장소 범위 외 |
| ARC-03 운영 설정 단일 정본·fail-fast | 보류 | 배포 파이프라인 계약 변경 — Secure 쿠키를 env 무의존으로 설계해 이번 변경이 드리프트를 추가하지 않도록 함 |
| OPS-01 / QUAL-01 | 보류 | 지속 개선 트랙 (lock/hash, 경고 제거) |

## 6. 최종 승인 기준 대비 현황 (검증 보고서 §7)

- [ ] 과거 비밀번호 이력 제거 + 운영 비밀번호 회전 — **운영자 대기**
- [x] GET 구독 해지 "상태 변경 없음" 회귀 테스트
- [x] 관리자 로그인 rate limit 및 Secure cookie 테스트
- [x] 직접 포트 접근 차단 (loopback 바인딩, 운영 확인)
- [ ] 운영 필수 설정 fail-fast — 보류 (ARC-03)
- [ ] PG 표기 시 실제 PG 테스트 — 보류 (ARC-01)
- [ ] 설계 상태표 동기화 — 보류 (ARC-02, 외부 저장소)

**남은 차단 이슈는 SEC-01 자격증명 회전 1건**이며, 이는 운영자 절차로만 해소 가능하다.
