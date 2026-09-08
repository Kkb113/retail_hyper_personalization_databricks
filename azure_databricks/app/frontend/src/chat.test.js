import test from "node:test";
import assert from "node:assert/strict";
import { request, productLabel } from "./chat.js";

test("chat sends only the supplied payload with session and CSRF protection", async (t) => {
  let calls = 0;
  t.mock.method(globalThis, "fetch", async (path, options) => {
    calls++;
    assert.equal(path, "/api/chat");
    assert.equal(options.method, "POST");
    assert.equal(options.credentials, "same-origin");
    assert.equal(options.headers["x-csrf-token"], "test-csrf");
    assert.equal(options.headers["x-retail-request"], "workbench-v1");
    assert.deepEqual(JSON.parse(options.body), {
      text: "Recommend something",
      customer_id: null,
    });
    assert.ok(options.signal instanceof AbortSignal);
    return { ok: true, json: async () => ({ text: "Hello", cards: [] }) };
  });
  assert.deepEqual(
    await request(
      "/api/chat",
      { text: "Recommend something", customer_id: null },
      "test-csrf",
    ),
    { text: "Hello", cards: [] },
  );
  assert.equal(calls, 1);
});

test("authorization failures ask for a new session without leaking response bodies", async (t) => {
  t.mock.method(globalThis, "fetch", async () => ({ ok: false, status: 403 }));
  await assert.rejects(request("/api/chat", {}), /start a new chat/);
});

test("server failures do not retry billable operations", async (t) => {
  let calls = 0;
  t.mock.method(globalThis, "fetch", async () => {
    calls++;
    return { ok: false, status: 500 };
  });
  await assert.rejects(request("/api/chat", {}), /couldn't complete/);
  assert.equal(calls, 1);
});

test("network errors are sanitized", async (t) => {
  t.mock.method(globalThis, "fetch", async () => {
    throw new Error("internal sensitive diagnostics");
  });
  await assert.rejects(request("/api/chat", {}), {
    message:
      "The connection was interrupted. Please try again when the demo is available.",
  });
});

test("card labels never invent a product name", () => {
  assert.equal(
    productLabel({ product_name: "Shirt", product_id: "P1" }),
    "Shirt",
  );
  assert.equal(productLabel({ product_id: "P1" }), "P1");
  assert.equal(productLabel({}), "Details");
});
