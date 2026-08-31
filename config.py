import datetime
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Literal, Self

import yaml
from pydantic import BaseModel, ConfigDict, Field


class SettingBaseModel(BaseModel):
    model_config = ConfigDict(
        use_attribute_docstrings=True, extra="forbid", populate_by_name=True
    )


class Weekday(StrEnum):
    MONDAY = "MONDAY"
    TUESDAY = "TUESDAY"
    WEDNESDAY = "WEDNESDAY"
    THURSDAY = "THURSDAY"
    FRIDAY = "FRIDAY"
    SATURDAY = "SATURDAY"
    SUNDAY = "SUNDAY"

    @property
    def index(self) -> int:
        return list(Weekday).index(self)


def week_start_for_date(
    value: datetime.date, starting_day: Weekday = Weekday.MONDAY
) -> datetime.date:
    """First day of the calendar week containing ``value`` (week aligned to ``starting_day``)."""
    return value - datetime.timedelta(days=(value.weekday() - starting_day.index) % 7)


class TermTimeSlot(SettingBaseModel):
    start_time: datetime.time
    "Slot start time"
    end_time: datetime.time
    "Slot end time"


class TermConfig(SettingBaseModel):
    class DateRange(SettingBaseModel):
        start_date: datetime.date
        "Inclusive range start date in ISO format (YYYY-MM-DD)"
        end_date: datetime.date
        "Inclusive range end date in ISO format (YYYY-MM-DD)"

    name: str
    "Academic term name (for example, Fall 2025)"
    semester: DateRange
    "Single teaching period (start and end dates inclusive)"
    days: list[Weekday] = [
        Weekday.MONDAY,
        Weekday.TUESDAY,
        Weekday.WEDNESDAY,
        Weekday.THURSDAY,
        Weekday.FRIDAY,
        Weekday.SATURDAY,
    ]
    "Working days used by the scheduler (for example, MONDAY..SATURDAY)"
    starting_day: Weekday = Weekday.MONDAY
    "Starting day of the week (for example, MONDAY)"
    time_slots: list[TermTimeSlot] = [
        TermTimeSlot(start_time=datetime.time(9, 0), end_time=datetime.time(10, 30)),
        TermTimeSlot(start_time=datetime.time(10, 40), end_time=datetime.time(12, 10)),
        TermTimeSlot(start_time=datetime.time(12, 40), end_time=datetime.time(14, 10)),
        TermTimeSlot(start_time=datetime.time(14, 20), end_time=datetime.time(15, 50)),
        TermTimeSlot(start_time=datetime.time(16, 0), end_time=datetime.time(17, 30)),
        TermTimeSlot(start_time=datetime.time(17, 40), end_time=datetime.time(19, 10)),
        TermTimeSlot(start_time=datetime.time(19, 20), end_time=datetime.time(20, 50)),
    ]
    "Teaching slots for the term"
    sections: list["SectionConfig"] = Field(default_factory=list)
    "Section-based hierarchy from dtsn.yaml"
    instructor_positions: list[str] = []
    "Allowed instructor.position values (staff titles); empty means unrestricted"
    course_instructor_roles: list[str] = []
    "Allowed course.instructors[].role values (subject roles); empty means unrestricted"
    course_component_tags: list[str] = []
    "Allowed course.components[].tag values; empty means unrestricted"
    room_attributes: list["RoomAttributeDef"] = Field(default_factory=list)
    "Allowed room.features keys with types; empty means any key is allowed"


class RoomAttributeDef(SettingBaseModel):
    key: str
    "Attribute key stored in room.features"
    type: Literal["boolean", "string", "number", "enum", "list"]
    "Value type for this attribute"
    hint: str | None = None
    "Short hint shown next to the attribute in the room editor"
    enum_values: list[str] = Field(default_factory=list)
    "Allowed values when type is enum; ignored otherwise"


class RoomConfig(SettingBaseModel):
    id: str
    "Room identifier used in schedule output"
    name: str
    "Human-readable room name"
    capacity: int | None = None
    "Maximum room capacity"
    features: dict[str, bool | str | int | list[str]] = Field(default_factory=dict)
    "Room attribute values keyed by term.room_attributes[].key"


class InstructorConfig(SettingBaseModel):
    id: str
    "Instructor unique identifier"
    name_en: str | None = None
    "English display name"
    name_ru: str | None = None
    "Russian display name"
    email: str | None = None
    "Work email when known"
    alias: str | None = None
    "Short handle or Telegram-style alias from staff roster"
    position: str | None = None
    "Staff position from roster (for example, Full Professor, Visiting)"


class InstructorsConfig(SettingBaseModel):
    """Standalone instructors file (same shape as schedule-builder-backend instructors config)."""

    instructors: list[InstructorConfig] = []
    "Available instructors"

    @classmethod
    def from_yaml(cls, path: Path) -> Self:
        with open(path, encoding="utf-8") as handle:
            payload = yaml.safe_load(handle)
        return cls.model_validate(payload)


class SectionConfig(SettingBaseModel):
    class SectionProgram(SettingBaseModel):
        class ProgramTrack(SettingBaseModel):
            code: str
            "Track identifier"
            name: str
            "Track display name"
            groups: list[str] = []
            "Track groups as plain group codes"

        code: str
        "Program identifier"
        name: str
        "Program display name"
        tracks: list[ProgramTrack] = []
        "Program tracks (optional wrapper when groups are split by track)"
        groups: list[str] = []
        "Program-level groups when tracks are not used (for example, elective bucket ids)"
        time_slots: list[TermTimeSlot] | None = None
        "Optional custom teaching slots for this program; when set, replaces term.time_slots for this program"
        semester: TermConfig.DateRange | None = None
        "Optional teaching window for this program; when set, replaces term.semester for this program"

    code: str
    "Section identifier"
    name: str
    "Section display name"
    default_layout: Literal["groups", "compact_groups", "calendar"] | None = None
    "Default timetable layout when opening this section (groups, compact_groups, or calendar)"
    programs: list[SectionProgram] = []
    "Programs inside the section"


class StudentsGroups(SettingBaseModel):
    code: str
    "Student entity code (group/program/selector id)"
    kind: str
    "Distribution kind (for example, core/english/elective)"
    name: str | None = None
    "Optional display name"
    estimated_size: int | None = None
    "Expected student count"
    students: list[str] = []
    "Optional explicit student membership list"


type CommonCourseClassTags = Literal["lec", "tut", "lab", "class"]


class WeeklyPatternSlotEdit(SettingBaseModel):
    """Override or cancel one weekly pattern occurrence in a selected week."""

    select_week: datetime.date
    "Date (YYYY-MM-DD) identifying the week (any day in that week)"
    cancel: bool = False
    "If true, skip this meeting for the selected week"
    date: datetime.date | None = None
    "Optional concrete meeting date; defaults to ``weekday`` in that week"
    start_time: datetime.time | None = None
    "Optional meeting start; defaults to the pattern start_time"
    end_time: datetime.time | None = None
    "Optional meeting end; defaults to the pattern end_time"
    room: str | None = None
    "Optional room id; defaults to the pattern room"
    instructor: str | list[str] | None = None
    "Optional instructor id(s); defaults to the pattern instructor"


class WeeklyPatternSlot(SettingBaseModel):
    """Fixed weekly day/time for one meeting in a recurring core-course component."""

    weekday: Weekday
    "Weekday name (for example, MONDAY)"
    start_time: datetime.time
    "Meeting start time"
    end_time: datetime.time
    "Meeting end time"
    room: str | None = None
    "Room id from spreadsheet (for example, 460 or ONLINE)"
    instructor: str | list[str] | None = None
    "Instructor id, or list of ids for co-teaching"
    edits: list[WeeklyPatternSlotEdit] | None = None
    "Per-week overrides or cancellations keyed by ``select_week``"


@dataclass(frozen=True)
class ResolvedWeeklyMeeting:
    date: datetime.date
    start_time: datetime.time
    end_time: datetime.time
    room: str | None
    instructor: str | list[str] | None


class SessionOccurrence(SettingBaseModel):
    """One concrete placed meeting (date, time, room, instructor)."""

    date: datetime.date
    "Meeting date (YYYY-MM-DD)"
    start_time: datetime.time
    "Meeting start time"
    end_time: datetime.time
    "Meeting end time"
    room: str | None = None
    "Room id (None or empty if unknown)"
    instructor: str | list[str] | None = None
    "Instructor id(s) for this meeting"


class ComponentSessionSeries(SettingBaseModel):
    """Placed meeting series for a component (electives use calendar dates)."""

    audience: list[str] = []
    "Student group ids for this session series (subset of component audience)"
    weekly_pattern: list[WeeklyPatternSlot] | None = None
    "Fixed weekly slots for core courses"
    dates_pattern: list[SessionOccurrence] | None = None
    "Concrete placed meetings (for electives and other calendar-date series)"


class CourseConfig(SettingBaseModel):
    class Component(SettingBaseModel):
        tag: CommonCourseClassTags | str
        "Class tag (for example, lec, tut, lab, class)"
        per_week: int | None = None
        "Number of weekly meetings"
        per_semester: int | None = None
        "Total meetings across the semester (for electives and other non-weekly patterns)"
        instructor_pool: list[str | list[str]] = []
        """
        Candidate instructors; nested list means co-teaching set
        
        Example:
        - [nikolay_kudasov] # only nikolay_kudasov can teach this class
        - [nikolay_kudasov, anatoliy_baskakov] # any of them can teach this class
        - [[nikolay_kudasov, anatoliy_baskakov], [alexey_stepanov]] # any of them can teach this class, either nikolay_kudasov with anatoliy_baskakov co-teaching, or alexey_stepanov teaching alone
        """
        audience: list[str] = []
        """
        Who attends: each entry is a group id or an ``@`` selector from ``sections`` hierarchy (union if several).

        Examples:
        - ``[@BS_Y1_EN]`` — whole program
        - ``[@MS_Y1/AIDE]`` — one track
        - ``[@BS_Y2_EN/Software Development, @BS_Y2_EN/Cybersecurity]`` — union of tracks
        - ``[ENG-eap1]`` or ``[B22-CBS-02]`` — direct group id
        """
        expected_enrollment: int | None = None
        "Expected enrollment used for room sizing, defer from sum(student_group.size for groups in audience) if None"
        per_group: bool = False
        "Whether one class instance should be created per group, if True, then one class instance will be created for each group in audience. It is useful for lab classes where each group needs a separate meeting. If false, then one class instance (meeting) will be created for all groups in audience, so they will be effectively in same time, same room, same instructor."
        relates_to: int | list[int] | None = None
        "Optional component index or list of indices that this component depends on for same-day/order/back-to-back preferences."
        sessions: list[ComponentSessionSeries] | None = None
        "Concrete placed sessions when known (for example, summer electives from spreadsheet dates)"

    class CourseInstructor(SettingBaseModel):
        id: str
        "Instructor id from the global instructors list"
        role: str
        "Subject role (for example, Primary Instructor, Secondary Instructor, Teaching Assistant)"

    name: str
    "Course name"
    section_code: str
    "Exactly one timetable section this course belongs to (term.sections[].code)"
    short_name: str | None = None
    "Short English display name"
    name_ru: str | None = None
    "Russian display name"
    short_name_ru: str | None = None
    "Short Russian display name"
    instructors: list[CourseInstructor] = []
    "Subject-level staff with roles; preferred in event instructor pickers"
    components: list[Component]
    "Course subparts (lec/tut/lab/…) to schedule"


class ScheduleConfig(SettingBaseModel):
    schema_: str | None = Field(None, alias="$schema")
    "Optional JSON schema reference"
    term: TermConfig
    "Term-level configuration (sections are nested inside term)"
    rooms: list[RoomConfig] = []
    "Available rooms"
    instructors: list[InstructorConfig] = []
    "Available instructors"
    students_groups: list[StudentsGroups] = []
    "Student groups entries"
    courses: list[CourseConfig] = []
    "All courses to schedule"

    @classmethod
    def from_yaml(cls, path: Path) -> Self:
        with open(path, encoding="utf-8") as f:
            yaml_config = yaml.safe_load(f) or {}
        if isinstance(yaml_config, dict) and "sections" in yaml_config:
            term = dict(yaml_config.get("term") or {})
            if "sections" not in term:
                term["sections"] = yaml_config["sections"]
            yaml_config = {
                key: value for key, value in yaml_config.items() if key != "sections"
            }
            yaml_config["term"] = term
        return cls.model_validate(yaml_config)

    @classmethod
    def save_schema(cls, path: Path) -> None:
        with open(path, "w", encoding="utf-8") as f:
            schema = {
                "$schema": "https://json-schema.org/draft-07/schema",
                **cls.model_json_schema(),
            }
            yaml.dump(schema, f, sort_keys=False)

    @classmethod
    def openapi_schema(cls) -> dict:
        ref = "#/components/schemas/{model}"
        root = cls.model_json_schema(ref_template=ref)
        defs = root.pop("$defs", {})
        schemas = {**defs, cls.__name__: root}
        return {
            "openapi": "3.1.0",
            "info": {
                "title": "Schedule Assistant Config",
                "version": "0.1.0",
                "description": "OpenAPI schema for schedule-assistant YAML configuration files.",
            },
            "components": {"schemas": schemas},
        }

    @classmethod
    def save_openapi_schema(cls, path: Path) -> None:
        with open(path, "w", encoding="utf-8") as f:
            yaml.dump(cls.openapi_schema(), f, sort_keys=False)


def resolve_selector_map(cfg: ScheduleConfig) -> dict[str, set[str]]:
    selector_map: dict[str, set[str]] = {}
    for section in cfg.term.sections:
        for program in section.programs:
            program_groups: set[str] = set(program.groups)
            for track in program.tracks:
                if track.groups:
                    g = set(track.groups)
                    selector_map[f"@{program.code}/{track.name}"] = g
                    program_groups.update(g)
            if program_groups:
                selector_map[f"@{program.code}"] = program_groups
    return selector_map


def expand_groups(tokens: list[str], selector_map: dict[str, set[str]]) -> list[str]:
    out: set[str] = set()
    for t in tokens:
        if t in selector_map:
            out.update(selector_map[t])
        else:
            out.add(t)
    return sorted(out)


ScheduleConfig.model_rebuild()
