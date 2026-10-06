"""Run application errors, independent of HTTP transports."""


class RunError(Exception):
    retryable = False

    def __init__(self, detail: str):
        super().__init__(detail)
        self.detail = detail


class RunNotFound(RunError):
    """The authorized resource could not be found."""


class RunConflict(RunError):
    """Current session/Run identity or lifecycle rejects the request."""


class InvalidRunRequest(RunError):
    """The requested input/model/relationship is invalid."""


class RunUnavailable(RunError):
    """Execution dependencies are unavailable."""

    retryable = True


class RunExecutionFailed(RunError):
    """Graph execution failed and its durable outcome was recorded."""

    retryable = True


class CancellationRequested(Exception):
    """The caller requested cancellation; graph termination must be confirmed."""
