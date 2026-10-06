"""Shared graph invocation for initial, user-resume and Executor delivery.

Delivery protocols own input identity and receipts. This boundary owns context,
model validation, Executor submission effects and streamed service projection.
It accepts plain values, never an open caller database session.
"""

from __future__ import annotations

from typing import Any, Callable, Mapping
from uuid import UUID

from api_service.runs.persistence import graph as projection
from api_service.runs.project_context import read_project_snapshot
from integrations.executor.client import current_submission_effects, submission_scope
from service_runtime.diagnostics import span
from service_runtime.model_selection import validate_checkpoint_selection


_UNSET = object()


class GraphInvocation:
    def __init__(self, graph: Any, *, session_factory=None, dispatcher=None,
                 project_context_loader=None, model_validator=validate_checkpoint_selection):
        self.graph = graph
        self.session_factory = session_factory
        self.dispatcher = dispatcher
        self.project_context_loader = project_context_loader
        self.model_validator = model_validator

    def validate_model(self, values: Mapping[str, Any], expected=_UNSET):
        if self.model_validator is not None:
            if expected is _UNSET:
                self.model_validator(values)
            else:
                self.model_validator(values, expected)

    async def project_snapshot(self, *, user_id, session_id, project_id=None):
        return await read_project_snapshot(
            user_id=user_id, session_id=session_id, project_id=project_id,
            session_factory=self.session_factory,
        )

    async def ensure_project_context(self, values, config, *, allow_default=False):
        if not values or "project_system_prompt" in values:
            return
        if self.project_context_loader is not None:
            update = await self.project_context_loader(values)
        elif allow_default and values.get("user_id") and values.get("session_id"):
            update = await self.project_snapshot(
                user_id=values["user_id"], session_id=values["session_id"],
                project_id=values.get("project_id"),
            )
        else:
            return  # Standalone legacy graphs may have no API project identity.
        await self.graph.aupdate_state(config, update)

    async def invoke(self, value, config, *, values=None, user_id=None,
                     agent_run_id=None, trigger_message_id=None,
                     durability="sync", accept_state: Callable | None = None):
        """Run to the next wait/end; projection owns a short UoW per state.

        Initial input can echo an older invocation's state. Its receipt gate
        prevents previous events being projected into the new invocation.
        Nested execution must share its cancellation owner's POST tracker.
        """
        # Covers user starts/resumes AND Executor-event/recovery invocations.
        # Copy rather than modifying a reusable checkpoint/event config.
        from service_settings import get_settings
        config = {**config, "recursion_limit": get_settings().agent.recursion_limit}
        if values is not None:
            self.validate_model(values)
            await self.ensure_project_context(values, config)
        with submission_scope(current_submission_effects()):
            with span("graph.invoke"):
                if getattr(self.graph, "name", None) != "agentic-planning-v1":
                    return await self.graph.ainvoke(value, config=config, durability=durability)
                cursor = projection.InvocationProjection()
                async for state in self.graph.astream(value, config=config, stream_mode="values", durability=durability):
                    if accept_state is not None and not accept_state(state):
                        continue
                    await cursor.persist(
                        state, user_id=UUID(str(user_id or state["user_id"])),
                        agent_run_id=agent_run_id or state["agent_run_id"],
                        trigger_message_id=trigger_message_id,
                        session_factory=self.session_factory, dispatcher=self.dispatcher,
                    )

    async def project(self, state, *, user_id, agent_run_id=None, trigger_message_id=None):
        return await projection.persist_graph_state(
            state, user_id=user_id, agent_run_id=agent_run_id,
            trigger_message_id=trigger_message_id,
            session_factory=self.session_factory, dispatcher=self.dispatcher,
        )

    async def user_turn(self, config, graph_input, **kwargs):
        from api_service.runs.protocols.initial import start_and_project
        return await start_and_project(
            self.graph, config, graph_input, invocation=self,
            session_factory=self.session_factory, dispatcher=self.dispatcher, **kwargs,
        )

    async def user_resume(self, config, **kwargs):
        from api_service.runs.protocols.user_resume import resume_and_project
        return await resume_and_project(
            self.graph, config, invocation=self,
            session_factory=self.session_factory, dispatcher=self.dispatcher, **kwargs,
        )

    async def executor_resume(self, context):
        from api_service.runs.protocols.executor import ExecutorResumeProtocol
        return await ExecutorResumeProtocol(self)(context)

    async def executor_event(self, context):
        """Deliver the result, then repair public state from its durable receipt."""
        from api_service.runs.projection import synchronize_executor_completion
        await self.executor_resume(context)
        await synchronize_executor_completion(context, self.graph)
