from datetime import date, timedelta

import pytest
import yaml
from pydantic import ValidationError

from book import (
    TreeSlotValue,
    build_program_groups_from_config,
    collect_all_slot_payloads,
    detect_slot_conflicts,
    iter_slot_occurrences,
    resolve_session_window,
)
from config import (
    ScheduleConfig,
    TermConfig,
    Weekday,
    WeeklyAlternation,
    week_start_for_date,
)


def make_config() -> ScheduleConfig:
    return ScheduleConfig.model_validate(
        {
            "term": {
                "name": "Fall",
                "semester": {"start_date": "2026-09-01", "end_date": "2026-09-30"},
                "blocks": [
                    {
                        "id": "first",
                        "name": "First block",
                        "start_date": "2026-09-01",
                        "end_date": "2026-09-15",
                    }
                ],
                "sections": [
                    {
                        "code": "core",
                        "name": "Core",
                        "programs": [
                            {
                                "code": "P1",
                                "name": "P1",
                                "groups": ["G1"],
                                "semester": {
                                    "start_date": "2026-08-25",
                                    "end_date": "2026-10-06",
                                },
                            },
                            {
                                "code": "P2",
                                "name": "P2",
                                "tracks": [
                                    {"code": "T", "name": "Track", "groups": ["G2"]}
                                ],
                                "semester": {
                                    "start_date": "2026-09-08",
                                    "end_date": "2026-09-22",
                                },
                            },
                        ],
                    }
                ],
            },
            "rooms": [{"id": "108", "name": "Room 108"}],
            "courses": [
                {
                    "name": "Math",
                    "section_code": "core",
                    "blocks": ["first"],
                    "components": [
                        {
                            "tag": "lec",
                            "audience": ["G1"],
                            "sessions": [
                                {
                                    "weekly_pattern": [
                                        {
                                            "weekday": "TUESDAY",
                                            "start_time": "09:00",
                                            "end_time": "10:30",
                                            "room": "108",
                                        }
                                    ]
                                }
                            ],
                        }
                    ],
                }
            ],
        }
    )


def booked_dates(cfg: ScheduleConfig) -> list[date]:
    dates = []
    for program in build_program_groups_from_config(cfg):
        for course in program.courses:
            for component in course.components:
                for slot in component.slots:
                    if not slot.recurring:
                        dates.append(date.fromisoformat(slot.date))
                        continue
                    recurrence = slot.recurrence
                    assert recurrence is not None
                    current = date.fromisoformat(recurrence["start_date"])
                    end = date.fromisoformat(recurrence["until_date"])
                    while current <= end:
                        dates.append(current)
                        current += timedelta(days=7)
    return sorted(dates)


def test_schema_round_trip_and_defaults(tmp_path) -> None:
    cfg = make_config()
    session = cfg.courses[0].components[0].sessions[0]
    session.active = TermConfig.DateRange(
        start_date="2026-09-08", end_date="2026-09-15"
    )
    path = tmp_path / "config.yaml"
    path.write_text(yaml.safe_dump(cfg.model_dump(mode="json", by_alias=True)))
    assert ScheduleConfig.from_yaml(path) == cfg
    assert cfg.term.blocks[0].id == "first"
    assert cfg.courses[0].blocks == ["first"]

    raw = cfg.model_dump(mode="json")
    del raw["term"]["blocks"]
    del raw["courses"][0]["blocks"]
    del raw["courses"][0]["components"][0]["sessions"][0]["active"]
    defaults = ScheduleConfig.model_validate(raw)
    assert defaults.term.blocks == []
    assert defaults.courses[0].blocks is None
    assert defaults.courses[0].components[0].sessions[0].active is None
    schema = ScheduleConfig.model_json_schema()
    assert "TermBlock" in schema["$defs"]
    assert "active" in schema["$defs"]["ComponentSessionSeries"]["properties"]
    assert "TermBlock" in ScheduleConfig.openapi_schema()["components"]["schemas"]


@pytest.mark.parametrize("blocks", [None, [], ["first"], ["missing-block"]])
def test_nullable_course_blocks_round_trip_without_bounding_dates(
    tmp_path, blocks
) -> None:
    raw = make_config().model_dump(mode="json")
    raw["courses"][0]["blocks"] = blocks
    cfg = ScheduleConfig.model_validate(raw)
    assert cfg.courses[0].blocks == blocks
    path = tmp_path / "config.yaml"
    path.write_text(yaml.safe_dump(cfg.model_dump(mode="json", by_alias=True)))
    restored = ScheduleConfig.from_yaml(path)
    assert restored.courses[0].blocks == blocks
    assert booked_dates(restored) == booked_dates(make_config())
    property_schema = ScheduleConfig.model_json_schema()["$defs"]["CourseConfig"][
        "properties"
    ]["blocks"]
    assert property_schema["default"] is None
    assert {variant["type"] for variant in property_schema["anyOf"]} == {
        "array",
        "null",
    }


def test_active_requires_raw_dates_not_block_ids() -> None:
    raw = make_config().model_dump(mode="json")
    raw["courses"][0]["components"][0]["sessions"][0]["active"] = "first"
    with pytest.raises(ValidationError):
        ScheduleConfig.model_validate(raw)


@pytest.mark.parametrize("audience", [["G1"], ["@P1"]])
def test_null_active_uses_program_window_without_term_or_block_clipping(
    audience,
) -> None:
    cfg = make_config()
    session = cfg.courses[0].components[0].sessions[0]
    session.audience = audience
    assert resolve_session_window(cfg, audience, None).model_dump(mode="json") == {
        "start_date": "2026-08-25",
        "end_date": "2026-10-06",
    }
    assert booked_dates(cfg) == [
        date(2026, 8, 25),
        *[date(2026, 9, day) for day in (1, 8, 15, 22, 29)],
        date(2026, 10, 6),
    ]
    cfg.courses[0].blocks = ["missing-block"]
    cfg.term.blocks = []
    assert len(booked_dates(cfg)) == 7


@pytest.mark.parametrize("audience", [[], ["unknown"]])
def test_default_window_for_empty_or_unknown_audiences(audience) -> None:
    cfg = make_config()
    assert resolve_session_window(cfg, audience, None) == cfg.term.semester


@pytest.mark.parametrize("audience", [["G1", "G2"], ["@P1", "@P2/T"], ["@P2/Track"]])
def test_explicit_active_intersects_all_program_windows_inclusively(audience) -> None:
    cfg = make_config()
    session = cfg.courses[0].components[0].sessions[0]
    session.audience = audience
    session.active = TermConfig.DateRange(
        start_date="2026-09-01", end_date="2026-09-15"
    )
    assert booked_dates(cfg) == [date(2026, 9, 8), date(2026, 9, 15)]
    session.active = TermConfig.DateRange(
        start_date="2026-09-22", end_date="2026-10-20"
    )
    assert booked_dates(cfg) == [date(2026, 9, 22)]
    session.active = TermConfig.DateRange(
        start_date="2026-10-01", end_date="2026-10-06"
    )
    assert booked_dates(cfg) == []


def test_split_same_audience_sessions_and_presets_are_independent() -> None:
    cfg = make_config()
    component = cfg.courses[0].components[0]
    first = component.sessions[0]
    first.active = TermConfig.DateRange(start_date="2026-09-01", end_date="2026-09-15")
    second = first.model_copy(deep=True)
    second.active = TermConfig.DateRange(start_date="2026-09-16", end_date="2026-09-30")
    component.sessions.append(second)
    cfg.term.blocks[0].start_date = date(2026, 10, 1)
    cfg.term.blocks = []
    before = cfg.model_dump()
    groups = build_program_groups_from_config(cfg)
    assert len(groups[0].courses[0].components) == 2
    assert booked_dates(cfg) == [date(2026, 9, day) for day in (1, 8, 15, 22, 29)]
    assert cfg.model_dump() == before


def test_alternation_preserves_phase_before_anchor_and_across_split_windows() -> None:
    cfg = make_config()
    component = cfg.courses[0].components[0]
    first = component.sessions[0]
    first.weekly_pattern[0].alternation = WeeklyAlternation(anchor_week="2026-09-15")
    assert booked_dates(cfg) == [date(2026, 9, day) for day in (1, 15, 29)]
    first = component.sessions[0]
    first.active = TermConfig.DateRange(start_date="2026-09-08", end_date="2026-09-20")
    second = first.model_copy(deep=True)
    second.active = TermConfig.DateRange(start_date="2026-09-21", end_date="2026-10-06")
    component.sessions.append(second)
    assert booked_dates(cfg) == [date(2026, 9, 15), date(2026, 9, 29)]
    slots = [
        slot
        for node in build_program_groups_from_config(cfg)[0].courses[0].components
        for slot in node.slots
    ]
    assert all(not slot.recurring for slot in slots)


def test_alternation_uses_starting_day_and_ignores_inactive_edits() -> None:
    cfg = make_config()
    raw = cfg.model_dump(mode="json")
    raw["term"]["starting_day"] = "WEDNESDAY"
    session = raw["courses"][0]["components"][0]["sessions"][0]
    session["active"] = {"start_date": "2026-09-01", "end_date": "2026-09-30"}
    slot = session["weekly_pattern"][0]
    slot["alternation"] = {"anchor_week": "2026-09-02"}
    slot["edits"] = [
        {"select_week": "2026-09-01", "date": "2026-09-03"},
        {"select_week": "2026-09-08", "cancel": True},
        {"select_week": "2026-09-22", "date": "2026-10-20"},
    ]
    cfg = ScheduleConfig.model_validate(raw)
    assert booked_dates(cfg) == [date(2026, 10, 20)]
    assert week_start_for_date(date(2026, 9, 8), Weekday.WEDNESDAY) == date(2026, 9, 2)


def test_concrete_dates_and_moved_overrides_are_not_silently_clipped() -> None:
    raw = make_config().model_dump(mode="json")
    session = raw["courses"][0]["components"][0]["sessions"][0]
    session["active"] = {"start_date": "2026-09-01", "end_date": "2026-09-15"}
    session["weekly_pattern"][0]["edits"] = [
        {"select_week": "2026-09-01", "date": "2026-10-20"},
        {"select_week": "2026-09-08", "cancel": True},
    ]
    session["dates_pattern"] = [
        {
            "date": "2026-11-03",
            "start_time": "09:00",
            "end_time": "10:30",
            "room": "108",
        }
    ]
    cfg = ScheduleConfig.model_validate(raw)
    assert booked_dates(cfg) == [
        date(2026, 9, 15),
        date(2026, 10, 20),
        date(2026, 11, 3),
    ]
    cfg.courses[0].components[0].sessions[0].active = TermConfig.DateRange(
        start_date="2026-12-01", end_date="2026-12-02"
    )
    assert booked_dates(cfg) == [date(2026, 11, 3)]


def test_payloads_and_conflict_detection_use_windowed_alternating_dates() -> None:
    cfg = make_config()
    session = cfg.courses[0].components[0].sessions[0]
    session.active = TermConfig.DateRange(
        start_date="2026-09-08", end_date="2026-09-30"
    )
    session.weekly_pattern[0].alternation = WeeklyAlternation(anchor_week="2026-08-31")
    programs = build_program_groups_from_config(cfg)
    payloads = collect_all_slot_payloads(programs)
    assert [payload["start"][:10] for payload in payloads] == [
        "2026-09-15",
        "2026-09-29",
    ]
    assert all("recurrence" not in payload for payload in payloads)
    component = programs[0].courses[0].components[0]
    items = [TreeSlotValue("Math", component, slot) for slot in component.slots]
    assert [
        start.date() for item in items for start, _ in iter_slot_occurrences(item)
    ] == [date(2026, 9, 15), date(2026, 9, 29)]
    bookings = [
        {
            "room_id": "108",
            "title": "Other booking",
            "start": f"2026-09-{day:02d}T09:00:00+03:00",
            "end": f"2026-09-{day:02d}T10:30:00+03:00",
        }
        for day in (1, 8, 15, 22, 29)
    ]
    reports = detect_slot_conflicts(items, bookings)
    assert [
        start.date()
        for report in reports
        for start, _, _ in report.conflicting_occurrences
    ] == [date(2026, 9, 15), date(2026, 9, 29)]


def test_disjoint_program_windows_and_reversed_active_emit_no_weekly_bookings() -> None:
    cfg = make_config()
    session = cfg.courses[0].components[0].sessions[0]
    session.audience = ["G1", "G2"]
    cfg.term.sections[0].programs[1].semester = TermConfig.DateRange(
        start_date="2026-10-07", end_date="2026-10-20"
    )
    assert resolve_session_window(cfg, session.audience, None) is None
    assert booked_dates(cfg) == []
    session.audience = ["G1"]
    session.active = TermConfig.DateRange(
        start_date="2026-09-20", end_date="2026-09-10"
    )
    assert booked_dates(cfg) == []
