"""The hand-written schema validator, upstream-name checks and settings layering.

``test_config.py`` drives these through the CLI and the real schema; here each
keyword of ``config.validate`` is exercised on a small schema of its own, so a
regression names the keyword rather than a config file.
"""

from __future__ import annotations

import math

import pytest

from usd_asset_gate import config as cfg
from usd_asset_gate.config import Config, ConfigError, UpstreamConfig


def error(value, schema, path=""):
    with pytest.raises(ConfigError) as exc:
        cfg.validate(value, schema, path)
    return str(exc.value)


# --- types ---------------------------------------------------------------------


@pytest.mark.parametrize("expected", ["integer", "number"])
def test_booleans_are_not_numbers(expected):
    assert error(True, {"type": expected}) == f"(top level): expected {expected}, got boolean"


def test_integer_is_a_number_but_float_is_not_an_integer():
    cfg.validate(3, {"type": "number"})
    assert error(1.0, {"type": "integer"}, "n") == "n: expected integer, got number"


def test_null_is_named_null():
    assert error(None, {"type": "string"}, "s") == "s: expected string, got null"


@pytest.mark.parametrize("value", [math.inf, -math.inf, math.nan])
def test_non_finite_numbers_are_rejected(value):
    assert error(value, {"type": "number"}, "x") == f"x: must be a finite number, got {value}"


# --- enum, pattern, bounds -----------------------------------------------------


def test_enum_lists_the_options_as_json():
    assert error("b", {"enum": ["a", 1]}, "k") == 'k: "b" is not one of "a", 1'


def test_pattern_is_searched_not_fully_matched():
    schema = {"type": "string", "pattern": "x"}
    cfg.validate("axb", schema)
    assert error("abc", schema, "p") == "p: 'abc' does not match x"


@pytest.mark.parametrize(
    "schema, ok, bad, message",
    [
        ({"exclusiveMinimum": 0}, 0.5, 0, "v: must be > 0"),
        ({"minimum": 0}, 0, -1, "v: must be >= 0"),
        ({"maximum": 10}, 10, 11, "v: must be <= 10"),
    ],
)
def test_numeric_bounds_at_the_boundary(schema, ok, bad, message):
    cfg.validate(ok, schema, "v")
    assert error(bad, schema, "v") == message


# --- arrays and objects --------------------------------------------------------


def test_array_items_are_named_by_index():
    schema = {"type": "array", "items": {"type": "string"}}
    assert error(["a", 1], schema, "include") == "include[1]: expected string, got integer"
    assert error([1], schema) == "[0]: expected string, got integer"


def test_non_string_keys_are_rejected():
    assert error({1: "x"}, {"type": "object"}, "rules") == "rules[1]: keys must be strings"


def test_unknown_keys_use_the_schema_label():
    closed = {"type": "object", "additionalProperties": False}
    assert error({"nope": 1}, closed, "upstream") == "upstream.nope: unknown key"
    labelled = {**closed, "x-unknown-label": "rule"}
    assert error({"nope": 1}, labelled, "rules") == "rules.nope: unknown rule"


def test_additional_properties_default_to_allowed():
    cfg.validate({"anything": [1, 2]}, {"type": "object"})


def test_additional_properties_schema_applies_to_every_extra_key():
    schema = {"type": "object", "additionalProperties": {"type": "integer", "minimum": 0}}
    cfg.validate({"Mesh": 0, "Xform": 3}, schema)
    assert error({"Mesh": -1}, schema, "max_by_type") == "max_by_type.Mesh: must be >= 0"


def test_pattern_properties_validate_matching_keys():
    schema = {
        "type": "object",
        "additionalProperties": False,
        "patternProperties": {"^up:": {"type": "boolean"}},
    }
    cfg.validate({"up:a": True}, schema)
    assert error({"up:a": "yes"}, schema, "r") == "r.up:a: expected boolean, got string"
    assert error({"down:a": True}, schema, "r") == "r.down:a: unknown key"


def test_named_properties_take_precedence_over_patterns():
    schema = {
        "type": "object",
        "properties": {"up:a": {"type": "string"}},
        "patternProperties": {"^up:": {"type": "boolean"}},
    }
    cfg.validate({"up:a": "text"}, schema)


# --- anyOf ---------------------------------------------------------------------

ANY_OF = {"anyOf": [{"type": "integer"}, {"type": "object", "additionalProperties": False}]}


def test_any_of_accepts_any_option():
    cfg.validate(3, ANY_OF)
    cfg.validate({}, ANY_OF)


def test_any_of_reports_the_error_of_the_option_with_the_same_type():
    assert error({"x": 1}, ANY_OF, "k") == "k.x: unknown key"


def test_any_of_without_a_same_type_option_reports_the_value():
    assert error("s", ANY_OF, "k") == 'k: "s" is not a valid value'


# --- check_upstream_names ------------------------------------------------------

AVAILABLE = ["v:A", "v:B"]


@pytest.mark.parametrize("key", ["validators", "disable"])
def test_unknown_exact_upstream_name_is_named_by_index(key):
    config = Config(upstream=UpstreamConfig(**{key: ["v:A", "v:Nope"]}))
    with pytest.raises(ConfigError, match=rf"^upstream\.{key}\[1\]: unknown upstream validator 'v:Nope'"):
        cfg.check_upstream_names(config, AVAILABLE)


def test_globs_may_match_nothing():
    config = Config(upstream=UpstreamConfig(validators=["x:*", "v:?"], disable=["[z]:C"]))
    cfg.check_upstream_names(config, AVAILABLE)


def test_upstream_rule_keys_must_name_an_installed_validator():
    cfg.check_upstream_names(Config(rules={"upstream:v:A": {}, "default-prim": {}}), AVAILABLE)
    with pytest.raises(ConfigError, match=r"^rules\.upstream:v:Nope: unknown rule"):
        cfg.check_upstream_names(Config(rules={"upstream:v:Nope": {}}), AVAILABLE)


def test_upstream_rule_keys_in_overrides_are_named_by_index():
    config = Config(overrides=[(["a/**"], {"upstream:v:A": {}}), (["b/**"], {"upstream:v:Nope": {}})])
    with pytest.raises(ConfigError, match=r"^overrides\[1\]\.rules\.upstream:v:Nope: unknown rule"):
        cfg.check_upstream_names(config, AVAILABLE)


# --- parse, load and rule_settings ---------------------------------------------


def test_include_absent_is_none_but_empty_is_kept():
    assert cfg.parse({}).include is None
    assert cfg.parse({"include": []}).include == []


def test_parse_copies_rule_settings():
    data = {"rules": {"prim-budget": {"max_by_type": {"Mesh": 5}}}}
    config = cfg.parse(data)
    data["rules"]["prim-budget"]["max_by_type"]["Mesh"] = 99
    assert config.rules["prim-budget"]["max_by_type"] == {"Mesh": 5}


def test_load_prefixes_errors_with_the_file(tmp_path):
    path = tmp_path / "gate.yml"
    path.write_text("colour: red\n")
    with pytest.raises(ConfigError) as exc:
        cfg.load(str(path))
    assert str(exc.value) == f"{path}: colour: unknown key"


def test_overrides_replace_nested_mappings_rather_than_merge():
    config = Config(
        rules={"prim-budget": {"max_by_type": {"Mesh": 5}}},
        overrides=[(["big/**"], {"prim-budget": {"max_by_type": {"Xform": 2}}})],
    )
    assert config.rule_settings("big/a.usda")["prim-budget"] == {
        "enabled": True,
        "severity": "warning",
        "max_by_type": {"Xform": 2},
    }
    assert config.rule_settings("small/a.usda")["prim-budget"]["max_by_type"] == {"Mesh": 5}


def test_no_path_ignores_overrides():
    config = Config(overrides=[(["**"], {"default-prim": {"enabled": False}})])
    assert config.rule_settings()["default-prim"]["enabled"] is True
    assert config.rule_settings("a.usda")["default-prim"]["enabled"] is False


def test_upstream_rules_appear_only_when_configured():
    assert not any(k.startswith("upstream:") for k in Config().rule_settings())
    config = Config(rules={"upstream:v:A": {"severity": "warning"}})
    assert config.rule_settings()["upstream:v:A"] == {"severity": "warning"}


def test_rule_settings_returns_copies():
    config = Config(rules={"prim-budget": {"max_by_type": {"Mesh": 5}}})
    config.rule_settings()["prim-budget"]["max_by_type"]["Mesh"] = 99
    assert config.rules["prim-budget"]["max_by_type"] == {"Mesh": 5}
