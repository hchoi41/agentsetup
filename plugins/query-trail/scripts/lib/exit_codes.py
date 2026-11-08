"""Shared exit-code convention for every query-trail script (design spec v1 section 1.4).

0 PASS; 1 check FAIL (stop, open G8 or the phase's gate); 2 REFUSED, precondition or policy
not met, nothing ran; 3 human decision needed, gate_request.json written; 4 engine or
environment error (one unchanged retry after Max fixes the cause).
"""
from __future__ import annotations

import enum


class ExitCode(enum.IntEnum):
    """Process exit status. A script's exit code is the verdict (spec 0.1 rule 1)."""

    PASS = 0
    FAIL = 1
    REFUSED = 2
    NEEDS_DECISION = 3
    ENGINE_ERROR = 4


class QueryTrailError(Exception):
    """Base error that carries the exit code a CLI should return."""

    exit_code: ExitCode = ExitCode.ENGINE_ERROR

    def __init__(self, message: str, exit_code: ExitCode | None = None) -> None:
        super().__init__(message)
        if exit_code is not None:
            self.exit_code = exit_code


class RefusedError(QueryTrailError):
    """Precondition or policy not met; nothing ran (exit 2)."""

    exit_code = ExitCode.REFUSED


class CheckFailedError(QueryTrailError):
    """A deterministic check failed (exit 1)."""

    exit_code = ExitCode.FAIL
