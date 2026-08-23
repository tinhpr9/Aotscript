#!/usr/bin/env python3
"""Deterministic CalVer engine and canonical release version management for Aotscript."""

from __future__ import annotations

import calendar
import dataclasses
import datetime
import json
import pathlib
import re
from typing import Any

CALVER_REGEX = re.compile(r"^([12]\d{3})\.(0[1-9]|1[0-2])\.(0[1-9]|[12]\d|3[01])\.([0-9]{2})$")


@dataclasses.dataclass(frozen=True)
class CalVer:
    year: int
    month: int
    day: int
    seq: int

    @property
    def date(self) -> datetime.date:
        return datetime.date(self.year, self.month, self.day)

    def serialize(self) -> str:
        return f"{self.year:04d}.{self.month:02d}.{self.day:02d}.{self.seq:02d}"

    def __str__(self) -> str:
        return self.serialize()


def parse_calver(version_str: str) -> CalVer:
    match = CALVER_REGEX.fullmatch(version_str.strip())
    if not match:
        raise ValueError(f"invalid_calver_format:{version_str}")
    year_str, month_str, day_str, seq_str = match.groups()
    year = int(year_str)
    month = int(month_str)
    day = int(day_str)
    seq = int(seq_str)

    if seq < 1 or seq > 99:
        raise ValueError(f"invalid_calver_sequence:{seq}")

    _, max_days = calendar.monthrange(year, month)
    if day < 1 or day > max_days:
        raise ValueError(f"invalid_calver_day_for_month:{year}-{month:02d}-{day:02d}")

    return CalVer(year=year, month=month, day=day, seq=seq)


def next_calver(current_str: str, target_date: datetime.date | None = None) -> CalVer:
    current = parse_calver(current_str)
    if target_date is None:
        target_date = datetime.date.today()

    if target_date < current.date:
        raise ValueError(f"cannot_bump_to_past_date:{target_date}_before_{current.date}")

    if target_date == current.date:
        if current.seq >= 99:
            raise ValueError(f"max_daily_calver_sequence_reached:{current_str}")
        return CalVer(year=current.year, month=current.month, day=current.day, seq=current.seq + 1)
    else:
        return CalVer(year=target_date.year, month=target_date.month, day=target_date.day, seq=1)


def load_canonical_version(path: pathlib.Path) -> dict[str, str]:
    if not path.is_file():
        raise FileNotFoundError(f"canonical_version_file_missing:{path}")
    data = json.loads(path.read_text(encoding="utf-8"))
    version = data.get("version")
    if not isinstance(version, str):
        raise ValueError("canonical_version_missing")
    parsed = parse_calver(version)
    canonical_ver = parsed.serialize()
    return {
        "version": canonical_ver,
        "worker_version": f"aot-worker-{canonical_ver}",
        "tag": f"worker-v{canonical_ver}",
    }


def save_canonical_version(path: pathlib.Path, version_str: str) -> dict[str, str]:
    parsed = parse_calver(version_str)
    ver = parsed.serialize()
    payload = {
        "version": ver,
        "worker_version": f"aot-worker-{ver}",
        "tag": f"worker-v{ver}",
    }
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    return payload


if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1:
        cmd = sys.argv[1]
        if cmd == "validate":
            v = sys.argv[2]
            parsed = parse_calver(v)
            print(f"VALID: {parsed}")
        elif cmd == "bump":
            v = sys.argv[2]
            nb = next_calver(v)
            print(nb.serialize())
