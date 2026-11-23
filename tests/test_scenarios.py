"""Every demo scenario must produce its expected decision, for the expected reason."""
import pytest

from app import suite
from app.scenarios import SCENARIOS


@pytest.fixture(scope="module", autouse=True)
def documents():
    suite.ensure_documents()


def checks(result, severity):
    return {f["check"] for f in result["findings"] if f["severity"] == severity}


@pytest.mark.parametrize("key", list(SCENARIOS))
def test_expected_status(key):
    out = suite.run_scenario(key)
    assert out["first"]["status"] == SCENARIOS[key]["expect"]
    if SCENARIOS[key].get("expect_after_fix"):
        assert out["after_fix"]["status"] == SCENARIOS[key]["expect_after_fix"]


def test_clean_has_no_issues():
    r = suite.run_scenario("clean")["first"]
    assert not checks(r, "fix") and not checks(r, "block") and not checks(r, "note")


def test_state_mismatch_is_the_only_problem():
    r = suite.run_scenario("state_mismatch")["first"]
    assert checks(r, "fix") == {"C9"}
    assert not checks(r, "block")


def test_wrong_document_is_caught_and_explained():
    r = suite.run_scenario("wrong_document")["first"]
    assert "C3" in checks(r, "fix")
    assert any(a["kind"] == "document" and a["slot"] == "bank_proof" for a in r["asks"])
    assert "invoice" in r["vendor_message"]["text"].lower()


def test_bank_reuse_blocks_and_stays_quiet():
    r = suite.run_scenario("bank_reuse")["first"]
    assert {"C14", "C15"} <= checks(r, "block")
    message = r["vendor_message"]["text"].lower()
    assert "kumar" not in message and "bank" not in message   # the vendor is not told why


def test_name_variants_are_not_flagged():
    r = suite.run_scenario("name_variants")["first"]
    assert not checks(r, "fix") and not checks(r, "block")


def test_documents_only_builds_the_form():
    r = suite.run_scenario("documents_only")["first"]
    assert r["submission"]["company"]["legal_name"] == "Konkan Coastal Foods Private Limited"
    assert r["submission"]["tax"]["gstin"].startswith("27")
    assert len(r["autofilled"]) >= 10


def test_same_input_same_output():
    a = suite.run_scenario("state_mismatch")["first"]
    b = suite.run_scenario("state_mismatch")["first"]
    assert [f["title"] for f in a["findings"]] == [f["title"] for f in b["findings"]]
