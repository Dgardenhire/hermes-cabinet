"""The one error type Cabinet's page backend raises. ``status_code`` is the HTTP status the route answers with."""

from __future__ import annotations

from typing import Any


class ApiError(Exception):
    def __init__(self, status_code: int, detail: Any):
        super().__init__(str(detail))
        self.status_code = status_code
        self.detail = detail
