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
from instructors_roster import DEFAULT_INSTRUCTORS_YAML, InstructorRegistry

TERM_NAME = "Summer 2026"
TERM_START = date(2026, 6, 1)
TERM_END = date(2026, 7, 20)

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

def _group_entry_code(entry: Any) -> str:
    if isinstance(entry, str):
        return entry.strip()
    if isinstance(entry, dict):
        return str(entry.get("code") or entry.get("id") or "").strip()
    return ""


# Summer 2026 programs — groups referenced in core-courses-lessons-sum-2026.yaml.
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
                    "name": "Data Science",
                    "code": "DS",
                    "kind": "track",
                    "groups": ["M25-DS-01"],
                },
                {
                    "name": "Technological Entrepreneurship",
                    "code": "TE",
                    "kind": "track",
                    "groups": ["M25-TE-01"],
                },
            ],
        }
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
    "M25-DS-01": 26,
    "M25-TE-01": 17,
}


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


_ONLINE_ROOM_LABELS = frozenset({"online", "онлайн"})


def _normalize_room_value(room: str | None) -> str:
    if room is None:
        return ""
    trimmed = str(room).strip()
    if not trimmed:
        return ""
    if trimmed.casefold() in _ONLINE_ROOM_LABELS:
        return "ONLINE"
    return trimmed


# (weekday, start_time, end_time, room, instructor_ids)
WeeklySlotSig = tuple[str, str, str, str, tuple[str, ...]]

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


def _core_occurrence_from_row(
    row: dict[str, Any],
    instructor_ids: tuple[str, ...],
    *,
    date_value: str,
) -> dict[str, Any]:
    modifiers = row.get("modifiers") or {}
    room = _normalize_room_value(row.get("room")) or _normalize_room_value(modifiers.get("location"))
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
                    "room": _normalize_room_value(str(location)),
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


def _add_instructor_ref(target: set[str], value: str | list[str] | None) -> None:
    if value is None:
        return
    if isinstance(value, str):
        target.add(value)
        return
    target.update(value)


def _collect_instructor_ids_from_components(components: list[dict[str, Any]]) -> set[str]:
    ids: set[str] = set()
    for component in components:
        for item in component.get("instructor_pool") or []:
            _add_instructor_ref(ids, item)
        for session in component.get("sessions") or []:
            for pattern in session.get("weekly_pattern") or []:
                _add_instructor_ref(ids, pattern.get("instructor"))
                for edit in pattern.get("edits") or []:
                    _add_instructor_ref(ids, edit.get("instructor"))
            for occurrence in session.get("occurrences") or []:
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


def split_teacher_names(raw: str | None) -> list[str]:
    if not raw:
        return []
    parts = [p.strip() for p in raw.split(",")]
    names = [p for p in parts if p]
    return names


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


def build_group_selectors(programs: dict[str, list[dict[str, Any]]]) -> dict[str, set[str]]:
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
                track_groups = {_group_entry_code(g) for g in track.get("groups", []) if _group_entry_code(g)}
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


def collect_academic_groups(programs: dict[str, list[dict[str, Any]]]) -> list[dict[str, Any]]:
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
    degree_by_level = {"bachelor": "bs", "master": "ms"}
    core_programs: list[dict[str, Any]] = []
    for level_name in ("bachelor", "master"):
        for program in programs.get(level_name, []):
            tracks = []
            for track in program.get("tracks", []):
                track_name = track.get("name", "")
                track_groups = [g for g in track.get("groups", []) if _group_entry_code(g)]
                tracks.append(
                    {
                        "code": str(track.get("code") or _slug_code(track_name).upper()),
                        "name": track.get("name"),
                        "kind": str(track.get("kind") or "track"),
                        "groups": track_groups,
                    }
                )
            core_programs.append(
                {
                    "code": str(program.get("code") or program.get("id") or "").strip(),
                    "name": program.get("name"),
                    "kind": "degree_year",
                    "degree": degree_by_level.get(level_name),
                    "language": program.get("language"),
                    "year": program.get("year"),
                    "tracks": tracks,
                }
            )
    return [{"code": "core", "name": "Основные курсы", "kind": "core", "programs": core_programs}]


def build_students_groups(academic_groups: list[dict[str, Any]]) -> list[dict[str, Any]]:
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


def infer_per_group(
    class_tag: str,
    student_groups: list[str],
    *,
    source_group_count: int | None = None,
    is_shared_lesson: bool = False,
) -> bool:
    if is_shared_lesson:
        return False
    effective_group_count = source_group_count if source_group_count is not None else len(student_groups)
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
    instructors_map: dict[str, InstructorConfig],
    registry: InstructorRegistry,
) -> list[dict[str, Any]]:
    entries_by_key: dict[tuple[str, str, str, str | None, str | None], tuple[dict[str, Any], tuple[str, ...]]] = {}
    for lesson in lessons:
        teacher_ids = tuple(
            sorted(
                registry.resolve_into_map(name, instructors_map)
                for name in split_teacher_names(lesson.get("instructor"))
            )
        )
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
            "audience": list(audience),
            "occurrences": occurrences,
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
    duration_slots = max(_elective_duration_slots(lesson) for lesson in lessons)
    teacher_signatures = [
        tuple(
            sorted(
                registry.resolve_into_map(name, instructors_map)
                for name in split_teacher_names(lesson.get("instructor"))
            )
        )
        for lesson in lessons
    ]
    representative = lessons[0]
    sessions = _elective_sessions_from_lessons(lessons, student_groups, instructors_map, registry)
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
        course_name = str(lesson["subject"]).strip()
        if not course_name or course_name.strip().lower() in {"group meeting with administration"}:
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
        alias = _elective_alias_token_for_lessons(lessons, alias_by_subject=alias_by_subject)
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


def expand_grouped_core_courses_to_rows(payload: list[dict[str, Any]]) -> list[dict[str, Any]]:
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


def _elective_duration_slots(lesson: dict[str, Any]) -> int:
    max_slots = 1
    for occurrence in lesson.get("occurrences") or []:
        start_raw = occurrence.get("start_time")
        end_raw = occurrence.get("end_time")
        if not start_raw or not end_raw:
            continue
        delta = datetime.strptime(normalize_time(end_raw), "%H:%M") - datetime.strptime(
            normalize_time(start_raw), "%H:%M"
        )
        duration_minutes = abs(int(delta.total_seconds())) // 60
        max_slots = max(max_slots, max(1, round(duration_minutes / 90)))
    return max_slots


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
    instructors_map: dict[str, InstructorConfig],
    registry: InstructorRegistry,
    course_short_names: dict[str, str] | None = None,
    *,
    alias_by_subject: dict[str, str] | None = None,
) -> None:
    tag_order = {"lec": 0, "tut": 1, "lab": 2, "class": 3}
    by_course = _group_elective_lessons_by_course(grouped_electives)

    for course_name, lessons in by_course.items():
        alias = _elective_alias_token_for_lessons(lessons, alias_by_subject=alias_by_subject)
        if course_short_names is not None:
            course_short_names[course_name] = _elective_raw_alias_for_lessons(
                lessons,
                alias_by_subject=alias_by_subject,
            )
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
                tuple(cls.get("student_groups") or []),
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
    parser.add_argument("output_yaml", type=Path, nargs="?", default=Path("config-candidate.yaml"))
    parser.add_argument(
        "--rooms-json",
        type=Path,
        default=Path("rooms.json"),
        help="Path to rooms JSON export (optional)",
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
    instructors_yaml_path = resolve_data_path(args.instructors_yaml, *search_dirs)
    if not instructors_yaml_path.exists():
        raise FileNotFoundError(
            f"Instructors roster not found: {instructors_yaml_path}. "
            "Run instructors_roster.py first."
        )
    instructor_registry = InstructorRegistry.from_yaml(instructors_yaml_path)
    rooms = load_rooms(rooms_json_path)

    academic_groups = collect_academic_groups(PROGRAMS)
    elective_alias_by_subject = load_elective_alias_by_subject(search_dirs)
    summer_elective_student_groups = collect_elective_student_groups(
        grouped_elective_lessons,
        alias_by_subject=elective_alias_by_subject,
    )
    summer_elective_group_ids = [str(group["code"]) for group in summer_elective_student_groups if group.get("code")]
    sections = build_sections(PROGRAMS)
    sections = append_summer_electives_to_sections(sections, summer_elective_group_ids)
    students_groups = build_students_groups(academic_groups)
    students_groups.extend(summer_elective_student_groups)

    instructors_map: dict[str, InstructorConfig] = {}
    aggregated: dict[PatternKey, dict[str, Any]] = {}

    for r in rows:
        course = normalize_lesson_name(r["lesson_name"])
        if course.strip().lower() in {"group meeting with administration"}:
            continue
        teacher_names = split_teacher_names(r["teacher"])
        teacher_signature = tuple(
            sorted(instructor_registry.resolve_into_map(name, instructors_map) for name in teacher_names)
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
                "duration_slots": 1,
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
                occurrence = _core_occurrence_from_row(r, teacher_signature, date_value=date_value)
                for group_id in groups:
                    aggregated[key]["occurrences_by_signature"][teacher_signature][group_id].append(occurrence)
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
    selector_map = build_group_selectors(PROGRAMS)
    group_order = build_group_order_from_sections(sections)

    def render_config(selected_rows: list[dict[str, Any]]) -> dict[str, Any]:
        selected_keys = {
            _pattern_key_from_row(row)
            for row in selected_rows
            if normalize_lesson_name(row["lesson_name"]).strip().lower()
            not in {"group meeting with administration"}
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
                if data["duration_slots"] != 1:
                    cls["duration_slots"] = data["duration_slots"]
                cls["instructor_pool"] = _build_instructor_pool(signatures_for_pool)
                is_shared_lesson = frozenset(groups_for_cls) in data.get("shared_group_batches", set())
                if infer_per_group(
                    pattern.class_tag,
                    cls["student_groups"],
                    source_group_count=len(groups_for_cls),
                    is_shared_lesson=is_shared_lesson,
                ):
                    cls["per_group"] = True
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
            scheduled_instructor_ids.update(_collect_instructor_ids_from_components(components))
        instructors = _build_output_instructors(instructors_map, instructor_registry, scheduled_instructor_ids)

        course_entries: list[tuple[bool, str, dict[str, Any]]] = []
        for course_name, components in courses_map.items():
            is_elective_course = course_is_elective.get(course_name, False)
            course_payload: dict[str, Any] = {"name": course_name}
            if is_elective_course:
                short_name = course_short_names.get(course_name)
                if short_name:
                    course_payload["short_name"] = short_name
            course_payload["course_tags"] = ["elective"] if is_elective_course else ["core_course"]
            course_payload["components"] = components
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

        return {
            "term": {
                "name": TERM_NAME,
                "semester": {"start_date": TERM_START.isoformat(), "end_date": TERM_END.isoformat()},
            },
            "rooms": rooms,
            "instructors": instructors,
            "sections": sections,
            "students_groups": students_groups,
            "courses": course_items,
        }

    styled_config = apply_yaml_style_overrides(render_config(rows))
    args.output_yaml.write_text(dump_config_yaml(styled_config), encoding="utf-8")
    print(f"Wrote {args.output_yaml}")


if __name__ == "__main__":
    main()
