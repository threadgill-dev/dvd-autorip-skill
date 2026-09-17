#!/usr/bin/env python3
"""Validate config/config.local.json against the schema documented in
references/config-schema.md.

This script never prompts and never contains real values -- it only checks a file
that Claude (or the user) already wrote. Prompting for missing values is a
conversational step handled in SKILL.md's Stage 1, not here.

Usage:
    python validate_config.py <path-to-config.local.json>

Exit code 0 + prints "OK" if valid. Exit code 1 + prints one line per problem if not.
"""

import json
import sys

ALWAYS_REQUIRED_FIELDS = [
    ("library", "movies_path"),
    ("library", "shows_path"),
]

# Only required when media_server.type is "jellyfin" (the default when the field is
# missing entirely -- see config-schema.md). Under "none" these are never checked,
# even if present with placeholder values left over from an earlier jellyfin setup.
JELLYFIN_ONLY_REQUIRED_FIELDS = [
    ("jellyfin", "base_url"),
    ("jellyfin", "api_key"),
]

MEDIA_SERVER_TYPE_VALUES = {"jellyfin", "none"}

PLACEHOLDER_MARKER = "REPLACE_ME"

BONUS_CONTENT_HANDLING_VALUES = {"discard", "keep", "ask"}


def validate(config: dict) -> list[str]:
    problems = []

    media_server = config.get("media_server")
    media_server_type = "jellyfin"
    if media_server is not None:
        if not isinstance(media_server, dict):
            problems.append(f"'media_server' should be an object, got {type(media_server).__name__}")
        else:
            media_server_type = media_server.get("type", "jellyfin")
            if media_server_type not in MEDIA_SERVER_TYPE_VALUES:
                problems.append(
                    f"'media_server.type' is '{media_server_type}', must be one of: "
                    f"{', '.join(sorted(MEDIA_SERVER_TYPE_VALUES))}"
                )

    required_fields = list(ALWAYS_REQUIRED_FIELDS)
    if media_server_type == "jellyfin":
        required_fields += JELLYFIN_ONLY_REQUIRED_FIELDS

    for section, field in required_fields:
        value = config.get(section, {})
        if not isinstance(value, dict):
            problems.append(f"'{section}' should be an object, got {type(value).__name__}")
            continue
        field_value = value.get(field)
        if not field_value or not isinstance(field_value, str):
            problems.append(f"'{section}.{field}' is missing or empty")
        elif PLACEHOLDER_MARKER in field_value:
            problems.append(
                f"'{section}.{field}' still contains the example placeholder "
                f"({PLACEHOLDER_MARKER}) -- it was never actually filled in"
            )

    # bonus_content is optional (missing == "ask"/remember_choice: false, the safe
    # default -- see config-schema.md), but if present, catch a typo'd handling value
    # early rather than letting it silently misbehave mid-run.
    bonus_content = config.get("bonus_content")
    if bonus_content is not None:
        if not isinstance(bonus_content, dict):
            problems.append(f"'bonus_content' should be an object, got {type(bonus_content).__name__}")
        else:
            handling = bonus_content.get("handling", "ask")
            if handling not in BONUS_CONTENT_HANDLING_VALUES:
                problems.append(
                    f"'bonus_content.handling' is '{handling}', must be one of: "
                    f"{', '.join(sorted(BONUS_CONTENT_HANDLING_VALUES))}"
                )
            remember_choice = bonus_content.get("remember_choice", False)
            if not isinstance(remember_choice, bool):
                problems.append("'bonus_content.remember_choice' must be true or false")

    # setup.declined_optional_installs is optional (missing == [], nothing declined
    # yet), but if present should be a real list of key strings, not something Stage 1
    # would choke on when checking membership.
    setup = config.get("setup")
    if setup is not None:
        if not isinstance(setup, dict):
            problems.append(f"'setup' should be an object, got {type(setup).__name__}")
        else:
            declined = setup.get("declined_optional_installs", [])
            if not isinstance(declined, list) or not all(isinstance(k, str) for k in declined):
                problems.append("'setup.declined_optional_installs' must be an array of strings")

            model_effort_ack = setup.get("model_effort_ack", False)
            if not isinstance(model_effort_ack, bool):
                problems.append("'setup.model_effort_ack' must be true or false")

    return problems


def main() -> int:
    if len(sys.argv) != 2:
        print("Usage: validate_config.py <path-to-config.local.json>", file=sys.stderr)
        return 1

    path = sys.argv[1]
    try:
        with open(path, "r", encoding="utf-8") as f:
            config = json.load(f)
    except FileNotFoundError:
        print(f"MISSING: {path} does not exist yet -- first-run setup is needed")
        return 1
    except json.JSONDecodeError as e:
        print(f"INVALID: {path} is not valid JSON ({e})")
        return 1

    problems = validate(config)
    if problems:
        for p in problems:
            print(f"PROBLEM: {p}")
        return 1

    print("OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
