# v2 plan: an agent that takes actions, measured by simulation

v1 is a read-only support agent with a 53-case single-turn eval suite. v2 adds safe write
actions and a tau-bench-style simulation harness that measures them over multi-turn
conversations with a simulated customer.

This plan was written at the end of Phase 0 and records the decisions made there. Phases 1
to 7 below are the task list.

## Baseline on the day v2 started

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

33 unit tests pass, ruff clean, 57 commits all authored by Arun Teja Reddy Kallam with no
AI attribution anywhere in history.

## Phase 0 findings that shape the design

### Grading is on end state, not on the path taken

tau2's `docs/evaluation.md` is explicit that `evaluation_criteria.actions` is **one
reference trajectory**, not a checklist the agent has to reproduce. It is replayed on a
fresh database only to derive the target end state, which is then compared by hash. Any
path that reaches an equivalent end state scores full reward. The agent's tool calls are
compared to the reference list only when `RewardType.ACTION` is in `reward_basis`, which
the retail, airline, and telecom domains never do.

Our grader copies this. Matching trajectories would punish correct-but-different solutions
and would break comparability with published numbers. Reference actions are recorded per
task, replayed to build the target state, and also reported as a diagnostic
(`partial_action_reward`, split by read tools versus write tools) without gating the score.

Final reward is the product of the components listed in the task's `reward_basis`. Default
basis is `[DB, COMMUNICATE]`.

### Shopify write mutations, verified against the docs for API version 2026-01

| tool | mutation | scope | facts that matter |
|---|---|---|---|
| `cancel_order` | `orderCancel` | `write_orders` | Irreversible. Runs asynchronously and returns a `Job`. Fails if the order is already cancelled, has pending payment authorizations, has active returns, or has fulfillments that cannot be cancelled. `reason` and `restock` are required. |
| `update_shipping_address` | `orderUpdate` | `write_orders` | The order must be unfulfilled. |
| `request_return` | `returnRequest` | `write_returns` | Creates a return with status `REQUESTED` that the merchant must approve or decline. This is the right shape for a support agent: the agent asks, a human decides. |
| `transfer_to_human` | none | none | Local handoff record, no Shopify call. |

The store is confirmed a development store (`plan.partnerDevelopment = true`,
`aurora-outfitters-co.myshopify.com`). The app currently holds 10 scopes and every one of
them is read-only, so no write is possible today even by accident.

**Scopes to enable before Phase 2 can touch the live store (Arun does this, not Claude):**
`write_orders`, `write_returns`, `read_returns`.

Note that `orderCancel` being asynchronous means the tool cannot report the final state
from the mutation response alone. It has to poll the job or re-read the order. The sim
backend models this as a synchronous write, which is a deliberate simplification to record
in the limitations section of the README.

The repo pins API version 2026-01 while 2026-10 is current. 2026-01 is still supported and
documented. Upgrading is optional and out of scope for v2 unless a needed field is missing.

### Where session state lives

Lambda is stateless, and v2 needs two things to survive between HTTP requests: a pending
confirmation ("I am about to cancel #1001, do you confirm?") and, for the public demo, a
per-visitor sandbox copy of the store that write actions can safely mutate.

**Decision: a signed, client-held state token. No new AWS infrastructure, no new cost.**

The reason this fits is that the API is already client-state-driven. The frontend sends the
entire conversation history on every request and the backend holds nothing between calls.
Adding one signed `session_state` field to that same round trip continues the existing
design instead of introducing a second, contradictory one.

The token carries the session id, an expiry, a nonce, the pending confirmation if there is
one, and the ordered list of sandbox mutations applied so far. The sandbox is reconstructed
deterministically as seed plus mutation list, so the token stays small (a handful of
mutations, not a copy of the database). It is signed with HMAC-SHA256 using a new secret
`SESSION_SIGNING_KEY`, and the server rejects anything that fails the signature or the
expiry check. A confirmation only counts if it matches the hash of the exact pending action,
so a stale or swapped token cannot confirm a different write.

Replay of a sandbox mutation is harmless because the sandbox belongs to that one visitor.

**The one case this does not cover** is a replayed write against the live dev store, where
an idempotency record has to live somewhere the client cannot forge. Live writes are behind
a feature flag that stays off in the deployed demo, and Phases 1 to 4 run the simulation
in-process with no storage at all. So the upgrade path is written down and not built:
if live writes are ever enabled in a deployed environment, add one DynamoDB table
(`pk`, on-demand, TTL attribute) holding `idem#<key>` records. That stays inside the
free tier at demo volume, but it is new infrastructure and new IAM, so it waits until
something actually needs it.

## Phases

### Phase 1: simulated store backend

- [ ] `StoreBackend` protocol with two implementations: `ShopifyAdminBackend` (the existing
      live client) and `SimStoreBackend` (local JSON database).
- [ ] Read-only export script that snapshots the dev store (products, variants, inventory,
      customers, orders with fulfillment and payment states, dates) to a seed file.
- [ ] Frozen clock for simulations and the public sandbox, so return-window cases do not
      rot as real time passes.
- [ ] Canonical database hashing for grading: stable key ordering, volatile fields excluded.
- [ ] MCP server selects its backend by config.
- [ ] Contract tests proving both backends return the same shapes for the read tools.

### Phase 2: safe write actions

- [ ] Policy engine in code, with eligibility rules read from `data/policies`: return
      window, final sale, gift cards, order state, identity match. Tools refuse with a
      structured reason. The model cannot bypass it.
- [ ] Four tools on the sim backend first: `cancel_order`, `update_shipping_address`,
      `request_return`, `transfer_to_human`. Live backend behind a feature flag, dev store
      only.
- [ ] Mutation gate as graph nodes before any write, following SABER:
  - [ ] deterministic policy check
  - [ ] targeted reflection with only the relevant rules, the request, and the order facts
  - [ ] explicit confirmation held in graph state, classified yes / no / unclear, where
        unclear asks again and a change of mind cancels
  - [ ] idempotent execution keyed per action
  - [ ] audit log entry for every write
- [ ] Writes require order number and email match, extending the current authorization.
- [ ] Context cleaning for long conversations: summarize old turns, keep tool facts.
- [ ] Unit tests for every policy rule and every gate path.

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
- [ ] Metrics: pass^1 to pass^k (k=4), per category, write precision and recall, unsafe
      write count (target 0), cost per resolved conversation, p50 and p95 latency, turns.
- [ ] Async runs with a concurrency limit and resume, results written to
      `evals/results/sim/<run-id>/` and never overwritten.
- [ ] About 50 tasks covering the categories listed in the brief.
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

- [ ] GitHub Actions: lint and unit tests on push, a small simulation smoke run on manual
      trigger or nightly, artifacts uploaded, badge in the README.
- [ ] Release manifest: hash of prompts, model ids, policy and knowledge snapshot, git SHA,
      stamped on every response, log line, and eval run.
- [ ] Structured JSON logs with conversation and trace ids, per-node timing, tokens and cost.
- [ ] Public demo safety: sandbox writes per session, clear labelling, rate limits, per
      session token cap.
- [ ] Cold start measured, cheapest fix applied, before and after reported.

### Phase 6: website upgrade

- [ ] Audit the live site first: desktop and mobile screenshots, design critique,
      accessibility review, issue list in the phase summary.
- [ ] Scenario chips, sandbox banner, confirmation card with working Confirm and Cancel,
      clear loading and retry states.
- [ ] "Inside the agent" panel: graph path, tool calls, gate and policy decisions, verify
      outcome, latency, tokens, cost, release hash.
- [ ] "How it works" page: headline numbers, pass^k curve, category breakdown, failure
      taxonomy, gate ablation, architecture diagram, step-by-step transcript viewer reading
      static JSON from the eval results.
- [ ] Responsive, keyboard accessible, WCAG AA contrast, meta tags and an OG image.

### Phase 7: documentation and resume

- [ ] README rewrite led by what the agent does and the headline simulation numbers, with
      the updated architecture diagram, the eval method and its limitations, the before and
      after log, and a cost and latency table.
- [ ] A short honest write-up in `docs/` suitable for a LinkedIn post.
- [ ] Three resume bullets using only numbers from saved runs, plus the keyword list.

## Ground rules carried through every phase

- Commit and push to `main`, authored by Arun Teja Reddy Kallam, never any AI attribution.
- Every number published anywhere comes from a run saved in the repo.
- Agent fixes are reported separately from harness and task fixes.
- Paid runs print a cost estimate first and honour a `--max-usd` cap. Iterate on small
  subsets at k=2, full runs at k=4 only for final numbers.
- Write actions touch the development store only, never anything real.
