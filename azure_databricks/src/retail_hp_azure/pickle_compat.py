"""Narrow compatibility boundary for one callable referenced by frozen joblib files."""

from __future__ import annotations

import sys
import types
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

import joblib  # type: ignore[import-untyped]
import pandas as pd  # type: ignore[import-untyped]


def normalize_categorical_frame(frame: Any, missing_value: str = "Unknown") -> pd.DataFrame:
    """Reproduce the sole legacy callable embedded in the approved preprocessors."""
    return pd.DataFrame(frame).astype("string").fillna(str(missing_value)).astype(str)


@contextmanager
def _legacy_module_alias() -> Iterator[None]:
    previous_package = sys.modules.get("src")
    previous_module = sys.modules.get("src.recommender_utils")
    package = types.ModuleType("src")
    package.__path__ = []
    module = types.ModuleType("src.recommender_utils")
    module.normalize_categorical_frame = normalize_categorical_frame  # type: ignore[attr-defined]
    package.recommender_utils = module  # type: ignore[attr-defined]
    sys.modules["src"] = package
    sys.modules["src.recommender_utils"] = module
    try:
        yield
    finally:
        if previous_package is None:
            sys.modules.pop("src", None)
        else:
            sys.modules["src"] = previous_package
        if previous_module is None:
            sys.modules.pop("src.recommender_utils", None)
        else:
            sys.modules["src.recommender_utils"] = previous_module


def load_approved_joblib(path: str | Any) -> Any:
    """Load a pre-hash-approved artifact with only its required module alias exposed."""
    with _legacy_module_alias():
        return joblib.load(path)
