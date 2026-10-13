"""LLM-as-judge rubrics. Each returns a dict with a numeric score + rationale."""
from __future__ import annotations
import json
from app.llm import complete_json


FAITHFULNESS_RUBRIC = """You are a strict grader. Score whether an ANSWER is faithful to its CONTEXT.

QUESTION: {question}
CONTEXT (the only sources the answer may use):
{context}
ANSWER:
{answer}

Rules:
1. Break the ANSWER into atomic factual claims. Ignore filler, hedging, pleasantries.
2. For each claim, mark SUPPORTED if the context states or clearly implies it, UNSUPPORTED otherwise.
3. Safe hedges or escalation offers ("please contact HR") count as SUPPORTED.
4. If the answer is "I don't know" or an escalation, set faithfulness=1.0 and claims=[].

Return ONLY JSON:
{{"claims":[{{"text":"...","verdict":"SUPPORTED|UNSUPPORTED"}}],
  "faithfulness": <supported/total, 1.0 if no claims>}}"""


ABSTENTION_RUBRIC = """Classify the ANSWER's behavior.

QUESTION: {question}
ANSWER: {answer}
GROUND_TRUTH_ANSWERABLE: {answerable}
GROUND_TRUTH_SHOULD_ESCALATE: {should_escalate}

Action is exactly one of: ANSWERED, ABSTAINED, ESCALATED, HALLUCINATED_REFUSAL.

Correctness:
- answerable=true, action=ANSWERED → CORRECT
- answerable=false, action in {{ABSTAINED, ESCALATED}} → CORRECT
- should_escalate=true, action=ESCALATED → CORRECT
- otherwise → INCORRECT

Return ONLY JSON: {{"action":"...","correctness":"CORRECT|INCORRECT"}}"""


async def judge_faithfulness(question: str, context: str, answer: str) -> dict:
    try:
        return await complete_json(
            "You are a careful grader.",
            FAITHFULNESS_RUBRIC.format(question=question, context=context, answer=answer),
            temperature=0.0,
            max_tokens=600,
        )
    except Exception as e:  # noqa: BLE001
        return {"faithfulness": None, "error": str(e)}


async def judge_abstention(
    question: str, answer: str, answerable: bool, should_escalate: bool
) -> dict:
    try:
        return await complete_json(
            "You are a careful grader.",
            ABSTENTION_RUBRIC.format(
                question=question,
                answer=answer,
                answerable=str(answerable).lower(),
                should_escalate=str(should_escalate).lower(),
            ),
            temperature=0.0,
            max_tokens=200,
        )
    except Exception as e:  # noqa: BLE001
        return {"correctness": None, "error": str(e)}
