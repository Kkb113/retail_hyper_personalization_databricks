import React, { useEffect, useRef, useState } from "react";
import { createRoot } from "react-dom/client";
import { request, productLabel } from "./chat.js";
import "./style.css";

function App() {
  const [session, setSession] = useState(null);
  const [customer, setCustomer] = useState("");
  const [messages, setMessages] = useState([]);
  const [draft, setDraft] = useState("");
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState("");
  const inFlight = useRef(false),
    bottom = useRef(null),
    input = useRef(null);
  async function connect() {
    if (inFlight.current) return;
    inFlight.current = true;
    setBusy(true);
    try {
      const next = await request("/api/session", {});
      setSession(next);
      setCustomer(next.customers[0] ?? "");
      setMessages([]);
      setDraft("");
      setNotice("");
    } catch {
      setNotice("Chat is unavailable until the demo is connected.");
      setSession(null);
    } finally {
      inFlight.current = false;
      setBusy(false);
    }
  }
  useEffect(() => {
    connect();
  }, []);
  useEffect(() => {
    bottom.current?.scrollIntoView({ block: "nearest" });
  }, [messages, busy]);
  async function send(event) {
    event.preventDefault();
    const text = draft.trim();
    if (!text || inFlight.current || !session) return;
    inFlight.current = true;
    setBusy(true);
    setNotice("");
    setDraft("");
    setMessages((previous) => [...previous, { role: "user", text }]);
    try {
      const reply = await request(
        "/api/chat",
        { text, customer_id: customer || null },
        session.csrf,
      );
      setMessages((previous) => [...previous, { ...reply, role: "assistant" }]);
    } catch (error) {
      setMessages((previous) => [
        ...previous,
        { role: "assistant", text: error.message, status: "unavailable" },
      ]);
    } finally {
      inFlight.current = false;
      setBusy(false);
      input.current?.focus();
    }
  }
  return (
    <div className="chat-app">
      <header>
        <a className="brand" href="#conversation">
          Intellify <span>Retail assistant</span>
        </a>
        <button className="text-button" disabled={busy} onClick={connect}>
          New chat
        </button>
      </header>
      <main>
        <section
          id="conversation"
          className="conversation"
          aria-label="Conversation"
          role="log"
          aria-live="polite"
          aria-relevant="additions text"
        >
          {!messages.length && (
            <div className="welcome">
              <span className="assistant-mark" aria-hidden="true">
                i
              </span>
              <h1>What are you looking for?</h1>
              <p>
                Ask for recommendations, compare products, or explore something
                new.
              </p>
            </div>
          )}
          {messages.map((message, index) => (
            <article key={index} className={"message " + message.role}>
              <h2>{message.role === "user" ? "You" : "Retail assistant"}</h2>
              <p className="message-text">{message.text}</p>
              {!!message.cards?.length && (
                <div className="products">
                  {message.cards.map((card, i) => (
                    <div className="product" key={i}>
                      <h3>{productLabel(card)}</h3>
                      {card.brand_name && (
                        <p className="muted">{card.brand_name}</p>
                      )}
                      {!card.product_id && !card.product_name && (
                        <dl>
                          {Object.entries(card).map(([key, value]) => (
                            <React.Fragment key={key}>
                              <dt>{key.replaceAll("_", " ")}</dt>
                              <dd>
                                {typeof value === "object"
                                  ? JSON.stringify(value)
                                  : String(value ?? "Not available")}
                              </dd>
                            </React.Fragment>
                          ))}
                        </dl>
                      )}
                      {typeof card.base_price === "number" && (
                        <p className="price">
                          {card.base_price.toLocaleString()}{" "}
                          <small>source price · currency unspecified</small>
                        </p>
                      )}
                      {card.active_discount_pct > 0 && (
                        <p>{card.active_discount_pct}% promotion</p>
                      )}
                      {card.reason_codes && (
                        <details>
                          <summary>Recommendation details</summary>
                          <p>{card.reason_codes}</p>
                        </details>
                      )}
                    </div>
                  ))}
                </div>
              )}
              {!!message.evidence?.length && (
                <details className="evidence">
                  <summary>View sources</summary>
                  {message.evidence.map((item, i) => (
                    <pre key={i}>{JSON.stringify(item, null, 2)}</pre>
                  ))}
                </details>
              )}
            </article>
          ))}
          {busy && messages.length > 0 && (
            <p className="thinking" role="status">
              The assistant is thinking…
            </p>
          )}
          <div ref={bottom} />
        </section>
        <div className="composer-area">
          {session && session.customers.length > 0 && (
            <label className="customer">
              Chat for
              <select
                aria-label="Customer context"
                disabled={busy}
                value={customer}
                onChange={(event) => {
                  setCustomer(event.target.value);
                  setMessages([]);
                  setDraft("");
                }}
              >
                <option value="">Guest</option>
                {session.customers.map((id) => (
                  <option key={id}>{id}</option>
                ))}
              </select>
            </label>
          )}
          {notice && (
            <p className="notice" role="status">
              {notice}{" "}
              <button className="text-button" onClick={connect} disabled={busy}>
                Reconnect
              </button>
            </p>
          )}
          <form onSubmit={send} className="composer">
            <textarea
              ref={input}
              aria-label="Message the retail assistant"
              placeholder="Ask the retail assistant…"
              rows={2}
              maxLength={1600}
              value={draft}
              onChange={(event) => setDraft(event.target.value)}
              onKeyDown={(event) => {
                if (
                  event.key === "Enter" &&
                  !event.shiftKey &&
                  !event.nativeEvent.isComposing
                ) {
                  event.preventDefault();
                  if (!busy) event.currentTarget.form.requestSubmit();
                }
              }}
            />
            <button
              className="send"
              type="submit"
              disabled={busy || !session || !draft.trim()}
              aria-label="Send message"
            >
              ↑
            </button>
          </form>
          <p className="footnote">
            Synthetic retail demo · Chats are temporary
          </p>
        </div>
      </main>
    </div>
  );
}
createRoot(document.getElementById("root")).render(<App />);
