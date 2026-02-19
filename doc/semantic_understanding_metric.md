# Semantic Understanding Metric

## Overview

CER and WER measure surface-level accuracy — every missing word ending, dropped conjunction, or transposed letter counts against the score. For Czech (and other highly inflected languages), this overstates how badly the STT output fails in practice: a transcript can differ significantly at the character level while conveying the same facts.

The LLM understanding metric takes a different angle. It asks a reasoning LLM to extract *semantic facts* from both the ground-truth and the STT output, classify each fact by where it appears, and compute a score based on how much of the expected meaning was preserved.

---

## How It Works

1. **Single LLM call** with both the expected transcript and the STT output.
2. The LLM extracts facts in subject / predicate / object form from both texts simultaneously.
3. Each fact is classified with a `verdict`:
   - `both` — the fact appears in both texts (information preserved).
   - `expected` — the fact is only in the expected text (information lost).
   - `got` — the fact is only in the STT output (possible hallucination or addition).
4. A score is computed and the fact list is formatted as a human-readable detail string.
5. Both are returned as a `CustomMetricResult(score, detail)`.

### Score Formula

```
score = facts_both / (facts_both + facts_expected_only) * 100
```

`got`-only facts are shown in the detail string for inspection but do not affect the denominator — they are not "wrong" relative to the ground truth, they are extra.

---

## Data Model

### `CustomMetricResult` (`helpers/diff.py`)

The generic return type for any custom metric function:

```python
@dataclass(frozen=True)
class CustomMetricResult:
    score: float   # 0–100
    detail: str    # human-readable explanation, shown in HTML report
```

### `SemanticFact` and `Verdict` (`helpers/llm_understanding.py`)

```python
class Verdict(str, Enum):
    BOTH     = "both"      # fact in both expected and got
    EXPECTED = "expected"  # fact only in expected (missing from STT)
    GOT      = "got"       # fact only in got (not in expected)

@dataclass(frozen=True)
class SemanticFact:
    subject:   str
    predicate: str
    object:    str
    verdict:   Verdict
```

### LLM JSON Schema

The LLM is instructed to return:

```json
{
  "facts": [
    {"subject": "...", "predicate": "...", "object": "...", "verdict": "both|expected|got"}
  ]
}
```

---

## Configuration

| Variable | Required | Description |
|---|---|---|
| `GEMINI_API_KEY` | Yes (for LLM metric) | Gemini API key. If absent, the metric is silently skipped. |

The model is hardcoded to `gemini-2.5-pro`. It is configured in `helpers/llm_understanding.py`.

Add to your `.env`:
```
GEMINI_API_KEY=your_key_here
```

---

## Integration

### In `benchmark.py` (automatic)

If `GEMINI_API_KEY` is set, `benchmark.py` builds an `LLMUnderstandingAnalyzer` and passes its `compare` method to `transcribe_and_diff`. The TSV report gains a `custom_metric` column.

### In `transcribe_and_diff()` (manual / test)

```python
from helpers.semantic_understanding import SemanticUnderstandingAnalyzer

analyzer = SemanticUnderstandingAnalyzer(api_key=os.getenv("GEMINI_API_KEY"))
report = await transcribe_and_diff(
   provider, wav_path, txt_path, out_path,
   custom_metric_fn=analyzer.compare,
)
print(f"Understanding score: {report.custom_metric.score:.1f}%")
print(report.custom_metric.detail)
```

If `custom_metric_fn` is `None` (the default), `DiffReport.custom_metric` is `None` and the TSV column and HTML section are omitted.

---

## Output

### TSV Report

A `custom_metric` column is appended when the metric is active:

```
provider  file     ...  custom_metric
Deepgram  audio1   ...  87.5
ElevenLabs audio1  ...  92.3
```

### HTML Diff Report

When `custom_metric` is present, the HTML report gains:

- A stat card showing the understanding score.
- A collapsible detail section listing every extracted fact grouped by verdict (`both` / `expected only` / `got only`).

---

## Writing Your Own Custom Metric

Any async callable with the signature below can be used as `custom_metric_fn`:

```python
async def my_metric(text_expected: str, text_got: str) -> CustomMetricResult:
    ...
    return CustomMetricResult(score=score, detail=explanation)
```

Pass it to `transcribe_and_diff(custom_metric_fn=my_metric)` or use it directly in `benchmark.py`.

---

## Limitations

- **LLM cost**: one Gemini API call per (provider × audio file) pair.
- **Non-determinism**: LLMs may extract slightly different facts on retries. Temperature is set to 0.2 to reduce variance.
- **Language**: Gemini handles Czech well, but very colloquial or dialectal speech may confuse fact extraction.
- **Short texts**: transcripts shorter than ~20 words may yield too few facts for a meaningful score.
