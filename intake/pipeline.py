"""Intake pipeline: extract, validate, then accept or send to human review.

Nothing is auto-accepted unless every rule passes and every field is above the
confidence threshold. A reviewer's correction is validated again before it is
accepted, and every step is written to an audit log that never contains patient data.
"""
import hashlib
import re
from dataclasses import dataclass, field

from .validate import validate

PHI_FIELDS = {"patient_name", "member_id"}
ROLES = {"reviewer": {"review"}, "auditor": {"read_audit"}, "admin": {"review", "read_audit"}}


class NotAllowed(Exception):
    pass


def redact(text):
    """Remove patient identifiers from free text before it is logged."""
    text = re.sub(r"(patient(?:\s*name)?\s*[:\-]\s*)[^\n]+", r"\1[REDACTED]", text, flags=re.I)
    text = re.sub(r"(member\s*id\s*[:\-]\s*)\S+", r"\1[REDACTED]", text, flags=re.I)
    return text


@dataclass
class Document:
    doc_id: str
    status: str                      # accepted | needs_review | rejected
    fields: dict
    reasons: list = field(default_factory=list)


class Intake:
    def __init__(self, extractor, min_confidence=0.85, today=None):
        self.extractor = extractor
        self.min_confidence = min_confidence
        self.today = today
        self.documents = {}
        self.audit = []

    def _log(self, doc_id, event, detail="", actor="system"):
        self.audit.append({"doc": doc_id, "event": event, "actor": actor, "detail": redact(str(detail))})

    def _reasons(self, fields):
        reasons = [f"{name}: {problem}" for name, problem in validate(fields, self.today)]
        reasons += [f"{name}: low confidence ({conf:.2f})" for name, (_, conf) in sorted(fields.items())
                    if conf < self.min_confidence]
        return reasons

    def submit(self, text):
        # The id is a hash of the content, so the same document sent twice is one record.
        doc_id = hashlib.sha256(text.encode()).hexdigest()[:12]
        if doc_id in self.documents:
            self._log(doc_id, "duplicate_ignored")
            return self.documents[doc_id]
        try:
            fields = self.extractor.extract(text)
        except Exception as e:  # a failing model must not lose the document
            fields, reasons = {}, [f"extractor failed: {type(e).__name__}"]
        else:
            reasons = self._reasons(fields)
        doc = Document(doc_id, "needs_review" if reasons else "accepted", fields, reasons)
        self.documents[doc_id] = doc
        self._log(doc_id, doc.status, "; ".join(reasons))
        return doc

    def review_queue(self):
        return [d for d in self.documents.values() if d.status == "needs_review"]

    def review(self, doc_id, role, user, corrections=None, reject=False):
        """A person fixes or rejects a document. Their corrections are trusted as read (confidence 1.0) but still validated."""
        if "review" not in ROLES.get(role, set()):
            self._log(doc_id, "review_denied", f"role={role}", actor=user)
            raise NotAllowed(f"role {role!r} cannot review documents")
        doc = self.documents[doc_id]
        if doc.status != "needs_review":
            raise ValueError(f"document is {doc.status}, not waiting for review")
        if reject:
            doc.status, doc.reasons = "rejected", ["rejected by reviewer"]
            self._log(doc_id, "rejected", actor=user)
            return doc
        for name, value in (corrections or {}).items():
            doc.fields[name] = (value, 1.0)
        # Confirming without a correction means the reviewer vouches for the low-confidence values.
        doc.fields = {k: (v, 1.0) for k, (v, _) in doc.fields.items()}
        doc.reasons = self._reasons(doc.fields)
        doc.status = "needs_review" if doc.reasons else "accepted"
        changed = sorted(corrections or {})
        self._log(doc_id, f"reviewed_{doc.status}", f"fields changed: {changed}", actor=user)
        return doc

    def read_audit(self, role):
        if "read_audit" not in ROLES.get(role, set()):
            raise NotAllowed(f"role {role!r} cannot read the audit log")
        return list(self.audit)

    def stats(self):
        docs = list(self.documents.values())
        auto = sum(1 for e in self.audit if e["event"] == "accepted")
        return {"total": len(docs), "auto_accepted": auto,
                "needs_review": sum(d.status == "needs_review" for d in docs),
                "accepted_after_review": sum(1 for e in self.audit if e["event"] == "reviewed_accepted"),
                "rejected": sum(d.status == "rejected" for d in docs),
                "auto_rate": auto / len(docs) if docs else 0.0}
