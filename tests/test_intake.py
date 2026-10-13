from datetime import date

import pytest

from intake.extract import RuleExtractor
from intake.pipeline import Intake, NotAllowed, redact
from intake.validate import cents, npi_is_valid, validate

TODAY = date(2026, 6, 1)

# Synthetic document. 1234567893 is the well-known example NPI with a valid check digit.
GOOD = """EXPLANATION OF BENEFITS
Claim #: CLM-20481
Patient Name: Jordan Sample
Member ID: XYZ123456
Provider NPI: 1234567893
Date of Service: 03/14/2026

99213  Office visit, established patient   $150.00
85025  Complete blood count                 $45.50
Total Billed: $195.50
"""


def intake(extractor=None):
    return Intake(extractor or RuleExtractor(), today=TODAY)


# --- validators ---------------------------------------------------------------

def test_npi_check_digit():
    assert npi_is_valid("1234567893")
    assert not npi_is_valid("1234567890")      # one digit off
    assert not npi_is_valid("123456789")       # too short
    assert not npi_is_valid(None)


def test_amounts_are_exact_integer_cents():
    assert cents("1,234.50") == 123450 and cents("0.07") == 7
    assert cents("150.00") + cents("45.50") == cents("195.50")


# --- extraction and automatic acceptance --------------------------------------

def test_clean_document_is_extracted_and_accepted():
    doc = intake().submit(GOOD)
    assert doc.status == "accepted" and doc.reasons == []
    assert doc.fields["claim_id"][0] == "CLM-20481"
    assert [l["cpt"] for l in doc.fields["lines"][0]] == ["99213", "85025"]


@pytest.mark.parametrize("change,reason", [
    (("1234567893", "1234567890"), "provider_npi: fails the NPI check digit"),
    (("$195.50", "$200.00"), "total_billed: lines add up to 195.50, document says 200.00"),
    (("03/14/2026", "03/14/2027"), "service_date: is in the future"),
    (("03/14/2026", "14th March"), "service_date: missing"),
    (("Member ID: XYZ123456\n", ""), "member_id: missing"),
])
def test_each_kind_of_bad_document_goes_to_review_with_a_reason(change, reason):
    doc = intake().submit(GOOD.replace(*change))
    assert doc.status == "needs_review" and reason in doc.reasons


def test_low_confidence_guess_is_reviewed_even_when_it_validates():
    # The NPI label is missing, so the extractor guesses from a bare 10-digit number.
    doc = intake().submit(GOOD.replace("Provider NPI: 1234567893", "Provider 1234567893"))
    assert doc.fields["provider_npi"] == ("1234567893", 0.60)
    assert doc.reasons == ["provider_npi: low confidence (0.60)"]


def test_confidently_wrong_model_is_still_caught_by_the_rules():
    class Overconfident:
        def extract(self, text):
            fields = RuleExtractor().extract(text)
            fields["total_billed"] = ("999.99", 0.99)
            return fields

    doc = intake(Overconfident()).submit(GOOD)
    assert doc.status == "needs_review" and "total_billed" in doc.reasons[0]


def test_extractor_crash_keeps_the_document_for_review():
    class Broken:
        def extract(self, text):
            raise TimeoutError("model timed out")

    doc = intake(Broken()).submit(GOOD)
    assert doc.status == "needs_review" and doc.reasons == ["extractor failed: TimeoutError"]


def test_same_document_twice_is_one_record():
    system = intake()
    assert system.submit(GOOD) is system.submit(GOOD)
    assert system.stats()["total"] == 1


# --- human review -------------------------------------------------------------

def test_reviewer_correction_is_revalidated_then_accepted():
    system = intake()
    doc = system.submit(GOOD.replace("$195.50", "$200.00"))
    still_wrong = system.review(doc.doc_id, "reviewer", "dana", {"total_billed": "190.00"})
    assert still_wrong.status == "needs_review"                 # a person can be wrong too
    fixed = system.review(doc.doc_id, "reviewer", "dana", {"total_billed": "195.50"})
    assert fixed.status == "accepted" and system.review_queue() == []


def test_reviewer_can_confirm_a_low_confidence_value_or_reject():
    system = intake()
    a = system.submit(GOOD.replace("Provider NPI: 1234567893", "Provider 1234567893"))
    assert system.review(a.doc_id, "reviewer", "dana").status == "accepted"
    b = system.submit(GOOD.replace("CLM-20481", "CLM-99999").replace("1234567893", "1234567890"))
    assert system.review(b.doc_id, "reviewer", "dana", reject=True).status == "rejected"
    with pytest.raises(ValueError):
        system.review(b.doc_id, "reviewer", "dana", reject=True)


def test_only_review_roles_can_review_and_denials_are_logged():
    system = intake()
    doc = system.submit(GOOD.replace("$195.50", "$200.00"))
    with pytest.raises(NotAllowed):
        system.review(doc.doc_id, "auditor", "sam", {"total_billed": "195.50"})
    assert doc.status == "needs_review"
    assert system.audit[-1]["event"] == "review_denied" and system.audit[-1]["actor"] == "sam"
    with pytest.raises(NotAllowed):
        system.read_audit("reviewer")


# --- privacy and reporting ----------------------------------------------------

def test_audit_log_never_contains_patient_identifiers():
    system = intake()
    doc = system.submit(GOOD.replace("$195.50", "$200.00"))
    system.review(doc.doc_id, "admin", "dana", {"patient_name": "Jordan Q. Sample", "total_billed": "195.50"})
    log = str(system.read_audit("auditor"))
    assert "Jordan" not in log and "XYZ123456" not in log
    assert "patient_name" in log                      # the field name is logged, the value is not


def test_redact_removes_name_and_member_id_from_free_text():
    cleaned = redact(GOOD)
    assert "Jordan" not in cleaned and "XYZ123456" not in cleaned and "CLM-20481" in cleaned


def test_stats_report_how_much_was_automatic():
    system = intake()
    system.submit(GOOD)
    bad = system.submit(GOOD.replace("CLM-20481", "CLM-2").replace("$195.50", "$200.00"))
    system.review(bad.doc_id, "reviewer", "dana", {"total_billed": "195.50", "claim_id": "CLM-20482"})
    assert system.stats() == {"total": 2, "auto_accepted": 1, "needs_review": 0, "accepted_after_review": 1,
                              "rejected": 0, "auto_rate": 0.5}
