from __future__ import annotations

import argparse
import csv
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from config import InstructorConfig, InstructorsConfig

SCRIPT_DIR = Path(__file__).resolve().parent

EMAIL_PATTERN = re.compile(r"[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}")

USERS_CSV_DEFAULT_NAMES = (
    "Руслану для плагина - People.25-26.csv",
    "exportUsers_2026-4-14.csv",
)

DEFAULT_INSTRUCTORS_YAML = Path("instructors.yaml")


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


def to_instructor_id(name: str) -> str:
    slug = "".join(ch.lower() if ch.isalnum() else "_" for ch in name).strip("_")
    while "__" in slug:
        slug = slug.replace("__", "_")
    if not slug:
        slug = "unknown_instructor"
    return slug


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

    def to_instructor_config(self) -> InstructorConfig:
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
        return InstructorConfig.model_validate(payload)


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


def register_instructor_profile(
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


def build_roster_from_lookup(lookup: InstructorLookup) -> list[InstructorConfig]:
    profiles: dict[str, InstructorProfile] = {}
    for entry in lookup.people.iter_with_email():
        token = entry.name_en or entry.email or ""
        if token:
            register_instructor_profile(token, profiles, lookup)
    for export in lookup.export_by_email.values():
        token = export.name_en or export.name_ru or export.email
        if token:
            register_instructor_profile(token, profiles, lookup)
    return [
        profile.to_instructor_config()
        for profile in sorted(profiles.values(), key=lambda item: item.preferred_name().casefold())
    ]


def build_roster_from_csv_paths(csv_paths: list[Path]) -> list[InstructorConfig]:
    return build_roster_from_lookup(load_instructor_lookup(csv_paths))


def dump_instructors_yaml(config: InstructorsConfig) -> str:
    payload = config.model_dump(mode="json", exclude_none=True)
    return yaml.dump(payload, sort_keys=False, allow_unicode=True, width=10_000)


def _lookup_keys_for_instructor(instructor: InstructorConfig) -> list[str]:
    keys: list[str] = []
    if instructor.name_en:
        keys.extend(_en_name_lookup_keys(instructor.name_en))
    if instructor.name_ru:
        keys.extend(_ru_name_lookup_keys(instructor.name_ru))
    if instructor.email:
        keys.append(instructor.email.lower())
    if instructor.alias:
        keys.append(instructor.alias.lstrip("@").casefold())
    return keys


@dataclass
class InstructorRegistry:
    by_id: dict[str, InstructorConfig]
    name_index: dict[str, str]

    @classmethod
    def from_config(cls, config: InstructorsConfig) -> InstructorRegistry:
        by_id = {instructor.id: instructor for instructor in config.instructors}
        name_index: dict[str, str] = {}
        for instructor in config.instructors:
            for key in _lookup_keys_for_instructor(instructor):
                name_index.setdefault(key, instructor.id)
        return cls(by_id=by_id, name_index=name_index)

    @classmethod
    def from_yaml(cls, path: Path) -> InstructorRegistry:
        return cls.from_config(InstructorsConfig.from_yaml(path))

    def resolve_id(self, token: str) -> str:
        cleaned = " ".join(token.split())
        if not cleaned:
            return to_instructor_id(token)
        if "@" in cleaned:
            hit = self.name_index.get(cleaned.lower())
            if hit:
                return hit
        for key in _en_name_lookup_keys(cleaned):
            hit = self.name_index.get(key)
            if hit:
                return hit
        if _has_cyrillic(cleaned):
            for key in _ru_name_lookup_keys(cleaned):
                hit = self.name_index.get(key)
                if hit:
                    return hit
        alias_key = cleaned.lstrip("@").casefold()
        hit = self.name_index.get(alias_key)
        if hit:
            return hit
        return to_instructor_id(cleaned)

    def seed_instructors_map(self) -> dict[str, InstructorConfig]:
        return dict(self.by_id)

    def resolve_into_map(self, name: str, instructors_map: dict[str, InstructorConfig]) -> str:
        instructor_id = self.resolve_id(name)
        if instructor_id not in instructors_map:
            if instructor_id in self.by_id:
                instructors_map[instructor_id] = self.by_id[instructor_id]
            else:
                display = " ".join(name.split())
                name_en, name_ru = _split_display_name(display)
                instructors_map[instructor_id] = InstructorConfig(
                    id=instructor_id,
                    name_en=name_en or (display if not name_ru else None),
                    name_ru=name_ru,
                )
        return instructor_id


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Build instructors.yaml from People and exportUsers CSV rosters"
    )
    parser.add_argument(
        "output_yaml",
        type=Path,
        nargs="?",
        default=DEFAULT_INSTRUCTORS_YAML,
        help=f"Output path (default: {DEFAULT_INSTRUCTORS_YAML.name})",
    )
    parser.add_argument(
        "--users-csv",
        type=Path,
        default=None,
        help="Staff directory CSV. If omitted, loads default People/exportUsers files from cwd.",
    )
    args = parser.parse_args()

    search_dirs = (Path.cwd(), SCRIPT_DIR)
    csv_paths = resolve_users_csv_paths(args.users_csv, *search_dirs)
    if not csv_paths or not any(path.exists() for path in csv_paths):
        raise FileNotFoundError(
            "No staff CSV files found. Pass --users-csv or place default roster files in the project dir."
        )

    roster = build_roster_from_csv_paths(csv_paths)
    config = InstructorsConfig(instructors=roster)
    args.output_yaml.write_text(dump_instructors_yaml(config), encoding="utf-8")
    print(f"Wrote {args.output_yaml} ({len(roster)} instructors)")


if __name__ == "__main__":
    main()
