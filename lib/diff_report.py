from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from diff_match_patch import diff_match_patch

@dataclass(frozen=True)
class DiffReport:
    html_path: Path
    expected: str
    got: str
    levenshtein: int

    @property
    def character_error_rate(self):
        """Returns character error rate in percent (based on levenshtein distance)."""
        return round(float(self.levenshtein) / len(self.expected) * 100, 1)


def write_diff_html(
        *,
        expected: str,
        got: str,
        out_path: Path,
        title: str = "STT Transcript Diff",
        context_hint: Optional[str] = None,
) -> DiffReport:
    """
    Create a human-readable HTML diff using diff-match-patch.

    Output contains:
      - expected text
      - got text
      - colored diff (insertions/deletions)

    Returns DiffReport with the written path.
    """
    out_path = out_path.resolve()
    out_path.parent.mkdir(parents=True, exist_ok=True)

    dmp = diff_match_patch()
    diffs = dmp.diff_main(expected, got)
    dmp.diff_cleanupSemantic(diffs)
    levenshtein = dmp.diff_levenshtein(diffs)

    diff_html = dmp.diff_prettyHtml(diffs)

    # report object
    report = DiffReport(html_path=out_path, expected=expected, got=got, levenshtein=levenshtein)

    # Minimal, self-contained HTML document. dmp.diff_prettyHtml returns <span> tags with inline styles.
    # We wrap it with some structure + monospace + whitespace preserving.
    hint_html = f"<div class='hint'>{_escape_html(context_hint)}</div>" if context_hint else ""
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
  <h1>{_escape_html(title)}: {report.character_error_rate}% Character Error Rate</h1>
  {hint_html}

  <div class="panel">
    <h2>Diff (red = deletions, green = insertions)</h2>
    <div class="diff">{diff_html}</div>
  </div>

  <div class="panel">
    <h2>Expected</h2>
    <pre>{_escape_html(expected)}</pre>
  </div>

  <div class="panel">
    <h2>Got</h2>
    <pre>{_escape_html(got)}</pre>
  </div>


</body>
</html>
"""
    out_path.write_text(html, encoding="utf-8")
    return report


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
