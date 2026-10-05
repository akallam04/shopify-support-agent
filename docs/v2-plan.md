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

v2 shipped in Phase 5 with the public sandbox:

- Images are tagged with the git SHA they were built from, and the Lambda is deployed by that
  tag, so the image the function uses always keeps a tag.
- An ECR lifecycle rule expires **untagged** images only. Tagged images, including the one the
  Lambda runs, are never expired, because a deleted image makes the function fail the next
  time Lambda reloads it.

The live Lambda already calls API 2026-10. Only its `SHOPIFY_API_VERSION` variable changed,
after a probe showed every query in the deployed code returns cleanly at 2026-10.

### Public API limits

API Gateway throttles the stage at a burst of 5 and 0.5 requests per second sustained,
enforced best-effort by AWS, and the account's Lambda concurrency limit of 10 caps parallel
work. Reserved concurrency cannot go lower because AWS keeps at least 10 unreserved. Throttled
responses carry no CORS headers, so the frontend treats them like network errors: two retries
with backoff, then a busy message with a retry button. Details are in `deploy/README.md`.

## Budget

Model API spend for Phases 2 to 4 is planned around roughly $12 in total.

- A reserve of at least $1 is always left unspent so the live demo keeps answering.
- Phase 2 is mostly code and unit tests against stubbed model clients. Its paid runs stay
  small: the 53-case regression suite (about $0.19 a run) once at the end of the phase, plus
  a few short write-flow conversations.
- The user simulator, and any other role where the model choice does not change the result,
  runs on an OpenAI-compatible provider with free credits (Qwen Cloud or Nebius).
- Before Phase 3 runs anything large, a run plan with cost estimates covers Phases 3 and 4:
  harness bring-up, task validation, the baseline, gate on versus off, a model comparison on a
  subset, and the final run. The gate on versus off comparison is the headline result, so its
  budget is reserved first.
- Any single run estimated above $1 needs approval before it starts.
- AWS spend has a budget alert at $5 a month.
- The project stays API-only, with no GPU training and no locally hosted models.

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

- [x] Re-checked against 2026-10: `orderCancel(orderId, reason, restock, refundMethod, notifyCustomer,
      staffNote)` with the deprecated `refund` argument removed; `orderUpdate(input: OrderInput)`
      whose `shippingAddress` overwrites the existing one; `returnRequest` now identifies items by
      `fulfillmentLineItemId` with an optional `returnReasonDefinitionId`. The simulated store
      records fulfillment line item ids so it can model returns the same way.
- [x] Policy engine in code (`mcp_server/policy.py`), with every customer-facing reason taken
      from `data/policies`: the 2-hour window and not-yet-shipped rule for cancellations and
      address changes, United States and Canada only, the 30-day return window from delivery,
      final sale and gift cards, quantities already in a return, and the return fee and label
      terms. It reads the time from the backend's clock, frozen in the simulated store.
- [x] Four tools on the simulated store: `cancel_order`, `update_shipping_address`,
      `request_return`, `transfer_to_human`, with `prepare` (validate, authorize, check policy,
      summarize) and `execute` (check again, apply once per idempotency key, audit).
- [x] Writes require the order number and the email on the order. A wrong email gets the same
      not-found answer as a missing order.
- [x] Mutation gate as graph nodes before any write, following SABER, switchable by config:
  - [x] deterministic policy check
  - [x] targeted reflection with only the relevant rule, the customer's messages, and the order facts
  - [x] explicit confirmation held in graph state, classified confirm / decline / unclear / change,
        where unclear asks again and a change of mind drops the pending action
  - [x] idempotent execution keyed per action
  - [x] audit log entry for every write, refusal, and replay
- [x] Context cleaning for long conversations.
- [x] Unit tests for every policy rule and every gate path: 165 offline tests, with mutation checks
      showing the gate tests fail when the gate is broken.
- [x] Live backend write primitives (`orderCancel`, `orderUpdate`, `returnRequest`) behind
      `WRITE_ACTIONS`, development store only, using the write-test token. The backend refuses to
      build writes without that token or on anything but a development store, and a Shopify error
      becomes a "nothing was changed" answer plus an audit entry.
- [x] Live writes checked against the simulated store with `scripts/live_write_check.py`: all nine
      cases agree after one fix (reports in `evals/results/live-writes/`).
- [x] Built the missing scenarios on the development store (orders #1016 to #1023), re-exported the
      seed with the printed anchor as the frozen time and with live returns included, and re-ran the
      contract tests: 54/54. Steps are in `docs/live-write-testing.md`.

#### Phase 2 design

- **The model proposes, the graph disposes.** When the order model emits a write tool call and
  the gate is on, the graph holds it back as a candidate instead of executing it, as SABER's
  mutation-gated verification does.
- **Policy is always enforced, gate or no gate.** The tools check policy themselves, so turning
  the gate off never allows a write that breaks store rules. The switches control only the SABER
  safeguards: `MUTATION_GATE` (hold writes back at all), `GATE_REFLECTION`, and
  `GATE_CONFIRMATION`. Each component has its own switch because SABER's Retail ablation was not
  additive (66.9% without safeguards, 80.8% with reflection alone, 80.5% with verification
  alone, 77.7% with both), so Phase 4 measures them separately here.
- **Confirmations, refusals, and results are templates,** built from the prepared action and the
  policy's reasons. They cost nothing, are grounded by construction, and hostile input cannot
  steer their wording. Clear yes and no replies are matched by pattern; only ambiguous replies
  reach the model.
- **Idempotency** keys hash the action and its normalized arguments. A replay returns the first
  result without writing again. Refusals are not cached, since a refused action can become
  allowed later. Policy is checked again at execution time, in case time or state moved on after
  the customer confirmed.
- **Context cleaning is a deterministic digest:** recent messages stay verbatim, and older ones
  fold into the order numbers and emails the customer gave, their earlier requests, and the
  actions already completed. SABER summarizes blocks with an auxiliary model; support chats here
  are short, so a model call per turn is not worth its cost yet.
- **The public API stays read-only for now.** `WRITE_ACTIONS` is off by default, and the graph
  refuses to start if the MCP client exposes write tools, since only the in-process executor can
  run the gate. The simulation harness runs the graph in process and carries the pending action
  between turns itself. The signed session token that will carry it over HTTP ships with the
  public sandbox in Phase 5.
- **Cancellations do not restock inventory,** live (`restock: false`) or simulated, so the two
  stores stay comparable. The simulated cancellation records what Shopify records: a paid order
  becomes `REFUNDED` and its fulfillment status `FULFILLMENT_NOT_REQUIRED`.

#### Phase 2 findings

- **Published policy sets a 2-hour window** for cancellations and address changes ("within 2 hours
  of it being placed, provided it has not entered processing"), and shipping goes to the United
  States and Canada only. The policy engine enforces exactly that, so the agent never acts against
  what the policy documents tell customers. The reseed fixtures moved accordingly: every fixture
  time is an offset from one anchor, which also becomes the simulated store's frozen time.
- **A real-model smoke test found two agent bugs** (`scripts/smoke_write_flow.py`, three staged
  conversations, about $0.02 a run). The model named a return item the way the customer did
  ("rain jacket"), which an exact title match rejected, so a final-sale refusal came out as
  "the order does not include that item". And on orders with no shipping address on file, a new
  address had no recipient name, so reflection stopped to ask for one. Both were fixed in code,
  with regression tests that fail without the fix: return items now match when every word the
  customer used fits exactly one item on the order, and a new address takes the customer's name
  when the order has none. After the fixes all three conversations reach the right outcome.
- **Shopify keeps backdated delivery events.** The reseed script creates the oldest delivery first
  and reads it back; every delivery time matched the request to the second, so the out-of-window
  return exists on the live store too.
- **The live check found one bug, in the return call.** The 2026-10 schema lists the reason on a
  return line as optional, but Shopify rejected the request with "Return reason can't be blank".
  The old `returnReason` field is deprecated; the current `returnReasonDefinitionId` points into
  Shopify's reason library, so the agent's nine reasons map to library handles (`too-small`,
  `changed-my-mind`, and so on), looked up by handle at run time. The first run recorded the
  failure; after the fix the live return matched the simulated one exactly.
- **Calibration.** After a live cancellation Shopify showed `REFUNDED` and
  `FULFILLMENT_NOT_REQUIRED`; the simulated cancellation now records the same.
- **Shopify's query cost limit.** Exporting orders with their returns nested inside cost 1,416
  points against a limit of 1,000 per query, so that export uses pages of 10 orders.
- **The read-only agent is unchanged:** the 53-case suite passed 53/53 after the gate was added
  (`evals/results/20261004-152330_v2-phase2-regression.json`).

### Phase 3: simulation harness

- [x] `evals/sim/` with an agent policy document consistent with `data/policies`.
- [x] Task schema mirroring tau2: id, user scenario (persona, reason for call, known info,
      unknown info, instructions), evaluation criteria (reference actions, `communicate_info`,
      optional natural-language assertions, forbidden actions, `reward_basis`).
- [x] User simulator driven by a different model family than the agent, revealing
      information only when asked, with stop signals. Simulator errors tracked separately
      and rerun. Infrastructure errors count as failures.
- [x] Orchestrator: turn-taking to a cap, a fresh `SimStoreBackend` per conversation, the
      graph called in process, full trajectory recorded (messages, tool calls, graph path,
      gate decisions, latency, tokens, cost).
- [x] Grader: database hash against the target, `communicate_info` substring checks,
      forbidden-action checks, optional judge for natural-language assertions with tool
      outputs supplied. Reward is the product over `reward_basis`.
- [x] **No-write tasks need a second check.** When the right answer is no write (a refusal,
      an out-of-window return, an identity mismatch), the target state equals the starting
      state, so an agent that does nothing at all passes the database check. Every such task
      must also carry `communicate_info` or a natural-language assertion that the agent said
      the right thing. A validation step fails the task file if one is missing.
- [x] Keep tau2's action diagnostics: report how many reference actions the agent matched,
      split into read tools and write tools, without letting it gate the reward. This shows
      cases where the database check passed only because no write was attempted.
- [x] Metrics: pass^1 to pass^k (k=4), per category, write precision and recall, unsafe
      write count (target 0), cost per resolved conversation, p50 and p95 latency, turns.
- [x] Async runs with a concurrency limit and resume, results written to
      `evals/results/sim/<run-id>/` and never overwritten.
- [x] About 50 tasks covering: order status; cancellation, eligible and not; address change,
      eligible and not; returns inside the window, outside it, and for a final-sale item;
      product and policy questions; multi-intent requests; a wrong email or identity mismatch;
      someone asking about another person's order; prompt injection mid-conversation;
      frustrated, vague, or rambling customers; a customer who says no or changes their mind
      at the confirmation step; handoff requests; and a customer with several orders where
      the agent must ask which one.
- [x] Task validation: replay every reference trajectory through the policy engine, run a
      strong model once and review its failures for task bugs, fix or drop ambiguous tasks.
- [x] Failure taxonomy with labels and manual spot checks.
- [x] One command that turns a failing trajectory into a new regression task.
- [x] The 53-case suite stays as the fast single-turn gate and must stay at 53/53.

### Phase 4: iterate and measure

- [x] Baseline simulation run, plus the mutation gate on versus off ablation and the gate-parts
      measurement (reflection off, confirmation off).
- [x] Fix failures, preferring graph and code fixes over prompt edits. Re-run. Log before
      and after, separating agent fixes from task and harness fixes (docs/fix-log.md). Ten
      held-out tasks, committed before the first fix, run once on the frozen code.
- [x] Model comparison on pass^k and cost per resolved conversation, after checking the
      current Anthropic lineup.
- [x] Prompt caching on the stable blocks, with the cost change reported (57 percent of Sonnet
      5.5's agent cost). The latency change was not measured separately.
- [x] Report counts as well as percentages, and do not over-claim on about 50 tasks. Grader
      audit of 114 passing conversations, with grading fixes regraded and both numbers kept.

### Phase 5: production polish

- [x] GitHub Actions: lint, unit tests and gitleaks on push, a small simulation smoke run on
      manual trigger only (no scheduled paid runs), its results uploaded, badges in the README.
- [x] Release manifest: hash of prompts, model ids, policy and knowledge snapshot, git SHA, and
      gate switches, stamped on every response, log line, and eval run.
- [x] Structured JSON logs with trace and session ids, per-node timing, tokens and cost, and no
      message text.
- [x] Public demo safety: sandbox writes per session, labelling on the page (a fuller banner
      comes in Phase 6), API Gateway rate limits, a per-session token cap, and a cap on total
      conversation size per request.
- [x] Signed session token carrying the pending action and the sandbox mutations over HTTP, and
      the API switched to the in-process executor with a per-session sandbox (`API_MODE=sandbox`).
- [x] Deploy as described under "Deployment and container images": images tagged by commit,
      the ECR rule expiring untagged images only, and the function holding only the read-only
      Shopify token.
- [x] Cold start measured, cheapest fix applied, before and after reported (`deploy/README.md`).
- [x] When the model API refuses or is unavailable (billing, usage limits, outages), the API
      returns a typed error with CORS headers and the demo shows a friendly "the demo is resting,
      try again later" message instead of a raw 500.
- [x] A daily GitHub Actions check sends one real chat message to the public demo endpoint and
      fails unless it gets a proper answer, so a broken demo produces an email. It calls only
      the public endpoint and needs no secrets.
- [x] Gate reflection off by default, with the switch kept, decided from the gate-parts runs.
- [ ] Agent issues found by the Phase 4 runs, fixed and measured on fresh runs, never by
      changing the frozen headline:
      - [x] The confirmation text ended with a doubled period.
      - [x] A decline that also asks for something else dropped the second request.
      - [x] The canned handoff reply repeated word for word.
      - [x] The return confirmation did not show the return reason.
      - [x] The order list showed only "fulfilled", and the agent sometimes called it delivered.
      - [ ] Some refusals (an unsupported country, a final-sale item) are handed off instead of
            explained.
      - [ ] The agent sometimes calls an order eligible before the policy check refuses it.
      - [ ] After a corrected detail at confirmation, the model sometimes asks for a yes itself
            before the gate asks again.

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
