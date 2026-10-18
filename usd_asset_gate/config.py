"""Load, validate and resolve ``.usd-asset-gate.yml``.

The schema is defined once, here, as a JSON Schema document built from the rule
registry. It is published as ``schema/usd-asset-gate.schema.json`` (regenerate with
``python -m usd_asset_gate.config > schema/usd-asset-gate.schema.json``) and
enforced by a small hand-written validator, so the runtime dependencies stay
``usd-core`` and ``PyYAML``.
"""

from __future__ import annotations

import copy
import json
import os
import re
import sys
from dataclasses import dataclass, field
from typing import Any, Iterable

from .discovery import matches_glob
from .model import FAIL_ON_VALUES, severity_of  # noqa: F401 (re-exported)
from .rules import registry

DEFAULT_CONFIG_FILE = ".usd-asset-gate.yml"
UPSTREAM_RULE_PATTERN = r"^upstream:[^:]+:[^:]+$"
SCHEMA_ID = (
    "https://raw.githubusercontent.com/Preponderous-Software/usd-asset-gate/main/"
    "schema/usd-asset-gate.schema.json"
)


class ConfigError(Exception):
    """The config file is unreadable or invalid; the message names the key."""


# --- schema ------------------------------------------------------------------

_STRINGS = {"type": "array", "items": {"type": "string"}}


def _rules_schema() -> dict:
    return {
        "type": "object",
        "description": "Per-rule settings, keyed by rule ID.",
        "x-unknown-label": "rule",
        "properties": {spec.id: spec.schema() for spec in registry.RULES},
        "patternProperties": {
            UPSTREAM_RULE_PATTERN: {
                "type": "object",
                "description": "One OpenUSD validator, by its registry name.",
                "additionalProperties": False,
                "properties": {
                    "enabled": {"type": "boolean"},
                    "severity": registry.SEVERITY_SCHEMA,
                },
            }
        },
        "additionalProperties": False,
    }


def build_schema() -> dict:
    rules = _rules_schema()
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": SCHEMA_ID,
        "title": "usd-asset-gate configuration (.usd-asset-gate.yml)",
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "version": {"type": "integer", "enum": [1]},
            "include": {
                **_STRINGS,
                "description": "Path globs to check (default: every USD file).",
            },
            "exclude": {**_STRINGS, "description": "Path globs never to check."},
            "fail_on": {"enum": list(FAIL_ON_VALUES)},
            "upstream": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "enabled": {"type": "boolean"},
                    "validators": {
                        **_STRINGS,
                        "description": "Validator names or globs to run (default: all).",
                    },
                    "keywords": {
                        **_STRINGS,
                        "description": "Run validators carrying any of these keywords.",
                    },
                    "disable": {**_STRINGS, "description": "Validator names or globs to skip."},
                },
            },
            "rules": rules,
            "overrides": {
                "type": "array",
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["paths"],
                    "properties": {"paths": _STRINGS, "rules": rules},
                },
            },
        },
    }


# --- validation --------------------------------------------------------------

_TYPES = {
    "object": dict,
    "array": list,
    "string": str,
    "boolean": bool,
    "number": (int, float),
    "integer": int,
}


def _type_ok(value: Any, expected: str) -> bool:
    if expected in ("integer", "number") and isinstance(value, bool):
        return False
    return isinstance(value, _TYPES[expected])


def _type_name(value: Any) -> str:
    if value is None:
        return "null"
    for name, t in (("boolean", bool), ("integer", int), ("number", float), ("string", str),
                    ("array", list), ("object", dict)):
        if isinstance(value, t):
            return name
    return type(value).__name__


def _show(value: Any) -> str:
    """Render a YAML value for an error message; dates and other non-JSON types too."""
    try:
        return json.dumps(value)
    except (TypeError, ValueError):
        return repr(str(value))


def _join(path: str, key: Any) -> str:
    if isinstance(key, int):
        return f"{path}[{key}]"
    return f"{path}.{key}" if path else str(key)


def validate(value: Any, schema: dict, path: str = "") -> None:
    """Raise ``ConfigError`` naming the key path of the first violation."""
    where = path or "(top level)"
    if "anyOf" in schema:
        errors = []
        for option in schema["anyOf"]:
            try:
                validate(value, option, path)
                break
            except ConfigError as exc:
                errors.append(str(exc))
        else:
            # Prefer the error from the option whose type matches the value: for a
            # mapping, "unknown key" is more useful than "expected integer".
            same_type = [
                err
                for option, err in zip(schema["anyOf"], errors)
                if option.get("type") and _type_ok(value, option["type"])
            ]
            if same_type:
                raise ConfigError(same_type[0])
            raise ConfigError(f"{where}: {json.dumps(value)} is not a valid value")
        return
    expected = schema.get("type")
    if expected and not _type_ok(value, expected):
        raise ConfigError(f"{where}: expected {expected}, got {_type_name(value)}")
    if "enum" in schema and value not in schema["enum"]:
        options = ", ".join(_show(v) for v in schema["enum"])
        raise ConfigError(f"{where}: {_show(value)} is not one of {options}")
    if "pattern" in schema and isinstance(value, str) and not re.search(schema["pattern"], value):
        raise ConfigError(f"{where}: {value!r} does not match {schema['pattern']}")
    if (
        "exclusiveMinimum" in schema
        and isinstance(value, (int, float))
        and value <= schema["exclusiveMinimum"]
    ):
        raise ConfigError(f"{where}: must be > {schema['exclusiveMinimum']}")
    if "minimum" in schema and isinstance(value, (int, float)) and value < schema["minimum"]:
        raise ConfigError(f"{where}: must be >= {schema['minimum']}")
    if "maximum" in schema and isinstance(value, (int, float)) and value > schema["maximum"]:
        raise ConfigError(f"{where}: must be <= {schema['maximum']}")
    if isinstance(value, list) and "items" in schema:
        for i, item in enumerate(value):
            validate(item, schema["items"], _join(path, i))
    if isinstance(value, dict):
        for key in schema.get("required", []):
            if key not in value:
                raise ConfigError(f"{_join(path, key)}: required key is missing")
        props = schema.get("properties", {})
        patterns = schema.get("patternProperties", {})
        additional = schema.get("additionalProperties", True)
        for key, item in value.items():
            if not isinstance(key, str):
                raise ConfigError(f"{_join(path, key)}: keys must be strings")
            child = _join(path, key)
            if key in props:
                validate(item, props[key], child)
                continue
            matched = [s for p, s in patterns.items() if re.search(p, key)]
            if matched:
                for s in matched:
                    validate(item, s, child)
                continue
            if additional is False:
                label = schema.get("x-unknown-label", "key")
                raise ConfigError(f"{child}: unknown {label}")
            if isinstance(additional, dict):
                validate(item, additional, child)


# --- resolved configuration --------------------------------------------------


@dataclass
class UpstreamConfig:
    enabled: bool = True
    validators: list[str] = field(default_factory=list)
    keywords: list[str] = field(default_factory=list)
    disable: list[str] = field(default_factory=list)


@dataclass
class Config:
    include: list[str] | None = None
    exclude: list[str] = field(default_factory=list)
    fail_on: str = "error"
    upstream: UpstreamConfig = field(default_factory=UpstreamConfig)
    rules: dict[str, dict] = field(default_factory=dict)
    overrides: list[tuple[list[str], dict[str, dict]]] = field(default_factory=list)
    source: str | None = None  # file the config was read from, if any

    def rule_settings(self, path: str | None = None) -> dict[str, dict]:
        """Effective settings for every configurable rule, for one file.

        Defaults, then ``rules``, then each ``overrides`` entry whose ``paths``
        match ``path``, in file order. Each layer replaces settings key by key
        (one level deep: a whole ``max_by_type`` mapping is replaced, not merged).
        Upstream rules appear only when configured explicitly.
        """
        effective = {spec.id: spec.default_settings() for spec in registry.RULES}
        layers = [self.rules]
        if path is not None:
            layers += [rules for paths, rules in self.overrides if matches_glob(path, paths)]
        for layer in layers:
            for rule_id, settings in layer.items():
                effective.setdefault(rule_id, {}).update(copy.deepcopy(settings))
        return effective


def parse(data: Any, source: str | None = None) -> Config:
    """Validate a parsed YAML document and build a ``Config``."""
    if data is None:
        data = {}
    validate(data, build_schema())
    upstream = data.get("upstream", {})
    return Config(
        include=list(data["include"]) if "include" in data else None,
        exclude=list(data.get("exclude", [])),
        fail_on=data.get("fail_on", "error"),
        upstream=UpstreamConfig(
            enabled=upstream.get("enabled", True),
            validators=list(upstream.get("validators", [])),
            keywords=list(upstream.get("keywords", [])),
            disable=list(upstream.get("disable", [])),
        ),
        rules=copy.deepcopy(data.get("rules", {})),
        overrides=[
            (list(o["paths"]), copy.deepcopy(o.get("rules", {})))
            for o in data.get("overrides", [])
        ],
        source=source,
    )


def load(path: str | None = None) -> Config:
    """Read ``path``, or ``.usd-asset-gate.yml`` in the working directory if present.

    With no file at all the defaults apply.
    """
    import yaml

    if path is None:
        if not os.path.isfile(DEFAULT_CONFIG_FILE):
            return Config()
        path = DEFAULT_CONFIG_FILE
    try:
        with open(path, encoding="utf-8") as fh:
            data = yaml.safe_load(fh)
    except OSError as exc:
        raise ConfigError(f"cannot read config file {path}: {exc.strerror}") from None
    except yaml.YAMLError as exc:
        raise ConfigError(f"{path}: invalid YAML: {exc}") from None
    try:
        return parse(data, source=path)
    except ConfigError as exc:
        raise ConfigError(f"{path}: {exc}") from None


def check_upstream_names(config: Config, available: Iterable[str]) -> None:
    """Every upstream name the config mentions must exist in the installed ``usd-core``.

    Globs (containing ``*``, ``?`` or ``[``) may match nothing; exact names may not.
    """
    available = set(available)

    def is_glob(name: str) -> bool:
        return any(ch in name for ch in "*?[")

    for key in ("validators", "disable"):
        for i, name in enumerate(getattr(config.upstream, key)):
            if not is_glob(name) and name not in available:
                raise ConfigError(
                    f"upstream.{key}[{i}]: unknown upstream validator {name!r} "
                    "(run `usd-asset-gate rules` for the list)"
                )
    rule_maps = [("rules", config.rules)] + [
        (f"overrides[{i}].rules", rules) for i, (_, rules) in enumerate(config.overrides)
    ]
    for prefix, rules in rule_maps:
        for rule_id in rules:
            if rule_id.startswith("upstream:") and rule_id[len("upstream:"):] not in available:
                raise ConfigError(
                    f"{prefix}.{rule_id}: unknown rule (no such upstream validator in the "
                    "installed usd-core)"
                )


if __name__ == "__main__":
    sys.stdout.write(json.dumps(build_schema(), indent=2) + "\n")
