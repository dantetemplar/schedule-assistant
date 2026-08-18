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
# Azure AD imAddresses: ObjectId (32 hex, no dashes) glued to the real local-part.
AZURE_IM_ADDRESS_PATTERN = re.compile(r"^[0-9a-f]{32}.+@", re.IGNORECASE)

USERS_CSV_DEFAULT_NAMES = (
    "2024-26.ProjectionFaculty - People.25-26 (1) (2).csv",
    "Руслану для плагина - People.25-26.csv",
    "exportUsers_2026-4-14.csv",
)

DEFAULT_INSTRUCTORS_YAML = Path("instructors.yaml")


def normalize_person_name(name: str) -> str:
    return " ".join(name.split()).casefold()


def _email_domain_rank(email: str) -> int:
    lowered = email.casefold()
    if lowered.endswith("@innopolis.university"):
        return 0
    if lowered.endswith("@innopolis.ru"):
        return 1
    # Guest / synced mailboxes — never preferred as instructor ids.
    if lowered.endswith("@innopolis.onmicrosoft.com") or "#ext#" in lowered:
        return 50
    if "@innopolis." in lowered:
        return 5
    return 10


def _is_azure_im_address(email: str) -> bool:
    """True for SIP/IM aliases like ``{objectIdWithoutDashes}{upn}@domain``."""
    return bool(AZURE_IM_ADDRESS_PATTERN.match(email))


def extract_emails(raw: str | None) -> list[str]:
    if not raw:
        return []
    emails: list[str] = []
    seen: set[str] = set()
    for match in EMAIL_PATTERN.findall(str(raw)):
        if "#EXT#" in match.upper():
            continue
        if _is_azure_im_address(match):
            continue
        email = match.lower()
        if email in seen:
            continue
        seen.add(email)
        emails.append(email)
    return emails


def pick_primary_email(raw: str | None) -> str | None:
    """Pick one email from a cell that may list several (prefer Innopolis domains)."""
    emails = extract_emails(raw)
    if not emails:
        return None
    return sorted(emails, key=lambda email: (_email_domain_rank(email), email))[0]


def _row_primary_email(row: dict[str, str]) -> str | None:
    """Best corporate email from an exportUsers row (UPN + alternate mails).

    Prefer UPN/mail/otherMails; imAddresses is only a fallback and Azure ObjectId
    SIP aliases are filtered out in ``extract_emails``.
    """
    for key in ("userPrincipalName", "mail", "otherMails"):
        email = pick_primary_email(str(row.get(key) or ""))
        if email:
            return email
    return pick_primary_email(str(row.get("imAddresses") or ""))


def _is_student_directory_row(row: dict[str, str]) -> bool:
    if str(row.get("jobTitle") or "").strip().casefold() == "student":
        return True
    dn = str(row.get("onPremisesDistinguishedName") or "")
    return "OU=Applicants," in dn


def _instructor_row_priority(row: dict[str, str], email: str | None = None) -> int:
    """Lower is better. Prefer staff + @innopolis.ru/@innopolis.university."""
    upn = str(row.get("userPrincipalName") or "")
    primary = (email or _row_primary_email(row) or upn).lower()
    rank = _email_domain_rank(primary) * 10
    if "#EXT#" in upn.upper():
        rank += 100
    if str(row.get("userType") or "").strip() == "Guest":
        rank += 50
    if _is_student_directory_row(row):
        rank += 20
    dn = str(row.get("onPremisesDistinguishedName") or "")
    if "OU=VizitingStaff," in dn:
        rank -= 5
    return rank


def _has_cyrillic(text: str) -> bool:
    return any("\u0400" <= char <= "\u04ff" for char in text)


_GIVEN_NAME_VARIANTS: dict[str, set[str]] = {
    "andrei": {"andrei", "andrey"},
    "andrey": {"andrei", "andrey"},
    "alexandr": {"alexandr", "alexander", "aleksandr"},
    "alexander": {"alexandr", "alexander", "aleksandr"},
    "aleksandr": {"alexandr", "alexander", "aleksandr"},
    "benjamin": {"benjamin", "benzhamin", "beniamin"},
    "benzhamin": {"benjamin", "benzhamin", "beniamin"},
    "beniamin": {"benjamin", "benzhamin", "beniamin"},
    "imam": {"imam", "muwaffaq"},
    "muwaffaq": {"imam", "muwaffaq"},
    "iaroslav": {"iaroslav", "yaroslav"},
    "yaroslav": {"iaroslav", "yaroslav"},
    "yuri": {"yuri", "yurii", "yury"},
    "yurii": {"yuri", "yurii", "yury"},
    "yury": {"yuri", "yurii", "yury"},
    "vladimir": {"vladimir", "vlad"},
    "xavier": {"xavier", "ksavier"},
    "ksavier": {"xavier", "ksavier"},
    "alexei": {"alexei", "alexey", "aleksei", "aleksey"},
    "alexey": {"alexei", "alexey", "aleksei", "aleksey"},
    "aleksei": {"alexei", "alexey", "aleksei", "aleksey"},
    "aleksey": {"alexei", "alexey", "aleksei", "aleksey"},
    "dmitri": {"dmitri", "dmitry", "dmitriy", "dmitrii"},
    "dmitry": {"dmitri", "dmitry", "dmitriy", "dmitrii"},
    "dmitriy": {"dmitri", "dmitry", "dmitriy", "dmitrii"},
    "dmitrii": {"dmitri", "dmitry", "dmitriy", "dmitrii"},
    "evgeni": {"evgeni", "evgeny", "evgeniy", "evgenii", "eugene"},
    "evgeny": {"evgeni", "evgeny", "evgeniy", "evgenii", "eugene"},
    "evgeniy": {"evgeni", "evgeny", "evgeniy", "evgenii", "eugene"},
    "evgenii": {"evgeni", "evgeny", "evgeniy", "evgenii", "eugene"},
    "eugene": {"evgeni", "evgeny", "evgeniy", "evgenii", "eugene"},
    "georgy": {"georgy", "georgii", "georgiy", "george"},
    "georgii": {"georgy", "georgii", "georgiy", "george"},
    "georgiy": {"georgy", "georgii", "georgiy", "george"},
    "george": {"georgy", "georgii", "georgiy", "george"},
    "mikhail": {"mikhail", "michael"},
    "michael": {"mikhail", "michael"},
    "natalia": {"natalia", "natalya", "nataliya"},
    "natalya": {"natalia", "natalya", "nataliya"},
    "nataliya": {"natalia", "natalya", "nataliya"},
    "nikolai": {"nikolai", "nikolay", "nicolai", "nicolay"},
    "nikolay": {"nikolai", "nikolay", "nicolai", "nicolay"},
    "nicolai": {"nikolai", "nikolay", "nicolai", "nicolay"},
    "nicolay": {"nikolai", "nikolay", "nicolai", "nicolay"},
    "sergei": {"sergei", "sergey", "sergio"},
    "sergey": {"sergei", "sergey", "sergio"},
    "sergio": {"sergei", "sergey", "sergio"},
    "valeria": {"valeria", "valeriia", "valeriya"},
    "valeriia": {"valeria", "valeriia", "valeriya"},
    "valeriya": {"valeria", "valeriia", "valeriya"},
    "vlad": {"vlad", "vladislav"},
    "vladislav": {"vlad", "vladislav"},
}

# Latin surname spellings that refer to the same Russian family name.
_FAMILY_NAME_VARIANTS: dict[str, set[str]] = {
    "zuyev": {"zuyev", "zouev", "zuev"},
    "zouev": {"zuyev", "zouev", "zuev"},
    "zuev": {"zuyev", "zouev", "zuev"},
    # Потёмкин: ё→e (Potemkin), ё→yo (Potyomkin), email/old Potyomckin
    "potemkin": {"potemkin", "potyomkin", "potyomckin"},
    "potyomkin": {"potemkin", "potyomkin", "potyomckin"},
    "potyomckin": {"potemkin", "potyomkin", "potyomckin"},
    # Корнева (lessons) vs Корнаева (People / email)
    "korneva": {"korneva", "kornaeva"},
    "kornaeva": {"korneva", "kornaeva"},
    "creed": {"creed", "kreed"},
    "kreed": {"creed", "kreed"},
    "andryushchenko": {"andryushchenko", "andriushchenko"},
    "andriushchenko": {"andryushchenko", "andriushchenko"},
    "harin": {"harin", "kharin"},
    "kharin": {"harin", "kharin"},
    "kostanyan": {"kostanyan", "kostanian"},
    "kostanian": {"kostanyan", "kostanian"},
    "mohammadifar": {"mohammadifar", "mohammadi"},
    "mohammadi": {"mohammadifar", "mohammadi"},
}

# Passport / scholarly RU→LAT (щ→shch matches Moshchanetskii).
_CYR_TO_LAT = {
    "а": "a",
    "б": "b",
    "в": "v",
    "г": "g",
    "д": "d",
    "е": "e",
    "ё": "e",
    "ж": "zh",
    "з": "z",
    "и": "i",
    "й": "i",
    "к": "k",
    "л": "l",
    "м": "m",
    "н": "n",
    "о": "o",
    "п": "p",
    "р": "r",
    "с": "s",
    "т": "t",
    "у": "u",
    "ф": "f",
    "х": "kh",
    "ц": "ts",
    "ч": "ch",
    "ш": "sh",
    "щ": "shch",
    "ъ": "",
    "ы": "y",
    "ь": "",
    "э": "e",
    "ю": "yu",
    "я": "ya",
}


def transliterate_ru_to_en(text: str, *, y_for_short_i: bool = False) -> str:
    table = dict(_CYR_TO_LAT)
    if y_for_short_i:
        table["й"] = "y"
    return "".join(table.get(ch, ch) for ch in text.casefold())


def _latin_forms(name: str) -> list[str]:
    """Latin renderings of a name (й→i and й→y variants)."""
    if not _has_cyrillic(name):
        return [name]
    forms = [
        transliterate_ru_to_en(name),
        transliterate_ru_to_en(name, y_for_short_i=True),
    ]
    seen: set[str] = set()
    unique: list[str] = []
    for form in forms:
        key = normalize_person_name(form)
        if key in seen:
            continue
        seen.add(key)
        unique.append(form)
    return unique


def _latin_form(name: str) -> str:
    return _latin_forms(name)[0]


def _given_names_compatible(left: str, right: str) -> bool:
    left_key = left.casefold()
    right_key = right.casefold()
    if left_key == right_key:
        return True
    left_variants = _GIVEN_NAME_VARIANTS.get(left_key, {left_key})
    return right_key in left_variants


def _family_names_compatible(left: str, right: str) -> bool:
    left_key = left.casefold()
    right_key = right.casefold()
    if left_key == right_key:
        return True
    left_variants = _FAMILY_NAME_VARIANTS.get(left_key, {left_key})
    return right_key in left_variants


def _en_name_lookup_keys(name: str) -> list[str]:
    cleaned = " ".join(name.split())
    if not cleaned:
        return []
    keys: set[str] = {normalize_person_name(cleaned)}
    for latin in _latin_forms(cleaned):
        keys.add(normalize_person_name(latin))
        parts = latin.split()
        if len(parts) < 2:
            continue
        keys.add(normalize_person_name(f"{parts[1]} {parts[0]}"))
        given = parts[0]
        family = " ".join(parts[1:])
        given_variants = _GIVEN_NAME_VARIANTS.get(given.casefold(), {given})
        family_variants = _FAMILY_NAME_VARIANTS.get(family.casefold(), {family})
        for given_variant in given_variants:
            for family_variant in family_variants:
                keys.add(normalize_person_name(f"{given_variant} {family_variant}"))
                keys.add(normalize_person_name(f"{family_variant} {given_variant}"))
        # Compact first+last for long Western names ("Karim … ElDakroury").
        if len(parts) >= 3:
            last = parts[-1]
            last_variants = _FAMILY_NAME_VARIANTS.get(last.casefold(), {last})
            for given_variant in given_variants:
                for last_variant in last_variants:
                    keys.add(normalize_person_name(f"{given_variant} {last_variant}"))
                    keys.add(normalize_person_name(f"{last_variant} {given_variant}"))
            # Also treat second token as a surname candidate (Xavier … Vasquez Zelada).
            for mid in parts[1:-1]:
                mid_family = _FAMILY_NAME_VARIANTS.get(mid.casefold(), {mid})
                for given_variant in given_variants:
                    for mid_variant in mid_family:
                        keys.add(
                            normalize_person_name(f"{given_variant} {mid_variant}")
                        )
                        keys.add(
                            normalize_person_name(f"{mid_variant} {given_variant}")
                        )
        # FIO / family-given: also index given+family without patronymic.
        if len(parts) >= 3:
            keys.add(normalize_person_name(f"{parts[1]} {parts[0]}"))
            keys.add(normalize_person_name(f"{parts[0]} {parts[1]}"))
            mid = parts[1]
            mid_variants = _GIVEN_NAME_VARIANTS.get(mid.casefold(), {mid})
            family_head = parts[0]
            family_head_variants = _FAMILY_NAME_VARIANTS.get(
                family_head.casefold(), {family_head}
            )
            for mid_variant in mid_variants:
                for family_variant in family_head_variants:
                    keys.add(normalize_person_name(f"{mid_variant} {family_variant}"))
                    keys.add(normalize_person_name(f"{family_variant} {mid_variant}"))
    return list(keys)


def _ru_name_lookup_keys(name: str) -> list[str]:
    cleaned = " ".join(name.split())
    if not cleaned:
        return []
    parts = cleaned.split()
    keys = {normalize_person_name(cleaned)}
    if len(parts) >= 2:
        keys.add(normalize_person_name(f"{parts[1]} {parts[0]}"))
    if len(parts) >= 3:
        # FIO → given family / family given without patronymic
        keys.add(normalize_person_name(f"{parts[1]} {parts[0]}"))
        keys.add(normalize_person_name(f"{parts[0]} {parts[1]}"))
    # Latin forms so EN lesson names resolve to the same instructor
    for key in list(keys):
        for latin in _latin_forms(key):
            keys.add(normalize_person_name(latin))
    return list(keys)


def _given_family_candidates(name: str) -> list[tuple[str, str]]:
    """Candidate (given, family) pairs in Latin, covering EN and RU FIO orders."""
    parts = [p for p in name.split() if p]
    if len(parts) < 2:
        return []
    candidates: list[tuple[str, str]] = []
    for latin_name in _latin_forms(name):
        latin_parts = latin_name.split()
        if len(latin_parts) < 2:
            continue
        candidates.append((latin_parts[0], latin_parts[-1]))
        candidates.append((latin_parts[-1], latin_parts[0]))
        if len(latin_parts) >= 3:
            # Traditional RU FIO: family given patronymic
            candidates.append((latin_parts[1], latin_parts[0]))
            # Compound Western names: Naghmeh Mohammadi Far / Xavier Antonio Vasquez
            candidates.append((latin_parts[0], latin_parts[1]))
            candidates.append((latin_parts[1], latin_parts[0]))
            for family_part in latin_parts[1:-1]:
                candidates.append((latin_parts[0], family_part))
    # Deduplicate while preserving order
    seen: set[tuple[str, str]] = set()
    unique: list[tuple[str, str]] = []
    for pair in candidates:
        key = (pair[0].casefold(), pair[1].casefold())
        if key in seen:
            continue
        seen.add(key)
        unique.append(pair)
    return unique


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
    include_in_roster: bool = True


def _load_export_users_directory(
    csv_path: Path,
) -> tuple[dict[str, ExportEntry], dict[str, ExportEntry], dict[str, ExportEntry]]:
    best_by_en: dict[str, tuple[ExportEntry, int]] = {}
    best_by_ru: dict[str, tuple[ExportEntry, int]] = {}
    by_email: dict[str, ExportEntry] = {}
    with csv_path.open(encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle):
            display_name = str(row.get("displayName") or "").strip()
            email = _row_primary_email(row)
            if not display_name or not email:
                continue
            # Keep student accounts for name→email resolution (many TAs), but do not
            # dump every student into instructors.yaml.
            is_student = _is_student_directory_row(row)
            name_en, name_ru = _split_display_name(display_name)
            entry = ExportEntry(
                email=email,
                name_en=name_en,
                name_ru=name_ru,
                include_in_roster=not is_student,
            )
            existing_email = by_email.get(email)
            if existing_email is None or (
                entry.include_in_roster and not existing_email.include_in_roster
            ):
                by_email[email] = entry
            priority = _instructor_row_priority(row, email)
            for key in _en_name_lookup_keys(name_en or display_name):
                current = best_by_en.get(key)
                if current is None or priority < current[1]:
                    best_by_en[key] = (entry, priority)
            if name_ru:
                for key in _ru_name_lookup_keys(name_ru):
                    current = best_by_ru.get(key)
                    if current is None or priority < current[1]:
                        best_by_ru[key] = (entry, priority)
            elif _has_cyrillic(display_name):
                for key in _ru_name_lookup_keys(display_name):
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
    emails: list[str] | None = None
    alias: str | None = None
    position: str | None = None

    def __post_init__(self) -> None:
        if self.emails is None:
            self.emails = extract_emails(self.email) if self.email else []
        if self.email is None and self.emails:
            self.email = pick_primary_email(", ".join(self.emails))


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
            primary = pick_primary_email(other.email) or other.email
            # Prefer Innopolis corporate email when merging conflicting addresses.
            if not self.email or _email_domain_rank(primary) < _email_domain_rank(
                self.email
            ):
                self.email = primary
                self.id = primary
            elif "," in (self.email or "") or "," in (self.id or ""):
                self.email = pick_primary_email(self.email) or self.email
                self.id = self.email or self.id
        if other.name_en and not self.name_en:
            self.name_en = other.name_en
        if other.name_ru:
            candidate_ru = other.name_ru
            if not self.name_ru or len(candidate_ru.split()) > len(
                self.name_ru.split()
            ):
                self.name_ru = candidate_ru
        if other.alias and not self.alias:
            self.alias = other.alias
        if other.position and not self.position:
            self.position = other.position
        if self.email:
            primary = pick_primary_email(self.email) or self.email
            self.email = primary
            if "@" in self.id or "," in self.id:
                self.id = primary
        if not self.email:
            self.id = to_instructor_id(self.name_en or self.name_ru or self.id)

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


def _header_index(header: list[str], *names: str) -> int:
    lowered = {cell.strip().casefold(): idx for idx, cell in enumerate(header)}
    for name in names:
        idx = lowered.get(name.casefold())
        if idx is not None:
            return idx
    return -1


def _people_csv_column_indices(header: list[str]) -> dict[str, int]:
    name_en_idx = _header_index(header, "English Name")
    name_ru_idx = _header_index(header, "Russian Name")
    if name_en_idx < 0 or name_ru_idx < 0:
        shtat_idx = next(
            (idx for idx, cell in enumerate(header) if cell.strip().startswith("ШТАТ")),
            1,
        )
        name_en_idx = max(0, shtat_idx - 1)
        name_ru_idx = shtat_idx
    email_idx = _header_index(header, "Email")
    if email_idx < 0:
        raise ValueError("People CSV header is missing Email column")
    return {
        "name_en": name_en_idx,
        "name_ru": name_ru_idx,
        "email": email_idx,
        "position": _header_index(header, "Position"),
        "alias": _header_index(header, "Alias"),
        "student": _header_index(header, "Student?", "IsStudent"),
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
        for email in entry.emails or []:
            self._by_email[email.lower()] = entry
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
        required_idx = max(email_idx, name_en_idx, name_ru_idx)
        for row in rows[header_idx + 1 :]:
            if not _is_people_data_row(row, columns):
                continue
            if len(row) <= required_idx:
                continue
            if (
                student_idx >= 0
                and student_idx < len(row)
                and str(row[student_idx] or "").strip().casefold() == "yes"
            ):
                continue
            name_en = str(row[name_en_idx] or "").strip()
            name_ru_raw = str(row[name_ru_idx] or "").strip()
            name_ru = (
                name_ru_raw if name_ru_raw and _has_cyrillic(name_ru_raw) else None
            )
            email_raw = str(row[email_idx] or "").strip()
            emails = extract_emails(email_raw)
            email = pick_primary_email(email_raw)
            alias_raw = (
                str(row[alias_idx] or "").strip()
                if alias_idx >= 0 and alias_idx < len(row)
                else ""
            )
            alias = alias_raw if alias_raw and alias_raw != "[не использует]" else None
            position_raw = (
                str(row[position_idx] or "").strip()
                if position_idx >= 0 and position_idx < len(row)
                else ""
            )
            position = position_raw if position_raw else None
            self._register(
                PeopleEntry(
                    name_en=name_en,
                    name_ru=name_ru,
                    email=email,
                    emails=emails,
                    alias=alias,
                    position=position,
                )
            )

    def find(self, token: str) -> PeopleEntry | None:
        cleaned = " ".join(token.split())
        if not cleaned:
            return None
        if "@" in cleaned:
            primary = pick_primary_email(cleaned)
            if primary:
                by_email = self._by_email.get(primary)
                if by_email:
                    return by_email
            for email in extract_emails(cleaned):
                by_email = self._by_email.get(email)
                if by_email:
                    return by_email
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

    def iter_unique(self) -> list[PeopleEntry]:
        unique: dict[int, PeopleEntry] = {}
        for entry in self._by_name.values():
            unique[id(entry)] = entry
        for entry in self._by_email.values():
            unique[id(entry)] = entry
        return list(unique.values())


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
    people_csv: Path | None = None
    for name in USERS_CSV_DEFAULT_NAMES:
        for directory in dirs:
            candidate = directory / name
            if not candidate.exists() or candidate in found:
                continue
            if _is_export_users_csv(candidate):
                found.append(candidate)
                continue
            # Prefer the first People sheet in USERS_CSV_DEFAULT_NAMES order.
            if people_csv is None:
                people_csv = candidate
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
    if export.email:
        if not profile.email or _email_domain_rank(export.email) < _email_domain_rank(
            profile.email
        ):
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
        if not profile.name_ru or len(candidate_ru.split()) > len(
            profile.name_ru.split()
        ):
            profile.name_ru = candidate_ru
    if people.email:
        if not profile.email or _email_domain_rank(people.email) < _email_domain_rank(
            profile.email
        ):
            profile.email = people.email
            profile.id = people.email
    if people.alias and not profile.alias:
        profile.alias = people.alias
    if people.position and not profile.position:
        profile.position = people.position


def _best_export_for_token(token: str, lookup: InstructorLookup) -> ExportEntry | None:
    """Resolve exportUsers row, preferring @innopolis.ru / @innopolis.university."""
    candidates: list[ExportEntry] = []
    cleaned = " ".join(token.split())
    if not cleaned:
        return None
    primary = _find_export_entry(cleaned, lookup)
    if primary:
        candidates.append(primary)
    if _has_cyrillic(cleaned):
        for key in _ru_name_lookup_keys(cleaned):
            hit = lookup.export_by_ru.get(key)
            if hit:
                candidates.append(hit)
    for key in _en_name_lookup_keys(cleaned):
        hit = lookup.export_by_en.get(key)
        if hit:
            candidates.append(hit)
    if not candidates:
        return None
    return min(
        candidates, key=lambda entry: (_email_domain_rank(entry.email), entry.email)
    )


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


def _names_refer_to_same_person(
    left: InstructorProfile, right: InstructorProfile
) -> bool:
    if left.email and right.email and left.email == right.email:
        return True

    for left_name in (left.name_en, left.name_ru):
        if not left_name:
            continue
        for right_name in (right.name_en, right.name_ru):
            if not right_name:
                continue
            if normalize_person_name(left_name) == normalize_person_name(right_name):
                return True

            left_ru_keys = set(_ru_name_lookup_keys(left_name))
            right_ru_keys = set(_ru_name_lookup_keys(right_name))
            if left_ru_keys & right_ru_keys:
                return True

            left_en_keys = set(_en_name_lookup_keys(left_name))
            right_en_keys = set(_en_name_lookup_keys(right_name))
            if left_en_keys & right_en_keys:
                return True

            for left_given, left_family in _given_family_candidates(left_name):
                for right_given, right_family in _given_family_candidates(right_name):
                    if not _family_names_compatible(left_family, right_family):
                        continue
                    if _given_names_compatible(left_given, right_given):
                        return True
    return False


def profiles_should_merge(left: InstructorProfile, right: InstructorProfile) -> bool:
    if _names_refer_to_same_person(left, right):
        return True
    left_tokens = _profile_label_tokens(left)
    right_tokens = _profile_label_tokens(right)
    return bool(left_tokens & right_tokens)


def instructor_config_to_profile(instructor: InstructorConfig) -> InstructorProfile:
    return InstructorProfile(
        id=instructor.id,
        name_en=instructor.name_en,
        name_ru=instructor.name_ru,
        email=instructor.email,
        alias=instructor.alias,
        position=instructor.position,
    )


def _prefer_canonical_id(left: InstructorConfig, right: InstructorConfig) -> str:
    left_email = (
        pick_primary_email(left.email or left.id)
        if (left.email or "@" in left.id)
        else None
    )
    right_email = (
        pick_primary_email(right.email or right.id)
        if (right.email or "@" in right.id)
        else None
    )
    if left_email and right_email:
        left_rank = _email_domain_rank(left_email)
        right_rank = _email_domain_rank(right_email)
        if left_rank != right_rank:
            return left.id if left_rank < right_rank else right.id
        return (
            left.id
            if left.email == left.id
            else right.id
            if right.email == right.id
            else left.id
        )
    if left_email and not right_email:
        return left.id
    if right_email and not left_email:
        return right.id
    if left.name_en and not right.name_en:
        return left.id
    if right.name_en and not left.name_en:
        return right.id
    # Prefer ASCII slug ids for stable references in YAML.
    left_ascii = all(ord(ch) < 128 for ch in left.id)
    right_ascii = all(ord(ch) < 128 for ch in right.id)
    if left_ascii and not right_ascii:
        return left.id
    if right_ascii and not left_ascii:
        return right.id
    return left.id


def normalize_instructor_config(instructor: InstructorConfig) -> InstructorConfig:
    """Collapse multi-email id/email cells to a single preferred corporate address."""
    primary = pick_primary_email(instructor.email) or pick_primary_email(instructor.id)
    if primary is None:
        return instructor
    payload = instructor.model_dump(mode="python", exclude_none=True)
    payload["id"] = primary
    payload["email"] = primary
    return InstructorConfig.model_validate(payload)


def collapse_duplicate_instructors(
    instructors_map: dict[str, InstructorConfig],
) -> dict[str, str]:
    """
    Merge cross-script / spelling-variant duplicates in-place.

    Returns a redirect map: every id that ever appeared → surviving canonical id.
    """
    ids = list(instructors_map)
    parent = {instructor_id: instructor_id for instructor_id in ids}

    def find(instructor_id: str) -> str:
        while parent[instructor_id] != instructor_id:
            parent[instructor_id] = parent[parent[instructor_id]]
            instructor_id = parent[instructor_id]
        return instructor_id

    def union(left_id: str, right_id: str) -> None:
        root_left = find(left_id)
        root_right = find(right_id)
        if root_left != root_right:
            parent[root_right] = root_left

    for index, left_id in enumerate(ids):
        left = instructor_config_to_profile(instructors_map[left_id])
        for right_id in ids[index + 1 :]:
            right = instructor_config_to_profile(instructors_map[right_id])
            if left.email and right.email and left.email != right.email:
                # Guest + corporate mailboxes: merge only when names clearly match.
                if _names_refer_to_same_person(left, right):
                    union(left_id, right_id)
                continue
            if profiles_should_merge(left, right):
                union(left_id, right_id)

    groups: dict[str, list[str]] = {}
    for instructor_id in ids:
        groups.setdefault(find(instructor_id), []).append(instructor_id)

    redirect: dict[str, str] = {}
    for members in groups.values():
        if len(members) == 1:
            only_id = members[0]
            redirect[only_id] = only_id
            continue

        keep_id = members[0]
        for member_id in members[1:]:
            keep_id = _prefer_canonical_id(
                instructors_map[keep_id],
                instructors_map[member_id],
            )

        kept = instructor_config_to_profile(instructors_map[keep_id])
        for member_id in members:
            if member_id == keep_id:
                continue
            kept.merge(instructor_config_to_profile(instructors_map[member_id]))
            del instructors_map[member_id]

        new_cfg = kept.to_instructor_config()
        if keep_id in instructors_map:
            del instructors_map[keep_id]
        instructors_map[new_cfg.id] = new_cfg

        for member_id in members:
            redirect[member_id] = new_cfg.id
        redirect[new_cfg.id] = new_cfg.id

    return redirect


def remap_instructor_ref(value: Any, id_map: dict[str, str]) -> Any:
    if isinstance(value, str):
        return id_map.get(value, value)
    if isinstance(value, list):
        remapped = [remap_instructor_ref(item, id_map) for item in value]
        # Preserve unique order for instructor pools / multi-instructor fields.
        if remapped and all(isinstance(item, str) for item in remapped):
            seen: set[str] = set()
            unique: list[str] = []
            for item in remapped:
                if item in seen:
                    continue
                seen.add(item)
                unique.append(item)
            return unique
        return remapped
    return value


def remap_instructor_ids_in_obj(node: Any, id_map: dict[str, str]) -> Any:
    if isinstance(node, dict):
        out: dict[str, Any] = {}
        for key, value in node.items():
            if key in {"instructor", "instructor_pool"}:
                out[key] = remap_instructor_ref(value, id_map)
            else:
                out[key] = remap_instructor_ids_in_obj(value, id_map)
        return out
    if isinstance(node, list):
        return [remap_instructor_ids_in_obj(item, id_map) for item in node]
    return node


_INITIAL_SURNAME = re.compile(
    r"^([A-Za-zА-Яа-яЁё])\.\s+([A-Za-zА-Яа-яЁё][A-Za-zА-Яа-яЁё\-']+)$"
)


def _expand_initial_surname_token(token: str, lookup: InstructorLookup) -> str:
    """Map 'В. Гордин' onto a full People/export display name when unambiguous."""
    match = _INITIAL_SURNAME.match(token)
    if not match:
        return token
    initial, surname = match.group(1), match.group(2)
    initial_cf = initial.casefold()
    surname_cf = surname.casefold()
    hits: list[str] = []

    def consider(name: str | None) -> None:
        if not name:
            return
        parts = name.split()
        if len(parts) < 2:
            return
        if parts[-1].casefold() == surname_cf or parts[0].casefold() == surname_cf:
            given = parts[0] if parts[-1].casefold() == surname_cf else parts[1]
            if given[:1].casefold() == initial_cf:
                hits.append(name)

    for entry in lookup.people.iter_unique():
        consider(entry.name_en)
        consider(entry.name_ru)
    for index in (lookup.export_by_en, lookup.export_by_ru):
        for key, entry in index.items():
            # Compare latinized surname so "Гордин" matches key "gordin …".
            latin_surname = transliterate_ru_to_en(surname).casefold()
            if surname_cf not in key and latin_surname not in key:
                continue
            consider(entry.name_en)
            consider(entry.name_ru)
    # Prefer the People sheet name when several spellings refer to one person.
    unique = list(dict.fromkeys(hits))
    if len(unique) == 1:
        return unique[0]
    if not unique:
        return token
    people_hits = [name for name in unique if lookup.people.find(name) is not None]
    return people_hits[0] if len(people_hits) == 1 else unique[0]


def resolve_instructor_profile(
    token: str, lookup: InstructorLookup
) -> InstructorProfile:
    display_name = " ".join(token.split())
    display_name = _expand_initial_surname_token(display_name, lookup)
    name_en, name_ru = _split_display_name(display_name)
    profile = InstructorProfile(
        id=display_name,
        name_en=name_en or (display_name if not name_ru else None),
        name_ru=name_ru,
    )

    people = lookup.people.find(display_name)
    export = _best_export_for_token(display_name, lookup)
    if people:
        for people_token in (people.name_en, people.name_ru):
            if not people_token:
                continue
            alt = _best_export_for_token(people_token, lookup)
            if alt is None:
                continue
            if export is None or _email_domain_rank(alt.email) < _email_domain_rank(
                export.email
            ):
                export = alt

    if export:
        _apply_export_to_profile(profile, export)
    if people:
        _apply_people_to_profile(profile, people)
    if not profile.email:
        profile.id = to_instructor_id(
            profile.name_en or profile.name_ru or display_name
        )
    return profile


def register_instructor_profile(
    name: str,
    instructors_map: dict[str, InstructorProfile],
    lookup: InstructorLookup,
) -> str:
    profile = resolve_instructor_profile(name, lookup)
    canonical_id = profile.id

    # Fast path: already present by email / id.
    if profile.email and profile.email in instructors_map:
        instructors_map[profile.email].merge(profile)
        profile = instructors_map[profile.email]
        canonical_id = profile.id
    elif canonical_id in instructors_map:
        instructors_map[canonical_id].merge(profile)
        profile = instructors_map[canonical_id]
        canonical_id = profile.id
    else:
        # Only compare against name-only stubs when we have an email; full scan
        # otherwise (small People sheet). Avoid O(n²) over thousands of emails.
        candidates = (
            [
                existing_id
                for existing_id, existing in instructors_map.items()
                if not existing.email
            ]
            if profile.email
            else list(instructors_map)
        )
        for existing_id in candidates:
            if existing_id == canonical_id:
                continue
            if profiles_should_merge(instructors_map[existing_id], profile):
                profile.merge(instructors_map[existing_id])
                del instructors_map[existing_id]

    existing = instructors_map.get(canonical_id)
    if existing and existing is not profile:
        existing.merge(profile)
        profile = existing
    instructors_map[canonical_id] = profile
    # Re-key if merge promoted id to email.
    if profile.id != canonical_id:
        instructors_map.pop(canonical_id, None)
        instructors_map[profile.id] = profile
        canonical_id = profile.id
    return canonical_id


def build_roster_from_lookup(lookup: InstructorLookup) -> list[InstructorConfig]:
    profiles: dict[str, InstructorProfile] = {}
    for entry in lookup.people.iter_unique():
        token = entry.name_en or entry.name_ru or entry.email or ""
        if token:
            register_instructor_profile(token, profiles, lookup)
    for export in lookup.export_by_email.values():
        if not export.include_in_roster:
            continue
        token = export.name_en or export.name_ru or export.email
        if token:
            register_instructor_profile(token, profiles, lookup)
    return [
        profile.to_instructor_config()
        for profile in sorted(
            profiles.values(),
            key=lambda item: item.preferred_name().casefold(),
        )
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
    for email in extract_emails(instructor.email) or extract_emails(instructor.id):
        keys.append(email)
    if instructor.email:
        keys.append(instructor.email.lower())
    if instructor.alias:
        keys.append(instructor.alias.lstrip("@").casefold())
    # Keep legacy multi-email id lookups working until configs are regenerated.
    if "," in instructor.id:
        keys.append(instructor.id.lower())
        keys.extend(extract_emails(instructor.id))
    return keys


@dataclass
class InstructorRegistry:
    by_id: dict[str, InstructorConfig]
    name_index: dict[str, str]
    lookup: InstructorLookup | None = None

    @classmethod
    def from_config(cls, config: InstructorsConfig) -> InstructorRegistry:
        by_id: dict[str, InstructorConfig] = {}
        legacy_redirects: dict[str, str] = {}
        for instructor in config.instructors:
            original_id = instructor.id
            normalized = normalize_instructor_config(instructor)
            existing = by_id.get(normalized.id)
            if existing is None:
                by_id[normalized.id] = normalized
            else:
                profile = instructor_config_to_profile(existing)
                profile.merge(instructor_config_to_profile(normalized))
                by_id[normalized.id] = profile.to_instructor_config()
            if original_id != normalized.id:
                legacy_redirects[original_id] = normalized.id
            for email in extract_emails(original_id) + extract_emails(instructor.email):
                if email != normalized.id:
                    legacy_redirects.setdefault(email, normalized.id)
        name_index: dict[str, str] = {}
        for instructor in by_id.values():
            for key in _lookup_keys_for_instructor(instructor):
                name_index.setdefault(key, instructor.id)
        for legacy_id, canonical_id in legacy_redirects.items():
            name_index.setdefault(legacy_id.lower(), canonical_id)
            name_index.setdefault(legacy_id, canonical_id)
        return cls(by_id=by_id, name_index=name_index)

    @classmethod
    def from_yaml(cls, path: Path) -> InstructorRegistry:
        return cls.from_config(InstructorsConfig.from_yaml(path))

    def resolve_id(self, token: str) -> str:
        cleaned = " ".join(token.split())
        if not cleaned:
            return to_instructor_id(token)
        if self.lookup is not None:
            cleaned = _expand_initial_surname_token(cleaned, self.lookup)
        if "@" in cleaned:
            primary = pick_primary_email(cleaned)
            if primary:
                hit = self.name_index.get(primary) or self.by_id.get(primary)
                if hit:
                    return hit if isinstance(hit, str) else hit.id
            for email in extract_emails(cleaned):
                hit = self.name_index.get(email) or self.by_id.get(email)
                if hit:
                    return hit if isinstance(hit, str) else hit.id
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

    def resolve_into_map(
        self, name: str, instructors_map: dict[str, InstructorConfig]
    ) -> str:
        if self.lookup is not None:
            profile = resolve_instructor_profile(name, self.lookup)
            if profile.email and _email_domain_rank(profile.email) <= 1:
                instructor_id = profile.email
                cfg = profile.to_instructor_config()
                existing = instructors_map.get(instructor_id) or self.by_id.get(
                    instructor_id
                )
                if existing is not None:
                    kept = instructor_config_to_profile(existing)
                    kept.merge(profile)
                    instructors_map[instructor_id] = kept.to_instructor_config()
                else:
                    instructors_map[instructor_id] = cfg
                return instructor_id
        instructor_id = self.resolve_id(name)
        if instructor_id in instructors_map:
            existing = instructors_map[instructor_id]
            display = " ".join(name.split())
            name_en, name_ru = _split_display_name(display)
            if name_en and not existing.name_en:
                existing.name_en = name_en
            if name_ru and (
                not existing.name_ru
                or len(name_ru.split()) > len(existing.name_ru.split())
            ):
                existing.name_ru = name_ru
            return instructor_id

        if instructor_id in self.by_id:
            instructors_map[instructor_id] = self.by_id[instructor_id]
            return instructor_id

        display = " ".join(name.split())
        name_en, name_ru = _split_display_name(display)
        stub = InstructorConfig(
            id=instructor_id,
            name_en=name_en or (display if not name_ru else None),
            name_ru=name_ru,
        )
        # Merge with an already-collected cross-script duplicate if present.
        for existing_id, existing in list(instructors_map.items()):
            left = instructor_config_to_profile(existing)
            right = instructor_config_to_profile(stub)
            if not profiles_should_merge(left, right):
                continue
            keep_id = _prefer_canonical_id(existing, stub)
            kept = instructor_config_to_profile(
                existing if keep_id == existing_id else stub
            )
            kept.merge(
                instructor_config_to_profile(
                    stub if keep_id == existing_id else existing
                )
            )
            new_cfg = kept.to_instructor_config()
            if existing_id in instructors_map:
                del instructors_map[existing_id]
            instructors_map[new_cfg.id] = new_cfg
            return new_cfg.id
        instructors_map[instructor_id] = stub
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
