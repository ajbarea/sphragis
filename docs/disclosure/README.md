# Contamination disclosure

`contamination-disclosure.json` is this study's record in the Contamination Disclosure v1.1
format (Angulo, Yeste and Espinos-Morato, "Benchmark Contamination: A Taxonomy Organized by
Defeated Mitigation", arXiv:2608.29463). It describes the sealed test windows and the
checkpoint evaluated on them; the research log entry of 2026-10-20 gives its reasons.

`disclosure.schema.json` is the authors' schema, copied unchanged from
https://github.com/Jangulo7/contamination-disclosure-paper at commit
3d0dee015b24196e2879770b3ffadd271ff9b74c (`templates/disclosure.schema.json`), under
CC BY 4.0 (https://creativecommons.org/licenses/by/4.0/). `tests/unit/test_contamination_disclosure.py`
validates the record against it and checks its checkpoint against `MODEL_REVISIONS`.
