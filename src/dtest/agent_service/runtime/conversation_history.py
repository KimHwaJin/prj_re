"""Bound conversational context by request turns, independently of checkpoints.

One public Run is one turn: its question, plan proposals and HITL feedback stay
together. Tool/internal messages are not conversation turns. Historical records
without turn_id remain readable and are grouped by user/assistant exchanges.
"""

from __future__ import annotations


def _turns(history):
    groups = []
    identities = []
    for item in history:
        if item.get("role") not in {"user", "assistant"}:
            continue
        identity = item.get("turn_id")
        if identity:
            new_turn = not groups or identities[-1] != identity
        else:
            # Old checkpoints have no request identity. Do not invent a prior
            # question for an orphaned assistant message.
            if not groups and item["role"] != "user":
                continue
            new_turn = (
                not groups
                or item["role"] == "user"
                or identities[-1] is not None
            )
        if new_turn:
            groups.append([])
            identities.append(identity)
        groups[-1].append(item)
    return groups


def bounded_history(history, *, previous_turns):
    """Keep complete prior turns and the current turn; never slice messages."""
    return [
        item
        for turn in _turns(history)[-(previous_turns + 1) :]
        for item in turn
    ]


def append_history(
    history, *, role, content, turn_id, settings, initial_request=None
):
    identity = str(turn_id)
    if (
        initial_request is not None
        and history
        and not any(item.get("turn_id") == identity for item in history)
    ):
        # Pre-098 checkpoints only had role/content. Locate the current request
        # so an initial resume does not trim its original question as a prior turn.
        starts = [
            i
            for i, item in enumerate(history)
            if item.get("role") == "user"
            and item.get("content") == initial_request
            and not item.get("turn_id")
        ]
        if starts:
            start = starts[-1]
            history = [
                *history[:start],
                *[{**item, "turn_id": identity} for item in history[start:]],
            ]
    return bounded_history(
        [
            *history,
            {"role": role, "content": content, "turn_id": str(turn_id)},
        ],
        previous_turns=settings.set_max_history,
    )


def history_for_prompt(state, settings):
    turns = _turns(state.get("history", []))
    keep = settings.set_max_history + 1 if settings.active_multi_turn else 1
    # Storage identity belongs to the runtime, not the LLM prompt/API contract.
    return [
        {"role": item["role"], "content": item["content"]}
        for turn in turns[-keep:]
        for item in turn
    ]
