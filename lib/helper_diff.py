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

    @property
    def character_error_rate(self):
        """Returns character error rate in percent (based on levenshtein distance)."""
        return round(float(self.levenshtein) / len(normalize_text_for_diff(self.expected)) * 100)


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

    dmp = diff_match_patch()
    diffs = dmp.diff_main(normalize_text_for_diff(expected), normalize_text_for_diff(got))
    dmp.diff_cleanupSemantic(diffs)
    levenshtein = dmp.diff_levenshtein(diffs)

    diff_html = dmp.diff_prettyHtml(diffs)

    # report object
    report = DiffReport(report_file=out_path, expected=expected, got=got, levenshtein=levenshtein)

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
  </style>
</head>
<body>
  <h1>{_escape_html(title)}: {round(report.character_error_rate, 1)}% CER</h1>
  
  <div class='hint'>{_escape_html(detail)}</div>

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
