"""MLflow PyFunc boundary for the portable retail recommender."""

from __future__ import annotations

import importlib
from typing import Any

import pandas as pd  # type: ignore[import-untyped]

from retail_hp_azure.recommender import AdaptiveRetailRecommender

_mlflow_pyfunc: Any = importlib.import_module("mlflow.pyfunc")


class RetailHyperPersonalizationModel(_mlflow_pyfunc.PythonModel):  # type: ignore[misc]
    """Thin serialization boundary; all domain behavior lives in the runtime package."""

    model: AdaptiveRetailRecommender

    def load_context(self, context: Any) -> None:
        self.model = AdaptiveRetailRecommender(
            data_root=context.artifacts["data"],
            model_root=context.artifacts["models"],
        )

    def predict(
        self, context: Any, model_input: pd.DataFrame, params: dict[str, Any] | None = None
    ) -> pd.DataFrame:
        del context, params
        return self.model.predict(model_input)
