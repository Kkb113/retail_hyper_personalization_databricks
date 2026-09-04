# Phase 1 codebase review

Reviewed 2026-09-04, starting from the curated Azure-only main commit
`1eb6a68bb42ca223df9657fdd0481a1a33ad2dbd`.

## Findings and implementation decisions

| Finding | Evidence at start | Phase 1 resolution |
| --- | --- | --- |
| No installable runtime | `pyproject.toml` declared `packages = []` | Explicit Azure src-layout package discovery; wheel-only clean-import smoke tests |
| No Azure bundle | Root legacy bundle is ignored | New subtree bundle, one `poc` target, fixed verified host, no resources; marker-only sync eligibility |
| Scope was documentation only | Phase 0 scope contract and historical inventory | Strict typed config, independent trust anchors, account-first preflight, resolved-bundle checks |
| Budget policy stale | Cost files still requested threshold/recipient | Record INR 12,000/9,000/3,000 and two private recipients; keep deployment blocked |
| No control-plane safety tests | 17 Phase 0 integrity tests | Negative tests for wrong scope, tag removal, enabled features, overrides and injected resources/hooks |
| Legacy local files can mask bugs | Ignored `pytest.ini`, `src`, `agent`, app copies remain on this workstation | Explicit pyproject test configuration, no legacy imports, installed-wheel tests with Python isolated mode |
| Loose development dependency list | Three direct pins without transitive hashes | Hashed universal development and four foundation runtime locks |
| CI did not test a runtime | Phase 0 lint/integrity only | Type checking, schema parity, app health tests, four runtime install matrix, required aggregate check retained |

The 45-file migration payload and model hash contracts remain byte-for-byte
unchanged. Phase 0's selected adaptive router and synthetic evaluation metrics
are preserved. No model unpickling or model-serving claim is part of this phase.

## Deliberate limitations

- Existing Premium workspace is retained; no upgrade, paid add-on, compute or
  external service is created. Workspace tags are observed, not mutated.
- Phase 1 runtime locks intentionally do not assert serialized-model compatibility.
  Phase 5 must package actual retrieval/ranking/routing/business rules, not merely
  return the frozen recommendation snapshot.
- App skeleton exposes health/version only and reports not-ready. It has no fake
  recommendations, LLM calls, cloud credentials or authentication implementation.
- The production release remains `HOLD_PENDING_FUTURE_HOLDOUT`.
- Source/CLI guards are not a replacement for Azure RBAC, policy, budgets or a
  shutdown controller. These remain later gates before billable deployment.
- Old Phase 0 evidence remains historical; current decisions live in `config/poc.json`.

## Issues caught during verification

- The official CLI rejected `= 1.15.0` as a version constraint. Changed to an
  inclusive lower/upper bound for exactly the pinned version; the live check
  must pass, not just YAML parsing.
- Existing Windows pytest temporary/cache directories had access restrictions.
  Tests use a new workspace-owned temporary path rather than altering/deleting
  unrelated directories.
- PyYAML permits duplicate mapping keys by default. The bundle loader now rejects
  duplicates, preventing ambiguous configurations between tools.
- Python treats booleans as numbers for equality. Strict policy validation also
  checks literal types, so `0` cannot masquerade as the `false` feature flag.
- Strict live validation warned that a Shared bundle root would be writable by
  all workspace users. Use a private per-user root resolved at runtime; never
  commit the actual user name. Strict validation also requires a non-empty file
  set, so only a fixed harmless marker is eligible for future sync. No upload is
  performed by validation or planning.
