"""Pronunciation: respelling rules, the /api/pronunciation routes, and synthesis applying them.

Matching cases and the terms/units fixture come from qwen3-tts-openai, where the feature
started, so both projects read the same files the same way.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from app.pronunciation import PronRule, apply_pronunciation

FIXTURE = Path(__file__).parent / "fixtures" / "tts_pronunciation.json"


# --- matching -------------------------------------------------------------------------------


def test_whole_word_off_touches_substring() -> None:
    rules = [PronRule(match="nginx", replace="engine X")]
    assert (
        apply_pronunciation("edit nginx.conf for nginx", rules) == "edit engine X.conf for engine X"
    )


def test_whole_word_on_leaves_substring() -> None:
    rules = [PronRule(match="nginx", replace="engine X", whole_word=True)]
    assert apply_pronunciation("edit nginx.conf for nginx", rules) == "edit nginx.conf for engine X"


def test_whole_word_edges_are_latin_only_so_cjk_text_matches() -> None:
    rules = [PronRule(match="nginx", replace="エンジンエックス", whole_word=True)]
    assert apply_pronunciation("nginxを使う", rules) == "エンジンエックスを使う"


def test_case_insensitive_default_and_case_sensitive() -> None:
    loose = [PronRule(match="nginx", replace="engine X")]
    assert apply_pronunciation("NGINX and Nginx", loose) == "engine X and engine X"
    strict = [PronRule(match="nginx", replace="engine X", case_sensitive=True)]
    assert apply_pronunciation("nginx NGINX Nginx", strict) == "engine X NGINX Nginx"


def test_units_only_after_a_number() -> None:
    rules = [PronRule(match="GB", replace="gigabytes", after_number=True)]
    assert apply_pronunciation("24GB and 24 GB, not GB", rules) == (
        "24 gigabytes and 24 gigabytes, not GB"
    )


def test_longest_match_wins_and_output_is_never_rewritten() -> None:
    rules = [
        PronRule(match="vLLM", replace="V L L M", whole_word=True),
        PronRule(match="vLLM-Omni", replace="V L L M Omni", whole_word=True),
        PronRule(match="k8s", replace="Kubernetes"),
        PronRule(match="Kubernetes", replace="kube"),
    ]
    assert apply_pronunciation("vLLM-Omni and vLLM on k8s", rules) == (
        "V L L M Omni and V L L M on Kubernetes"
    )


def test_replacement_and_match_are_literal() -> None:
    assert apply_pronunciation("foo", [PronRule(match="foo", replace=r"a\b\1$0")]) == r"a\b\1$0"
    assert apply_pronunciation("c++ now", [PronRule(match="c++", replace="cpp")]) == "cpp now"


# --- API ------------------------------------------------------------------------------------


def _rules(client: Any) -> list[dict[str, Any]]:
    return client.get("/api/pronunciation").json()["rules"]


def _rewrite(client: Any, text: str) -> str:
    return client.post("/api/pronunciation/test", json={"text": text}).json()["text"]


def test_put_get_test_and_persist(client: Any, settings: Any) -> None:
    assert _rules(client) == []
    resp = client.put(
        "/api/pronunciation",
        json={"rules": [{"match": "nginx", "replace": "engine X", "whole_word": True}]},
    )
    assert resp.status_code == 200
    assert _rules(client)[0]["replace"] == "engine X"
    assert _rewrite(client, "run nginx now") == "run engine X now"
    saved = json.loads((settings.data_dir / "pronunciation.json").read_text("utf-8"))
    assert saved["rules"][0]["match"] == "nginx"  # survives a restart


def test_put_rejects_an_empty_match(client: Any) -> None:
    resp = client.put("/api/pronunciation", json={"rules": [{"match": "", "replace": "x"}]})
    assert resp.status_code == 400
    assert resp.json()["error"]["code"] == "invalid_pronunciation"


def test_import_terms_units_file(client: Any) -> None:
    data = json.loads(FIXTURE.read_text("utf-8"))
    resp = client.post("/api/pronunciation/import", json={"mode": "replace", "rules": data})
    assert resp.status_code == 200, resp.text
    assert resp.json()["summary"]["total"] == 119 + 16
    out = _rewrite(
        client,
        "Running vLLM-Omni on 2 GPUs with 24GB VRAM at 300 W, e.g. Qwen on k8s. "
        "Spin up the MoE model, see llama.cpp, etc.",
    )
    assert out == (
        "Running V L L M Omni on 2 G P Us with 24 gigabytes V RAM at 300 watts, for example "
        "Chwen on Kubernetes. Spin up the mixture of experts model, see llama C P P, et cetera"
    )
    assert _rewrite(client, "the W key and moe") == "the W key and moe"


def test_export_round_trips(client: Any) -> None:
    data = json.loads(FIXTURE.read_text("utf-8"))
    client.post("/api/pronunciation/import", json={"mode": "replace", "rules": data})
    exported = client.get("/api/pronunciation/export").json()
    assert len(exported["terms"]) == 119 and len(exported["units"]) == 16
    assert {"match": "MoE", "say": "mixture of experts", "case_sensitive": True} in exported[
        "terms"
    ]
    before = _rules(client)
    resp = client.post("/api/pronunciation/import", json={"mode": "replace", "rules": exported})
    assert resp.status_code == 200 and _rules(client) == before


def test_import_merge_updates_and_appends(client: Any) -> None:
    client.put(
        "/api/pronunciation",
        json={
            "rules": [
                {"match": "nginx", "replace": "engine x", "whole_word": True},
                {"match": "k8s", "replace": "kubernetes"},
            ]
        },
    )
    file = {"terms": [{"match": "NGINX", "say": "engine ex"}, {"match": "sql", "say": "sequel"}]}
    resp = client.post("/api/pronunciation/import", json={"mode": "merge", "rules": file})
    assert resp.status_code == 200, resp.text
    assert resp.json()["summary"] == {
        "added": 1,
        "updated": 1,
        "removed": 0,
        "total": 3,
        "in_file": 2,
    }
    assert [(r["match"], r["replace"]) for r in _rules(client)] == [
        ("NGINX", "engine ex"),
        ("k8s", "kubernetes"),
        ("sql", "sequel"),
    ]


def test_import_replace_and_dry_run(client: Any) -> None:
    client.put("/api/pronunciation", json={"rules": [{"match": "a", "replace": "b"}]})
    dry = client.post(
        "/api/pronunciation/import",
        json={"mode": "replace", "dry_run": True, "rules": [{"match": "x", "say": "y"}]},
    )
    assert dry.json()["summary"] == {
        "added": 1,
        "updated": 0,
        "removed": 1,
        "total": 1,
        "in_file": 1,
    }
    assert [r["match"] for r in _rules(client)] == ["a"]  # dry run saved nothing
    client.post(
        "/api/pronunciation/import", json={"mode": "replace", "rules": [{"match": "x", "say": "y"}]}
    )
    assert [r["match"] for r in _rules(client)] == ["x"]


def test_import_accepts_qwen3_voices_json(client: Any) -> None:
    old = {
        "pronunciation": {
            "en": [{"match": "AI", "replace": "A I"}],
            "ko": [{"match": "AI", "replace": "에이아이"}],
        }
    }
    resp = client.post("/api/pronunciation/import", json={"mode": "merge", "rules": old})
    assert resp.status_code == 200
    assert [(r["match"], r["replace"]) for r in _rules(client)] == [("AI", "A I")]


@pytest.mark.parametrize(
    ("body", "needle"),
    [
        ({"mode": "merge", "rules": {"terms": [{"match": "", "say": "b"}]}}, "match is empty"),
        ({"mode": "merge", "rules": {"terms": [{"say": "b"}]}}, "terms[0].match"),
        ({"mode": "merge", "rules": {"terms": [], "words": []}}, "unknown section"),
        ({"mode": "merge", "rules": "nope"}, "must hold"),
    ],
)
def test_import_rejects_bad_files(client: Any, body: dict[str, Any], needle: str) -> None:
    resp = client.post("/api/pronunciation/import", json=body)
    assert resp.status_code == 400 and needle in resp.json()["error"]["message"], resp.text


# --- synthesis ------------------------------------------------------------------------------


def test_speech_sends_the_respelled_text(client: Any, library: Any, fake_engine: Any) -> None:
    client.put(
        "/api/pronunciation",
        json={"rules": [{"match": "nginx", "replace": "engine X", "whole_word": True}]},
    )
    body = {"model": "voxcpm2", "voice": "ashley-en", "input": "restart nginx now"}
    assert client.post("/v1/audio/speech", json=body).status_code == 200
    assert fake_engine.state["last"]["input"] == "restart engine X now"
    styled = {**body, "instructions": "calm"}
    assert client.post("/api/speak", json=styled).status_code == 200
    assert fake_engine.state["last"]["input"] == "(calm)restart engine X now"
    take = client.get("/api/takes").json()["takes"][0]
    assert take["text"] == "restart nginx now"  # the take keeps what was typed
