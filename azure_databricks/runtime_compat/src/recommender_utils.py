"""Minimal compatibility shim required by the frozen ranker preprocessor."""

from __future__ import annotations

from typing import Any

import pandas as pd


def normalize_categorical_frame(frame: Any, missing_value: str = "Unknown") -> pd.DataFrame:
    """Reproduce the single legacy callable referenced by the approved joblib payload."""
    return pd.DataFrame(frame).astype("string").fillna(str(missing_value)).astype(str)
