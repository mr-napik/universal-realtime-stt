"""
LLM-based semantic understanding metric for STT accuracy evaluation.

Extracts semantic facts from both the expected and STT transcripts in a single
LLM call, classifies each by verdict (both / expected-only / got-only), and
returns a CustomMetricResult with a numeric score and a human-readable detail
string for the HTML report.

See doc/llm_understanding_metric.md for full documentation.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from logging import getLogger

from helpers.diff import CustomMetricResult
from helpers.llm_api import LLMBasicClient

logger = getLogger(__name__)

_MODEL_ID = "gemini-3-pro-preview"

_SYSTEM_PROMPT = """\
You are evaluating speech-to-text accuracy for Czech audio transcription.

Your task: given an expected (ground-truth) transcript and an STT output, extract
semantic facts from both and classify each fact.

A fact is a simple statement: subject + predicate + object.
Focus on: named entities, events, quotes, attributions, and statements of fact.
Ignore punctuation, word-order differences, and morphological variation (Czech inflection).

Classify each fact with a verdict:
- "both"     — the fact is present in both the expected and the STT output
- "expected" — the fact is only in the expected text (information lost by STT)
- "got"      — the fact is only in the STT output (possibly hallucinated or added)

Return JSON only, no markdown, using this exact schema:
{
  "facts": [
    {"subject": "...", "predicate": "...", "object": "...", "verdict": "both|expected|got"}
  ]
}
"""

_PROMPT_TEMPLATE = """\
Expected transcript:
{expected}

STT output:
{got}
"""


class Verdict(str, Enum):
    BOTH = "both"          # fact in both expected and got
    EXPECTED = "expected"  # fact only in expected (missing from STT)
    GOT = "got"            # fact only in got (not in expected)


@dataclass(frozen=True)
class SemanticFact:
    subject: str
    predicate: str
    object: str
    verdict: Verdict


def _build_detail(facts: list[SemanticFact]) -> str:
    """Format facts grouped by verdict into a readable detail string."""
    groups = {
        Verdict.BOTH: [],
        Verdict.EXPECTED: [],
        Verdict.GOT: [],
    }
    for f in facts:
        groups[f.verdict].append(f)

    lines: list[str] = []
    labels = {
        Verdict.BOTH: "Preserved (both)",
        Verdict.EXPECTED: "Missing from STT (expected only)",
        Verdict.GOT: "Extra in STT (got only)",
    }
    for verdict, label in labels.items():
        group = groups[verdict]
        if not group:
            continue
        lines.append(f"{label}:")
        for f in group:
            lines.append(f"  • {f.subject} {f.predicate} {f.object}")
    return "\n".join(lines) if lines else "No facts extracted."


class LLMUnderstandingAnalyzer:
    """
    Semantic understanding metric using Gemini.

    Usage:
        analyzer = LLMUnderstandingAnalyzer(api_key=os.getenv("GEMINI_API_KEY"))
        result = await analyzer.compare(expected_text, got_text)
        # result.score  → float 0–100
        # result.detail → formatted fact list
    """

    def __init__(self, api_key: str) -> None:
        self._llm = LLMBasicClient(api_key=api_key, model_id=_MODEL_ID, max_tokens=16000)

    async def compare(self, text_expected: str, text_got: str) -> CustomMetricResult:
        """
        Extract and classify semantic facts from both texts in a single LLM call.

        Score = facts_both / (facts_both + facts_expected_only) * 100
        GOT-only facts appear in detail but don't affect the score denominator.
        """
        prompt = _PROMPT_TEMPLATE.format(expected=text_expected, got=text_got)
        raw = await self._llm.call_llm(prompt, _SYSTEM_PROMPT, max_retries=1)

        facts = [
            SemanticFact(
                subject=item["subject"],
                predicate=item["predicate"],
                object=item["object"],
                verdict=Verdict(item["verdict"]),
            )
            for item in raw.get("facts", [])
        ]

        facts_both = sum(1 for f in facts if f.verdict is Verdict.BOTH)
        facts_expected = sum(1 for f in facts if f.verdict is Verdict.EXPECTED)
        denominator = facts_both + facts_expected
        score = round(facts_both / denominator * 100, 1) if denominator > 0 else 0.0

        logger.info(
            "LLM understanding: score=%.1f%% (%d both, %d expected-only, %d got-only)",
            score, facts_both, facts_expected,
            sum(1 for f in facts if f.verdict is Verdict.GOT),
        )

        return CustomMetricResult(score=score, detail=_build_detail(facts))
