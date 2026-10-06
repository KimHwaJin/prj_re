"""Document budgets, role references, exact section patches and reset policies."""

import json
from dataclasses import replace
from types import SimpleNamespace
import httpx
import pytest
from langgraph.store.memory import InMemoryStore
from langchain_core.messages import HumanMessage
from dtest.settings.loader import load_settings, ConfigurationError
from dtest.contracts.project_memory import (
    MemoryLimits,
    MemoryPatch,
    MemoryConflict,
    markdown_parts,
    replace_section,
    section_body,
)
from dtest.agent_service.context import AgentContext
from dtest.agent_service.runtime.memory_selection import (
    select_memory,
    dumps,
    token_estimate,
)
from dtest.agent_service.runtime.project_memory import (
    validate_memory_proposals,
)
from dtest.agent_service.agents.analysis.agent_builders.conversation.agent import (
    build_agent,
    reply_schema,
)
from dtest.agent_service.agents.analysis.planning.catalog import AssetCatalog
from tests.agent_service.test_project_memory import (
    snapshot,
    provider,
    proposal,
    value,
    QUOTE,
)
from tests.agent_service.test_conversation_performance import model, response


@pytest.mark.parametrize(
    "field,value",
    [
        ("max_updates", 5),
        ("patch_max_chars", 0),
        ("max_chars", 1023),
        ("prompt_max_tokens", -1),
        ("prompt_max_chars", -1),
    ],
)
def test_invalid_limits_do_not_fall_back(field, value):
    with pytest.raises(ConfigurationError):
        load_settings(
            config={"AGENT_PROJECT_MEMORY_" + field.upper(): value}, environ={}
        )


def test_config_wins_and_limits_can_be_raised_or_disabled():
    settings = load_settings(
        config={
            "AGENT_PROJECT_MEMORY_MAX_CHARS": 30000,
            "AGENT_PROJECT_MEMORY_PATCH_MAX_CHARS": 6000,
            "AGENT_PROJECT_MEMORY_PROMPT_MAX_TOKENS": 0,
        },
        environ={"AGENT_PROJECT_MEMORY_PATCH_MAX_CHARS": "100"},
    )
    limits = MemoryLimits.from_settings(settings.agent)
    assert (
        limits.max_chars,
        limits.patch_max_chars,
        limits.prompt_max_tokens,
    ) == (30000, 6000, 0)
    with pytest.raises(ConfigurationError):
        load_settings(
            config={
                "AGENT_PROJECT_MEMORY_MAX_CHARS": 1024,
                "AGENT_PROJECT_MEMORY_PATCH_MAX_CHARS": 2000,
            },
            environ={},
        )


def test_role_filters_provenance_and_document_does_not_change():
    content = (
        "## 프로젝트 배경\n원인 분석\n\n## 분석 선호\n이상치부터 "
        "확인\n\n## 보고서 선호\n쉬운 보고서\n\n## 공유할 주요 "
        "발견\n사용자 공유 "
        "메모\n"
    )
    document = {
        **snapshot(content=content, version=1),
        "source": {"quote": "인용", "run_id": "private-source"},
    }
    report = select_memory(
        document,
        role="analysis_execution_report",
        request="",
        limits=MemoryLimits(),
    )
    repair = select_memory(
        document,
        role="analysis_execution_repair",
        request="",
        limits=MemoryLimits(),
    )
    assert set(report["selection"]["included_sections"]) == {
        "report_preferences",
        "background",
        "shared_findings",
    }
    assert set(repair["selection"]["included_sections"]) == {
        "background",
        "analysis_preferences",
    }
    assert (
        "private-source" not in dumps(report)
        and "source" not in report["memory"]
    )
    assert document["content"] == content


def test_complete_message_budget_and_relevance_and_no_partial_section_edits():
    limits = MemoryLimits(prompt_max_chars=2000, prompt_max_tokens=1900)
    content = (
        "## 프로젝트 배경\n"
        + "x" * 1000
        + "\n## 분석 선호\n이상치는 제거 전에 확인한다\n## 보고서 선호\n"
        + "x" * 1000
    )
    doc = snapshot(content=content)
    result = select_memory(
        doc,
        role="analysis_conversation",
        request="이상치 확인 방법",
        limits=limits,
        automatic_write=True,
    )
    assert result["selection"]["included_sections"] == ["analysis_preferences"]
    assert "background" not in result["write_policy"]["editable_sections"]
    assert (
        len(dumps(result)) <= limits.prompt_max_chars
        and token_estimate(dumps(result)) <= limits.prompt_max_tokens
    )
    assert result["selection"]["omitted_sections"] == 2
    assert (
        select_memory(
            doc,
            role="analysis_conversation",
            request="",
            limits=replace(limits, prompt_max_tokens=10),
        )
        is None
    )
    korean = select_memory(
        snapshot(content="보고서는 쉽게 작성한다"),
        role="analysis_conversation",
        request="",
        limits=MemoryLimits(),
    )
    assert token_estimate(dumps(korean)) > len(dumps(korean))


@pytest.mark.asyncio
async def test_zero_prompt_budget_does_not_read_or_auto_write():
    memory = provider()
    seen = []

    async def handle(request):
        body = json.loads(request.content)
        seen.append(body)
        return response({"role": "assistant", "content": json.dumps(value())})

    ctx = AgentContext(
        user_id="u",
        project_id="p",
        project_memory_policy=memory,
        project_memory_auto_write=True,
        project_memory_limits=MemoryLimits(prompt_max_tokens=0),
    )
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handle)
    ) as client:
        await build_agent(
            model(client), AssetCatalog(), store=InMemoryStore()
        ).ainvoke({"request": QUOTE}, context=ctx)
    assert memory.read.await_count == memory.apply.await_count == 0
    assert all(
        '"reference_type": "project_memory"' not in m["content"]
        for m in seen[0]["messages"]
    )


@pytest.mark.parametrize(
    "current,quote,content,valid",
    [
        (
            "앞으로 보고서는 비전문가 대상으로 쉽게 작성해줘",
            "앞으로 보고서는 비전문가 대상으로 쉽게 작성해줘",
            "보고서는 비전문가를 대상으로 쉽게 작성한다",
            True,
        ),
        (
            "이번 보고서는 짧게 작성해줘",
            "짧게 작성해줘",
            "보고서는 짧게 작성한다",
            False,
        ),
        (
            "이번 분석에서는 이상치를 빼줘",
            "이번 분석에서는 이상치를 빼줘",
            "이상치는 제거한다",
            False,
        ),
        (
            "데이터를 확인해줘",
            "데이터를 확인해줘",
            "데이터 확인을 선호한다",
            False,
        ),
        (
            "앞으로 보고서는 쉽게 써줘",
            "이전 대화에서 한 말",
            "보고서는 쉽게 작성한다",
            False,
        ),
        (
            "앞으로 보고서는 평균 109를 기억해줘",
            "앞으로 보고서는 평균 109를 기억해줘",
            "평균을 기억한다",
            False,
        ),
        (
            "이번 보고서는 짧게. 앞으로 보고서는 원인 중심으로 써줘",
            "앞으로 보고서는 원인 중심으로 써줘",
            "보고서는 원인 중심으로 작성한다",
            True,
        ),
    ],
)
def test_current_quote_supported_normalization_and_session_only_rejection(
    current, quote, content, valid
):
    ctx = AgentContext(
        user_id="u",
        project_id="p",
        project_memory_policy=provider(),
        project_memory_auto_write=True,
    )
    document = snapshot()
    request = SimpleNamespace(
        runtime=SimpleNamespace(context=ctx),
        state={
            "project_memory_snapshot": document,
            "project_memory_reference": select_memory(
                document,
                role="analysis_conversation",
                request=current,
                limits=ctx.project_memory_limits,
                automatic_write=True,
            ),
        },
        messages=[HumanMessage(content=json.dumps({"request": current}))],
    )
    reply = reply_schema(AssetCatalog(), 5)(
        **value([proposal(content=content, quote=quote)])
    )
    if valid:
        validate_memory_proposals(reply, request)
    else:
        with pytest.raises(ValueError):
            validate_memory_proposals(reply, request)


def test_configured_output_schema_limits_and_no_old_topic_key():
    limits = MemoryLimits(max_updates=2, patch_max_chars=2000)
    schema = reply_schema(AssetCatalog(), 5, memory_limits=limits)
    assert (
        len(
            schema(
                **value(
                    [
                        proposal(section=s, content="x" * 1500)
                        for s in ["background", "report_preferences"]
                    ]
                )
            ).memory_updates
        )
        == 2
    )
    with pytest.raises(ValueError):
        schema(
            **value(
                [
                    proposal(section=s)
                    for s in [
                        "background",
                        "analysis_preferences",
                        "report_preferences",
                    ]
                ]
            )
        )
    with pytest.raises(ValueError):
        schema(**value([proposal(key="old_topic")]))
    with pytest.raises(ValueError):
        reply_schema(
            AssetCatalog(), 5, memory_limits=MemoryLimits(patch_max_chars=10)
        )(**value([proposal()]))


def test_shipped_yaml_with_commented_agent_examples_loads_default_limits():
    from pathlib import Path

    settings = load_settings(
        environ={},
        config_path=Path(__file__).resolve().parents[2] / "config.example.yml",
    )
    assert MemoryLimits.from_settings(settings.agent) == MemoryLimits()


def test_partial_replacement_preserves_other_sections_and_unknown_markdown():
    content = (
        "공통 설명\n\n## 프로젝트 배경\n배경 유지\n\n## 보고서 선호\n기존 "
        "선호\n\n## 별도 메모\n자유 형식 "
        "유지\n"
    )
    patch = MemoryPatch(
        section="report_preferences",
        old_text="기존 선호",
        content="새 선호",
        expected_version=7,
    )
    updated = replace_section(content, patch)
    assert updated == (
        "공통 설명\n\n## 프로젝트 배경\n배경 유지\n\n## 보고서 "
        "선호\n새 선호\n\n## 별도 메모\n자유 형식 "
        "유지\n"
    )
    with pytest.raises(MemoryConflict):
        replace_section(
            content, patch.model_copy(update={"old_text": "잘못된 원문"})
        )


@pytest.mark.parametrize("fence", ["```", "~~~~"])
def test_fenced_headings_are_not_section_boundaries(fence):
    content = (
        "## 보고서 선호\n설명\n"
        + fence
        + "python\n## 프로젝트 배경\n"
        + fence
        + "\n"
    )
    assert len(markdown_parts(content)) == 1
    assert section_body(content, "background") == ""
    updated = replace_section(
        content,
        MemoryPatch(
            section="background",
            old_text="",
            content="새 배경",
            expected_version=0,
        ),
    )
    assert updated.startswith(content)
    assert section_body(updated, "background") == "새 배경"


def test_duplicate_headings_readable_but_not_automatically_editable():
    content = "## 보고서 선호\none\n## 보고서 선호\ntwo\n"
    reference = select_memory(
        snapshot(content=content),
        role="analysis_conversation",
        request="",
        limits=MemoryLimits(),
        automatic_write=True,
    )
    assert (
        "one" in reference["memory"]["content"]
        and "two" in reference["memory"]["content"]
    )
    assert (
        "report_preferences"
        not in reference["write_policy"]["editable_sections"]
    )
    with pytest.raises(MemoryConflict):
        section_body(content, "report_preferences")


def test_patch_cannot_inject_a_new_section_and_heading_at_eof():
    with pytest.raises(MemoryConflict):
        replace_section(
            "",
            MemoryPatch(
                section="background",
                old_text="",
                content="## 다른 제목\n탈출",
                expected_version=0,
            ),
        )
    updated = replace_section(
        "## 프로젝트 배경",
        MemoryPatch(
            section="background",
            old_text="",
            content="배경",
            expected_version=0,
        ),
    )
    assert updated == "## 프로젝트 배경\n배경\n"


@pytest.mark.parametrize("field", ["MAX_TOPICS", "TOPIC_MAX_CHARS"])
@pytest.mark.parametrize("source", ["config", "env"])
def test_removed_topic_settings_are_explicit_errors(field, source):
    values = {"AGENT_PROJECT_MEMORY_" + field: 64}
    with pytest.raises(
        ConfigurationError, match="Removed topic memory setting"
    ):
        load_settings(
            config=values if source == "config" else {},
            environ=values if source == "env" else {},
        )


def test_unclosed_code_fences_cannot_swallow_other_sections():
    patch = MemoryPatch(
        section="background",
        old_text="",
        content="```python\nunfinished",
        expected_version=0,
    )
    with pytest.raises(MemoryConflict, match="close all"):
        replace_section("", patch)
    document = "자유 메모\n```\n코드 블록이 닫히지 않음"
    reference = select_memory(
        snapshot(content=document),
        role="analysis_conversation",
        request="",
        limits=MemoryLimits(),
        automatic_write=True,
    )
    assert reference["write_policy"]["editable_sections"] == []
    with pytest.raises(MemoryConflict, match="unfinished"):
        replace_section(document, patch.model_copy(update={"content": "배경"}))
