"""Execution ownership failure shared by adapters and service orchestration."""
class ExecutionNeedsRecovery(RuntimeError):
    """Execution termination/ownership was uncertain; never retry as a graph error."""
