class ApiError(Exception):
    """Application error with a machine-readable code.

    Rendered as {"detail": ..., "code": ...} by the handler in main.py, so a
    frontend can branch on `code` instead of parsing human-readable text.
    """

    def __init__(self, status_code: int, code: str, detail: str):
        self.status_code = status_code
        self.code = code
        self.detail = detail
        super().__init__(detail)
