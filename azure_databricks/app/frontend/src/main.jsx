import React, { useState } from "react";
import { createRoot } from "react-dom/client";
import { compareRanks } from "./compare.js";
import "./style.css";

const screens = [
  "Workbench",
  "Customer 360",
  "Concierge",
  "What-if studio",
  "Discovery",
  "Opportunities",
  "Quality & evidence",
  "Feedback",
];
function App() {
  const [screen, setScreen] = useState("Workbench"),
    [session, setSession] = useState(null);
  const [customer, setCustomer] = useState(""),
    [busy, setBusy] = useState(false),
    [notice, setNotice] = useState("");
  const [result, setResult] = useState(null),
    [baseline, setBaseline] = useState([]),
    [diff, setDiff] = useState([]);
  const [query, setQuery] = useState(""),
    [price, setPrice] = useState(""),
    [category, setCategory] = useState("");
  const [sensitivity, setSensitivity] = useState("High"),
    [promotion, setPromotion] = useState(false);
  const [product, setProduct] = useState(""),
    [reason, setReason] = useState("relevant");
  const [sentiment, setSentiment] = useState("positive"),
    [confirmed, setConfirmed] = useState(false);
  const [feedbackProducts, setFeedbackProducts] = useState([]),
    [dependencies, setDependencies] = useState(null);
  const [history, setHistory] = useState([]);
  async function api(path, body) {
    const response = await fetch(path, {
      method: body ? "POST" : "GET",
      credentials: "same-origin",
      headers: {
        "Content-Type": "application/json",
        "x-retail-request": "workbench-v1",
        "x-csrf-token": session?.csrf ?? "",
      },
      ...(body ? { body: JSON.stringify(body) } : {}),
    });
    const data = await response.json();
    if (!response.ok)
      throw new Error(
        typeof data.detail === "string"
          ? data.detail
          : "Check the input fields and try again.",
      );
    return data;
  }
  async function run(fn) {
    if (busy) return;
    setBusy(true);
    setNotice("");
    try {
      await fn();
    } catch (error) {
      setNotice(error.message);
      setResult(null);
    } finally {
      setBusy(false);
    }
  }
  async function action(name, args = {}) {
    const data = await api("/api/action", { action: name, arguments: args });
    setResult(data);
    setHistory((h) => [
      ...h.slice(-9),
      { action: name, request: data.request_id, provenance: data.provenance },
    ]);
    return data;
  }
  const authorized = session && !busy;
  function navigate(next) {
    setScreen(next);
    setResult(null);
    setNotice("");
    setDiff([]);
    setQuery("");
    setConfirmed(false);
  }
  const rows = result?.rows ?? result?.cards ?? [];
  return (
    <div className="layout">
      <a href="#main" className="skip">
        Skip to workbench
      </a>
      <aside className="sidebar">
        <div className="brand">
          <span className="mark">i</span>
          <div>
            INTELLIFY<small>Retail intelligence</small>
          </div>
        </div>
        <p className="eyebrow">DEMO WORKSPACE</p>
        <nav aria-label="Experiences">
          {screens.map((name, i) => (
            <button
              key={name}
              aria-current={screen === name ? "page" : undefined}
              disabled={busy}
              className={screen === name ? "active" : ""}
              onClick={() => navigate(name)}
            >
              <span className="nav-number">
                {String(i + 1).padStart(2, "0")}
              </span>
              {name}
            </button>
          ))}
        </nav>
        <div className="sidebar-note">
          <span className="dot" /> On-demand by design
          <p>No browser action starts compute. Synthetic data only.</p>
        </div>
      </aside>
      <div className="workspace">
        <header className="topbar">
          <span>
            AZURE DATABRICKS <span className="slash">/</span> RETAIL POC
          </span>
          <span className="badge">
            {session ? "Authorized session" : "Cost-safe · locked"}
          </span>
        </header>
        <main id="main">
          <div className="heading">
            <div>
              <p className="eyebrow">PERSONALIZATION, WITH PROOF</p>
              <h1>{screen}</h1>
              <p className="subtitle">
                From customer signals to a recommendation you can explain.
              </p>
            </div>
            <button
              disabled={busy}
              onClick={() =>
                run(async () => {
                  const data = await api("/api/session", {});
                  setSession(data);
                  setCustomer(data.customers[0] ?? "");
                  setResult(null);
                  setHistory([]);
                  setFeedbackProducts([]);
                  setBaseline([]);
                  setDiff([]);
                  setProduct("");
                  setConfirmed(false);
                  setDependencies(null);
                  setNotice(
                    "Session authorized. Select an experience to begin.",
                  );
                })
              }
            >
              {session ? "New session" : "Connect securely"}{" "}
              <span aria-hidden="true">↗</span>
            </button>
          </div>
          <section className="contextbar" aria-label="Customer context">
            <label>
              Customer context
              <select
                value={customer}
                disabled={!authorized}
                onChange={(e) => {
                  setCustomer(e.target.value);
                  setResult(null);
                  setBaseline([]);
                  setHistory([]);
                  setFeedbackProducts([]);
                  setProduct("");
                  setConfirmed(false);
                }}
              >
                <option value="">Guest discovery</option>
                {session?.customers.map((id) => (
                  <option key={id}>{id}</option>
                ))}
              </select>
            </label>
            <div>
              <strong>Champion v3</strong>
              <span>Governed recommender</span>
            </div>
            <div>
              <strong>GPT-5.6 Luna</strong>
              <span>Evidence-only agent</span>
            </div>
            <div>
              <strong>Explicit consent</strong>
              <span>Feedback is never automatic</span>
            </div>
          </section>
          <div
            role="status"
            aria-live="polite"
            className={notice || busy ? "notice" : "quiet"}
          >
            {busy ? "Checking governed sources. Please wait…" : notice}
          </div>
          {!session && (
            <div className="locked">
              <strong>Safe to explore. Live data stays locked.</strong>
              <p>
                Connect through the Databricks App during an operator-approved
                demo window. This preview does not query Azure, call Luna, or
                manufacture customer results.
              </p>
            </div>
          )}
          {screen === "Workbench" && (
            <>
              <section className="intro">
                <div>
                  <p className="eyebrow">THE DEMO PATH</p>
                  <h2>
                    One customer.
                    <br />A more relevant experience.
                  </h2>
                  <p>
                    Explore the profile, inspect the recommendation, then change
                    a preference to see what moves—and why.
                  </p>
                  <button onClick={() => navigate("Customer 360")}>
                    Start with customer context →
                  </button>
                </div>
                <ol className="journey">
                  <li>
                    <span>01</span>
                    <div>
                      <h3>Understand</h3>
                      <p>Safe profile and observed behavior.</p>
                    </div>
                  </li>
                  <li>
                    <span>02</span>
                    <div>
                      <h3>Recommend</h3>
                      <p>Model-backed ranking, grounded facts.</p>
                    </div>
                  </li>
                  <li>
                    <span>03</span>
                    <div>
                      <h3>Learn explicitly</h3>
                      <p>Confirmed feedback, auditable receipt.</p>
                    </div>
                  </li>
                </ol>
              </section>
              <section className="tiles">
                {[
                  ["Concierge", "Ask naturally. Inspect the evidence."],
                  [
                    "What-if studio",
                    "Compare rankings without changing a profile.",
                  ],
                  ["Discovery", "Find products by meaning and constraints."],
                ].map(([name, text]) => (
                  <button
                    className="tile"
                    key={name}
                    onClick={() => navigate(name)}
                  >
                    <span className="eyebrow">EXPLORE</span>
                    <h3>{name} ↗</h3>
                    <p>{text}</p>
                  </button>
                ))}
              </section>
              <button
                className="secondary"
                disabled={!authorized}
                onClick={() =>
                  run(async () =>
                    setDependencies(await api("/health/dependencies")),
                  )
                }
              >
                Check dependency status
              </button>
              {dependencies && <Facts row={dependencies} />}
            </>
          )}
          {screen === "Customer 360" && (
            <section className="panel">
              <h2>Signals, not personal identifiers</h2>
              <p>
                Profile, loyalty and behavior aggregates from the governed
                customer view.
              </p>
              <button
                disabled={!authorized || !customer}
                onClick={() =>
                  run(() =>
                    action("get_customer_360", { customer_id: customer }),
                  )
                }
              >
                Load customer profile
              </button>
            </section>
          )}
          {screen === "Concierge" && (
            <section className="panel">
              <h2>Your retail copilot</h2>
              <p>
                Luna selects a governed tool. Only validated results are shown;
                no unverified token stream.
              </p>
              <form
                onSubmit={(e) => {
                  e.preventDefault();
                  run(async () =>
                    setResult(
                      await api("/api/chat", {
                        text: query,
                        customer_id: customer || null,
                      }),
                    ),
                  );
                }}
              >
                <label>
                  Ask a question
                  <textarea
                    maxLength={1600}
                    required
                    value={query}
                    onChange={(e) => setQuery(e.target.value)}
                    placeholder="Recommend products for this customer and explain the ranking"
                  />
                </label>
                <button disabled={!authorized || !query.trim()}>
                  Ask Luna →
                </button>
              </form>
              <button
                className="secondary"
                disabled={!authorized || !customer}
                onClick={() =>
                  run(async () => {
                    const data = await action("get_recommendations", {
                      customer_id: customer,
                      top_n: 5,
                      mode: "batch",
                    });
                    setFeedbackProducts(data.rows);
                  })
                }
              >
                Browse batch recommendations without an LLM
              </button>
            </section>
          )}
          {screen === "What-if studio" && (
            <section className="panel">
              <h2>Change the scenario. Keep the profile.</h2>
              <p>
                Real-time simulation requires an explicitly running recommender.
                It never starts one here.
              </p>
              <div className="fields">
                <label>
                  Favorite category ID
                  <input
                    value={category}
                    maxLength={128}
                    onChange={(e) => setCategory(e.target.value)}
                    placeholder="Optional exact category ID"
                  />
                </label>
                <label>
                  Price sensitivity
                  <select
                    value={sensitivity}
                    onChange={(e) => setSensitivity(e.target.value)}
                  >
                    {["Low", "Medium", "High"].map((x) => (
                      <option key={x}>{x}</option>
                    ))}
                  </select>
                </label>
              </div>
              <button
                disabled={!authorized || !customer}
                onClick={() =>
                  run(async () => {
                    setDiff([]);
                    const base = await action("get_recommendations", {
                      customer_id: customer,
                      top_n: 5,
                    });
                    setBaseline(base.rows);
                    const next = await action("simulate_scenario", {
                      customer_id: customer,
                      scenario_id: "what-if",
                      top_n: 5,
                      price_sensitivity: sensitivity,
                      ...(category ? { favorite_category_id: category } : {}),
                    });
                    setDiff(compareRanks(base.rows, next.rows));
                  })
                }
              >
                Compare baseline and scenario
              </button>
              {diff.length > 0 && (
                <div className="table-wrap">
                  <table>
                    <caption>
                      Ranking changes · {baseline.length} baseline products
                    </caption>
                    <thead>
                      <tr>
                        <th>Product</th>
                        <th>Before</th>
                        <th>After</th>
                        <th>Change</th>
                      </tr>
                    </thead>
                    <tbody>
                      {diff.map((row) => (
                        <tr key={row.product_id}>
                          <td>{row.product_id}</td>
                          <td>{row.before ?? "—"}</td>
                          <td>{row.after ?? "—"}</td>
                          <td>{row.change}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
            </section>
          )}
          {screen === "Discovery" && (
            <section className="panel">
              <h2>Describe the product you have in mind</h2>
              <form
                onSubmit={(e) => {
                  e.preventDefault();
                  run(() =>
                    action("search_products", {
                      query,
                      top_n: 5,
                      promotion_only: promotion,
                      ...(price !== "" ? { max_price: Number(price) } : {}),
                      ...(category ? { category_id: category } : {}),
                    }),
                  );
                }}
              >
                <label>
                  Search by meaning
                  <input
                    required
                    maxLength={300}
                    value={query}
                    onChange={(e) => setQuery(e.target.value)}
                    placeholder="Comfortable everyday essentials"
                  />
                </label>
                <div className="fields">
                  <label>
                    Maximum price · source units
                    <input
                      type="number"
                      min="0"
                      max="1000000"
                      value={price}
                      onChange={(e) => setPrice(e.target.value)}
                    />
                  </label>
                  <label>
                    Category ID
                    <input
                      value={category}
                      maxLength={128}
                      onChange={(e) => setCategory(e.target.value)}
                    />
                  </label>
                </div>
                <label className="check">
                  <input
                    type="checkbox"
                    checked={promotion}
                    onChange={(e) => setPromotion(e.target.checked)}
                  />
                  Active promotions only
                </label>
                <button disabled={!authorized}>Find products →</button>
              </form>
              <p className="muted">
                Source prices have no verified currency. We do not label them as
                INR.
              </p>
            </section>
          )}
          {screen === "Opportunities" && (
            <section className="panel">
              <h2>A reason to reconnect</h2>
              <p>
                Verified active promotions among this customer's
                recommendations. Replenishment, cart recovery and price-change
                triggers are not implemented and are not presented as live
                insights.
              </p>
              <button
                disabled={!authorized || !customer}
                onClick={() =>
                  run(() =>
                    action("get_opportunities", { customer_id: customer }),
                  )
                }
              >
                Find active opportunities
              </button>
            </section>
          )}
          {screen === "Quality & evidence" && (
            <section className="panel">
              <h2>Show the work behind the result</h2>
              <p>
                Coverage is not accuracy. Inspect source versions, timestamps
                and request IDs.
              </p>
              <button
                disabled={!authorized || !session?.quality_allowed}
                onClick={() => run(() => action("get_quality_summary"))}
              >
                Load governed quality summary
              </button>
              <ol className="evidence">
                {history.map((item, i) => (
                  <li key={i}>
                    <strong>{item.action}</strong>
                    <Facts
                      row={{ request_id: item.request, ...item.provenance }}
                    />
                  </li>
                ))}
              </ol>
              {!history.length && <p>No tool requests in this session yet.</p>}
            </section>
          )}
          {screen === "Feedback" && (
            <section className="panel">
              <h2>Feedback with an audit trail</h2>
              <p>
                Load batch recommendations first. Confirmation writes to the
                existing operational store; it does not retrain the model.
              </p>
              <button
                className="secondary"
                disabled={!authorized || !customer}
                onClick={() =>
                  run(async () => {
                    const data = await action("get_recommendations", {
                      customer_id: customer,
                      top_n: 5,
                    });
                    setFeedbackProducts(data.rows);
                    setProduct("");
                    setConfirmed(false);
                  })
                }
              >
                Load eligible recommendations
              </button>
              <form
                onSubmit={(e) => {
                  e.preventDefault();
                  run(async () => {
                    const receipt = await api("/api/feedback", {
                      product_id: product,
                      sentiment,
                      reason_code: reason,
                      confirmed,
                    });
                    setResult(receipt);
                    setConfirmed(false);
                    setNotice(
                      "Feedback receipt returned by the operational store.",
                    );
                  });
                }}
              >
                <label>
                  Recommended product
                  <select
                    required
                    value={product}
                    onChange={(e) => {
                      setProduct(e.target.value);
                      setConfirmed(false);
                    }}
                  >
                    <option value="">Select a product</option>
                    {feedbackProducts.map((row) => (
                      <option key={row.product_id}>{row.product_id}</option>
                    ))}
                  </select>
                </label>
                <div className="fields">
                  <label>
                    Sentiment
                    <select
                      value={sentiment}
                      onChange={(e) => {
                        setSentiment(e.target.value);
                        setConfirmed(false);
                      }}
                    >
                      {["positive", "negative", "neutral"].map((x) => (
                        <option key={x}>{x}</option>
                      ))}
                    </select>
                  </label>
                  <label>
                    Reason
                    <select
                      value={reason}
                      onChange={(e) => {
                        setReason(e.target.value);
                        setConfirmed(false);
                      }}
                    >
                      {[
                        "relevant",
                        "not_relevant",
                        "already_owned",
                        "too_expensive",
                        "out_of_stock",
                        "other",
                      ].map((x) => (
                        <option key={x}>{x}</option>
                      ))}
                    </select>
                  </label>
                </div>
                <label className="check">
                  <input
                    type="checkbox"
                    checked={confirmed}
                    onChange={(e) => setConfirmed(e.target.checked)}
                  />
                  I confirm saving this feedback for the selected
                  recommendation.
                </label>
                <button disabled={!authorized || !product || !confirmed}>
                  Save confirmed feedback
                </button>
              </form>
            </section>
          )}
          {result && (
            <section aria-label="Results" className="results">
              <div className="result-title">
                <h2>{result.text || "Governed results"}</h2>
                <span className="badge">
                  {result.status || `${rows.length} records`}
                </span>
              </div>
              {rows.length === 0 && !result.text && (
                <p>No matching records. Try a different selection.</p>
              )}
              <div className="cards">
                {rows.map((row, i) => (
                  <article
                    className="product"
                    key={`${row.product_id ?? "record"}-${i}`}
                  >
                    <span className="eyebrow">
                      {row.rank ? `RANK ${row.rank}` : "VERIFIED RECORD"}
                    </span>
                    <h3>
                      {row.product_name ||
                        row.product_id ||
                        row.customer_id ||
                        "Summary"}
                    </h3>
                    <Facts row={row} />
                    {row.product_id && !row.product_name && (
                      <button
                        className="secondary"
                        disabled={!authorized}
                        onClick={() =>
                          run(() =>
                            action("get_product_details", {
                              product_id: row.product_id,
                            }),
                          )
                        }
                      >
                        Inspect product facts
                      </button>
                    )}
                  </article>
                ))}
              </div>
              {result.provenance && (
                <details>
                  <summary>Source evidence</summary>
                  <Facts row={result.provenance} />
                  <p>Request: {result.request_id}</p>
                </details>
              )}
              {result.evidence?.map((item, i) => (
                <details key={i}>
                  <summary>Tool evidence {i + 1}</summary>
                  <Facts row={item} />
                </details>
              ))}
            </section>
          )}
          <footer>
            Synthetic retail POC <span>•</span> No purchases or outbound
            messages <span>•</span> Temporary scenarios never overwrite profiles
          </footer>
        </main>
      </div>
    </div>
  );
}
function Facts({ row }) {
  return (
    <dl>
      {Object.entries(row).map(([key, value]) => (
        <div key={key}>
          <dt>{key.replaceAll("_", " ")}</dt>
          <dd>
            {typeof value === "object"
              ? JSON.stringify(value)
              : String(value ?? "Not available")}
          </dd>
        </div>
      ))}
    </dl>
  );
}
createRoot(document.getElementById("root")).render(<App />);
