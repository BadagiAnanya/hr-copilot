from app.agent.nodes import do_guardrails, _extract_citations
from app.types import RetrievedChunk, Trace, UserContext


def _trace(**kw) -> Trace:
    t = Trace.new(UserContext(slack_user_id="U"), "q?")
    for k, v in kw.items():
        setattr(t, k, v)
    return t


def test_abstains_when_nothing_retrieved():
    t = _trace(answer="here's my guess", retrieved=[], tool_calls=[])
    do_guardrails(t)
    assert t.abstained
    assert "route it to HR" in t.answer


def test_pii_scrubbed():
    t = _trace(
        answer="Your Aadhaar is 123412341234 and your card ends 4111 1111 1111 1111.",
        retrieved=[RetrievedChunk(1, "d", "s", "x", 0.9)],
    )
    do_guardrails(t)
    assert "123412341234" not in t.answer
    assert "4111" not in t.answer
    assert "[REDACTED]" in t.answer


def test_only_retrieved_citations_kept():
    t = _trace(
        answer="Sick leave is 12 days [leave_policy_v3#3.1 Sick Leave]. Unicorns are real [hallucinated#1].",
        retrieved=[RetrievedChunk(id=42, document_id="leave_policy_v3",
                                  section="3.1 Sick Leave", content="x", score=0.9)],
    )
    cites = _extract_citations(t)
    assert len(cites) == 1
    assert cites[0].document_id == "leave_policy_v3"
