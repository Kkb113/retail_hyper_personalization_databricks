export function compareRanks(baseline, scenario) {
  const before = new Map(baseline.map((row) => [row.product_id, row.rank]));
  const after = new Map(scenario.map((row) => [row.product_id, row.rank]));
  return [...new Set([...before.keys(), ...after.keys()])].map(
    (product_id) => ({
      product_id,
      before: before.get(product_id) ?? null,
      after: after.get(product_id) ?? null,
      change: !before.has(product_id)
        ? "Added"
        : !after.has(product_id)
          ? "Removed"
          : before.get(product_id) === after.get(product_id)
            ? "Unchanged"
            : "Reranked",
    }),
  );
}
