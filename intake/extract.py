"""Field extraction from the text of a billing document (the output of OCR).

`Extractor` is the seam for a model: anything with `extract(text) -> dict` of
field name to (value, confidence). `RuleExtractor` is a deterministic baseline
so the pipeline can be tested without a model or an OCR engine.
"""
import re

FIELDS = ("claim_id", "patient_name", "member_id", "provider_npi", "service_date", "total_billed", "lines")

_LINE = re.compile(r"^\s*(\d{5})\s+(.+?)\s+\$?([\d,]+\.\d{2})\s*$", re.M)


def _find(pattern, text):
    m = re.search(pattern, text, re.I | re.M)
    return m.group(1).strip() if m else None


class RuleExtractor:
    """Reads labelled fields. Confidence is 0.95 when a label matched cleanly, lower when it had to guess."""

    def extract(self, text):
        out = {}

        def put(name, value, confidence):
            if value is not None:
                out[name] = (value, confidence)

        put("claim_id", _find(r"claim\s*(?:#|no\.?|number)\s*[:\-]?\s*([A-Z0-9\-]{4,})", text), 0.95)
        put("patient_name", _find(r"patient(?:\s*name)?\s*[:\-]\s*([A-Za-z ,.'\-]{3,60})$", text), 0.95)
        put("member_id", _find(r"member\s*id\s*[:\-]\s*([A-Z0-9]{6,})", text), 0.95)
        put("service_date", _find(r"(?:date of service|dos)\s*[:\-]\s*([\d/\-]{8,10})", text), 0.95)

        npi = _find(r"npi\s*[:\-#]?\s*(\d{10})\b", text)
        if npi is None:
            # No label: fall back to any standalone 10-digit number, and say we are less sure.
            loose = re.findall(r"(?<!\d)(\d{10})(?!\d)", text)
            if len(loose) == 1:
                put("provider_npi", loose[0], 0.60)
        else:
            put("provider_npi", npi, 0.95)

        total = _find(r"total\s*(?:billed|charges?|amount)?\s*[:\-]\s*\$?([\d,]+\.\d{2})", text)
        put("total_billed", total, 0.95)

        lines = [{"cpt": c, "description": d.strip(), "amount": a} for c, d, a in _LINE.findall(text)]
        if lines:
            out["lines"] = (lines, 0.90)
        return out
