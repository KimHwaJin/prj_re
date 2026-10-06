"""Only Executor events that can advance a graph are routed to the command ledger."""

EVENT_TYPES = frozenset(
    {"execution.operation_completed", "execution.completed"}
)
