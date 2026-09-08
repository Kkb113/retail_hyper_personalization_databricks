export async function request(path, body, csrf = "") {
  let response;
  try {
    response = await fetch(path, {
      method: "POST",
      credentials: "same-origin",
      headers: {
        "Content-Type": "application/json",
        "x-retail-request": "workbench-v1",
        "x-csrf-token": csrf,
      },
      body: JSON.stringify(body),
      signal: AbortSignal.timeout(85000),
    });
  } catch {
    throw new Error(
      "The connection was interrupted. Please try again when the demo is available.",
    );
  }
  if (!response.ok) {
    throw new Error(
      response.status === 401 || response.status === 403
        ? "Please start a new chat to reconnect securely."
        : "I couldn't complete that request. Please try again when the demo is available.",
    );
  }
  return response.json();
}
export function productLabel(card) {
  return card.product_name || card.product_id || "Details";
}
