"""U6 contract evidence: all model calls are fake and keys are test-only."""

from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path
from typing import Any

import pytest
from folio_eval.grade import GraderVote
from folio_eval.leakcheck import ScryptParams, build_manifest
from folio_eval.recall_llm_ceiling import collect, render_prompt, write_report
from folio_eval.resolve_labels import IndexedConcept, LabelIndex
from folio_eval.verifier_collect import RunnerReply


def events(names: Any, tool: bool = False) -> RunnerReply:
    rows: list[dict[str, Any]] = [{"type": "thread.started", "model": "fake"}]
    if tool:
        rows.append({"type": "item.completed", "item": {"type": "command_execution"}})
    rows += [
        {"type": "item.completed", "item": {"type": "agent_message", "text": json.dumps(names)}},
        {"type": "turn.completed"},
    ]
    return RunnerReply("\n".join(map(json.dumps, rows)))


class Fake:
    def __init__(self, names: Any = None, failures: int = 0, error: Any = None) -> None:
        self.names = names if names is not None else ["  alternate—name  "]
        self.failures = failures
        self.error = error
        self.calls = 0

    def __call__(self, prompt: str, cwd: Path) -> RunnerReply:
        assert cwd.is_dir() and not list(cwd.iterdir())
        assert not cwd.is_relative_to(Path.cwd())
        assert "http" not in prompt and "urn:" not in prompt
        assert "alternate" not in prompt
        self.calls += 1
        if self.error:
            raise self.error
        return events(self.names, self.calls <= self.failures)


@pytest.fixture
def inputs(tmp_path: Path) -> dict[str, Any]:
    dictionary = LabelIndex.from_concepts(
        [
            IndexedConcept("urn:gold", ("Gold",), ("Alternate-name",)),
            IndexedConcept("urn:other", ("Other", "Shared"), ()),
            IndexedConcept("urn:third", ("Third", "Shared"), ()),
        ]
    )
    ids = [f"i{i:02}" for i in range(20)]
    relations = [
        {
            "item_id": item,
            "iri": "urn:gold",
            "stage": "never_produced",
            "agreement": 2 if n % 2 == 0 else 3,
        }
        for n, item in enumerate(ids)
    ]
    path = tmp_path / "u2.json"
    path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "relations": relations,
                "fingerprint": {
                    "corpus_content_sha256": "a" * 64,
                    "ontology_cache_sha256": "b" * 64,
                },
            }
        )
    )
    votes = [
        GraderVote(item, str(i), family, {"Gold": 0.9 if i < 2 or n % 2 else 0.59}, "generator")
        for n, item in enumerate(ids)
        for i, family in enumerate(("codex", "codex", "claude"))
    ]
    salt = b"fake-test-salt"
    manifest = build_manifest(
        ["private company"],
        salt,
        gold_version="test",
        gold_content_sha256="a" * 64,
        scrypt_params=ScryptParams(n=16, r=1, p=1, dklen=16, test_params=True),
    )
    embedding = tmp_path / "embedding.json"
    embedding.write_text(
        json.dumps(
            {
                "attribution_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                "rankings": {
                    arm: {"50": {"recovered_count": 0}}
                    for arm in ("whole_passage", "sentence_windows")
                },
                "passages": {
                    item: {
                        "never_produced_count": 1,
                        **{
                            arm: {"100": {"recovered_count": 0}}
                            for arm in ("whole_passage", "sentence_windows")
                        },
                    }
                    for item in ids
                },
            }
        )
    )
    return dict(
        embedding=embedding,
        embedding_sha256=hashlib.sha256(embedding.read_bytes()).hexdigest(),
        corpus_content_sha256="a" * 64,
        ontology_cache_sha256="b" * 64,
        item_ids=set(ids),
        attribution=path,
        attribution_sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
        passages={item: "Public legal passage." for item in ids},
        dictionary=dictionary,
        votes=votes,
        checkpoint=tmp_path / "cp",
        runner_identity="fake",
        manifest=manifest,
        salt=salt,
    )


def test_alternative_recovery_and_majority_split(inputs: dict[str, Any]) -> None:
    result = collect(**inputs, runner=Fake())
    assert result is not None
    assert result["recovered"] == 20
    assert result["recovered_codex_only_majority"] == 10
    assert result["recovered_claude_included_majority"] == 10
    assert result["upper_bound"] == 1 and result["publishable"] == 1
    assert result["attribution_sha256"] == inputs["attribution_sha256"]


def test_ambiguous_unmatched_non_gold_and_duplicates(inputs: dict[str, Any]) -> None:
    result = collect(**inputs, runner=Fake(["Shared", "Unknown", "Other", "Gold", "Gold"]))
    assert result is not None
    assert result["ambiguous"] == result["unmatched"] == result["non_gold_proposals"] == 20
    assert result["recovered"] == 20 and result["duplicate_names"] == 20
    assert result["proposals"] == 80
    assert result["proposals"] == sum(
        result[k] for k in ("ambiguous", "unmatched", "matched_gold", "non_gold_proposals")
    )
    assert all(row["non_gold_proposals"] == 1 for row in result["per_item"].values())


def test_tool_event_three_retries_and_failure_threshold(inputs: dict[str, Any]) -> None:
    fake = Fake(failures=9)
    result = collect(**inputs, runner=fake)
    assert result is not None
    assert fake.calls == 26
    assert result["failed"] == 3 and result["publishable"] == 0
    assert result["failure_histogram"] == {"tool_event": 9}
    assert result["recovered"] == 17


def test_exactly_ten_percent_can_publish(inputs: dict[str, Any]) -> None:
    result = collect(**inputs, runner=Fake(failures=6))
    assert result is not None
    assert result["failed"] == 2 and result["publishable"] == 1


def test_retry_then_resume_retains_failure_evidence(inputs: dict[str, Any]) -> None:
    fake = Fake(failures=1)
    assert collect(**inputs, runner=fake, limit=2) is None
    assert fake.calls == 3
    resumed = Fake()
    result = collect(**inputs, runner=resumed)
    assert result is not None
    assert resumed.calls == 18 and result["failure_histogram"] == {"tool_event": 1}
    untouched = Fake()
    assert collect(**inputs, runner=untouched) == result and untouched.calls == 0
    stored = "\n".join(p.read_text() for p in inputs["checkpoint"].glob("*.json"))
    assert "Public legal passage" not in stored and "Alternate-name" not in stored


@pytest.mark.parametrize(
    "surface", ["https://folio.openlegalstandard.org/X", "LMSS.SALI.ORG/X", "urn:gold"]
)
def test_prompt_refuses_iris(inputs: dict[str, Any], surface: Any) -> None:
    with pytest.raises(ValueError, match="IRI"):
        render_prompt(surface, inputs["dictionary"])


def test_prompt_passage_only_and_neutralizes_links(inputs: dict[str, Any]) -> None:
    prompt = render_prompt("Public law https://example.org/reference", inputs["dictionary"])
    assert "[link]" in prompt and "http" not in prompt
    assert "Gold" not in prompt and "urn:" not in prompt
    assert "legal concepts" in prompt


@pytest.mark.parametrize("names", ["prose", {}, [4], [""], ["Gold", None]])
def test_bad_response_typed_failures(inputs: dict[str, Any], names: Any) -> None:
    result = collect(**inputs, runner=Fake(names))
    assert result is not None
    assert result["failed"] == 20
    assert result["failure_histogram"] == {"invalid_response": 60}


@pytest.mark.parametrize(
    "error,reason",
    [
        (subprocess.TimeoutExpired("fake", 1), "timeout"),
        (subprocess.CalledProcessError(1, "fake"), "nonzero_exit"),
        (OSError("private company"), "os_error"),
    ],
)
def test_runner_errors_are_sanitized(inputs: dict[str, Any], error: Any, reason: Any) -> None:
    result = collect(**inputs, runner=Fake(error=error))
    assert result is not None
    assert result["failure_histogram"] == {reason: 60}
    assert "private company" not in json.dumps(result)


def test_sha_binding_before_runner(inputs: dict[str, Any]) -> None:
    inputs["attribution_sha256"] = "0" * 64
    fake = Fake()
    with pytest.raises(ValueError, match="SHA-256"):
        collect(**inputs, runner=fake)
    assert fake.calls == 0 and not inputs["checkpoint"].exists()


def test_changed_inputs_and_corrupt_checkpoint_refused(inputs: dict[str, Any]) -> None:
    collect(**inputs, runner=Fake(), limit=1)
    with pytest.raises(ValueError, match="fingerprint"):
        collect(**{**inputs, "runner_identity": "changed"}, runner=Fake())
    path = next(p for p in inputs["checkpoint"].glob("*.json") if p.name != "manifest.json")
    payload = json.loads(path.read_text())
    payload["metrics"]["recovered"] += 1
    path.write_text(json.dumps(payload))
    with pytest.raises(ValueError, match="checksum"):
        collect(**inputs, runner=Fake())


def test_missing_majority_or_unknown_selection_fails_before_runner(inputs: dict[str, Any]) -> None:
    fake = Fake()
    with pytest.raises(ValueError, match="majority"):
        collect(**{**inputs, "votes": inputs["votes"][:1]}, runner=fake)
    with pytest.raises(ValueError, match="selected"):
        collect(**{**inputs, "item_ids": {"unknown"}}, runner=fake)
    assert fake.calls == 0


def test_write_leak_scan_before_replace(inputs: dict[str, Any], tmp_path: Path) -> None:
    report = collect(**inputs, runner=Fake())
    assert report is not None
    out = tmp_path / "report.json"
    write_report(out, report, inputs["manifest"], inputs["salt"])
    before = out.read_bytes()
    report["per_item"]["private company"] = report["per_item"].pop("i00")
    with pytest.raises(ValueError, match="collision"):
        write_report(out, report, inputs["manifest"], inputs["salt"])
    assert out.read_bytes() == before


def test_numeric_report_rejects_prose(inputs: dict[str, Any], tmp_path: Path) -> None:
    report = collect(**inputs, runner=Fake())
    assert report is not None
    report["explanation"] = "untrusted model prose"
    with pytest.raises(ValueError, match="numeric"):
        write_report(tmp_path / "bad.json", report, inputs["manifest"], inputs["salt"])
    assert not (tmp_path / "bad.json").exists()


def test_failure_reason_cannot_store_runner_prose(inputs: dict[str, Any]) -> None:
    from folio_eval.verifier_collect import AttemptRejected

    report = collect(**inputs, runner=Fake(error=AttemptRejected("untrusted model prose")))
    assert report is not None
    assert report["failure_histogram"] == {"invalid_response": 60}


def test_lemma_matching_and_alias_deduplication(inputs: dict[str, Any]) -> None:
    report = collect(**inputs, runner=Fake(["Golds", "Gold", "Alternate-name"]))
    assert report is not None
    assert report["unmatched"] == 0 and report["recovered"] == 20
    assert report["duplicate_concepts"] == 40 and report["proposals"] == 60


def test_empty_selection_never_calls_runner(inputs: dict[str, Any]) -> None:
    fake = Fake()
    with pytest.raises(ValueError, match="residual"):
        collect(**{**inputs, "item_ids": set()}, runner=fake)
    assert fake.calls == 0


def test_cli_pinned_owl_limit_resume_and_numeric_artifact(
    inputs: dict[str, Any], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import folio_eval.recall_llm_ceiling as module
    import run_recall_llm_ceiling as launcher
    from folio_eval.synthesize import CorpusManifest, LoadedCorpus, SyntheticItem

    owl = tmp_path / "ontology.owl"
    owl.write_text("""<rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#"
      xmlns:rdfs="http://www.w3.org/2000/01/rdf-schema#"
      xmlns:skos="http://www.w3.org/2004/02/skos/core#">
      <rdf:Description rdf:about="urn:gold"><rdfs:label>Gold</rdfs:label>
        <skos:prefLabel>Preferred Gold</skos:prefLabel>
        <skos:altLabel>Alternate-name</skos:altLabel></rdf:Description>
    </rdf:RDF>""")
    digest = hashlib.sha256(owl.read_bytes()).hexdigest()
    grading_dictionary = LabelIndex.from_concepts(
        [IndexedConcept("urn:gold", ("Gold", "Preferred Gold"), ("Alternate-name",))]
    )
    monkeypatch.setattr(module, "load_folio_index", lambda: (grading_dictionary, digest, "test"))
    dictionary = module.load_dictionary(owl, digest)
    assert dictionary is grading_dictionary
    assert dictionary.norm_preferred["preferred gold"] == ["urn:gold"]
    assert dictionary.norm_alternative["alternate-name"] == ["urn:gold"]
    with pytest.raises(ValueError, match="SHA-256"):
        module.load_dictionary(owl, "0" * 64)
    manifest = CorpusManifest(
        version=1,
        content_sha256="a" * 64,
        nomatch_content_sha256="b" * 64,
        ontology_cache_sha256=digest,
        answer_rule_config_sha256="d" * 64,
        item_counts={"scoreable": 20, "nomatch": 0},
        non_lexical_fraction=1.0,
        non_lexical_floor=0.3,
        scoreable=True,
        seed=7,
        created="test",
        manifest_path=Path("unused.json"),
    )
    items = tuple(
        SyntheticItem(
            item_id=item,
            doc_type="motion",
            jurisdiction="US",
            text=inputs["passages"][item],
            gold_iris=frozenset({"urn:gold"}),
            verification="deterministic",
            provenance={
                "grader_votes": [v.to_json() for v in inputs["votes"] if v.item_id == item]
            },
        )
        for item in sorted(inputs["item_ids"])
    )
    monkeypatch.setattr(module, "load_corpus", lambda _: LoadedCorpus(manifest, items, ()))
    monkeypatch.setattr(module, "load_manifest", lambda _: inputs["manifest"])
    ids = tmp_path / "ids.json"
    ids.write_text(json.dumps(sorted(inputs["item_ids"])))
    salt = tmp_path / "salt"
    salt.write_bytes(inputs["salt"])
    output = tmp_path / "out.json"
    attribution = json.loads(inputs["attribution"].read_text())
    attribution["fingerprint"]["ontology_cache_sha256"] = digest
    inputs["attribution"].write_text(json.dumps(attribution))
    inputs["attribution_sha256"] = hashlib.sha256(inputs["attribution"].read_bytes()).hexdigest()
    local = json.loads(inputs["embedding"].read_text())
    local["attribution_sha256"] = inputs["attribution_sha256"]
    inputs["embedding"].write_text(json.dumps(local))
    inputs["embedding_sha256"] = hashlib.sha256(inputs["embedding"].read_bytes()).hexdigest()
    args = [
        "--embedding",
        str(inputs["embedding"]),
        "--embedding-sha256",
        inputs["embedding_sha256"],
        "--item-ids",
        str(ids),
        "--attribution",
        str(inputs["attribution"]),
        "--attribution-sha256",
        inputs["attribution_sha256"],
        "--model",
        "fake",
        "--checkpoint",
        str(inputs["checkpoint"]),
        "--salt-file",
        str(salt),
        "--ontology-cache",
        str(owl),
        "--out",
        str(output),
    ]
    assert launcher.main([*args, "--limit", "1"], runner=Fake()) == 0
    assert not output.exists()
    fake = Fake()
    assert launcher.main(args, runner=fake) == 0 and fake.calls == 19
    report = json.loads(output.read_text())
    assert report["recovered"] == 20
    assert "Public legal passage" not in output.read_text()
    assert "Alternate-name" not in output.read_text()

    # Same IDs and votes, different text and validated corpus digest.
    from dataclasses import replace

    changed = LoadedCorpus(
        replace(manifest, content_sha256="c" * 64),
        tuple(replace(item, text="Changed public passage.") for item in items),
        (),
    )
    monkeypatch.setattr(module, "load_corpus", lambda _: changed)

    def forbidden_prompt(*args):
        pytest.fail("prompt constructed for a changed corpus")

    monkeypatch.setattr(module, "render_prompt", forbidden_prompt)
    fake = Fake()
    with pytest.raises(ValueError, match="fingerprint"):
        launcher.main(args, runner=fake)
    assert fake.calls == 0


def test_review_stale_empty_residual_refused(inputs):
    fake = Fake()
    with pytest.raises(ValueError, match="residual"):
        collect(**{**inputs, "item_ids": set()}, runner=fake)
    assert fake.calls == 0


def test_review_changed_corpus_refused_before_prompts(inputs, monkeypatch):
    import folio_eval.recall_llm_ceiling as module

    def forbidden(*args):
        pytest.fail("prompt constructed before provenance validation")

    monkeypatch.setattr(module, "render_prompt", forbidden)
    with pytest.raises(ValueError, match="fingerprint"):
        collect(
            **{**inputs, "corpus_content_sha256": "changed", "ontology_cache_sha256": "b" * 64},
            runner=Fake(),
        )


def test_review_llm_heading_collision_precedes_prompts(inputs, monkeypatch):
    import folio_eval.recall_llm_ceiling as module

    def forbidden(*args):
        pytest.fail("prompt constructed before preflight")

    monkeypatch.setattr(module, "render_prompt", forbidden)
    inputs["manifest"] = build_manifest(
        ["Distance past rank 200"],
        inputs["salt"],
        gold_version="fake",
        gold_content_sha256="a" * 64,
    )
    fake = Fake()
    with pytest.raises(ValueError, match="leak check"):
        collect(**inputs, runner=fake)
    assert fake.calls == 0


def test_review_no_free_form_residual_needed(inputs):
    del inputs["item_ids"]
    result = collect(**inputs, runner=Fake())
    assert result["item_count"] == 20
    assert result["embedding_sha256"] == inputs["embedding_sha256"]


def test_review_empty_bound_residual_is_valid(inputs):
    local = json.loads(inputs["embedding"].read_text())
    for row in local["passages"].values():
        row["whole_passage"]["100"]["recovered_count"] = 1
    inputs["embedding"].write_text(json.dumps(local))
    inputs["embedding_sha256"] = hashlib.sha256(inputs["embedding"].read_bytes()).hexdigest()
    inputs["item_ids"] = set()
    fake = Fake()
    result = collect(**inputs, runner=fake)
    assert result["item_count"] == 0 and fake.calls == 0


def test_grading_resolver_recovers_alternative_plural(inputs):
    result = collect(**inputs, runner=Fake(["Alternate-names"]))
    assert result is not None
    assert result["recovered"] == 20
    assert result["mapping_method"] == "grade.resolve_gold_value"
    write_report(inputs["checkpoint"] / "report.json", result, inputs["manifest"], inputs["salt"])


def test_dictionary_rejects_different_grading_ontology(tmp_path, monkeypatch):
    from folio_eval import recall_llm_ceiling as module

    path = tmp_path / "ontology.owl"
    path.write_bytes(b"pinned bytes")
    monkeypatch.setattr(module, "load_folio_index", lambda: (LabelIndex(), "0" * 64, "test"))
    with pytest.raises(ValueError, match="grading ontology SHA-256"):
        module.load_dictionary(path, hashlib.sha256(path.read_bytes()).hexdigest())


def test_grading_preferred_label_precedence(inputs):
    from folio_eval.recall_llm_ceiling import _metrics

    dictionary = LabelIndex.from_concepts(
        [
            IndexedConcept("urn:gold", ("Shared",), ()),
            IndexedConcept("urn:other", ("Other",), ("Shared",)),
        ]
    )
    result = _metrics('["Shared"]', dictionary, {"urn:gold"}, {"urn:gold": "codex_only"})
    assert result["recovered"] == 1
    assert result["ambiguous"] == 0
