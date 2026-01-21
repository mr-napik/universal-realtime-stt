from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Iterator


@dataclass(frozen=True)
class AssetPair:
    wav: Path
    txt: Path


def get_test_files(assets_dir: Path) -> Iterator[AssetPair]:
    """
    Iterate over *.wav files in assets_dir (recursively),
    yielding (wav, txt) pairs where txt has same basename.

    Enforces:
      - wav exists
      - matching txt exists
    """
    assets_dir = assets_dir.resolve()
    if not assets_dir.exists():
        assert False, "Assets directory doesn't exist."

    for wav in sorted(assets_dir.rglob("*.wav")):
        if not wav.is_file():
            continue
        txt = wav.with_suffix(".txt")
        if not txt.exists():
            raise FileNotFoundError(f"Missing expected transcript file for {wav.name}: {txt}")
        if not txt.is_file():
            raise FileNotFoundError(f"Transcript path exists but is not a file: {txt}")
        yield AssetPair(wav=wav, txt=txt)
