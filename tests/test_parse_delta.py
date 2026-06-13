"""parse_delta tolerance — the JSON extraction that absorbs the failure
modes small local models actually produce. Schema validation is NOT here
(that's merge_delta); this only asserts a dict comes back out."""

import json

import pytest

from perdura import parse_delta

CANON = {"new_nodes": [{"ref": "a", "type": "claim", "text": "x"}],
         "new_edges": []}


def test_plain_json():
    assert parse_delta(json.dumps(CANON)) == CANON


def test_strips_think_block():
    raw = "<think>let me reason about this</think>" + json.dumps(CANON)
    assert parse_delta(raw) == CANON


def test_unclosed_think_block():
    # qwen3 sometimes never closes the tag before the JSON
    raw = "<think>reasoning runs into\n" + json.dumps(CANON)
    assert parse_delta(raw) == CANON


def test_strips_markdown_fences():
    raw = "```json\n" + json.dumps(CANON) + "\n```"
    assert parse_delta(raw) == CANON


def test_surrounding_prose():
    raw = "Sure! Here is the delta:\n" + json.dumps(CANON) + "\nHope that helps."
    assert parse_delta(raw) == CANON


def test_trailing_comma_repair():
    raw = '{"new_nodes": [{"type": "claim", "text": "x"},], "new_edges": [],}'
    out = parse_delta(raw)
    assert out["new_nodes"][0]["text"] == "x"


def test_no_json_raises():
    with pytest.raises(ValueError):
        parse_delta("I refuse to answer in JSON.")


def test_braces_inside_strings_are_safe():
    # a brace inside a string value must not confuse the extractor
    obj = {"new_nodes": [{"type": "claim", "text": "use {curly} braces"}]}
    assert parse_delta("prose " + json.dumps(obj))["new_nodes"][0]["text"] \
        == "use {curly} braces"
