"""Regression coverage for tie-dependent candidate quotas across backends."""

import pandas as pd
from retail_hp_azure.recommender import AdaptiveRetailRecommender, _percentile


def test_candidate_boundary_uses_product_id_for_equal_scores():
    values = [("z", 1.0), ("a", 1.0), ("b", 1.0)]
    first = AdaptiveRetailRecommender._source_frame(values, "content", 2)
    second = AdaptiveRetailRecommender._source_frame(list(reversed(values)), "content", 2)
    pd.testing.assert_frame_equal(first, second)
    assert list(first.ProductID) == ["a", "b"]


def test_numerical_noise_does_not_split_percentile_ties():
    result = _percentile(pd.Series([0.5, 0.5 + 1e-15, 0.8]))
    assert result.iloc[0] == result.iloc[1]
    assert result.iloc[2] > result.iloc[1]


def test_popularity_ties_do_not_depend_on_series_order():
    model = object.__new__(AdaptiveRetailRecommender)
    model.eligible_ids = {"a", "b", "z"}
    first = pd.Series([1.0, 1.0, 1.0], index=["z", "a", "b"])
    assert model._ordered_scores(first, 2) == model._ordered_scores(first.iloc[::-1], 2)
    assert model._ordered_scores(first, 2) == [("a", 1.0), ("b", 1.0)]
