"""Shared snapshot-to-service projection rules for initial turns and resumes."""


class GraphProjectionError(RuntimeError):
    """Graph progress is durable; only service projection may be retried."""


def snapshot_interrupts(snapshot):
    return [item for task in snapshot.tasks for item in task.interrupts]


def checkpoint_state(snapshot):
    """Never retain a stale __interrupt__ value from a root dict channel."""
    state = dict(snapshot.values)
    state.pop("__interrupt__", None)
    interrupts = snapshot_interrupts(snapshot)
    if interrupts:
        state["__interrupt__"] = tuple(interrupts)
    return state
