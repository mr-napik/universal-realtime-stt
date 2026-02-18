"""
Tests for diff report generation and TSV export.

Exercises DiffReport, HTML output, and benchmark TSV writing
with sample text — no STT providers or audio files needed.

    pytest tests/test_diff.py -v
"""
from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from helpers.diff import DiffReport, write_diff_report


# Czech sample texts — expected is ground truth, got simulates STT output with typical errors.
EXPECTED = (
    "Dobrý den, vítejte v naší přednášce o umělé inteligenci. "
    "Dnes budeme hovořit o tom, jak se strojové učení využívá v praxi. "
    "Začneme základními pojmy a postupně přejdeme k pokročilejším tématům."
)

GOT = (
    "Dobrý den vítejte v naší přednášce o umělé inteligenci. "
    "Dnes budeme hovořit o tom, jak se strojové učení využívá praxi. "
    "Začneme základními pojmy a postupně přejdeme k pokročilejším tématům."
)


class TestDiffReport(unittest.TestCase):

    def setUp(self) -> None:
        self._tmpdir = TemporaryDirectory()
        self.tmp = Path(self._tmpdir.name)

    def tearDown(self) -> None:
        self._tmpdir.cleanup()

    def test_write_diff_report(self) -> None:
        """HTML diff report is created with correct metrics."""
        html_path = self.tmp / "test.diff.html"
        report = write_diff_report(
            expected=EXPECTED,
            got=GOT,
            out_path=html_path,
            title="test sample",
            detail="Unit test",
        )

        # File written
        self.assertTrue(html_path.exists())
        html = html_path.read_text(encoding="utf-8")
        self.assertIn("WER", html)
        self.assertIn("CER", html)

        # Sanity: got is close to expected, so error rates should be low but nonzero
        self.assertGreater(report.word_error_rate, 0)
        self.assertLess(report.word_error_rate, 20)
        self.assertGreater(report.character_error_rate, 0)
        self.assertLess(report.character_error_rate, 20)

        # Word counts should be reasonable
        self.assertGreater(report.words_expected, 10)
        self.assertGreater(report.words_got, 10)

    def test_to_metrics_dict(self) -> None:
        """to_metrics_dict() includes all numeric fields and computed properties, excludes str/Path."""
        report = write_diff_report(
            expected=EXPECTED,
            got=GOT,
            out_path=self.tmp / "test.diff.html",
            title="test",
            detail="test",
        )
        metrics = report.to_metrics_dict()

        # Computed properties present
        self.assertIn("word_error_rate", metrics)
        self.assertIn("character_error_rate", metrics)
        self.assertIn("match_percentage", metrics)

        # Numeric fields present
        self.assertIn("chars_expected", metrics)
        self.assertIn("word_levenshtein", metrics)

        # Raw text and path excluded
        self.assertNotIn("text_expected", metrics)
        self.assertNotIn("text_got", metrics)
        self.assertNotIn("report_file", metrics)

        # All values are strings (formatted for TSV)
        for k, v in metrics.items():
            self.assertIsInstance(v, str, f"metrics[{k!r}] should be str, got {type(v)}")

    def test_tsv_roundtrip(self) -> None:
        """Benchmark write_tsv produces valid TSV with auto-discovered columns."""
        from benchmark import BenchmarkResult, write_tsv

        reports = []
        for i, (exp, got) in enumerate([(EXPECTED, GOT), (GOT, EXPECTED)]):
            report = write_diff_report(
                expected=exp,
                got=got,
                out_path=self.tmp / f"diff_{i}.html",
                title=f"sample {i}",
                detail="test",
            )
            reports.append(BenchmarkResult(f"Provider{i}", f"file{i}.wav", report, None))

        # Add a failed result
        reports.append(BenchmarkResult("FailedProvider", "fail.wav", None, "connection timeout"))

        tsv_path = write_tsv(reports, "test")
        self.assertTrue(tsv_path.exists())

        lines = tsv_path.read_text(encoding="utf-8").rstrip("\n").split("\n")
        header = lines[0].split("\t")
        self.assertEqual(lines[0].count("\t") + 1, len(header))

        # All data rows have same number of columns as header
        for i, line in enumerate(lines[1:], 1):
            cols = line.split("\t")
            self.assertEqual(len(cols), len(header), f"Row {i} has {len(cols)} cols, header has {len(header)}")

        # Structural columns present
        self.assertEqual(header[0], "provider")
        self.assertEqual(header[1], "file")
        self.assertEqual(header[-2], "diff_report")
        self.assertEqual(header[-1], "error")

        # Metric columns auto-discovered from DiffReport
        self.assertIn("word_error_rate", header)
        self.assertIn("character_error_rate", header)