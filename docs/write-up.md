# An AI support agent that asks before it acts

This is a write-up of the second version of a customer support agent for a Shopify store: what I
built, what broke along the way, and what the numbers say. Every number here comes from a run
saved in `evals/results/`.

## Where it started

The first version was read-only. It answered product and policy questions with retrieval over the
store's catalog and policy pages, looked up orders through an MCP server I wrote over the Shopify
Admin GraphQL API, and refused or handed off anything else. A LangGraph state machine served by
FastAPI ran it, and a 53-case suite measured it. It passed all 53.

The second version had one goal: let the agent change orders (cancel, change the shipping address,
request a return, hand off to a person) and show, with numbers, that it does so safely.

## What I built

**One tool contract, two stores.** The tools run against either the live development store or a
simulated store built from a snapshot of it. Contract tests check that both return the same results.
The simulated store has a frozen clock, so "placed 30 minutes ago" or "delivered 5 days ago" never
goes stale, and its state hashes canonically, so a conversation can be graded on where the store
ended up.

**A policy engine in code.** The store's rules (the 2-hour window for cancelling or changing an
address, the 30-day return window, final sale, shipping state, US and Canada addresses) live in
`mcp_server/policy.py`. The tools refuse with a structured reason when a rule fails. The model
cannot talk its way past them.

**A gate in the graph.** When the model proposes a change, the graph does not run it. It checks the
proposal against the policy engine, renders the exact change from a template, and holds it in
graph state. The next customer message is read as yes, no, unclear, or something else. Only a clear
yes runs the change, once, with an idempotency key. The customer sees the change and the checks it
passed before they answer.

**A sandbox for the public demo.** Lambda keeps no state, so each visitor's private copy of the
store is rebuilt per request from the frozen snapshot plus a list of changes. That list, and any
pending change, travel in an HMAC-signed token the browser holds. A pending change is re-checked
against the rebuilt store before it can run, and a tampered token resets the session. The Lambda
function only ever holds a read-only Shopify token.

**A simulator to measure all of it.** I followed tau-bench. A different model (Qwen 3.8 Flash)
plays the customer from a written scenario against the real agent and a fresh copy of the store. The
grade combines the store's end state (compared with replaying the reference actions on another fresh
copy), the facts the agent had to give, assertions checked by a judge model with the tool outputs as
ground truth, and forbidden writes. A separate check reads every write, including in failed
conversations, and asks one question: did the customer clearly say yes to this?

## How I set up the comparison

The headline compares two settings of the same agent on the same 50 tasks, 4 tries each. With the
gate on, confirmation is enforced in code. With the gate off, the prompt tells the model to describe
the change and wait for a yes, and nothing enforces it. That is a fair baseline: it is how most
agents ask for confirmation.

I report pass^k, the chance that all k tries of a task succeed, and paired bootstrap intervals over
tasks for every difference. Before the first fix, I wrote ten more tasks in the same style and
committed them, and ran them only after the code was frozen. After the headline, I read a random 10
percent of passing conversations and every gate-off pass that made a change, to check the grader.

The two headline runs cost $6.66 in Claude calls for 400 conversations, agent and judge together.

## What broke

**The prompt-only baseline made changes nobody agreed to.** Asked in the prompt, the model most
often asked the customer for a reason and then made the change, treating the reason as consent. In
one task the customer was going to say no at the confirmation; with the gate off, the order was
cancelled before they could. Over 200 conversations: 39 writes without a clear yes, 8 forbidden
writes, 42 conversations with at least one.

**My harness overspent and miscounted.** The spend cap only blocked new conversations, so a run whose
real cost beat the estimate overshot ($0.27 against a $0.17 cap); it now charges after every turn.
Midway through the headline, the account hit its monthly API usage limit. The runner recorded 25
refused calls as agent failures, and the live demo answered with a server error. Billing refusals now
stop a run without recording anything, and cut conversations rerun on resume. The demo now says it
is resting instead of failing, and a daily check calls it and emails me when it breaks.

**My grader had bugs, and fixing them made my result smaller.** The yes-check judge first saw raw
tool arguments, so it flagged a return the customer had approved as "1 x Sierra Sun Hoody (M)"
because the call named the size that arrived. Then it flagged returns the customer had agreed to
because the agent never mentioned the return fee. The audit found 19 wrong verdicts in 88, all on
gate-off returns. With the judge fixed and every saved conversation regraded, the gate's lead in
resolved safely fell from +0.285 to +0.195. The fix went in anyway, and both numbers are published.

**The simulator found real agent bugs.**

- The grounding check rejected order numbers from the agent's own earlier reply, and the retry
  prompt then sent the customer to email support.
- The reflection check asked why the customer wanted an address change, which needs no reason.
- In 80 of 614 saved gate-on conversations, the model asked for a yes itself right before the gate
  did, so customers confirmed twice. A draft that asks for a yes now goes back to the model once.
- Refusals were handed to the support team instead of explained. The transfer tool's own
  description said to use it for "exceptions to store policy", and the router sent an unsupported
  address to handoff.
- The agent told a customer "it hasn't shipped yet, so I can update the address" before the 2-hour
  window refused it. The status tool showed when the order was placed, but not what the clock
  allowed. It now carries what the policy engine allows right now.
- A return could be filed with a reason the customer never gave. Returns now need the customer's own
  words for the reason, checked against what they wrote. The first version of that rule asked every
  customer why they were cancelling too, in 5 of 5 cancellations, although the store needs no cancel
  reason. Cancel reasons are now optional and never guessed.

**Cold starts.** The first deploy of the new version made cold starts worse. The container copied a
167 MB embedding model into `/tmp` on every start, which sometimes ran into Lambda's 10-second init
limit, and the first retrieval on a new instance took up to 24 seconds while image layers loaded
lazily. Linking the model read-only and reading it in the background brought the first retrieval to
0.75 to 1.1 seconds. The first cold start on a newly deployed image still took 13.5 to 26.9 seconds
across three deploys, so the deploy script now warms the function before any visitor arrives.

**The interface told the wrong story.** On a phone, tapping Yes rewrote the confirmation card above
to "Confirmed", while the customer's "Yes" landed below it. Read top to bottom, the agent seemed to
act before the yes, the opposite of the point. The card now keeps its proposal with a small "You
said yes", and the result arrives as a new message after the reply.

## What the numbers say

- **The gate did not cost resolution.** Gate on resolved 190 of 200 and gate off 185, a difference
  of +0.025 [-0.045, +0.105]. The two are level.
- **The gate removed unsafe changes.** Conversations with an unsafe write went from 42 to 0, and safe
  pass^1 went from 0.755 to 0.950, +0.195 [+0.090, +0.310].
- **Confirmation does the work.** Turned off one part at a time on the 32 tasks with a proposed
  change, removing confirmation brought back 20 writes without a yes and 4 forbidden writes.
  Removing the reflection check, a second model call that compares the change with the request,
  changed nothing measurable, so it is off by default now. The SABER paper found reflection helped
  in its retail setting; here the policy engine and the confirmation leave it little to catch.
- **The held-out tasks held.** 40 of 40 resolved safely. Ten tasks is a small sample.
- **My fixes did not move the overall pass rate.** 94 of 100 before and 93 after on the same tasks,
  -0.010 [-0.060, +0.040]. The targeted failures went away and write recall rose from 0.895 to 0.974,
  but other tasks failed some tries instead.
- **The bigger model was not clearly better.** Sonnet 5.5 resolved 39 of 40 safely against Haiku
  4.5's 36, +0.075 [-0.025, +0.175], at twice the median turn latency. It cost less per resolved
  conversation only because its prompts are cached.

What I would claim from this: enforcing confirmation in code removed unsafe writes in these tasks
without lowering the resolution rate. What I would not claim: that the agent is better than another
agent, or that small differences between my agent versions are real. With 50 tasks, most of those
intervals cross zero.

## What I would do next

Run a small simulation on every pull request with a budget cap, turn real chat logs into
regression tasks the way saved simulation runs already are, and try a second store's policies to
see how much of the policy engine carries over.
