import { test } from "node:test";
import assert from "node:assert/strict";
import { compareRanks } from "./compare.js";
test("diff preserves rank and distinguishes additions, removals and unchanged", () => {
  assert.deepEqual(
    compareRanks(
      [
        { product_id: "a", rank: 1 },
        { product_id: "b", rank: 2 },
      ],
      [
        { product_id: "b", rank: 1 },
        { product_id: "c", rank: 2 },
      ],
    ).map((x) => x.change),
    ["Removed", "Reranked", "Added"],
  );
  assert.equal(
    compareRanks(
      [{ product_id: "a", rank: 1 }],
      [{ product_id: "a", rank: 1 }],
    )[0].change,
    "Unchanged",
  );
  assert.deepEqual(compareRanks([], []), []);
});
