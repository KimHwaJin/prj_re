"""Canonical portable report payload for the validated observation Worker A/B."""

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path


def build(folder):
    data = json.loads((folder / "summary.json").read_text())
    validation = json.loads((folder / "validation.json").read_text())
    decision = json.loads((folder / "decision.json").read_text())
    pairs = {(r["profile"], int(r["users"]), r["variant"]): r for r in data}

    def pair(profile, n=50):
        return pairs[profile, n, "baseline"], pairs[profile, n, "candidate"]

    def change(before, after):
        return (1 - after / before) * 100

    datasets = {}
    blocks = []
    charts = []
    tables = []

    def md(key, title, body, source=True):
        block = {"id": key, "type": "markdown", "body": title + "\n\n" + body}
        if source:
            block["sourceId"] = "measurement"
        blocks.append(block)

    def table(key, title, rows, columns, sort="users"):
        datasets[key] = rows
        tables.append(
            {
                "id": key,
                "title": title,
                "dataset": key,
                "sourceId": "measurement",
                "defaultSort": {"field": sort, "direction": "asc"},
                "columns": [
                    {"field": f, "label": label, "type": kind}
                    for f, label, kind in columns
                ],
            }
        )
        blocks.append({"id": key + "-block", "type": "table", "tableId": key})

    def comparative(profile):
        rows = []
        for n in (1, 10, 30, 50):
            b, a = pair(profile, n)
            rows.append(
                {
                    "users": n,
                    "cohort": str(n) + "명",
                    "before": b["mean_seconds"],
                    "after": a["mean_seconds"],
                    "reduction_percent": change(
                        b["mean_seconds"], a["mean_seconds"]
                    ),
                    "repeats": int(b["repeats"]),
                    "before_p95_range": f"{b['p95_min']:.2f}~{b['p95_max']:.2f}",
                    "after_p95_range": f"{a['p95_min']:.2f}~{a['p95_max']:.2f}",
                    "before_batch": b["makespan_seconds"],
                    "after_batch": a["makespan_seconds"],
                    "before_rate": b["batch_users_per_second"],
                    "after_rate": a["batch_users_per_second"],
                }
            )
        return rows

    def chart(key, title, rows):
        datasets[key] = [
            {**row, "seconds": row[field], "condition": label}
            for row in rows
            for field, label in [
                ("before", "기존 누적 write"),
                ("after", "증가분 write 후보"),
            ]
        ]
        charts.append(
            {
                "id": key,
                "title": title,
                "subtitle": "LLM mock 지연 0초·Worker 20·50명 각 3회, 나머지 각 1회",
                "showDescription": True,
                "type": "bar",
                "intent": "comparison",
                "dataset": key,
                "sourceId": "measurement",
                "encodings": {
                    "x": {
                        "field": "cohort",
                        "type": "nominal",
                        "label": "동시 사용자",
                    },
                    "y": {
                        "field": "seconds",
                        "type": "quantitative",
                        "label": "사용자 평균 초",
                        "format": "number",
                        "unit": "s",
                    },
                    "color": {
                        "field": "condition",
                        "type": "nominal",
                        "label": "저장 방식",
                    },
                },
                "palette": {"kind": "categorical"},
                "labels": {"values": "all"},
                "legend": {"position": "bottom", "sort": "spec"},
                "settings": {"groupMode": "grouped", "sort": "none"},
                "layout": "full",
            }
        )
        blocks.append({"id": key + "-block", "type": "chart", "chartId": key})

    normal_b, normal_a = pair("standard")
    large_b, large_a = pair("large20")
    title = "증가분 결과 저장의 Worker 부하 비교"
    md("title", "# " + title, "", False)
    md(
        "summary",
        "## 저장량 감소와 사용자 완료 시간의 효과를 나누어 판단했다",
        f"**50명 대형 결과 흐름의 저장량은 사용자당 {large_b['logical_mib_per_user']:.3f}→{large_a['logical_mib_per_user']:.3f}MiB, {change(large_b['logical_mib_per_user'], large_a['logical_mib_per_user']):.1f}% 감소**였다. 사용자 평균 완료 시간은 {large_b['mean_seconds']:.2f}→{large_a['mean_seconds']:.2f}초다. 기본 흐름은 {normal_b['mean_seconds']:.2f}→{normal_a['mean_seconds']:.2f}초였다. 두 50명 조건은 각각 3회다.\n\n"
        f"**총 {validation['attempts']}번 중 {validation['trials']}개 trial·{validation['user_scenarios']}개 사용자 시나리오를 완주했다. 후보 50명·대형 결과 1회는 중단되어 별도 보존했다. 아래 시간은 완료한 trial의 조건부 비교이며 실패를 포함한 전체 처리량이 아니다.** 모델 추론 지연을 제거한 로컬 서비스 비교다. 슬롯/풀을 늘리지 않았으며 beta DeltaChannel은 비교 대상에 넣지 않았다. "
        + decision["summary"],
    )
    md(
        "definition",
        "## 같은 자원에서 프로젝트 생성부터 리포트 완료까지 측정했다",
        "전체 시간은 프로젝트/세션 생성→새 요청→계획 편집→승인→HTTP Executor 제출/continue/finalize→Redis 결과→Worker 재개→최종 success SSE다. 인증 등록·warmup·기동/종료·SQL 증거 캡처는 제외했다. 리포트는 Agent Markdown 응답이며 별도 Artifact POST 등록은 포함하지 않는다.\n\n"
        "**LLM mock 지연은 모든 호출에서 0초**다. create_agent·스킬 조회·middleware·프롬프트 직렬화는 실제 수행한다. 분석 실행은 HTTP fixture의 합성 결과이며 Python Tool 코드를 실행하지 않는다. 실제 PostgreSQL checkpoint/Store·API 권한/CRUD·Redis Streams/Inbox·Worker·manifest/checksum·SSE를 거친다.\n\n"
        "API 1프로세스·공통 Worker20·CRUD10/overflow0·checkpoint4·bridge4·Store2를 맞췄다. SSE0.5초·claim/cancel0.25초·Ingress0.2/idle2초·notify ON도 같다. 1/10/30/50명 유한 동시 유입으로, 지속 유입 용량이나 HPA 확장을 측정하지 않았다.",
    )
    columns = [
        ("users", "동시 사용자", "number"),
        ("before", "기존 평균 초", "number"),
        ("after", "후보 평균 초", "number"),
        ("reduction_percent", "평균 단축 %", "number"),
        ("repeats", "각 반복 수", "number"),
        ("before_batch", "기존 전체 종료 초", "number"),
        ("after_batch", "후보 전체 종료 초", "number"),
        ("before_rate", "기존 batch 사용자/초", "number"),
        ("after_rate", "후보 batch 사용자/초", "number"),
        ("before_p95_range", "기존 trial p95 범위", "text"),
        ("after_p95_range", "후보 trial p95 범위", "text"),
    ]
    md(
        "normal",
        "## 기본 흐름의 비용은 새 저장 방식과 별도로 남는다",
        f"등록 Tool4개를 2개 Operation으로 실행하고 계획 선택/작성·review·report의 4개 모델 호출을 수행했다. 50명 평균은 {normal_b['mean_seconds']:.2f}→{normal_a['mean_seconds']:.2f}초, trial 평균 범위는 {normal_b['mean_min']:.2f}~{normal_b['mean_max']:.2f}/{normal_a['mean_min']:.2f}~{normal_a['mean_max']:.2f}초다. 아래 막대는 사용자 평균, 표의 처리율은 각 trial의 사용자 수/전체 종료 시간이다. 반복 p95 평균을 합친 모집단 p95로 부르지 않는다.",
    )
    chart(
        "normal-chart",
        "기본 분석의 동시 사용자별 완료 시간",
        comparative("standard"),
    )
    table(
        "normal-table",
        "기본 분석의 시간 대조",
        comparative("standard"),
        columns,
    )
    md(
        "large",
        "## 대형 결과와 20번의 재개를 같은 승인 계획으로 실행했다",
        f"기존 등록 load/profile Tool을 20개 단계로 구성하고 every_n_tools=1을 사용했다. 각 Tool stdout은 64KiB 반복 문자 로그이며 저장 미리보기는16000자다. review20회·계획2회·report1회, 총23개 모델 호출의 지연은 모두0초다. 50명 평균은 {large_b['mean_seconds']:.2f}→{large_a['mean_seconds']:.2f}초, trial 평균 범위는 {large_b['mean_min']:.2f}~{large_b['mean_max']:.2f}/{large_a['mean_min']:.2f}~{large_a['mean_max']:.2f}초다. **기본 흐름보다 호출/재개 수가 많으므로 두 시나리오의 절대 시간을 성능 순위로 섞지 않는다.**",
    )
    chart(
        "large-chart",
        "대형 결과 분석의 동시 사용자별 완료 시간",
        comparative("large20"),
    )
    table(
        "large-table",
        "대형 결과 분석의 시간 대조",
        comparative("large20"),
        columns,
    )
    cost = []
    for profile, label in [
        ("standard", "기본4 Tool/2 Operation"),
        ("large20", "대형20 Tool/20 Operation"),
    ]:
        for variant, condition in [
            ("baseline", "기존"),
            ("candidate", "후보"),
        ]:
            r = pairs[profile, 50, variant]
            cost.append(
                {
                    "scenario": label,
                    "condition": condition,
                    "users": 50,
                    "logical_mib": r["logical_mib_per_user"],
                    "column_mib": r["column_payload_mib_per_user"],
                    "observation_write_mib": r[
                        "observation_write_mib_per_user"
                    ],
                    "write_ms": r["saver_write_ms_per_user"],
                    "read_ms": r["saver_read_ms_per_user"],
                    "cpu_seconds": r["cpu_per_user_seconds"],
                    "crud_sql": r["crud_sql_per_user"],
                    "user_queue_ms": r["user_queue_mean_ms"],
                    "event_queue_ms": r["event_queue_mean_ms"],
                    "cp": r["checkpoints_per_user"],
                }
            )
    md(
        "storage",
        "## 증가분 write는 full snapshot과 재개 횟수를 줄이지 않는다",
        f"대형 흐름의 observations pending write는 사용자당 {large_b['observation_write_mib_per_user']:.3f}→{large_a['observation_write_mib_per_user']:.3f}MiB다. 전체 목록 snapshot blob과 checkpoint 횟수는 유지한다. 원본 승인/HTTP body/receipt/실패 근거를 삭제하지 않았다. 저장 함수 시간은 동시 호출끼리 겹치는 누계여서 E2E 시간에서 그대로 뺄 수 없다.\n\n"
        "논리 바이트는 JSON text+고유 blob+retained write다. 반복 문자 fixture는 PostgreSQL에서 잘 압축되므로 압축 column 크기도 별도로 본다. **column 크기 합은 heap/index/WAL/TOAST 전체 디스크 크기가 아니다.** 실제 자연어/숫자 로그의 압축률과 클라우드 DB I/O는 달라질 수 있다. 읽기 전체 decode·모델 프레임워크·API SQL·상태 폴링 비용도 남는다. CRUD SQL 차이는 동일 쿼리의 폴링/진행시간 변동이며 이번 작업으로 쿼리를 최적화한 성과가 아니다.",
    )
    table(
        "cost-table",
        "50명 조건의 사용자당 비용과 명령 대기",
        cost,
        [
            ("scenario", "시나리오", "text"),
            ("condition", "방식", "text"),
            ("logical_mib", "논리 MiB", "number"),
            ("column_mib", "압축 column MiB", "number"),
            ("observation_write_mib", "관찰 write MiB", "number"),
            ("write_ms", "저장 함수 누계 ms", "number"),
            ("read_ms", "읽기 함수 누계 ms", "number"),
            ("cpu_seconds", "API CPU 초", "number"),
            ("crud_sql", "CRUD SQL 회", "number"),
            ("user_queue_ms", "사용자 명령 대기 ms", "number"),
            ("event_queue_ms", "XADD→handler 시작 ms", "number"),
            ("cp", "checkpoint 수", "number"),
        ],
        sort="scenario",
    )
    md(
        "validation",
        "## 전체 표본·소스·완료 근거를 독립 검산했다",
        f"완료한 {validation['trials']}회는 성공 결과의 순서·summary·상태 hash가 두 방식/사용자 수에서 같다. 각 trial 종료 시 session owner/recovery/Inbox/Outbox/CRUD checkout과 실행 자리는0이다. 실제 src 해시를 기준 Git 정본과 대조했고 변경된 runtime은 상태/reducer·결과 writer·receive reset뿐이다.\n\n"
        "측정 순서를 교차하고 동시에 두 서버를 부하하지 않았다. 각 trial은 새 DB/namespace에서 실행한 후 정리한다. 50명은각3회, 나머지는각1회다. 독립 검사기는 원문 SHA·사용자 평균/p95·SQL 합·저장 bytes·호출 수·최종 근거·반복 모집단을 다시 계산한다.\n\n"
        "잘못된 이벤트 이력 fixture 계약과 URL prefix를 사용한 첫 비교 세트는 전부 제외했다. 실제 items/next_cursor/has_more 계약 및 명시적 API base/path 설정을 양쪽에 동일 적용한 뒤 전 조건을 새로 수행했다. 제외 원본/중단 로그는 보관했다. 이후 올바른 계약의 후보 50명·대형 결과 1회는 ExecutorOutcomeUnknown 이후 20개 명령 RECOVERY·43개 READY 상태로 멈췄다. 원래 예외 사슬이 보존되지 않아 최초 원인은 미확정이다. 이 중단은 별도 실패 사건이며 재시험 성공으로 삭제하지 않았다. 성공 시간 그래프는 실패의 손실을 포함하지 못한다.",
    )
    md(
        "limitations",
        "## 로컬 동시 유입 결과를 운영 용량으로 확대 해석하지 않는다",
        "LLM 추론·실 Executor 계산·Jupyter/PVC/MinIO·CPU quota/HPA/멀티 Pod·지속 유입·1주 대기는 제외했다. API CPU만 측정하고 DB/Redis/mock/client CPU는 포함하지 않았다. 함수/SQL/queue/slot 시간은 서로 겹친다. 반복3회와 기존 서비스가 함께 떠 있는 로컬 자원만으로 통계적 우월성이나 SLA를 확정하지 않는다.\n\n"
        "SSE 폴링0.5초와 작업 scheduling 편차도 사용자 완료 시각에 영향을 준다. 신규 tagged pending write를 구버전 LastValue worker가 이어받는 역방향 혼합 배포는 여전히 지원을 보장하지 않는다. 현재 기본 root URL와 Event history 상대 경로의 prefix 정합성은 운영 설정/연계 후속 사항으로 별도 기록했다.",
    )
    md(
        "decision",
        "## 확인한 효과에 맞춰 후보 채택 범위를 제한한다",
        decision["detail"],
    )
    md(
        "questions",
        "## 배포 전에 확인할 조건이 남아 있다",
        "먼저 중단을 일으킨 최초 예외와 POST 결과를 어떻게 확보할 것인가? 실제 로그의 크기/압축률과 Operation 수 분포는 어떤가? Pod CPU quota·총 DB 연결 예산에서 같은 결과가 유지되는가? 진행 중 대기 Run을 새 worker로 넘길 때 혼합 버전 경계를 어떻게 보장할 것인가? 기본 Executor URL와 이력 경로를 같은 규칙으로 조립하는 후속 변경도 실제 gap 대조가 필요하다.",
    )
    generated = datetime.now(timezone.utc).isoformat()
    source = {
        "id": "measurement",
        "kind": "file",
        "name": "Validated local Worker trial rollup",
        "path": "docs/reports/observation-worker-2026-10-04/summary.json",
        "query": {
            "engine": "SQLite",
            "language": "sql",
            "sql": (folder / "aggregation.sql").read_text(),
            "tables_used": ["trials"],
            "description": "Executed rollup of validated HTTP/Worker captures; checkpoint bytes originate in captured PostgreSQL checkpoint tables",
            "filters": [
                "one process,common slots20,PG checkpoint4,CRUD10",
                "0s model transport fixture; actual framework/middleware",
                "1/10/30/50 finite burst,50 repeats3",
                "initial mismatched-history matrix wholly excluded",
            ],
            "metric_definitions": [
                "mean_seconds: arithmetic mean of user project-create to success-SSE wall time",
                "batch_users_per_second: n/cohort makespan per trial, then arithmetic mean across repeats; not sustained capacity",
                "logical bytes: selected checkpoint JSON text/metadata+unique blobs+retained writes per user",
                "column payload: selected pg_column_size sum; excludes total heap/index/WAL",
                "saver times: overlapping per-user call work, not a wall-time component",
            ],
        },
    }
    artifact = {
        "surface": "report",
        "manifest": {
            "version": 1,
            "surface": "report",
            "title": title,
            "generatedAt": generated,
            "blocks": blocks,
            "charts": charts,
            "tables": tables,
            "sources": [source],
        },
        "snapshot": {
            "version": 1,
            "status": "ready",
            "generatedAt": generated,
            "datasets": datasets,
        },
        "sources": [source],
    }
    (folder / "artifact.json").write_text(
        json.dumps(artifact, ensure_ascii=False, indent=2) + "\n"
    )
    notes = {
        "audience": "technical",
        "delivery": "portable canonical HTML, local Codex runtime",
        "required_structure_map": {
            "title": "title",
            "technical_summary": "summary",
            "key_findings": "normal/large/storage",
            "definitions": "definition before quantitative charts",
            "methodology": "definition/validation",
            "limitations": "limitations and adjacent notes",
            "next_steps": "decision",
            "further_questions": "questions",
        },
        "chart_contracts": [
            {
                "id": c["id"],
                "family": "comparison",
                "type": "grouped bar",
                "grain": "cohort/variant",
                "categories": 4,
                "rows": 8,
                "unit": "seconds",
                "zero_baseline": True,
                "legend": "existing cumulative vs incremental candidate",
                "fallback": "exact paired table",
                "qa": "packaged portable reader receipt",
            }
            for c in charts
        ],
        "omitted_charts": "Cost metrics in different units stay in an exact lookup table; no time trend fabricated from four cohort levels",
        "scope": "technical Worker performance adoption decision; no services reconfigured; conditional completed-trial curves include an explicit unresolved failed-attempt caveat",
    }
    (folder / "source-notes.json").write_text(
        json.dumps(notes, ensure_ascii=False, indent=2) + "\n"
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("folder", type=Path)
    build(parser.parse_args().folder)
