"""Execution ownership failure shared by adapters and service orchestration."""
class InvocationNeedsRecovery(RuntimeError):
    """A stopped invocation needs session-local reconciliation."""


class ExecutionNeedsRecovery(RuntimeError):
    """Execution termination/ownership was uncertain; never retry as a graph error."""
