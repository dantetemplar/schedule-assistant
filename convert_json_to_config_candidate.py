from __future__ import annotations

import argparse
import json
import re
from collections import defaultdict
from copy import deepcopy
from dataclasses import dataclass
from datetime import date, datetime, time
from pathlib import Path
from typing import Any

import yaml

from config import InstructorConfig, Weekday
from instructors_roster import (
    DEFAULT_INSTRUCTORS_YAML,
    InstructorRegistry,
    collapse_duplicate_instructors,
    load_instructor_lookup,
    remap_instructor_ids_in_obj,
    resolve_users_csv_paths,
)

TERM_NAME = "Fall 2026"
TERM_START = date(2026, 8, 24)
TERM_END = date(2026, 12, 27)

DEFAULT_PARSE_CORE_COURSES_YAML = Path("parse-core-courses.yaml")

# Fallback when parse-core overrides are absent.
PROGRAM_SEMESTER: dict[str, tuple[date, date]] = {
    "BS_Y1_EN": (date(2026, 9, 1), TERM_END),
    "BS_Y1_RU": (date(2026, 9, 1), TERM_END),
    "BS_Y2_EN": (date(2026, 8, 31), TERM_END),
    "BS_Y2_RU": (date(2026, 8, 31), TERM_END),
    "BS_Y3_EN": (date(2026, 8, 24), TERM_END),
    "BS_Y3_RU": (date(2026, 8, 24), TERM_END),
}

# Default term grid (must match schedule_config TermConfig.time_slots defaults).
TERM_TIME_SLOTS: list[tuple[str, str]] = [
    ("09:00", "10:30"),
    ("10:40", "12:10"),
    ("12:40", "14:10"),
    ("14:20", "15:50"),
    ("16:00", "17:30"),
    ("17:40", "19:10"),
    ("19:20", "20:50"),
]
TERM_TIME_SLOT_STARTS = {start for start, _ in TERM_TIME_SLOTS}
TERM_END_BY_START = {start: end for start, end in TERM_TIME_SLOTS}

DEFAULT_INSTRUCTOR_POSITIONS = [
    "Full Professor",
    "Associate Professor",
    "Assistant Professor",
    "Senior Instructor",
    "Instructor",
    "Teaching Assistant",
    "Teaching Assistant Intern",
    "Visiting",
]

DEFAULT_COURSE_INSTRUCTOR_ROLES = [
    "Primary Instructor",
    "Secondary Instructor",
    "Teaching Assistant",
]

DEFAULT_COURSE_COMPONENT_TAGS = [
    "lec",
    "tut",
    "lab",
    "class",
]

DEFAULT_ROOM_ATTRIBUTES = [
    {
        "key": "Слайды",
        "type": "string",
        "hint": "Проектор / телевизор / экран",
        "enum_values": [],
    },
    {
        "key": "Доски",
        "type": "string",
        "hint": "Маркерные и передвижные доски",
        "enum_values": [],
    },
    {
        "key": "Розетки",
        "type": "string",
        "hint": "Наличие и количество розеток",
        "enum_values": [],
    },
    {
        "key": "Мебель",
        "type": "string",
        "hint": "Столы, стулья и прочая мебель",
        "enum_values": [],
    },
    {
        "key": "Заметка",
        "type": "string",
        "hint": "Особая заметка (прозрачная, многоярусная, …)",
        "enum_values": [],
    },
]

DEFAULT_ROOM_SURVEY_FEATURES_JSON = Path("room_survey_features.json")
DEFAULT_ROOM_SURVEY_PDF = Path.home() / "Downloads" / "Аудитории ИУ.pdf"

_ROOM_SURVEY_FIELD_KEYS = ("Слайды", "Доски", "Вместимость", "Розетки")
_ROOM_SURVEY_FEATURE_MAP = {
    "Слайды": "Слайды",
    "Доски": "Доски",
    "Розетки": "Розетки",
    "Вместимость": "Мебель",
}

# Roster title aliases → canonical position (case-insensitive match).
POSITION_ALIASES: dict[str, str] = {
    "professor": "Full Professor",
    "full professor": "Full Professor",
    "docent": "Associate Professor",
    "associate professor": "Associate Professor",
    "professor docent": "Associate Professor",
    "assistant professor": "Assistant Professor",
    "senior instructor": "Senior Instructor",
    "instructor": "Instructor",
    "visiting": "Visiting",
    # IU HR "assistant" / "TA" → Teaching Assistant (not Assistant Professor).
    "assistant": "Teaching Assistant",
    "ta": "Teaching Assistant",
    "teacher assistant": "Teaching Assistant",
    "teaching assistant": "Teaching Assistant",
    "ta intern": "Teaching Assistant Intern",
    "teacher assistant intern": "Teaching Assistant Intern",
    "teaching assistant intern": "Teaching Assistant Intern",
}

# Component tag → subject role; lower rank wins when an instructor teaches several tags.
COURSE_ROLE_BY_COMPONENT_TAG: dict[str, tuple[int, str]] = {
    "lec": (0, "Primary Instructor"),
    "lecture": (0, "Primary Instructor"),
    "tut": (1, "Secondary Instructor"),
    "tutorial": (1, "Secondary Instructor"),
    "lab": (2, "Teaching Assistant"),
    "laboratory": (2, "Teaching Assistant"),
}

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
DEFAULT_CORE_COURSES_YAML = Path("core-courses-lessons-fall-2026.yaml")
DEFAULT_ELECTIVES_YAML = Path("electives-lessons-fall-2026.yaml")

# Temporary: emit elective subjects/components without weekly_pattern / dates_pattern.
SKIP_ELECTIVE_PATTERNS_AND_OCCURRENCES = True


def _normalize_instructor_position(position: str | None) -> str | None:
    raw = (position or "").strip()
    if not raw or raw in {"?", "-"}:
        return None
    canonical = POSITION_ALIASES.get(raw.casefold())
    if canonical is not None:
        return canonical
    if raw in DEFAULT_INSTRUCTOR_POSITIONS:
        return raw
    return None


def _iter_pool_instructor_ids(pool: list[Any]) -> list[str]:
    ids: list[str] = []
    for item in pool:
        if isinstance(item, str) and item.strip():
            ids.append(item.strip())
        elif isinstance(item, list):
            for nested in item:
                if isinstance(nested, str) and nested.strip():
                    ids.append(nested.strip())
    return ids


def _derive_course_instructors(
    components: list[dict[str, Any]],
) -> list[dict[str, str]]:
    best_by_id: dict[str, tuple[int, str]] = {}
    for component in components:
        tag = str(component.get("tag") or "").strip().casefold()
        role_info = COURSE_ROLE_BY_COMPONENT_TAG.get(tag)
        if role_info is None:
            continue
        rank, role = role_info
        for instructor_id in _iter_pool_instructor_ids(
            list(component.get("instructor_pool") or [])
        ):
            current = best_by_id.get(instructor_id)
            if current is None or rank < current[0]:
                best_by_id[instructor_id] = (rank, role)
    return [
        {"id": instructor_id, "role": role}
        for instructor_id, (_rank, role) in sorted(
            best_by_id.items(),
            key=lambda item: (item[1][0], item[0].casefold()),
        )
    ]


def _normalize_output_instructors(
    instructors: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    normalized: list[dict[str, Any]] = []
    for instructor in instructors:
        entry = dict(instructor)
        position = _normalize_instructor_position(entry.get("position"))
        if position:
            entry["position"] = position
        else:
            entry.pop("position", None)
        normalized.append(entry)
    return normalized


def _group_entry_code(entry: Any) -> str:
    if isinstance(entry, str):
        return entry.strip()
    if isinstance(entry, dict):
        return str(entry.get("code") or entry.get("id") or "").strip()
    return ""


# Fall 2026 programs — groups referenced in core-courses-lessons-fall-2026.yaml.
PROGRAMS: dict[str, list[dict[str, Any]]] = {
    "bachelor": [
        {
            "code": "BS_Y1_EN",
            "name": "BS - Year 1 (EN)",
            "tracks": [
                {
                    "name": "Computer Science and Engineering",
                    "code": "CSE",
                    "groups": [
                        "B26-CSE-01",
                        "B26-CSE-02",
                        "B26-CSE-03",
                        "B26-CSE-04",
                        "B26-CSE-05",
                    ],
                },
                {
                    "name": "Data Science and Artificial Intelligence",
                    "code": "DSAI",
                    "groups": [
                        "B26-DSAI-01",
                        "B26-DSAI-02",
                        "B26-DSAI-03",
                        "B26-DSAI-04",
                        "B26-DSAI-05",
                        "B26-DSAI-06",
                    ],
                },
            ],
        },
        {
            "code": "BS_Y1_RU",
            "name": "BS - Year 1 (RU)",
            "tracks": [
                {
                    "name": "MFAI",
                    "code": "MFAI",
                    "groups": [
                        "B26-MFAI-01",
                        "B26-MFAI-02",
                        "B26-MFAI-03",
                        "B26-MFAI-04",
                        "B26-MFAI-05",
                        "B26-MFAI-06",
                        "B26-MFAI-07",
                    ],
                },
                {
                    "name": "Robotics",
                    "code": "RO",
                    "groups": ["B26-RO-01"],
                },
                {
                    "name": "AI360",
                    "code": "AI360",
                    "groups": ["B26-AI360-01"],
                },
            ],
        },
        {
            "code": "BS_Y2_EN",
            "name": "BS - Year 2 (EN)",
            "tracks": [
                {
                    "name": "Computer Science and Engineering",
                    "code": "CSE",
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
            "code": "BS_Y2_RU",
            "name": "BS - Year 2 (RU)",
            "tracks": [
                {
                    "name": "MFAI",
                    "code": "MFAI",
                    "groups": [
                        "B25-MFAI-01",
                        "B25-MFAI-02",
                        "B25-MFAI-03",
                        "B25-MFAI-04",
                        "B25-MFAI-05",
                        "B25-MFAI-06",
                    ],
                },
                {
                    "name": "AI360",
                    "code": "AI360",
                    "groups": ["B25-AI360-01"],
                },
                {
                    "name": "Robotics",
                    "code": "RO",
                    "groups": ["B25-RO-01"],
                },
            ],
        },
        {
            "code": "BS_Y3_EN",
            "name": "BS - Year 3 (EN)",
            "tracks": [
                {
                    "name": "Software Development",
                    "code": "SD",
                    "groups": ["B24-SD-01", "B24-SD-02", "B24-SD-03"],
                },
                {
                    "name": "Cybersecurity",
                    "code": "CBS",
                    "groups": ["B24-CBS-01", "B24-CBS-02", "B24-CBS-03"],
                },
                {
                    "name": "Data Science",
                    "code": "DS",
                    "groups": ["B24-DS-01"],
                },
                {
                    "name": "Artificial Intelligence",
                    "code": "AI",
                    "groups": ["B24-AI-01", "B24-AI-02", "B24-AI-03"],
                },
                {
                    "name": "Game Development",
                    "code": "GD",
                    "groups": ["B24-GD-01"],
                },
                {
                    "name": "Robotics",
                    "code": "RO",
                    "groups": ["B24-RO-01"],
                },
            ],
        },
        {
            "code": "BS_Y3_RU",
            "name": "BS - Year 3 (RU)",
            "tracks": [
                {
                    "name": "MFAI",
                    "code": "MFAI",
                    "groups": [
                        "B24-MFAI-01",
                        "B24-MFAI-02",
                        "B24-MFAI-03",
                        "B24-MFAI-04",
                    ],
                },
                {
                    "name": "AI360",
                    "code": "AI360",
                    "groups": ["B24-AI360-01"],
                },
            ],
        },
    ],
    "master": [
        {
            "code": "MS_Y1",
            "name": "MS - Year 1",
            "tracks": [
                {
                    "name": "AI and Data Engineering",
                    "code": "AIDE",
                    "groups": ["M26-AIDE-01"],
                },
                {
                    "name": "Robotics",
                    "code": "RO",
                    "groups": ["M26-RO-01"],
                },
                {
                    "name": "Software Engineering",
                    "code": "SE",
                    "groups": ["M26-SE-01", "M26-SE-02"],
                },
                {
                    "name": "Secure Systems and Network Engineering",
                    "code": "SNE",
                    "groups": ["M26-SNE-01"],
                },
                {
                    "name": "Technological Entrepreneurship",
                    "code": "TE",
                    "groups": ["M26-TE-01"],
                },
            ],
        }
    ],
    "phd": [
        {
            "code": "PHD_Y1",
            "name": "PhD - Year 1",
            "tracks": [
                {
                    "name": "PhD",
                    "code": "PHD",
                    "groups": ["PhD"],
                },
            ],
        }
    ],
}


GROUP_ESTIMATED_SIZE: dict[str, int] = {
    "B26-CSE-01": 27,
    "B26-CSE-02": 27,
    "B26-CSE-03": 26,
    "B26-CSE-04": 26,
    "B26-CSE-05": 26,
    "B26-DSAI-01": 26,
    "B26-DSAI-02": 25,
    "B26-DSAI-03": 25,
    "B26-DSAI-04": 25,
    "B26-DSAI-05": 25,
    "B26-DSAI-06": 25,
    "B26-AI360-01": 18,
    "B26-MFAI-01": 26,
    "B26-MFAI-02": 26,
    "B26-MFAI-03": 26,
    "B26-MFAI-04": 26,
    "B26-MFAI-05": 26,
    "B26-MFAI-06": 26,
    "B26-MFAI-07": 26,
    "B26-RO-01": 2,
    "B25-CSE-01": 29,
    "B25-CSE-02": 29,
    "B25-CSE-03": 27,
    "B25-CSE-04": 27,
    "B25-CSE-05": 29,
    "B25-DSAI-01": 28,
    "B25-DSAI-02": 27,
    "B25-DSAI-03": 27,
    "B25-DSAI-04": 28,
    "B25-DSAI-05": 27,
    "B25-AI360-01": 18,
    "B25-MFAI-01": 26,
    "B25-MFAI-02": 26,
    "B25-MFAI-03": 26,
    "B25-MFAI-04": 26,
    "B25-MFAI-05": 26,
    "B25-MFAI-06": 26,
    "B25-RO-01": 2,
    "B24-SD-01": 30,
    "B24-SD-02": 27,
    "B24-SD-03": 25,
    "B24-CBS-01": 27,
    "B24-CBS-02": 26,
    "B24-CBS-03": 26,
    "B24-DS-01": 24,
    "B24-AI-01": 27,
    "B24-AI-02": 24,
    "B24-AI-03": 24,
    "B24-GD-01": 16,
    "B24-RO-01": 14,
    "B24-MFAI-01": 24,
    "B24-MFAI-02": 24,
    "B24-MFAI-03": 24,
    "B24-MFAI-04": 16,
    "B24-AI360-01": 10,
    "M26-AIDE-01": 27,
    "M26-RO-01": 14,
    "M26-SE-01": 18,
    "M26-SE-02": 17,
    "M26-SNE-01": 21,
    "M26-TE-01": 11,
    "PhD": 25,
}


# English layout (levels + groups). Membership filled later.
ENGLISH_PROGRAM: dict[str, Any] = {
    "code": "ENGLISH_YEAR1",
    "name": "English",
    "tracks": [
        {
            "code": "AWA_I",
            "name": "AWA-I",
            "groups": [f"AWA-I-{i}" for i in range(1, 17)],
        },
        {
            "code": "EAP",
            "name": "EAP",
            "groups": [f"EAP-{i}" for i in (1, 2, 3, 4, 6, 7, 8, 9, 10, 11)],
        },
        {
            "code": "FL",
            "name": "FL",
            "groups": [f"FL-{i}" for i in range(1, 7)],
        },
    ],
}

ENGLISH_GROUP_ESTIMATED_SIZE = 12


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


DATE_WEEKDAY_NAMES = tuple(day.value for day in Weekday)


_ENGLISH_COURSE_NAMES = frozenset(
    {
        "foreign language",
        "иностранный язык",
        "english for academic purposes i",
        "english for academic purposes ii",
        "english for academic purposes",
    }
)


def normalize_class_tag(value: str | None) -> str:
    if value is None:
        return "class"
    cleaned = value.strip().lower()
    return CLASS_TAG_MAP.get(cleaned, cleaned.replace(" ", "_"))


def is_english_course(lesson_name: str) -> bool:
    lowered = lesson_name.strip().lower()
    if lowered in _ENGLISH_COURSE_NAMES:
        return True
    return lowered.startswith("english for academic purposes")


_ACADEMIC_GROUP_ID_FIXES: dict[str, str] = {
    "M26-RO-": "M26-RO-01",
    "M26-RO15-01": "M26-RO-01",
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


def normalize_time(value: str | time | datetime) -> str:
    if isinstance(value, datetime):
        return value.strftime("%H:%M")
    if isinstance(value, time):
        return value.strftime("%H:%M")
    text = str(value).strip()
    if not text:
        return text
    for fmt in ("%H:%M:%S", "%H:%M"):
        try:
            return datetime.strptime(text, fmt).strftime("%H:%M")
        except ValueError:
            continue
    raise ValueError(f"Invalid time value: {value!r}")


def _unique_keep_order(items: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for item in items:
        if item in seen:
            continue
        seen.add(item)
        out.append(item)
    return out


def normalize_group_names(
    group_field: str | list[str] | tuple[str, ...] | None,
) -> list[str]:
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


_ONLINE_ROOM_LABELS = frozenset({"online", "онлайн"})
_MODIFIER_ONLY_ROOM_RE = re.compile(
    r"^(?:"
    r"(?:ON|ONLY ON|НА|ТОЛЬКО НА|ТОЛЬКО)\s+\d{1,2}[/.]\d{1,2}"
    r"(?:[,\s]+\d{1,2}[/.]\d{1,2})*"
    r"|"
    r"(?:STARTS ON|STARTS FROM|FROM|С|НАЧАЛО С|СТАРТ|СТАРТ С)\s+\d{1,2}[/.]\d{1,2}"
    r")$",
    re.IGNORECASE,
)
_STARTS_AT_RE = re.compile(
    r"\(?((?:starts?\s+at)|(?:начало\s+в))\s+(\d{1,2}:\d{2})\)?",
    re.IGNORECASE,
)
_ENDS_AT_RE = re.compile(
    r"\(?((?:ends?\s+at)|(?:till)|(?:конец\s+в)|(?:до))\s+(\d{1,2}:\d{2})\)?",
    re.IGNORECASE,
)
_RANGE_TIME_RE = re.compile(r"\(?(\d{1,2}:\d{2})-(\d{1,2}:\d{2})\)?")


def _normalize_room_value(room: str | None) -> str:
    if room is None:
        return ""
    trimmed = str(room).strip()
    if not trimmed:
        return ""
    if trimmed.casefold() in _ONLINE_ROOM_LABELS:
        return "ONLINE"
    # Modifier-only cells ("ТОЛЬКО НА 12/09", "НАЧАЛО С 31/08") are not rooms.
    if _MODIFIER_ONLY_ROOM_RE.fullmatch(trimmed):
        return ""
    return trimmed


def _time_to_minutes(value: str) -> int:
    hh, mm = normalize_time(value).split(":")
    return int(hh) * 60 + int(mm)


def _minutes_to_time(total: int) -> str:
    total = max(0, total) % (24 * 60)
    return f"{total // 60:02d}:{total % 60:02d}"


def _parse_embedded_location_timing(
    room: str | None,
) -> tuple[str | None, str | None, str]:
    """Extract start/end overrides and real room from strings like 'STARTS AT 18:00 ONLINE'."""
    raw = str(room or "").strip()
    if not raw:
        return None, None, ""

    rest = raw
    starts_at: str | None = None
    ends_at: str | None = None

    if match := _RANGE_TIME_RE.search(rest):
        starts_at = normalize_time(match.group(1))
        ends_at = normalize_time(match.group(2))
        rest = rest.replace(match.group(0), " ")

    if match := _STARTS_AT_RE.search(rest):
        starts_at = normalize_time(match.group(2))
        rest = rest.replace(match.group(0), " ")

    if match := _ENDS_AT_RE.search(rest):
        ends_at = normalize_time(match.group(2))
        rest = rest.replace(match.group(0), " ")

    location = re.sub(r"\s+", " ", rest).strip(" ,;/")
    # Only treat as embedded timing when the original room looked like a modifier blob.
    looks_like_modifier = (
        starts_at is not None
        or ends_at is not None
        or bool(
            re.search(
                r"(starts?\s+at|начало|till|ends?\s+at|конец)", raw, re.IGNORECASE
            )
        )
    )
    if not looks_like_modifier:
        return None, None, _normalize_room_value(raw)
    return starts_at, ends_at, _normalize_room_value(location)


def _apply_embedded_location_timing(
    *,
    start_time: str,
    end_time: str,
    room: str | None,
) -> tuple[str, str, str]:
    starts_at, ends_at, location = _parse_embedded_location_timing(room)
    start = normalize_time(start_time)
    end = normalize_time(end_time)
    if starts_at and ends_at:
        return starts_at, ends_at, location
    if starts_at:
        duration = _time_to_minutes(end) - _time_to_minutes(start)
        if duration <= 0:
            duration = 90
        return (
            starts_at,
            _minutes_to_time(_time_to_minutes(starts_at) + duration),
            location,
        )
    if ends_at:
        return start, ends_at, location
    return start, end, location if location or not room else _normalize_room_value(room)


# (weekday, start_time, end_time, room, instructor_ids)
WeeklySlotSig = tuple[str, str, str, str, tuple[str, ...]]


def _instructor_pattern_value(
    instructor_ids: tuple[str, ...],
) -> str | list[str] | None:
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


def _core_occurrence_from_row(
    row: dict[str, Any],
    instructor_ids: tuple[str, ...],
    *,
    date_value: str,
) -> dict[str, Any]:
    modifiers = row.get("modifiers") or {}
    room = _normalize_room_value(row.get("room")) or _normalize_room_value(
        modifiers.get("location")
    )
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


def _nest_edits_from_modifiers(
    modifiers: dict[str, Any] | None,
) -> list[dict[str, Any]]:
    edits: list[dict[str, Any]] = []
    for nested in _nest_modifier_entries(modifiers):
        location = nested.get("location")
        if not location:
            continue
        for date_value in nested.get("on") or []:
            edits.append(
                {
                    "select_week": str(date_value),
                    "room": _normalize_room_value(str(location)),
                }
            )
    return edits


def _dedupe_pattern_edits(edits: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[tuple[Any, ...]] = set()
    deduped: list[dict[str, Any]] = []
    for edit in sorted(edits, key=lambda item: str(item.get("select_week", ""))):
        instructor = edit.get("instructor")
        instructor_key = (
            tuple(instructor) if isinstance(instructor, list) else instructor
        )
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
        key=lambda item: (
            str(item.get("date", "")),
            str(item.get("start_time", "")),
            str(item.get("end_time", "")),
        ),
    ):
        instructor = occurrence.get("instructor")
        instructor_key = (
            tuple(instructor) if isinstance(instructor, list) else instructor
        )
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
        return [_unique_keep_order(list(sig))]
    if all(len(sig) == 1 for sig in signatures_for_pool):
        return sorted({sig[0] for sig in signatures_for_pool})
    return [_unique_keep_order(list(sig)) for sig in signatures_for_pool]


def _add_instructor_ref(target: set[str], value: str | list[str] | None) -> None:
    if value is None:
        return
    if isinstance(value, str):
        target.add(value)
        return
    target.update(value)


def _collect_instructor_ids_from_components(
    components: list[dict[str, Any]],
) -> set[str]:
    ids: set[str] = set()
    for component in components:
        for item in component.get("instructor_pool") or []:
            _add_instructor_ref(ids, item)
        for session in component.get("sessions") or []:
            for pattern in session.get("weekly_pattern") or []:
                _add_instructor_ref(ids, pattern.get("instructor"))
                for edit in pattern.get("edits") or []:
                    _add_instructor_ref(ids, edit.get("instructor"))
            for occurrence in session.get("dates_pattern") or []:
                _add_instructor_ref(ids, occurrence.get("instructor"))
    return ids


def _build_output_instructors(
    instructors_map: dict[str, InstructorConfig],
    registry: InstructorRegistry,
    scheduled_ids: set[str],
) -> list[dict[str, Any]]:
    output_ids = set(scheduled_ids)
    for instructor in registry.by_id.values():
        if instructor.position:
            output_ids.add(instructor.id)

    def resolve_instructor(instructor_id: str) -> InstructorConfig:
        return instructors_map.get(instructor_id) or registry.by_id[instructor_id]

    return [
        resolve_instructor(instructor_id).model_dump(mode="json", exclude_none=True)
        for instructor_id in sorted(
            output_ids,
            key=lambda instructor_id: (
                resolve_instructor(instructor_id).name_en
                or resolve_instructor(instructor_id).name_ru
                or instructor_id
            ).casefold(),
        )
    ]


def _weekly_pattern_from_slots(
    slots: set[WeeklySlotSig],
    edits_by_slot: dict[WeeklySlotSig, list[dict[str, Any]]] | None = None,
) -> list[dict[str, Any]]:
    order = {name: index for index, name in enumerate(DATE_WEEKDAY_NAMES)}
    pattern: list[dict[str, Any]] = []
    for weekday, start_time, end_time, room, instructor_ids in sorted(
        slots,
        key=lambda item: (order.get(item[0], 99), item[1], item[2], item[3], item[4]),
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
        slot_edits = _dedupe_pattern_edits(
            (edits_by_slot or {}).get(
                (weekday, start_time, end_time, room, instructor_ids), []
            )
        )
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
    occurrences_by_signature: dict[tuple[str, ...], dict[str, list[dict[str, Any]]]]
    | None = None,
    signatures_for_pool: list[tuple[str, ...]] | None = None,
    edits_by_slot: dict[WeeklySlotSig, list[dict[str, Any]]] | None = None,
    selector_map: dict[str, set[str]] | None = None,
    group_order: dict[str, int] | None = None,
) -> list[dict[str, Any]]:
    del (
        per_group
    )  # sessions always follow spreadsheet slot audiences; flag stays on component
    signatures = signatures_for_pool or [()]

    def _audience_tokens(group_ids: list[str]) -> list[str]:
        ordered = _unique_keep_order(
            [gid for gid in groups_for_cls if gid in group_ids]
        )
        if selector_map is not None and group_order is not None:
            return _unique_keep_order(
                compress_groups_to_selectors(ordered, selector_map, group_order)
            )
        return ordered

    # One session per slot-audience: groups that share a concrete weekly slot stay together
    # (merged spreadsheet cells), even when the component is marked per_group.
    slot_to_groups: dict[WeeklySlotSig, set[str]] = defaultdict(set)
    for group_id in groups_for_cls:
        for slot in slots_source.get(group_id, set()):
            slot_to_groups[slot].add(group_id)

    slots_by_audience: dict[frozenset[str], set[WeeklySlotSig]] = defaultdict(set)
    for slot, group_ids in slot_to_groups.items():
        slots_by_audience[frozenset(group_ids)].add(slot)

    result: list[dict[str, Any]] = []
    for audience_groups, slots in sorted(
        slots_by_audience.items(),
        key=lambda item: (
            min(
                (groups_for_cls.index(gid) for gid in item[0] if gid in groups_for_cls),
                default=10**9,
            ),
            len(item[0]),
            tuple(sorted(item[0])),
        ),
    ):
        result.append(
            {
                "audience": _audience_tokens(sorted(audience_groups)),
                "weekly_pattern": _weekly_pattern_from_slots(slots, edits_by_slot),
            }
        )

    occurrence_groups = [
        group_id
        for group_id in groups_for_cls
        if _occurrences_for_variant(
            signatures, [group_id], occurrences_by_signature or {}
        )
    ]
    if occurrence_groups:
        occ_by_group = {
            group_id: _occurrences_for_variant(
                signatures, [group_id], occurrences_by_signature or {}
            )
            for group_id in occurrence_groups
        }
        clusters: dict[tuple[tuple[str, ...], ...], list[str]] = defaultdict(list)

        def _occ_fingerprint(
            items: list[dict[str, Any]],
        ) -> tuple[tuple[str, ...], ...]:
            return tuple(
                sorted(
                    (
                        str(item.get("date") or ""),
                        str(item.get("start_time") or ""),
                        str(item.get("end_time") or ""),
                        str(item.get("room") or ""),
                        json.dumps(
                            item.get("instructor"), ensure_ascii=False, sort_keys=True
                        ),
                    )
                    for item in items
                )
            )

        for group_id, items in occ_by_group.items():
            clusters[_occ_fingerprint(items)].append(group_id)
        for group_ids in clusters.values():
            result.append(
                {
                    "audience": _audience_tokens(group_ids),
                    "dates_pattern": occ_by_group[group_ids[0]],
                }
            )

    if not result and student_groups:
        return []
    return result


def split_teacher_names(raw: str | None) -> list[str]:
    if not raw:
        return []
    normalized = " ".join(str(raw).replace("\n", " ").split())
    parts = [p.strip() for p in normalized.split(",") if p.strip()]
    names: list[str] = []
    # "П. Останин И. Бутаков" / "А. Фролов Ф. Иванов ..." (no commas)
    initial_surname = re.compile(r"[A-Za-zА-Яа-яЁё]\.\s*[A-Za-zА-Яа-яЁё-]+")
    for part in parts:
        matches = initial_surname.findall(part)
        if len(matches) >= 2 and sum(len(m) for m in matches) >= max(
            10, int(len(part) * 0.7)
        ):
            names.extend(m.strip() for m in matches)
            continue
        names.append(part)
    return names


def _resolve_teacher_signature(
    names: list[str],
    instructors_map: dict[str, InstructorConfig],
    registry: InstructorRegistry,
) -> tuple[str, ...]:
    ids: list[str] = []
    seen: set[str] = set()
    for name in names:
        instructor_id = registry.resolve_into_map(name, instructors_map)
        if instructor_id in seen:
            continue
        seen.add(instructor_id)
        ids.append(instructor_id)
    return tuple(sorted(ids))


def normalize_lesson_name(lesson_name: str) -> str:
    name = lesson_name.strip()
    lowered = name.lower()
    if lowered in {"foreign language", "иностранный язык"}:
        return "Foreign Language"
    return name


class FlowStyleList(list):
    pass


class _YamlDumper(yaml.SafeDumper):
    pass


def _yaml_str_presenter(dumper: yaml.SafeDumper, data: str) -> yaml.nodes.ScalarNode:
    if "\n" in data:
        return dumper.represent_scalar("tag:yaml.org,2002:str", data, style="|")
    return dumper.represent_scalar("tag:yaml.org,2002:str", data)


def _represent_flow_list(
    dumper: yaml.SafeDumper, data: FlowStyleList
) -> yaml.nodes.SequenceNode:
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
            if key in {"instructor_pool", "audience"} and isinstance(value, list):
                out[key] = FlowStyleList(value)
            else:
                out[key] = apply_yaml_style_overrides(value)
        return out
    if isinstance(node, list):
        return [apply_yaml_style_overrides(item) for item in node]
    return node


def parse_room_survey_pdf_text(text: str) -> dict[str, dict[str, str]]:
    """Parse IU room survey text (from «Аудитории ИУ.pdf») into room features."""
    normalized = text.replace("\f", "\n")
    blocks: list[list[str]] = []
    current: list[str] = []
    header_re = re.compile(r"^(\d+[A-Za-zА-Яа-я]*)(?:\s+(.+))?$")
    field_re = re.compile(rf"^({'|'.join(_ROOM_SURVEY_FIELD_KEYS)}):\s*(.*)$")

    for raw_line in normalized.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if header_re.match(line) and not field_re.match(line):
            if current:
                blocks.append(current)
            current = [line]
            continue
        current.append(line)
    if current:
        blocks.append(current)

    rooms: dict[str, dict[str, str]] = {}
    for lines in blocks:
        header_match = header_re.match(lines[0])
        if header_match is None:
            continue
        room_id = header_match.group(1)
        note = (header_match.group(2) or "").strip() or None
        fields: dict[str, str] = {}
        current_field: str | None = None
        for line in lines[1:]:
            field_match = field_re.match(line)
            if field_match is not None:
                current_field = field_match.group(1)
                fields[current_field] = field_match.group(2).strip()
                continue
            if current_field is None:
                continue
            fields[current_field] = f"{fields.get(current_field, '')} {line}".strip()

        features: dict[str, str] = {}
        for source_key, feature_key in _ROOM_SURVEY_FEATURE_MAP.items():
            value = fields.get(source_key, "").strip()
            if value:
                features[feature_key] = value
        if note:
            features["Заметка"] = note
        if features:
            rooms[room_id] = features
    return rooms


def extract_room_survey_features_from_pdf(pdf_path: Path) -> dict[str, dict[str, str]]:
    import subprocess

    completed = subprocess.run(
        ["pdftotext", "-layout", str(pdf_path), "-"],
        check=True,
        capture_output=True,
        text=True,
    )
    return parse_room_survey_pdf_text(completed.stdout)


def load_room_survey_features(
    json_path: Path | None = None,
    *,
    pdf_path: Path | None = None,
) -> dict[str, dict[str, str]]:
    if pdf_path is not None and pdf_path.exists():
        return extract_room_survey_features_from_pdf(pdf_path)
    path = json_path or (SCRIPT_DIR / DEFAULT_ROOM_SURVEY_FEATURES_JSON)
    if not path.exists():
        return {}
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        return {}
    features: dict[str, dict[str, str]] = {}
    for room_id, values in payload.items():
        key = str(room_id).strip()
        if not key or not isinstance(values, dict):
            continue
        cleaned = {
            str(feature_key): str(feature_value).strip()
            for feature_key, feature_value in values.items()
            if str(feature_key).strip() and str(feature_value).strip()
        }
        if cleaned:
            features[key] = cleaned
    return features


def load_rooms(
    rooms_json_path: Path,
    *,
    survey_features: dict[str, dict[str, str]] | None = None,
) -> list[dict[str, Any]]:
    if not rooms_json_path.exists():
        return []
    rows = json.loads(rooms_json_path.read_text(encoding="utf-8"))
    survey = survey_features or {}
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
        room: dict[str, Any] = {
            "id": room_id,
            "name": str(row.get("title") or row.get("short_name") or room_id),
            "capacity": capacity,
        }
        features = survey.get(room_id)
        if features:
            room["features"] = dict(features)
        rooms.append(room)
    return sorted(
        rooms,
        key=lambda room: (
            str(room.get("id", ""))[:1],
            -int(room.get("capacity", 0)),
            str(room.get("id", "")),
        ),
    )


def build_group_selectors(
    programs: dict[str, list[dict[str, Any]]],
) -> dict[str, set[str]]:
    selectors: dict[str, set[str]] = {}
    for level_programs in programs.values():
        for program in level_programs:
            program_id = str(program.get("code") or program.get("id") or "").strip()
            if not program_id:
                continue
            program_groups = {
                _group_entry_code(g)
                for track in program.get("tracks", [])
                for g in track.get("groups", [])
                if _group_entry_code(g)
            }
            if program_groups:
                selectors[f"@{program_id}"] = program_groups
            for track in program.get("tracks", []):
                track_name = track.get("name")
                if not track_name:
                    continue
                track_groups = {
                    _group_entry_code(g)
                    for g in track.get("groups", [])
                    if _group_entry_code(g)
                }
                if track_groups:
                    selectors[f"@{program_id}/{track_name}"] = track_groups
    return selectors


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


def _section_lookup_maps(
    sections: list[dict[str, Any]],
) -> tuple[dict[str, str], dict[str, str]]:
    program_to_section: dict[str, str] = {}
    group_to_section: dict[str, str] = {}
    for section in sections:
        section_code = str(section.get("code") or "").strip()
        if not section_code:
            continue
        for program in section.get("programs") or []:
            program_code = str(program.get("code") or "").strip()
            if program_code:
                program_to_section[program_code] = section_code
            for group in program.get("groups") or []:
                gid = group if isinstance(group, str) else _group_entry_code(group)
                if gid:
                    group_to_section[gid] = section_code
            for track in program.get("tracks") or []:
                for group in track.get("groups") or []:
                    gid = group if isinstance(group, str) else _group_entry_code(group)
                    if gid:
                        group_to_section[gid] = section_code
    return program_to_section, group_to_section


def derive_course_section_code(
    course_name: str,
    components: list[dict[str, Any]],
    sections: list[dict[str, Any]],
) -> str:
    """Map a course to exactly one term section from component audiences."""
    program_to_section, group_to_section = _section_lookup_maps(sections)
    codes: set[str] = set()
    for component in components:
        tokens = list(component.get("audience") or [])
        for session in component.get("sessions") or []:
            tokens.extend(session.get("audience") or [])
        for token in tokens:
            raw = str(token or "").strip()
            if not raw:
                continue
            if raw.startswith("@"):
                program_code = raw[1:].split("/", 1)[0].strip()
                section_code = program_to_section.get(program_code)
                if section_code:
                    codes.add(section_code)
                continue
            section_code = group_to_section.get(raw)
            if section_code:
                codes.add(section_code)
    if len(codes) != 1:
        raise ValueError(
            f"Course {course_name!r} must map to exactly one section; "
            f"found {sorted(codes) or []}"
        )
    return next(iter(codes))


def collect_academic_groups(
    programs: dict[str, list[dict[str, Any]]],
) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for _level_name, level_programs in programs.items():
        for program in level_programs:
            for track in program.get("tracks", []):
                for g in track.get("groups", []):
                    gid = _group_entry_code(g)
                    if not gid or gid in seen:
                        continue
                    seen.add(gid)
                    out.append(
                        {
                            "code": gid,
                            "name": gid,
                            "estimated_size": GROUP_ESTIMATED_SIZE.get(gid),
                        }
                    )
    return out


def _slug_code(value: str) -> str:
    cleaned = re.sub(r"[^a-zA-Z0-9]+", "_", str(value or "").strip().lower())
    return cleaned.strip("_") or "item"


def build_sections(programs: dict[str, list[dict[str, Any]]]) -> list[dict[str, Any]]:
    core_programs: list[dict[str, Any]] = []
    for level_name in ("bachelor", "master", "phd"):
        for program in programs.get(level_name, []):
            tracks = []
            for track in program.get("tracks", []):
                track_name = track.get("name", "")
                track_groups = [
                    g for g in track.get("groups", []) if _group_entry_code(g)
                ]
                tracks.append(
                    {
                        "code": str(
                            track.get("code") or _slug_code(track_name).upper()
                        ),
                        "name": track.get("name"),
                        "groups": track_groups,
                    }
                )
            core_programs.append(
                {
                    "code": str(program.get("code") or program.get("id") or "").strip(),
                    "name": program.get("name"),
                    "tracks": tracks,
                }
            )
    return [
        {
            "code": "core",
            "name": "Основные курсы",
            "default_layout": "groups",
            "programs": core_programs,
        },
        {
            "code": "english",
            "name": "Английский",
            "default_layout": "groups",
            "programs": [deepcopy(ENGLISH_PROGRAM)],
        },
        {
            "code": "electives",
            "name": "Элективы",
            "default_layout": "calendar",
            "programs": [],
        },
    ]


def _normalize_hhmm(value: Any) -> str | None:
    text = str(value or "").strip()
    if not text:
        return None
    if len(text) >= 5 and text[2] == ":":
        return text[:5]
    return text


def _expand_audience_tokens(
    tokens: list[Any], selector_map: dict[str, set[str]]
) -> set[str]:
    groups: set[str] = set()
    for token in tokens:
        raw = str(token or "").strip()
        if not raw:
            continue
        if raw.startswith("@"):
            groups.update(selector_map.get(raw, set()))
            continue
        groups.add(raw)
    return groups


def _collect_meeting_time_pairs(
    course_items: list[dict[str, Any]],
) -> list[tuple[set[str], str, str]]:
    """Return (audience_groups, start_hhmm, end_hhmm) for every placed meeting."""
    pairs: list[tuple[set[str], str, str]] = []
    for course in course_items:
        for component in course.get("components") or []:
            component_groups = [
                str(g)
                for g in (component.get("audience") or [])
                if str(g).strip()
            ]
            for session in component.get("sessions") or []:
                audience = [
                    str(a) for a in (session.get("audience") or []) if str(a).strip()
                ]
                tokens = audience or component_groups
                for slot in session.get("weekly_pattern") or []:
                    start = _normalize_hhmm(slot.get("start_time"))
                    end = _normalize_hhmm(slot.get("end_time"))
                    if start and end:
                        pairs.append((set(tokens), start, end))
                for occurrence in session.get("dates_pattern") or []:
                    start = _normalize_hhmm(occurrence.get("start_time"))
                    end = _normalize_hhmm(occurrence.get("end_time"))
                    if start and end:
                        pairs.append((set(tokens), start, end))
    return pairs


def load_program_semesters_from_parse_core(
    path: Path | None = None,
) -> dict[str, tuple[date, date]]:
    """Read override.programs windows from parse-core-courses.yaml."""
    parse_path = path or (SCRIPT_DIR / DEFAULT_PARSE_CORE_COURSES_YAML)
    if not parse_path.is_file():
        return dict(PROGRAM_SEMESTER)

    raw = yaml.safe_load(parse_path.read_text(encoding="utf-8")) or {}
    windows: dict[str, tuple[date, date]] = dict(PROGRAM_SEMESTER)
    for target in raw.get("targets") or []:
        for override in target.get("override") or []:
            start_raw = override.get("start_date")
            end_raw = override.get("end_date")
            if not start_raw or not end_raw:
                continue
            start = date.fromisoformat(str(start_raw)[:10])
            end = date.fromisoformat(str(end_raw)[:10])
            for code in override.get("programs") or []:
                token = str(code).strip()
                if token:
                    windows[token] = (start, end)
    return windows


def attach_program_semesters(
    sections: list[dict[str, Any]],
    program_semesters: dict[str, tuple[date, date]] | None = None,
) -> list[dict[str, Any]]:
    """Attach optional program.semester from parse-core / PROGRAM_SEMESTER map."""
    windows = program_semesters if program_semesters is not None else load_program_semesters_from_parse_core()
    for section in sections:
        for program in section.get("programs") or []:
            code = str(program.get("code") or "").strip()
            window = windows.get(code)
            if window is None:
                program.pop("semester", None)
                continue
            start, end = window
            program["semester"] = {
                "start_date": start.isoformat(),
                "end_date": end.isoformat(),
            }
    return sections


def program_time_slots_subset_of_term(
    pairs: list[tuple[str, str]] | set[tuple[str, str]],
) -> bool:
    """True when every program slot start is one of the term time_slots starts."""
    if not pairs:
        return True
    return all(start in TERM_TIME_SLOT_STARTS for start, _end in pairs)


def _canonicalize_program_slot_pair(start: str, end: str) -> tuple[str, str]:
    """Map known term starts to the canonical term end (ignore till/truncation)."""
    term_end = TERM_END_BY_START.get(start)
    if term_end is not None:
        return start, term_end
    return start, end


def attach_program_time_slots(
    sections: list[dict[str, Any]],
    course_items: list[dict[str, Any]],
    selector_map: dict[str, set[str]],
) -> list[dict[str, Any]]:
    """Set each program's time_slots from distinct meeting start/end pairs that touch it.

    Skips programs whose collected slots are a subset of term.time_slots (by start).
    Meetings that start on a term slot use the term end (so till 13:00 does not
    create a fake 12:40–13:00 program row).
    """
    program_groups: dict[str, set[str]] = {}
    for section in sections:
        for program in section.get("programs") or []:
            code = str(program.get("code") or "").strip()
            if not code:
                continue
            groups: set[str] = set()
            for group in program.get("groups") or []:
                gid = _group_entry_code(group)
                if gid:
                    groups.add(gid)
            for track in program.get("tracks") or []:
                for group in track.get("groups") or []:
                    gid = _group_entry_code(group)
                    if gid:
                        groups.add(gid)
            # Also accept explicit selector map entries for this program.
            groups.update(selector_map.get(f"@{code}", set()))
            program_groups[code] = groups

    slots_by_program: dict[str, set[tuple[str, str]]] = {
        code: set() for code in program_groups
    }
    for tokens, start, end in _collect_meeting_time_pairs(course_items):
        pair = _canonicalize_program_slot_pair(start, end)
        expanded = _expand_audience_tokens(list(tokens), selector_map)
        # Direct @PROGRAM / @PROGRAM/TRACK tokens also map via expansion.
        for code, groups in program_groups.items():
            if not groups:
                continue
            if expanded & groups:
                slots_by_program[code].add(pair)
                continue
            # Audience may still be unresolved selectors for this program.
            if any(str(token).startswith(f"@{code}") for token in tokens):
                slots_by_program[code].add(pair)

    updated = deepcopy(sections)
    for section in updated:
        for program in section.get("programs") or []:
            code = str(program.get("code") or "").strip()
            pairs = sorted(slots_by_program.get(code) or [])
            if not pairs:
                program.pop("time_slots", None)
                continue
            if program_time_slots_subset_of_term(pairs):
                program.pop("time_slots", None)
                continue
            program["time_slots"] = [
                {"start_time": start, "end_time": end} for start, end in pairs
            ]
    return updated


def collect_english_groups(
    english_program: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    program = english_program or ENGLISH_PROGRAM
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for track in program.get("tracks", []):
        for group in track.get("groups", []):
            gid = _group_entry_code(group)
            if not gid or gid in seen:
                continue
            seen.add(gid)
            out.append(
                {
                    "code": gid,
                    "kind": "english",
                    "name": gid,
                    "estimated_size": ENGLISH_GROUP_ESTIMATED_SIZE,
                    "students": [],
                }
            )
    return out


def build_students_groups(
    academic_groups: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    distribution: list[dict[str, Any]] = []
    for group in academic_groups:
        gid = group.get("code") or group.get("id")
        if not gid:
            continue
        distribution.append(
            {
                "code": gid,
                "kind": "core",
                "name": group.get("name", gid),
                "estimated_size": group.get("estimated_size"),
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
        (
            (selector, members)
            for selector, members in selector_map.items()
            if members.issubset(group_set)
        ),
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
    groups = cls.get("audience", [])
    if not groups:
        return 10**9

    def member_rank(gid: str) -> int:
        return group_order.get(gid, 10**9)

    def token_rank(token: str) -> int:
        if token in selector_map:
            return min((member_rank(gid) for gid in selector_map[token]), default=10**9)
        return member_rank(token)

    return min((token_rank(token) for token in groups), default=10**9)


def infer_per_group(
    class_tag: str,
    student_groups: list[str],
    *,
    source_group_count: int | None = None,
    is_shared_lesson: bool = False,
) -> bool:
    if is_shared_lesson:
        return False
    effective_group_count = (
        source_group_count if source_group_count is not None else len(student_groups)
    )
    if class_tag == "class" and effective_group_count > 1:
        return True
    if class_tag == "lab":
        return True
    return False


def _is_parallel_stream_group_token(token: str) -> bool:
    value = token.strip().upper()
    return len(value) == 2 and value[0] == "G" and value[1].isdigit()


def _elective_parallel_stream_group(lesson: dict[str, Any]) -> str:
    audience = normalize_group_names(lesson.get("audience") or [])
    for token in audience:
        if _is_parallel_stream_group_token(token):
            return token.strip().upper()
    return ""


def _elective_explicit_alias(lesson: dict[str, Any]) -> str:
    for key in ("alias", "course_name"):
        value = str(lesson.get(key) or "").strip()
        if value:
            return value
    return ""


def _elective_raw_alias_for_lessons(
    lessons: list[dict[str, Any]],
    *,
    alias_by_subject: dict[str, str] | None = None,
) -> str:
    alias_tokens: set[str] = set()
    for lesson in lessons:
        explicit = _elective_explicit_alias(lesson)
        if explicit:
            alias_tokens.add(explicit)
        for token in normalize_group_names(lesson.get("audience") or []):
            if not _is_parallel_stream_group_token(token):
                alias_tokens.add(token)
    if alias_tokens:
        return sorted(alias_tokens)[0]
    if lessons and alias_by_subject:
        subject = str(lessons[0]["subject"]).strip()
        mapped = alias_by_subject.get(subject)
        if mapped:
            return mapped
    if lessons:
        return str(lessons[0]["subject"]).strip()
    return "UNK"


def _slug_alias_token(alias: str) -> str:
    ascii_slug = _slug_code(alias)
    if ascii_slug and ascii_slug != "item":
        return ascii_slug.upper()
    token = re.sub(r"[^\w]+", "_", alias, flags=re.UNICODE).strip("_")
    return token.upper() if token else "UNK"


def _elective_alias_token_for_lessons(
    lessons: list[dict[str, Any]],
    *,
    alias_by_subject: dict[str, str] | None = None,
) -> str:
    return _slug_alias_token(
        _elective_raw_alias_for_lessons(lessons, alias_by_subject=alias_by_subject),
    )


def elective_student_group_id(alias: str, parallel: str = "") -> str:
    if parallel:
        return f"{alias}-{parallel}"
    return alias


def _elective_parallel_groups_for_lessons(lessons: list[dict[str, Any]]) -> list[str]:
    parallels = {_elective_parallel_stream_group(lesson) for lesson in lessons}
    parallels.discard("")
    return sorted(parallels)


def _elective_component_tag(
    lesson: dict[str, Any], *, shared_for_parallel_groups: bool
) -> str:
    explicit = lesson.get("type")
    if explicit:
        return normalize_class_tag(str(explicit))
    if shared_for_parallel_groups:
        return "lec"
    return "class"


def _elective_sessions_from_lessons(
    lessons: list[dict[str, Any]],
    audience: list[str],
    instructors_map: dict[str, InstructorConfig],
    registry: InstructorRegistry,
) -> list[dict[str, Any]]:
    entries_by_key: dict[
        tuple[str, str, str, str | None, str | None],
        tuple[dict[str, Any], tuple[str, ...]],
    ] = {}
    for lesson in lessons:
        teacher_ids = _resolve_teacher_signature(
            split_teacher_names(lesson.get("instructor")),
            instructors_map,
            registry,
        )
        for occurrence in lesson.get("occurrences") or []:
            occ_date = occurrence.get("date")
            if not occ_date:
                continue
            start_time, end_time, room = _apply_embedded_location_timing(
                start_time=str(occurrence.get("start_time") or "00:00"),
                end_time=str(
                    occurrence.get("end_time")
                    or occurrence.get("start_time")
                    or "00:00"
                ),
                room=occurrence.get("room"),
            )
            key = (
                str(occ_date),
                start_time,
                end_time,
                room or None,
                occurrence.get("a1_range"),
            )
            cleaned = {
                **occurrence,
                "start_time": start_time,
                "end_time": end_time,
                "room": room or None,
            }
            if key not in entries_by_key:
                entries_by_key[key] = (cleaned, teacher_ids)

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
            "start_time": str(occurrence["start_time"]),
            "end_time": str(occurrence["end_time"]),
        }
        room = _normalize_room_value(occurrence.get("room"))
        if room:
            entry["room"] = room
        if len(teacher_ids) == 1:
            entry["instructor"] = teacher_ids[0]
        elif len(teacher_ids) > 1:
            entry["instructor"] = list(teacher_ids)
        occurrences.append(entry)

    return [
        {
            "audience": _unique_keep_order(list(audience)),
            "dates_pattern": occurrences,
        }
    ]


def _elective_component_from_lessons(
    lessons: list[dict[str, Any]],
    student_groups: list[str],
    instructors_map: dict[str, InstructorConfig],
    registry: InstructorRegistry,
    *,
    shared_for_parallel_groups: bool,
) -> dict[str, Any] | None:
    if not lessons or not student_groups:
        return None
    teacher_signatures = [
        _resolve_teacher_signature(
            split_teacher_names(lesson.get("instructor")),
            instructors_map,
            registry,
        )
        for lesson in lessons
    ]
    representative = lessons[0]
    if SKIP_ELECTIVE_PATTERNS_AND_OCCURRENCES:
        sessions: list[dict[str, Any]] = []
        meeting_count = max(
            (len(lesson.get("occurrences") or []) for lesson in lessons),
            default=0,
        )
    else:
        sessions = _elective_sessions_from_lessons(
            lessons, student_groups, instructors_map, registry
        )
        meeting_count = len(sessions[0]["dates_pattern"]) if sessions else 0
    return {
        "tag": _elective_component_tag(
            representative, shared_for_parallel_groups=shared_for_parallel_groups
        ),
        "audience": _unique_keep_order(list(student_groups)),
        "per_semester": meeting_count
        or max(len(lesson.get("occurrences") or []) for lesson in lessons),
        "instructor_pool": _elective_instructor_pool(teacher_signatures),
        "sessions": sessions,
    }


def _group_elective_lessons_by_course(
    grouped_electives: list[dict[str, Any]],
) -> dict[str, list[dict[str, Any]]]:
    by_course: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for lesson in grouped_electives:
        course_name = str(lesson["subject"]).strip()
        if not course_name or course_name.strip().lower() in {
            "group meeting with administration"
        }:
            continue
        if not lesson.get("occurrences"):
            continue
        by_course[course_name].append(lesson)
    return by_course


def collect_elective_student_groups(
    grouped_electives: list[dict[str, Any]],
    *,
    alias_by_subject: dict[str, str] | None = None,
) -> list[dict[str, Any]]:
    by_id: dict[str, dict[str, Any]] = {}
    for lessons in _group_elective_lessons_by_course(grouped_electives).values():
        alias = _elective_alias_token_for_lessons(
            lessons, alias_by_subject=alias_by_subject
        )
        parallels = _elective_parallel_groups_for_lessons(lessons)
        title = str(lessons[0]["subject"]).strip()
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


def _elective_program_code(sheet_name: str) -> str:
    ascii_slug = _slug_code(sheet_name).upper()
    if ascii_slug and ascii_slug != "ITEM":
        return ascii_slug
    return _slug_alias_token(sheet_name)


def _elective_groups_by_sheet(
    grouped_electives: list[dict[str, Any]],
    *,
    alias_by_subject: dict[str, str] | None = None,
) -> dict[str, list[str]]:
    by_sheet: dict[str, set[str]] = defaultdict(set)
    for lessons in _group_elective_lessons_by_course(grouped_electives).values():
        alias = _elective_alias_token_for_lessons(
            lessons, alias_by_subject=alias_by_subject
        )
        parallels = _elective_parallel_groups_for_lessons(lessons)
        group_ids = (
            [elective_student_group_id(alias, parallel) for parallel in parallels]
            if parallels
            else [elective_student_group_id(alias)]
        )
        sheets = {
            str(lesson.get("google_sheet_name") or "").strip() or "Electives"
            for lesson in lessons
        }
        for sheet_name in sheets:
            by_sheet[sheet_name].update(group_ids)
    return {
        sheet_name: sorted(group_ids)
        for sheet_name, group_ids in sorted(by_sheet.items())
    }


def append_electives_to_sections(
    sections: list[dict[str, Any]],
    grouped_electives: list[dict[str, Any]],
    *,
    alias_by_subject: dict[str, str] | None = None,
) -> list[dict[str, Any]]:
    groups_by_sheet = _elective_groups_by_sheet(
        grouped_electives, alias_by_subject=alias_by_subject
    )
    if not groups_by_sheet:
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
            "default_layout": "calendar",
            "programs": [],
        }
        updated.append(electives_section)
    else:
        electives_section.setdefault("default_layout", "calendar")
    programs = list(electives_section.get("programs") or [])
    used_codes: set[str] = {
        str(program.get("code") or "").strip()
        for program in programs
        if str(program.get("code") or "").strip()
    }
    for sheet_name, group_ids in groups_by_sheet.items():
        code = _elective_program_code(sheet_name)
        if code in used_codes:
            suffix = 2
            while f"{code}_{suffix}" in used_codes:
                suffix += 1
            code = f"{code}_{suffix}"
        used_codes.add(code)
        programs.append(
            {
                "code": code,
                "name": sheet_name,
                "groups": group_ids,
            }
        )
    electives_section["programs"] = programs
    return updated


def _pattern_key_from_row(row: dict[str, Any]) -> PatternKey:
    class_tag = normalize_class_tag(row["lesson_class_type"])
    room = _normalize_room_value(row.get("room"))
    # Per-group labs use different rooms/times per audience; room belongs on each
    # session slot, not in the aggregation key (otherwise one component per room).
    if class_tag == "lab":
        room = ""
    return PatternKey(
        course=normalize_lesson_name(row["lesson_name"]),
        class_tag=class_tag,
        room=str(room or ""),
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


def load_elective_alias_by_subject(search_dirs: tuple[Path, ...]) -> dict[str, str]:
    aliases: dict[str, str] = {}
    candidates: list[Path] = []
    for directory in search_dirs:
        candidates.extend(directory.glob("electives-lessons*.json"))
        parsers_dir = directory.parent / "parsers"
        if parsers_dir.is_dir():
            candidates.extend(parsers_dir.glob("electives-lessons*.json"))
    seen_paths: set[Path] = set()
    for path in candidates:
        resolved = path.resolve()
        if resolved in seen_paths:
            continue
        seen_paths.add(resolved)
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(payload, list):
            continue
        for item in payload:
            if not isinstance(item, dict):
                continue
            subject = str(item.get("lesson_name") or item.get("subject") or "").strip()
            alias = str(item.get("course_name") or item.get("alias") or "").strip()
            if not subject or not alias or _is_parallel_stream_group_token(alias):
                continue
            aliases.setdefault(subject, alias)
    return aliases


def load_grouped_elective_lessons(path: Path) -> list[dict[str, Any]]:
    payload = _load_yaml_list(path, "Electives lessons")
    for index, item in enumerate(payload):
        if not isinstance(item, dict):
            raise ValueError(f"Electives lessons entry {index} must be an object")
        for key in (
            "subject",
            "audience",
            "occurrences",
            "spreadsheet_id",
            "google_sheet_gid",
            "google_sheet_name",
        ):
            if key not in item:
                raise ValueError(
                    f"Electives lessons entry {index} missing required field: {key}"
                )
    return payload


def resolve_lessons_search_dirs(input_path: Path) -> tuple[Path, ...]:
    dirs: list[Path] = []
    for directory in (input_path.parent, Path.cwd(), SCRIPT_DIR):
        resolved = directory.resolve()
        if resolved not in dirs:
            dirs.append(resolved)
    return tuple(dirs)


def expand_grouped_core_courses_to_rows(
    payload: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for course in payload:
        subject = course["subject"]
        for component in course["components"]:
            rows.append(
                {
                    "lesson_name": subject,
                    "lesson_class_type": component.get("type"),
                    "weekday": component["weekday"],
                    "start_time": component["start_time"],
                    "end_time": component["end_time"],
                    "room": component.get("room"),
                    "teacher": component.get("instructor"),
                    "group_name": normalize_group_names(component["audience"]),
                    "students_number": component.get("students_number"),
                    "modifiers": component.get("modifiers"),
                }
            )
    return rows


def load_core_courses_file(path: Path) -> list[dict[str, Any]]:
    payload = _load_yaml_list(path, "Core courses lessons")
    for index, item in enumerate(payload):
        if not isinstance(item, dict):
            raise ValueError(f"Core courses entry {index} must be an object")
        for key in (
            "subject",
            "components",
            "spreadsheet_id",
            "google_sheet_gid",
            "google_sheet_name",
        ):
            if key not in item:
                raise ValueError(
                    f"Core courses entry {index} missing required field: {key}"
                )
        if not item["components"]:
            raise ValueError(f"Core courses entry {index} has no components")
        for comp_index, component in enumerate(item["components"]):
            if not isinstance(component, dict):
                raise ValueError(
                    f"Core courses entry {index} component {comp_index} must be an object"
                )
            for key in ("weekday", "start_time", "end_time", "audience"):
                if key not in component:
                    raise ValueError(
                        f"Core courses entry {index} component {comp_index} missing required field: {key}"
                    )
    return expand_grouped_core_courses_to_rows(payload)


def _elective_instructor_pool(
    teacher_id_sets: list[tuple[str, ...]],
) -> list[str] | list[list[str]]:
    unique_signatures = sorted({sig for sig in teacher_id_sets if sig})
    if not unique_signatures:
        return []
    if len(unique_signatures) == 1:
        sig = unique_signatures[0]
        if len(sig) == 1:
            return [sig[0]]
        return [_unique_keep_order(list(sig))]
    if all(len(sig) == 1 for sig in unique_signatures):
        return sorted({sig[0] for sig in unique_signatures})
    return [_unique_keep_order(list(sig)) for sig in unique_signatures]


def merge_elective_courses(
    courses_map: dict[str, list[dict[str, Any]]],
    course_is_elective: dict[str, bool],
    grouped_electives: list[dict[str, Any]],
    instructors_map: dict[str, InstructorConfig],
    registry: InstructorRegistry,
    course_short_names: dict[str, str] | None = None,
    *,
    alias_by_subject: dict[str, str] | None = None,
) -> None:
    tag_order = {"lec": 0, "tut": 1, "lab": 2, "class": 3}
    by_course = _group_elective_lessons_by_course(grouped_electives)

    for course_name, lessons in by_course.items():
        alias = _elective_alias_token_for_lessons(
            lessons, alias_by_subject=alias_by_subject
        )
        if course_short_names is not None:
            course_short_names[course_name] = _elective_raw_alias_for_lessons(
                lessons,
                alias_by_subject=alias_by_subject,
            )
        parallels = _elective_parallel_groups_for_lessons(lessons)
        shared_lessons = [
            lesson for lesson in lessons if not _elective_parallel_stream_group(lesson)
        ]
        parallel_lessons: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for lesson in lessons:
            parallel = _elective_parallel_stream_group(lesson)
            if parallel:
                parallel_lessons[parallel].append(lesson)

        if parallels:
            audience = [
                elective_student_group_id(alias, parallel) for parallel in parallels
            ]
            if shared_lessons:
                shared_cls = _elective_component_from_lessons(
                    shared_lessons,
                    audience,
                    instructors_map,
                    registry,
                    shared_for_parallel_groups=True,
                )
                if shared_cls:
                    courses_map[course_name].append(shared_cls)
            for parallel in parallels:
                parallel_cls = _elective_component_from_lessons(
                    parallel_lessons[parallel],
                    [elective_student_group_id(alias, parallel)],
                    instructors_map,
                    registry,
                    shared_for_parallel_groups=False,
                )
                if parallel_cls:
                    courses_map[course_name].append(parallel_cls)
        else:
            single_cls = _elective_component_from_lessons(
                lessons,
                [elective_student_group_id(alias)],
                instructors_map,
                registry,
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
                tuple(cls.get("audience") or []),
            ),
        )


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
    parser.add_argument(
        "output_yaml", type=Path, nargs="?", default=Path("config-candidate.yaml")
    )
    parser.add_argument(
        "--rooms-json",
        type=Path,
        default=Path("rooms.json"),
        help="Path to rooms JSON export (optional)",
    )
    parser.add_argument(
        "--room-survey-json",
        type=Path,
        default=DEFAULT_ROOM_SURVEY_FEATURES_JSON,
        help=f"Parsed IU room survey features JSON (default: {DEFAULT_ROOM_SURVEY_FEATURES_JSON.name})",
    )
    parser.add_argument(
        "--room-survey-pdf",
        type=Path,
        default=None,
        help="Optional IU rooms PDF to re-extract survey features (overrides --room-survey-json)",
    )
    parser.add_argument(
        "--instructors-yaml",
        type=Path,
        default=DEFAULT_INSTRUCTORS_YAML,
        help=f"Pre-built instructors roster YAML (default: {DEFAULT_INSTRUCTORS_YAML.name})",
    )
    args = parser.parse_args()

    search_dirs = resolve_lessons_search_dirs(args.core_courses_yaml)
    core_courses_path = resolve_data_path(args.core_courses_yaml, *search_dirs)
    electives_path = resolve_data_path(args.electives_yaml, *search_dirs)

    rows: list[dict[str, Any]] = load_core_courses_file(core_courses_path)
    grouped_elective_lessons: list[dict[str, Any]] = []
    if electives_path.exists():
        grouped_elective_lessons = load_grouped_elective_lessons(electives_path)
    if not rows and not grouped_elective_lessons:
        raise ValueError("Core courses and electives inputs are both empty")

    rooms_json_path = resolve_data_path(args.rooms_json, *search_dirs)
    room_survey_json_path = resolve_data_path(args.room_survey_json, *search_dirs)
    room_survey_pdf_path = args.room_survey_pdf
    if (
        room_survey_pdf_path is None
        and not room_survey_json_path.exists()
        and DEFAULT_ROOM_SURVEY_PDF.exists()
    ):
        room_survey_pdf_path = DEFAULT_ROOM_SURVEY_PDF
    room_survey_features = load_room_survey_features(
        room_survey_json_path,
        pdf_path=room_survey_pdf_path,
    )
    instructors_yaml_path = resolve_data_path(args.instructors_yaml, *search_dirs)
    if not instructors_yaml_path.exists():
        raise FileNotFoundError(
            f"Instructors roster not found: {instructors_yaml_path}. "
            "Run instructors_roster.py first."
        )
    instructor_registry = InstructorRegistry.from_yaml(instructors_yaml_path)
    instructor_registry.lookup = load_instructor_lookup(
        resolve_users_csv_paths(None, Path.cwd(), SCRIPT_DIR, *search_dirs)
    )
    rooms = load_rooms(rooms_json_path, survey_features=room_survey_features)

    academic_groups = collect_academic_groups(PROGRAMS)
    elective_alias_by_subject = load_elective_alias_by_subject(search_dirs)
    elective_student_groups = collect_elective_student_groups(
        grouped_elective_lessons,
        alias_by_subject=elective_alias_by_subject,
    )
    sections = build_sections(PROGRAMS)
    if grouped_elective_lessons:
        sections = append_electives_to_sections(
            sections,
            grouped_elective_lessons,
            alias_by_subject=elective_alias_by_subject,
        )
    students_groups = build_students_groups(academic_groups)
    students_groups.extend(collect_english_groups())
    students_groups.extend(elective_student_groups)

    instructors_map: dict[str, InstructorConfig] = {}
    aggregated: dict[PatternKey, dict[str, Any]] = {}

    for r in rows:
        course = normalize_lesson_name(r["lesson_name"])
        if course.strip().lower() in {"group meeting with administration"}:
            continue
        if is_english_course(course):
            # English stays in its own section; ENG remapping is not ready yet.
            continue
        teacher_names = split_teacher_names(r["teacher"])
        teacher_signature = _resolve_teacher_signature(
            teacher_names, instructors_map, instructor_registry
        )
        key = _pattern_key_from_row(r)
        if key not in aggregated:
            aggregated[key] = {
                "groups": set(),
                "teacher_signatures": set(),
                "groups_by_signature": defaultdict(set),
                "slots_by_signature": defaultdict(lambda: defaultdict(set)),
                "occurrences_by_signature": defaultdict(lambda: defaultdict(list)),
                "edits_by_slot": defaultdict(list),
                "slots_by_group": defaultdict(set),
                "shared_group_batches": set(),
            }
        aggregated[key]["teacher_signatures"].add(teacher_signature)

        groups = normalize_group_names(r["group_name"])
        if len(groups) > 1:
            aggregated[key]["shared_group_batches"].add(frozenset(groups))
        aggregated[key]["groups_by_signature"][teacher_signature].update(groups)
        aggregated[key]["groups"].update(groups)
        modifiers = r.get("modifiers") or {}
        if _top_level_on_dates(modifiers) and not _nest_modifier_entries(modifiers):
            for date_value in _top_level_on_dates(modifiers):
                occurrence = _core_occurrence_from_row(
                    r, teacher_signature, date_value=date_value
                )
                for group_id in groups:
                    aggregated[key]["occurrences_by_signature"][teacher_signature][
                        group_id
                    ].append(occurrence)
        elif _nest_modifier_entries(modifiers):
            slot_sig: WeeklySlotSig = (
                r["weekday"],
                normalize_time(r["start_time"]),
                normalize_time(r["end_time"]),
                _normalize_room_value(r.get("room")),
                teacher_signature,
            )
            for group_id in groups:
                aggregated[key]["slots_by_group"][group_id].add(slot_sig)
                aggregated[key]["slots_by_signature"][teacher_signature][group_id].add(
                    slot_sig
                )
            aggregated[key]["edits_by_slot"][slot_sig].extend(
                _nest_edits_from_modifiers(modifiers)
            )
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
                aggregated[key]["slots_by_signature"][teacher_signature][group_id].add(
                    slot_sig
                )

    selector_map = build_group_selectors(PROGRAMS)
    group_order = build_group_order_from_sections(sections)

    def render_config(selected_rows: list[dict[str, Any]]) -> dict[str, Any]:
        selected_keys = {
            _pattern_key_from_row(row)
            for row in selected_rows
            if normalize_lesson_name(row["lesson_name"]).strip().lower()
            not in {"group meeting with administration"}
            and not is_english_course(normalize_lesson_name(row["lesson_name"]))
        }

        courses_map: dict[str, list[dict[str, Any]]] = defaultdict(list)
        course_is_elective: dict[str, bool] = defaultdict(bool)
        course_short_names: dict[str, str] = {}
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
                    groups_by_slot_fingerprint: dict[
                        tuple[WeeklySlotSig, ...], list[str]
                    ] = defaultdict(list)
                    for gid in groups_for_cls:
                        slot_fingerprint = tuple(sorted(slots_source.get(gid, set())))
                        groups_by_slot_fingerprint[slot_fingerprint].append(gid)
                    if len(groups_by_slot_fingerprint) > 1:
                        for cluster_groups in groups_by_slot_fingerprint.values():
                            filtered_slots = {
                                gid: slots_source[gid]
                                for gid in cluster_groups
                                if gid in slots_source
                            }
                            emission_variants.append(
                                (
                                    sorted(cluster_groups),
                                    filtered_slots,
                                    signatures_for_pool,
                                )
                            )
                        continue
                emission_variants.append(
                    (groups_for_cls, slots_source, signatures_for_pool)
                )

            for groups_for_cls, slots_source, signatures_for_pool in emission_variants:
                cls = {
                    "tag": pattern.class_tag,
                    "audience": _unique_keep_order(
                        compress_groups_to_selectors(
                            groups_for_cls, selector_map, group_order
                        )
                    ),
                }
                per_week = max(
                    (len(slots) for slots in slots_source.values()), default=1
                )
                if per_week != 1:
                    cls["per_week"] = per_week
                cls["instructor_pool"] = _build_instructor_pool(signatures_for_pool)
                is_shared_lesson = frozenset(groups_for_cls) in data.get(
                    "shared_group_batches", set()
                )
                if infer_per_group(
                    pattern.class_tag,
                    cls["audience"],
                    source_group_count=len(groups_for_cls),
                    is_shared_lesson=is_shared_lesson,
                ):
                    cls["per_group"] = True
                sessions = _core_sessions_from_slots(
                    groups_for_cls,
                    slots_source,
                    cls["audience"],
                    per_group=bool(cls.get("per_group")),
                    occurrences_by_signature=data["occurrences_by_signature"],
                    signatures_for_pool=signatures_for_pool,
                    edits_by_slot=data["edits_by_slot"],
                    selector_map=selector_map,
                    group_order=group_order,
                )
                if sessions:
                    cls["sessions"] = sessions
                courses_map[pattern.course].append(cls)

        merge_elective_courses(
            courses_map,
            course_is_elective,
            grouped_elective_lessons,
            instructors_map,
            instructor_registry,
            course_short_names,
            alias_by_subject=elective_alias_by_subject,
        )

        for course_name, components in courses_map.items():
            courses_map[course_name] = sorted(
                components,
                key=lambda cls: (
                    tag_order.get(cls.get("tag", ""), 99),
                    class_group_rank(cls, selector_map, group_order),
                ),
            )

        scheduled_instructor_ids: set[str] = set()
        for components in courses_map.values():
            scheduled_instructor_ids.update(
                _collect_instructor_ids_from_components(components)
            )

        # Final cross-script / spelling-variant merge + rewrite refs in courses.
        id_redirect = collapse_duplicate_instructors(instructors_map)
        if any(old != new for old, new in id_redirect.items()):
            for course_name, components in list(courses_map.items()):
                courses_map[course_name] = remap_instructor_ids_in_obj(
                    components, id_redirect
                )
            scheduled_instructor_ids = {
                id_redirect.get(instructor_id, instructor_id)
                for instructor_id in scheduled_instructor_ids
            }

        instructors = _build_output_instructors(
            instructors_map, instructor_registry, scheduled_instructor_ids
        )

        course_entries: list[tuple[bool, str, dict[str, Any]]] = []
        for course_name, components in courses_map.items():
            is_elective_course = course_is_elective.get(course_name, False)
            course_payload: dict[str, Any] = {
                "name": course_name,
                "section_code": derive_course_section_code(
                    course_name, components, sections
                ),
            }
            if is_elective_course:
                short_name = course_short_names.get(course_name)
                if short_name:
                    course_payload["short_name"] = short_name
            course_payload["components"] = components
            course_instructors = _derive_course_instructors(components)
            if course_instructors:
                course_payload["instructors"] = course_instructors
            course_entries.append(
                (
                    is_elective_course,
                    course_name,
                    course_payload,
                )
            )
        course_items = [
            item
            for _, _, item in sorted(
                course_entries,
                key=lambda entry: (
                    entry[0],
                    min(
                        (
                            class_group_rank(component, selector_map, group_order)
                            for component in entry[2].get("components", [])
                        ),
                        default=10**9,
                    ),
                    entry[1].casefold(),
                ),
            )
        ]

        sections_with_slots = attach_program_time_slots(
            sections, course_items, selector_map
        )
        sections_with_slots = attach_program_semesters(sections_with_slots)

        instructors = _normalize_output_instructors(instructors)
        return {
            "term": {
                "name": TERM_NAME,
                "semester": {
                    "start_date": TERM_START.isoformat(),
                    "end_date": TERM_END.isoformat(),
                },
                "instructor_positions": list(DEFAULT_INSTRUCTOR_POSITIONS),
                "course_instructor_roles": list(DEFAULT_COURSE_INSTRUCTOR_ROLES),
                "course_component_tags": list(DEFAULT_COURSE_COMPONENT_TAGS),
                "room_attributes": [dict(item) for item in DEFAULT_ROOM_ATTRIBUTES],
                "sections": sections_with_slots,
            },
            "rooms": rooms,
            "instructors": instructors,
            "students_groups": students_groups,
            "courses": course_items,
        }

    styled_config = apply_yaml_style_overrides(render_config(rows))
    args.output_yaml.write_text(dump_config_yaml(styled_config), encoding="utf-8")
    print(f"Wrote {args.output_yaml}")


if __name__ == "__main__":
    main()
