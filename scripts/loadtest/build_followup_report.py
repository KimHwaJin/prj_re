#!/usr/bin/env python3
"""Create the follow-up HTML report's canonical manifest from reviewed suite evidence."""

import csv
import json
from pathlib import Path
from followup_evidence import build

OUT = Path("docs/reports/service-followup-2026-09-28")


def fmt(value, digits=2):
    return "완료 표본 없음" if value is None else f"{value:,.{digits}f}"


def main():
    data, sources, facts = build(output=OUT)
    mixed = next(
        r for r in data["http_summary"] if r["scenario"] == "crud_mixed"
    )
    agent = data["agent_flow"]
    peak = next(r for r in agent if r["users"] == 100)
    http_peak = next(
        r
        for r in data["http_summary"]
        if r["scenario"] == "approval" and r["users"] == 100
    )
    queue = next((r for r in data["run_queue"] if r["users"] == 100), None)
    old = facts["baseline"]["stages"][-1]
    mm = facts["mixed_metadata"]
    am = facts["agent_metadata"]
    assert mm["final_locust"] == "stopped" and am["final_locust"] == "stopped"
    assert (
        mm["before_database"]["agent_runs"]
        == mm["after_database"]["agent_runs"]
    )
    assert (
        mm["before_mock_executor"]["unique_submissions"]
        == am["after_mock_executor"]["unique_submissions"]
    )
    mixed_p95 = max(r["p95_ms"] for r in data["mixed_endpoints"])
    count_j = sum(r["journeys"] for r in agent)
    errors_j = sum(r["failures"] for r in agent)
    count_http = sum(
        r["requests"]
        for r in data["http_summary"]
        if r["scenario"] == "approval"
    )
    errors_http = sum(
        r["failures"]
        for r in data["http_summary"]
        if r["scenario"] == "approval"
    )
    resources = data["resources"]

    def resource(scenario, service):
        return next(
            r
            for r in resources
            if r["scenario"] == scenario
            and r["users"] == 100
            and r["service"] == service
        )

    mapi = resource("crud_mixed", "API")
    aapi = resource("approval", "API")
    aq = next(
        r
        for r in data["queues"]
        if r["scenario"] == "approval" and r["users"] == 100
    )
    mixed_minutes = data["mixed_minutes"]
    first_minutes = mixed_minutes[:3]
    last_minutes = mixed_minutes[-3:]
    first_rps = sum(r["requests"] for r in first_minutes) / sum(
        r["seconds"] for r in first_minutes
    )
    last_rps = sum(r["requests"] for r in last_minutes) / sum(
        r["seconds"] for r in last_minutes
    )
    all_nodes = (
        am["after_database"]["agent_runs"]
        - am["before_database"]["agent_runs"]
    )
    node_status = {
        k: am["after_database"]["run_status_counts"].get(k, 0)
        - am["before_database"]["run_status_counts"].get(k, 0)
        for k in set(am["after_database"]["run_status_counts"])
        | set(am["before_database"]["run_status_counts"])
    }
    data["resources_100"] = [r for r in resources if r["users"] == 100]
    data["node_100"] = [r for r in data["agent_nodes"] if r["users"] == 100]
    # These are display filters of the same material queries, with original SQL preserved.
    for alias, original in [
        ("resources_100", "resources"),
        ("node_100", "agent_nodes"),
    ]:
        src = next(s for s in sources if s["id"] == original + "_source")
        sources.append({**src, "id": alias + "_source"})
    audit = {
        "datasets": data,
        "facts": facts,
        "run_status_delta": node_status,
        "runtime_settings": json.loads(
            Path("var/loadtest/followup-environment-20260928.json").read_text()
        ),
        "worker_implementation": (
            "src/app/agent_run_worker.py: run_forever awaits "
            "execute_claimed before "
            "claim_one"
        ),
    }
    (OUT / "audit.json").write_text(
        json.dumps(audit, ensure_ascii=False, indent=2)
    )
    sources.append(
        {
            "id": "audit",
            "label": "추가 부하시험 통합 검산",
            "path": str(OUT / "audit.json"),
        }
    )
    for name, rows in data.items():
        if not rows:
            continue
        with (OUT / (name + ".csv")).open("w") as f:
            w = csv.DictWriter(f, fieldnames=list(rows[0]))
            w.writeheader()
            w.writerows(rows)
    blocks = []
    charts = []
    tables = []

    def md(id, body):
        blocks.append(
            {"id": id, "type": "markdown", "body": body, "sourceId": "audit"}
        )

    def chart(id, title, dataset, x, y, label, kind="bar", color=None):
        enc = {
            "x": {
                "field": x,
                "type": "quantitative" if x == "minute" else "nominal",
                "label": "측정 경과 분" if x == "minute" else "구간",
            },
            "y": {"field": y, "type": "quantitative", "label": label},
        }
        if color:
            enc["color"] = {"field": color, "type": "nominal"}
        charts.append(
            {
                "id": id,
                "title": title,
                "type": kind,
                "dataset": dataset,
                "sourceId": dataset + "_source",
                "layout": "full",
                "valueFormat": "number",
                "encodings": enc,
            }
        )
        blocks.append({"id": id + "_block", "type": "chart", "chartId": id})

    def table(id, title, dataset, columns):
        first = columns[0][0]
        tables.append(
            {
                "id": id,
                "title": title,
                "dataset": dataset,
                "sourceId": dataset + "_source",
                "density": "spacious",
                "defaultSort": {"field": first, "direction": "asc"},
                "columns": [
                    {"field": f, "label": label, "type": "text"}
                    if f
                    in ("scenario", "service", "endpoint", "name", "action")
                    else {"field": f, "label": label, "format": "number"}
                    for f, label in columns
                ],
            }
        )
        blocks.append({"id": id + "_block", "type": "table", "tableId": id})

    title = "Follow-up Load Tests: CRUD & Agent"
    md("title", "# " + title)
    md(
        "summary",
        f"""## Executive Summary

- **100명 CRUD 혼합 시험을 15분 유지했습니다.** 측정 HTTP {mixed["requests"]:,}건, 실패 {mixed["failures"]:,}건, 처리량 {fmt(mixed["rps"])}건/초였습니다. API별 p95 중 최댓값은 {mixed_p95:g}ms였습니다.
- **에이전트는 실제 호출과 resume으로 Workflow 승인 대기까지 시험했습니다.** 1~100명 측정 구간의 여정 {count_j:,}회 중 실패 {errors_j:,}회, HTTP {count_http:,}건 중 실패 {errors_http:,}건입니다. 100명에서 완료 처리량은 {fmt(peak["successful_per_sec"])}회/초, 여정 p95는 {fmt(peak["p95_sec"])}초였습니다.
- **전체 시험은 무오류가 아닙니다.** 안정화·종료 대기까지 포함하면 성공 2,737회와 120초 타임아웃 1회입니다. 100명 구간에 시작한 Run 하나가 정체되어 종료 대기 중 실패로 기록됐고, 증거 보존 후 취소했습니다. 서버가 계속 heartbeat를 갱신하므로 이 정체를 lease 만료만으로 감지하기 어렵습니다. 이 문제의 원인 규명이 증설보다 우선입니다.
- **Executor 제출은 실행하지 않았습니다.** Mock Executor 제출 수는 {am["after_mock_executor"]["unique_submissions"]}건으로 그대로입니다. 에이전트 처리량은 CRUD HTTP RPS와 다른 단위이므로 두 지표를 분리해 해석해야 합니다.""",
    )
    md(
        "scope",
        f"""## 두 시험의 조건과 종료 지점

2026년 9월 28일 같은 로컬 Docker 환경에서 CRUD 혼합 → 승인 대기 시험을 순차 실행했습니다. 기존 프로젝트·세션 생성 전용 100명 결과는 {old["rps"]:.2f}건/초였으며, 이번에는 조회·수정·삭제를 추가했습니다. 동작 구성이 다르므로 수치 차이를 코드 성능 개선·회귀로 단정하지 않습니다.

**CRUD 혼합:** 100명 도달 후 30초 안정화, 900초 측정. 생성/조회/수정/삭제 목표 비율 20/50/20/10%, 프로젝트와 세션을 무작위 선택합니다. 사용자별 0.5~1.5초 대기가 있습니다. 각 사용자가 새로 만든 자원만 변경하고 기본 프로젝트 및 다른 사용자의 자원은 삭제하지 않습니다. 삭제 가능한 자원이 없으면 생성으로 대체하므로 실제 비율은 목표와 약간 다를 수 있습니다.

**승인 대기:** 1→5→10→25→50→75→100명, 각 단계 15초 안정화 후 60초 측정. 새 세션에서 최초 Run과 3번 resume을 실행하여 `workflow_approval` interrupt를 확인하고 다음 세션을 만듭니다. 승인 응답은 보내지 않습니다. 여정 사이 추가 대기는 없고, Run 상태를 0.25초 간격으로 조회합니다.

API 4개 프로세스, Run Worker 4개, LLM Mock 지연 0ms입니다. HTTP p95는 API 응답 지연이고, 여정 p95는 세션 생성부터 모든 HITL 단계를 거쳐 승인 대기에 도달하기까지의 시간입니다. DB·checkpoint·Worker·HITL·Workflow 저장은 실제 경로입니다.""",
    )
    md(
        "mixed_throughput",
        f"""## CRUD 혼합 처리량을 시간에 따라 확인했습니다

15분 평균 처리량은 {fmt(mixed["rps"])}건/초였습니다. 처음 3분은 {fmt(first_rps)}건/초, 마지막 3분은 {fmt(last_rps)}건/초였습니다. 아래 선은 누적 RPS가 아니라 **각 약 1분 구간의 요청 수를 구간 시간으로 나눈 값**입니다. 응답이 끝나면 평균 약 1초 쉬므로 약 100건/초 부근의 결과는 사용자 행동으로도 제한됩니다.

오류는 HTTP와 시나리오를 별도로 확인했습니다. 장시간 처리량에 추세가 있는지 보되, 이번 15분 관측으로 더 긴 시간의 안정성을 보장하지는 않습니다.""",
    )
    chart(
        "mixed_rps",
        "CRUD 혼합 분 단위 처리량",
        "mixed_minutes",
        "minute",
        "rps",
        "HTTP 요청/초",
        "line",
    )
    md(
        "mixed_endpoint_text",
        f"""## 조회·수정·삭제를 포함한 API별 지연

API별 p95의 최댓값은 {mixed_p95:g}ms, 전체 HTTP 최대 응답은 {fmt(mixed["max_ms"], 0)}ms였습니다. 아래 표는 각 API의 호출 수·실패·p95·p99를 보여줍니다. 최대값은 단일 느린 요청의 영향을 받으므로 p95와 함께 봅니다. CRUD HTTP 요청과 동일 동작을 기록한 FLOW 통계를 합산하지 않았습니다.""",
    )
    table(
        "mixed_endpoint_table",
        "CRUD 혼합 API별 결과",
        "mixed_endpoints",
        [
            ("endpoint", "API"),
            ("requests", "요청 수"),
            ("failures", "실패"),
            ("avg_ms", "평균 ms"),
            ("p95_ms", "p95 ms"),
            ("p99_ms", "p99 ms"),
            ("max_ms", "최대 ms"),
        ],
    )
    md(
        "mixed_mix_text",
        (
            "실제 실행 비율은 아래와 같습니다. 성공적으로 기록된 CRUD "
            "동작 기준이며, 삭제 대상이 없을 때 생성으로 대체하는 동작도 "
            "실제 비율에 반영했습니다."
        ),
    )
    table(
        "mix_table",
        "CRUD 실제 동작 비율",
        "mixed_mix",
        [("action", "동작"), ("requests", "횟수"), ("actual_pct", "비율 %")],
    )
    md(
        "memory_text",
        f"""## 메모리는 지속 증가 여부를 관찰했습니다

혼합 시험 측정 구간에서 API 메모리 표본은 {fmt(mapi["memory_min"], 1)}~{fmt(mapi["memory_max"], 1)}MiB였습니다. 아래 값은 각 분의 컨테이너 메모리 평균입니다. PostgreSQL 메모리에는 연결과 버퍼·캐시의 영향이 포함될 수 있어 증가만으로 누수라고 판단하지 않습니다.

시험 직후 에이전트 시험이 이어졌으므로 긴 무부하 회복 구간은 따로 측정하지 않았습니다. 누수 판정에는 더 긴 유지 시험과 부하 종료 후 회수 관측이 필요합니다.""",
    )
    chart(
        "mixed_memory_chart",
        "CRUD 혼합 컨테이너 메모리",
        "mixed_memory",
        "minute",
        "memory_mib",
        "MiB",
        "line",
        "service",
    )
    md(
        "agent_throughput_text",
        f"""## 에이전트는 완료 여정 수로 처리량을 봐야 합니다

100명 구간에서 승인 대기까지 성공한 여정은 초당 {fmt(peak["successful_per_sec"])}회였습니다. 같은 구간의 HTTP 처리량은 {fmt(http_peak["rps"])}건/초지만 여기에는 Run 상태 polling이 포함됩니다. **HTTP 요청을 많이 처리하는 것과 사용자 분석 여정을 많이 끝내는 것은 다릅니다.**

아래 막대는 해당 측정 구간에서 완료된 여정 수 기준입니다. 단계 전환 시 이미 진행 중인 여정이 다음 구간에서 끝날 수 있으므로 각 단계를 완전히 독립된 실험으로 보지는 않습니다.""",
    )
    md(
        "agent_degradation",
        (
            "25명에서 7.80회/초였던 완료 처리량이 50명 6.70회/초, 75명 "
            "5.32회/초, 100명 3.43회/초로 떨어졌습니다. 100명 구간에는 "
            "정체 Run 하나가 Worker 한 개를 점유한 영향도 포함됩니다. "
            "따라서 100명 결과를 정상 Worker 4개의 순수 용량 한계로 "
            "단정할 수 없습니다. 100명 HTTP 요청 중 상태 polling은 "
            "18,357/19,414건(94.6%)이므로 polling 비용도 대조 시험 "
            "대상으로 삼아야 "
            "합니다."
        ),
    )
    chart(
        "agent_throughput_chart",
        "승인 대기 여정 완료 처리량",
        "agent_flow",
        "user_label",
        "successful_per_sec",
        "성공 여정/초",
    )
    md(
        "agent_latency_text",
        f"""## 100명 구간의 승인 대기 여정 p95는 {fmt(peak["p95_sec"])}초였습니다

아래 지연은 HTTP POST 응답 시간보다 넓은 범위로, Run 큐 대기·Graph 실행·checkpoint 저장·HITL 재개·클라이언트 polling을 포함합니다. LLM 응답은 0ms Mock이므로 실제 모델 서비스 지연을 더하면 사용자 체감 시간은 달라집니다.

시나리오 실패 {errors_j}회와 HTTP 실패 {errors_http}회를 별도로 집계했습니다. 실패가 있다면 짧게 끝난 실패가 지연 분포에 섞일 수 있으므로 오류율과 함께 읽어야 합니다.""",
    )
    md(
        "censored_timeout",
        (
            "**이 지연 표에는 종료 대기 중 발생한 120초 타임아웃 1회가 "
            "포함되지 않습니다.** 해당 Run은 100명 측정 도중 시작했지만 "
            "60초 측정 창이 끝난 뒤 실패가 확정됐습니다. 따라서 100명 p95 "
            "28초는 그 구간에서 완료된 여정의 분포이며, 미완료·장기 정체 "
            "위험까지 대표하지 "
            "않습니다."
        ),
    )
    chart(
        "agent_latency_chart",
        "승인 대기 여정 p95",
        "agent_flow",
        "user_label",
        "p95_sec",
        "초",
    )
    md(
        "agent_detail_text",
        (
            "단계별 완료 수와 꼬리 지연은 아래와 같습니다. p95·p99는 "
            "Locust의 해당 구간 히스토그램 근사값이며, 백분위수를 단계 "
            "사이에 평균하지 "
            "않았습니다."
        ),
    )
    table(
        "agent_table",
        "에이전트 단계별 결과",
        "agent_flow",
        [
            ("users", "사용자"),
            ("journeys", "여정 수"),
            ("failures", "실패"),
            ("successful_per_sec", "성공/초"),
            ("avg_sec", "평균 초"),
            ("p95_sec", "p95 초"),
            ("p99_sec", "p99 초"),
        ],
    )
    if queue:
        md(
            "queue_text",
            f"""## 실행 전 큐 대기와 실제 Run 실행 시간을 분리했습니다

100명 구간에서 기록된 성공 여정의 Run 기준, 큐 대기 평균은 {fmt(queue["queue_avg_sec"])}초, p95는 {fmt(queue["queue_p95_sec"])}초였습니다. 실행 시작부터 마지막 Run 갱신까지의 평균은 {fmt(queue["execution_avg_sec"])}초였습니다. 이 두 시간 합계 중 큐 대기 비중은 {fmt(queue["queue_share_pct"], 1)}%입니다.

현재 Worker 구현은 프로세스마다 Run 하나를 끝낸 뒤 다음 Run을 가져옵니다. API 4개 프로세스이므로 Run을 수행하는 소비 루프도 4개입니다. 이 구조에서 동시 여정 수가 늘면 HTTP API가 빨리 응답하더라도 큐 대기가 늘 수 있습니다. 개별 코드의 CPU·DB 비용을 확정하려면 profiling이 추가로 필요합니다.

큐 지표는 저장된 성공 여정의 Run timestamp에서 계산했고, Run 완료 시각으로 각 측정 구간에 배정했습니다. 실패·미완료 Run은 이 지연 표본에 포함되지 않는다는 한계가 있습니다.""",
        )
        table(
            "queue_table",
            "Run 큐 및 실행 지연",
            "run_queue",
            [
                ("users", "사용자"),
                ("runs", "Run 표본"),
                ("queue_avg_sec", "큐 평균 초"),
                ("queue_p95_sec", "큐 p95 초"),
                ("execution_avg_sec", "실행 평균 초"),
                ("queue_share_pct", "큐 비중 %"),
                ("max_attempts", "최대 시도"),
            ],
        )
    md(
        "node_text",
        (
            "100명 구간의 각 HITL 도달 시간을 비교합니다. 각 RUN 지연에는 "
            "그 단계의 큐 대기와 처리, 다음 polling까지의 관측 지연이 "
            "포함되므로 순수 노드 실행 시간으로 읽으면 안 "
            "됩니다."
        ),
    )
    table(
        "node_table",
        "100명 단계별 HITL 도달 지연",
        "node_100",
        [
            ("name", "도달 지점"),
            ("runs", "Run 수"),
            ("failures", "실패"),
            ("avg_sec", "평균 초"),
            ("p95_sec", "p95 초"),
            ("p99_sec", "p99 초"),
        ],
    )
    md(
        "agent_http_text",
        (
            "아래는 같은 100명 구간에서 세션 생성, Run 접수/resume, Run "
            "상태 조회 API 자체의 응답 시간입니다. Run 접수 응답은 "
            "Graph가 끝났다는 뜻이 아니므로 앞의 여정 지연과 "
            "구분합니다."
        ),
    )
    table(
        "agent_http_table",
        "100명 에이전트 HTTP API 응답",
        "agent_http_100",
        [
            ("endpoint", "API"),
            ("requests", "요청 수"),
            ("failures", "실패"),
            ("avg_ms", "평균 ms"),
            ("p95_ms", "p95 ms"),
            ("p99_ms", "p99 ms"),
        ],
    )
    md(
        "resource_text",
        f"""## API·DB 자원과 큐 적체를 함께 봅니다

100명 승인 대기 구간에서 API CPU 평균은 {fmt(aapi["cpu_avg"], 1)}%, 표본 최댓값은 {fmt(aapi["cpu_max"], 1)}%였습니다. Docker CPU 100%는 논리 코어 하나이며, VM은 14코어를 공유합니다. 에이전트 Run pending 표본 최댓값은 {aq["pending_max"]}개, running 최댓값은 {aq["running_max"]}개였습니다.

CRUD DB 연결 표본 최댓값은 {aq["connections_max"]}개, checkpoint DB 연결 최댓값은 {aq["agent_connections_max"]}개였습니다. 두 값은 각각의 표본 최대이므로 같은 순간 합계로 단정할 수 없습니다. 모니터는 수초 간격이어서 매우 짧은 잠금·CPU 스파이크는 놓칠 수 있습니다. 다른 로컬 컨테이너의 자원 사용도 결과에 영향을 줄 수 있습니다.""",
    )
    table(
        "resources_table",
        "100명 구간 자원 비교",
        "resources_100",
        [
            ("scenario", "시험"),
            ("service", "서비스"),
            ("cpu_avg", "CPU 평균 %"),
            ("cpu_max", "CPU 최대 %"),
            ("memory_min", "메모리 최소 MiB"),
            ("memory_max", "메모리 최대 MiB"),
            ("samples", "표본 수"),
        ],
    )
    md(
        "validation",
        f"""## 완료 여부와 Executor 미호출을 대조했습니다

승인 대기 시험 전체(안정화·전환·종료 대기 포함)에 저장된 성공 여정은 {facts["successful_journeys_all"]:,}회, 실패 여정은 {facts["failed_journeys_all"]:,}회입니다. DB Agent Run 증가량은 {all_nodes:,}개이며 상태별 증가량은 `{json.dumps(node_status, ensure_ascii=False)}`입니다. 성공 여정마다 같은 Task에 속하는 Run 4개와 최종 승인 interrupt를 확인했습니다.

두 시험 동안 Mock Executor 고유 제출 수는 {am["after_mock_executor"]["unique_submissions"]}건으로 변하지 않았습니다. 현재 API 목적지도 Mock 서버이며 실제 Executor 요청은 보내지 않았습니다. 시험 종료 후 Locust는 stopped / 사용자 0명입니다. 위 DB 상태 증가는 정체 Run 취소 전 스냅샷입니다. 취소 후 최종 검증에서는 pending/running 모두 0개, canceled 1개이며 재시도는 0개입니다. 테스트 생성 이력·soft delete 행·checkpoint는 후속 분석을 위해 보존했습니다.""",
    )
    md(
        "stalled_run",
        """## 우선 조사할 결함: Graph가 정체돼도 heartbeat는 계속됩니다

실패한 최초 Run은 `a8a46edb-9c11-428c-be8d-2fedbcb76d23`, 세션은 `2e27d5d1-3dc7-413d-a28b-4cb967bde4b8`입니다. UTC 03:06:21 접수, 03:06:27 실행 시작 후 마지막 이벤트는 `agent_run started`였고, 03:08:21 클라이언트의 120초 제한을 넘겼습니다. 마지막 이벤트에 `user_request`가 정상 포함됐으며 API 로그의 `user_request is required`는 0건입니다. 기존 오류가 재현된 것으로 볼 증거는 없습니다.

타임아웃 뒤에도 Task heartbeat와 lease가 갱신되고, Run은 running으로 남았습니다. 진단 시점에 비유휴 DB 세션과 잠금 대기는 관측되지 않았고 오류 로그도 없었습니다. **Graph 내부의 어떤 await가 멈췄는지는 현재 증거로 확정할 수 없습니다.** `RunService._run_cancellable`은 Graph 완료와 취소만 기다리며 실행 상한이 없고, heartbeat는 별도 루프로 계속 갱신됩니다. 따라서 실행이 진전되지 않아도 lease가 살아 있는 상태가 가능합니다. 이 코드는 관측 현상을 설명하지만 최초 정체의 원인을 증명하지는 않습니다.

증거를 저장한 뒤 이 시험 Run만 정상 cancel API로 취소했습니다. HTTP 202와 최종 canceled를 확인했으며 잔여 running/pending은 0개입니다. 별도로 부하 제어 스크립트의 `/stop` 요청은 15초 read timeout을 넘겼습니다. 측정 7단계 파일은 이미 저장됐으며 실제 Locust 중지와 종료 스냅샷을 후속 확인했습니다. 재실행 시에는 graceful drain을 기다리도록 stop 요청 제한을 200초로 수정했습니다.""",
    )
    md(
        "next_steps",
        """## 다음 개선은 관측된 구간부터 검증합니다

1. **정체 Run:** 노드 시작/종료·checkpoint 저장·외부 await의 trace를 남기고, 전체 Run 실행 상한 및 진행 없는 실행의 감지·취소·상태 확정을 검토합니다. 동일 100명 시험을 반복해 재현해야 합니다.
2. **에이전트 처리량:** 정체 문제를 분리한 뒤 Worker 수와 polling 간격을 각각 하나씩 바꾸어 대조합니다. API CPU 경쟁을 줄이기 위한 Worker 프로세스 분리도 후보이며, 현재 결과만으로 증설 효과를 확정하지 않습니다.
3. **DB·checkpoint:** 느린 Run의 query/trace를 수집합니다. CRUD DB와 checkpoint DB 연결 수를 함께 보고 PostgreSQL 전체 연결 제한 안에서 조정합니다.
4. **CRUD·실제 모델 조건:** 실제 사용자 동작 비율과 SLO를 합의하고 큰 목록·공유 자원 경합을 검증합니다. Mock LLM에 현실적인 지연을 넣어 재시험합니다. 이번 결과에는 실제 LLM·Executor 처리 시간이 없습니다.""",
    )
    md(
        "limits",
        """## 남은 질문과 해석 한계

- 실제 사용자별 요청 대기·CRUD 비율과 허용 p95·p99·오류율은 아직 정해지지 않았습니다.
- CRUD는 15분 유지했지만 승인 대기는 각 단계 60초 측정입니다. 두 시험의 안정성 입증 범위는 다릅니다.
- 순차 시험이므로 뒤 시험은 DB 데이터·연결 풀·캐시가 더 쌓인 환경에서 실행됐습니다. 독립 반복 시험 또는 역순 대조가 필요합니다.
- 승인 대기 시험은 SSE 대신 polling을 사용합니다. 실제 프런트엔드의 SSE 연결 유지 비용은 포함하지 않습니다.
- UUID Bearer 인증 경로만 사용하며 외부 인증 공급자는 호출하지 않습니다.
- 이전 `user_request is required` 오류의 재현 여부는 이번 로컬·단일 설정 조건에 한정됩니다. 재현되지 않았더라도 다른 환경의 원인이 해결됐다는 뜻은 아닙니다.
- 오류가 0이어도 향후 무오류나 최대 수용량을 보장하지 않습니다. CPU·연결·메모리는 표본 관측이며, 장기 누수 및 장애 복구는 별도 검증 범위입니다.""",
    )
    artifact = {
        "surface": "report",
        "manifest": {
            "version": 1,
            "surface": "report",
            "title": title,
            "generatedAt": am["finished_at"],
            "description": (
                "100명 CRUD 혼합 15분 유지 + 에이전트 승인 대기 1~100명 "
                "단계 시험"
            ),
            "blocks": blocks,
            "charts": charts,
            "tables": tables,
            "sources": sources,
        },
        "snapshot": {
            "version": 1,
            "generatedAt": am["finished_at"],
            "status": "ready",
            "datasets": data,
        },
        "sources": sources,
    }
    (OUT / "artifact.json").write_text(
        json.dumps(artifact, ensure_ascii=False, indent=2)
    )
    (OUT / "source-notes.json").write_text(
        json.dumps(
            {
                "audience": "product stakeholders",
                "delivery": "portable HTML",
                "baseline_preservation": (
                    "Prior CRUD report retained unchanged"
                ),
                "chart_map": [
                    "mixed rps: minute time series, 15 points",
                    "mixed memory: minute time series by service",
                    "agent throughput: discrete load-level comparison",
                    "agent p95: discrete load-level latency comparison",
                ],
                "cohorts": (
                    "Stage stats exclude warmup. Journey log includes "
                    "warmup and drain; Run delays are assigned by Run "
                    "updated_at within stage "
                    "windows."
                ),
                "reproduction": (
                    "scripts/loadtest/followup_suite.py; "
                    "scripts/loadtest/followup_evidence.py; "
                    "scripts/loadtest/build_followup_report.py"
                ),
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    print(
        json.dumps(
            {
                "mixed": mixed,
                "agent_100": peak,
                "agent_http_100": http_peak,
                "queue_100": queue,
                "run_status_delta": node_status,
                "all_successful_journeys": facts["successful_journeys_all"],
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
