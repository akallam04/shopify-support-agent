const REDUCED_MOTION = window.matchMedia("(prefers-reduced-motion: reduce)");
const WRITE_TOOLS = { cancel_order: "cancel", update_shipping_address: "address change", request_return: "return" };
const INTENT_NOTES = {
  product: "product question",
  policy: "policy question",
  order: "order request",
  smalltalk: "small talk",
  handoff: "handoff",
  out_of_scope: "out of scope",
};

const STEP_NODES = {
  guard: ["sanitize", "context"],
  route: ["route"],
  lookup: ["retrieve", "order_tools", "handoff"],
  gate: ["gate", "confirm", "execute"],
  verify: ["verify"],
  reply: ["respond"],
};

const STEP_COSTS = { route: ["route"], lookup: ["order_tools"], gate: ["confirm", "reflect"], reply: ["respond"] };

function el(tag, attrs = {}, children = []) {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(attrs)) {
    if (key === "text") node.textContent = value;
    else if (key === "class") node.className = value;
    else node.setAttribute(key, value);
  }
  for (const child of [].concat(children)) {
    if (child !== null && child !== undefined && child !== false) node.append(child);
  }
  return node;
}

function svg(markup, cls = "") {
  const span = document.createElement("span");
  span.innerHTML = `<svg viewBox="0 0 24 24" aria-hidden="true" class="${cls}">${markup}</svg>`;
  return span.firstChild;
}

function prettyDoc(id) {
  return id.replace(/^policy-/, "").replace(/-/g, " ");
}

function toolChip(tool) {
  const a = tool.args || {};
  if (tool.kind === "policy_check") return { step: "gate", text: `policy check: ${WRITE_TOOLS[tool.name] || tool.name}` };
  if (tool.name === "get_order_status") return { step: "lookup", text: `order status ${a.order_number || ""}`.trim() };
  if (tool.name === "list_customer_orders") return { step: "lookup", text: "order list" };
  if (tool.name === "check_inventory") return { step: "lookup", text: `stock: ${a.product_query || ""}`.trim() };
  if (tool.name === "transfer_to_human") return { step: "lookup", text: "handoff recorded" };
  if (WRITE_TOOLS[tool.name]) {
    const ok = typeof tool.result === "object" && tool.result && tool.result.ok;
    return { step: "gate", text: `${WRITE_TOOLS[tool.name]} ${a.order_number || ""} ${ok ? "done" : "refused"}`.trim() };
  }
  return { step: "lookup", text: tool.name.replace(/_/g, " ") };
}

function gateModel(trace, pending) {
  const steps = trace.gate || [];
  const last = (name) => [...steps].reverse().find((g) => g.step === name);
  const policy = last("policy");
  const reason = last("reason_check");
  const confirmation = last("confirmation");
  if (steps.some((g) => g.step === "executed")) return { state: "done", note: "ran after your yes" };
  if (steps.some((g) => g.step === "refused")) return { state: "stop", note: "store refused" };
  if (pending) return { state: "wait", note: "waiting for your yes" };
  if (confirmation && confirmation.label === "decline") return { state: "done", note: "nothing changed" };
  if (policy && !policy.allowed) return { state: "stop", note: "refused by policy" };
  if (reason && !reason.found) return { state: "hold", note: "asked for the reason" };
  return { state: "done", note: "checked" };
}

function stepModel(data) {
  const t = data.trace;
  const ran = new Set(t.path);
  const blocked = t.blocked;
  const steps = [];
  steps.push({ key: "guard", name: "Guard", ...(blocked ? { state: "block", note: "blocked" } : { state: "done", note: "passed" }) });
  steps.push({ key: "route", name: "Route", ...(ran.has("route") ? { state: "done", note: INTENT_NOTES[data.intent] || data.intent } : { state: "skip", note: "" }) });
  let lookup = { state: "skip", note: "" };
  if (ran.has("retrieve")) lookup = { state: "done", note: data.intent === "product" ? "catalog" : "policies" };
  else if (ran.has("order_tools")) lookup = { state: "done", note: "order tools" };
  else if (ran.has("handoff")) lookup = { state: "done", note: "support team" };
  steps.push({ key: "lookup", name: "Look up", ...lookup });
  const touchedGate = ran.has("gate") || ran.has("confirm") || ran.has("execute");
  steps.push({ key: "gate", name: "Gate", ...(touchedGate ? gateModel(t, data.pending) : { state: "skip", note: "" }) });
  let verify = { state: "skip", note: "" };
  if (ran.has("verify") && t.verify !== "not needed") {
    if (t.verify.startsWith("fell back")) verify = { state: "stop", note: "safe fallback" };
    else if (t.rewrites) verify = { state: "done", note: "rewrote once", loop: true };
    else verify = { state: "done", note: "passed" };
  } else if (ran.has("verify")) {
    verify = { state: "skip", note: "not needed" };
  }
  steps.push({ key: "verify", name: "Verify", ...verify });
  steps.push({ key: "reply", name: "Reply", state: "done", note: blocked ? "refused" : "" });
  return steps;
}

function railLabel(steps) {
  const parts = steps.map((s) => {
    if (s.state === "skip") return `${s.name} skipped`;
    return s.note ? `${s.name}: ${s.note}` : s.name;
  });
  return `Agent steps for this reply. ${parts.join(". ")}. Show details.`;
}

function stepTime(nodes, key) {
  const ms = nodes.filter((n) => STEP_NODES[key].includes(n.node)).reduce((sum, n) => sum + n.ms, 0);
  return ms >= 1000 ? `${(ms / 1000).toFixed(2)}s` : `${Math.round(ms)}ms`;
}

function stepCost(costs, key) {
  const usd = (STEP_COSTS[key] || []).reduce((sum, node) => sum + (costs[node] || 0), 0);
  return usd ? `$${usd.toFixed(4)}` : "none";
}

function gateLine(g) {
  const action = WRITE_TOOLS[g.action] || (g.action || "").replace(/_/g, " ");
  switch (g.step) {
    case "reason_check":
      return g.found ? ["ok", "Reason taken from the customer's own words"] : ["hold", "No reason from the customer, so it asked instead of guessing"];
    case "policy":
      return g.allowed ? ["ok", `Policy check passed for the ${action}`] : ["stop", `Policy refused the ${action}: ${(g.code || "").replace(/_/g, " ")}`];
    case "reflection":
      return [g.verdict === "proceed" ? "ok" : "hold", `Reflection said ${g.verdict}`];
    case "confirmation_requested":
      return ["hold", `Held the ${action} until the customer says yes`];
    case "confirmation":
      return [g.label === "confirm" ? "ok" : "hold", `Customer reply read as ${g.label} (${g.source === "pattern" ? "fixed pattern" : "model"})`];
    case "executed":
      return ["ok", `Ran the ${action} on the sandbox store`];
    case "refused":
      return ["stop", `The store refused the ${action}`];
    case "self_confirm_nudge":
      return ["hold", "Draft asked for a yes itself, sent back to propose the change through the gate"];
    case "handoff_recorded":
      return ["ok", "Handoff recorded for the support team"];
    default:
      return ["", g.step];
  }
}

function argsText(args) {
  return maskEmails(
    Object.entries(args || {})
      .map(([k, v]) => `${k}=${typeof v === "string" ? v : JSON.stringify(v)}`)
      .join(", "),
  );
}

function detailsPanel(data, steps, id) {
  const t = data.trace;
  const rows = steps.map((s) =>
    el("tr", { class: `is-${s.state}` }, [
      el("th", { scope: "row", text: s.name }),
      el("td", { text: s.state === "skip" ? s.note || "did not run" : s.note || "done" }),
      el("td", { text: s.state === "skip" ? "" : stepTime(t.nodes, s.key) }),
      el("td", { text: s.state === "skip" ? "" : stepCost(t.node_costs || {}, s.key) }),
    ]),
  );
  const table = el("table", { class: "steps-table" }, [
    el("thead", {}, el("tr", {}, [el("th", { scope: "col", text: "Step" }), el("th", { scope: "col", text: "What happened" }), el("th", { scope: "col", text: "Time" }), el("th", { scope: "col", text: "Model cost" })])),
    el("tbody", {}, rows),
  ]);
  const body = el("div", { class: "inside", id, hidden: "" }, [el("div", { class: "table-wrap" }, table)]);

  if (t.tools.length) {
    const calls = el("div", { class: "inside__row" }, el("p", { class: "inside__key", text: "Tool calls" }));
    for (const c of t.tools) {
      const label = c.kind === "policy_check" ? `policy check: ${c.name}` : `${c.name}(${argsText(c.args)})`;
      calls.append(el("details", { class: "call" }, [el("summary", { text: label }), el("pre", { text: maskEmails(typeof c.result === "string" ? c.result : JSON.stringify(c.result, null, 2)) })]));
    }
    body.append(calls);
  }
  if (t.retrieved && t.retrieved.length) {
    body.append(el("div", { class: "inside__row" }, [el("p", { class: "inside__key", text: "Documents retrieved" }), el("p", { text: t.retrieved.map(prettyDoc).join(", ") })]));
  }
  if (t.gate.length) {
    const list = el("ul", { class: "gate-lines" });
    for (const g of t.gate) {
      const [tone, text] = gateLine(g);
      list.append(el("li", { class: tone, text }));
    }
    body.append(el("div", { class: "inside__row" }, [el("p", { class: "inside__key", text: "Gate decisions" }), list]));
  }
  const tok = t.tokens || {};
  const cached = (tok.cache_read_input_tokens || 0) + (tok.cache_creation_input_tokens || 0);
  body.append(
    el("p", {
      class: "inside__totals",
      text: `${data.latency_s.toFixed(2)}s in all, ${(tok.input_tokens || 0).toLocaleString()} tokens in and ${(tok.output_tokens || 0).toLocaleString()} out${cached ? `, ${cached.toLocaleString()} cached` : ""}, $${(t.cost_usd ?? 0).toFixed(4)} model cost, release ${data.release}`,
    }),
  );
  return body;
}

let railCount = 0;

function buildBrain(data) {
  const steps = stepModel(data);
  const id = `inside-${++railCount}`;
  const chips = [];
  if (data.trace.blocked) chips.push({ step: "guard", text: "injection pattern" });
  for (const doc of (data.trace.retrieved || []).slice(0, 2)) chips.push({ step: "lookup", text: prettyDoc(doc) });
  for (const tool of data.trace.tools) chips.push(toolChip(tool));

  let order = 0;
  const orderOf = {};
  const track = el("span", { class: "rail__track" });
  steps.forEach((s, i) => {
    if (s.state !== "skip") orderOf[s.key] = order++;
    const node = el("span", { class: `step is-${s.state}${s.loop ? " has-loop" : ""}`, "data-step": s.key, style: `--order:${orderOf[s.key] ?? 0}` }, [
      el("span", { class: "step__dot" }, s.loop ? svg('<path d="M4 12a8 8 0 0 1 14-5.3M20 4v4h-4M20 12a8 8 0 0 1-14 5.3M4 20v-4h4" />', "step__loop") : null),
      el("span", { class: "step__name", text: s.name }),
    ]);
    if (i) track.append(el("span", { class: `rail__link${s.state === "skip" ? "" : " is-on"}`, style: `--order:${orderOf[s.key] ?? 0}` }));
    track.append(node);
  });

  const notes = el("span", { class: "rail__chips" });
  for (const s of steps) {
    if (["wait", "block", "stop", "hold"].includes(s.state) || s.loop) {
      notes.append(el("span", { class: `chip chip--${s.state}${s.loop ? " chip--loop" : ""}`, style: `--order:${orderOf[s.key] ?? 0}`, text: `${s.name}: ${s.note}` }));
    }
  }
  for (const c of chips) notes.append(el("span", { class: "chip", style: `--order:${orderOf[c.step] ?? 0}`, text: c.text }));

  const button = el("button", { class: "rail", type: "button", "aria-expanded": "false", "aria-controls": id, "aria-label": railLabel(steps) }, [
    track,
    notes,
    el("span", { class: "rail__more", "aria-hidden": "true", text: "Details" }),
  ]);
  const panel = detailsPanel(data, steps, id);
  button.addEventListener("click", () => {
    const open = button.getAttribute("aria-expanded") !== "true";
    button.setAttribute("aria-expanded", String(open));
    panel.hidden = !open;
  });
  const brain = el("div", { class: "brain" }, [button, panel]);
  if (!REDUCED_MOTION.matches) {
    brain.classList.add("is-playing");
  }
  return brain;
}

const ANSWERS = { confirmed: "you said yes", declined: "you said no", replaced: "you asked for something else" };

function settleGate(brain, outcome) {
  const gate = brain && brain.querySelector('.step[data-step="gate"]');
  if (!gate) return;
  gate.classList.remove("is-wait");
  gate.classList.add("is-hold");
  const chip = brain.querySelector(".chip--wait");
  if (chip) {
    chip.className = "chip chip--settled";
    chip.textContent = `Gate: held, then ${ANSWERS[outcome]}`;
  }
  const row = brain.querySelector(".steps-table tr.is-wait td");
  if (row) row.textContent = `held, then ${ANSWERS[outcome]}`;
  const rail = brain.querySelector(".rail");
  rail.setAttribute("aria-label", rail.getAttribute("aria-label").replace("Gate: waiting for your yes", `Gate: held, then ${ANSWERS[outcome]}`));
}

const SHIELD = '<path class="shield__body" d="M12 2.8l7.5 3.1v5.6c0 4.6-3.1 8.2-7.5 9.7-4.4-1.5-7.5-5.1-7.5-9.7V5.9z" /><path class="shield__mark" d="M12 7.6v4.6M12 15.4h.01" /><path class="shield__check" d="M8.4 12.3l2.5 2.5 4.8-5.1" />';

function buildConfirmCard(pending, onAnswer) {
  const yes = el("button", { class: "btn btn--primary", type: "button", text: "Yes, go ahead" });
  const no = el("button", { class: "btn btn--quiet", type: "button", text: "No, keep it" });
  const titleId = `card-${++railCount}`;
  const checks = el("ul", { class: "confirm__checks", "aria-label": "Store policy checks this change passed" });
  for (const c of pending.checks || []) checks.append(el("li", {}, [svg('<path d="M5 12.5l4.2 4.2L19 7" />'), document.createTextNode(c)]));
  const card = el("div", { class: "confirm", role: "group", "aria-labelledby": titleId, "data-state": "open" }, [
    el("div", { class: "confirm__head" }, [
      el("span", { class: "shield" }, svg(SHIELD)),
      el("p", { class: "confirm__title", id: titleId, text: "Confirm this change" }),
      el("span", { class: "sandbox-tag", text: "Sandbox" }),
    ]),
    el("p", { class: "confirm__summary", "data-summary": pending.summary, text: `I will ${pending.summary}` }),
    pending.checks && pending.checks.length ? checks : null,
    el("div", { class: "confirm__actions" }, [yes, no]),
    el("p", { class: "confirm__hint", text: "Nothing changes until you say yes." }),
  ]);
  yes.addEventListener("click", () => onAnswer("Yes"));
  no.addEventListener("click", () => onAnswer("No"));
  return card;
}

const ANSWER_TAGS = { confirmed: "You said yes", declined: "You said no", replaced: "You asked for something else" };

function settleCard(card, outcome) {
  card.dataset.state = outcome;
  card.querySelector(".confirm__title").textContent = "Proposed change";
  card.querySelector(".sandbox-tag").textContent = ANSWER_TAGS[outcome];
}

function outcomeKind(trace) {
  const steps = trace.gate || [];
  if (steps.some((g) => g.step === "executed")) return "done";
  if (steps.some((g) => g.step === "confirmation" && g.label === "decline")) return "kept";
  return null;
}

function buildOutcome(kind, text) {
  const titleId = `outcome-${++railCount}`;
  return el("div", { class: `outcome outcome--${kind}`, role: "group", "aria-labelledby": titleId }, [
    el("div", { class: "outcome__head" }, [
      el("span", { class: "shield" }, svg(SHIELD)),
      el("p", { class: "outcome__title", id: titleId, text: kind === "done" ? "Done" : "Nothing changed" }),
    ]),
    el("p", { class: "outcome__text", text }),
  ]);
}

const EMAIL_RE = /([A-Za-z0-9._%+-])[A-Za-z0-9._%+-]*@([A-Za-z0-9.-]+\.[A-Za-z]{2,})/g;

function maskEmails(text) {
  return text.replace(EMAIL_RE, "$1\u2022\u2022\u2022@$2");
}
