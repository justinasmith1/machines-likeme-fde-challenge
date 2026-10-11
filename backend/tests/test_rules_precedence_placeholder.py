import json
from pathlib import Path

import pytest

from app.rules.engine import evaluate
from app.rules.parameters import RuleParameters

GOLDEN = Path(__file__).parent / "golden" / "expected_outcomes.json"


@pytest.mark.xfail(reason="engine not implemented yet", raises=NotImplementedError, strict=True)
def test_rejected_takes_precedence_over_held():
    evaluate(None, None, None, [], RuleParameters())


@pytest.mark.xfail(reason="engine not implemented yet", raises=NotImplementedError, strict=True)
def test_golden_set_outcomes():
    documents = json.loads(GOLDEN.read_text())
    assert documents
    # Real test (extraction fixtures -> engine -> compare to expected_outcome) comes with Phase 2.
    evaluate(None, None, None, [], RuleParameters())
