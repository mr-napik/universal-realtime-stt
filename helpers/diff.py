from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from diff_match_patch import diff_match_patch


_PUNCTUATION_NORMALIZE = str.maketrans({
    # Curly/smart double quotes → straight
    '\u201c': '"',  # "
    '\u201d': '"',  # "
    '\u201e': '"',  # „
    '\u201f': '"',  # ‟
    # Curly/smart single quotes → straight
    '\u2018': "'",  # '
    '\u2019': "'",  # '
    '\u201a': "'",  # ‚
    '\u201b': "'",  # ‛
    # Guillemets → straight
    '\u00ab': '"',  # «
    '\u00bb': '"',  # »
    '\u2039': "'",  # ‹
    '\u203a': "'",  # ›
    # Dashes → hyphen
    '\u2013': '-',  # en-dash –
    '\u2014': '-',  # em-dash —
    '\u2010': '-',  # hyphen ‐
    '\u2011': '-',  # non-breaking hyphen ‑
    '\u2212': '-',  # minus sign −
    # Ellipsis → period
    '\u2026': '.',  # …
})

_PUNCTUATION_REMOVE = str.maketrans('', '', '.,!?;:"\'-')


def normalize_text_for_diff(s: str, remove_punctuation: bool = True) -> str:
    """
    Normalize text for comparison:
    - unify whitespace (converts all whitespaces to single space),
    - convert to lowercase (as case is really hard for stt),
    - normalize punctuation variants (curly quotes, dashes, etc.) to ASCII equivalents,
    - optionally remove common punctuation entirely (default: True).
    """
    s = s.translate(_PUNCTUATION_NORMALIZE)
    if remove_punctuation:
        s = s.translate(_PUNCTUATION_REMOVE)
    return " ".join(s.strip().split()).lower()


def _escape_html(s: Optional[str]) -> str:
    if s is None:
        return ""
    return (
        s.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
        .replace("'", "&#39;")
    )


@dataclass(frozen=True)
class DiffReport:
    report_file: Path
    expected: str
    got: str
    levenshtein: int
    # Length stats (on normalized text)
    expected_chars: int
    expected_words: int
    got_chars: int
    got_words: int
    # Diff breakdown
    matched_chars: int
    inserted_chars: int
    deleted_chars: int

    @property
    def character_error_rate(self) -> float:
        """Returns character error rate in percent (based on levenshtein distance)."""
        if self.expected_chars == 0:
            return 0.0
        return round(float(self.levenshtein) / self.expected_chars * 100, 1)

    @property
    def match_percentage(self) -> float:
        """Returns percentage of expected characters that matched."""
        if self.expected_chars == 0:
            return 100.0
        return round(float(self.matched_chars) / self.expected_chars * 100, 1)


def write_diff_report(
        *,
        expected: str,
        got: str,
        out_path: Path,
        title: str,
        detail: str,
) -> DiffReport:
    """
    Create a human-readable HTML diff using diff-match-patch.

    Output contains:
      - expected text
      - got text
      - colored diff (insertions/deletions)

    Returns DiffReport object with the written path.
    """
    out_path = out_path.resolve()
    out_path.parent.mkdir(parents=True, exist_ok=True)

    # Normalize texts for comparison
    expected_norm = normalize_text_for_diff(expected)
    got_norm = normalize_text_for_diff(got)

    dmp = diff_match_patch()
    diffs = dmp.diff_main(expected_norm, got_norm)
    dmp.diff_cleanupSemantic(diffs)
    levenshtein = dmp.diff_levenshtein(diffs)

    diff_html = dmp.diff_prettyHtml(diffs)

    # Calculate diff breakdown: op is -1=delete, 0=equal, 1=insert
    matched_chars = sum(len(text) for op, text in diffs if op == 0)
    inserted_chars = sum(len(text) for op, text in diffs if op == 1)
    deleted_chars = sum(len(text) for op, text in diffs if op == -1)

    # Report object with all stats
    report = DiffReport(
        report_file=out_path,
        expected=expected,
        got=got,
        levenshtein=levenshtein,
        expected_chars=len(expected_norm),
        expected_words=len(expected_norm.split()),
        got_chars=len(got_norm),
        got_words=len(got_norm.split()),
        matched_chars=matched_chars,
        inserted_chars=inserted_chars,
        deleted_chars=deleted_chars,
    )

    # Minimal, self-contained HTML document. dmp.diff_prettyHtml returns <span> tags with inline styles.
    # We wrap it with some structure + monospace + whitespace preserving.
    html = f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8"/>
  <title>{_escape_html(title)}</title>
  <style>
    body {{
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Arial, sans-serif;
      margin: 2em;
    }}
    .hint {{
      padding: 10px 12px;
      border-left: 4px solid #888;
      background: #f6f6f6;
      margin: 12px 0 18px 0;
      white-space: pre-wrap;
      font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, "Liberation Mono", "Courier New", monospace;
      font-size: 12px;
    }}
    .panel {{
      margin: 16px 0;
    }}
    .panel h2 {{
      margin: 0 0 8px 0;
      font-size: 14px;
      color: #333;
    }}
    pre {{
      padding: 12px;
      background: #fafafa;
      border: 1px solid #e6e6e6;
      border-radius: 8px;
      overflow-x: auto;
      white-space: pre-wrap;
      word-break: break-word;
      font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, "Liberation Mono", "Courier New", monospace;
      font-size: 12px;
      line-height: 1.4;
    }}
    .diff {{
      padding: 12px;
      border: 1px solid #e6e6e6;
      border-radius: 8px;
      background: #fff;
      white-space: pre-wrap;
      word-break: break-word;
      font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, "Liberation Mono", "Courier New", monospace;
      font-size: 12px;
      line-height: 1.4;
    }}
    .stats {{
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
      gap: 12px;
      margin: 16px 0;
    }}
    .stat {{
      padding: 12px;
      background: #f8f9fa;
      border: 1px solid #e6e6e6;
      border-radius: 8px;
    }}
    .stat-label {{
      font-size: 11px;
      color: #666;
      text-transform: uppercase;
      letter-spacing: 0.5px;
    }}
    .stat-value {{
      font-size: 20px;
      font-weight: 600;
      color: #333;
      margin-top: 4px;
    }}
    .stat-detail {{
      font-size: 11px;
      color: #888;
      margin-top: 2px;
    }}
  </style>
</head>
<body>
  <h1>{_escape_html(title)}: {report.character_error_rate}% CER</h1>

  <div class='hint'>{_escape_html(detail)}</div>

  <div class="stats">
    <div class="stat">
      <div class="stat-label">Character Error Rate</div>
      <div class="stat-value">{report.character_error_rate:.1f}%</div>
      <div class="stat-detail">Levenshtein: {report.levenshtein}</div>
    </div>
    <div class="stat">
      <div class="stat-label">Expected</div>
      <div class="stat-value">{report.expected_chars} chars</div>
      <div class="stat-detail">{report.expected_words} words</div>
    </div>
    <div class="stat">
      <div class="stat-label">Got</div>
      <div class="stat-value">{report.got_chars} chars</div>
      <div class="stat-detail">{report.got_words} words</div>
    </div>
    <div class="stat">
      <div class="stat-label">Matched</div>
      <div class="stat-value">{report.match_percentage:.1f}%</div>
      <div class="stat-detail">{report.matched_chars} chars</div>
    </div>
    <div class="stat">
      <div class="stat-label">Inserted</div>
      <div class="stat-value">{report.inserted_chars} chars</div>
      <div class="stat-detail">Extra in STT output</div>
    </div>
    <div class="stat">
      <div class="stat-label">Deleted</div>
      <div class="stat-value">{report.deleted_chars} chars</div>
      <div class="stat-detail">Missing from STT output</div>
    </div>
  </div>

  <div class="panel">
    <h2>Diff (regardless of punctuation, spaces and capitalization; red = deletions, green = insertions)</h2>
    <div class="diff">{diff_html}</div>
  </div>

  <div class="panel">
    <h2>Expected (Ground Truth)</h2>
    <pre>{_escape_html(expected)}</pre>
  </div>

  <div class="panel">
    <h2>Got (Result of STT)</h2>
    <pre>{_escape_html(got)}</pre>
  </div>


</body>
</html>
"""
    out_path.write_text(html, encoding="utf-8")
    return report
