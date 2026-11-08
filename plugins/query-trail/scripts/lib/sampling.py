"""Sampling unit, rank and salt rule shared by run_steps.py (level-80 sample) and sample.py (P8 draw).

Design spec v1 section 6.2 (v4 P8): the sampling unit is a NULL-safe row id = SHA-256 of the canonical
row (lib.canon, D-04 rules, the run's FLOAT tolerance) + 0x1f + the row's file position, so duplicates
stay distinct units. For rows captured from a stream (level 80) the file position is replaced by the
occurrence index among identical canonical rows (spec 6.2 implementation note, assumed), which ignores
BigQuery's arbitrary result order. Draw order: rank = SHA-256(salt + 0x1f + row id), ascending, tie-break
by row id, never by a business key. One implementation, so both scripts give the same id and rank.
"""
from __future__ import annotations

import hashlib
from typing import Any, Mapping

SEP = b"\x1f"
ROW_ID_RULE_POSITION = "sha256(canonical row + 0x1f + file position)"
ROW_ID_RULE_OCCURRENCE = "sha256(canonical row + 0x1f + occurrence index)"
RANK_RULE = "sha256(salt + 0x1f + row id), ascending, tie-break row id"


def row_id(row_bytes: bytes, position: int) -> str:
    """NULL-safe unit id: SHA-256 of canonical row bytes, a unit separator, and the position or occurrence."""
    return hashlib.sha256(row_bytes + SEP + str(int(position)).encode("ascii")).hexdigest()


def rank_key(salt: str, rid: str) -> str:
    """Hash-rank of a unit under a salt (lower draws first)."""
    return hashlib.sha256(salt.encode("utf-8") + SEP + rid.encode("ascii")).hexdigest()


def default_salt(cfg: Mapping[str, Any]) -> str:
    """The salt rule: run_config.sample_salt, else the run id (recorded by whoever draws first)."""
    return str(cfg.get("sample_salt") or cfg["run_id"])


class OccurrenceIds:
    """Row ids by occurrence index among identical canonical rows (stream capture, spec 6.2 note)."""

    def __init__(self) -> None:
        self._seen: dict[bytes, int] = {}

    def next_id(self, row_bytes: bytes) -> str:
        """Row id of the next occurrence of `row_bytes`."""
        k = self._seen.get(row_bytes, 0)
        self._seen[row_bytes] = k + 1
        return row_id(row_bytes, k)
