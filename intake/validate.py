"""Checks that do not depend on the model: formats, check digits, and arithmetic.

A model can be confidently wrong. These rules catch the errors that matter in
billing regardless of how sure the extractor says it is.
"""
import re
from datetime import date, datetime


def cents(amount):
    """'1,234.50' -> 123450. Integer cents so sums are exact."""
    whole, _, frac = amount.replace(",", "").partition(".")
    return int(whole) * 100 + int((frac + "00")[:2])


def npi_is_valid(npi):
    """NPI check digit: Luhn over the number prefixed with 80840 (the US health-plan prefix)."""
    if not re.fullmatch(r"\d{10}", npi or ""):
        return False
    total = 0
    for i, ch in enumerate(reversed("80840" + npi)):
        d = int(ch)
        if i % 2 == 1:
            d = d * 2 - 9 if d * 2 > 9 else d * 2
        total += d
    return total % 10 == 0


def parse_date(value):
    for fmt in ("%m/%d/%Y", "%Y-%m-%d", "%m-%d-%Y"):
        try:
            return datetime.strptime(value, fmt).date()
        except ValueError:
            pass
    return None


def validate(fields, today=None):
    """Return a list of (field, problem). Empty means every rule passed."""
    today = today or date.today()
    problems = []
    value = {k: v[0] for k, v in fields.items()}

    for required in ("claim_id", "patient_name", "member_id", "provider_npi", "service_date", "total_billed", "lines"):
        if required not in value:
            problems.append((required, "missing"))

    if "provider_npi" in value and not npi_is_valid(value["provider_npi"]):
        problems.append(("provider_npi", "fails the NPI check digit"))

    if "service_date" in value:
        d = parse_date(value["service_date"])
        if d is None:
            problems.append(("service_date", "not a recognisable date"))
        elif d > today:
            problems.append(("service_date", "is in the future"))

    for line in value.get("lines", []):
        if not re.fullmatch(r"\d{5}", line["cpt"]):
            problems.append(("lines", f"bad procedure code {line['cpt']!r}"))

    if "total_billed" in value and value.get("lines"):
        line_sum = sum(cents(l["amount"]) for l in value["lines"])
        if line_sum != cents(value["total_billed"]):
            problems.append(("total_billed", f"lines add up to {line_sum / 100:.2f}, document says "
                                             f"{cents(value['total_billed']) / 100:.2f}"))
    return problems
