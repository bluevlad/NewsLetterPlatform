# StandUp 주간 인사이트 — 레거시 보관 (2026-08-15 서비스 종료)

메인 화면 개편(2026-08-15)과 함께 StandUp 주간 인사이트 테넌트를 운영에서 제거하고
코드·템플릿을 이 디렉토리에 보관한다. `src/tenant/` 밖에 있으므로
`registry.discover_and_register()` 자동 등록 대상에서 제외된다.

## 원래 위치

| 보관 파일 | 원래 경로 |
|-----------|-----------|
| `tenant/` | `src/tenant/standup/` |
| `templates/weekly_report.html` | `templates/standup/weekly_report.html` |

## 원래 구성 (복원 시 참조)

- 원본 서비스: StandUp (port 9060) `/api/v1/insight/*`
- 발송: weekly 전용 — 매주 월요일 09:30 (수집 08:00)
- `config/tenants.yaml` 의 `standup:` 블록 (git 이력 76c66eb 이전 참조)
- 환경변수 (`.env`): `STANDUP_API_URL`, `STANDUP_WEEKLY_DAY_OF_WEEK`,
  `STANDUP_WEEKLY_COLLECT_HOUR/MINUTE`, `STANDUP_WEEKLY_SEND_HOUR/MINUTE`

## 복원 방법

1. `legacy/standup/tenant/` → `src/tenant/standup/` 이동
2. `legacy/standup/templates/` → `templates/standup/` 이동
3. `config/tenants.yaml` 에 standup 블록 복원 (git 이력 참조)
4. `.env` 에 STANDUP_* 환경변수 확인

기존 발송 이력·아카이브 데이터(DB `tenant_id='standup'`)는 삭제하지 않고 유지한다.
공개 통합 아카이브(`/archive`)는 등록된 테넌트만 노출하므로 화면에는 나타나지 않는다.
