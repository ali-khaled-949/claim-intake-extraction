# claim-intake-extraction

A document-intake pipeline for medical billing: pull the key fields out of a
billing document, check them with rules that do not trust the extractor, accept
the clean ones automatically, and send the rest to a person. Python, standard
library only.

**This is a demo I built to show how I take an AI extraction prototype to something
dependable.** It contains no OCR engine and no LLM: the input is document text, and
the extractor is a rule-based stand-in behind the interface a model would implement.
All data is synthetic; no real patient information is used.

## What it covers

| Concern | How it is handled |
|---|---|
| Model boundary | Any extractor with `extract(text) -> {field: (value, confidence)}` plugs in. |
| Validation | NPI check digit, date sanity, procedure-code format, and line items must add up to the total, in exact integer cents. |
| Confident but wrong | Rules run regardless of confidence. A test feeds a 0.99-confidence wrong total and it is still caught. |
| Low confidence | A value below the threshold goes to review even when it validates. |
| Human oversight | Review queue with reasons. A reviewer's correction is validated again before acceptance; a reviewer can also confirm or reject. |
| Failure handling | If the extractor crashes or times out, the document is kept and queued for review, never lost. |
| Duplicates | Document id is a content hash; the same document twice is one record. |
| Access control | Roles: reviewer, auditor, admin. Denied attempts are logged with the user. |
| Privacy | The audit log records events and field names, never patient names or member ids; free text is redacted before logging. |
| Reporting | Counts and the automatic-acceptance rate, so the value of the automation can be measured. |

## Run it

```bash
pip install -r requirements.txt
pytest -q      # 18 tests
```

## Layout

`intake/extract.py` extractor interface and rule baseline · `intake/validate.py` rules ·
`intake/pipeline.py` routing, review, roles, audit · `tests/`

## Limits

- No OCR and no LLM: real scanned documents and a real model's error rate are not measured here.
- In-memory storage; a real system needs a database, encryption at rest, and retention rules.
- This is not a HIPAA-compliant system. It shows two habits that matter for one (least access, no patient data in logs).
- One document layout; real payers each format documents differently.
