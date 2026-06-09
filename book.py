"""prompt_toolkit TUI to preview and batch-book courses from ScheduleConfig via room-booking BMP API."""

from __future__ import annotations

import argparse
import os
import re
import shutil
import sys
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from enum import Enum
from pathlib import Path
from typing import Any, Iterable
from zoneinfo import ZoneInfo

import httpx
from prompt_toolkit import Application
from prompt_toolkit.formatted_text import FormattedText
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.layout import FormattedTextControl, Layout, Window
from prompt_toolkit.styles import Style
from prompt_toolkit.widgets import Box, Frame

from config import (
    ComponentSessionSeries,
    CourseConfig,
    ScheduleConfig,
    TermConfig,
    WeeklyPatternSlot,
    WeeklyPatternSlotEdit,
    week_start_for_date,
)

MSK = ZoneInfo("Europe/Moscow")
SCRIPT_DIR = Path(__file__).resolve().parent
BOOK_TREE_STYLE = Style.from_dict({"strike": "strike"})
DEFAULT_CONFIG = SCRIPT_DIR / "config-candidate.yaml"
DEFAULT_API_BASE = "http://127.0.0.1:8008"
DEFAULT_API_URL = f"{DEFAULT_API_BASE}/bmp/auto-bookings/batch"
DEFAULT_AUTO_BOOKINGS_BATCH_CANCEL_URL = f"{DEFAULT_API_BASE}/bmp/auto-bookings/batch"
DEFAULT_BOOKINGS_URL = f"{DEFAULT_API_BASE}/bookings/"
HTTP_TIMEOUT_SECONDS = 300.0
NON_BOOKABLE_ROOM_LABELS = frozenset({"online", "онлайн"})
_GROUP_SUFFIX_RE = re.compile(r"-G(\d+)$", re.IGNORECASE)
_LAB_TITLE_AUDIENCE_RE = re.compile(r"\(lab,\s*(.+)\)$")
_SLOT_TITLE_RE = re.compile(r"^(.+?) \((\w+)(?:,\s*(.+))?\)$")
_SLOT_TITLE_LOOSE_RE = re.compile(r"^(.+?) \((\w+)(?:,\s*(.+))?\).*$")
_SCHEDULE_ASSISTANT_IU_TITLE_RE = re.compile(
    r"^Schedule Assistant IU (?:Auto:\s*)?",
    re.IGNORECASE,
)
_AUTO_TITLE_PREFIXES = (
    "Schedule Assistant IU Auto:",
    "Auto:",
)
_BOOKING_TITLE_FORWARD_PREFIXES = ("FW:", "RE:", "Fwd:")
_RECURRENCE_DAY_RE = re.compile(r"<t:DaysOfWeek>([^<]+)</t:DaysOfWeek>")
_RECURRENCE_START_RE = re.compile(r"<t:StartDate>([^<]+)</t:StartDate>")
_RECURRENCE_END_RE = re.compile(r"<t:EndDate>([^<]+)</t:EndDate>")
_DAY_NAME_TO_BYDAY = {
    "MONDAY": "MO",
    "TUESDAY": "TU",
    "WEDNESDAY": "WE",
    "THURSDAY": "TH",
    "FRIDAY": "FR",
    "SATURDAY": "SA",
    "SUNDAY": "SU",
    "MON": "MO",
    "TUE": "TU",
    "WED": "WE",
    "THU": "TH",
    "FRI": "FR",
    "SAT": "SA",
    "SUN": "SU",
}
_BYDAY_TO_API_WEEKDAY = {
    "MO": "monday",
    "TU": "tuesday",
    "WE": "wednesday",
    "TH": "thursday",
    "FR": "friday",
    "SA": "saturday",
    "SU": "sunday",
}
_API_WEEKDAY_TO_PYTHON = {
    "monday": 0,
    "tuesday": 1,
    "wednesday": 2,
    "thursday": 3,
    "friday": 4,
    "saturday": 5,
    "sunday": 6,
}
@dataclass(frozen=True)
class SlotRow:
    slot_id: str
    date: str
    start_time: str
    end_time: str
    room: str | None
    bookable: bool
    disabled_reason: str | None
    recurring: bool = False
    recurrence: dict[str, Any] | None = None

    def label(self) -> str:
        room_text = self.room if self.room else "—"
        suffix = f" ({self.disabled_reason})" if self.disabled_reason else ""
        schedule = f"{self.date}  {self.start_time}–{self.end_time}"
        if self.recurring:
            schedule = f"Weekly {schedule}"
        return f"{schedule}  @ {room_text}{suffix}"


@dataclass(frozen=True)
class PlacedComponent:
    """One component session series for one or more audiences in the same session."""

    course: CourseConfig
    component: CourseConfig.Component
    session: ComponentSessionSeries
    audiences: list[str]
    program_name: str
    term: TermConfig


@dataclass(frozen=True)
class ComponentNode:
    component_id: str
    label: str
    placed: PlacedComponent
    slots: list[SlotRow]


@dataclass(frozen=True)
class CourseNode:
    course_id: str
    name: str
    components: list[ComponentNode]


@dataclass(frozen=True)
class ProgramGroup:
    name: str
    courses: list[CourseNode]


def _normalize_room(room: str | None) -> str | None:
    if room is None:
        return None
    trimmed = room.strip()
    if not trimmed:
        return None
    if trimmed.lower() in NON_BOOKABLE_ROOM_LABELS:
        return "online"
    return trimmed


def _slot_bookable(room: str | None, known_room_ids: set[str]) -> tuple[bool, str | None]:
    if room is None:
        return False, "no room"
    if room.lower() in NON_BOOKABLE_ROOM_LABELS:
        return False, "online"
    if room not in known_room_ids:
        return False, "unknown room"
    return True, None


def _weekday_to_byday(day: str) -> str:
    token = day.strip().upper()
    if token in _DAY_NAME_TO_BYDAY:
        return _DAY_NAME_TO_BYDAY[token]
    if len(token) >= 2 and token[:2] in _DAY_NAME_TO_BYDAY.values():
        return token[:2]
    return token[:2]


def _weekday_api_value(day: str) -> str:
    byday = _weekday_to_byday(day)
    return _BYDAY_TO_API_WEEKDAY.get(byday, day.strip().lower())


def _weekday_display_name(weekday_api: str) -> str:
    return weekday_api.strip().capitalize()


def _group_name_from_code(group_code: str) -> str | None:
    match = _GROUP_SUFFIX_RE.search(group_code.strip())
    if match:
        return f"G{match.group(1)}"
    return None


def _section_label(course_tags: list[str]) -> str:
    for tag in course_tags:
        if tag == "core_course":
            return "core"
        if tag:
            return tag
    return "unknown"


def _program_code_from_audiences(audiences: list[str]) -> str:
    for audience in audiences:
        code = audience.strip()
        if code.startswith("@"):
            return code.removeprefix("@")
    return audiences[0].strip() if audiences else "unknown"


def _booking_categories(placed: PlacedComponent) -> list[str]:
    return [
        _section_label(list(placed.course.course_tags)),
        _program_code_from_audiences(placed.audiences),
        placed.course.name,
    ]


def program_track_label(section_name: str, program_name: str) -> str:
    return f"{section_name} / {program_name}"


def build_section_program_maps(
    cfg: ScheduleConfig,
) -> tuple[dict[str, str], dict[str, str]]:
    group_to_program: dict[str, str] = {}
    selector_to_program: dict[str, str] = {}
    for section in cfg.sections:
        for program in section.programs:
            label = program_track_label(section.name, program.name)
            selector_to_program[f"@{program.code}"] = label
            for group in program.groups:
                group_to_program[group] = label
            for track in program.tracks:
                for group in track.groups:
                    group_to_program[group] = label
                selector_to_program[f"@{program.code}/{track.name}"] = label
                selector_to_program[f"@{program.code}/{track.code}"] = label
    return group_to_program, selector_to_program


def resolve_program_name(
    audience: str,
    *,
    group_to_program: dict[str, str],
    selector_to_program: dict[str, str],
) -> str:
    if audience in group_to_program:
        return group_to_program[audience]
    if audience in selector_to_program:
        return selector_to_program[audience]
    if audience.startswith("@"):
        return selector_to_program.get(audience, audience.removeprefix("@"))
    return "Unknown program"


def _session_audiences(component: CourseConfig.Component, session: ComponentSessionSeries) -> list[str]:
    if session.audience:
        return list(session.audience)
    return list(component.student_groups)


def _audiences_key(audiences: list[str]) -> str:
    return ",".join(sorted(audiences))


def _resolve_program_for_audiences(
    audiences: list[str],
    *,
    group_to_program: dict[str, str],
    selector_to_program: dict[str, str],
) -> str:
    program_names = {
        resolve_program_name(audience, group_to_program=group_to_program, selector_to_program=selector_to_program)
        for audience in audiences
    }
    if len(program_names) == 1:
        return program_names.pop()
    return resolve_program_name(audiences[0], group_to_program=group_to_program, selector_to_program=selector_to_program)


def _component_label(component_tag: str, audiences: list[str]) -> str:
    audience_text = ", ".join(audiences)
    groups = [group for audience in audiences if (group := _group_name_from_code(audience))]
    if groups:
        return f"{component_tag} · {audience_text} ({', '.join(groups)})"
    return f"{component_tag} · {audience_text}"


def _weekly_recurrence_for_segment(term: TermConfig, day: str, segment_start: date, segment_end: date) -> dict[str, str]:
    return {
        "kind": "weekly_until",
        "weekday": _weekday_api_value(day),
        "start_date": segment_start.isoformat(),
        "until_date": segment_end.isoformat(),
    }


def _weekly_meeting_dates_in_term(term: TermConfig, weekday: str) -> list[date]:
    weekday_api = _weekday_api_value(weekday)
    target = _API_WEEKDAY_TO_PYTHON[weekday_api]
    dates: list[date] = []
    current = term.semester.start_date
    end = term.semester.end_date
    while current <= end:
        if current.weekday() == target:
            dates.append(current)
        current += timedelta(days=1)
    return dates


def _edit_for_meeting_date(
    meeting_date: date,
    edits: list[WeeklyPatternSlotEdit],
    term: TermConfig,
) -> WeeklyPatternSlotEdit | None:
    week_key = week_start_for_date(meeting_date, term.starting_day)
    for edit in edits:
        if week_start_for_date(edit.select_week, term.starting_day) == week_key:
            return edit
    return None


def _edit_changes_meeting(edit: WeeklyPatternSlotEdit) -> bool:
    if edit.cancel:
        return True
    return any(
        value is not None
        for value in (edit.date, edit.start_time, edit.end_time, edit.room, edit.instructor)
    )


def _resolved_weekly_meeting(
    pattern: WeeklyPatternSlot,
    meeting_date: date,
    edit: WeeklyPatternSlotEdit | None,
) -> tuple[date, str, str, str | None] | None:
    if edit and edit.cancel:
        return None
    resolved_date = edit.date if edit and edit.date else meeting_date
    start_time = (edit.start_time if edit and edit.start_time else pattern.start_time).strftime("%H:%M:%S")
    end_time = (edit.end_time if edit and edit.end_time else pattern.end_time).strftime("%H:%M:%S")
    room = _normalize_room(edit.room if edit and edit.room else pattern.room)
    return resolved_date, start_time, end_time, room


def _recurrence_segments_excluding_edit_weeks(
    term: TermConfig,
    weekday: str,
    excluded_week_starts: set[date],
) -> list[tuple[date, date]]:
    meeting_dates = _weekly_meeting_dates_in_term(term, weekday)
    active_dates = [
        meeting_date
        for meeting_date in meeting_dates
        if week_start_for_date(meeting_date, term.starting_day) not in excluded_week_starts
    ]
    if not active_dates:
        return []

    segments: list[tuple[date, date]] = []
    group_start = active_dates[0]
    previous = active_dates[0]
    for current in active_dates[1:]:
        if (current - previous).days == 7:
            previous = current
            continue
        segments.append((group_start, previous))
        group_start = current
        previous = current
    segments.append((group_start, previous))
    return segments


def _make_slot_row(
    *,
    slot_id: str,
    meeting_date: date | str,
    start_time: str,
    end_time: str,
    room: str | None,
    known_room_ids: set[str],
    recurring: bool = False,
    recurrence: dict[str, str] | None = None,
) -> SlotRow:
    bookable, reason = _slot_bookable(room, known_room_ids)
    return SlotRow(
        slot_id=slot_id,
        date=meeting_date.isoformat() if isinstance(meeting_date, date) else str(meeting_date),
        start_time=start_time,
        end_time=end_time,
        room=room,
        bookable=bookable,
        disabled_reason=reason,
        recurring=recurring,
        recurrence=recurrence,
    )


def _slots_from_weekly_pattern(
    pattern: WeeklyPatternSlot,
    term: TermConfig,
    known_room_ids: set[str],
    *,
    slot_id_prefix: str,
    pattern_index: int,
) -> list[SlotRow]:
    edits = list(pattern.edits or [])
    day_label = str(pattern.weekday)
    start_time = pattern.start_time.strftime("%H:%M:%S")
    end_time = pattern.end_time.strftime("%H:%M:%S")
    base_room = _normalize_room(pattern.room)

    slots: list[SlotRow] = []
    excluded_week_starts: set[date] = set()
    for meeting_date in _weekly_meeting_dates_in_term(term, day_label):
        edit = _edit_for_meeting_date(meeting_date, edits, term)
        if edit is None or not _edit_changes_meeting(edit):
            continue
        excluded_week_starts.add(week_start_for_date(meeting_date, term.starting_day))
        resolved = _resolved_weekly_meeting(pattern, meeting_date, edit)
        if resolved is None:
            continue
        resolved_date, resolved_start, resolved_end, resolved_room = resolved
        slots.append(
            _make_slot_row(
                slot_id=f"{slot_id_prefix}#w{pattern_index}#e{resolved_date.isoformat()}",
                meeting_date=resolved_date,
                start_time=resolved_start,
                end_time=resolved_end,
                room=resolved_room,
                known_room_ids=known_room_ids,
            )
        )

    for segment_index, (segment_start, segment_end) in enumerate(
        _recurrence_segments_excluding_edit_weeks(term, day_label, excluded_week_starts)
    ):
        slots.append(
            _make_slot_row(
                slot_id=f"{slot_id_prefix}#w{pattern_index}#s{segment_index}",
                meeting_date=day_label,
                start_time=start_time,
                end_time=end_time,
                room=base_room,
                known_room_ids=known_room_ids,
                recurring=True,
                recurrence=_weekly_recurrence_for_segment(term, day_label, segment_start, segment_end),
            )
        )
    return slots


def _slots_from_session(
    session: ComponentSessionSeries,
    term: TermConfig,
    known_room_ids: set[str],
    *,
    slot_id_prefix: str,
) -> list[SlotRow]:
    slots: list[SlotRow] = []
    for index, occurrence in enumerate(session.occurrences or []):
        start_time = occurrence.start_time.strftime("%H:%M:%S")
        end_time = occurrence.end_time.strftime("%H:%M:%S")
        room = _normalize_room(occurrence.room)
        bookable, reason = _slot_bookable(room, known_room_ids)
        slots.append(
            SlotRow(
                slot_id=f"{slot_id_prefix}#d{index}",
                date=occurrence.date.isoformat(),
                start_time=start_time,
                end_time=end_time,
                room=room,
                bookable=bookable,
                disabled_reason=reason,
            )
        )

    for index, pattern in enumerate(session.weekly_pattern or []):
        slots.extend(
            _slots_from_weekly_pattern(
                pattern,
                term,
                known_room_ids,
                slot_id_prefix=slot_id_prefix,
                pattern_index=index,
            )
        )
    return slots


def build_program_groups_from_config(cfg: ScheduleConfig) -> list[ProgramGroup]:
    known_room_ids = {room.id for room in cfg.rooms}
    group_to_program, selector_to_program = build_section_program_maps(cfg)
    by_program: dict[str, dict[str, list[ComponentNode]]] = defaultdict(lambda: defaultdict(list))

    for course in cfg.courses:
        for component in course.components:
            for session_index, session in enumerate(component.sessions or []):
                session_audiences = _session_audiences(component, session)
                if not session_audiences:
                    continue
                program_name = _resolve_program_for_audiences(
                    session_audiences,
                    group_to_program=group_to_program,
                    selector_to_program=selector_to_program,
                )
                audiences_key = _audiences_key(session_audiences)
                component_id = f"{program_name}|{course.name}|{component.tag}|{audiences_key}|s{session_index}"
                slots = _slots_from_session(
                    session,
                    cfg.term,
                    known_room_ids,
                    slot_id_prefix=component_id,
                )
                if not slots:
                    continue
                placed = PlacedComponent(
                    course=course,
                    component=component,
                    session=session,
                    audiences=session_audiences,
                    program_name=program_name,
                    term=cfg.term,
                )
                by_program[program_name][course.name].append(
                    ComponentNode(
                        component_id=component_id,
                        label=_component_label(str(component.tag), session_audiences),
                        placed=placed,
                        slots=slots,
                    )
                )

    programs: list[ProgramGroup] = []
    for program_name in sorted(by_program):
        course_nodes: list[CourseNode] = []
        for course_name in sorted(by_program[program_name], key=str.casefold):
            components = sorted(
                by_program[program_name][course_name],
                key=lambda node: (node.label.casefold(), node.component_id),
            )
            course_nodes.append(
                CourseNode(
                    course_id=f"{program_name}|{course_name}",
                    name=course_name,
                    components=components,
                )
            )
        programs.append(ProgramGroup(name=program_name, courses=course_nodes))
    return programs


def _format_clock_short(clock: str) -> str:
    hour, minute, *_ = clock.split(":")
    return f"{hour}:{minute}"


def _human_date(value: date) -> str:
    return f"{value.strftime('%B')} {value.day}"


def _lab_audience_from_title(title: str) -> str | None:
    match = _LAB_TITLE_AUDIENCE_RE.search(title)
    if not match:
        return None
    return match.group(1).strip()


def _schedule_detail_text(
    *,
    room_id: str | None,
    start: datetime,
    end: datetime,
    recurrence: dict[str, Any] | None,
    audience_label: str | None = None,
) -> str:
    room_text = f"@{room_id}" if room_id else "@—"
    if audience_label:
        room_text = f"{room_text} {audience_label}"
    time_text = (
        f"{_format_clock_short(start.strftime('%H:%M:%S'))}"
        f"-{_format_clock_short(end.strftime('%H:%M:%S'))}"
    )
    if recurrence:
        weekday = _weekday_display_name(str(recurrence["weekday"]))
        start_d = date.fromisoformat(str(recurrence["start_date"]))
        end_d = date.fromisoformat(str(recurrence["until_date"]))
        return (
            f"  {room_text}    each {weekday} at {time_text}, "
            f"from {_human_date(start_d)} until {_human_date(end_d)}"
        )
    return f"  {room_text}    on {_human_date(start.date())} at {time_text}"


def _booking_schedule_detail(slot: SlotRow) -> str:
    if slot.recurring and slot.recurrence:
        start_d = date.fromisoformat(str(slot.recurrence["start_date"]))
        end_d = date.fromisoformat(str(slot.recurrence["until_date"]))
        start = datetime.fromisoformat(f"{start_d.isoformat()}T{slot.start_time}").replace(tzinfo=MSK)
        end = datetime.fromisoformat(f"{end_d.isoformat()}T{slot.end_time}").replace(tzinfo=MSK)
        return _schedule_detail_text(
            room_id=slot.room,
            start=start,
            end=end,
            recurrence=slot.recurrence,
        )
    meeting_date = date.fromisoformat(slot.date)
    start = datetime.fromisoformat(f"{meeting_date.isoformat()}T{slot.start_time}").replace(tzinfo=MSK)
    end = datetime.fromisoformat(f"{meeting_date.isoformat()}T{slot.end_time}").replace(tzinfo=MSK)
    return _schedule_detail_text(room_id=slot.room, start=start, end=end, recurrence=None)


def _payload_schedule_detail(payload: dict[str, Any]) -> str:
    recurrence = payload.get("recurrence")
    start = _parse_booking_datetime(str(payload["start"]))
    end = _parse_booking_datetime(str(payload["end"]))
    title = str(payload.get("title") or "")
    return _schedule_detail_text(
        room_id=str(payload.get("room_id")) if payload.get("room_id") else None,
        start=start,
        end=end,
        recurrence=recurrence if isinstance(recurrence, dict) else None,
        audience_label=_lab_audience_from_title(title),
    )


def _payload_line(payload: dict[str, Any], status: str | None = None) -> str:
    line = _payload_schedule_detail(payload)
    if status:
        return f"{line}   {status}"
    return line


def _booking_categories_key(categories: Iterable[Any]) -> tuple[str, ...]:
    return tuple(str(category) for category in categories if str(category) != "Auto")


def _auto_recurrence_fields(recurrence: Any) -> dict[str, str] | None:
    if not isinstance(recurrence, str):
        return None
    day_match = _RECURRENCE_DAY_RE.search(recurrence)
    if not day_match:
        return None
    start_match = _RECURRENCE_START_RE.search(recurrence)
    end_match = _RECURRENCE_END_RE.search(recurrence)
    return {
        "weekday": day_match.group(1).strip().lower(),
        "start_date": start_match.group(1).strip() if start_match else "",
        "until_date": end_match.group(1).strip() if end_match else "",
    }


def payload_matches_auto_booking(payload: dict[str, Any], auto_booking: dict[str, Any]) -> bool:
    if str(payload.get("room_id")) != str(auto_booking.get("room_id")):
        return False

    payload_categories = payload.get("categories")
    auto_categories = auto_booking.get("categories")
    if not isinstance(payload_categories, list) or not isinstance(auto_categories, list):
        return False
    if not _auto_booking_categories_match(payload_categories, auto_categories):
        return False

    payload_start = _parse_booking_datetime(str(payload["start"]))
    payload_end = _parse_booking_datetime(str(payload["end"]))
    auto_start = _parse_booking_datetime(str(auto_booking["start"]))
    auto_end = _parse_booking_datetime(str(auto_booking["end"]))
    if payload_start.time() != auto_start.time() or payload_end.time() != auto_end.time():
        return False

    payload_recurrence = payload.get("recurrence")
    auto_recurrence = _auto_recurrence_fields(auto_booking.get("recurrence"))
    if isinstance(payload_recurrence, dict):
        if auto_recurrence is None:
            return False
        if str(payload_recurrence.get("weekday", "")).strip().lower() != auto_recurrence["weekday"]:
            return False
        if str(payload_recurrence.get("start_date", "")) != auto_recurrence["start_date"]:
            return False
        if str(payload_recurrence.get("until_date", "")) != auto_recurrence["until_date"]:
            return False
        return True

    if auto_recurrence is not None:
        return False
    return payload_start.date() == auto_start.date() and payload_end.date() == auto_end.date()


def _strip_forwarding_title_prefix(title: str) -> str:
    text = title.strip()
    while True:
        stripped = False
        for prefix in _BOOKING_TITLE_FORWARD_PREFIXES:
            if text.casefold().startswith(prefix.casefold()):
                text = text[len(prefix) :].strip()
                stripped = True
                break
        if not stripped:
            return text


def _strip_auto_booking_title_prefix(title: str) -> str:
    text = _strip_forwarding_title_prefix(title)
    for prefix in _AUTO_TITLE_PREFIXES:
        if text.casefold().startswith(prefix.casefold()):
            return text[len(prefix) :].strip()
    return text


def _normalized_auto_title(title: str) -> str:
    return _strip_auto_booking_title_prefix(title)


def _is_schedule_assistant_auto_title(title: str) -> bool:
    text = _strip_forwarding_title_prefix(title)
    return any(text.casefold().startswith(prefix.casefold()) for prefix in _AUTO_TITLE_PREFIXES)


def _normalize_booking_title_for_parse(title: str) -> str:
    text = _strip_auto_booking_title_prefix(title).strip()
    return _SCHEDULE_ASSISTANT_IU_TITLE_RE.sub("", text, count=1).strip()


def _parse_slot_title(title: str) -> tuple[str, str, str | None] | None:
    match = _SLOT_TITLE_RE.match(_normalize_booking_title_for_parse(title))
    if not match:
        return None
    audience = match.group(3)
    return match.group(1).strip(), match.group(2).strip(), audience.strip() if audience else None


def _course_and_component_from_title(title: str) -> tuple[str, str | None]:
    text = _normalize_booking_title_for_parse(title)
    match = _SLOT_TITLE_RE.match(text) or _SLOT_TITLE_LOOSE_RE.match(text)
    if match:
        return match.group(1).strip(), match.group(2).strip()
    return text, None


def _course_names_match_via_categories(booking: dict[str, Any], payload: dict[str, Any]) -> bool:
    payload_categories = payload.get("categories")
    booking_categories = booking.get("categories")
    if not isinstance(payload_categories, list) or not isinstance(booking_categories, list):
        return False
    payload_course = _course_name_from_booking_categories(payload_categories)
    booking_course = _course_name_from_booking_categories(booking_categories)
    if not payload_course or not booking_course:
        return False
    return _course_names_match_for_auto(payload_course, booking_course)


def _program_code_from_booking_categories(categories: Iterable[Any]) -> str | None:
    parts = [str(category) for category in categories if str(category) != "Auto"]
    if len(parts) < 2:
        return None
    program = parts[1]
    if program in {"core", "elective"}:
        return None
    return program


def _course_name_from_booking_categories(categories: Iterable[Any]) -> str | None:
    parts = [str(category) for category in categories if str(category) != "Auto"]
    if not parts:
        return None
    return parts[-1]


def _course_names_match_for_auto(payload_course: str, booking_course: str) -> bool:
    if payload_course == booking_course:
        return True
    for separator in (":", " -", " —"):
        if booking_course.startswith(f"{payload_course}{separator}"):
            return True
        if payload_course.startswith(f"{booking_course}{separator}"):
            return True
    return False


def _course_names_match_for_component_conflict(payload_course: str, booking_course: str) -> bool:
    if _course_names_match_for_auto(payload_course, booking_course):
        return True
    payload_normalized = payload_course.casefold().strip()
    booking_normalized = booking_course.casefold().strip()
    if not payload_normalized or not booking_normalized:
        return False
    if booking_normalized.startswith(f"{payload_normalized} "):
        return True
    if payload_normalized.startswith(f"{booking_normalized} "):
        return True
    return False


def _program_codes_match_for_auto(payload_program: str, booking_program: str) -> bool:
    if payload_program == booking_program:
        return True
    payload_base = payload_program.split("/", 1)[0]
    booking_base = booking_program.split("/", 1)[0]
    return payload_base == booking_base


def _auto_booking_categories_match(payload_categories: list[Any], auto_categories: list[Any]) -> bool:
    payload_key = _booking_categories_key(payload_categories)
    auto_key = _booking_categories_key(auto_categories)
    if payload_key == auto_key:
        return True
    if len(payload_key) < 3 or len(auto_key) < 3:
        return False
    if payload_key[0] != auto_key[0]:
        return False
    if not _program_codes_match_for_auto(payload_key[1], auto_key[1]):
        return False
    return _course_names_match_for_auto(payload_key[-1], auto_key[-1])


def _booking_identity_dict(booking: dict[str, Any]) -> dict[str, Any]:
    return {
        "room_id": booking.get("room_id"),
        "title": booking.get("title"),
        "start": booking.get("start"),
        "end": booking.get("end"),
        "categories": booking.get("categories"),
    }


def _slot_times_match_for_identity(
    booking_start: datetime,
    booking_end: datetime,
    payload_start: datetime,
    payload_end: datetime,
    booking_title: str,
) -> bool:
    start_match = booking_start.time() == payload_start.time()
    end_match = booking_end.time() == payload_end.time()
    if start_match and end_match:
        return True
    if _is_schedule_assistant_auto_title(booking_title) and start_match:
        return True
    return False


def booking_matches_payload_identity(booking: dict[str, Any], payload: dict[str, Any]) -> bool:
    if str(booking.get("room_id")) != str(payload.get("room_id")):
        return False

    booking_start = _parse_booking_datetime(str(booking["start"]))
    booking_end = _parse_booking_datetime(str(booking["end"]))
    payload_start = _parse_booking_datetime(str(payload["start"]))
    payload_end = _parse_booking_datetime(str(payload["end"]))
    payload_title = str(payload.get("title") or "")
    booking_title = str(booking.get("title") or "")
    if not _slot_times_match_for_identity(
        booking_start,
        booking_end,
        payload_start,
        payload_end,
        booking_title,
    ):
        return False

    if payload_title == booking_title:
        return True
    if _normalized_auto_title(booking_title) == payload_title:
        return True

    payload_parts = _parse_slot_title(payload_title)
    booking_parts = _parse_slot_title(booking_title)
    if payload_parts is None or booking_parts is None:
        return _is_schedule_assistant_auto_title(booking_title) and (
            _normalized_auto_title(booking_title) == payload_title
            or _normalized_auto_title(booking_title) == _normalized_auto_title(payload_title)
        )

    payload_course, payload_tag, payload_audience = payload_parts
    booking_course, booking_tag, booking_audience = booking_parts
    if payload_tag != booking_tag:
        return False
    if payload_course != booking_course and not (
        _is_schedule_assistant_auto_title(booking_title)
        and _course_names_match_for_auto(payload_course, booking_course)
    ):
        return False

    payload_categories = payload.get("categories")
    booking_categories = booking.get("categories")
    payload_program = (
        _program_code_from_booking_categories(payload_categories)
        if isinstance(payload_categories, list)
        else None
    )
    booking_program = (
        _program_code_from_booking_categories(booking_categories)
        if isinstance(booking_categories, list)
        else None
    )
    if payload_program is None:
        payload_program = payload_audience
    if booking_program is None:
        booking_program = booking_audience

    if payload_program and booking_program:
        if payload_program == booking_program:
            return True
        if _is_schedule_assistant_auto_title(booking_title) and _program_codes_match_for_auto(
            payload_program,
            booking_program,
        ):
            return True
        return False

    if payload_program and not booking_program:
        if _is_schedule_assistant_auto_title(booking_title):
            return True
        return False

    if booking_program and not payload_program:
        return False

    return True


def _auto_booking_matches_weekly_payload_identity(
    payload: dict[str, Any],
    auto_booking: dict[str, Any],
) -> bool:
    if str(payload.get("room_id")) != str(auto_booking.get("room_id")):
        return False

    payload_categories = payload.get("categories")
    auto_categories = auto_booking.get("categories")
    if isinstance(payload_categories, list) and isinstance(auto_categories, list):
        if not _auto_booking_categories_match(payload_categories, auto_categories):
            return False
    elif not booking_matches_payload_identity(_booking_identity_dict(auto_booking), payload):
        return False

    payload_start = _parse_booking_datetime(str(payload["start"]))
    payload_end = _parse_booking_datetime(str(payload["end"]))
    auto_start = _parse_booking_datetime(str(auto_booking["start"]))
    auto_end = _parse_booking_datetime(str(auto_booking["end"]))
    return payload_start.time() == auto_start.time() and payload_end.time() == auto_end.time()


def _weekly_series_contains_date(payload_recurrence: dict[str, Any], meeting_date: date) -> bool:
    weekday = str(payload_recurrence.get("weekday", "")).strip().lower()
    series_start = date.fromisoformat(str(payload_recurrence["start_date"]))
    series_end = date.fromisoformat(str(payload_recurrence["until_date"]))
    target = _API_WEEKDAY_TO_PYTHON[weekday]
    return series_start <= meeting_date <= series_end and meeting_date.weekday() == target


def payload_matches_auto_occurrence(payload: dict[str, Any], auto_booking: dict[str, Any]) -> bool:
    """Expanded calendar occurrence (no recurrence XML) that belongs to a config weekly slot."""
    payload_recurrence = payload.get("recurrence")
    if not isinstance(payload_recurrence, dict):
        return False
    if _auto_recurrence_fields(auto_booking.get("recurrence")) is not None:
        return False
    if not _auto_booking_matches_weekly_payload_identity(payload, auto_booking):
        return False
    auto_start = _parse_booking_datetime(str(auto_booking["start"]))
    return _weekly_series_contains_date(payload_recurrence, auto_start.date())


def payload_matches_auto_series(payload: dict[str, Any], auto_booking: dict[str, Any]) -> bool:
    """Same weekly booking as payload; recurrence start_date may differ (config vs Outlook)."""
    if not _auto_booking_matches_weekly_payload_identity(payload, auto_booking):
        return False

    payload_recurrence = payload.get("recurrence")
    auto_recurrence = _auto_recurrence_fields(auto_booking.get("recurrence"))
    if isinstance(payload_recurrence, dict):
        if auto_recurrence is None:
            return False
        if str(payload_recurrence.get("weekday", "")).strip().lower() != auto_recurrence["weekday"]:
            return False
        if str(payload_recurrence.get("until_date", "")) != auto_recurrence["until_date"]:
            return False
        return True

    if auto_recurrence is not None:
        return False
    payload_start = _parse_booking_datetime(str(payload["start"]))
    auto_start = _parse_booking_datetime(str(auto_booking["start"]))
    payload_end = _parse_booking_datetime(str(payload["end"]))
    auto_end = _parse_booking_datetime(str(auto_booking["end"]))
    return payload_start.date() == auto_start.date() and payload_end.date() == auto_end.date()


def find_matching_auto_booking(
    payload: dict[str, Any],
    auto_bookings: list[dict[str, Any]],
) -> dict[str, Any] | None:
    for auto_booking in auto_bookings:
        if payload_matches_auto_booking(payload, auto_booking):
            return auto_booking
        if payload_matches_auto_series(payload, auto_booking):
            return auto_booking
        if payload_matches_auto_occurrence(payload, auto_booking):
            return auto_booking
    return None


def auto_booking_matches_any_slot_payload(
    auto_booking: dict[str, Any],
    slot_payloads: list[dict[str, Any]],
) -> bool:
    for payload in slot_payloads:
        if payload_matches_auto_booking(payload, auto_booking):
            return True
        if payload_matches_auto_series(payload, auto_booking):
            return True
        if payload_matches_auto_occurrence(payload, auto_booking):
            return True
    return False


def collect_all_slot_payloads(programs: list[ProgramGroup]) -> list[dict[str, Any]]:
    payloads: list[dict[str, Any]] = []
    for program in programs:
        for course in program.courses:
            for component in course.components:
                for slot in component.slots:
                    payloads.append(build_booking_payload(component, slot))
    return payloads


def _extra_booking_candidate_key(booking: dict[str, Any]) -> str | None:
    outlook_booking_id = booking.get("outlook_booking_id")
    if outlook_booking_id:
        return f"id:{outlook_booking_id}"
    outlook_entry_id = booking.get("outlook_entry_id")
    room_id = booking.get("room_id")
    if outlook_entry_id and room_id:
        return f"entry:{room_id}:{outlook_entry_id}"
    return None


def _collect_auto_booking_candidates(
    auto_bookings: list[dict[str, Any]],
    existing_bookings: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """BMP auto-bookings plus room-calendar bookings whose title starts with an Auto prefix."""
    seen_keys: set[str] = set()
    candidates: list[dict[str, Any]] = []

    def append_candidate(booking: dict[str, Any]) -> None:
        key = _extra_booking_candidate_key(booking)
        if key is None or key in seen_keys:
            return
        seen_keys.add(key)
        candidates.append(booking)

    for booking in auto_bookings:
        append_candidate(booking)

    for booking in existing_bookings:
        if not _is_schedule_assistant_auto_title(str(booking.get("title") or "")):
            continue
        append_candidate(booking)

    return candidates


def find_extra_auto_bookings(
    auto_bookings: list[dict[str, Any]],
    slot_payloads: list[dict[str, Any]],
    *,
    existing_bookings: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    extra: list[dict[str, Any]] = []
    for candidate in _collect_auto_booking_candidates(auto_bookings, existing_bookings or []):
        if auto_booking_matches_any_slot_payload(candidate, slot_payloads):
            continue
        extra.append(candidate)
    extra.sort(key=lambda item: str(item.get("start") or ""))
    return extra


def _auto_booking_schedule_payload(auto_booking: dict[str, Any]) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "room_id": auto_booking.get("room_id"),
        "start": auto_booking.get("start"),
        "end": auto_booking.get("end"),
        "title": auto_booking.get("title"),
        "categories": auto_booking.get("categories") or [],
    }
    recurrence = _auto_recurrence_fields(auto_booking.get("recurrence"))
    if recurrence:
        payload["recurrence"] = recurrence
    return payload


def _extra_auto_booking_label(auto_booking: dict[str, Any]) -> str:
    title = _normalized_auto_title(str(auto_booking.get("title") or "booking"))
    return f"{title}  {_payload_schedule_detail(_auto_booking_schedule_payload(auto_booking)).strip()}"


@dataclass(frozen=True)
class ExtraCancelTarget:
    room_id: str
    start: str
    end: str
    title: str
    outlook_booking_id: str | None = None
    outlook_entry_id: str | None = None

    def display_key(self) -> str:
        if self.outlook_booking_id:
            return str(self.outlook_booking_id)
        if self.outlook_entry_id:
            return f"{self.room_id}:{self.outlook_entry_id}"
        return f"{self.room_id}:{self.title}@{self.start}"


def _extra_cancel_target_from_booking(booking: dict[str, Any]) -> ExtraCancelTarget | None:
    room_id = booking.get("room_id")
    start = booking.get("start")
    end = booking.get("end")
    title = booking.get("title")
    if not room_id or not start or not end or not title:
        return None
    outlook_booking_id = booking.get("outlook_booking_id")
    outlook_entry_id = booking.get("outlook_entry_id")
    if not outlook_booking_id and not outlook_entry_id:
        return None
    return ExtraCancelTarget(
        room_id=str(room_id),
        start=str(start),
        end=str(end),
        title=str(title),
        outlook_booking_id=str(outlook_booking_id) if outlook_booking_id else None,
        outlook_entry_id=str(outlook_entry_id) if outlook_entry_id else None,
    )


def collect_selected_extra_cancel_targets(roots: list["PromptTreeNode"]) -> list[ExtraCancelTarget]:
    targets: list[ExtraCancelTarget] = []

    def walk(nodes: list[PromptTreeNode]) -> None:
        for node in nodes:
            if node.extra_auto_booking is not None and node.checked and node.bookable:
                target = _extra_cancel_target_from_booking(node.extra_auto_booking)
                if target is not None:
                    targets.append(target)
            if node.children:
                walk(node.children)

    walk(roots)
    return targets


def count_selected_booking_slots(roots: list["PromptTreeNode"]) -> int:
    total = 0

    def walk(nodes: list[PromptTreeNode]) -> None:
        nonlocal total
        for node in nodes:
            if node.slot_value is not None and node.checked and node.bookable:
                if node.review_kind == "conflict" and node.conflict_mode == "skip":
                    continue
                total += 1
            if node.children:
                walk(node.children)

    walk(roots)
    return total


def count_selected_extra_bookings(roots: list["PromptTreeNode"]) -> int:
    return len(collect_selected_extra_cancel_targets(roots))


def booking_matches_payload_slot(booking: dict[str, Any], payload: dict[str, Any]) -> bool:
    return booking_matches_payload_identity(booking, payload)


def _same_time_and_categories(booking: dict[str, Any], reference: dict[str, Any]) -> bool:
    if str(booking.get("room_id")) != str(reference.get("room_id")):
        return False
    booking_categories = booking.get("categories")
    reference_categories = reference.get("categories")
    if not isinstance(booking_categories, list) or not isinstance(reference_categories, list):
        return False
    if _booking_categories_key(booking_categories) != _booking_categories_key(reference_categories):
        return False
    booking_start = _parse_booking_datetime(str(booking["start"]))
    booking_end = _parse_booking_datetime(str(booking["end"]))
    reference_start = _parse_booking_datetime(str(reference["start"]))
    reference_end = _parse_booking_datetime(str(reference["end"]))
    return (
        booking_start.time() == reference_start.time()
        and booking_end.time() == reference_end.time()
    )


def _booking_overlaps_auto_instance(booking: dict[str, Any], auto_booking: dict[str, Any]) -> bool:
    auto_as_payload = _booking_identity_dict(auto_booking)
    if not booking_matches_payload_identity(booking, auto_as_payload):
        if not _same_time_and_categories(booking, auto_booking):
            return False

    auto_recurrence = _auto_recurrence_fields(auto_booking.get("recurrence"))
    booking_start = _parse_booking_datetime(str(booking["start"]))
    if auto_recurrence is None:
        auto_start = _parse_booking_datetime(str(auto_booking["start"]))
        return booking_start.date() == auto_start.date()

    series_start = date.fromisoformat(auto_recurrence["start_date"])
    series_end = date.fromisoformat(auto_recurrence["until_date"])
    weekday = _API_WEEKDAY_TO_PYTHON[auto_recurrence["weekday"]]
    return series_start <= booking_start.date() <= series_end and booking_start.weekday() == weekday


def _booking_is_same_course_component(booking: dict[str, Any], payload: dict[str, Any]) -> bool:
    """Parallel sections or legacy titles for one course/component are not room conflicts."""
    payload_parts = _parse_slot_title(str(payload.get("title") or ""))
    if payload_parts is None:
        return False
    payload_course, payload_tag, _ = payload_parts
    booking_course, booking_tag = _course_and_component_from_title(str(booking.get("title") or ""))
    course_match = _course_names_match_for_component_conflict(payload_course, booking_course)
    if not course_match:
        course_match = _course_names_match_via_categories(booking, payload)
    if not course_match:
        return False
    if booking_tag is not None and booking_tag != payload_tag:
        return False
    return True


def _booking_in_own_auto_series(
    booking: dict[str, Any],
    payload: dict[str, Any],
    auto_bookings: list[dict[str, Any]],
) -> bool:
    if booking_matches_payload_slot(booking, payload):
        return True
    if _booking_is_same_course_component(booking, payload):
        return True
    if payload_matches_auto_booking(payload, booking):
        return True

    for auto_booking in auto_bookings:
        if not (
            payload_matches_auto_booking(payload, auto_booking)
            or payload_matches_auto_series(payload, auto_booking)
            or payload_matches_auto_occurrence(payload, auto_booking)
        ):
            continue
        if _booking_overlaps_auto_instance(booking, auto_booking):
            return True

    return False


def _first_weekly_meeting_date(range_start: date, range_end: date, weekday_api: str) -> str:
    target = _API_WEEKDAY_TO_PYTHON[weekday_api.strip().lower()]
    current = range_start
    while current <= range_end:
        if current.weekday() == target:
            return current.isoformat()
        current += timedelta(days=1)
    return range_start.isoformat()


def slot_datetimes(slot: SlotRow, *, term: TermConfig | None = None) -> tuple[datetime, datetime]:
    meeting_date = slot.date
    if slot.recurring and slot.recurrence:
        range_start = date.fromisoformat(str(slot.recurrence["start_date"]))
        range_end = date.fromisoformat(str(slot.recurrence["until_date"]))
        meeting_date = _first_weekly_meeting_date(
            range_start,
            range_end,
            str(slot.recurrence["weekday"]),
        )
    start = datetime.fromisoformat(f"{meeting_date}T{slot.start_time}").replace(tzinfo=MSK)
    end = datetime.fromisoformat(f"{meeting_date}T{slot.end_time}").replace(tzinfo=MSK)
    return start, end


def _booking_title(placed: PlacedComponent) -> str:
    tag = str(placed.component.tag)
    if tag == "lab":
        audience_text = ", ".join(placed.audiences)
        return f"{placed.course.name} ({tag}, {audience_text})"
    return f"{placed.course.name} ({tag})"


def build_booking_payload(
    component: ComponentNode,
    slot: SlotRow,
    *,
    start: datetime | None = None,
    end: datetime | None = None,
    recurrence: dict[str, Any] | None = None,
) -> dict[str, Any]:
    placed = component.placed
    if start is None or end is None:
        start, end = slot_datetimes(slot, term=placed.term)
    payload: dict[str, Any] = {
        "room_id": slot.room,
        "title": _booking_title(placed),
        "start": start.isoformat(),
        "end": end.isoformat(),
        "participant_emails": [],
        "categories": _booking_categories(placed),
    }
    if recurrence is not None:
        payload["recurrence"] = recurrence
    elif slot.recurring and slot.recurrence:
        payload["recurrence"] = slot.recurrence
    return payload


def _api_base_url(batch_api_url: str) -> str:
    url = batch_api_url.rstrip("/")
    for suffix in ("/bmp/auto-bookings/batch", "/auto-bookings/batch"):
        if url.endswith(suffix):
            return url[: -len(suffix)]
    return DEFAULT_API_BASE


def _bookings_list_url(batch_api_url: str) -> str:
    return f"{_api_base_url(batch_api_url).rstrip('/')}/bookings/"


def _auto_bookings_list_url(batch_api_url: str) -> str:
    return f"{_api_base_url(batch_api_url).rstrip('/')}/bmp/auto-bookings/"


def _auth_headers(token: str) -> dict[str, str]:
    return {
        "accept": "application/json",
        "Authorization": f"Bearer {token}",
    }


def fetch_room_bookings(
    *,
    bookings_url: str,
    token: str,
    room_ids: list[str],
    start: datetime,
    end: datetime,
) -> list[dict[str, Any]]:
    if not room_ids:
        return []
    params: list[tuple[str, str | bool]] = [
        ("start", start.isoformat()),
        ("end", end.isoformat()),
        ("include_red", "false"),
    ]
    for room_id in sorted(set(room_ids)):
        params.append(("room_ids", room_id))
    with httpx.Client(timeout=HTTP_TIMEOUT_SECONDS) as client:
        response = client.get(bookings_url, headers=_auth_headers(token), params=params)
        response.raise_for_status()
        data = response.json()
    if not isinstance(data, list):
        return []
    return data


def fetch_auto_bookings(*, api_url: str, token: str) -> list[dict[str, Any]]:
    url = _auto_bookings_list_url(api_url)
    with httpx.Client(timeout=HTTP_TIMEOUT_SECONDS) as client:
        response = client.get(url, headers=_auth_headers(token))
        response.raise_for_status()
        data = response.json()
    if not isinstance(data, list):
        return []
    return data


def _try_fetch_auto_bookings(*, api_url: str, token: str) -> list[dict[str, Any]]:
    try:
        return fetch_auto_bookings(api_url=api_url, token=token)
    except httpx.HTTPStatusError as error:
        print(
            f"Warning: auto-bookings HTTP {error.response.status_code}: "
            f"{error.response.text[:200]}",
            file=sys.stderr,
        )
    except httpx.HTTPError as error:
        print(f"Warning: auto-bookings request failed: {error}", file=sys.stderr)
    return []


def _parse_booking_datetime(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=MSK)
    return parsed.astimezone(MSK)


def _intervals_overlap(start_a: datetime, end_a: datetime, start_b: datetime, end_b: datetime) -> bool:
    return start_a < end_b and start_b < end_a


def iter_slot_occurrences(item: TreeSlotValue) -> list[tuple[datetime, datetime]]:
    slot = item.slot
    placed = item.component.placed
    if slot.recurring and slot.recurrence:
        weekday = str(slot.recurrence["weekday"])
        range_start = date.fromisoformat(str(slot.recurrence["start_date"]))
        range_end = date.fromisoformat(str(slot.recurrence["until_date"]))
        target = _API_WEEKDAY_TO_PYTHON[weekday.strip().lower()]
        occurrences: list[tuple[datetime, datetime]] = []
        current = range_start
        while current <= range_end:
            if current.weekday() == target:
                start = datetime.fromisoformat(f"{current.isoformat()}T{slot.start_time}").replace(tzinfo=MSK)
                end = datetime.fromisoformat(f"{current.isoformat()}T{slot.end_time}").replace(tzinfo=MSK)
                occurrences.append((start, end))
            current += timedelta(days=1)
        return occurrences

    start, end = slot_datetimes(slot, term=placed.term)
    return [(start, end)]


def _occurrence_conflicts(
    occurrence: tuple[datetime, datetime],
    existing: list[dict[str, Any]],
    *,
    room_id: str,
) -> list[dict[str, Any]]:
    start, end = occurrence
    hits: list[dict[str, Any]] = []
    for booking in existing:
        if str(booking.get("room_id")) != room_id:
            continue
        existing_start = _parse_booking_datetime(str(booking["start"]))
        existing_end = _parse_booking_datetime(str(booking["end"]))
        if _intervals_overlap(start, end, existing_start, existing_end):
            hits.append(booking)
    return hits


@dataclass
class SlotConflictReport:
    item: TreeSlotValue
    conflicting_occurrences: list[tuple[datetime, datetime, list[dict[str, Any]]]]


def detect_slot_conflicts(
    items: list[TreeSlotValue],
    existing_bookings: list[dict[str, Any]],
    *,
    auto_bookings: list[dict[str, Any]] | None = None,
) -> list[SlotConflictReport]:
    reports: list[SlotConflictReport] = []
    for item in items:
        room_id = item.slot.room
        if not room_id:
            continue
        payload = build_booking_payload(item.component, item.slot)
        hits: list[tuple[datetime, datetime, list[dict[str, Any]]]] = []
        for occurrence in iter_slot_occurrences(item):
            conflicts = _occurrence_conflicts(occurrence, existing_bookings, room_id=room_id)
            conflicts = [
                booking
                for booking in conflicts
                if not _booking_in_own_auto_series(booking, payload, auto_bookings or [])
            ]
            if conflicts:
                hits.append((occurrence[0], occurrence[1], conflicts))
        if hits:
            reports.append(SlotConflictReport(item=item, conflicting_occurrences=hits))
    return reports


def _first_weekday_on_or_after(weekday_api: str, on_or_after: date, until: date) -> date | None:
    target = _API_WEEKDAY_TO_PYTHON[weekday_api.strip().lower()]
    current = on_or_after
    while current <= until:
        if current.weekday() == target:
            return current
        current += timedelta(days=1)
    return None


def _calendar_segments_around_conflicts(
    item: TreeSlotValue,
    conflict_dates: set[date],
) -> list[tuple[date, date]]:
    slot = item.slot
    if not slot.recurrence:
        return []
    term_start = date.fromisoformat(str(slot.recurrence["start_date"]))
    term_end = date.fromisoformat(str(slot.recurrence["until_date"]))
    segments: list[tuple[date, date]] = []
    cursor = term_start
    for conflict in sorted(conflict_dates):
        until = conflict - timedelta(days=1)
        if cursor <= until:
            segments.append((cursor, until))
        cursor = conflict + timedelta(days=1)
    if cursor <= term_end:
        segments.append((cursor, term_end))
    return segments


def _payloads_for_calendar_segments(
    item: TreeSlotValue,
    segments: list[tuple[date, date]],
) -> list[dict[str, Any]]:
    payloads: list[dict[str, Any]] = []
    slot = item.slot
    recurrence_base = dict(slot.recurrence or {})
    weekday_api = str(recurrence_base["weekday"])
    for segment_start, segment_end in segments:
        first_meeting = _first_weekday_on_or_after(weekday_api, segment_start, segment_end)
        if first_meeting is None:
            continue
        start = datetime.fromisoformat(f"{first_meeting.isoformat()}T{slot.start_time}").replace(tzinfo=MSK)
        end = datetime.fromisoformat(f"{first_meeting.isoformat()}T{slot.end_time}").replace(tzinfo=MSK)
        recurrence = dict(recurrence_base)
        recurrence["start_date"] = segment_start.isoformat()
        recurrence["until_date"] = segment_end.isoformat()
        payloads.append(
            build_booking_payload(
                item.component,
                slot,
                start=start,
                end=end,
                recurrence=recurrence,
            ),
        )
    return payloads


def _format_conflict_line(item: TreeSlotValue, start: datetime, end: datetime, booking: dict[str, Any]) -> str:
    title = str(booking.get("title") or "booking")
    return (
        f"  {_human_date(start.date())} {_format_clock_short(start.strftime('%H:%M:%S'))}"
        f"-{_format_clock_short(end.strftime('%H:%M:%S'))}  overlaps \"{title}\""
    )


def _format_conflict_details(report: SlotConflictReport) -> list[str]:
    lines: list[str] = []
    for start, end, conflicts in report.conflicting_occurrences:
        for booking in conflicts:
            lines.append(_format_conflict_line(report.item, start, end, booking))
    return lines


def _conflict_hit_count(report: SlotConflictReport | None) -> int:
    if report is None:
        return 0
    return len(_format_conflict_details(report))


def _conflict_section_for_node(node: PromptTreeNode, depth: int) -> list[str]:
    if node.conflict_report is not None:
        indent = "  " * depth
        lines = [f"{indent}{node.label}"]
        for detail in _format_conflict_details(node.conflict_report):
            lines.append(f"{indent}  {detail.strip()}")
        lines.append("")
        return lines

    sections: list[str] = []
    for child in node.children:
        sections.extend(_conflict_section_for_node(child, depth + 1))
    if not sections:
        return []

    if depth > 0:
        return [f"{'  ' * depth}{node.label}", *sections]
    return sections


def _format_all_conflicts_grouped(roots: list[PromptTreeNode]) -> list[str]:
    lines: list[str] = []
    for root in roots:
        lines.extend(_conflict_section_for_node(root, 0))
    return lines


def _count_conflict_slots(roots: list[PromptTreeNode]) -> int:
    count = 0

    def walk(nodes: list[PromptTreeNode]) -> None:
        nonlocal count
        for node in nodes:
            if node.conflict_report is not None:
                count += 1
            walk(node.children)

    for root in roots:
        walk([root])
    return count


def save_conflicts_report(roots: list[PromptTreeNode], path: Path) -> int:
    slot_count = _count_conflict_slots(roots)
    lines = _format_all_conflicts_grouped(roots)
    header = (
        f"Booking conflicts report\n"
        f"Generated: {datetime.now(MSK).isoformat()}\n"
        f"Conflicting slots: {slot_count}\n"
    )
    body = "\n".join(lines) if lines else "No conflicts."
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"{header}\n{body}\n", encoding="utf-8")
    return slot_count


def fetch_existing_bookings_for_config(
    cfg: ScheduleConfig,
    *,
    token: str,
    api_url: str,
) -> list[dict[str, Any]]:
    room_ids = [room.id for room in cfg.rooms]
    if not room_ids:
        return []

    term = cfg.term
    range_start = datetime.fromisoformat(f"{term.semester.start_date.isoformat()}T00:00:00").replace(
        tzinfo=MSK
    )
    range_end = datetime.fromisoformat(f"{term.semester.end_date.isoformat()}T23:59:59").replace(
        tzinfo=MSK
    )

    return fetch_room_bookings(
        bookings_url=_bookings_list_url(api_url),
        token=token,
        room_ids=room_ids,
        start=range_start,
        end=range_end,
    )


def _slot_has_own_booking(
    payload: dict[str, Any],
    *,
    auto_bookings: list[dict[str, Any]],
    existing_bookings: list[dict[str, Any]],
) -> bool:
    if find_matching_auto_booking(payload, auto_bookings):
        return True
    for booking in existing_bookings:
        if _booking_in_own_auto_series(booking, payload, auto_bookings):
            return True
    return False


def classify_review_item(
    item: TreeSlotValue,
    *,
    auto_bookings: list[dict[str, Any]],
    existing_bookings: list[dict[str, Any]],
) -> tuple[str, SlotConflictReport | None, bool]:
    payload = build_booking_payload(item.component, item.slot)
    partially_booked = _slot_has_own_booking(
        payload,
        auto_bookings=auto_bookings,
        existing_bookings=existing_bookings,
    )
    reports = detect_slot_conflicts([item], existing_bookings, auto_bookings=auto_bookings)
    if reports:
        return "conflict", reports[0], partially_booked
    if partially_booked:
        return "booked", None, partially_booked
    return "ready", None, False


def _can_split_review_slot(node: PromptTreeNode) -> bool:
    if node.slot_value is None:
        return False
    slot = node.slot_value.slot
    return bool(slot.recurring and slot.recurrence)


def collect_review_payloads(roots: list[PromptTreeNode]) -> list[dict[str, Any]]:
    payloads: list[dict[str, Any]] = []

    def walk(nodes: list[PromptTreeNode]) -> None:
        for node in nodes:
            if node.children:
                walk(node.children)
                continue
            if not node.checked or node.slot_value is None:
                continue
            if node.review_kind == "conflict" and node.conflict_mode == "skip":
                continue
            if (
                node.review_kind == "conflict"
                and node.conflict_mode == "split"
                and node.conflict_report is not None
            ):
                conflict_dates = {
                    start.date() for start, _, _ in node.conflict_report.conflicting_occurrences
                }
                segments = _calendar_segments_around_conflicts(node.slot_value, conflict_dates)
                payloads.extend(_payloads_for_calendar_segments(node.slot_value, segments))
                continue
            payloads.append(build_booking_payload(node.slot_value.component, node.slot_value.slot))

    walk(roots)
    return payloads


def _group_annotated_payloads(
    entries: list[tuple[dict[str, Any], str | None]],
) -> list[tuple[str, list[tuple[dict[str, Any], str | None]]]]:
    by_title: dict[str, list[tuple[dict[str, Any], str | None]]] = defaultdict(list)
    for payload, status in entries:
        by_title[str(payload["title"])].append((payload, status))
    return [(title, by_title[title]) for title in sorted(by_title, key=str.casefold)]


def _group_payloads_by_title(
    payloads: list[dict[str, Any]],
) -> list[tuple[str, list[tuple[int, dict[str, Any]]]]]:
    by_title: dict[str, list[tuple[int, dict[str, Any]]]] = defaultdict(list)
    for index, payload in enumerate(payloads):
        by_title[str(payload["title"])].append((index, payload))
    return [
        (title, sorted(by_title[title], key=lambda item: item[0]))
        for title in sorted(by_title, key=str.casefold)
    ]


def _print_grouped_payload_lines(
    entries: list[tuple[dict[str, Any], str | None]],
    *,
    header: str | None = None,
) -> None:
    if header:
        print(header)
    for title, title_entries in _group_annotated_payloads(entries):
        print(title)
        for payload, status in title_entries:
            print(_payload_line(payload, status))


def _print_submission_preview(bookings: list[dict[str, Any]]) -> None:
    entries = [(payload, None) for payload in bookings]
    _print_grouped_payload_lines(entries, header=f"\nWill submit {len(bookings)} booking(s):")


def _batch_result_status_label(item: dict[str, Any]) -> str:
    return "OK" if item.get("status") == "ok" else "ERROR"


def _batch_result_error_text(item: dict[str, Any]) -> str:
    error = item.get("error")
    if error:
        return str(error).strip()
    message = item.get("message_body")
    if message:
        return str(message).strip().splitlines()[0]
    return "unknown error"


def _print_batch_results(payloads: list[dict[str, Any]], result: dict[str, Any]) -> None:
    ok = 0
    failed = 0
    print()
    for title, entries in _group_payloads_by_title(payloads):
        print(title)
        for index, payload in entries:
            item = result.get(str(index))
            if item is None:
                item = result.get(index, {})
            status_label = _batch_result_status_label(item)
            if status_label == "OK":
                ok += 1
            else:
                failed += 1
            print(_payload_line(payload, status_label))
            if status_label == "ERROR":
                print(f"    {_batch_result_error_text(item)}")
    print(f"\nBatch done: {ok} ok, {failed} failed")


def _auto_bookings_batch_cancel_url(batch_api_url: str) -> str:
    url = batch_api_url.rstrip("/")
    for suffix in ("/bmp/auto-bookings/batch", "/auto-bookings/batch"):
        if url.endswith(suffix):
            return url
    return DEFAULT_AUTO_BOOKINGS_BATCH_CANCEL_URL


def post_auto_bookings_batch(
    *,
    api_url: str,
    token: str,
    bookings: list[dict[str, Any]],
) -> dict[str, Any]:
    headers = {
        "accept": "application/json",
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }
    with httpx.Client(timeout=HTTP_TIMEOUT_SECONDS) as client:
        response = client.post(api_url, headers=headers, json={"bookings": bookings})
        response.raise_for_status()
        return response.json()


def delete_auto_bookings_batch(
    *,
    api_url: str,
    token: str,
    outlook_booking_ids: list[str],
) -> dict[str, Any]:
    headers = {
        "accept": "application/json",
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }
    with httpx.Client(timeout=HTTP_TIMEOUT_SECONDS) as client:
        response = client.request(
            "DELETE",
            _auto_bookings_batch_cancel_url(api_url),
            headers=headers,
            json={"outlook_booking_ids": outlook_booking_ids},
        )
        response.raise_for_status()
        return response.json()


def cancel_extra_booking(
    *,
    api_url: str,
    token: str,
    target: ExtraCancelTarget,
) -> None:
    url = f"{_api_base_url(api_url).rstrip('/')}/bookings/cancel-extra"
    body: dict[str, Any] = {
        "room_id": target.room_id,
        "start": target.start,
        "end": target.end,
        "title": target.title,
    }
    if target.outlook_booking_id:
        body["outlook_booking_id"] = target.outlook_booking_id
    if target.outlook_entry_id:
        body["outlook_entry_id"] = target.outlook_entry_id
    with httpx.Client(timeout=HTTP_TIMEOUT_SECONDS) as client:
        response = client.post(
            url,
            headers={**_auth_headers(token), "Content-Type": "application/json"},
            json=body,
        )
        response.raise_for_status()


def _print_cancel_batch_results(result: dict[str, Any]) -> None:
    cancelled = result.get("cancelled") or []
    failed = result.get("failed") or {}
    print(f"\nCancel done: {len(cancelled)} cancelled, {len(failed)} failed")
    for outlook_booking_id in cancelled:
        print(f"  cancelled {outlook_booking_id}")
    for outlook_booking_id, error in failed.items():
        print(f"  failed {outlook_booking_id}: {error}")


def _print_cancel_entry_results(
    *,
    cancelled: list[str],
    failed: dict[str, str],
) -> None:
    if not cancelled and not failed:
        return
    print(f"\nRoom-calendar cancel: {len(cancelled)} cancelled, {len(failed)} failed")
    for key in cancelled:
        print(f"  cancelled {key}")
    for key, error in failed.items():
        print(f"  failed {key}: {error}")


@dataclass(frozen=True)
class TreeSlotValue:
    course_name: str
    component: ComponentNode
    slot: SlotRow


class CheckState(str, Enum):
    UNCHECKED = "unchecked"
    CHECKED = "checked"
    PARTIAL = "partial"


def _course_label_strikethrough(node: PromptTreeNode) -> bool:
    if not node.is_course or node.total_count == 0:
        return False
    if node.bookable_count > 0 and node.booked_count == node.bookable_count:
        return True
    return node.bookable_count == 0 and node.online_count == node.total_count


def _node_stats_suffix(node: PromptTreeNode) -> str:
    return (
        f"  ({node.bookable_count}/{node.total_count} bookable, "
        f"{node.booked_count} booked, "
        f"{node.selected_count} selected, "
        f"{node.conflict_count} conflicts)"
    )


def format_node_label_formatted(node: PromptTreeNode) -> FormattedText:
    if node.slot_value is not None:
        return FormattedText([("", format_node_label(node))])
    suffix = _node_stats_suffix(node)
    if _course_label_strikethrough(node):
        return FormattedText([("strike", node.label), ("", suffix)])
    return FormattedText([("", f"{node.label}{suffix}")])


@dataclass
class PromptTreeNode:
    id: str
    label: str
    children: list["PromptTreeNode"] = field(default_factory=list)
    checked: bool = False
    expanded: bool = True
    slot_value: TreeSlotValue | None = None
    extra_auto_booking: dict[str, Any] | None = None
    bookable: bool = True
    state: CheckState = CheckState.UNCHECKED
    bookable_count: int = 0
    booked_count: int = 0
    online_count: int = 0
    conflict_count: int = 0
    total_count: int = 0
    selected_count: int = 0
    is_course: bool = False
    review_kind: str | None = None
    partially_booked: bool = False
    conflict_report: SlotConflictReport | None = None
    conflict_mode: str = "skip"


@dataclass
class VisibleRow:
    node: PromptTreeNode
    depth: int


def build_extra_auto_bookings_node(extra_bookings: list[dict[str, Any]]) -> PromptTreeNode | None:
    if not extra_bookings:
        return None
    children = [
        PromptTreeNode(
            id=f"extra|{index}|{_extra_booking_candidate_key(auto_booking) or index}",
            label=_extra_auto_booking_label(auto_booking),
            extra_auto_booking=auto_booking,
            bookable=True,
            expanded=True,
        )
        for index, auto_booking in enumerate(extra_bookings)
    ]
    return PromptTreeNode(
        id="__extra_auto_bookings__",
        label=f"Extra auto bookings ({len(extra_bookings)})",
        children=children,
        expanded=False,
    )


def _count_bookable_slots(program: ProgramGroup) -> tuple[int, int]:
    bookable = 0
    total = 0
    for course in program.courses:
        for component in course.components:
            for slot in component.slots:
                total += 1
                if slot.bookable:
                    bookable += 1
    return bookable, total


def recompute_node(node: PromptTreeNode) -> None:
    if node.extra_auto_booking is not None:
        node.total_count = 1
        node.bookable_count = 1
        node.booked_count = 0
        node.online_count = 0
        node.conflict_count = 0
        node.selected_count = int(node.checked)
        node.state = CheckState.CHECKED if node.checked else CheckState.UNCHECKED
        return

    if node.slot_value is not None:
        slot = node.slot_value.slot
        node.total_count = 1
        node.bookable_count = int(node.bookable)
        node.booked_count = int(node.bookable and node.review_kind == "booked")
        node.online_count = int(not node.bookable and slot.disabled_reason == "online")
        node.conflict_count = int(node.conflict_report is not None)
        node.selected_count = int(node.checked and node.bookable)
        node.state = CheckState.CHECKED if node.checked else CheckState.UNCHECKED
        return

    for child in node.children:
        recompute_node(child)

    node.total_count = sum(child.total_count for child in node.children)
    node.bookable_count = sum(child.bookable_count for child in node.children)
    node.booked_count = sum(child.booked_count for child in node.children)
    node.online_count = sum(child.online_count for child in node.children)
    node.conflict_count = sum(child.conflict_count for child in node.children)
    node.selected_count = sum(child.selected_count for child in node.children)

    if not node.children:
        node.state = CheckState.UNCHECKED
        return

    if node.bookable_count == 0:
        node.state = CheckState.UNCHECKED
    elif node.selected_count == node.bookable_count:
        node.state = CheckState.CHECKED
    elif node.selected_count == 0:
        node.state = CheckState.UNCHECKED
    else:
        node.state = CheckState.PARTIAL


def check_marker_for_node(node: PromptTreeNode) -> str:
    if node.bookable_count == 0:
        return "[.]"
    if node.selected_count == node.bookable_count:
        return "[x]"
    if node.selected_count == 0:
        return "[ ]"
    return "[-]"


def recompute_tree(nodes: Iterable[PromptTreeNode]) -> None:
    for node in nodes:
        recompute_node(node)


def format_node_label(node: PromptTreeNode) -> str:
    if node.extra_auto_booking is not None:
        return f"{node.label}  [EXTRA]"
    if node.slot_value is not None:
        text = node.label
        if node.review_kind == "booked":
            text += "  [BOOKED]"
        elif node.review_kind == "conflict":
            count = _conflict_hit_count(node.conflict_report)
            badge = f"BOOKED · CONFLICT {count}" if node.partially_booked else f"CONFLICT {count}"
            badge = f"{badge} · {node.conflict_mode}"
            text += f"  [{badge}]"
        elif node.review_kind == "ready":
            text += "  [OK]"
        return text
    return f"{node.label}{_node_stats_suffix(node)}"


def walk_visible(nodes: Iterable[PromptTreeNode], depth: int = 0) -> list[VisibleRow]:
    rows: list[VisibleRow] = []
    for node in nodes:
        rows.append(VisibleRow(node=node, depth=depth))
        if node.expanded:
            rows.extend(walk_visible(node.children, depth + 1))
    return rows


def _apply_parent_slot_selection(node: PromptTreeNode, checked: bool) -> bool:
    if node.review_kind == "booked":
        node.checked = False
        return True
    if node.review_kind == "conflict":
        node.conflict_mode = "skip"
        node.checked = checked
        return True
    return False


def _toggle_slot_checked(node: PromptTreeNode) -> None:
    if node.extra_auto_booking is not None:
        node.checked = not node.checked
        return
    if not node.bookable or node.review_kind == "booked":
        return
    node.checked = not node.checked
    if node.review_kind == "conflict":
        node.conflict_mode = "skip"


def set_subtree_checked(node: PromptTreeNode, checked: bool) -> None:
    if node.extra_auto_booking is not None:
        node.checked = checked
        return
    if node.slot_value is not None:
        if checked and not node.bookable:
            return
        if _apply_parent_slot_selection(node, checked):
            return
        node.checked = checked
        return
    for child in node.children:
        set_subtree_checked(child, checked)


def set_all_bookable_checked(nodes: Iterable[PromptTreeNode], checked: bool) -> None:
    for node in nodes:
        if node.extra_auto_booking is not None:
            continue
        if node.slot_value is not None:
            if not node.bookable:
                continue
            if _apply_parent_slot_selection(node, checked):
                continue
            node.checked = checked
        else:
            set_all_bookable_checked(node.children, checked)


def build_prompt_tree(
    programs: list[ProgramGroup],
    *,
    auto_bookings: list[dict[str, Any]],
    existing_bookings: list[dict[str, Any]],
    slot_payloads: list[dict[str, Any]],
) -> list[PromptTreeNode]:
    roots: list[PromptTreeNode] = []
    for program in programs:
        course_nodes: list[PromptTreeNode] = []
        for course in program.courses:
            component_nodes: list[PromptTreeNode] = []
            for component in course.components:
                slot_nodes: list[PromptTreeNode] = []
                for slot in component.slots:
                    slot_value = TreeSlotValue(course.name, component, slot)
                    review_kind: str | None = None
                    partially_booked = False
                    conflict_report: SlotConflictReport | None = None
                    if slot.bookable:
                        review_kind, conflict_report, partially_booked = classify_review_item(
                            slot_value,
                            auto_bookings=auto_bookings,
                            existing_bookings=existing_bookings,
                        )
                    slot_nodes.append(
                        PromptTreeNode(
                            id=slot.slot_id,
                            label=slot.label(),
                            slot_value=slot_value,
                            bookable=slot.bookable,
                            expanded=True,
                            review_kind=review_kind,
                            partially_booked=partially_booked,
                            conflict_report=conflict_report,
                            conflict_mode="skip",
                        )
                    )
                component_nodes.append(
                    PromptTreeNode(
                        id=component.component_id,
                        label=component.label,
                        children=slot_nodes,
                        expanded=True,
                    )
                )
            course_nodes.append(
                PromptTreeNode(
                    id=course.course_id,
                    label=course.name,
                    children=component_nodes,
                    expanded=False,
                    is_course=True,
                )
            )
        roots.append(
            PromptTreeNode(
                id=program.name,
                label=program.name,
                children=course_nodes,
                expanded=False,
            )
        )
    extra_node = build_extra_auto_bookings_node(
        find_extra_auto_bookings(
            auto_bookings,
            slot_payloads,
            existing_bookings=existing_bookings,
        )
    )
    if extra_node is not None:
        roots.append(extra_node)
    recompute_tree(roots)
    return roots


TREE_HEADER_LINES = 1


class BookTreePrompt:
    def __init__(
        self,
        roots: list[PromptTreeNode],
        *,
        title: str,
        conflicts_out: Path,
    ) -> None:
        self.roots = roots
        self.title = title
        self.conflicts_out = conflicts_out
        self.cursor = 0
        self.scroll_row = 0
        self.action: str | None = None
        self.status = ""
        self.conflicts_view = False
        self.conflict_panel_lines: list[str] = []
        self.conflict_scroll = 0
        self.visible_rows: list[VisibleRow] = []

        recompute_tree(self.roots)
        self.rebuild_visible_rows()

        self._display_text: FormattedText = FormattedText([])
        self.formatted_control = FormattedTextControl(
            text=lambda: self._display_text,
            focusable=False,
        )
        self.kb = KeyBindings()
        self._bind_keys()

        self.frame = Frame(Box(Window(content=self.formatted_control, wrap_lines=False), padding=0), title=title)
        self.app = Application(
            layout=Layout(self.frame),
            key_bindings=self.kb,
            full_screen=True,
            refresh_interval=None,
            style=BOOK_TREE_STYLE,
        )

    def rebuild_visible_rows(self) -> None:
        self.visible_rows = walk_visible(self.roots)
        self.clamp_cursor()

    def _bind_keys(self) -> None:
        @self.kb.add("up")
        def _up(_event) -> None:
            if self.conflicts_view:
                self.conflict_scroll = max(0, self.conflict_scroll - 1)
            else:
                self.cursor = max(0, self.cursor - 1)
            self.refresh()

        @self.kb.add("down")
        def _down(_event) -> None:
            if self.conflicts_view:
                self.conflict_scroll += 1
            else:
                self.cursor = min(len(self.visible_rows) - 1, self.cursor + 1)
            self.refresh()

        @self.kb.add("<scroll-up>")
        def _scroll_up(_event) -> None:
            if self.conflicts_view:
                self.conflict_scroll = max(0, self.conflict_scroll - 3)
            else:
                self.scroll_row = max(0, self.scroll_row - 3)
            self.refresh()

        @self.kb.add("<scroll-down>")
        def _scroll_down(_event) -> None:
            if self.conflicts_view:
                self.conflict_scroll += 3
            else:
                max_scroll = max(0, len(self.visible_rows) - self._viewport_tree_rows())
                self.scroll_row = min(max_scroll, self.scroll_row + 3)
            self.refresh()

        @self.kb.add("left")
        def _collapse(_event) -> None:
            node = self.visible_rows[self.cursor].node
            if node.children and node.expanded:
                node.expanded = False
                self.rebuild_visible_rows()
            self.refresh()

        @self.kb.add("right")
        def _expand(_event) -> None:
            node = self.visible_rows[self.cursor].node
            if node.children and not node.expanded:
                node.expanded = True
                self.rebuild_visible_rows()
            self.refresh()

        @self.kb.add("tab")
        def _toggle_expanded(_event) -> None:
            node = self.visible_rows[self.cursor].node
            if node.children:
                node.expanded = not node.expanded
                self.rebuild_visible_rows()
            self.refresh()

        @self.kb.add(" ")
        def _toggle_checked(_event) -> None:
            if self.conflicts_view:
                return
            node = self.visible_rows[self.cursor].node
            if node.slot_value is not None or node.extra_auto_booking is not None:
                _toggle_slot_checked(node)
            else:
                set_subtree_checked(node, node.state != CheckState.CHECKED)
            recompute_tree(self.roots)
            self.refresh()

        @self.kb.add("a")
        def _select_all(_event) -> None:
            set_all_bookable_checked(self.roots, True)
            recompute_tree(self.roots)
            self.refresh()

        @self.kb.add("n")
        def _clear(_event) -> None:
            set_all_bookable_checked(self.roots, False)
            recompute_tree(self.roots)
            self.refresh()

        @self.kb.add("b")
        @self.kb.add("enter")
        def _book(_event) -> None:
            if self.conflicts_view:
                self.status = "press V to return to tree"
                self.refresh()
                return
            if count_selected_booking_slots(self.roots) == 0:
                self.status = "nothing selected"
                self.refresh()
                return
            self.action = "book"
            _event.app.exit()

        @self.kb.add("x")
        @self.kb.add("X")
        def _cancel_extra(_event) -> None:
            if self.conflicts_view:
                self.status = "press V to return to tree"
                self.refresh()
                return
            if count_selected_extra_bookings(self.roots) == 0:
                self.status = "no extra bookings selected"
                self.refresh()
                return
            self.action = "cancel_extra"
            _event.app.exit()

        @self.kb.add("s")
        def _skip_conflicts(_event) -> None:
            if self.conflicts_view:
                return
            node = self.visible_rows[self.cursor].node
            if node.review_kind != "conflict":
                self.status = "skip only applies to conflict slots"
                self.refresh()
                return
            node.conflict_mode = "skip"
            set_subtree_checked(node, False)
            recompute_tree(self.roots)
            self.refresh()

        @self.kb.add("p")
        def _split_conflicts(_event) -> None:
            if self.conflicts_view:
                return
            node = self.visible_rows[self.cursor].node
            if node.review_kind != "conflict":
                self.status = "split only applies to conflict slots"
                self.refresh()
                return
            if not _can_split_review_slot(node):
                self.status = "split only for weekly slots"
                self.refresh()
                return
            node.conflict_mode = "split"
            set_subtree_checked(node, True)
            recompute_tree(self.roots)
            self.refresh()

        @self.kb.add("v")
        @self.kb.add("V")
        def _view_conflicts(_event) -> None:
            self._toggle_conflicts_view()

        @self.kb.add("]")
        def _conflict_scroll_down(_event) -> None:
            if not self.conflicts_view:
                return
            self.conflict_scroll += 8
            self.refresh()

        @self.kb.add("[")
        def _conflict_scroll_up(_event) -> None:
            if not self.conflicts_view:
                return
            self.conflict_scroll = max(0, self.conflict_scroll - 8)
            self.refresh()

        @self.kb.add("w")
        @self.kb.add("W")
        def _save_conflicts(_event) -> None:
            self._save_conflicts_to_file()

        @self.kb.add("q")
        @self.kb.add("c-c")
        @self.kb.add("escape")
        def _quit(_event) -> None:
            _event.app.exit()

    def _save_conflicts_to_file(self) -> None:
        slot_count = save_conflicts_report(self.roots, self.conflicts_out)
        self.status = f"saved {slot_count} slot(s) → {self.conflicts_out}"
        print(f"Conflicts written to {self.conflicts_out}", file=sys.stderr)
        self.refresh()

    def _toggle_conflicts_view(self) -> None:
        if self.conflicts_view:
            self.conflicts_view = False
            self.conflict_scroll = 0
            self.status = ""
            self.refresh()
            return

        conflict_count = _count_conflict_slots(self.roots)
        if conflict_count == 0:
            self.status = "no conflicts"
            self.refresh()
            return

        self.conflict_panel_lines = _format_all_conflicts_grouped(self.roots)
        self.conflict_scroll = 0
        self.conflicts_view = True
        self.status = f"{conflict_count} conflicting slot(s)"
        self.refresh()

    def _frame_title(self) -> str:
        if self.conflicts_view:
            keys = "V back to tree · ↑/↓ or [/] scroll · W save · Q quit"
            title = f"All conflicts — {keys}"
        else:
            selected_slots = count_selected_booking_slots(self.roots)
            selected_extra = count_selected_extra_bookings(self.roots)
            keys = (
                "↑/↓ move · Space toggle · A all · N none · "
                "S skip · P split · V conflicts · W save · B book · X cancel extra · Q quit"
            )
            title = f"book {selected_slots} · cancel extra {selected_extra} — {keys}"
        if self.status:
            title = f"{title} — {self.status}"
        return title

    def clamp_cursor(self) -> None:
        self.cursor = min(self.cursor, max(0, len(self.visible_rows) - 1))

    def _terminal_body_lines(self) -> int:
        try:
            terminal_height = shutil.get_terminal_size().lines
        except OSError:
            terminal_height = 24
        return max(12, terminal_height - 4)

    def _viewport_tree_rows(self) -> int:
        body_lines = self._terminal_body_lines() - TREE_HEADER_LINES
        return max(6, body_lines)

    def _ensure_cursor_visible(self, row_count: int) -> None:
        viewport = self._viewport_tree_rows()
        max_scroll = max(0, row_count - viewport)
        if self.cursor < self.scroll_row:
            self.scroll_row = self.cursor
        elif self.cursor >= self.scroll_row + viewport:
            self.scroll_row = min(max_scroll, self.cursor - viewport + 1)
        self.scroll_row = min(self.scroll_row, max_scroll)

    def refresh(self) -> None:
        self.frame.title = self._frame_title()

        if self.conflicts_view:
            viewport = self._viewport_tree_rows()
            detail_block = ["── by program / course / component / slot ──", *self.conflict_panel_lines]
            max_scroll = max(0, len(detail_block) - viewport)
            self.conflict_scroll = min(self.conflict_scroll, max_scroll)
            window = detail_block[self.conflict_scroll : self.conflict_scroll + viewport]
            lines: list[str] = []
            if max_scroll > 0:
                lines.append(
                    f"lines {self.conflict_scroll + 1}–{self.conflict_scroll + len(window)}"
                    f" of {len(detail_block)}"
                )
            lines.extend(window)
            self._display_text = FormattedText([("", "\n".join(lines))])
            return

        rows = self.visible_rows
        self.clamp_cursor()
        self._ensure_cursor_visible(len(rows))
        viewport = self._viewport_tree_rows()
        row_end = min(len(rows), self.scroll_row + viewport)
        parts: FormattedText = []
        if len(rows) > viewport:
            parts.append(
                (
                    "",
                    f"rows {self.scroll_row + 1}–{row_end} of {len(rows)}\n",
                )
            )
        for index, row in enumerate(rows[self.scroll_row : row_end]):
            row_index = self.scroll_row + index
            check_marker = check_marker_for_node(row.node)
            expand_marker = ("v" if row.node.expanded else ">") if row.node.children else " "
            cursor = ">" if row_index == self.cursor else " "
            indent = "  " * row.depth
            parts.append(("", f"{cursor} {indent}{expand_marker} {check_marker} "))
            parts.extend(format_node_label_formatted(row.node))
            parts.append(("", "\n"))
        self._display_text = FormattedText(parts)

    def run(self) -> str | None:
        self.refresh()
        self.app.run()
        return self.action


def _confirm(prompt: str) -> bool:
    while True:
        answer = input(f"{prompt} [y/N]: ").strip().lower()
        if answer in {"", "n", "no"}:
            return False
        if answer in {"y", "yes"}:
            return True
        print("Type y or n.")


def _resolve_program_index(programs: list[ProgramGroup], preset: str | None) -> int:
    if preset is None:
        return 0
    for index, program in enumerate(programs):
        if program.name == preset:
            return index
    print(f"Unknown program: {preset}", file=sys.stderr)
    for program in programs:
        print(f"  {program.name}", file=sys.stderr)
    sys.exit(1)


def _submit_bookings(
    *,
    api_url: str,
    token: str,
    bookings: list[dict[str, Any]],
) -> None:
    try:
        result = post_auto_bookings_batch(api_url=api_url, token=token, bookings=bookings)
    except httpx.HTTPStatusError as error:
        print(f"HTTP {error.response.status_code}: {error.response.text[:500]}", file=sys.stderr)
        sys.exit(1)
    except httpx.HTTPError as error:
        print(f"Request failed: {error}", file=sys.stderr)
        sys.exit(1)

    _print_batch_results(bookings, result)


def _submit_cancel_extra(
    *,
    api_url: str,
    token: str,
    targets: list[ExtraCancelTarget],
) -> None:
    bmp_ids = [target.outlook_booking_id for target in targets if target.outlook_booking_id]
    slot_targets = [target for target in targets if not target.outlook_booking_id]

    if bmp_ids:
        try:
            result = delete_auto_bookings_batch(
                api_url=api_url,
                token=token,
                outlook_booking_ids=[booking_id for booking_id in bmp_ids if booking_id],
            )
        except httpx.HTTPStatusError as error:
            print(f"HTTP {error.response.status_code}: {error.response.text[:500]}", file=sys.stderr)
            sys.exit(1)
        except httpx.HTTPError as error:
            print(f"Request failed: {error}", file=sys.stderr)
            sys.exit(1)
        _print_cancel_batch_results(result)

    cancelled_entry: list[str] = []
    failed_entry: dict[str, str] = {}
    for target in slot_targets:
        key = target.display_key()
        try:
            cancel_extra_booking(api_url=api_url, token=token, target=target)
            cancelled_entry.append(key)
        except httpx.HTTPStatusError as error:
            failed_entry[key] = f"HTTP {error.response.status_code}: {error.response.text[:200]}"
        except httpx.HTTPError as error:
            failed_entry[key] = str(error)

    _print_cancel_entry_results(cancelled=cancelled_entry, failed=failed_entry)
    if failed_entry:
        sys.exit(1)


def prepare_booking_tree(
    programs: list[ProgramGroup],
    *,
    cfg: ScheduleConfig,
    api_url: str,
    token: str,
    program_name: str | None = None,
    slot_payloads: list[dict[str, Any]] | None = None,
) -> tuple[list[PromptTreeNode], str]:
    print("Loading existing bookings...", file=sys.stderr)
    auto_bookings = _try_fetch_auto_bookings(api_url=api_url, token=token)
    try:
        existing_bookings = fetch_existing_bookings_for_config(
            cfg,
            token=token,
            api_url=api_url,
        )
    except httpx.HTTPStatusError as error:
        print(f"HTTP {error.response.status_code}: {error.response.text[:500]}", file=sys.stderr)
        sys.exit(1)
    except httpx.HTTPError as error:
        print(f"Request failed: {error}", file=sys.stderr)
        sys.exit(1)

    if slot_payloads is None:
        slot_payloads = collect_all_slot_payloads(programs)

    expand_index = _resolve_program_index(programs, program_name)
    if program_name is not None:
        tree_programs = [programs[expand_index]]
    else:
        tree_programs = programs

    title = tree_programs[0].name if len(tree_programs) == 1 else "Book schedule"
    roots = build_prompt_tree(
        tree_programs,
        auto_bookings=auto_bookings,
        existing_bookings=existing_bookings,
        slot_payloads=slot_payloads,
    )
    return roots, title


def run_book_tui(
    programs: list[ProgramGroup],
    *,
    cfg: ScheduleConfig,
    api_url: str,
    token: str,
    program_name: str | None = None,
    conflicts_out: Path = SCRIPT_DIR / "booking-conflicts.txt",
    roots: list[PromptTreeNode] | None = None,
    title: str | None = None,
    slot_payloads: list[dict[str, Any]] | None = None,
) -> None:
    if slot_payloads is None:
        slot_payloads = collect_all_slot_payloads(programs)

    while True:
        if roots is None or title is None:
            roots, title = prepare_booking_tree(
                programs,
                cfg=cfg,
                api_url=api_url,
                token=token,
                program_name=program_name,
                slot_payloads=slot_payloads,
            )
        action = BookTreePrompt(roots, title=title, conflicts_out=conflicts_out).run()
        if action == "book":
            bookings = collect_review_payloads(roots)
            if not bookings:
                print("Nothing selected to submit.", file=sys.stderr)
                return

            _print_submission_preview(bookings)
            if not _confirm("Proceed"):
                return

            _submit_bookings(api_url=api_url, token=token, bookings=bookings)
            return

        if action == "cancel_extra":
            cancel_targets = collect_selected_extra_cancel_targets(roots)
            if not cancel_targets:
                print("Nothing selected to cancel.", file=sys.stderr)
                return

            print(f"Cancel {len(cancel_targets)} extra auto booking(s):", file=sys.stderr)
            for target in cancel_targets:
                print(f"  {target.display_key()}", file=sys.stderr)
            if not _confirm("Proceed"):
                roots = None
                title = None
                continue

            _submit_cancel_extra(api_url=api_url, token=token, targets=cancel_targets)
            roots = None
            title = None
            continue

        return



def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Book course sessions from schedule config YAML via BMP batch API")
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG, help="Schedule config YAML")
    parser.add_argument(
        "--api-url",
        default=os.environ.get("ROOM_BOOKING_BATCH_URL", DEFAULT_API_URL),
        help="POST /auto-bookings/batch endpoint (or ROOM_BOOKING_BATCH_URL env)",
    )
    parser.add_argument(
        "--token",
        default=os.environ.get("ROOM_BOOKING_TOKEN"),
        help="Bearer token (or ROOM_BOOKING_TOKEN env)",
    )
    parser.add_argument("--program", help="Skip program picker (exact program label)")
    parser.add_argument(
        "--conflicts-out",
        type=Path,
        default=SCRIPT_DIR / "booking-conflicts.txt",
        help="Path for conflict report (W in TUI, or --export-conflicts)",
    )
    parser.add_argument(
        "--export-conflicts",
        action="store_true",
        help="Write all conflicts to --conflicts-out and exit (no TUI)",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> None:
    args = parse_args(argv)
    if not args.token:
        print("Set ROOM_BOOKING_TOKEN or pass --token", file=sys.stderr)
        sys.exit(1)
    if not args.config.is_file():
        print(f"Config not found: {args.config}", file=sys.stderr)
        sys.exit(1)

    cfg = ScheduleConfig.from_yaml(args.config)
    programs = build_program_groups_from_config(cfg)
    if not programs:
        print("No bookable sessions in config (need component.sessions with occurrences or weekly_pattern)", file=sys.stderr)
        sys.exit(1)

    slot_payloads = collect_all_slot_payloads(programs)
    roots, title = prepare_booking_tree(
        programs,
        cfg=cfg,
        api_url=args.api_url,
        token=args.token,
        program_name=args.program,
        slot_payloads=slot_payloads,
    )

    if args.export_conflicts:
        slot_count = save_conflicts_report(roots, args.conflicts_out)
        print(f"Wrote {slot_count} conflicting slot(s) to {args.conflicts_out}")
        return

    run_book_tui(
        programs,
        cfg=cfg,
        api_url=args.api_url,
        token=args.token,
        program_name=args.program,
        conflicts_out=args.conflicts_out,
        roots=roots,
        title=title,
        slot_payloads=slot_payloads,
    )


if __name__ == "__main__":
    main()
