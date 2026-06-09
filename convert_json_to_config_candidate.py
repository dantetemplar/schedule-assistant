from __future__ import annotations

import argparse
import csv
import json
import re
from collections import defaultdict
from copy import deepcopy
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any
from xml.etree import ElementTree as ET
from zipfile import ZipFile

import yaml

from config import Weekday

EXCLUDED_ROOM_IDS = {
    "1.1",
    "1.3",
    "3.1",
    "3.2",
    "3.3",
    "3.4",
    "3.5",
    "4.2",
    "4.3",
    "4.4",
    "4.5",
    "425",
    "309A",
}

SCRIPT_DIR = Path(__file__).resolve().parent
DEFAULT_CORE_COURSES_YAML = Path("core-courses-lessons-sum-2026.yaml")
DEFAULT_ELECTIVES_YAML = Path("electives-lessons-sum-2026.yaml")

def _program_code(program: dict[str, Any]) -> str:
    return str(program.get("code") or program.get("id") or "").strip()


def _group_entry_code(entry: Any) -> str:
    if isinstance(entry, str):
        return entry.strip()
    if isinstance(entry, dict):
        return str(entry.get("code") or entry.get("id") or "").strip()
    return ""


# Mirrors `sections` hierarchy in config.py — track `groups` are plain group id strings.
PROGRAMS: dict[str, list[dict[str, Any]]] = {
    "bachelor": [
        {
            "code": "BS_Y1_EN",
            "name": "BS - Year 1 (EN)",
            "language": "en",
            "year": 1,
            "tracks": [
                {
                    "name": "Computer Science and Engineering",
                    "code": "CSE",
                    "kind": "track",
                    "groups": [
                        "B25-CSE-01",
                        "B25-CSE-02",
                        "B25-CSE-03",
                        "B25-CSE-04",
                        "B25-CSE-05",
                    ],
                },
                {
                    "name": "Data Science and Artificial Intelligence",
                    "code": "DSAI",
                    "kind": "track",
                    "groups": [
                        "B25-DSAI-01",
                        "B25-DSAI-02",
                        "B25-DSAI-03",
                        "B25-DSAI-04",
                        "B25-DSAI-05",
                    ],
                },
            ],
        },
        {
            "code": "BS_Y1_RU",
            "name": "BS - Year 1 (RU)",
            "language": "ru",
            "year": 1,
            "tracks": [
                {
                    "name": "AI360",
                    "code": "AI360",
                    "kind": "track",
                    "groups": ["B25-AI360-01"],
                },
                {
                    "name": "MFAI",
                    "code": "MFAI",
                    "kind": "track",
                    "groups": [
                        "B25-MFAI-01",
                        "B25-MFAI-02",
                        "B25-MFAI-03",
                        "B25-MFAI-04",
                        "B25-MFAI-05",
                        "B25-MFAI-06",
                        "B25-MFAI-07",
                    ],
                },
                {
                    "name": "Robotics",
                    "code": "RO",
                    "kind": "track",
                    "groups": ["B25-RO-01"],
                },
            ],
        },
        {
            "code": "BS_Y2_EN",
            "name": "BS - Year 2 (EN)",
            "language": "en",
            "year": 2,
            "tracks": [
                {
                    "name": "Software Development",
                    "code": "SD",
                    "kind": "track",
                    "groups": ["B24-SD-01", "B24-SD-02", "B24-SD-03"],
                },
                {
                    "name": "Cybersecurity",
                    "code": "CBS",
                    "kind": "track",
                    "groups": ["B24-CBS-01", "B24-CBS-02", "B24-CBS-03"],
                },
                {
                    "name": "Data Science",
                    "code": "DS",
                    "kind": "track",
                    "groups": ["B24-DS-01"],
                },
                {
                    "name": "Artificial Intelligence",
                    "code": "AI",
                    "kind": "track",
                    "groups": ["B24-AI-01", "B24-AI-02", "B24-AI-03"],
                },
                {
                    "name": "Game Development",
                    "code": "GD",
                    "kind": "track",
                    "groups": ["B24-GD-01"],
                },
                {
                    "name": "Robotics",
                    "code": "RO",
                    "kind": "track",
                    "groups": ["B24-RO-01"],
                },
            ],
        },
        {
            "code": "BS_Y2_RU",
            "name": "BS - Year 2 (RU)",
            "language": "ru",
            "year": 2,
            "tracks": [
                {
                    "name": "MFAI",
                    "code": "MFAI",
                    "kind": "track",
                    "groups": ["B24-MFAI-01", "B24-MFAI-02", "B24-MFAI-03", "B24-MFAI-04"],
                },
                {
                    "name": "Robotics",
                    "code": "RO",
                    "kind": "track",
                    "groups": ["B24-RO15-01"],
                },
                {
                    "name": "AI360",
                    "code": "AI360",
                    "kind": "track",
                    "groups": ["B24-AI360-01"],
                },
            ],
        },
        {
            "code": "BS_Y3_EN",
            "name": "BS - Year 3 (EN)",
            "language": "en",
            "year": 3,
            "tracks": [
                {
                    "name": "Software Development",
                    "code": "SD",
                    "kind": "track",
                    "groups": ["B23-SD-01", "B23-SD-02", "B23-SD-03"],
                },
                {
                    "name": "Cybersecurity",
                    "code": "CBS",
                    "kind": "track",
                    "groups": ["B23-CBS-01", "B23-CBS-02"],
                },
                {
                    "name": "Artificial Intelligence",
                    "code": "AI",
                    "kind": "track",
                    "groups": ["B23-AI-01", "B23-AI-02"],
                },
                {
                    "name": "Data Science",
                    "code": "DS",
                    "kind": "track",
                    "groups": ["B23-DS-01", "B23-DS-02"],
                },
                {
                    "name": "Game Development",
                    "code": "GD",
                    "kind": "track",
                    "groups": ["B23-GD-01"],
                },
                {
                    "name": "Robotics",
                    "code": "RO",
                    "kind": "track",
                    "groups": ["B23-RO-01"],
                },
            ],
        },
    ],
    "master": [
        {
            "code": "MS_Y1",
            "name": "MS - Year 1",
            "year": 1,
            "tracks": [
                {
                    "name": "Software Engineering",
                    "code": "SE",
                    "kind": "track",
                    "groups": ["M25-SE-01", "M25-SE-02"],
                },
                {
                    "name": "AIDE",
                    "code": "AIDE",
                    "kind": "track",
                    "groups": ["M25-AIDE-01"],
                },
                {
                    "name": "Data Science",
                    "code": "DS",
                    "kind": "track",
                    "groups": ["M25-DS-01"],
                },
                {
                    "name": "Robotics",
                    "code": "RO",
                    "kind": "track",
                    "groups": ["M25-RO-01"],
                },
                {
                    "name": "Technological Entrepreneurship",
                    "code": "TE",
                    "kind": "track",
                    "groups": ["M25-TE-01"],
                },
                {
                    "name": "SNE",
                    "code": "SNE",
                    "kind": "track",
                    "groups": ["M25-SNE-01"],
                },
            ],
        }
    ],
    "phd": [
        {
            "code": "PHD",
            "name": "PhD",
            "year": 1,
            "tracks": [
                {
                    "name": "PhD",
                    "code": "PHD",
                    "kind": "track",
                    "groups": ["PhD"],
                },
            ],
        },
    ],
}


GROUP_ESTIMATED_SIZE: dict[str, int] = {
    "B25-CSE-01": 27,
    "B25-CSE-02": 27,
    "B25-CSE-03": 26,
    "B25-CSE-04": 26,
    "B25-CSE-05": 26,
    "B25-DSAI-01": 26,
    "B25-DSAI-02": 25,
    "B25-DSAI-03": 25,
    "B25-DSAI-04": 25,
    "B25-DSAI-05": 25,
    "B25-AI360-01": 18,
    "B25-MFAI-01": 18,
    "B25-MFAI-02": 18,
    "B25-MFAI-03": 18,
    "B25-MFAI-04": 18,
    "B25-MFAI-05": 18,
    "B25-MFAI-06": 18,
    "B25-MFAI-07": 30,
    "B25-RO-01": 2,
    "B24-SD-01": 30,
    "B24-SD-02": 30,
    "B24-SD-03": 30,
    "B24-CBS-01": 30,
    "B24-CBS-02": 30,
    "B24-CBS-03": 30,
    "B24-DS-01": 28,
    "B24-AI-01": 30,
    "B24-AI-02": 30,
    "B24-AI-03": 30,
    "B24-GD-01": 22,
    "B24-RO-01": 10,
    "B24-MFAI-01": 20,
    "B24-MFAI-02": 24,
    "B24-MFAI-03": 22,
    "B24-MFAI-04": 14,
    "B24-RO15-01": 1,
    "B24-AI360-01": 10,
    "B23-SD-01": 30,
    "B23-SD-02": 27,
    "B23-SD-03": 25,
    "B23-CBS-01": 27,
    "B23-CBS-02": 26,
    "B23-AI-01": 27,
    "B23-AI-02": 24,
    "B23-DS-01": 24,
    "B23-DS-02": 25,
    "B23-GD-01": 16,
    "B23-RO-01": 14,
    "M25-SE-01": 15,
    "M25-SE-02": 15,
    "M25-AIDE-01": 27,
    "M25-DS-01": 26,
    "M25-RO-01": 14,
    "M25-TE-01": 17,
    "M25-SNE-01": 21,
    "PhD": 25,
}


IGNORED_ELECTIVE_GROUP_IDS = {"spring26-bs3-tech-fbds"}

CLASS_TAG_MAP = {
    "лаб": "lab",
    "лаба": "lab",
    "lab": "lab",
    "тут": "tut",
    "tut": "tut",
    "tutorial": "tut",
    "лек": "lec",
    "лекция": "lec",
    "lec": "lec",
    "практ": "practice",
    "практика": "practice",
    "seminar": "sem",
}

@dataclass(frozen=True)
class PatternKey:
    course: str
    class_tag: str
    room: str
    start_date: str
    end_date: str
    stream_group: str = ""
    sheet_scope: str = ""


DATE_WEEKDAY_NAMES = tuple(day.value for day in Weekday)

SUMMER_ELECTIVE_TERM_PREFIX = "SUM26"


def normalize_class_tag(value: str | None) -> str:
    if value is None:
        return "class"
    cleaned = value.strip().lower()
    return CLASS_TAG_MAP.get(cleaned, cleaned.replace(" ", "_"))


_ACADEMIC_GROUP_ID_FIXES: dict[str, str] = {
    "M25-RO-": "M25-RO-01",
    "M25-RO15-01": "M25-RO-01",
}


def normalize_academic_group_id(group: str | None) -> str | None:
    token = str(group or "").strip()
    if not token:
        return None
    if token in _ACADEMIC_GROUP_ID_FIXES:
        return _ACADEMIC_GROUP_ID_FIXES[token]
    if token.endswith("-") and len(token) >= 6 and token[0] == "M" and token[3] == "-":
        return f"{token}01"
    return token


def normalize_group_names(group_field: str | list[str] | tuple[str, ...] | None) -> list[str]:
    if group_field is None:
        return []
    raw = list(group_field) if isinstance(group_field, (list, tuple)) else [group_field]
    out: list[str] = []
    seen: set[str] = set()
    for item in raw:
        normalized = normalize_academic_group_id(str(item))
        if normalized and normalized not in seen:
            seen.add(normalized)
            out.append(normalized)
    return out


def normalize_time(value: str) -> str:
    return value[:5]


def format_time_range(start: str, end: str) -> str:
    return f"{normalize_time(start)}-{normalize_time(end)}"


def _normalize_room_value(room: str | None) -> str:
    if room is None:
        return ""
    return str(room).strip()


# (weekday, start_time, end_time, room, instructor_ids)
WeeklySlotSig = tuple[str, str, str, str, tuple[str, ...]]

EMAIL_PATTERN = re.compile(r"[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}")

USERS_CSV_DEFAULT_NAMES = (
    "Руслану для плагина - People.25-26.csv",
    "exportUsers_2026-4-14.csv",
)


def normalize_person_name(name: str) -> str:
    return " ".join(name.split()).casefold()


def _row_primary_email(row: dict[str, str]) -> str | None:
    upn = str(row.get("userPrincipalName") or "").strip()
    if upn and "@" in upn and "#EXT#" not in upn.upper():
        return upn.lower()
    for match in EMAIL_PATTERN.findall(str(row.get("otherMails") or "")):
        if "#EXT#" not in match.upper():
            return match.lower()
    for match in EMAIL_PATTERN.findall(str(row.get("imAddresses") or "")):
        if "#EXT#" not in match.upper():
            return match.lower()
    return None


def _is_student_directory_row(row: dict[str, str]) -> bool:
    if str(row.get("jobTitle") or "").strip().casefold() == "student":
        return True
    dn = str(row.get("onPremisesDistinguishedName") or "")
    return "OU=Applicants," in dn


def _instructor_row_priority(row: dict[str, str]) -> int:
    upn = str(row.get("userPrincipalName") or "")
    if "#EXT#" in upn.upper():
        return 100
    if str(row.get("userType") or "").strip() == "Guest":
        return 50
    dn = str(row.get("onPremisesDistinguishedName") or "")
    if "OU=VizitingStaff," in dn:
        return 0
    if "@innopolis." in upn.lower():
        return 5
    return 10


def _has_cyrillic(text: str) -> bool:
    return any("\u0400" <= char <= "\u04FF" for char in text)


_GIVEN_NAME_VARIANTS: dict[str, set[str]] = {
    "andrei": {"andrei", "andrey"},
    "andrey": {"andrei", "andrey"},
    "alexandr": {"alexandr", "alexander"},
    "alexander": {"alexandr", "alexander"},
}


def _given_names_compatible(left: str, right: str) -> bool:
    left_key = left.casefold()
    right_key = right.casefold()
    if left_key == right_key:
        return True
    left_variants = _GIVEN_NAME_VARIANTS.get(left_key, {left_key})
    return right_key in left_variants


def _en_name_lookup_keys(name: str) -> list[str]:
    cleaned = " ".join(name.split())
    if not cleaned:
        return []
    keys = {normalize_person_name(cleaned)}
    parts = cleaned.split()
    if len(parts) >= 2:
        keys.add(normalize_person_name(f"{parts[1]} {parts[0]}"))
        given = parts[0]
        family = " ".join(parts[1:])
        for variant in _GIVEN_NAME_VARIANTS.get(given.casefold(), {given}):
            keys.add(normalize_person_name(f"{variant} {family}"))
    return list(keys)


def _ru_name_lookup_keys(name: str) -> list[str]:
    cleaned = " ".join(name.split())
    if not cleaned:
        return []
    parts = cleaned.split()
    keys = {normalize_person_name(cleaned)}
    if len(parts) >= 2:
        keys.add(normalize_person_name(f"{parts[1]} {parts[0]}"))
    return list(keys)


def _split_display_name(display_name: str) -> tuple[str | None, str | None]:
    cleaned = display_name.strip()
    if not cleaned:
        return None, None
    if _has_cyrillic(cleaned):
        return None, cleaned
    return cleaned, None


@dataclass
class ExportEntry:
    email: str
    name_en: str | None = None
    name_ru: str | None = None


def _load_export_users_directory(
    csv_path: Path,
) -> tuple[dict[str, ExportEntry], dict[str, ExportEntry], dict[str, ExportEntry]]:
    best_by_en: dict[str, tuple[ExportEntry, int]] = {}
    best_by_ru: dict[str, tuple[ExportEntry, int]] = {}
    by_email: dict[str, ExportEntry] = {}
    with csv_path.open(encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle):
            if _is_student_directory_row(row):
                continue
            display_name = str(row.get("displayName") or "").strip()
            email = _row_primary_email(row)
            if not display_name or not email:
                continue
            name_en, name_ru = _split_display_name(display_name)
            entry = ExportEntry(email=email, name_en=name_en, name_ru=name_ru)
            by_email[email] = entry
            priority = _instructor_row_priority(row)
            for key in _en_name_lookup_keys(name_en or display_name):
                current = best_by_en.get(key)
                if current is None or priority < current[1]:
                    best_by_en[key] = (entry, priority)
            if name_ru:
                for key in _ru_name_lookup_keys(name_ru):
                    current = best_by_ru.get(key)
                    if current is None or priority < current[1]:
                        best_by_ru[key] = (entry, priority)
    return (
        {key: entry for key, (entry, _) in best_by_en.items()},
        {key: entry for key, (entry, _) in best_by_ru.items()},
        by_email,
    )


@dataclass
class PeopleEntry:
    name_en: str
    name_ru: str | None = None
    email: str | None = None
    alias: str | None = None
    position: str | None = None


@dataclass
class InstructorProfile:
    id: str
    name_en: str | None = None
    name_ru: str | None = None
    email: str | None = None
    alias: str | None = None
    position: str | None = None

    def preferred_name(self) -> str:
        return self.name_en or self.name_ru or self.id

    def merge(self, other: InstructorProfile) -> None:
        if other.email:
            self.email = other.email
            self.id = other.email
        if other.name_en and not self.name_en:
            self.name_en = other.name_en
        if other.name_ru:
            candidate_ru = other.name_ru
            if not self.name_ru or len(candidate_ru.split()) > len(self.name_ru.split()):
                self.name_ru = candidate_ru
        if other.alias and not self.alias:
            self.alias = other.alias
        if other.position and not self.position:
            self.position = other.position
        if not self.email:
            self.id = self.name_en or self.name_ru or self.id

    def to_config_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {"id": self.id}
        if self.name_en:
            payload["name_en"] = self.name_en
        if self.name_ru:
            payload["name_ru"] = self.name_ru
        if self.email:
            payload["email"] = self.email
        if self.alias:
            payload["alias"] = self.alias
        if self.position:
            payload["position"] = self.position
        return payload


def _people_csv_column_indices(header: list[str]) -> dict[str, int]:
    shtat_idx = next((idx for idx, cell in enumerate(header) if cell.strip().startswith("ШТАТ")), 1)
    name_en_idx = max(0, shtat_idx - 1)
    return {
        "name_en": name_en_idx,
        "name_ru": shtat_idx,
        "email": header.index("Email"),
        "position": header.index("Position") if "Position" in header else -1,
        "alias": header.index("Alias") if "Alias" in header else -1,
        "student": header.index("Student?") if "Student?" in header else 4,
    }


def _is_people_data_row(row: list[str], columns: dict[str, int]) -> bool:
    name_en_idx = columns["name_en"]
    if len(row) <= name_en_idx:
        return False
    name_en = str(row[name_en_idx] or "").strip()
    if not name_en or "@" in name_en:
        return False
    lowered = name_en.casefold()
    if lowered in {"hrs", "total", "t1", "t2", "t3"}:
        return False
    return True


class PeopleCatalog:
    def __init__(self) -> None:
        self._by_name: dict[str, PeopleEntry] = {}
        self._by_email: dict[str, PeopleEntry] = {}
        self._by_alias: dict[str, PeopleEntry] = {}

    def _register(self, entry: PeopleEntry) -> None:
        for key in _en_name_lookup_keys(entry.name_en):
            self._by_name[key] = entry
        if entry.name_ru:
            for key in _ru_name_lookup_keys(entry.name_ru):
                self._by_name[key] = entry
        if entry.email:
            self._by_email[entry.email.lower()] = entry
        if entry.alias:
            self._by_alias[entry.alias.lstrip("@").casefold()] = entry

    def load_from_csv(self, csv_path: Path) -> None:
        if not csv_path.exists():
            return
        with csv_path.open(encoding="utf-8", newline="") as handle:
            rows = list(csv.reader(handle))
        header_idx = next((idx for idx, row in enumerate(rows) if "Email" in row), None)
        if header_idx is None:
            return
        header = rows[header_idx]
        columns = _people_csv_column_indices(header)
        email_idx = columns["email"]
        position_idx = columns["position"]
        alias_idx = columns["alias"]
        student_idx = columns["student"]
        name_en_idx = columns["name_en"]
        name_ru_idx = columns["name_ru"]
        for row in rows[header_idx + 1 :]:
            if not _is_people_data_row(row, columns):
                continue
            if len(row) <= max(email_idx, student_idx, name_ru_idx):
                continue
            if str(row[student_idx] if student_idx < len(row) else "").strip().casefold() == "yes":
                continue
            name_en = str(row[name_en_idx] or "").strip()
            name_ru_raw = str(row[name_ru_idx] or "").strip()
            name_ru = name_ru_raw or None
            email_raw = str(row[email_idx] or "").strip().lower()
            email = email_raw if "@" in email_raw and "#EXT#" not in email_raw.upper() else None
            alias_raw = (
                str(row[alias_idx] or "").strip() if alias_idx >= 0 and alias_idx < len(row) else ""
            )
            alias = alias_raw if alias_raw else None
            position_raw = (
                str(row[position_idx] or "").strip() if position_idx >= 0 and position_idx < len(row) else ""
            )
            position = position_raw if position_raw else None
            self._register(
                PeopleEntry(
                    name_en=name_en,
                    name_ru=name_ru or None,
                    email=email,
                    alias=alias,
                    position=position,
                )
            )

    def find(self, token: str) -> PeopleEntry | None:
        cleaned = " ".join(token.split())
        if not cleaned:
            return None
        if "@" in cleaned:
            by_email = self._by_email.get(cleaned.lower())
            if by_email:
                return by_email
        for key in _en_name_lookup_keys(cleaned):
            by_name = self._by_name.get(key)
            if by_name:
                return by_name
        if _has_cyrillic(cleaned):
            for key in _ru_name_lookup_keys(cleaned):
                by_name = self._by_name.get(key)
                if by_name:
                    return by_name
        return self._by_alias.get(cleaned.lstrip("@").casefold())

    def iter_with_email(self) -> list[PeopleEntry]:
        return list(self._by_email.values())


@dataclass
class InstructorLookup:
    export_by_en: dict[str, ExportEntry]
    export_by_ru: dict[str, ExportEntry]
    export_by_email: dict[str, ExportEntry]
    people: PeopleCatalog


def _is_export_users_csv(csv_path: Path) -> bool:
    if not csv_path.exists():
        return False
    with csv_path.open(encoding="utf-8", newline="") as handle:
        header_line = handle.readline()
    return "displayName" in header_line and "userPrincipalName" in header_line


def resolve_users_csv_paths(explicit: Path | None, *search_dirs: Path) -> list[Path]:
    dirs = [directory for directory in search_dirs if directory]
    if explicit is not None:
        if explicit.is_absolute() and explicit.exists():
            return [explicit]
        for directory in dirs:
            candidate = directory / explicit
            if candidate.exists():
                return [candidate]
        return [explicit]
    found: list[Path] = []
    for name in USERS_CSV_DEFAULT_NAMES:
        for directory in dirs:
            candidate = directory / name
            if candidate.exists() and candidate not in found:
                found.append(candidate)
    return found


def load_instructor_lookup(csv_paths: list[Path]) -> InstructorLookup:
    export_by_en: dict[str, ExportEntry] = {}
    export_by_ru: dict[str, ExportEntry] = {}
    export_by_email: dict[str, ExportEntry] = {}
    people = PeopleCatalog()
    for csv_path in csv_paths:
        if not csv_path.exists():
            continue
        if _is_export_users_csv(csv_path):
            by_en, by_ru, by_email = _load_export_users_directory(csv_path)
            export_by_en.update(by_en)
            export_by_ru.update(by_ru)
            export_by_email.update(by_email)
        else:
            people.load_from_csv(csv_path)
    return InstructorLookup(
        export_by_en=export_by_en,
        export_by_ru=export_by_ru,
        export_by_email=export_by_email,
        people=people,
    )


def _find_export_entry(token: str, lookup: InstructorLookup) -> ExportEntry | None:
    cleaned = " ".join(token.split())
    if not cleaned:
        return None
    if "@" in cleaned:
        return lookup.export_by_email.get(cleaned.lower())
    for key in _en_name_lookup_keys(cleaned):
        hit = lookup.export_by_en.get(key)
        if hit:
            return hit
    if _has_cyrillic(cleaned):
        for key in _ru_name_lookup_keys(cleaned):
            hit = lookup.export_by_ru.get(key)
            if hit:
                return hit
    return None


def _apply_export_to_profile(profile: InstructorProfile, export: ExportEntry) -> None:
    profile.email = export.email
    profile.id = export.email
    if export.name_en and not profile.name_en:
        profile.name_en = export.name_en
    if export.name_ru and not profile.name_ru:
        profile.name_ru = export.name_ru


def _apply_people_to_profile(profile: InstructorProfile, people: PeopleEntry) -> None:
    if not profile.name_en:
        profile.name_en = people.name_en
    if people.name_ru:
        candidate_ru = people.name_ru
        if not profile.name_ru or len(candidate_ru.split()) > len(profile.name_ru.split()):
            profile.name_ru = candidate_ru
    if people.email:
        profile.email = people.email
        profile.id = people.email
    if people.alias and not profile.alias:
        profile.alias = people.alias
    if people.position and not profile.position:
        profile.position = people.position


def _profile_label_tokens(profile: InstructorProfile) -> set[str]:
    tokens: set[str] = set()
    for value in (profile.name_en, profile.name_ru, profile.email, profile.alias):
        if not value:
            continue
        tokens.add(normalize_person_name(value))
        if "@" in value:
            tokens.add(value.lower())
            tokens.add(value.lstrip("@").casefold())
    return tokens


def _names_refer_to_same_person(left: InstructorProfile, right: InstructorProfile) -> bool:
    if left.email and right.email:
        return left.email == right.email

    for left_name in (left.name_en, left.name_ru):
        if not left_name:
            continue
        for right_name in (right.name_en, right.name_ru):
            if not right_name:
                continue
            if normalize_person_name(left_name) == normalize_person_name(right_name):
                return True
            if _has_cyrillic(left_name) or _has_cyrillic(right_name):
                left_keys = set(_ru_name_lookup_keys(left_name))
                right_keys = set(_ru_name_lookup_keys(right_name))
                if left_keys & right_keys:
                    return True
                continue
            left_parts = left_name.split()
            right_parts = right_name.split()
            if len(left_parts) >= 2 and len(right_parts) >= 2:
                if left_parts[-1].casefold() == right_parts[-1].casefold() and _given_names_compatible(
                    left_parts[0], right_parts[0]
                ):
                    return True
    return False


def profiles_should_merge(left: InstructorProfile, right: InstructorProfile) -> bool:
    if _names_refer_to_same_person(left, right):
        return True
    left_tokens = _profile_label_tokens(left)
    right_tokens = _profile_label_tokens(right)
    return bool(left_tokens & right_tokens)


def resolve_instructor_profile(token: str, lookup: InstructorLookup) -> InstructorProfile:
    display_name = " ".join(token.split())
    name_en, name_ru = _split_display_name(display_name)
    profile = InstructorProfile(
        id=display_name,
        name_en=name_en or (display_name if not name_ru else None),
        name_ru=name_ru,
    )

    people = lookup.people.find(display_name)
    export = _find_export_entry(display_name, lookup)
    if people and not export and people.name_ru:
        for key in _ru_name_lookup_keys(people.name_ru):
            export = lookup.export_by_ru.get(key)
            if export:
                break

    if export:
        _apply_export_to_profile(profile, export)
    if people:
        _apply_people_to_profile(profile, people)
    if not profile.email:
        profile.id = profile.name_en or profile.name_ru or display_name
    return profile


def register_instructor(
    name: str,
    instructors_map: dict[str, InstructorProfile],
    lookup: InstructorLookup,
) -> str:
    profile = resolve_instructor_profile(name, lookup)
    canonical_id = profile.id

    for existing_id in list(instructors_map):
        if existing_id == canonical_id:
            continue
        if profiles_should_merge(instructors_map[existing_id], profile):
            profile.merge(instructors_map[existing_id])
            del instructors_map[existing_id]

    existing = instructors_map.get(canonical_id)
    if existing:
        existing.merge(profile)
        profile = existing
    instructors_map[canonical_id] = profile
    return canonical_id


def seed_instructors_from_people_roster(
    instructors_map: dict[str, InstructorProfile],
    lookup: InstructorLookup,
) -> None:
    for entry in lookup.people.iter_with_email():
        token = entry.name_en or entry.email or ""
        if token:
            register_instructor(token, instructors_map, lookup)


def _teacher_signature(
    teacher_names: list[str],
    instructors_map: dict[str, InstructorProfile],
    lookup: InstructorLookup,
) -> tuple[str, ...]:
    return tuple(
        sorted(register_instructor(name, instructors_map, lookup) for name in teacher_names)
    )


def _instructor_pattern_value(instructor_ids: tuple[str, ...]) -> str | list[str] | None:
    if not instructor_ids:
        return None
    if len(instructor_ids) == 1:
        return instructor_ids[0]
    return list(instructor_ids)


def _nest_modifier_entries(modifiers: dict[str, Any] | None) -> list[dict[str, Any]]:
    if not modifiers:
        return []
    nest = modifiers.get("NEST")
    if not nest:
        return []
    return [entry for entry in nest if isinstance(entry, dict)]


def _top_level_on_dates(modifiers: dict[str, Any] | None) -> list[str]:
    if not modifiers:
        return []
    on = modifiers.get("on")
    if not on:
        return []
    return [str(value) for value in on]


def _is_occurrence_only_modifier(modifiers: dict[str, Any] | None) -> bool:
    return bool(_top_level_on_dates(modifiers)) and not _nest_modifier_entries(modifiers)


def _is_weekly_with_nest_modifier(modifiers: dict[str, Any] | None) -> bool:
    return bool(_nest_modifier_entries(modifiers))


def _core_occurrence_from_row(
    row: dict[str, Any],
    instructor_ids: tuple[str, ...],
    *,
    date_value: str,
) -> dict[str, Any]:
    modifiers = row.get("modifiers") or {}
    room = _normalize_room_value(row.get("room")) or str(modifiers.get("location") or "").strip()
    entry: dict[str, Any] = {
        "date": date_value,
        "start_time": normalize_time(row["start_time"]),
        "end_time": normalize_time(row["end_time"]),
    }
    if room:
        entry["room"] = room
    instructor = _instructor_pattern_value(instructor_ids)
    if instructor is not None:
        entry["instructor"] = instructor
    return entry


def _core_occurrences_from_row(row: dict[str, Any], instructor_ids: tuple[str, ...]) -> list[dict[str, Any]]:
    on_dates = _top_level_on_dates(row.get("modifiers"))
    return [
        _core_occurrence_from_row(row, instructor_ids, date_value=date_value)
        for date_value in on_dates
    ]


def _nest_edits_from_modifiers(modifiers: dict[str, Any] | None) -> list[dict[str, Any]]:
    edits: list[dict[str, Any]] = []
    for nested in _nest_modifier_entries(modifiers):
        location = nested.get("location")
        if not location:
            continue
        for date_value in nested.get("on") or []:
            edits.append(
                {
                    "select_week": str(date_value),
                    "room": str(location),
                }
            )
    return edits


def _dedupe_pattern_edits(edits: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[tuple[Any, ...]] = set()
    deduped: list[dict[str, Any]] = []
    for edit in sorted(edits, key=lambda item: str(item.get("select_week", ""))):
        instructor = edit.get("instructor")
        instructor_key = tuple(instructor) if isinstance(instructor, list) else instructor
        key = (
            str(edit.get("select_week", "")),
            edit.get("room"),
            edit.get("cancel"),
            edit.get("date"),
            edit.get("start_time"),
            edit.get("end_time"),
            instructor_key,
        )
        if key in seen:
            continue
        seen.add(key)
        deduped.append(edit)
    return deduped


def _dedupe_occurrences(occurrences: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[tuple[Any, ...]] = set()
    deduped: list[dict[str, Any]] = []
    for occurrence in sorted(
        occurrences,
        key=lambda item: (str(item.get("date", "")), str(item.get("start_time", "")), str(item.get("end_time", ""))),
    ):
        instructor = occurrence.get("instructor")
        instructor_key = tuple(instructor) if isinstance(instructor, list) else instructor
        key = (
            str(occurrence.get("date", "")),
            str(occurrence.get("start_time", "")),
            str(occurrence.get("end_time", "")),
            occurrence.get("room"),
            instructor_key,
        )
        if key in seen:
            continue
        seen.add(key)
        deduped.append(occurrence)
    return deduped


def _build_instructor_pool(
    signatures_for_pool: list[tuple[str, ...]],
) -> list[str] | list[str | list[str]]:
    if not signatures_for_pool:
        return []
    if len(signatures_for_pool) == 1:
        sig = signatures_for_pool[0]
        if len(sig) == 0:
            return []
        if len(sig) == 1:
            return [sig[0]]
        return [list(sig)]
    if all(len(sig) == 1 for sig in signatures_for_pool):
        return sorted(sig[0] for sig in signatures_for_pool)
    return [list(sig) for sig in signatures_for_pool]


def _weekly_pattern_from_slots(
    slots: set[WeeklySlotSig],
    edits_by_slot: dict[WeeklySlotSig, list[dict[str, Any]]] | None = None,
) -> list[dict[str, Any]]:
    order = {name: index for index, name in enumerate(DATE_WEEKDAY_NAMES)}
    pattern: list[dict[str, Any]] = []
    for weekday, start_time, end_time, room, instructor_ids in sorted(
        slots, key=lambda item: (order.get(item[0], 99), item[1], item[2], item[3], item[4])
    ):
        entry: dict[str, Any] = {
            "weekday": weekday,
            "start_time": normalize_time(start_time),
            "end_time": normalize_time(end_time),
        }
        if room:
            entry["room"] = room
        instructor = _instructor_pattern_value(instructor_ids)
        if instructor is not None:
            entry["instructor"] = instructor
        slot_edits = _dedupe_pattern_edits((edits_by_slot or {}).get((weekday, start_time, end_time, room, instructor_ids), []))
        if slot_edits:
            entry["edits"] = slot_edits
        pattern.append(entry)
    return pattern


def _occurrences_for_variant(
    signatures_for_pool: list[tuple[str, ...]],
    groups_for_cls: list[str],
    occurrences_by_signature: dict[tuple[str, ...], dict[str, list[dict[str, Any]]]],
) -> list[dict[str, Any]]:
    collected: list[dict[str, Any]] = []
    for signature in signatures_for_pool:
        by_group = occurrences_by_signature.get(signature, {})
        for group_id in groups_for_cls:
            collected.extend(by_group.get(group_id, []))
    return _dedupe_occurrences(collected)


def _core_sessions_from_slots(
    groups_for_cls: list[str],
    slots_source: dict[str, set[WeeklySlotSig]],
    student_groups: list[str],
    *,
    per_group: bool,
    occurrences_by_signature: dict[tuple[str, ...], dict[str, list[dict[str, Any]]]] | None = None,
    signatures_for_pool: list[tuple[str, ...]] | None = None,
    edits_by_slot: dict[WeeklySlotSig, list[dict[str, Any]]] | None = None,
) -> list[dict[str, Any]]:
    signatures = signatures_for_pool or [()]
    if per_group:
        result: list[dict[str, Any]] = []
        for group_id in sorted(groups_for_cls):
            slots = slots_source.get(group_id, set())
            occurrences = _occurrences_for_variant(signatures, [group_id], occurrences_by_signature or {})
            if slots:
                result.append(
                    {
                        "audience": [group_id],
                        "weekly_pattern": _weekly_pattern_from_slots(slots, edits_by_slot),
                    }
                )
            if occurrences:
                result.append(
                    {
                        "audience": [group_id],
                        "occurrences": occurrences,
                    }
                )
        return result

    all_slots: set[WeeklySlotSig] = set()
    for group_id in groups_for_cls:
        all_slots.update(slots_source.get(group_id, set()))
    occurrences = _occurrences_for_variant(signatures, groups_for_cls, occurrences_by_signature or {})
    if not all_slots and not occurrences:
        return []

    result = []
    if all_slots:
        result.append(
            {
                "audience": list(student_groups),
                "weekly_pattern": _weekly_pattern_from_slots(all_slots, edits_by_slot),
            }
        )
    if occurrences:
        result.append(
            {
                "audience": list(student_groups),
                "occurrences": occurrences,
            }
        )
    return result


def maybe_online(room: str | None) -> bool:
    if not room:
        return False
    cleaned = room.strip().lower()
    return cleaned in {"онлайн", "online"}


def to_instructor_id(name: str) -> str:
    slug = "".join(ch.lower() if ch.isalnum() else "_" for ch in name).strip("_")
    while "__" in slug:
        slug = slug.replace("__", "_")
    if not slug:
        slug = "unknown_instructor"
    return slug


def split_teacher_names(raw: str | None) -> list[str]:
    if not raw:
        return []
    parts = [p.strip() for p in raw.split(",")]
    names = [p for p in parts if p]
    return names


def is_english_lesson(lesson_name: str) -> bool:
    name = lesson_name.strip().lower()
    return ("english" in name) or ("англий" in name) or ("иностран" in name) or ("foreign language" in name)


def infer_course_tags(course_name: str, *, is_elective_course: bool = False) -> list[str]:
    if is_english_lesson(course_name):
        return ["english"]
    if is_elective_course:
        return ["elective"]
    return ["core_course"]


def normalize_lesson_name(lesson_name: str) -> str:
    name = lesson_name.strip()
    lowered = name.lower()
    if lowered in {"foreign language", "иностранный язык"}:
        return "Foreign Language"
    return name


def should_exclude_lesson(lesson_name: str) -> bool:
    return lesson_name.strip().lower() in {
        "group meeting with administration",
    }


_ENGLISH_DISTRIBUTION_WEEKDAY_ALIASES: dict[str, Weekday] = {
    "M": Weekday.MONDAY,
    "T": Weekday.TUESDAY,
    "W": Weekday.WEDNESDAY,
    "TH": Weekday.THURSDAY,
    "F": Weekday.FRIDAY,
    "S": Weekday.SATURDAY,
}


def canonical_weekday_label(value: str) -> str:
    token = value.strip().upper()
    alias = _ENGLISH_DISTRIBUTION_WEEKDAY_ALIASES.get(token)
    if alias is not None:
        return alias.value
    return Weekday(token).value


class FlowStyleList(list):
    pass


class _YamlDumper(yaml.SafeDumper):
    pass


def _yaml_str_presenter(dumper: yaml.SafeDumper, data: str) -> yaml.nodes.ScalarNode:
    if "\n" in data:
        return dumper.represent_scalar("tag:yaml.org,2002:str", data, style="|")
    return dumper.represent_scalar("tag:yaml.org,2002:str", data)


def _represent_flow_list(dumper: yaml.SafeDumper, data: FlowStyleList) -> yaml.nodes.SequenceNode:
    return dumper.represent_sequence("tag:yaml.org,2002:seq", data, flow_style=True)


_YamlDumper.add_representer(str, _yaml_str_presenter)
_YamlDumper.add_representer(FlowStyleList, _represent_flow_list)


def dump_config_yaml(config: dict[str, Any]) -> str:
    return yaml.dump(
        config,
        Dumper=_YamlDumper,
        sort_keys=False,
        allow_unicode=True,
        width=10_000,
    )


def apply_yaml_style_overrides(node: Any) -> Any:
    if isinstance(node, dict):
        out: dict[str, Any] = {}
        for key, value in node.items():
            if key in {"instructor_pool", "student_groups"} and isinstance(value, list):
                out[key] = FlowStyleList(value)
            else:
                out[key] = apply_yaml_style_overrides(value)
        return out
    if isinstance(node, list):
        return [apply_yaml_style_overrides(item) for item in node]
    return node


def excel_time_to_hhmm(raw: str) -> str:
    value = raw.strip()
    try:
        fraction = float(value)
        minutes = int(round(fraction * 24 * 60))
        hh = (minutes // 60) % 24
        mm = minutes % 60
        return f"{hh:02d}:{mm:02d}"
    except ValueError:
        return value[:5]


def group_id_from_english_label(label: str) -> str:
    base = "".join(ch.lower() if ch.isalnum() else "_" for ch in label.strip())
    while "__" in base:
        base = base.replace("__", "_")
    return f"ENG-{base.strip('_')}"


def english_group_sort_key_from_id(group_id: str) -> tuple[str, int, str]:
    gid = str(group_id or "").strip()
    match = re.match(r"^(ENG-[A-Za-z_]+?)(\d+)$", gid)
    if match:
        return (match.group(1).lower(), int(match.group(2)), gid)
    return (gid.lower(), 10**9, gid)


def load_xlsx_rows(xlsx_path: Path) -> list[list[str]]:
    ns = {"a": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
    with ZipFile(xlsx_path) as z:
        sst: list[str] = []
        if "xl/sharedStrings.xml" in z.namelist():
            root = ET.fromstring(z.read("xl/sharedStrings.xml"))
            for si in root.findall("a:si", ns):
                sst.append("".join((t.text or "") for t in si.findall(".//a:t", ns)))

        sheet = ET.fromstring(z.read("xl/worksheets/sheet1.xml"))
        out: list[list[str]] = []
        for row in sheet.findall("a:sheetData/a:row", ns):
            vals: list[str] = []
            for cell in row.findall("a:c", ns):
                c_type = cell.attrib.get("t")
                v = cell.find("a:v", ns)
                if v is None:
                    vals.append("")
                    continue
                raw = v.text or ""
                if c_type == "s" and raw.isdigit():
                    idx = int(raw)
                    vals.append(sst[idx] if idx < len(sst) else "")
                else:
                    vals.append(raw)
            out.append(vals)
        return out


def load_rooms(rooms_json_path: Path) -> list[dict[str, Any]]:
    if not rooms_json_path.exists():
        return []
    rows = json.loads(rooms_json_path.read_text(encoding="utf-8"))
    rooms: list[dict[str, Any]] = []
    for row in rows:
        room_id = str(row.get("id", "")).strip()
        if not room_id:
            continue
        if room_id in EXCLUDED_ROOM_IDS:
            continue
        capacity = row.get("capacity")
        if not isinstance(capacity, int):
            continue
        rooms.append(
            {
                "id": room_id,
                "name": str(row.get("title") or row.get("short_name") or room_id),
                "capacity": capacity,
            }
        )
    return sorted(
        rooms,
        key=lambda room: (
            str(room.get("id", ""))[:1],
            -int(room.get("capacity", 0)),
            str(room.get("id", "")),
        ),
    )


def load_english_distribution(
    xlsx_path: Path,
) -> tuple[
    list[dict[str, Any]],
    dict[tuple[str, str, str], set[str]],
    dict[tuple[str, str], set[str]],
    dict[str, set[str]],
    dict[str, int],
]:
    if not xlsx_path.exists():
        return [], {}, {}, {}, {}

    rows = load_xlsx_rows(xlsx_path)
    if not rows:
        return [], {}, {}, {}, {}

    header = [h.strip().lower() for h in rows[0]]
    col = {name: i for i, name in enumerate(header)}
    required = ["e group", "instructor", "days", "time", "e-mail"]
    if any(key not in col for key in required):
        return [], {}, {}, {}, {}

    by_id: dict[str, dict[str, Any]] = {}
    by_slot_and_instr: dict[tuple[str, str, str], set[str]] = defaultdict(set)
    by_slot_only: dict[tuple[str, str], set[str]] = defaultdict(set)
    group_instructors: dict[str, set[str]] = defaultdict(set)
    group_slots: dict[str, set[tuple[str, str]]] = defaultdict(set)

    for row in rows[1:]:
        max_idx = max(col[k] for k in required)
        if len(row) <= max_idx:
            continue

        label = row[col["e group"]].strip()
        if not label:
            continue
        gid = group_id_from_english_label(label)
        instr_name = row[col["instructor"]].strip() or "Unknown Instructor"
        group_instructors[gid].add(instr_name)
        time_hhmm = excel_time_to_hhmm(row[col["time"]])
        days_raw = row[col["days"]].strip()
        day_tokens = [d for d in (part.strip() for part in days_raw.split("/")) if d]
        day_names = [canonical_weekday_label(d) for d in day_tokens]
        email = row[col["e-mail"]].strip().lower()
        if "@" not in email:
            email = ""

        group = by_id.setdefault(
            gid,
            {
                "code": gid,
                "kind": "english",
                "name": label,
                "students": [],
                "size": 0,
            },
        )
        if email and email not in group["students"]:
            group["students"].append(email)

        for day in day_names:
            by_slot_and_instr[(day, time_hhmm, instr_name)].add(gid)
            by_slot_only[(day, time_hhmm)].add(gid)
            group_slots[gid].add((day, time_hhmm))

    shared_groups: list[dict[str, Any]] = []
    for group in sorted(by_id.values(), key=lambda g: english_group_sort_key_from_id(str(g.get("code") or g.get("id") or ""))):
        group["size"] = len(group["students"])
        shared_groups.append(group)

    group_per_week = {gid: len(slots) for gid, slots in group_slots.items() if slots}
    return shared_groups, by_slot_and_instr, by_slot_only, group_instructors, group_per_week


def _track_group_ids(tracks: list[dict[str, Any]]) -> set[str]:
    return {_group_entry_code(g) for track in tracks for g in track.get("groups", []) if _group_entry_code(g)}


def _program_group_ids(program: dict[str, Any]) -> set[str]:
    groups: set[str] = set()
    if "tracks" in program:
        groups.update(_track_group_ids(program.get("tracks", [])))
    if "extra_hierarchy" in program:
        groups.update(_track_group_ids(program.get("extra_hierarchy", [])))
    if "groups" in program:
        groups.update({_group_entry_code(g) for g in program.get("groups", []) if _group_entry_code(g)})
    return groups


def build_group_selectors(programs: dict[str, list[dict[str, Any]]]) -> dict[str, set[str]]:
    selectors: dict[str, set[str]] = {}
    for level_programs in programs.values():
        for program in level_programs:
            program_id = _program_code(program)
            if not program_id:
                continue
            program_groups = _program_group_ids(program)
            if program_groups:
                selectors[f"@{program_id}"] = program_groups
            for track in program.get("tracks", []):
                track_name = track.get("name")
                if not track_name:
                    continue
                track_groups = {_group_entry_code(g) for g in track.get("groups", []) if _group_entry_code(g)}
                if track_groups:
                    selectors[f"@{program_id}/{track_name}"] = track_groups
            for track in program.get("extra_hierarchy", []):
                track_name = track.get("name")
                if not track_name:
                    continue
                track_groups = {_group_entry_code(g) for g in track.get("groups", []) if _group_entry_code(g)}
                if track_groups:
                    selectors[f"@{program_id}/{track_name}"] = track_groups
    return selectors


def build_group_order(programs: dict[str, list[dict[str, Any]]]) -> dict[str, int]:
    order: dict[str, int] = {}
    idx = 0
    for level_programs in programs.values():
        for program in level_programs:
            if "tracks" in program:
                for track in program.get("tracks", []):
                    for group in track.get("groups", []):
                        gid = _group_entry_code(group)
                        if gid and gid not in order:
                            order[gid] = idx
                            idx += 1
            else:
                for group in program.get("groups", []):
                    gid = _group_entry_code(group)
                    if gid and gid not in order:
                        order[gid] = idx
                        idx += 1
            for track in program.get("extra_hierarchy", []):
                for group in track.get("groups", []):
                    gid = _group_entry_code(group)
                    if gid and gid not in order:
                        order[gid] = idx
                        idx += 1
    return order


def build_group_order_from_sections(sections: list[dict[str, Any]]) -> dict[str, int]:
    order: dict[str, int] = {}
    idx = 0

    def register_group(group: Any) -> None:
        nonlocal idx
        gid = group if isinstance(group, str) else _group_entry_code(group)
        if gid and gid not in order:
            order[gid] = idx
            idx += 1

    for section in sections:
        for program in section.get("programs", []):
            for group in program.get("groups", []):
                register_group(group)
            for track in program.get("tracks", []):
                for group in track.get("groups", []):
                    register_group(group)
    return order


def collect_academic_groups(programs: dict[str, list[dict[str, Any]]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for _level_name, level_programs in programs.items():
        for program in level_programs:
            if "tracks" in program:
                for track in program.get("tracks", []):
                    for g in track.get("groups", []):
                        gid = _group_entry_code(g)
                        if not gid or gid in seen:
                            continue
                        seen.add(gid)
                        est = GROUP_ESTIMATED_SIZE.get(gid)
                        if est is None and isinstance(g, dict):
                            est = g.get("estimated_size", g.get("size"))
                        out.append(
                            {
                                "code": gid,
                                "name": gid,
                                "estimated_size": est,
                            }
                        )
            else:
                for g in program.get("groups", []):
                    gid = _group_entry_code(g)
                    if not gid or gid in seen:
                        continue
                    seen.add(gid)
                    est = GROUP_ESTIMATED_SIZE.get(gid)
                    if est is None and isinstance(g, dict):
                        est = g.get("estimated_size", g.get("size"))
                    out.append(
                        {
                            "code": gid,
                            "name": gid,
                            "estimated_size": est,
                        }
                    )
    return out


def enrich_academic_groups_from_predefined(
    academic_groups: list[dict[str, Any]],
    predefined_json_path: Path,
) -> list[dict[str, Any]]:
    def _row_code(row: dict[str, Any]) -> str:
        return str(row.get("code") or row.get("id") or "").strip()

    ordered_ids = [_row_code(g) for g in academic_groups if _row_code(g)]
    if not predefined_json_path.exists():
        return [g for g in academic_groups if _row_code(g)]

    try:
        payload = json.loads(predefined_json_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return [g for g in academic_groups if _row_code(g)]

    predefined = payload.get("academic_groups")
    if not isinstance(predefined, list):
        return [g for g in academic_groups if _row_code(g)]

    by_id: dict[str, dict[str, Any]] = {_row_code(g): dict(g) for g in academic_groups if _row_code(g)}
    for item in predefined:
        if not isinstance(item, dict):
            continue
        gid = str(item.get("name") or "").strip()
        if not gid:
            continue
        if gid.lower() in IGNORED_ELECTIVE_GROUP_IDS:
            continue
        students_raw = item.get("user_emails")
        students = []
        if isinstance(students_raw, list):
            students = [str(email).strip().lower() for email in students_raw if str(email).strip()]

        if gid not in by_id:
            continue

        existing = by_id[gid]
        merged = dict(existing)
        merged["code"] = gid
        merged["name"] = existing.get("name") or gid
        merged["students"] = students
        merged["estimated_size"] = len(students) if students else existing.get("estimated_size")
        by_id[gid] = merged

    return [by_id[gid] for gid in ordered_ids if gid in by_id]


def _slug_code(value: str) -> str:
    cleaned = re.sub(r"[^a-zA-Z0-9]+", "_", str(value or "").strip().lower())
    return cleaned.strip("_") or "item"


def _track_code_fallback(track_name: str) -> str:
    return _slug_code(track_name).upper()


def _elective_bucket_for_group(group_id: str) -> str | None:
    normalized = str(group_id or "").strip().lower()
    if not normalized:
        return None
    if "bs2" in normalized and ("rus" in normalized or "ru" in normalized):
        return "BS2_RU"
    if "bs3" in normalized and "tech" in normalized:
        return "BS3_TECH"
    return None


ENGLISH_LEVEL_TRACK_CODE = {
    "AWA-I": "AWA_I",
    "EAP": "EAP",
    "FL": "FL",
    "Other": "OTHER",
}


def build_sections(
    programs: dict[str, list[dict[str, Any]]],
    english_groups: list[dict[str, Any]],
    elective_group_ids: set[str],
) -> list[dict[str, Any]]:
    degree_by_level = {"bachelor": "bs", "master": "ms", "phd": "phd"}
    core_programs: list[dict[str, Any]] = []
    for level_name in ("bachelor", "master", "phd"):
        for program in programs.get(level_name, []):
            tracks = []
            for track in program.get("tracks", []):
                track_name = track.get("name", "")
                track_groups = [g for g in track.get("groups", []) if _group_entry_code(g)]
                tracks.append(
                    {
                        "code": str(track.get("code") or _track_code_fallback(track_name)),
                        "name": track.get("name"),
                        "kind": str(track.get("kind") or "track"),
                        "groups": track_groups,
                    }
                )
            core_programs.append(
                {
                    "code": _program_code(program),
                    "name": program.get("name"),
                    "kind": "degree_year",
                    "degree": degree_by_level.get(level_name),
                    "language": program.get("language"),
                    "year": program.get("year"),
                    "tracks": tracks,
                }
            )

    grouped: dict[str, list[dict[str, Any]]] = {"AWA-I": [], "EAP": [], "FL": [], "Other": []}
    for group in english_groups:
        gid = group.get("code") or group.get("id")
        if not gid:
            continue
        gid_lower = str(gid).lower()
        key = "Other"
        if gid_lower.startswith("eng-awa_i_"):
            key = "AWA-I"
        elif gid_lower.startswith("eng-eap"):
            key = "EAP"
        elif gid_lower.startswith("eng-fl"):
            key = "FL"
        grouped[key].append(group)

    english_tracks: list[dict[str, Any]] = []
    for track_name in ("AWA-I", "EAP", "FL", "Other"):
        items = sorted(
            grouped[track_name],
            key=lambda item: english_group_sort_key_from_id(str(item.get("code") or item.get("id") or "")),
        )
        if not items:
            continue
        english_tracks.append(
            {
                "code": ENGLISH_LEVEL_TRACK_CODE.get(track_name, _track_code_fallback(track_name)),
                "name": track_name,
                "kind": "english_level",
                "groups": [str(item.get("code") or item.get("id")) for item in items],
            }
        )

    sections: list[dict[str, Any]] = [
        {"code": "core", "name": "Основные курсы", "kind": "core", "programs": core_programs},
    ]
    if english_tracks:
        sections.append(
            {
                "code": "english",
                "name": "Английский",
                "kind": "english",
                "programs": [
                    {
                        "code": "ENGLISH_YEAR1",
                        "name": "English for BS - Year 1",
                        "kind": "english_program",
                        "applies_to": ["BS_Y1_EN", "BS_Y1_RU"],
                        "tracks": english_tracks,
                    }
                ],
            }
        )
    if elective_group_ids:
        elective_buckets: dict[str, list[str]] = {"BS2_RU": [], "BS3_TECH": []}
        for group_id in sorted(elective_group_ids):
            bucket = _elective_bucket_for_group(group_id)
            if bucket is None:
                continue
            elective_buckets[bucket].append(group_id)

        elective_programs: list[dict[str, Any]] = []
        if elective_buckets["BS2_RU"]:
            elective_programs.append(
                {
                    "code": "BS2_RU",
                    "name": "BS2 Ru",
                    "kind": "elective_bucket",
                    "groups": list(elective_buckets["BS2_RU"]),
                }
            )
        if elective_buckets["BS3_TECH"]:
            elective_programs.append(
                {
                    "code": "BS3_TECH",
                    "name": "BS3 Tech",
                    "kind": "elective_bucket",
                    "groups": list(elective_buckets["BS3_TECH"]),
                }
            )

        if elective_programs:
            sections.append(
                {
                    "code": "electives",
                    "name": "Элективы",
                    "kind": "electives",
                    "programs": elective_programs,
                }
            )
    return sections


def attach_english_to_programs(
    programs: dict[str, list[dict[str, Any]]],
    english_groups: list[dict[str, Any]],
) -> dict[str, list[dict[str, Any]]]:
    enriched = deepcopy(programs)
    grouped: dict[str, list[dict[str, Any]]] = {"AWA-I": [], "EAP": [], "FL": [], "Other": []}

    for group in english_groups:
        gid = group.get("code") or group.get("id")
        if not gid:
            continue
        item = {"code": gid, "size": group.get("size")}
        gid_lower = str(gid).lower()
        if gid_lower.startswith("eng-awa_i_"):
            grouped["AWA-I"].append(item)
        elif gid_lower.startswith("eng-eap"):
            grouped["EAP"].append(item)
        elif gid_lower.startswith("eng-fl"):
            grouped["FL"].append(item)
        else:
            grouped["Other"].append(item)

    tracks: list[dict[str, Any]] = []
    for track_name in ("AWA-I", "EAP", "FL", "Other"):
        groups = sorted(
            grouped[track_name],
            key=lambda item: english_group_sort_key_from_id(str(item.get("code") or item.get("id") or "")),
        )
        if groups:
            tracks.append(
                {
                    "name": track_name,
                    "code": ENGLISH_LEVEL_TRACK_CODE.get(track_name, _track_code_fallback(track_name)),
                    "kind": "english_level",
                    "groups": groups,
                }
            )

    if tracks:
        enriched["english"] = [
            {
                "code": "ENGLISH_YEAR1",
                "name": "English for BS - Year 1",
                "language": "en",
                "tracks": tracks,
            }
        ]

    return enriched


def build_group_buckets(
    academic_groups: list[dict[str, Any]],
    english_groups: list[dict[str, Any]],
) -> dict[str, list[dict[str, Any]]]:
    english_clean = [
        {
            "code": group.get("code") or group.get("id"),
            "name": group.get("name", group.get("code") or group.get("id")),
            "estimated_size": group.get("size"),
            "students": group.get("students", []),
        }
        for group in english_groups
    ]
    return {
        "academic": academic_groups,
        "english": sorted(
            english_clean,
            key=lambda g: english_group_sort_key_from_id(str(g.get("code") or g.get("id") or "")),
        ),
        # Filled when elective groups are introduced. Keep schema explicit now.
        "elective": [],
    }


def build_students_groups(
    academic_groups: list[dict[str, Any]],
    english_groups: list[dict[str, Any]],
    elective_group_ids: set[str],
) -> list[dict[str, Any]]:
    distribution: list[dict[str, Any]] = []
    for group in academic_groups:
        gid = group.get("code") or group.get("id")
        if not gid:
            continue
        distribution.append(
            {
                "code": gid,
                "kind": "elective" if gid in elective_group_ids else "core",
                "name": group.get("name", gid),
                "estimated_size": group.get("estimated_size"),
                "students": group.get("students", []),
            }
        )
    for group in sorted(
        english_groups,
        key=lambda g: english_group_sort_key_from_id(str(g.get("code") or g.get("id") or "")),
    ):
        gid = group.get("code") or group.get("id")
        if not gid:
            continue
        distribution.append(
            {
                "code": gid,
                "kind": "english",
                "name": group.get("name", gid),
                "estimated_size": group.get("size"),
                "students": group.get("students", []),
            }
        )
    return distribution


def compress_groups_to_selectors(
    groups: list[str],
    selector_map: dict[str, set[str]],
    group_order: dict[str, int],
) -> list[str]:
    group_set = set(groups)
    if not group_set:
        return []

    selected: list[str] = []
    covered: set[str] = set()
    candidates = sorted(
        ((selector, members) for selector, members in selector_map.items() if members.issubset(group_set)),
        key=lambda item: (-len(item[1]), item[0].count("/"), item[0]),
    )
    for selector, members in candidates:
        if members.issubset(covered):
            continue
        selected.append(selector)
        covered.update(members)

    leftovers = list(group_set - covered)

    def member_rank(gid: str) -> int:
        return group_order.get(gid, 10**9)

    def token_rank(token: str) -> int:
        if token in selector_map:
            return min((member_rank(gid) for gid in selector_map[token]), default=10**9)
        return member_rank(token)

    combined = selected + leftovers
    return sorted(combined, key=lambda token: (token_rank(token), token))


def class_group_rank(
    cls: dict[str, Any],
    selector_map: dict[str, set[str]],
    group_order: dict[str, int],
) -> int:
    groups = cls.get("student_groups", [])
    if not groups:
        return 10**9

    def member_rank(gid: str) -> int:
        return group_order.get(gid, 10**9)

    def token_rank(token: str) -> int:
        if token in selector_map:
            return min((member_rank(gid) for gid in selector_map[token]), default=10**9)
        return member_rank(token)

    return min((token_rank(token) for token in groups), default=10**9)


def course_group_rank(
    course: dict[str, Any],
    selector_map: dict[str, set[str]],
    group_order: dict[str, int],
) -> int:
    ranks = [
        class_group_rank(component, selector_map, group_order)
        for component in course.get("components", [])
    ]
    return min(ranks) if ranks else 10**9


def infer_per_group(
    class_tag: str,
    student_groups: list[str],
    *,
    is_english_course: bool,
    source_group_count: int | None = None,
    is_shared_lesson: bool = False,
) -> bool:
    if is_shared_lesson:
        return False
    if is_english_course and class_tag == "class":
        return True
    effective_group_count = source_group_count if source_group_count is not None else len(student_groups)
    if class_tag == "class" and effective_group_count > 1:
        return True
    if class_tag == "lab":
        return True
    return False


def _weekday_name_from_date(value: date) -> str:
    return DATE_WEEKDAY_NAMES[value.weekday()]


def _is_parallel_stream_group_token(token: str) -> bool:
    value = token.strip().upper()
    return len(value) == 2 and value[0] == "G" and value[1].isdigit()


def _elective_parallel_stream_group(lesson: dict[str, Any]) -> str:
    audience = _row_audience(lesson)
    if not audience:
        return ""
    token = audience[0].strip().upper()
    if _is_parallel_stream_group_token(token):
        return token
    return ""


def _slug_alias_token(alias: str) -> str:
    ascii_slug = _slug_code(alias)
    if ascii_slug and ascii_slug != "item":
        return ascii_slug.upper()
    token = re.sub(r"[^\w]+", "_", alias, flags=re.UNICODE).strip("_")
    return token.upper() if token else "UNK"


def _elective_alias_token_for_lessons(lessons: list[dict[str, Any]]) -> str:
    alias_tokens: set[str] = set()
    for lesson in lessons:
        for token in _row_audience(lesson):
            if not _is_parallel_stream_group_token(token):
                alias_tokens.add(token)
    if alias_tokens:
        return _slug_alias_token(sorted(alias_tokens)[0])
    if lessons:
        return _slug_alias_token(_elective_subject(lessons[0]))
    return "UNK"


def _elective_alias_token(lesson: dict[str, Any]) -> str:
    return _elective_alias_token_for_lessons([lesson])


def elective_student_group_id(alias: str, parallel: str = "") -> str:
    parts = [SUMMER_ELECTIVE_TERM_PREFIX, alias]
    if parallel:
        parts.append(parallel)
    return "-".join(parts)


def _elective_parallel_groups_for_lessons(lessons: list[dict[str, Any]]) -> list[str]:
    parallels = {_elective_parallel_stream_group(lesson) for lesson in lessons}
    parallels.discard("")
    return sorted(parallels)


def _elective_component_tag(lesson: dict[str, Any], *, shared_for_parallel_groups: bool) -> str:
    explicit = lesson.get("type")
    if explicit:
        return normalize_class_tag(str(explicit))
    if shared_for_parallel_groups:
        return "lec"
    return "class"


def _elective_sessions_from_lessons(
    lessons: list[dict[str, Any]],
    audience: list[str],
    instructors_map: dict[str, InstructorProfile],
    lookup: InstructorLookup,
) -> list[dict[str, Any]]:
    entries_by_key: dict[tuple[str, str, str, str | None, str | None], tuple[dict[str, Any], tuple[str, ...]]] = {}
    for lesson in lessons:
        teacher_ids = tuple(_register_elective_instructors(lesson, instructors_map, lookup))
        for occurrence in lesson.get("occurrences") or []:
            occ_date = occurrence.get("date")
            if not occ_date:
                continue
            key = (
                str(occ_date),
                str(occurrence.get("start_time") or ""),
                str(occurrence.get("end_time") or ""),
                occurrence.get("room"),
                occurrence.get("a1_range"),
            )
            if key not in entries_by_key:
                entries_by_key[key] = (occurrence, teacher_ids)

    sorted_entries = sorted(
        entries_by_key.values(),
        key=lambda item: (str(item[0].get("date")), str(item[0].get("start_time"))),
    )
    if not sorted_entries:
        return []

    occurrences: list[dict[str, Any]] = []
    for occurrence, teacher_ids in sorted_entries:
        entry: dict[str, Any] = {
            "date": str(occurrence["date"]),
            "start_time": str(occurrence.get("start_time") or "00:00"),
            "end_time": str(occurrence.get("end_time") or occurrence.get("start_time") or "00:00"),
        }
        room = occurrence.get("room")
        if room is not None and str(room).strip():
            entry["room"] = str(room).strip()
        if len(teacher_ids) == 1:
            entry["instructor"] = teacher_ids[0]
        elif len(teacher_ids) > 1:
            entry["instructor"] = list(teacher_ids)
        occurrences.append(entry)

    return [
        {
            "audience": list(audience),
            "occurrences": occurrences,
        }
    ]


def _elective_component_from_lessons(
    lessons: list[dict[str, Any]],
    student_groups: list[str],
    instructors_map: dict[str, InstructorProfile],
    lookup: InstructorLookup,
    *,
    shared_for_parallel_groups: bool,
) -> dict[str, Any] | None:
    if not lessons or not student_groups:
        return None
    duration_slots = max(_elective_duration_slots(lesson) for lesson in lessons)
    teacher_signatures = [
        tuple(_register_elective_instructors(lesson, instructors_map, lookup)) for lesson in lessons
    ]
    representative = lessons[0]
    sessions = _elective_sessions_from_lessons(lessons, student_groups, instructors_map, lookup)
    meeting_count = len(sessions[0]["occurrences"]) if sessions else 0
    cls: dict[str, Any] = {
        "tag": _elective_component_tag(representative, shared_for_parallel_groups=shared_for_parallel_groups),
        "student_groups": student_groups,
        "per_semester": meeting_count or max(len(lesson.get("occurrences") or []) for lesson in lessons),
        "instructor_pool": _elective_instructor_pool(teacher_signatures),
        "sessions": sessions,
    }
    if duration_slots != 1:
        cls["duration_slots"] = duration_slots
    return cls


def _group_elective_lessons_by_course(
    grouped_electives: list[dict[str, Any]],
) -> dict[str, list[dict[str, Any]]]:
    by_course: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for lesson in grouped_electives:
        course_name = normalize_lesson_name(_elective_subject(lesson))
        if not course_name or should_exclude_lesson(course_name):
            continue
        if not lesson.get("occurrences"):
            continue
        by_course[course_name].append(lesson)
    return by_course


def collect_elective_student_groups(grouped_electives: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_id: dict[str, dict[str, Any]] = {}
    for lessons in _group_elective_lessons_by_course(grouped_electives).values():
        alias = _elective_alias_token_for_lessons(lessons)
        parallels = _elective_parallel_groups_for_lessons(lessons)
        title = _elective_subject(lessons[0])
        if parallels:
            for parallel in parallels:
                group_id = elective_student_group_id(alias, parallel)
                by_id[group_id] = {
                    "code": group_id,
                    "name": f"{title} · {parallel}",
                    "kind": "elective",
                    "estimated_size": None,
                    "students": [],
                }
            continue

        group_id = elective_student_group_id(alias)
        students_number = lessons[0].get("students_number")
        estimated_size = int(students_number) if students_number is not None else None
        by_id[group_id] = {
            "code": group_id,
            "name": title,
            "kind": "elective",
            "estimated_size": estimated_size,
            "students": [],
        }
    return [by_id[group_id] for group_id in sorted(by_id)]


def append_summer_electives_to_sections(
    sections: list[dict[str, Any]],
    elective_group_ids: list[str],
) -> list[dict[str, Any]]:
    if not elective_group_ids:
        return sections
    updated = deepcopy(sections)
    electives_section: dict[str, Any] | None = None
    for section in updated:
        if section.get("code") == "electives":
            electives_section = section
            break
    if electives_section is None:
        electives_section = {
            "code": "electives",
            "name": "Элективы",
            "kind": "electives",
            "programs": [],
        }
        updated.append(electives_section)
    programs = list(electives_section.get("programs") or [])
    programs.append(
        {
            "code": "SUM26",
            "name": "Summer 2026",
            "kind": "elective_bucket",
            "groups": sorted(elective_group_ids),
        }
    )
    electives_section["programs"] = programs
    return updated


def _pattern_key_from_row(row: dict[str, Any]) -> PatternKey:
    class_tag = normalize_class_tag(row["lesson_class_type"])
    room = str(row.get("room") or "")
    # Per-group labs use different rooms/times per audience; room belongs on each
    # session slot, not in the aggregation key (otherwise one component per room).
    if class_tag == "lab":
        room = ""
    return PatternKey(
        course=normalize_lesson_name(row["lesson_name"]),
        class_tag=class_tag,
        room=str(room or ""),
        start_date="",
        end_date="",
        stream_group="",
        sheet_scope="",
    )


def resolve_data_path(path: Path, *search_dirs: Path) -> Path:
    if path.is_absolute() and path.exists():
        return path
    if path.exists():
        return path.resolve()
    for directory in search_dirs:
        candidate = directory / path
        if candidate.exists():
            return candidate.resolve()
    return path


def _load_yaml_list(path: Path, label: str) -> list[dict[str, Any]]:
    if not path.exists():
        raise FileNotFoundError(f"{label} not found: {path}")
    if path.suffix.lower() not in {".yaml", ".yml"}:
        raise ValueError(f"{label} must be a YAML file: {path}")
    payload = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(payload, list):
        raise ValueError(f"{label} must be a YAML list: {path}")
    return payload


def load_grouped_elective_lessons(path: Path) -> list[dict[str, Any]]:
    payload = _load_yaml_list(path, "Electives lessons")
    for index, item in enumerate(payload):
        if not isinstance(item, dict):
            raise ValueError(f"Electives lessons entry {index} must be an object")
        for key in ("subject", "audience", "occurrences", "spreadsheet_id", "google_sheet_gid", "google_sheet_name"):
            if key not in item:
                raise ValueError(f"Electives lessons entry {index} missing required field: {key}")
    return payload


def resolve_lessons_search_dirs(input_path: Path) -> tuple[Path, ...]:
    dirs: list[Path] = []
    for directory in (input_path.parent, Path.cwd(), SCRIPT_DIR):
        resolved = directory.resolve()
        if resolved not in dirs:
            dirs.append(resolved)
    return tuple(dirs)


def _row_instructor(row: dict[str, Any]) -> str | None:
    return row.get("instructor")


def _row_audience(row: dict[str, Any]) -> list[str]:
    return normalize_group_names(row["audience"])


def _elective_subject(row: dict[str, Any]) -> str:
    return str(row["subject"]).strip()


def expand_grouped_core_courses_to_rows(payload: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for course in payload:
        cohort = course.get("cohort")
        subject = course["subject"]
        parent_fields = {
            "course_name": cohort,
            "spreadsheet_id": course["spreadsheet_id"],
            "google_sheet_gid": course["google_sheet_gid"],
            "google_sheet_name": course["google_sheet_name"],
            "start_date": course.get("start_date"),
            "end_date": course.get("end_date"),
        }
        for component in course["components"]:
            audience = _row_audience(component)
            rows.append(
                {
                    "lesson_name": subject,
                    "lesson_class_type": component.get("type"),
                    "weekday": component["weekday"],
                    "start_time": component["start_time"],
                    "end_time": component["end_time"],
                    "room": component.get("room"),
                    "teacher": _row_instructor(component),
                    "group_name": audience,
                    "students_number": component.get("students_number"),
                    "modifiers": component.get("modifiers"),
                    "a1_range": component.get("a1_range"),
                    **parent_fields,
                }
            )
    return rows


def load_core_courses_file(path: Path) -> list[dict[str, Any]]:
    payload = _load_yaml_list(path, "Core courses lessons")
    for index, item in enumerate(payload):
        if not isinstance(item, dict):
            raise ValueError(f"Core courses entry {index} must be an object")
        for key in ("subject", "components", "spreadsheet_id", "google_sheet_gid", "google_sheet_name"):
            if key not in item:
                raise ValueError(f"Core courses entry {index} missing required field: {key}")
        if not item["components"]:
            raise ValueError(f"Core courses entry {index} has no components")
        for comp_index, component in enumerate(item["components"]):
            if not isinstance(component, dict):
                raise ValueError(f"Core courses entry {index} component {comp_index} must be an object")
            for key in ("weekday", "start_time", "end_time", "audience"):
                if key not in component:
                    raise ValueError(
                        f"Core courses entry {index} component {comp_index} missing required field: {key}"
                    )
    return expand_grouped_core_courses_to_rows(payload)


def _elective_occurrence_dates(lesson: dict[str, Any]) -> list[date]:
    dates: list[date] = []
    for occurrence in lesson.get("occurrences") or []:
        raw = occurrence.get("date")
        if raw:
            dates.append(date.fromisoformat(str(raw)))
    return dates


def _elective_duration_slots(lesson: dict[str, Any]) -> int:
    max_slots = 1
    for occurrence in lesson.get("occurrences") or []:
        start_raw = occurrence.get("start_time")
        end_raw = occurrence.get("end_time")
        if not start_raw or not end_raw:
            continue
        delta = datetime.strptime(normalize_time(str(end_raw)), "%H:%M") - datetime.strptime(
            normalize_time(str(start_raw)), "%H:%M"
        )
        duration_minutes = abs(int(delta.total_seconds())) // 60
        max_slots = max(max_slots, max(1, round(duration_minutes / 90)))
    return max_slots


def _register_elective_instructors(
    lesson: dict[str, Any],
    instructors_map: dict[str, InstructorProfile],
    lookup: InstructorLookup,
) -> list[str]:
    teacher_names = split_teacher_names(_row_instructor(lesson))
    return list(_teacher_signature(teacher_names, instructors_map, lookup))


def _elective_instructor_pool(teacher_id_sets: list[tuple[str, ...]]) -> list[str] | list[list[str]]:
    unique_signatures = sorted({sig for sig in teacher_id_sets if sig})
    if not unique_signatures:
        return []
    if len(unique_signatures) == 1:
        sig = unique_signatures[0]
        if len(sig) == 1:
            return [sig[0]]
        return [list(sig)]
    if all(len(sig) == 1 for sig in unique_signatures):
        return sorted({sig[0] for sig in unique_signatures})
    return [list(sig) for sig in unique_signatures]


def merge_elective_courses(
    courses_map: dict[str, list[dict[str, Any]]],
    course_is_elective: dict[str, bool],
    grouped_electives: list[dict[str, Any]],
    instructors_map: dict[str, InstructorProfile],
    lookup: InstructorLookup,
) -> None:
    tag_order = {"lec": 0, "tut": 1, "lab": 2, "class": 3}
    by_course = _group_elective_lessons_by_course(grouped_electives)

    for course_name, lessons in by_course.items():
        alias = _elective_alias_token_for_lessons(lessons)
        parallels = _elective_parallel_groups_for_lessons(lessons)
        shared_lessons = [lesson for lesson in lessons if not _elective_parallel_stream_group(lesson)]
        parallel_lessons: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for lesson in lessons:
            parallel = _elective_parallel_stream_group(lesson)
            if parallel:
                parallel_lessons[parallel].append(lesson)

        if parallels:
            audience = [elective_student_group_id(alias, parallel) for parallel in parallels]
            if shared_lessons:
                shared_cls = _elective_component_from_lessons(
                    shared_lessons,
                    audience,
                    instructors_map,
                    lookup,
                    shared_for_parallel_groups=True,
                )
                if shared_cls:
                    courses_map[course_name].append(shared_cls)
            for parallel in parallels:
                parallel_cls = _elective_component_from_lessons(
                    parallel_lessons[parallel],
                    [elective_student_group_id(alias, parallel)],
                    instructors_map,
                    lookup,
                    shared_for_parallel_groups=False,
                )
                if parallel_cls:
                    courses_map[course_name].append(parallel_cls)
        else:
            single_cls = _elective_component_from_lessons(
                lessons,
                [elective_student_group_id(alias)],
                instructors_map,
                lookup,
                shared_for_parallel_groups=False,
            )
            if single_cls:
                courses_map[course_name].append(single_cls)

        course_is_elective[course_name] = True

    for course_name, components in courses_map.items():
        if not course_is_elective.get(course_name):
            continue
        courses_map[course_name] = sorted(
            components,
            key=lambda cls: (
                tag_order.get(cls.get("tag", ""), 99),
                tuple(cls.get("student_groups") or []),
            ),
        )


def detect_block_key(google_sheet_name: str | None) -> str | None:
    if not google_sheet_name:
        return None
    lowered = google_sheet_name.strip().lower()
    if "1st block" in lowered:
        return "block1"
    if "2nd block" in lowered:
        return "block2"
    return None


def output_path_for_block(base_output: Path, block_key: str) -> Path:
    return base_output.with_name(f"{base_output.stem}-{block_key}{base_output.suffix}")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Convert core-courses and elective lessons YAML into config-candidate.yaml"
    )
    parser.add_argument(
        "core_courses_yaml",
        type=Path,
        nargs="?",
        default=DEFAULT_CORE_COURSES_YAML,
        help=f"Grouped core courses lessons YAML (default: {DEFAULT_CORE_COURSES_YAML.name})",
    )
    parser.add_argument(
        "electives_yaml",
        type=Path,
        nargs="?",
        default=DEFAULT_ELECTIVES_YAML,
        help=f"Grouped electives lessons YAML (default: {DEFAULT_ELECTIVES_YAML.name})",
    )
    parser.add_argument("output_yaml", type=Path, nargs="?", default=Path("config-candidate.yaml"))
    parser.add_argument(
        "--english-distribution-xlsx",
        type=Path,
        default=Path("Foreign language.xlsx"),
        help="Path to Foreign language distribution XLSX",
    )
    parser.add_argument(
        "--rooms-json",
        type=Path,
        default=Path("rooms.json"),
        help="Path to rooms JSON export",
    )
    parser.add_argument(
        "--predefined-json",
        type=Path,
        default=Path("predefined.json"),
        help="Path to predefined.json with academic_groups students",
    )
    parser.add_argument(
        "--users-csv",
        type=Path,
        default=None,
        help="Staff directory CSV (displayName -> email as instructor id). "
        "If omitted, loads People.25-26.csv and/or exportUsers_2026-4-14.csv from the project dir.",
    )
    args = parser.parse_args()

    search_dirs = resolve_lessons_search_dirs(args.core_courses_yaml)
    core_courses_path = resolve_data_path(args.core_courses_yaml, *search_dirs)
    electives_path = resolve_data_path(args.electives_yaml, *search_dirs)

    rows: list[dict[str, Any]] = load_core_courses_file(core_courses_path)
    grouped_elective_lessons = load_grouped_elective_lessons(electives_path)
    if not rows and not grouped_elective_lessons:
        raise ValueError("Core courses input is empty")
    block_rows: dict[str, list[dict[str, Any]]] = {"block1": [], "block2": []}
    unclassified_rows: list[dict[str, Any]] = []
    for row in rows:
        block_key = detect_block_key(row.get("google_sheet_name"))
        if block_key in block_rows:
            block_rows[block_key].append(row)
        else:
            unclassified_rows.append(row)

    distribution_path = resolve_data_path(args.english_distribution_xlsx, *search_dirs)
    rooms_json_path = resolve_data_path(args.rooms_json, *search_dirs)
    predefined_json_path = resolve_data_path(args.predefined_json, *search_dirs)
    users_csv_paths = resolve_users_csv_paths(
        args.users_csv,
        core_courses_path.parent,
        Path.cwd(),
    )
    instructor_lookup = load_instructor_lookup(users_csv_paths)
    rooms = load_rooms(rooms_json_path)

    (
        shared_groups,
        english_slot_instr_map,
        english_slot_map,
        english_group_instructors,
        english_group_per_week,
    ) = load_english_distribution(distribution_path)
    programs = attach_english_to_programs(PROGRAMS, shared_groups)
    academic_groups = collect_academic_groups(PROGRAMS)
    academic_groups = enrich_academic_groups_from_predefined(academic_groups, predefined_json_path)
    summer_elective_student_groups = collect_elective_student_groups(grouped_elective_lessons)
    summer_elective_group_ids = [str(group["code"]) for group in summer_elective_student_groups if group.get("code")]
    sections = build_sections(PROGRAMS, shared_groups, set())
    sections = append_summer_electives_to_sections(sections, summer_elective_group_ids)
    students_groups = build_students_groups(academic_groups, shared_groups, set())
    students_groups.extend(summer_elective_student_groups)

    instructors_map: dict[str, InstructorProfile] = {}
    seed_instructors_from_people_roster(instructors_map, instructor_lookup)
    aggregated: dict[PatternKey, dict[str, Any]] = {}

    for r in rows:
        course = normalize_lesson_name(r["lesson_name"])
        if should_exclude_lesson(course):
            continue
        teacher_names = split_teacher_names(r["teacher"])
        teacher_signature = _teacher_signature(teacher_names, instructors_map, instructor_lookup)
        key = _pattern_key_from_row(r)
        if key not in aggregated:
            aggregated[key] = {
                "groups": set(),
                "raw_groups": set(),
                "teacher_signatures": set(),
                "groups_by_signature": defaultdict(set),
                "slots_by_signature": defaultdict(lambda: defaultdict(set)),
                "occurrences_by_signature": defaultdict(lambda: defaultdict(list)),
                "edits_by_slot": defaultdict(list),
                "duration_slots": 1,
                "slots_by_group": defaultdict(set),
                "shared_group_batches": set(),
                "is_elective": False,
            }
        aggregated[key]["teacher_signatures"].add(teacher_signature)

        groups = normalize_group_names(r["group_name"])
        if len(groups) > 1:
            aggregated[key]["shared_group_batches"].add(frozenset(groups))
        aggregated[key]["raw_groups"].update(groups)
        if is_english_lesson(course):
            day = Weekday(r["weekday"]).value
            start = normalize_time(r["start_time"])
            matched_groups: set[str] = set()
            for instructor_name in teacher_names:
                matched_groups.update(english_slot_instr_map.get((day, start, instructor_name), set()))
            if not matched_groups:
                matched_groups.update(english_slot_map.get((day, start), set()))
            if matched_groups:
                groups = sorted(matched_groups)
        aggregated[key]["groups_by_signature"][teacher_signature].update(groups)
        aggregated[key]["groups"].update(groups)
        modifiers = r.get("modifiers") or {}
        if _is_occurrence_only_modifier(modifiers):
            for occurrence in _core_occurrences_from_row(r, teacher_signature):
                for group_id in groups:
                    aggregated[key]["occurrences_by_signature"][teacher_signature][group_id].append(occurrence)
        elif _is_weekly_with_nest_modifier(modifiers):
            slot_sig: WeeklySlotSig = (
                r["weekday"],
                normalize_time(r["start_time"]),
                normalize_time(r["end_time"]),
                _normalize_room_value(r.get("room")),
                teacher_signature,
            )
            for group_id in groups:
                aggregated[key]["slots_by_group"][group_id].add(slot_sig)
                aggregated[key]["slots_by_signature"][teacher_signature][group_id].add(slot_sig)
            aggregated[key]["edits_by_slot"][slot_sig].extend(_nest_edits_from_modifiers(modifiers))
        else:
            slot_sig = (
                r["weekday"],
                normalize_time(r["start_time"]),
                normalize_time(r["end_time"]),
                _normalize_room_value(r.get("room")),
                teacher_signature,
            )
            for group_id in groups:
                aggregated[key]["slots_by_group"][group_id].add(slot_sig)
                aggregated[key]["slots_by_signature"][teacher_signature][group_id].add(slot_sig)

        duration_minutes = (
            datetime.strptime(normalize_time(r["end_time"]), "%H:%M")
            - datetime.strptime(normalize_time(r["start_time"]), "%H:%M")
        ).seconds // 60
        aggregated[key]["duration_slots"] = max(aggregated[key]["duration_slots"], 1, round(duration_minutes / 90))
    selector_map = build_group_selectors(programs)
    group_order = build_group_order_from_sections(sections)

    def render_config(selected_rows: list[dict[str, Any]]) -> dict[str, Any]:
        term_starts = [r["start_date"] for r in selected_rows if r.get("start_date")]
        term_ends = [r["end_date"] for r in selected_rows if r.get("end_date")]
        for lesson in grouped_elective_lessons:
            dates = _elective_occurrence_dates(lesson)
            if dates:
                term_starts.append(min(dates).isoformat())
                term_ends.append(max(dates).isoformat())
        if not term_starts or not term_ends:
            raise ValueError("Cannot determine term dates from input rows or electives")
        global_start = min(term_starts)
        global_end = max(term_ends)
        selected_keys = {
            _pattern_key_from_row(row)
            for row in selected_rows
            if not should_exclude_lesson(normalize_lesson_name(row["lesson_name"]))
        }

        courses_map: dict[str, list[dict[str, Any]]] = defaultdict(list)
        course_is_elective: dict[str, bool] = defaultdict(bool)
        tag_order = {"lec": 0, "tut": 1, "lab": 2, "class": 3}
        for pattern, data in sorted(
            aggregated.items(),
            key=lambda x: (
                x[0].course,
                tag_order.get(x[0].class_tag, 99),
                x[0].class_tag,
            ),
        ):
            if pattern not in selected_keys:
                continue
            if data.get("is_elective"):
                course_is_elective[pattern.course] = True
            teacher_signatures = sorted(data["teacher_signatures"])

            # Build emission variants:
            # 1) split lec/tut by teacher signatures when they differ;
            # 2) additionally split lec/tut by audience slot clusters when one teacher
            #    teaches different audiences in different recurring slots.
            if pattern.class_tag in {"lec", "tut"} and len(teacher_signatures) > 1:
                base_variants = []
                for signature in teacher_signatures:
                    base_variants.append(
                        (
                            sorted(data["groups_by_signature"][signature]),
                            data["slots_by_signature"][signature],
                            [signature],
                        )
                    )
            else:
                base_variants = [
                    (
                        sorted(data["groups"]),
                        data["slots_by_group"],
                        teacher_signatures,
                    )
                ]

            emission_variants: list[tuple[list[str], Any, list[tuple[str, ...]]]] = []
            for groups_for_cls, slots_source, signatures_for_pool in base_variants:
                if pattern.class_tag in {"lec", "tut"}:
                    groups_by_slot_fingerprint: dict[tuple[WeeklySlotSig, ...], list[str]] = defaultdict(list)
                    for gid in groups_for_cls:
                        slot_fingerprint = tuple(sorted(slots_source.get(gid, set())))
                        groups_by_slot_fingerprint[slot_fingerprint].append(gid)
                    if len(groups_by_slot_fingerprint) > 1:
                        for cluster_groups in groups_by_slot_fingerprint.values():
                            filtered_slots = {gid: slots_source[gid] for gid in cluster_groups if gid in slots_source}
                            emission_variants.append((sorted(cluster_groups), filtered_slots, signatures_for_pool))
                        continue
                emission_variants.append((groups_for_cls, slots_source, signatures_for_pool))

            for groups_for_cls, slots_source, signatures_for_pool in emission_variants:

                cls = {
                    "tag": pattern.class_tag,
                    "student_groups": compress_groups_to_selectors(groups_for_cls, selector_map, group_order),
                }
                per_week = max((len(slots) for slots in slots_source.values()), default=1)
                if per_week != 1:
                    cls["per_week"] = per_week
                if pattern.course == "Nature Inspired Computing":
                    # Project-specific override: keep this course at one meeting/week
                    # for each emitted component stream.
                    cls.pop("per_week", None)
                if data["duration_slots"] != 1:
                    cls["duration_slots"] = data["duration_slots"]
                if is_english_lesson(pattern.course):
                    cls["instructor_pool"] = sorted(
                        sig[0] for sig in signatures_for_pool if sig
                    )
                else:
                    cls["instructor_pool"] = _build_instructor_pool(signatures_for_pool)
                is_shared_lesson = frozenset(groups_for_cls) in data.get("shared_group_batches", set())
                if infer_per_group(
                    pattern.class_tag,
                    cls["student_groups"],
                    is_english_course=is_english_lesson(pattern.course),
                    source_group_count=len(groups_for_cls),
                    is_shared_lesson=is_shared_lesson,
                ):
                    cls["per_group"] = True
                if is_english_lesson(pattern.course) and cls.get("tag") == "class":
                    # Emit one class per concrete student group (ENG-* from XLSX
                    # when slot+teacher matches were found).
                    direct_groups = sorted(data["groups"])
                    if direct_groups:
                        for token in direct_groups:
                            split_cls = deepcopy(cls)
                            split_cls["student_groups"] = [token]
                            xlsx_per_week = english_group_per_week.get(token)
                            if xlsx_per_week and xlsx_per_week > 0:
                                if xlsx_per_week == 1:
                                    split_cls.pop("per_week", None)
                                else:
                                    split_cls["per_week"] = xlsx_per_week
                            xlsx_instructors = english_group_instructors.get(token, set())
                            if xlsx_instructors:
                                split_cls["instructor_pool"] = sorted(
                                    register_instructor(name, instructors_map, instructor_lookup)
                                    for name in xlsx_instructors
                                )
                                courses_map[pattern.course].append(split_cls)
                                continue
                            # Narrow teacher pool to instructors that actually teach
                            # this specific foreign-language group.
                            group_signatures = [
                                sig for sig in teacher_signatures if token in data["groups_by_signature"].get(sig, set())
                            ]
                            if group_signatures:
                                split_cls["instructor_pool"] = sorted(
                                    name for sig in group_signatures for name in sig
                                )
                            courses_map[pattern.course].append(split_cls)
                        continue
                if not data.get("is_elective") and not is_english_lesson(pattern.course):
                    sessions = _core_sessions_from_slots(
                        groups_for_cls,
                        slots_source,
                        cls["student_groups"],
                        per_group=bool(cls.get("per_group")),
                        occurrences_by_signature=data["occurrences_by_signature"],
                        signatures_for_pool=signatures_for_pool,
                        edits_by_slot=data["edits_by_slot"],
                    )
                    if sessions:
                        cls["sessions"] = sessions
                courses_map[pattern.course].append(cls)

        merge_elective_courses(
            courses_map,
            course_is_elective,
            grouped_elective_lessons,
            instructors_map,
            instructor_lookup,
        )

        for course_name, components in courses_map.items():
            courses_map[course_name] = sorted(
                components,
                key=lambda cls: (
                    tag_order.get(cls.get("tag", ""), 99),
                    class_group_rank(cls, selector_map, group_order),
                ),
            )

        # Generic relation mapping: connect each tutorial to the best matching lecture.
        for components in courses_map.values():
            lecture_indices = [idx for idx, cls in enumerate(components) if cls.get("tag") == "lec"]
            tutorial_indices = [idx for idx, cls in enumerate(components) if cls.get("tag") == "tut"]
            if not lecture_indices or not tutorial_indices:
                continue
            # Keep config compact: only emit relates_to for ambiguous multi-stream cases.
            if len(lecture_indices) <= 1 and len(tutorial_indices) <= 1:
                continue

            def _as_set(value: Any) -> set[str]:
                if not isinstance(value, list):
                    return set()
                return {str(item) for item in value if isinstance(item, str)}

            def _flatten_instructors(value: Any) -> set[str]:
                if not isinstance(value, list):
                    return set()
                flat: set[str] = set()
                for item in value:
                    if isinstance(item, str):
                        flat.add(item)
                    elif isinstance(item, list):
                        flat.update(str(v) for v in item if isinstance(v, str))
                return flat

            for tut_idx, tut_cls in enumerate(components):
                if tut_cls.get("tag") != "tut" or tut_cls.get("relates_to") is not None:
                    continue

                tut_groups = _as_set(tut_cls.get("student_groups"))
                tut_instructors = _flatten_instructors(tut_cls.get("instructor_pool"))
                best: tuple[tuple[int, int, int, int], int] | None = None

                for lec_idx in lecture_indices:
                    lec_cls = components[lec_idx]
                    lec_groups = _as_set(lec_cls.get("student_groups"))
                    lec_instructors = _flatten_instructors(lec_cls.get("instructor_pool"))
                    group_overlap = len(tut_groups & lec_groups)
                    instructor_overlap = len(tut_instructors & lec_instructors)
                    if group_overlap == 0 and instructor_overlap == 0:
                        continue

                    score = (
                        group_overlap,
                        instructor_overlap,
                        1 if lec_idx < tut_idx else 0,
                        -abs(tut_idx - lec_idx),
                    )
                    if best is None or score > best[0]:
                        best = (score, lec_idx)

                if best is not None:
                    tut_cls["relates_to"] = best[1]

        # Split lab streams across multiple lecture streams and emit relates_to.
        # This is important for courses like Nature Inspired Computing where each
        # lab audience corresponds to a specific lecture audience.
        for course_name, components in list(courses_map.items()):
            lecture_indices = [idx for idx, cls in enumerate(components) if cls.get("tag") == "lec"]
            if len(lecture_indices) <= 1:
                continue

            def _as_set(value: Any) -> set[str]:
                if not isinstance(value, list):
                    return set()
                return {str(item) for item in value if isinstance(item, str)}

            rebuilt: list[dict[str, Any]] = []
            for idx, cls in enumerate(components):
                if cls.get("tag") != "lab" or cls.get("relates_to") is not None:
                    rebuilt.append(cls)
                    continue

                lab_groups = _as_set(cls.get("student_groups"))
                if not lab_groups:
                    rebuilt.append(cls)
                    continue

                overlaps: list[tuple[int, set[str]]] = []
                for lec_idx in lecture_indices:
                    lec_groups = _as_set(components[lec_idx].get("student_groups"))
                    overlap = lab_groups & lec_groups
                    if overlap:
                        overlaps.append((lec_idx, overlap))

                if len(overlaps) <= 1:
                    rebuilt.append(cls)
                    continue

                union_overlap: set[str] = set()
                for _lec_idx, overlap in overlaps:
                    union_overlap.update(overlap)
                if union_overlap != lab_groups:
                    rebuilt.append(cls)
                    continue

                # Build one lab component per matched lecture stream.
                for lec_idx, overlap in overlaps:
                    split_cls = deepcopy(cls)
                    split_cls["student_groups"] = sorted(overlap)
                    split_cls["relates_to"] = lec_idx
                    rebuilt.append(split_cls)

            courses_map[course_name] = rebuilt

        instructors = [
            profile.to_config_dict()
            for profile in sorted(instructors_map.values(), key=lambda item: item.preferred_name().casefold())
        ]

        course_entries: list[tuple[bool, str, dict[str, Any]]] = []
        for course_name, components in courses_map.items():
            direct_group_tokens = {
                token
                for component in components
                for token in component.get("student_groups", [])
                if isinstance(token, str) and not token.startswith("@")
            }
            is_elective_course = course_is_elective.get(course_name, False) or any(
                str(token).startswith(f"{SUMMER_ELECTIVE_TERM_PREFIX}-")
                for component in components
                for token in component.get("student_groups", [])
                if isinstance(token, str)
            )
            course_entries.append(
                (
                    is_elective_course,
                    course_name,
                    {
                        "name": course_name,
                        "course_tags": infer_course_tags(course_name, is_elective_course=is_elective_course),
                        "components": components,
                    },
                )
            )
        course_items = [
            item
            for _, _, item in sorted(
                course_entries,
                key=lambda entry: (
                    entry[0],
                    course_group_rank(entry[2], selector_map, group_order),
                    entry[1].casefold(),
                ),
            )
        ]

        return {
            "term": {
                "name": "Spring 2026",
                "semester": {"start_date": global_start, "end_date": global_end},
            },
            "rooms": rooms,
            "instructors": instructors,
            "sections": sections,
            "students_groups": students_groups,
            "courses": course_items,
        }

    if block_rows["block1"] and block_rows["block2"]:
        selected_by_block = {
            "block1": block_rows["block1"] + unclassified_rows,
            "block2": block_rows["block2"] + unclassified_rows,
        }
        for block_key, selected_rows in selected_by_block.items():
            if not selected_rows:
                continue
            output_path = output_path_for_block(args.output_yaml, block_key)
            styled_config = apply_yaml_style_overrides(render_config(selected_rows))
            output_path.write_text(dump_config_yaml(styled_config), encoding="utf-8")
            print(f"Wrote {output_path}")
    else:
        styled_config = apply_yaml_style_overrides(render_config(rows))
        args.output_yaml.write_text(dump_config_yaml(styled_config), encoding="utf-8")
        print(f"Wrote {args.output_yaml}")


if __name__ == "__main__":
    main()
