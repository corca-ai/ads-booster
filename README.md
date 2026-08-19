# ads-booster

TypeScript, NestJS, PostgreSQL로 구성한 Ads Booster 백엔드입니다. 현재는 DB 연결 상태를 확인하는 헬스 체크 API를 제공합니다.

## 준비 사항

- Node.js 20 이상
- Bun 1.3 이상
- PostgreSQL

## 로컬 실행

```bash
bun install
cp .env.example .env
createdb ads_booster
bun run migration:run
bun run start:dev
```

서버가 실행되면 DB 연결 상태를 확인합니다.

```bash
curl http://127.0.0.1:3000/health
```

정상 응답:

```json
{"status":"ok","database":"connected"}
```

## 환경변수

| 변수 | 기본 예시 | 설명 |
| --- | --- | --- |
| `NODE_ENV` | `development` | 실행 환경: `development`, `test`, `production` |
| `PORT` | `3000` | HTTP 서버 포트 |
| `DATABASE_URL` | `postgresql://park@127.0.0.1:5432/ads_booster` | PostgreSQL 연결 URL |

로컬 계정이 `park`가 아니거나 비밀번호가 필요하면 `DATABASE_URL`을 환경에 맞게 변경합니다.

## 명령어

| 명령어 | 용도 |
| --- | --- |
| `bun run start:dev` | 개발 서버 실행 |
| `bun run build` | TypeScript 빌드 |
| `bun run start:prod` | 빌드 결과 실행 |
| `bun run check` | Biome 및 TypeScript 정적 검사 |
| `bun run test` | E2E 테스트 실행 |
| `bun run migration:run` | 대기 중인 TypeORM 마이그레이션 적용 |
| `bun run migration:generate` | 엔티티 변경으로 마이그레이션 생성 |
| `bun run migration:revert` | 최근 마이그레이션 되돌리기 |

TypeORM의 `synchronize`는 꺼져 있습니다. 스키마 변경은 마이그레이션으로 관리합니다.

## 주요 경로

비즈니스 기능은 `src/domain/<도메인>`이 소유합니다. 공통 설정은 `src/global/config` 아래에서 설정 도메인별로 관리합니다.

| 경로 | 역할 |
| --- | --- |
| `src/main.ts` | NestJS 애플리케이션 시작점 |
| `src/app.module.ts` | 데이터베이스와 도메인 모듈 조합 |
| `src/global/config/environment/` | Zod 기반 환경변수 파싱 |
| `src/global/database/` | TypeORM 연결, 마이그레이션 데이터 소스, Nest DB 모듈 |
| `src/domain/health/` | PostgreSQL 연결 헬스 체크 도메인 모듈 |
| `test/app.e2e-spec.ts` | 실제 PostgreSQL을 사용하는 E2E 테스트 |
