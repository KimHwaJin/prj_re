# 094 다중 쿼리 Workflow 등록·HNSW 추천

2026-10-05 / feature/workflow-hnsw-retrieval / 시작 commit a141c11. [확정 계약](../workflow-registration-and-search.md), [변경 추적](../review/workflow-standard-changes.md), [검증 근거](../reports/workflow-hnsw-2026-10-05/README.md).

## 문제와 변경

기존 POST는 document 중심이며 user_queries를 받아도 무시할 수 있었다. Workflow당 활성 벡터 하나만 허용했고 검색 stub은 실제 계획에 연결되지 않았다. JSON SHA만으로는 검색 쿼리 수정 경합을 감지할 수 없었다.

하나의 Workflow JSON+여러 user_queries envelope를 검증·저장하고, 쿼리별 native pgvector 이력을 모델 공간/search_revision으로 구분했다. 별도 resource_revision은 작성 변경 충돌, search_revision은 비동기 색인 게시를 보호한다. candidate는 등록 시 벡터를 만들되 inactive이며 승격 template만 검색한다. 승격/복제·이전 버전에서 같은 텍스트와 모델 공간의 벡터를 재사용하고 batch UPSERT한다. 수정/삭제의 비활성화도 벡터 이력을 애플리케이션으로 가져오지 않는 SQL UPDATE로 처리한다. 임베딩 HTTP 입장/호출은 CRUD 연결을 반환한 후 실행한다. 늦은 완료와 삭제는 행 잠금·버전 재검사로 오래된 검색 벡터의 활성화를 막는다.

HNSW partial index와 이미 찾은 Workflow 제외 반복, 발견 Workflow들의 쿼리만 정확 대표 점수 계산을 연결했다. 후보 수/시간/scan/반복 예산, missing index/모델 설정/장애 진단을 제공하며 전체 exact fallback을 넣지 않았다. 실제 HNSW Index Scan을 확인했다. API/Agent가 기존 DATABASE_URL과 CRUD 풀/중앙 설정을 공유한다.

기존 create_agent planning 미들웨어가 전체 E2E 범위일 때만 검색 Tool을 합성한다. FAQ/결과 설명/부분 요청과 열린 계획 수정에는 검색하지 않는다. 모델은 이번 결과의 ID와 입력값만 선택하고 서버가 원본 정의·정책·버전을 해결한다. 추천+신규 합계는 MAX_PLAN_CANDIDATES, 후보 정의 입력량은 별도 문자 예산을 따른다. 출처 catalog_reference는 사용자 편집·최종 승인 snapshot에 보존한다. 새 분류 LLM/워커/DB 풀을 추가하지 않았다.

미사용 workflow/workflow_recommender.py stub은 삭제했으며 자산의 skills/tools/workflows 패키지는 유지했다. 예제 Skill·Tool 이름을 알고리즘에 하드코딩하지 않았다. 원본1.0 bytes·공개 실행2.0 JSON 정의도 유지했다. 등록·검색 최종 문서와 class 생성 Schema5개/예제, 중앙 설정 주석, 콘솔 OpenAPI를 갱신했다.

## 검증

Agent/API/진단800 passed(492 opt-in skipped), 관련 기능·실제PG27 passed, HTML9 passed. 서로 중복되는 시험 개수는 더하지 않는다. 기존 배열 벡터 이행·과거 데이터 비활성·index 반복 생성·downgrade/upgrade 왕복, wheel build/설치형 import/자산/삭제 stub 제외, 기존 원본4개 SHA와 실제 Schema 일치 검증을 통과했다. [시험 목록·환경·관찰](../reports/workflow-hnsw-2026-10-05/verification.json)을 따른다.

동일 벡터500개가 집중된 synthetic smoke에서는3개 Workflow 중1개만 찾았다. ANN의 전역 누락은 남고 부분 결과가 정직하게 표시된다. 이 기능 완료를 자연어 추천 품질 완료나 운영 처리량 개선률로 해석하지 않는다. HNSW를 쓰기로 한 결정과 이 한계를 구분한다.

## 남은 사항과 배포

실제 embedding 주소·모델·차원은 아직 제공되지 않아 임의 모델을 지정하거나 chat API를 embedding API로 사용하지 않았다. 실제 텍스트 corpus·모델 품질·동시 요청, threshold/ef_search/scan/memory/후보 수의 튜닝이 다음이다. 기존1.0/1.3의 명시 이행, 내부 생성 계획→공개 정의 exporter, Dataset/Artifact 완성 등 기존 후속도 유지한다. 제공 Gaia 템플릿 자체의 기존 SyntaxError와 전체 adapter 통합은 별도다.

기존 로컬 서비스/DB/Redis/Executor를 재기동·배포하지 않았다. 테스트 DB53608 전용 컨테이너만 사용했다. 로컬 Compose·진단 이미지에 pgvector 서버 지원을 반영했고 운영은 extension/DDL 권한·쓰기 이행 구간·모델 index provision·자산 재색인이 필요하다. 분리 feature 브랜치에 구현하며 베이스 병합·원격 push는 하지 않았다.
