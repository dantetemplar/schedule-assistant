from generate_cases import (
    _build_selector_map,
    _filter_courses,
    _section_by_code,
    _section_group_codes,
    _students_groups_without_kind,
)


def test_section_group_codes_traverses_programs_and_tracks() -> None:
    cfg = {
        "term": {
            "sections": [
                {
                    "code": "english",
                    "programs": [
                        {
                            "code": "ENGLISH",
                            "groups": ["DIRECT"],
                            "tracks": [
                                {
                                    "code": "AWA",
                                    "name": "AWA-I",
                                    "groups": ["TRACK"],
                                }
                            ],
                        }
                    ],
                }
            ]
        },
        "students_groups": [
            {"code": "DIRECT"},
            {"code": "TRACK"},
            {"code": "ORPHAN"},
        ],
    }

    english_section = _section_by_code(cfg, "english")

    assert _section_group_codes(english_section) == {"DIRECT", "TRACK"}


def test_filter_courses_uses_section_hierarchy_without_group_kind() -> None:
    cfg = {
        "sections": [
            {
                "code": "core",
                "programs": [
                    {
                        "code": "BS_Y1",
                        "tracks": [
                            {
                                "code": "SE",
                                "name": "Software Engineering",
                                "groups": ["B26-SE-01"],
                            }
                        ],
                    }
                ],
            }
        ],
        "students_groups": [{"code": "B26-SE-01"}],
        "courses": [
            {
                "name": "Core course",
                "components": [
                    {"tag": "lec", "audience": ["@BS_Y1/Software Engineering"]}
                ],
            }
        ],
    }

    courses = _filter_courses(
        cfg,
        _build_selector_map(cfg),
        _section_group_codes(_section_by_code(cfg, "core")),
    )

    assert courses[0]["components"][0]["audience"] == ["B26-SE-01"]


def test_generated_cases_drop_legacy_student_group_kind() -> None:
    cfg = {
        "students_groups": [
            {"code": "CORE", "kind": "core"},
            {"code": "ENGLISH", "kind": "english", "students": ["student"]},
        ]
    }

    assert _students_groups_without_kind(cfg) == [
        {"code": "CORE"},
        {"code": "ENGLISH", "students": ["student"]},
    ]
