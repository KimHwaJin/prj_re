"""Pure, deterministic repair boundaries. No model/network/analysis execution here.

AST checks validate structure/interface, not semantic equivalence or a Python sandbox.
Execution-local code can have side effects; a failed Step is not assumed atomic.
"""

from copy import deepcopy
from hashlib import sha256

from dtest.contracts.execution_repair import RepairResponse
from dtest.contracts.plan_review import canonical, require
from dtest.contracts.workflow_validation import validate, bindings
from dtest.contracts.tool_bindings import inherit_parameter_policy
from dtest.contracts.plan_projection import plan_view
from .compiler import verify_snapshot, ready_batch
from .sources import source_info

LEVELS = {
    0: "실패 결과 전달",
    1: "함수 원문을 유지한 실행 연결 보정",
    2: "목적·입출력 계약을 유지한 실패 Tool 구현 수정",
    3: "등록 Skill·Tool로 남은 계획 재작성 후 승인",
    4: "승인 목표 범위에서 실행별 코드·계획 자율 수정",
}


def seal(snapshot):
    snapshot["approval_sha256"] = sha256(
        canonical(
            {k: v for k, v in snapshot.items() if k != "approval_sha256"}
        ).encode()
    ).hexdigest()
    return snapshot


def effective_snapshot(state):
    original = state["approved_snapshot"]
    verify_snapshot(original)
    current = state.get("execution_snapshot") or original
    verify_snapshot(current)
    for key in (
        "run_id",
        "plan_id",
        "plan_revision",
        "input_values",
        "dataset_bindings",
        "context",
        "execution",
        "asset_revision",
        "excluded_step_ids",
        "user_actions",
    ):
        require(
            current.get(key) == original.get(key),
            "Repair changed immutable approved context or policy",
        )
    for key in (
        "schema_version",
        "workflow_id",
        "definition_version",
        "name",
        "description",
        "goal",
        "tags",
        "inputs",
    ):
        require(
            current["document"].get(key) == original["document"].get(key),
            "Repair changed the original analysis objective or inputs",
        )
    if state.get("execution_snapshot"):
        require(
            current["document"]["steps"] == current["steps"],
            "Repair plan Step projections differ",
        )
        accepted = [
            r
            for r in state.get("repair_history", [])
            if r["status"] == "accepted"
        ]
        require(
            bool(accepted)
            and accepted[-1]["execution_snapshot_sha256"]
            == current["approval_sha256"],
            "Repair snapshot has no accepted durable proposal",
        )
    return current


def proposal_snapshot(state, raw, catalog, *, level_limit):
    response = (
        raw
        if isinstance(raw, RepairResponse)
        else RepairResponse.model_validate(raw)
    )
    require(response.can_repair, "Agent could not propose a supported repair")
    require(
        catalog.revision == state["approved_snapshot"]["asset_revision"],
        "Deployed assets changed; repair cannot mix revisions",
    )
    original = effective_snapshot(state)
    require(original["execution"]["mode"] == "MULTI", "SINGLE cannot repair")
    frozen = set(state.get("completed_steps", [])) | set(
        state.get("skipped_steps", [])
    )
    failed = set(state.get("failed_step_ids", []))
    evidence = {
        o["step_id"]
        for o in state.get("observations", [])
        if o["status"] in {"SUCCEEDED", "FAILED"}
    }
    require(
        set(response.evidence_steps) <= evidence
        and bool(set(response.evidence_steps) & failed),
        "Repair must cite actual failed evidence",
    )
    require(
        "```" not in response.summary
        and "def " not in response.summary
        and "/workspace/" not in response.summary,
        "Repair summary must not expose Python source or runtime paths",
    )
    updated = deepcopy(original)
    document = updated["document"]
    before = {s["id"]: s for s in original["steps"]}
    excluded = set(original.get("excluded_step_ids", []))
    if excluded:
        document["decisions"] = [
            d
            for d in document["decisions"]
            if not set(d["after_steps"]) & excluded
        ]
        valid_decisions = {d["id"] for d in document["decisions"]}
        active_outputs = []
        for output in document["expected_outputs"]:
            refs = list(bindings([output["source"], output.get("when")]))
            if any(
                b["source"] == "step_output"
                and b["step_id"] in excluded
                or b["source"] == "agent_decision"
                and b["decision_id"] not in valid_decisions
                for b in refs
            ):
                continue
            if output["source"]["source"] == "agent_report":
                output["source"]["evidence_steps"] = [
                    s
                    for s in output["source"]["evidence_steps"]
                    if s not in excluded
                ]
            active_outputs.append(output)
        document["expected_outputs"] = active_outputs
    replan = (
        response.replacement_steps is not None
        or response.replacement_decisions is not None
    )
    if response.replacement_steps is not None:
        document["steps"] = deepcopy(response.replacement_steps)
    else:
        document["steps"] = deepcopy(original["steps"])
    if response.replacement_decisions is not None:
        document["decisions"] = deepcopy(response.replacement_decisions)
    if "ordered_call_ids" in document:
        document["ordered_call_ids"] = [s["id"] for s in document["steps"]]
    after = {s["id"]: s for s in document["steps"]}
    require(len(after) == len(document["steps"]), "Duplicate repaired Step ID")
    for key in frozen:
        require(
            key in after and after[key] == before[key],
            "Completed/skipped Steps are immutable",
        )
    # Evidence already consumed or ready must not be rewritten to change a past decision.
    resolved = state.get("execution_decisions", {})
    new_decisions = {d["id"]: d for d in document["decisions"]}
    for d in original["document"]["decisions"]:
        if d["id"] in resolved or set(d["after_steps"]) <= set(
            state.get("completed_steps", [])
        ):
            require(
                new_decisions.get(d["id"]) == d,
                "Resolved/evidence-ready decisions are immutable",
            )
    changed = set()
    semantic_confirmation = False
    seen = set()
    for patch in response.argument_changes:
        require(
            patch.step_id not in seen
            and patch.step_id in after
            and patch.step_id not in frozen,
            "Unknown, immutable or duplicate argument repair",
        )
        seen.add(patch.step_id)
        after[patch.step_id]["arguments"] = deepcopy(patch.arguments)
    metadata = deepcopy(catalog.metadata)
    # Preserve metadata for previous execution-local custom functions too.
    for step in original["steps"]:
        if step["tool_id"].startswith("custom."):
            pinned = original["tool_sources"][step["tool_id"]]
            source, info = source_info(
                pinned["code"],
                expected=pinned if pinned.get("origin_tool_id") else None,
            )
            if pinned.get("origin_tool_id"):
                info = inherit_parameter_policy(
                    info, catalog.metadata["tools"][pinned["origin_tool_id"]]
                )
            metadata["tools"][step["tool_id"]] = info
            metadata["skills"][step["skill_id"]]["tools"].append(
                step["tool_id"]
            )
    source_patches = {}
    source_effects = set()
    for patch in response.source_changes:
        require(
            patch.step_id in after
            and patch.step_id not in frozen
            and patch.step_id not in source_patches,
            "Unknown, immutable or duplicate source repair",
        )
        source_patches[patch.step_id] = patch.code
    level = 3 if replan else 1
    if source_patches:
        level = max(level, 2)
    sources = deepcopy(original["tool_sources"])
    skill_sources = deepcopy(original["skill_sources"])
    for step in document["steps"]:
        key, tool = step["id"], step["tool_id"]
        require(
            step["skill_id"] in metadata["skills"],
            "Repair Skill is not registered",
        )
        if key in frozen:
            continue
        if tool.startswith("custom."):
            existing = sources.get(tool)
            origin = (existing or {}).get("origin_tool_id")
            if not existing or not origin:
                if key not in before or key in source_patches:
                    level = 4
            if key in source_patches:
                require(
                    replan or key in failed,
                    "Tool implementation repair must target a failed Step",
                )
                source, info = source_info(
                    source_patches[key], expected=existing if origin else None
                )
                if origin:
                    source["origin_tool_id"] = origin
                    info = inherit_parameter_policy(
                        info, catalog.metadata["tools"][origin]
                    )
                if (
                    not existing
                    or source["code_sha256"] != existing["code_sha256"]
                ):
                    source_effects.add(key)
                sources[tool] = source
                metadata["tools"][tool] = info
            else:
                require(
                    tool in sources and tool in metadata["tools"],
                    "Custom Tool requires execution-local source",
                )
            metadata["skills"][step["skill_id"]]["tools"].append(tool)
        else:
            require(
                tool in catalog.sources
                and tool in metadata["skills"][step["skill_id"]]["tools"],
                "Repair Tool is not part of its registered Skill",
            )
            sources.setdefault(tool, deepcopy(catalog.sources[tool]))
            if key in source_patches:
                require(
                    replan or key in failed,
                    "Tool implementation repair must target a failed Step",
                )
                expected = sources[tool]
                source, info = source_info(
                    source_patches[key], expected=expected
                )
                if source["code_sha256"] != expected["code_sha256"]:
                    source_effects.add(key)
                    description = metadata["tools"][tool]["description"]
                    source["origin_tool_id"] = tool
                    info = inherit_parameter_policy(
                        info, metadata["tools"][tool]
                    )
                    tool = f"custom.repair_{sha256((key + source['code_sha256']).encode()).hexdigest()[:16]}"
                    step["tool_id"] = tool
                    sources[tool] = source
                    metadata["tools"][tool] = {
                        **info,
                        "description": description,
                    }
                    metadata["skills"][step["skill_id"]]["tools"].append(tool)
        if key in before:
            for name, binding in before[key]["arguments"].items():
                new_binding = step["arguments"].get(name)
                if (
                    new_binding != binding
                    and before[key]
                    .get("parameter_controls", {})
                    .get(name, {})
                    .get("editable")
                    is False
                ):
                    semantic_confirmation = True
                if (
                    not replan
                    and binding["source"] == "agent_decision"
                    and new_binding != binding
                ):
                    from jsonschema_rs import Draft202012Validator

                    decision = next(
                        d
                        for d in original["document"]["decisions"]
                        if d["id"] == binding["decision_id"]
                    )
                    require(
                        new_binding is not None
                        and new_binding.get("source") == "literal"
                        and Draft202012Validator(
                            decision["output_schema"]
                        ).is_valid(new_binding.get("value")),
                        (
                            "Repair cannot bypass an approved decision "
                            "value schema"
                        ),
                    )
                if (
                    binding["source"] == "system_context"
                    or binding["source"] == "workflow_input"
                    and binding["name"] in original.get("dataset_bindings", {})
                ):
                    require(
                        step["arguments"].get(name) == binding,
                        (
                            "Trusted dataset/system bindings cannot "
                            "change in a "
                            "repair"
                        ),
                    )
            if not replan:
                check = deepcopy(step)
                check["arguments"] = before[key]["arguments"]
                check["tool_id"] = before[key]["tool_id"]
                require(
                    check == before[key],
                    "Level 1/2 cannot change Step structure",
                )
        if key not in before or step != before[key] or key in source_effects:
            changed.add(key)
        skill_sources[step["skill_id"]] = deepcopy(
            catalog.skill_sources[step["skill_id"]]
        )
    # Detect source aliasing: exactly one execution-local Tool per patched Step.
    require(
        len(
            {
                s["tool_id"]
                for s in document["steps"]
                if s["tool_id"].startswith("custom.")
            }
        )
        == len(
            [
                s
                for s in document["steps"]
                if s["tool_id"].startswith("custom.")
            ]
        ),
        "Execution-local Tool IDs must be unique per Step",
    )
    require(
        changed or document["decisions"] != original["document"]["decisions"],
        "Repair must change code, bindings or remaining plan",
    )
    require(
        level <= level_limit,
        "Repair exceeds the configured service capability",
    )
    errors = validate(document, metadata)
    require(not errors, "; ".join(errors[:8]))
    # All paths resolve through the originally approved dataset map, not model-proposed input values.
    for step in document["steps"]:
        for b in bindings([step["arguments"], step.get("when")]):
            if b["source"] == "workflow_input":
                require(
                    b["name"] in original["input_values"]
                    or not document["inputs"][b["name"]]["required"],
                    "Required repaired input has no approved value",
                )
    # Reference policies survive registered Tool renaming and custom aliases.
    for step in document["steps"]:
        for name, policy in (
            metadata["tools"][step["tool_id"]]
            .get("parameter_bindings", {})
            .items()
        ):
            if (
                policy.get("input_kind") == "data_reference"
                and name in step["arguments"]
            ):
                binding = step["arguments"][name]
                name = binding["name"]
                absent_optional = (
                    name not in original["input_values"]
                    and not policy.get("required")
                    and not document["inputs"][name]["required"]
                )
                require(
                    absent_optional
                    or name in original.get("dataset_bindings", {}),
                    (
                        "Repaired data reference has no originally "
                        "approved dataset "
                        "binding"
                    ),
                )
    updated.update(
        steps=deepcopy(document["steps"]),
        tool_sources={
            s["tool_id"]: sources[s["tool_id"]] for s in document["steps"]
        },
        skill_sources=skill_sources,
    )
    seal(updated)
    proposed_batch, _ = ready_batch(
        updated,
        state.get("completed_steps", []),
        state.get("skipped_steps", []),
        state.get("execution_decisions", {}),
        {
            o["step_id"]: o["summary"]
            for o in state.get("observations", [])
            if o["status"] == "SUCCEEDED"
        },
    )
    require(
        proposed_batch,
        "Repair must produce a runnable correction Operation before Finalize",
    )
    view = plan_view(
        {
            "catalog": metadata,
            "document": document,
            "excluded_step_ids": [],
            "input_values": updated["input_values"],
            "input_origins": {k: "user" for k in updated["input_values"]},
            "editable_parameters": {},
            "plan_id": updated["plan_id"],
            "plan_revision": updated["plan_revision"],
            "policy": {
                "allowed_modes": ["MULTI"],
                "repair_level_limit": level_limit,
                "max_repair_attempts_limit": state["repair_max_attempts"],
            },
        }
    )
    authorized = state.get(
        "repair_authorized_level", original["execution"]["repair_level"]
    )
    result = {
        "snapshot": updated,
        "required_level": level,
        "summary": response.summary,
        "reason": response.reason,
        "attempt": state.get("repair_attempts", 0) + 1,
        "failed_step_ids": sorted(failed),
        "changed_step_ids": sorted(changed),
        "completed_step_ids": sorted(state.get("completed_steps", [])),
        "source_modified": any(
            s["tool_id"].startswith("custom.") for s in document["steps"]
        ),
        "workflow_eligible": not any(
            s["tool_id"].startswith("custom.") for s in document["steps"]
        ),
        "authorized_level": authorized,
        "requires_policy_escalation": level > authorized,
        "requires_approval": response.needs_user_input
        or semantic_confirmation
        or level > authorized
        or (level == 3 and authorized < 4),
        "steps": view["steps"],
        "base_snapshot_sha256": original["approval_sha256"],
    }
    result["proposal_sha256"] = sha256(canonical(result).encode()).hexdigest()
    return result


def verify_candidate(candidate, state):
    require(
        candidate["proposal_sha256"]
        == sha256(
            canonical(
                {k: v for k, v in candidate.items() if k != "proposal_sha256"}
            ).encode()
        ).hexdigest(),
        "Repair proposal changed",
    )
    require(
        candidate["base_snapshot_sha256"]
        == effective_snapshot(state)["approval_sha256"],
        "Repair proposal belongs to an older plan",
    )
    require(
        candidate["attempt"]
        == state.get("repair_attempts", 0) + 1
        <= state["repair_max_attempts"],
        "Repair budget exhausted or proposal is stale",
    )
    verify_snapshot(candidate["snapshot"])
