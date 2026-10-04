"""Generate Korean field annotations without changing contract validation rules.

Run from any directory: python scripts/design/annotate_public_contracts.py
JSONC companions are documentation only; API bodies must remain strict JSON.
Unknown field names fail generation so newly added fields require an explanation.
"""
from pathlib import Path
import json
import re

ROOT = Path(__file__).resolve().parents[2]
FIELDS = {
    'project_name': '프로젝트 이름. 요청 body에서 사용하고 공개 응답은 name으로 표시한다.',
    'system_prompt': '사용자 지정 프로젝트 공통 지침. 새 Run 실행 시작 시 고정하며 기존 Run 재개에서는 변경하지 않는다. 빈 문자열은 지침 없음이다.',
    'prompt_version': '프로젝트 지침 변경 버전. 생성1, system_prompt 내용이 실제 변경될 때만 증가하며 메모리 문서 version과 별개다.',
    'is_default': '사용자의 기본 프로젝트인지. 개별 이름 변경·삭제는 허용하지 않는다.',
    'q': '사용자 공개 ID·표시 이름의 대소문자 무시 부분 검색어. 앞뒤 공백을 제거하고 %, _, 역슬래시는 문자 그대로 찾는다.',
    'is_active': '사용자가 soft delete되지 않았는지. 로그인 세션의 유효성이나 자동 복구를 뜻하지 않는다.',
    'quote': '자동 메모리 갱신 내용을 뒷받침하는 현재 사용자 발언의 정확한 원문. 수동 편집에는 없다.',
    'intent': '자동 메모리 갱신 사유. project_context=지속적인 배경, preference_change=선호 변경, remember=명시적 기억 요청.',
    'section': 'Agent 내부 메모리 부분 갱신의 고정 분류. 공개 관리 API에는 section path가 없다.',
    'key': '객체의 내부 참조 키. 프로젝트 메모리의 공개 항목 ID를 뜻하지 않는다.',
    'entries': '프로젝트 공유 메모리 항목 또는 이번 쓰기가 반영한 항목 버전 목록.',
    'expected_version': '쓰기 전에 조회한 프로젝트 메모리 문서 버전. 최초 저장 전만 0이며 오래된 쓰기·초기화는 409다.',
    'is_deleted': '삭제된 메모리의 버전 표시. 사용 가능한 지식이 아니며 자동으로 복원하지 않는다.',

    'schema_version': '계약 형식 버전. SSE는 1, 새 Workflow는 2.0-draft, legacy 예제는 1.3을 사용한다. 정의 수정 횟수와 구분한다.',
    'workflow_id': '재사용 Workflow 정의를 식별하는 문자열. 기존 Workflow 관리 API의 DB UUID와 구분한다.',
    'definition_version': '동일 Workflow 정의의 변경 버전. 승인 화면 편집 횟수나 Run ID가 아니다.',
    'plan_id': '서버가 생성한 계획 후보 ID. 사용자 선택·편집·승인 시 현재 화면의 값을 그대로 보낸다.',
    'plan_revision': '계획 편집 버전. 현재 화면 값과 다르면 stale 요청으로 거절된다.',
    'interaction_id': '현재 HITL 확인 화면을 식별하는 UUID. 질문·판단·수정 액션은 이 화면을 대상으로 한다.',
    'revision': 'HITL 화면 버전. plan_revision 및 Workflow definition_version과 별개다.',
    'resume_token': '현재 사용자 재개 대상을 확인하는 UUID 토큰. 서버가 내려준 최신 값을 사용한다. 로그인 인증 토큰이 아니다.',
    'run_id': '사용자 요청 전체 흐름의 공개 Run UUID. resume에서도 같은 Run을 이어간다.',
    'session_id': '대화 세션 UUID. 같은 세션의 동시 실행은 제한되며 다른 세션과 구분된다.',
    'user_id': '서비스에 등록된 공개 사용자 식별자. 사내 SSO 연동 시 직원 식별자에 연결된다.',
    'project_id': '프로젝트 UUID. 프로젝트 소유권·공유 문맥을 구분한다.',
    'file_id': '향후 파일·이미지 첨부를 식별할 UUID. 현재 실제 Run 접수는 해당 입력을 422로 거절한다.',
    'input': '새 요청의 입력 객체. command와 함께 보내지 않는다. 현재 실제 입력은 text만 지원한다.',
    'command': '기존 Run의 HITL 응답 명령 객체. 새 입력 input과 함께 보내지 않는다.',
    'resume': '현재 대기에 맞는 action과 필드를 담은 재개 명령. 아무 액션이나 모든 화면에 보낼 수 없다.',
    'action': '재개 행위 종류. edit_plan/approve_plan/replan/answer_clarification/approve_decisions/approve_repair/reject_repair 중 현재 화면에 맞는 값을 쓴다.',
    'main_model_name': '등록된 모델 별칭. 새 요청에서 생략하면 기본 모델을 사용하며 재개에서는 바꿀 수 없다.',
    'model_revision': 'Run에 고정된 모델 설정 revision. Workflow나 승인 화면의 revision과 별개다.',
    'content': '입력 또는 Agent 메시지의 콘텐츠 블록 배열. 블록의 type으로 형식을 구분한다.',
    'text': '사용자 입력 또는 Agent 메시지 본문 문자열.',
    'role': '메시지 작성자 또는 사용자 권한 구분. 메시지는 assistant/user 등, 사용자 조회는 admin/user다.',
    'channel': 'Agent 메시지 용도. commentary는 중간 설명, answer는 답변으로 화면을 구분한다.',
    'input_values': '계획의 입력 이름별 최종값. 전달한 키만 수정하고 기존의 다른 입력값은 유지한다.',
    'step_changes': 'Tool 함수 인자 편집 목록. step_id·parameter·value로 수정 대상을 지정한다.',
    'parameter': '수정할 Tool 함수의 인자 이름. 편집 가능한 인자만 서버 검증을 통과한다.',
    'excluded_step_ids': '사용자가 제외한 Step ID 목록. 필드를 보내면 전체 제외 목록을 교체하고 생략하면 기존 목록을 유지한다.',
    'execution_overrides': '사용자가 조정하는 실행 정책. 허용 mode·수정 수준·횟수 상한 내에서만 변경된다.',
    'feedback': '계획 재작성 요청 또는 추가 질문 답변. 공백만 허용하지 않으며 최대 4000자다.',
    'values': '현재 판단 확인 화면의 decision_id별 최종값. 대기 중인 모든 판단을 빠짐없이 제출한다.',
    'proposal_sha256': '승인·거절할 수정 제안의 64자리 소문자 SHA-256. 다른 제안에 동의가 적용되지 않게 한다.',
    'allow_policy_escalation': '제안이 현재 수정 권한보다 높은 수준을 요구할 때 명시적으로 동의하는 값. 기본 false이며 시스템 상한은 넘을 수 없다.',
    'reason': '사용자가 취소 API에 전달하는 사유. 선택 항목이며 최대 1000자다.',
    'status': '이 객체의 현재 상태. Run 실행 상태, HITL open/resolved, 계획 Step planned/excluded 등 문맥별로 값이 다르다.',
    'interrupt': 'Run이 대기 중인 사용자 확인 화면 목록. 보통 kind에 맞춰 UI를 구성한다. 현재 대기가 없으면 null일 수 있다.',
    'failure': 'Run 실패 정보. 정상 또는 미실패 상태에서는 null일 수 있다.',
    'result': '종료한 Run의 결과 객체. 미종료 시 null이며 route·final_response 등은 주 API 문서의 결과 규격을 따른다.',
    'recovery_required': '자동 진행이 안전하지 않아 복구 판단이 필요한지 표시하는 값. 완료 성공으로 해석하지 않는다.',
    'checkpoint_run_id': '내부 LangGraph checkpoint 연결 식별자. 공개 Run ID나 session_id와 같은 의미로 사용하지 않는다.',
    'task_id': '내부 업무 Task 연결 UUID. 프론트 실행·재개 명령은 공개 Run API를 사용한다.',
    'attempt_count': 'Run 처리 시도 수. 모델 호출 수나 MULTI 오류 수정 횟수와는 별개다.',
    'next_attempt_at': '다음 처리 시도 예정 시각. 해당 예약이 없으면 null이다.',
    'cancel_reason': '서버에 기록된 취소 사유. 취소 요청이 없으면 null이다.',
    'cancel_requested_at': '취소 요청 기록 시각. 실제 취소 완료 시각과 구분한다.',
    'created_at': '이 자원이 생성된 시각. ISO 8601 문자열로 표현한다.',
    'updated_at': '이 자원의 마지막 갱신 시각. ISO 8601 문자열로 표현한다.',
    'started_at': 'Run 실행 시작 시각. 시작 전에는 null일 수 있다.',
    'completed_at': 'Run 종료 시각. 미종료 상태에서는 null일 수 있다.',
    'sequence': '저장 SSE 이벤트의 순번. 실제 전송의 id 및 재연결 Last-Event-ID에 대응한다.',
    'occurred_at': '저장 이벤트 발생 시각. 현재 상태 snapshot에는 없는 필드다.',
    'cursor': 'snapshot에서는 당시 조회 기준 커서이며 이미 처리한 모든 이벤트를 의미하지 않는다. 목록 API의 cursor는 별도의 페이지 토큰이다.',
    'data': 'SSE 이벤트의 실제 payload. type/kind에 따라 메시지·HITL·현재 Run 상태 등 구조가 달라진다.',
    'payload': '해당 HITL 또는 이벤트 종류의 세부 데이터. 계획·질문·판단·수정 제안을 담는다.',
    'kind': '대상 객체 종류. HITL은 plan_review/planning_question/decision_review/repair_review이며 입력·파라미터·산출물에도 각각 별도의 kind가 있다.',
    'summary': '사용자에게 표시할 핵심 설명. 실제 상세 정보는 payload나 관련 필드를 함께 확인한다.',
    'plans': '사용자에게 제시하는 코드 없는 PlanView 후보 목록. 설정된 최대 후보 수 범위에서 제시된다.',
    'approved_plan': '승인된 계획의 공개 PlanView. 실행에 필요한 소스까지 고정한 내부 approved snapshot과 다르며 Python 코드를 노출하지 않는다.',
    'resolution': 'HITL 처리 결과. approved/auto_approved/replanning/answered/rejected 등으로 화면 처리를 구분한다.',
    'notices': '화면에 함께 알려줄 안내 문자열 목록. 빈 배열이면 추가 안내가 없다.',
    'question': 'Agent가 사용자에게 묻는 추가 질문 본문.',
    'revision_policy': '계획 재작성 횟수 및 자유 코드 전환 정책의 현재 적용값.',
    'used': '이번 계획 흐름에서 사용한 재작성 횟수.',
    'limit': '허용된 계획 재작성 횟수 상한. 목록 조회 query의 limit와 구분한다.',
    'free_code_allowed': '등록 Tool 조합으로 해결되지 않을 때 자유 코드 계획을 허용하는지 표시한다.',
    'free_code_require_approval': '자유 코드 계획을 실행하기 전에 사용자 승인을 요구하는지 표시한다.',
    'name': '해당 입력·계획·Skill·함수 인자 등의 이름. binding의 name은 Workflow 입력 키를 참조한다.',
    'title': '사용자 화면의 표시 제목. value_schema 내부에서는 JSON Schema 표시 제목이다.',
    'description': '해당 정의·입력·Skill·Step·산출물의 의미를 사람이 읽을 수 있게 설명한 문자열.',
    'goal': 'Workflow 또는 계획이 달성하려는 분석 목표.',
    'tags': '정의·검색·분류에 사용하는 문자열 태그 목록.',
    'skills': '계획에 사용되는 등록 Skill 정보 목록. Skill은 Tool 묶음과 사용 지침을 제공한다.',
    'skill_id': '레포에 등록된 Skill의 고유 식별자. 그 Skill에 속한 Tool을 참조해야 한다.',
    'tool_id': '레포에 등록된 실행 가능한 Tool의 고유 식별자.',
    'function_name': 'Executor에서 실행할 Tool의 Python 함수 이름. 함수 본문은 공개 계획에 포함하지 않는다.',
    'steps': '분석 단계 목록. 새 Workflow의 Step은 하나의 등록 Tool을 참조하며 depends_on으로 선후관계를 정한다.',
    'step_id': '대상 Step의 ID. 편집 대상으로 지정하거나 이전 결과·의존성·근거를 참조할 때 쓴다.',
    'depends_on': '먼저 완료되어야 하는 Step ID 목록. 단순 배열 순서와 구분되며 순환 의존성은 허용하지 않는다.',
    'parameters': '사용자에게 보여주는 Tool 함수 인자 목록. kind와 editable에 따라 값 표시·편집 UI를 구성한다.',
    'inputs': '새 Workflow에서는 입력 이름별 정의 객체, PlanView에서는 입력 필드 목록, legacy에서는 실행 입력값 객체다.',
    'editable': '사용자가 수정할 수 있는지 표시한다. true여도 value_schema 및 실행 정책 검증을 통과해야 한다.',
    'required': '업무 입력·산출물에서는 필요한지 나타내는 boolean. JSON Schema에서는 필수 필드 이름 배열이다.',
    'value_schema': '입력·편집값을 검증하는 JSON Schema 객체 또는 boolean. 필수 여부 및 값 유효성은 별도다.',
    'has_value': '값이 실제로 정해져 있는지 표시한다. value=null 자체만으로 미확정이라고 판단하지 않는다.',
    'value': '직접 지정한 값·편집값·판단값. 의미와 허용 타입은 해당 입력 또는 파라미터 schema를 따른다.',
    'origin': '입력값이 정해진 출처. agent/workflow_default/user/unresolved를 구분한다.',
    'input_name': '이 인자가 참조하는 Workflow 입력 이름. 값은 대응하는 입력 필드에서 편집한다.',
    'selector': '이전 Step 반환값에서 추출할 키·인덱스 경로 배열. []는 반환값 전체이며 새 규격은 Python 식을 평가하지 않는다. legacy는 문자열 selector다.',
    'decision_id': '결과 기반 Agent 판단의 ID. 판단 정의·확인 화면·Tool 인자의 연결에 사용한다.',
    'guidance': 'Agent 판단 또는 지연 파라미터 선택에 적용할 지침. 사용자에게 선택 근거와 함께 보여줄 수 있다.',
    'context_key': '서버가 제공하는 실행 문맥 키. user_id/project_id/session_id/dataset_output_dir 등의 참조다.',
    'decisions': '실행 결과를 보고 나중에 확정할 판단 목록. 정적 계획과 결과 기반 판단을 분리한다.',
    'evidence_steps': '판단 또는 보고서가 근거로 사용할 Step ID 목록. 실제 실행 결과를 사용한다.',
    'execution': '새 Workflow/PlanView에서는 실행 정책 객체. legacy Step/Tool에서는 실행 조건 값이다.',
    'mode': 'SINGLE은 제출 계획을 한 실행 단위로, MULTI는 결과 판단·후속 Operation을 포함한 흐름으로 실행한다. 조건/decision은 MULTI가 필요하다.',
    'repair_level': '허용하는 오류 수정 자율성 수준 0~4. 상세 단계별 권한은 오류 수정 Runtime 문서를 따른다.',
    'max_repair_attempts': '허용하는 오류 수정 시도 수. 무한 반복을 허용하지 않으며 서버 상한 이하만 가능하다.',
    'review_mode': '실행 결과 이후 확인 정책. decision_boundary/every_tool/every_n_tools을 구분한다. 일반적인 계획 최초 승인을 대체하지 않는다.',
    'review_interval_tools': 'review_mode=every_n_tools에서 몇 개 Tool마다 확인할지 정하는 값.',
    'allowed_modes': '현재 계획·서버 정책이 사용자에게 허용하는 실행 mode 목록.',
    'repair_level_limit': '사용자가 조정할 수 있는 오류 수정 수준의 시스템 상한.',
    'max_repair_attempts_limit': '사용자가 조정할 수 있는 오류 수정 횟수의 시스템 상한.',
    'execution_kind': 'registered는 등록 자산 조합, free_code는 실행별 자유 코드가 포함된 계획이다.',
    'workflow_eligible': '등록된 Skill·Tool만의 재사용 Workflow로 인정할 수 있는지 표시한다. 실행 성공과 별개이며 수정 소스·자유 코드는 적격이 아닐 수 있다.',
    'approval_mode': 'user는 사용자 승인, configuration은 설정에 따른 승인 처리다.',
    'outputs': 'PlanView에서는 예상 산출물 목록. legacy에서는 출력 이름과 결과 참조의 매핑이다.',
    'output_id': '사용자에게 보여주는 예상 산출물의 ID.',
    'expected_outputs': 'Workflow가 기대하는 분석 결과·데이터·보고서 선언. 선언만으로 파일이 생성되는 것은 아니다.',
    'arguments': '새 Workflow에서는 실제 함수 인자 이름별 binding. legacy에서는 인자 값 또는 참조가 들어간다.',
    'source': '값을 얻는 방식. workflow_input/literal/step_output/agent_decision/system_context, 보고서에서는 agent_report를 구분한다.',
    'when': '단계 또는 산출물의 조건. false이면 해당 항목을 실행/요구하지 않으며 자유 Python 조건문을 입력하지 않는다.',
    'op': '비교 연산자. eq/ne/gt/gte/lt/lte/in/not_in 중 schema가 허용한 값을 쓴다.',
    'left': '조건 비교의 왼쪽 값 binding.',
    'right': '조건 비교의 오른쪽 값 binding.',
    'all': '하위 조건이 모두 참일 때 참인 AND 조건 배열.',
    'any': '하위 조건 중 하나 이상 참일 때 참인 OR 조건 배열.',
    'not': '하위 조건의 결과를 뒤집는 NOT 조건.',
    'key': 'system_context binding이 참조할 서버 제공 문맥 키.',
    'after_steps': 'Agent가 판단하기 전에 결과를 확보해야 하는 Step ID 목록.',
    'instruction': '결과를 읽고 판단값을 확정하는 Agent용 지침.',
    'output_schema': 'Agent 판단 결과값을 검증하는 JSON Schema. 예를 들어 boolean 또는 선택지 enum을 정의한다.',
    'parameter_controls': 'Tool 인자 이름별 편집 허용·값 schema. literal/판단 인자의 사용자 편집 규칙을 정의한다.',
    'format': '산출물 표현 형식 또는 Schema 형식 제약. 산출물은 native/parquet/markdown/html/json 등을 선언한다. 선언과 실제 저장 지원은 구분한다.',
    'attempt': '현재 제시된 수정 시도 번호. 수정 이력·횟수 상한과 함께 확인한다.',
    'max_attempts': '현재 오류 수정 흐름에 적용된 최대 시도 수.',
    'authorized_level': '현재 사용자·정책이 승인한 오류 수정 수준.',
    'required_level': '이번 수정 제안을 수행하는 데 필요한 최소 수정 수준.',
    'requires_policy_escalation': '이번 제안에 현재 승인 수준보다 높은 권한이 필요한지 표시한다.',
    'failed_step_ids': '수정 제안의 원인이 된 실패 Step ID 목록.',
    'completed_step_ids': '이미 완료된 Step ID 목록. 무조건 다시 실행할 대상으로 보지 않는다.',
    'changed_step_ids': '이번 수정 제안에서 변경되는 Step ID 목록.',
    'source_modified': '등록 함수 원문을 수정하는 제안인지 표시한다. Workflow 적격 여부에 영향을 준다.',
    'validation_scope': '제안이나 결과에 대해 검증한 범위. 의미적 정확성이나 Python sandbox 보장을 뜻하지 않는다.',
    'workflow': '기존 1.3 문서의 실제 Workflow 정의 wrapper. 새 2.0-draft는 이 wrapper 없이 루트에 정의한다.',
    'execution_mode': 'legacy Workflow의 실행 모드. 새 규격 execution.mode와 구분한다.',
    'input_schema': 'legacy 입력 이름별 타입·필수 여부·추론·검증 규칙.',
    'input_provenance': 'legacy 입력값의 출처를 기록하는 객체.',
    'unresolved_inputs': 'legacy에서 아직 정해지지 않은 입력의 질문 목록.',
    'allow_llm_inference': 'legacy 입력을 LLM이 추론하여 채워도 되는지 정의한다.',
    'validation': 'legacy 입력에 적용할 검증 규칙 목록.',
    'required_for': '이 입력이 필요한 legacy Step/Tool 참조 목록.',
    'context': 'legacy 실행에 사용하는 추가 문맥 객체.',
    'order': 'legacy Step/Tool 표시·실행 순서 번호. 새 규격은 의존성 관계를 사용한다.',
    'skill': 'legacy에서 참조하는 Skill 이름.',
    'skill_source': 'legacy Skill 문서 소스 경로. 새 정의는 skill_id를 사용한다.',
    'tool': 'legacy에서 실행할 Tool 함수 이름.',
    'tool_origin': 'legacy Tool의 출처 구분. 예제 registry는 등록 자산을 의미한다.',
    'tool_source': 'legacy Tool 함수 소스 경로. 새 정의는 tool_id로 등록 자산을 해석한다.',
    'selection_reason': 'legacy에서 이 Tool을 선택한 이유.',
    'tools': 'legacy Step 내부에 배치한 Tool 목록. 새 Step의 단일 tool_id와 구분한다.',
    'argument_sources': 'legacy 함수 인자 이름별 값의 출처 구분.',
    'returns': 'legacy Tool 반환값을 변수·출력으로 연결하는 규칙.',
    'result_variable': 'legacy Tool 반환값 전체를 보관할 변수 이름.',
    'variable': 'legacy 반환값 또는 추출값을 연결할 변수 이름.',
    'log_id': '저장된 Agent 로그 레코드 UUID.',
    'event_key': '로그와 이벤트의 중복 저장·연결에 사용하는 내부 키.',
    'agent_name': '로그를 발생시킨 Agent 이름.',
    'node': '로그를 발생시킨 그래프 노드 이름.',
    'event': '로그 이벤트 이름. SSE envelope의 type과 혼동하지 않는다.',
    'user_name': '서비스 사용자 표시 이름.',
    'default_project_id': '사용자 기본 프로젝트 UUID. 최초 SSO 등록 시 생성되는 기본 프로젝트에 연결된다.',
    'delete_yn': '사용자 비활성/삭제 표시. Y/N을 구분하며 계정 권한과 별개다.',
    'deleted_at': '사용자가 비활성/삭제 처리된 시각. 해당하지 않으면 null이다.',
    'csrf_token': '로그인 세션의 CSRF 값. 변경 요청의 X-CSRF-Token 헤더에 넣으며 로그인 쿠키를 대체하지 않는다.',
    'login_expires_at': '현재 서비스 로그인 세션의 만료 시각. 요청마다 자동 연장하지 않는다.',
    'next_cursor': '다음 목록 페이지 요청에 사용할 cursor. 다음 페이지가 없으면 null일 수 있다.',
    'has_next': '다음 목록 페이지가 존재하는지 표시한다.',
    'page': '목록 응답의 페이지 정보.',
    'detail': 'HTTP 오류의 상세 정보. validation 오류에서는 필드별 오류 목록이다.',
    'loc': '유효성 검증 오류가 발생한 위치 경로. body·필드명·배열 인덱스 등을 포함한다.',
    'msg': '유효성 검증 오류 설명.',
    'ctx': '유효성 검증 오류의 부가 문맥.',
    'sort': '목록 정렬. created_at 또는 -created_at으로 오름/내림차순을 선택한다.',
    'created_at_from': '목록 생성 시각의 포함 하한.',
    'created_at_to': '목록 생성 시각의 제외 상한.',
    'return_to': '로그인 성공 후 돌아갈 허용된 경로/대상. 임의 외부 URL은 허용하지 않는다.',
    'target': '로그인 복귀 대상. app은 프론트, docs는 서비스 Swagger이며 기본 app이다.',
    'Idempotency-Key': '논리 요청 중복 접수를 막는 헤더. 동일 요청 재시도는 같은 키, 다른 사용자 액션은 새 키를 쓴다.',
    'Last-Event-ID': '클라이언트가 마지막으로 처리한 저장 SSE 순번. 그 다음 이벤트부터 재생한다.',
    'X-CSRF-Token': '로그인 쿠키와 함께 POST/PUT/PATCH/DELETE에 필요한 CSRF 헤더. GET에는 검증을 요구하지 않는다.',
}
KEYWORDS = {
    '$schema': '사용하는 JSON Schema 명세 버전 URI.', '$id': '이 Schema 문서를 식별하는 URI.',
    '$ref': '같은 문서의 정의 또는 components schema 참조. 실제 업무 데이터 값이 아니다.',
    '$defs': '이 Schema에서 재사용하는 타입 정의 모음.', 'properties': '객체 필드 이름별 검증 규칙.',
    'additionalProperties': '정의되지 않은 필드 허용 여부 또는 동적 키의 값 schema. false면 임의 필드를 거절한다.',
    'propertyNames': '객체의 동적 키 이름에 적용할 검증 규칙.', 'items': '배열 원소에 적용할 Schema.',
    'oneOf': '열거한 Schema 중 정확히 하나에 맞아야 하는 선택 구조.',
    'anyOf': '열거한 Schema 중 하나 이상에 맞아야 하는 선택 구조. null 허용도 이 구조로 표현한다.',
    'allOf': '열거한 모든 Schema를 만족해야 하는 구조.', 'const': '반드시 일치해야 하는 단일 고정값.',
    'enum': '허용하는 값 목록.', 'default': 'Schema의 기본값 설명. 업무 실행 시 실제 값 주입 여부는 해당 계약을 따른다.',
    'minimum': '허용 숫자의 포함 하한.', 'maximum': '허용 숫자의 포함 상한.',
    'exclusiveMinimum': '허용 숫자의 제외 하한.', 'exclusiveMaximum': '허용 숫자의 제외 상한.',
    'minLength': '허용 문자열의 최소 길이.', 'maxLength': '허용 문자열의 최대 길이.',
    'minProperties': '객체에 필요한 최소 필드 수. Project PATCH는 이름 또는 지침 중 적어도 하나를 지정한다.',
    'minItems': '허용 배열의 최소 원소 수.', 'maxItems': '허용 배열의 최대 원소 수.',
    'uniqueItems': '배열 원소의 중복을 허용하지 않는지 표시한다.',
    'pattern': '문자열이 만족해야 하는 정규식.', 'discriminator': '선택 구조를 구분하는 필드 정보.',
    'propertyName': '선택 구조를 판별할 필드 이름.', 'mapping': '판별값과 대상 Schema 참조의 매핑.',
    '$comment': 'Schema 작성자를 위한 주석. 업무 payload 필드 또는 검증 규칙이 아니다.',
}
OPENAPI = {
    'openapi': 'OpenAPI 명세 버전.', 'info': 'API 명세의 제목·버전 등 기본 정보.',
    'version': 'OpenAPI 문서에 선언된 서비스 버전.', 'paths': 'URL 경로별 HTTP operation 정의.',
    'get': 'GET operation. 조회·SSE 구독 또는 SSO 로그인 이동에 사용한다.',
    'put': 'PUT operation. 해당 자원 전체를 수정한다. 메모리는 Markdown 문서 전체를 교체한다.',
    'delete': 'DELETE operation. 사용자는 soft delete, 메모리는 문서 초기화·버전 증가를 수행한다.',
    'patch': 'PATCH operation. 사용자 이름·권한 등 해당 자원의 허용 필드만 변경한다.',
    'post': 'POST operation. 새 요청·resume·취소·로그아웃 등에 사용한다.',
    'summary': 'Swagger 등에 표시할 operation 요약.', 'operationId': 'OpenAPI operation 식별자.',
    'parameters': 'path/query/header의 요청 파라미터 목록. JSON body 필드와 별개다.',
    'in': '요청 파라미터 위치. path/query/header/cookie를 구분한다.',
    'schema': '이 파라미터 또는 body/응답의 데이터 규격.',
    'requestBody': 'HTTP 요청 body 정의.', 'content': 'MIME type별 body/응답 규격.',
    'responses': 'HTTP 상태 코드별 응답 정의. 실제 SSE/redirect의 annotation 제한은 API 문서를 따른다.',
    'security': 'operation이 요구하는 인증 방식. CSRF는 변경 요청에서 별도로 검증한다.',
    'components': '재사용하는 데이터 Schema와 인증 정의.', 'schemas': 'API에서 사용하는 데이터 모델 정의 모음.',
    'securitySchemes': 'OpenAPI 인증 방식 정의 모음.',
    'LoginSession': '서비스 로그인 쿠키를 사용하는 인증 방식. 토큰 발급 API를 뜻하지 않는다.',
    'application/json': '일반 JSON body/응답 MIME type. 현재 사본의 SSE 표기는 실제 text/event-stream과 다를 수 있다.',
}
MODELS = {
    'MemoryPut': '프로젝트 메모리 Markdown 문서 전체와 현재 버전의 명시적 수정 요청.',
    'MemoryResource': '프로젝트당 하나의 메모리 문서와 버전·최종 변경 시각. 별도 memory_id가 없다.',

    'RunRequest': '새 입력 또는 현재 Run의 사용자 재개 요청.', 'RunCancel': 'Run 취소 API body.',
    'PublicRunSummary': '목록용 공개 Run 요약. 상세 대기 화면·최종 결과·resume token은 단건 조회한다.',
    'PublicRunResource': '공개 Run의 상태·대기 화면·최종 결과.', 'AgentRunLogResource': 'Agent 실행을 조사하는 구조화된 진단 로그. 프론트 진행 표시는 SSE를 사용한다.',
    'RunEvent': '저장 SSE 이벤트의 공통 envelope.', 'PlanView': '코드 없는 사용자용 계획 확인 화면.',
    'InteractionEvent': '계획 확인 HITL 열림/갱신 이벤트.', 'InteractionResolvedEvent': '계획 승인 완료 이벤트.',
    'PlanningTransitionEvent': '재작성·질문 응답 등에 따른 HITL 종료/전환 이벤트.',
    'DecisionInteractionEvent': '결과 기반 판단 확인 HITL 이벤트.', 'RepairInteractionEvent': '오류 수정 승인 HITL 이벤트.',
    'ClarificationEvent': '계획 작성 중 추가 질문 HITL 이벤트.', 'ChatInput': '콘텐츠 블록을 담는 새 사용자 입력.',
    'DecisionAction': '현재 판단 화면의 최종값 제출.', 'ExecutionOverrides': '승인 시 조정할 실행 정책.',
    'FileContent': '향후 파일 입력 블록. 현재 서버 접수 미지원.', 'ImageContent': '향후 이미지 입력 블록. 현재 서버 접수 미지원.',
    'ParameterChange': '하나의 Step 함수 인자 수정.', 'PlanAction': '계획 편집 또는 최종 승인 요청.',
    'PlanRevisionAction': '자연어 재작성 또는 추가 질문 답변.', 'RepairAction': '오류 수정안 승인/거절 요청.',
    'ResumeCommand': '현재 HITL에 맞는 재개 액션 선택 구조.', 'TextContent': '텍스트 입력·메시지 블록.',
    'DecisionView': '계획의 지연 판단 표시 정보.', 'InputView': '사용자에게 보여주는 계획 입력 정보.',
    'OutputView': '계획의 예상 산출물 표시 정보.', 'ParameterView': 'Tool 함수 인자의 값·출처·편집 정보.',
    'PolicyView': '계획의 실행 정책과 사용자 조정 상한.', 'SkillView': '등록 Skill 표시 정보.',
    'StepView': '등록 Tool 기반 실행 단계의 공개 표시 정보.', 'PlanningRevisionPolicy': '계획 재작성·자유 코드 전환 정책.',
    'ReviewPayload': '계획 후보와 안내·재작성 정책.', 'InteractionData': '계획 확인 화면 본문.',
    'ResolutionData': '계획 승인 종료 이벤트 본문.', 'ResolutionPayload': '승인된 공개 계획 및 안내.',
    'PlanningTransitionData': '계획 재작성·질문 응답 등의 화면 종료 본문.', 'PlanningTransitionPayload': '화면 전환 안내.',
    'DecisionField': '사용자가 확인할 판단값·근거·값 Schema.', 'DecisionInteractionData': '판단 확인 화면 본문.',
    'DecisionPayload': '현재 확인할 판단 목록.', 'RepairInteractionData': '수정 승인 화면 본문.',
    'RepairPayload': '수정 제안·변경 단계·권한·횟수 정보.', 'ClarificationData': '추가 질문 화면 본문.',
    'ClarificationPayload': '추가 질문과 안내·재작성 정책.',
    'DeleteYN': '비활성/삭제 표시 Y/N.', 'HTTPValidationError': '요청 유효성 검증 실패 상세.',
    'Page_AgentRunLogResource_': '진단 로그 목록과 페이지 정보. 기본50개·최대200개를 반환한다.',
    'PageInfo': '다음 페이지 커서·존재 여부.', 'Page_PublicRunResource_': '과거 전체 Run 응답 목록 형식.', 'Page_PublicRunSummary_': 'Run 요약 목록 및 페이지 정보.',
    'ProjectSummary': '프로젝트 선택 목록의 ID·이름·기본 여부·생성/변경 시각. 지침·하위 세션은 읽지 않는다.',
    'Page_ProjectSummary_': '소유한 활성 프로젝트 요약 목록과 페이지. 기본50·최대200개다.',
    'ProjectResource': '프로젝트 상세·생성·수정 응답. 공통 지침과 그 변경 버전을 포함한다.',
    'ProjectCreate': '프로젝트 생성 요청. 이름·공통 지침만 허용하며 공백 이름은 기존 기본 프로젝트 충돌 정책을 따른다.',
    'ProjectUpdate': '프로젝트 이름·공통 지침 부분 수정. 지정한 null은 거절하고 빈 지침은 초기화한다.',
    'UserSummary': '관리자 사용자 목록용 요약. 기본 프로젝트·로그인 세션 정보는 포함하지 않는다.',
    'Page_UserSummary_': '관리자 사용자 요약 목록·페이지. 기본50·최대200개다.',
    'UserRead': '본인 또는 관리자의 사용자 상세. 관리자만 삭제된 사용자도 조회한다.',
    'UserCreate': '관리자가 등록할 공개 사용자 ID·이름·권한.',
    'UserUpdate': '관리자의 표시 이름·권한 수정. ID 변경·복구는 제공하지 않는다.',
    'UserMe': '현재 로그인한 사용자와 CSRF·만료 정보.', 'UserRole': '사용자 권한 admin/user.',
    'ValidationError': '개별 필드의 요청 검증 오류.',
    'id': 'Workflow/Step/decision/산출물의 규격화된 문자열 ID.',
    'binding': '입력값·리터럴·이전 결과·Agent 판단·시스템 문맥의 연결 구조.',
    'condition': '비교·AND·OR·NOT으로 구성하는 조건 구조.', 'input': 'Workflow 입력 정의.',
    'step': '하나의 등록 Skill·Tool 실행 단계.', 'decision': '실행 결과 이후 확정할 Agent 판단.',
    'output': '기대 산출물 및 결과/보고서 연결.', 'execution': '실행 mode·수정 수준·확인 정책.',
}


MODELS.update({
    "RunDiagnosticsResource": "공개 Run 아래에서 읽는 내부 Task·현재 세션 점유 진단 snapshot.",
    "TaskDiagnostics": "최신 invocation에 연결된 내부 Task의 읽기 전용 진단 정보.",
    "SessionWorkDiagnostics": "같은 세션 전체의 현재 미완료 작업·입력 차단 원인.",
    "SessionExecutionDiagnostics": "같은 세션의 현재 실행 점유 기록. heartbeat로 생존을 확정하지 않는다.",
    "RunInvocationResource": "공개 Run에 속한 최초 호출·각 resume의 내부 실행 구간 진단.",
    "Page_RunInvocationResource_": "공개 Run의 내부 실행 구간 목록과 페이지 정보. 기본50·최대200개.",
    "AgentRunStatus": "내부 실행 구간 상태. interrupted는 사용자 대기와 Executor 대기를 직접 구분하지 않는다.",
    "TaskStatus": "내부 Task 상태. waiting_input에는 Executor 대기도 포함될 수 있다."
})
DIAGNOSTIC_FIELDS = {
    "run_id": "HITL 재개 전후에 유지되는 공개 Run ID. 내부 invocation_id와 구분한다.",
    "session_id": "이 공개 Run이 속한 대화 세션 UUID.",
    "observed_at": "이 진단 SQL statement의 DB 관측 시각. 실행 완료나 heartbeat 시각이 아니다.",
    "task": "최신 실행 구간에 연결된 내부 Task 진단 정보. 연결이 없거나 다른 Session/Run이면 null이다.",
    "session_work": "해당 Run뿐 아니라 같은 세션 전체의 현재 미완료 작업·점유 진단이다.",
    "task_id": "내부 Task 레코드 UUID. API 조회 주소는 공개 run_id를 사용한다. 과거 Task 없는 invocation은 null이다.",
    "graph_task_id": "Agent 그래프에서 사용하는 분석 작업 ID. 서비스 task_id와 별개다.",
    "root_run_id": "Task에 기록된 최초 실행 구간 ID.",
    "checkpoint_run_id": "Task에 기록된 LangGraph checkpoint 기준 ID. 진단값이며 API 경로를 조립하는 값이 아니다.",
    "trigger_message_id": "내부 Task를 시작하게 한 메시지 UUID.",
    "trigger_type": "내부 Task의 실행 계기 분류 문자열.",
    "is_unfinished": "이 Task가 미종료 상태이거나 복구 확인이 필요한지. 세션 전체 상태와 별개다.",
    "lock_owner": "Task lease에 기록된 소유자. 현재 그래프 점유는 session_work.execution에서 확인한다.",
    "heartbeat_at": "마지막으로 기록된 heartbeat 시각. 오래됐다는 이유만으로 종료나 미점유를 확정하지 않는다.",
    "lease_expires_at": "기록된 Task lease 만료 시각. 세션 점유 해제나 재실행 허가를 뜻하지 않는다.",
    "cancel_requested_at": "취소 요청이 기록된 시각. 실제 종료 확인과 별개다.",
    "failure_reason": "Task에 기록된 내부 실패 사유.",
    "recovery_required": "Task 또는 실행 점유에 대한 복구 확인 필요 여부. 이 조회는 복구를 실행하지 않는다.",
    "created_at": "해당 Task 또는 invocation 레코드의 DB 생성 시각.",
    "updated_at": "해당 Task 또는 invocation 레코드의 마지막 갱신 시각.",
    "completed_at": "해당 Task 또는 invocation의 기록된 종료 시각. invocation 종료는 공개 Run 전체 완료와 다르다.",
    "resources_active": "연결된 User·Project·Session이 모두 활성인지. 관리자만 숨김 자원도 조회한다.",
    "has_unfinished_work": "세션 전체에 미종료 작업·복구 필요·점유 또는 종료 불명이 존재하는지.",
    "can_start_new_run": "새 일반 입력에 대한 보수적 진단 snapshot. HITL 재개 권한이나 예약이 아니며 UI는 세션 availability, POST는 실제 재검사를 사용한다.",
    "blocking_reasons": "세션 전체의 새 일반 입력 차단 원인 목록. 사용자용 availability.reason과 별개의 내부 진단이다.",
    "execution": "같은 세션의 현재 실행 점유 기록. 조회한 Run과 다른 Run의 점유일 수도 있다.",
    "ownership_held": "DB에 세션 실행 점유가 기록되어 있는지. 워커의 실제 생존을 입증하지 않는다.",
    "owner_kind": "기록된 점유 종류. api_run 또는 executor_event 등.",
    "owner_id": "기록된 점유자 ID. 공개 Run 조회 경로를 조립하는 값이 아니다.",
    "owner_process": "점유자로 기록된 프로세스 식별 문자열.",
    "acquired_at": "기록된 세션 실행 점유 획득 시각.",
    "recovery_reason": "세션 실행 점유 복구 확인이 필요한 것으로 기록된 사유.",
    "invocation_id": "최초 실행·각 resume마다 생성되는 내부 실행 구간 UUID. 공개 Run ID와 다르다.",
    "attempt_count": "해당 invocation을 Worker가 점유한 횟수. 최초 시도도 포함한다.",
    "next_attempt_at": "해당 invocation을 다시 점유할 수 있는 다음 재시도 시각.",
    "cancel_reason": "해당 invocation에 기록된 취소 사유.",
    "failure": "해당 실행 구간에 기록된 실패 객체. 생산자가 정한 진단 형식이며 공개 Run 최종 결과가 아니다.",
    "started_at": "해당 invocation의 실제 실행 시작 기록 시각.",
    "items": "현재 페이지의 내부 실행 구간 목록. 총 개수나 공개 Run 목록이 아니다."
}

def field_description(key, path=(), parent=None):
    """Explain fields using the containing object, not only their spelling."""
    parent = parent or {}
    project_context = any(k in {'ProjectSummary', 'Page_ProjectSummary_', 'ProjectResource', 'ProjectCreate',
        'ProjectUpdate', 'project_list.json', 'project_detail.json', 'project_create.json', 'project_update.json',
        'docs/project-api.md'} for k in path)
    if project_context:
        descriptions = {
            'id': '프로젝트 UUID. 요청 경로에서는 project_id, 기존 공개 응답 필드는 id다.',
            'name': '프로젝트 표시 이름. 요청 body에서는 project_name을 사용한다.',
            'project_name': '변경/생성할 프로젝트 이름. 연속 공백을 정규화한다. 기본 프로젝트의 이름은 바꿀 수 없다.',
            'system_prompt': FIELDS['system_prompt'] + ' 수정에서 생략은 유지, 명시적 null은422, 빈 문자열은 초기화다.',
            'prompt_version': FIELDS['prompt_version'],
            'is_default': FIELDS['is_default'],
            'created_at': '프로젝트 최초 생성 시각.',
            'updated_at': '프로젝트 레코드 변경 시각. 하위 세션의 최근 대화 시각을 뜻하지 않는다.',
            'items': '현재 페이지의 프로젝트 요약 목록. system_prompt·prompt_version과 하위 세션/메시지는 포함하지 않는다.',
        }
        if key in descriptions: return descriptions[key]
    if '/api/v1/users' in path and parent.get('in') == 'query' and key == 'name':
        query = parent.get('name')
        descriptions = {'q': FIELDS['q'], 'role': '사용자 권한 admin/user 정확 일치 필터.',
            'status': '사용자 계정 상태 필터 active/deleted/all. 기본 active이며 Run 상태와 별개다.'}
        if query in descriptions: return '요청 query 이름. ' + descriptions[query]
    user_context = any(k in {'UserSummary', 'Page_UserSummary_', 'UserRead', 'UserMe', 'UserCreate', 'UserUpdate',
        'user_list.json', 'deleted_user.json'} for k in path)
    if user_context:
        descriptions = {
            'items': '현재 페이지의 사용자 요약 목록. 기본 프로젝트·로그인 세션 정보는 포함하지 않는다.',
            'user_id': '서비스 공개 문자열 사용자 ID. 내부 UUID가 아니며 SSO 사번과 연결된다.',
            'user_name': '사용자 표시 이름. 중복 가능하며 공개 ID와 별개다.',
            'role': '서비스 권한 admin 또는 user. 호출자 권한은 로그인한 사용자 DB 역할로 판단한다.',
            'is_active': FIELDS['is_active'],
            'default_project_id': '활성 기본 프로젝트 UUID. 삭제 등으로 없으면 null이며 조회에서 생성하지 않는다.',
            'delete_yn': 'N은 활성, Y는 soft delete. 관리자 상세는 삭제된 사용자도 읽으며 복구하지 않는다.',
            'created_at': '사용자 최초 등록 시각.',
            'updated_at': '사용자 레코드 최종 변경 시각. 로그인 세션 만료 시각과 별개다.',
            'deleted_at': '사용자의 soft delete 시각. 활성 사용자는 null이다.',
        }
        if key in descriptions: return descriptions[key]
    diagnostic_context = any(k in {'RunDiagnosticsResource','TaskDiagnostics','SessionWorkDiagnostics',
        'SessionExecutionDiagnostics','RunInvocationResource','Page_RunInvocationResource_',
        'run_diagnostics.json','run_invocations.json'} for k in path)
    if diagnostic_context:
        if key == 'status':
            if 'TaskDiagnostics' in path or 'task' in path:
                return '내부 Task 상태. waiting_input은 사용자 승인 또는 Executor 대기 모두 가능하므로 UI는 공개 Run status를 사용한다.'
            return '내부 실행 구간 상태 pending/running/interrupted/success/error/timeout/canceled. 공개 Run 전체 상태와 구분한다.'
        if key in DIAGNOSTIC_FIELDS: return DIAGNOSTIC_FIELDS[key]

    log_context = any('AgentRunLogResource' in k or k == 'run_logs.json' for k in path) or 'log_id' in parent
    if log_context:
        descriptions = {
            'items': '현재 페이지의 진단 로그 목록. 프론트 진행/HITL에는 SSE를 사용한다.',
            'log_id': '개별 저장 로그 UUID. SSE sequence나 resume_token과 무관하다.',
            'run_id': 'HITL 재개 전후에 유지되는 공개 Run ID.',
            'event_key': '내부 invocation 내 중복 저장 방지 키. 공개 Run 전체에서 유일하지 않을 수 있다.',
            'agent_name': '기록을 남긴 Agent 이름. 특정되지 않은 기록은 null이다.',
            'node': '기록을 남긴 그래프 노드 또는 실행 위치.',
            'event': '기록 생산자가 부여한 이벤트 이름.',
            'kind': '로그 분류 문자열. HITL 화면 kind와 별개다.',
            'payload': '생산자가 저장한 진단 객체. 종류별 형식이 다르며 SSE 응답으로 해석하지 않는다.',
            'created_at': 'DB 로그 저장 시각. 외부 작업의 실제 발생 시각이나 인과 순서를 보장하지 않는다.',
        }
        if key in descriptions: return descriptions[key]
    memory_context = any(k.startswith('Memory') for k in path)
    if memory_context and key == 'schema_version': return '프로젝트 메모리 단일 문서 형식 버전 2. 문서 변경 횟수인 version과 별개다.'
    if memory_context and key == 'project_id': return '이 메모리를 소유한 프로젝트 ID. 프로젝트당 문서는 하나이며 별도 메모리 ID가 없다.'
    if memory_context and key == 'content': return '프로젝트 공유 Markdown 문서 전체. 빈 문자열이면 초기화된 상태이며 콘텐츠 블록 배열이 아니다.'
    if memory_context and key == 'version': return '문서 변경·초기화 때 증가하는 버전. 개별 항목 버전이나 Run ID가 아니다.'
    if memory_context and key == 'expected_version': return '조회한 문서 버전. 첫 저장 전만 0이며 초기화 후에는 조회한 증가 버전을 그대로 보낸다.'

    schema_context = any(k in path for k in ('value_schema', 'output_schema', 'schema'))
    business_property = key in parent.get('properties', {})
    if key == 'input' and 'ValidationError' in path: return '유효성 검증에 실패한 원래 입력값. 실제 오류 응답·로그에는 민감 정보 노출 여부를 확인한다.'
    if key == 'kind' and 'AgentRunLogResource' in path: return 'Agent 로그의 분류 문자열. HITL 화면 kind와 별개의 로그 정보다.'
    if key == 'type' and parent.get('type') in ('text','image','file'): return '콘텐츠 블록 형식. text는 문자열 본문, image/file은 향후 첨부 참조이며 현재 text만 실제 접수된다.'
    if key == 'kind':
        actual = parent.get('kind')
        if 'InputView' in path or actual in ('parameter','data_reference'):
            return '입력 종류. parameter는 일반 값, data_reference는 서버가 접근을 확인한 데이터 참조다. 임의 파일 경로 입력과 구분한다.'
        if 'ParameterView' in path or actual in ('workflow_input','literal','step_reference','deferred','system_context'):
            return '함수 인자 값의 출처. workflow_input=입력 참조, literal=직접값, step_reference=이전 반환값, deferred=결과 이후 판단, system_context=서버 문맥이다.'
        if actual in ('plan_review','planning_question','decision_review','repair_review') or any(k.endswith('Data') for k in path):
            return 'HITL 화면 종류. plan_review=계획, planning_question=추가 질문, decision_review=결과 판단, repair_review=오류 수정 확인이다.'
        if actual in ('analysis_result','dataset','report') or 'OutputView' in path or 'output' in path:
            return '산출물 종류. analysis_result는 분석 반환값, dataset은 저장 데이터, report는 보고서다.'
    if key == 'name' and parent.get('source') == 'workflow_input': return '참조할 Workflow 입력 키. 예제 dataset은 inputs.dataset에서 정의한 입력이다.'
    if key == 'name' and 'workflow_id' in parent: return '사용자에게 표시할 Workflow 또는 계획 이름. 고유 식별자는 workflow_id/plan_id로 별도 관리한다.'
    if key == 'name' and 'ParameterView' in path: return '이 파라미터가 대응하는 Tool Python 함수의 인자 이름.'
    if key == 'name' and 'InputView' in path: return 'Workflow inputs의 입력 키. input_values의 동일 키로 최종값을 제출한다.'
    if key == 'id' and 'after_steps' in parent: return '이 결과 기반 Agent 판단의 고유 문자열 ID. agent_decision binding이 참조한다.'

    if path and path[-1] in ('$defs', 'schemas') and key in MODELS: return MODELS[key]
    if key == 'type' and business_property:
        if 'ValidationError' in path: return '유효성 검증 오류의 종류를 식별하는 문자열.'
        if any(k.endswith('Event') for k in path): return 'SSE 이벤트 이름. interaction.opened/updated/resolved 등 이벤트 처리 종류를 구분한다.'
        return '입력 콘텐츠 블록 종류 text/image/file. 현재 실제 접수는 text만 지원한다.'
    if key == 'required' and business_property: return '이 입력 또는 산출물이 업무 실행에 필요한지 표시하는 boolean. 필드 자체의 필수 여부와 구분한다.'
    if key == 'format' and business_property: return FIELDS['format']
    if key == 'items' and business_property: return '현재 페이지에 포함된 공개 Run 목록.'
    if 'paths' in path and not any(k in path for k in ('schema', 'properties', '$defs')):
        if key == 'content': return OPENAPI['content']
        if key == 'parameters': return OPENAPI['parameters']
        if key == 'summary': return 'Swagger에 표시하는 해당 HTTP operation 요약.'
        if key == 'tags': return 'Swagger에서 API operation을 묶는 분류 태그 목록.'
        if key == 'required': return 'HTTP 파라미터 또는 요청 body의 필수 여부. CSRF는 GET과 변경 요청의 실제 검증 정책을 함께 확인한다.'
    if key == 'type' and parent.get('type') in ('object','array','string','integer','number','boolean','null'): return '허용하는 JSON 데이터 타입. 이 Schema의 입력값 형식을 제한한다.'
    if key == 'items': return KEYWORDS['items']
    if key == 'title' and path == ('info',): return 'OpenAPI 문서의 서비스 표시 제목.'
    if key == 'default' and business_property: return 'Workflow 입력의 기본값. 해당 value_schema를 만족해야 하며 사용자 수정 정책을 함께 따른다.'

    if key == 'type':
        if 'paths' in path and 'securitySchemes' in path: return '인증 방식 타입. apiKey는 지정된 쿠키에서 인증값을 읽는 OpenAPI 표현이다.'
        if 'properties' in path or schema_context or ('$defs' in path and key not in parent.get('properties', {})):
            return '허용하는 데이터 타입. object/array/string/integer/number/boolean/null 등을 정의한다.'
        return '콘텐츠 블록에서는 text/image/file, SSE envelope에서는 이벤트 이름을 구분한다. 이 객체의 실제 값을 기준으로 해석한다.'
    if key == 'id':
        if any(k in path for k in ('steps', 'step')): return 'Workflow 내부 Step의 고유 문자열 ID. depends_on·step_output이 참조한다.'
        if any(k in path for k in ('decisions', 'decision')): return 'Workflow 내부 Agent 판단의 고유 문자열 ID. agent_decision이 참조한다.'
        if any(k in path for k in ('expected_outputs', 'output')): return 'Workflow 내부 예상 산출물의 고유 문자열 ID.'
        if 'workflow' in path: return 'legacy Workflow 또는 Tool의 정의 ID. DB에 저장된 Workflow resource UUID와 구분한다.'
        return '해당 정의 내부의 고유 ID. 공개 Run의 식별자는 별도 run_id 필드다.'
    if key == 'status':
        if 'PublicRunResource' in path or 'responses' in path or parent.get('type') == 'run.snapshot' or 'checkpoint_run_id' in parent:
            return 'Run 상태: pending/running/waiting_input/waiting_executor/success/error/timeout/canceled/recovery_required. 접수·승인과 실행 완료는 구분한다.'
        if 'StepView' in path or 'parameters' in parent: return '계획 Step 상태. planned는 포함, excluded는 사용자가 제외한 단계다.'
        if 'OutputView' in path: return '산출물 계획 상태. planned 또는 excluded_by_user로 구분한다.'
        if 'DecisionView' in path: return '계획 단계의 판단 상태. deferred는 실행 결과를 본 후 확정함을 뜻한다.'
        if 'interaction_id' in parent or any(k.endswith('Data') for k in path): return 'HITL 화면 상태. open은 응답 대기, resolved는 해당 확인 절차 종료다.'
    if key == 'required' and isinstance(parent.get(key), list): return KEYWORDS.get(key, '해당 객체에서 반드시 포함할 필드 이름 배열. 값이 null인지와 필드 생략 가능 여부는 별개다.')
    if key == 'name' and 'properties' in path: return FIELDS['name']
    if key == 'name' and parent.get('in') in ('path', 'query', 'header', 'cookie'):
        return '요청 파라미터 이름. ' + FIELDS.get(parent.get('name'), '인증 쿠키 또는 해당 API에서 선언한 파라미터 이름이다.')
    if key == 'limit' and 'paths' in path: return '목록 페이지 크기. 기본 50, 최대 200이며 계획 재작성 limit와 별개다.'
    if key == 'format' and (schema_context or 'properties' in path or '$defs' in path): return 'Schema 형식 제약. uuid/date-time 등의 문자열 표현을 설명한다.'
    if path and path[-1] in ('inputs', 'input_values') and key not in FIELDS:
        return f'Workflow 입력 이름 {key}. inputs는 정의, input_values는 해당 입력의 최종값이다.'
    if path and path[-1] == 'inputs' and isinstance(parent.get(key), dict):
        return f'Workflow 입력 이름 {key}에 대한 타입·필수 여부·편집·기본값 정의.'
    if path and path[-1] == 'input_values': return f'입력 이름 {key}에 사용자가 확정한 값. 해당 입력 Schema로 검증된다.'
    if path and path[-1] == 'values': return f'판단 ID {key}의 최종값. 현재 판단 화면의 value_schema를 만족해야 한다.'
    if path and path[-1] in ('arguments', 'argument_sources', 'parameter_controls'):
        return f'Tool 함수 인자 {key}의 ' + {'arguments':'값/출처 binding.','argument_sources':'legacy 값 출처.','parameter_controls':'사용자 편집 허용 및 값 Schema.'}[path[-1]]
    if path and path[-1] == 'input_schema': return f'legacy 입력 이름 {key}의 타입·필수 여부·추론 규칙.'
    if path and path[-1] == 'outputs' and not isinstance(parent.get(key), list): return f'legacy 출력 이름 {key}와 결과 참조/변수의 연결.'
    if key in FIELDS: return FIELDS[key]
    if key in KEYWORDS: return KEYWORDS[key]
    if key in OPENAPI: return OPENAPI[key]
    if key in MODELS: return MODELS[key]
    if key.startswith('/api/'): return '이 URL의 HTTP operation 정의. path placeholder는 실제 자원 ID로 치환한다.'
    if key.isdigit() and path and path[-1] == 'responses': return f'HTTP {key} 상태 코드 응답의 명세. 실제 SSE/redirect는 주 문서를 함께 확인한다.'
    if path and path[-1] == 'mapping': return f'선택 판별값 {key}에 대응하는 Schema 참조.'
    raise ValueError('Missing field explanation: ' + '/'.join((*path, key)))


def annotate_schema(node, path=()):
    """Only add/replace Schema descriptions; validation keywords stay unchanged."""
    if not isinstance(node, dict): return
    for key, value in node.get('properties', {}).items():
        if isinstance(value, dict):
            explanation = field_description(key, path, node)
            requirement = '필수 필드.' if key in node.get('required', []) else '생략 가능한 필드. 조건부 필수 여부는 업무 계약을 함께 확인한다.'
            value['description'] = explanation + ' ' + requirement
            annotate_schema(value, (*path, 'properties', key))
    for keyword in ('$defs', 'definitions'):
        for name, value in node.get(keyword, {}).items(): annotate_schema(value, (*path, keyword, name))
    for keyword in ('oneOf', 'anyOf', 'allOf'):
        for i, value in enumerate(node.get(keyword, [])): annotate_schema(value, (*path, keyword, str(i)))
    for keyword in ('items', 'additionalProperties', 'propertyNames', 'not'):
        if isinstance(node.get(keyword), dict): annotate_schema(node[keyword], (*path, keyword))


def render_jsonc(value, path=(), depth=0):
    indent = '  ' * depth
    if isinstance(value, dict):
        lines = ['{']
        for i, (key, child) in enumerate(value.items()):
            explanation = field_description(key, path, value)
            # For a schema property, include its contextual requirement annotation.
            if path and path[-1] == 'properties' and isinstance(child, dict): explanation = child.get('description', explanation)
            lines.append('  ' * (depth + 1) + '// ' + explanation)
            body = render_jsonc(child, (*path, key), depth + 1)
            lines.append('  ' * (depth + 1) + json.dumps(key, ensure_ascii=False) + ': ' + body + (',' if i < len(value)-1 else ''))
        lines.append(indent + '}'); return '\n'.join(lines)
    if isinstance(value, list):
        if not value: return '[]'
        lines = ['[']
        for i, child in enumerate(value):
            lines.append('  ' * (depth + 1) + render_jsonc(child, (*path, '[]'), depth + 1) + (',' if i < len(value)-1 else ''))
        lines.append(indent + ']'); return '\n'.join(lines)
    return json.dumps(value, ensure_ascii=False, allow_nan=False)


def strip_annotations(node, property_map=False):
    """Canonical validation rules, preserving a business field named description."""
    if isinstance(node, dict):
        return {k: strip_annotations(v, k in ('properties', '$defs', 'schemas'))
                for k, v in node.items() if property_map or k not in ('description', '$comment', 'title')}
    if isinstance(node, list): return [strip_annotations(v) for v in node]
    return node


def main():
    sources = sorted((ROOT/'docs/contracts').rglob('*.json'))
    workflow_schema = ROOT/'src/service_contracts/resources/workflow-definition.schema.json'
    schema_copy = ROOT/'docs/design/agentic-workflow-contract/workflow-definition.schema.json'
    workflow = json.loads(workflow_schema.read_text());original=strip_annotations(workflow)
    annotate_schema(workflow, ('WorkflowDefinition',))
    assert strip_annotations(workflow) == original
    for p in (schema_copy,):p.write_text(json.dumps(workflow, ensure_ascii=False, indent=2)+'\n')
    for p in sources:
        data = json.loads(p.read_text()); original = strip_annotations(data)
        if p.name == 'payload-schemas.json':
            for name, schema in data.items():annotate_schema(schema, (name,))
        elif p.name == 'openapi.snapshot.json':
            for name, schema in data['components']['schemas'].items():annotate_schema(schema, (name,))
            for url, methods in data['paths'].items():
                for method, operation in methods.items():
                    for parameter in operation.get('parameters', []):
                        if url == '/api/v1/users' and parameter['name'] in ('q', 'role', 'status'):
                            parameter['description'] = {
                                'q': FIELDS['q'],
                                'role': '사용자 권한 admin/user 정확 일치 필터. 생략하면 둘 다 조회한다.',
                                'status': '사용자 계정 상태 필터. active=활성(기본), deleted=soft delete만, all=전체. Run 상태와 별개다.',
                            }[parameter['name']]
                            continue
                        parameter['description'] = {
                            'limit':'목록 페이지 크기. 기본 50, 최대 200.',
                            'cursor':'다음 목록 페이지를 요청할 때 사용하는 토큰. SSE Last-Event-ID와 별개다.',
                            'target':'로그인 복귀 대상. app은 프론트, docs는 서비스 Swagger이며 기본 app이다.',
                        }.get(parameter['name']) or field_description(parameter['name'])
        else: continue
        assert strip_annotations(data) == original, p
        p.write_text(json.dumps(data, ensure_ascii=False, indent=2)+'\n')
    sources.append(schema_copy)
    total_fields = 0
    for p in sources:
        data = json.loads(p.read_text())
        if p.name in ('openapi.snapshot.json', 'payload-schemas.json', 'workflow-definition.schema.json'):path=()
        else:path=tuple(p.relative_to(ROOT).parts)
        rendered = '// 필드별 한국어 설명. 문서용 JSONC이며 API 제출에는 같은 이름의 .json을 사용한다.\n' + render_jsonc(data, path) + '\n'
        parsed = json.loads('\n'.join(line for line in rendered.splitlines() if not line.lstrip().startswith('//')))
        assert parsed == data, p
        total_fields += sum(line.lstrip().startswith('//') for line in rendered.splitlines()) - 1
        p.with_suffix('.jsonc').write_text(rendered)
    inline_blocks = 0
    for rel in ('docs/public-run-api.md','docs/workflow-json-reference.md','docs/project-api.md'):
        document = ROOT/rel
        def replace_block(match):
            nonlocal inline_blocks
            data = json.loads('\n'.join(line for line in match.group(1).splitlines() if not line.lstrip().startswith('//')))
            inline_blocks += 1
            rendered = render_jsonc(data, ('inline',rel))
            assert json.loads('\n'.join(line for line in rendered.splitlines() if not line.lstrip().startswith('//'))) == data
            return '```jsonc\n' + rendered + '\n```'
        document.write_text(re.sub(r'```jsonc?\n(.*?)\n```', replace_block, document.read_text(), flags=re.S))
    print(json.dumps({'annotated_files':len(sources),'commented_fields':total_fields,'inline_blocks':inline_blocks,'JSONC_equivalence':'pass','validation_rules':'unchanged'},ensure_ascii=False))

if __name__ == '__main__': main()
