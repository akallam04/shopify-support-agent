# v2 design: an agent that takes actions, measured by simulation

v1 is a read-only support agent with a 53-case single-turn eval suite. v2 adds safe write
actions and a tau-bench-style simulation harness that measures them over multi-turn
conversations with a simulated customer.

This document records the design decisions and the task list for each phase.

## Baseline when v2 started

Run `evals/results/20261004-025503_v2-phase0-baseline.json`, 53 cases, all live.

| metric | value |
|---|---|
| pass | 53/53 |
| intent accuracy | 100% |
| retrieval hit rate | 100% |
| tool success | 100% |
| refusal correctness | 100% |
| mean latency | 1.91s |
| p95 latency | 3.63s |
| cost per 100 conversations | $0.18 |

33 unit tests passed and ruff reported no issues.

## Design decisions

### Grading is on end state, not on the path taken

tau2's `docs/evaluation.md` is explicit that `evaluation_criteria.actions` is **one
reference trajectory**, not a checklist the agent has to reproduce. It is replayed on a
fresh database only to derive the target end state, which is then compared by hash. Any
path that reaches an equivalent end state scores full reward. The agent's tool calls are
compared to the reference list only when `RewardType.ACTION` is in `reward_basis`, which
the retail, airline, and telecom domains never do.

This grader copies that. Matching trajectories would punish correct-but-different solutions
and would break comparability with published numbers. Reference actions are recorded per
task, replayed to build the target state, and also reported as a diagnostic
(`partial_action_reward`, split by read tools versus write tools) without gating the score.

Final reward is the product of the components listed in the task's `reward_basis`. Default
basis is `[DB, COMMUNICATE]`.

### Shopify write mutations

These were checked against the 2026-01 docs before the API moved to 2026-10 in Phase 1.
Phase 2 re-checks each one against 2026-10 before building on it.

| tool | mutation | scope | facts that matter |
|---|---|---|---|
| `cancel_order` | `orderCancel` | `write_orders` | Irreversible. Runs asynchronously and returns a `Job`. Fails if the order is already cancelled, has pending payment authorizations, has active returns, or has fulfillments that cannot be cancelled. `reason` and `restock` are required. |
| `update_shipping_address` | `orderUpdate` | `write_orders` | The order must be unfulfilled. |
| `request_return` | `returnRequest` | `write_returns` | Creates a return with status `REQUESTED` that the merchant must approve or decline. This fits a support agent: the agent asks, a person decides. |
| `transfer_to_human` | none | none | Local handoff record, no Shopify call. |

`orderCancel` being asynchronous means the tool cannot report the final state from the
mutation response alone. It has to poll the job or re-read the order. The simulated store
treats it as a synchronous write, a deliberate simplification that the README's limitations
section will record.

### Two Shopify tokens

The store is a development store (`plan.partnerDevelopment = true`,
`aurora-outfitters-co.myshopify.com`).

- **Read token (`SHOPIFY_ADMIN_TOKEN`).** Held by the deployed Lambda. Every scope on this
  app is read-only, so the public demo cannot write to the store even if the agent is
  manipulated. Live writes stay off in the deployed demo.
- **Write-test token (`SHOPIFY_WRITE_TOKEN`).** A second custom app used only for live write
  testing and for reseeding test orders. It exists only in a developer's local `.env`.
  `deploy/sync_lambda_env.py` refuses to push it to the Lambda, under its own name or under
  any other name. Setup steps are in `docs/live-write-testing.md`.

### API version

The repo pinned API version 2026-01. Shopify supports each version for 12 months, so 2026-01
could lose support around January 2027. Phase 1 moved everything to 2026-10.

### Where session state lives

Lambda is stateless, and v2 needs two things to survive between HTTP requests: a pending
confirmation ("I am about to cancel #1001, do you confirm?") and, for the public demo, a
per-visitor sandbox copy of the store that write actions can safely mutate.

**Decision: a signed, client-held state token with an expiry. No new AWS infrastructure and
no new cost.**

This fits because the API is already client-state-driven. The frontend sends the entire
conversation history on every request and the backend holds nothing between calls. Adding
one signed `session_state` field to that round trip continues the existing design instead of
introducing a second, contradictory one.

The token carries the session id, an expiry, a nonce, the pending confirmation if there is
one, and the ordered list of sandbox mutations applied so far. The sandbox is rebuilt
deterministically as seed plus mutation list, so the token stays small. It is signed with
HMAC-SHA256 using `SESSION_SIGNING_KEY`, and the server rejects anything that fails the
signature or expiry check. Tokens expire 30 minutes after they are issued. A confirmation only
counts if it matches the hash of the exact pending action, so a stale or swapped token cannot
confirm a different write.

The token is signed, not encrypted, so anyone holding it can read it. It therefore carries
only what that visitor already knows about their own session: their pending action and their
own sandbox changes. It never holds server secrets, API tokens, or another customer's data.
Replaying a sandbox mutation is harmless because the sandbox belongs to that one visitor.

**The one case this does not cover** is a replayed write against the live store, where an
idempotency record has to live somewhere the client cannot forge. Live writes stay off in the
deployed demo, and the simulation runs in process with no storage at all. If live writes are
ever enabled in a deployed environment, the upgrade is one on-demand DynamoDB table (`pk`,
TTL attribute) holding `idem#<key>` records. That is new infrastructure and new IAM, so it
waits until something needs it.

### Deployment and container images

The Phase 1 code is not deployed yet. It ships once, when the public sandbox needs the
simulated store live. At that point:

- Images are tagged with the git SHA they were built from, and the Lambda is deployed by that
  tag, so the image the function uses always keeps a tag.
- An ECR lifecycle rule expires **untagged** images only. Tagged images, including the one the
  Lambda runs, are never expired, because a deleted image makes the function fail the next
  time Lambda reloads it.

The live Lambda already calls API 2026-10. Only its `SHOPIFY_API_VERSION` variable changed,
after a probe showed every query in the deployed code returns cleanly at 2026-10.

## Phases

### Phase 1: simulated store backend

- [x] Read tau2's retail domain (data model, tools, policy, tasks) and design the simulated
      database and hashing from it.
- [x] `StoreBackend` protocol with two implementations: `ShopifyAdminBackend` (live) and
      `SimStoreBackend` (in-memory JSON database, one fresh copy per conversation).
- [x] Read-only export script `scripts/export_store_snapshot.py` writing `data/sim/seed.json`.
      It refuses to run against anything but a development store, so real customer data can
      never land in the repo.
- [x] Frozen clock for simulations and the public sandbox.
- [x] Canonical database hashing (rules below).
- [x] MCP server selects its backend with `STORE_BACKEND=shopify|sim`.
- [x] Contract tests: 46 live comparisons, identical tool output from both backends.
- [x] Shopify API moved from 2026-01 to 2026-10.
- [x] `scripts/reseed_test_orders.py` for live write testing (see Phase 2).
- [x] `deploy/sync_lambda_env.py` to rotate a token into the Lambda without printing it.

#### Determinism rules for the simulated store

The same outcome must always produce the same hash, whatever order the agent did things in.

1. **Write tools never generate ids.** This follows tau2 retail, where every write changes
   fields on an existing record (`status`, `cancel_reason`, `return_items`, the address)
   instead of creating a new entity. A return request is recorded on its order, not as a new
   object with a fresh id.
2. **The only timestamps a write may record come from the frozen clock**, so they are the
   same on every run.
3. **The hash sorts every list**, so the order in which line items, fulfillments, or two
   return requests were recorded never changes it. No list in this domain carries meaning in
   its order, which makes this safe.
4. **The hash covers products, customers, and orders only.** Snapshot metadata (frozen time,
   notes) and side channels such as handoff summaries and the audit log stay outside it.
5. **The committed seed's hash is pinned in a test**, so the seed can only change on purpose.

#### Phase 1 findings

- **Missing scenarios in the store data.** No fulfilled order has a delivery date, no order has
  a shipping address, and no product is marked final sale. As the store stands, no order is
  return-eligible and the address tool has nothing to change. These scenarios are built on the
  store with the reseed script and then re-exported into the seed, so the simulated and live
  stores stay identical (see Phase 2).
- **Frozen time is 2026-08-12T16:00:00Z.** All 15 original orders were created within 43
  seconds on 2026-07-07, so the frozen time sits far enough after that for a delivery to be
  both after its order and more than 30 days old. This value is revisited when the new
  scenarios are built, since new orders are created at the real current date.
- **A v1 search bug, found by the contract tests.** Shopify's `products` connection sorts by
  `ID` unless asked otherwise, so `check_inventory` returned the three oldest matches, not the
  three most relevant. A query for "backpack" put a tent first because of its `backpacking` tag.
  Shopify's own `RELEVANCE` sort was no better: for "bottle" it ranked a camp chair (a match only
  in its description) above the bottle. Both backends now return up to 10 matches in explicit
  `ID` order, and shared tool code ranks products whose title matches the query first, keeping
  store order on ties. Shopify also matches product descriptions, so the seed stores them.
- **Deprecated fields removed.** Shopify flagged `Customer.email`, which the order lookup used to
  decide whether an email owns an order. It now reads `defaultEmailAddress.emailAddress`, checked
  to match on all 15 orders first. `ShopPlan.displayName` was replaced too. The client records
  Shopify's deprecation header, and the exporter prints it.
- **Exact order-name matching.** The live lookup used to take the first search hit for an order
  number. It now requires an exact name match, as the simulated store does.
- **`scripts/check_mcp.py` ignored `STORE_BACKEND`.** The MCP SDK starts servers with a minimal
  environment, so the script silently used the live store whatever was set. It now passes the
  environment through, and the server logs which backend it is using.

### Phase 2: safe write actions

- [ ] Re-check `orderCancel`, `orderUpdate` and `returnRequest` against the 2026-10 docs.
- [ ] Policy engine in code, with eligibility rules taken from `data/policies`: return
      window, final sale, gift cards, order state, identity match. Tools refuse with a
      structured reason. The model cannot bypass it.
- [ ] Four tools, built on the simulated store first: `cancel_order`,
      `update_shipping_address`, `request_return`, `transfer_to_human`. The live backend gets
      them behind a feature flag, development store only, using the write-test token.
- [ ] Mutation gate as graph nodes before any write, following SABER, switchable by config
      (`MUTATION_GATE=on|off`) so the Phase 4 ablation needs no refactor:
  - [ ] deterministic policy check
  - [ ] targeted reflection with only the relevant rules, the request, and the order facts
  - [ ] explicit confirmation held in graph state, classified yes / no / unclear, where
        unclear asks again and a change of mind cancels
  - [ ] idempotent execution keyed per action
  - [ ] audit log entry for every write
- [ ] Writes require order number and email match, extending the current authorization.
- [ ] Context cleaning for long conversations: summarize old turns, keep tool facts.
- [ ] Unit tests for every policy rule and every gate path.
- [ ] Build the missing scenarios on the development store with `scripts/reseed_test_orders.py`
      (dry run first, then `--apply`, using the write-test token): delivered orders inside and
      outside the 30-day window, orders with shipping addresses, and an order containing the
      final-sale product. Then re-export the seed and re-run the contract tests. `orderCancel`
      cannot be undone, so every live write test starts by topping up these fixtures. Used-up
      orders are left in place, never deleted.

### Phase 3: simulation harness

- [ ] `evals/sim/` with an agent policy document consistent with `data/policies`.
- [ ] Task schema mirroring tau2: id, user scenario (persona, reason for call, known info,
      unknown info, instructions), evaluation criteria (reference actions, `communicate_info`,
      optional natural-language assertions, forbidden actions, `reward_basis`).
- [ ] User simulator driven by a different model family than the agent, revealing
      information only when asked, with stop signals. Simulator errors tracked separately
      and rerun. Infrastructure errors count as failures.
- [ ] Orchestrator: turn-taking to a cap, a fresh `SimStoreBackend` per conversation, the
      graph called in process, full trajectory recorded (messages, tool calls, graph path,
      gate decisions, latency, tokens, cost).
- [ ] Grader: database hash against the target, `communicate_info` substring checks,
      forbidden-action checks, optional judge for natural-language assertions with tool
      outputs supplied. Reward is the product over `reward_basis`.
- [ ] **No-write tasks need a second check.** When the right answer is no write (a refusal,
      an out-of-window return, an identity mismatch), the target state equals the starting
      state, so an agent that does nothing at all passes the database check. Every such task
      must also carry `communicate_info` or a natural-language assertion that the agent said
      the right thing. A validation step fails the task file if one is missing.
- [ ] Keep tau2's action diagnostics: report how many reference actions the agent matched,
      split into read tools and write tools, without letting it gate the reward. This shows
      cases where the database check passed only because no write was attempted.
- [ ] Metrics: pass^1 to pass^k (k=4), per category, write precision and recall, unsafe
      write count (target 0), cost per resolved conversation, p50 and p95 latency, turns.
- [ ] Async runs with a concurrency limit and resume, results written to
      `evals/results/sim/<run-id>/` and never overwritten.
- [ ] About 50 tasks covering: order status; cancellation, eligible and not; address change,
      eligible and not; returns inside the window, outside it, and for a final-sale item;
      product and policy questions; multi-intent requests; a wrong email or identity mismatch;
      someone asking about another person's order; prompt injection mid-conversation;
      frustrated, vague, or rambling customers; a customer who says no or changes their mind
      at the confirmation step; handoff requests; and a customer with several orders where
      the agent must ask which one.
- [ ] Task validation: replay every reference trajectory through the policy engine, run a
      strong model once and review its failures for task bugs, fix or drop ambiguous tasks.
- [ ] Failure taxonomy with labels and manual spot checks.
- [ ] One command that turns a failing trajectory into a new regression task.
- [ ] The 53-case suite stays as the fast single-turn gate and must stay at 53/53.

### Phase 4: iterate and measure

- [ ] Baseline simulation run, plus the mutation gate on versus off ablation.
- [ ] Fix failures, preferring graph and code fixes over prompt edits. Re-run. Log before
      and after, separating agent fixes from task and harness fixes.
- [ ] Model comparison on pass^k and cost per resolved conversation, after checking the
      current Anthropic lineup.
- [ ] Prompt caching on the stable blocks, with the cost and latency change reported.
- [ ] Report counts as well as percentages, and do not over-claim on about 50 tasks.

### Phase 5: production polish

- [ ] GitHub Actions: lint, unit tests and gitleaks on push, a small simulation smoke run on
      manual trigger or nightly, artifacts uploaded, badge in the README.
- [ ] Release manifest: hash of prompts, model ids, policy and knowledge snapshot, git SHA,
      stamped on every response, log line, and eval run.
- [ ] Structured JSON logs with conversation and trace ids, per-node timing, tokens and cost.
- [ ] Public demo safety: sandbox writes per session, clear labelling, rate limits, a per
      session token cap, and a cap on total conversation size per request.
- [ ] Deploy as described under "Deployment and container images".
- [ ] Cold start measured, cheapest fix applied, before and after reported.

### Phase 6: website upgrade

- [ ] Audit the live site first: desktop and mobile screenshots, design critique, and an
      accessibility review, with the issue list recorded in `docs/site-audit.md`.
- [ ] Scenario chips, sandbox banner, confirmation card with working Confirm and Cancel,
      clear loading and retry states.
- [ ] "Inside the agent" panel: graph path, tool calls, gate and policy decisions, verify
      outcome, latency, tokens, cost, release hash.
- [ ] "How it works" page: headline numbers, pass^k curve, category breakdown, failure
      taxonomy, gate ablation, architecture diagram, step-by-step transcript viewer reading
      static JSON from the eval results.
- [ ] Responsive, keyboard accessible, WCAG AA contrast, meta tags and an OG image.

### Phase 7: documentation

- [ ] README rewrite led by what the agent does and the headline simulation numbers, with
      the updated architecture diagram, the eval method and its limitations, the before and
      after log, and a cost and latency table.
- [ ] A short write-up in `docs/` covering what was built, what broke, and what the numbers
      say.

## Working principles

- Every number published anywhere comes from a run saved in the repo.
- Agent fixes are reported separately from harness and task fixes.
- Paid runs print a cost estimate first and honour a `--max-usd` cap. Iterate on small
  subsets at k=2, full runs at k=4 only for final numbers.
- Write actions touch the development store only, never anything real.
