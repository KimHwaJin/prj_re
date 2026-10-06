"""Application failures translated into HTTP responses by the API boundary."""
class ApplicationError(Exception):
    def __init__(self, status_code: int, detail: str, headers=None):
        super().__init__(detail)
        self.status_code, self.detail, self.headers = status_code, detail, headers
