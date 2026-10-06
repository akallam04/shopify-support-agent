const $ = (id) => document.getElementById(id);

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

const signed = (v) => `${v >= 0 ? "+" : ""}${v.toFixed(3)}`;

const CATEGORY_NAMES = {
  address_change: "Address change",
  cancel: "Cancel",
  confirmation: "Confirmation step",
  difficult_customer: "Difficult customer",
  handoff: "Handoff",
  identity: "Identity",
  injection: "Injection",
  multi_intent: "Several requests",
  order_status: "Order status",
  other_person: "Someone else's order",
  product_policy: "Product and policy",
  return: "Return",
  which_order: "Which order",
};

const FAILURE_NAMES = {
  bad_handoff: "Handed a refusal to the support team instead of explaining it",
  task_ambiguous: "Assertion read as stricter than intended",
  policy_misstated: "Offered a lookup the store cannot do",
  grader_wrong: "Judge error",
  fact_missing: "Left out a required fact",
  missed_write: "Kept asking questions and never made the change",
  simulator_deviated: "Simulated customer broke its script",
  wrong_write: "Guessed the return reason",
};

const OWNER_NAMES = { agent: "Agent", task: "Task", harness: "Harness" };

/* pass^k */
function passkChart(container, headline) {
  const series = [
    { label: "Gate on, both measures", values: headline.on.pass_hat_k, color: "#17775f", width: 3, dash: "" },
    { label: "Gate off, resolved", values: headline.off.pass_hat_k, color: "#475763", width: 2, dash: "6 5" },
    { label: "Gate off, resolved safely", values: headline.off.pass_hat_k_safe, color: "#b3261e", width: 3, dash: "" },
  ];
  const draw = () => {
    const w = Math.max(300, container.clientWidth);
    const narrow = w < 520;
    const h = narrow ? 260 : 300;
    const m = { top: 16, right: narrow ? 16 : 190, bottom: narrow ? 108 : 40, left: 44 };
    const iw = w - m.left - m.right;
    const ih = h - m.top - m.bottom;
    const x = (k) => m.left + ((k - 1) / 3) * iw;
    const y = (v) => m.top + (1 - (v - 0.5) / 0.5) * ih;
    let svg = `<svg viewBox="0 0 ${w} ${h}" aria-hidden="true" font-family="Geist, sans-serif" font-size="13">`;
    for (const t of [0.5, 0.6, 0.7, 0.8, 0.9, 1.0]) {
      svg += `<line x1="${m.left}" x2="${m.left + iw}" y1="${y(t)}" y2="${y(t)}" stroke="#dde4ea" />`;
      svg += `<text x="${m.left - 8}" y="${y(t) + 4}" text-anchor="end" fill="#5f6e7a">${t.toFixed(1)}</text>`;
    }
    for (let k = 1; k <= 4; k++) {
      svg += `<text x="${x(k)}" y="${m.top + ih + 22}" text-anchor="middle" fill="#5f6e7a">k=${k}</text>`;
    }
    const labelY = series.map((s) => y(s.values[s.values.length - 1]) + 4);
    const order = labelY.map((v, i) => i).sort((a, b) => labelY[a] - labelY[b]);
    for (let j = 1; j < order.length; j++) {
      const gap = labelY[order[j]] - labelY[order[j - 1]];
      if (gap < 16) labelY[order[j]] += 16 - gap;
    }
    svg += '<g class="lines">';
    series.forEach((s, i) => {
      const pts = s.values.map((v, j) => `${x(j + 1)},${y(v)}`).join(" ");
      svg += `<polyline points="${pts}" fill="none" stroke="${s.color}" stroke-width="${s.width}" stroke-dasharray="${s.dash}" stroke-linejoin="round" stroke-linecap="round" />`;
      s.values.forEach((v, j) => {
        svg += `<circle cx="${x(j + 1)}" cy="${y(v)}" r="4" fill="${s.color}" stroke="#fff" stroke-width="1.5" />`;
      });
      if (narrow) {
        const ly = m.top + ih + 44 + i * 20;
        svg += `<line x1="${m.left}" x2="${m.left + 22}" y1="${ly - 4}" y2="${ly - 4}" stroke="${s.color}" stroke-width="${s.width}" stroke-dasharray="${s.dash}" />`;
        svg += `<text x="${m.left + 30}" y="${ly}" fill="#13202a">${s.label}</text>`;
      } else {
        svg += `<text x="${x(4) + 12}" y="${labelY[i]}" fill="${s.color}" font-weight="600">${s.label}</text>`;
      }
    });
    svg += "</g></svg>";
    container.innerHTML = svg;
  };
  draw();
  let timer;
  window.addEventListener("resize", () => {
    clearTimeout(timer);
    timer = setTimeout(draw, 120);
  });
}

/* intervals */
const DOMAIN = [-0.1, 0.45];
const pct = (v) => `${((v - DOMAIN[0]) / (DOMAIN[1] - DOMAIN[0])) * 100}%`;

function forest(container, rows) {
  const wrap = el("div", { class: "forest" });
  for (const r of rows) {
    const track = el("div", { class: "forest__track" }, [
      el("span", { class: "forest__axis" }),
      el("span", { class: "forest__zero", style: `left:${pct(0)}` }),
      el("span", { class: "forest__ci", style: `left:${pct(r.d.low)};width:calc(${pct(r.d.high)} - ${pct(r.d.low)})` }),
      el("span", { class: "forest__dot", style: `left:${pct(r.d.mean)}` }),
    ]);
    wrap.append(el("div", { class: "forest__row" }, [el("span", { class: "forest__label", text: r.label }), track]));
  }
  const ticks = el("div", { class: "forest__ticks" });
  for (const t of [-0.1, 0, 0.1, 0.2, 0.3, 0.4]) ticks.append(el("span", { style: `left:${pct(t)}`, text: t === 0 ? "0" : signed(t).replace(/0+$/, "") }));
  wrap.append(el("div", { class: "forest__row" }, [el("span"), ticks]));
  container.append(wrap);
}

/* failures and categories */
function failureBars(container, failures) {
  const owners = {};
  for (const item of failures.items) owners[item.label] = item.owner;
  const max = Math.max(...failures.by_label.map((f) => f.count));
  for (const f of failures.by_label) {
    const owner = owners[f.label];
    container.append(
      el("div", { class: "bar" }, [
        el("span", { class: "bar__label" }, [FAILURE_NAMES[f.label] || f.label, el("span", { class: "tag tag--plain bar__owner", text: OWNER_NAMES[owner] || owner })]),
        el("span", { class: "bar__count", text: String(f.count) }),
        el("span", { class: "bar__track", "aria-hidden": "true" }, el("span", { class: `bar__fill bar__fill--${owner}`, style: `width:${(f.count / max) * 100}%;--i:${failures.by_label.indexOf(f)}` })),
      ]),
    );
  }
  container.after(
    el("p", {
      class: "note",
      text: "Since the headline, refusals are explained instead of handed off, and the confirmation shows the return reason. Both were checked on fresh runs; these headline numbers were not rerun.",
    }),
  );
}

function categories(container, rows) {
  const sorted = [...rows].sort((a, b) => b.on.resolved_safely / b.on.n - b.off.resolved_safely / b.off.n - (a.on.resolved_safely / a.on.n - a.off.resolved_safely / a.off.n));
  for (const r of sorted) {
    const line = (name, v, cls) =>
      el("div", { class: "cat__row" }, [
        el("span", { text: name }),
        el("span", { class: "cat__track", "aria-hidden": "true" }, el("span", { class: `cat__fill ${cls}`, style: `width:${(v.resolved_safely / v.n) * 100}%` })),
        el("b", { text: `${v.resolved_safely} of ${v.n}` }),
      ]);
    container.append(
      el("div", { class: "cat" }, [el("p", { class: "cat__name", text: CATEGORY_NAMES[r.category] || r.category }), line("Gate on", r.on, ""), line("Gate off", r.off, "cat__fill--off")]),
    );
  }
}

/* conversations */
const WRITES = ["cancel_order", "update_shipping_address", "request_return"];

function flagFor(turn, gate) {
  const steps = turn.gate.map((g) => g.step);
  const wrote = turn.tools.some((t) => t.kind === "tool" && WRITES.includes(t.name));
  if (gate === "off" && wrote) return ["rose", "The change ran here, before the customer said yes."];
  if (steps.includes("confirmation_requested")) return ["amber", "The gate held the change and showed it to the customer."];
  if (steps.includes("executed")) return ["pine", "The change ran only after the yes."];
  return null;
}

const MOTION = !window.matchMedia("(prefers-reduced-motion: reduce)").matches;
const wait = (ms) => new Promise((r) => setTimeout(r, ms));

function compareSide(list, ex) {
  const beats = ex.turns.map((turn) => {
    const flag = flagFor(turn, ex.gate);
    const customer = el("li", { class: "mini__customer beat" }, [el("span", { class: "mini__who", text: "Customer" }), el("span", { class: "mini__text", text: turn.user })]);
    const reply = el("li", { class: "beat" }, [
      el("span", { class: "mini__who", text: "Assistant" }),
      el("span", { class: "mini__text", text: turn.agent }),
      flag ? el("span", { class: `flag flag--${flag[0]}`, text: flag[1] }) : null,
    ]);
    list.append(customer, reply);
    return { customer, reply, hold: Boolean(flag && flag[0] === "amber") };
  });
  const end = el("li", { class: "mini__end beat", text: "End of conversation" });
  list.append(end);
  return { beats, end };
}

function renderCompare(examples) {
  const sides = [];
  for (const list of document.querySelectorAll("[data-example]")) {
    const ex = examples.find((e) => e.id === list.dataset.example);
    if (ex) sides.push(compareSide(list, ex));
  }
  const all = () => sides.flatMap((side) => [...side.beats.flatMap((b) => [b.customer, b.reply]), side.end]);
  const button = $("replayCompare");
  if (!MOTION) {
    all().forEach((node) => node.classList.add("is-shown"));
    button.hidden = true;
    return;
  }
  let run = 0;
  async function play() {
    const mine = ++run;
    const live = () => mine === run;
    all().forEach((node) => node.classList.remove("is-shown", "is-live"));
    button.disabled = true;
    const turns = Math.max(...sides.map((side) => side.beats.length));
    for (let t = 0; t <= turns && live(); t++) {
      for (const side of sides) {
        const beat = side.beats[t];
        if (beat) beat.customer.classList.add("is-shown");
        else if (t === side.beats.length) side.end.classList.add("is-shown");
      }
      await wait(650);
      if (!live()) return;
      document.querySelectorAll(".flag.is-live").forEach((f) => f.classList.remove("is-live"));
      let hold = false;
      for (const side of sides) {
        const beat = side.beats[t];
        if (!beat) continue;
        beat.reply.classList.add("is-shown");
        if (beat.hold) {
          hold = true;
          beat.reply.querySelector(".flag").classList.add("is-live");
        }
      }
      await wait(hold ? 2200 : 1100);
    }
    if (live()) button.disabled = false;
  }
  button.addEventListener("click", play);
  onVisible($("compare"), play, 0.35);
}

function onVisible(node, callback, threshold = 0.3) {
  if (!node) return;
  if (!("IntersectionObserver" in window)) {
    callback();
    return;
  }
  const observer = new IntersectionObserver((entries) => {
    if (entries.some((e) => e.isIntersecting)) {
      observer.disconnect();
      callback();
    }
  }, { threshold });
  observer.observe(node);
}

function countUp(group) {
  const nums = [...group.querySelectorAll(".kpi__value > [data-num]")];
  const finals = nums.map((n) => Number(n.textContent));
  group.setAttribute("aria-busy", "true");
  const started = performance.now();
  const step = (now) => {
    const t = Math.min(1, (now - started) / 900);
    const eased = 1 - Math.pow(1 - t, 3);
    nums.forEach((n, i) => {
      n.textContent = String(Math.round(finals[i] * eased));
    });
    if (t < 1) requestAnimationFrame(step);
    else {
      nums.forEach((n, i) => {
        n.textContent = String(finals[i]);
      });
      group.removeAttribute("aria-busy");
    }
  };
  requestAnimationFrame(step);
}

function animateOnView() {
  if (!MOTION) return;
  for (const group of document.querySelectorAll(".kpis")) onVisible(group, () => countUp(group), 0.6);
  for (const figure of document.querySelectorAll(".figure")) onVisible(figure, () => figure.classList.add("is-in"), 0.35);
}

function gateText(g) {
  const action = (g.action || "").replace(/_/g, " ");
  switch (g.step) {
    case "policy":
      return g.allowed ? ["ok", `Policy check passed for ${action}`] : ["stop", `Policy check refused ${action}: ${(g.code || "").replace(/_/g, " ")}`];
    case "reflection":
      return [g.verdict === "proceed" ? "ok" : "hold", `Reflection said ${g.verdict}`];
    case "confirmation_requested":
      return ["hold", `Held ${action} until the customer says yes`];
    case "confirmation":
      return [g.label === "confirm" ? "ok" : "hold", `Customer reply read as ${g.label} (${g.source === "pattern" ? "fixed pattern" : "model"})`];
    case "executed":
      return ["ok", `Ran ${action}`];
    case "refused":
      return ["stop", `The store refused ${action}`];
    case "self_confirm_nudge":
      return ["hold", "Draft asked for a yes itself, sent back to propose the change through the gate"];
    case "handoff_recorded":
      return ["ok", "Handoff recorded for the support team"];
    default:
      return ["", g.step];
  }
}

function argsText(args) {
  return Object.entries(args || {})
    .map(([k, v]) => `${k}=${typeof v === "string" ? v : JSON.stringify(v)}`)
    .join(", ");
}

function turnTrace(turn) {
  const body = el("div", { class: "trace__body" });
  if (turn.tools.length) {
    const calls = el("div", { class: "trace__row" }, el("span", { class: "trace__key", text: "Tool calls" }));
    for (const c of turn.tools) {
      const label = c.kind === "policy_check" ? `policy check: ${c.name}` : `${c.name}(${argsText(c.args)})`;
      calls.append(el("details", { class: "call" }, [el("summary", { text: label }), el("pre", { text: JSON.stringify(c.result, null, 2) })]));
    }
    body.append(calls);
  }
  if (turn.gate.length) {
    const steps = el("ul", { class: "steps" });
    for (const g of turn.gate) {
      const [tone, text] = gateText(g);
      steps.append(el("li", { class: tone, text }));
    }
    body.append(el("div", { class: "trace__row" }, [el("span", { class: "trace__key", text: "Mutation gate" }), steps]));
  }
  const numbers = el("div", { class: "numbers" });
  numbers.innerHTML = `<span><b>${turn.latency_s.toFixed(2)}s</b> latency</span><span><b>$${turn.cost_usd.toFixed(4)}</b> model cost</span>`;
  body.append(el("div", { class: "trace__row" }, [el("span", { class: "trace__key", text: "Numbers" }), numbers]));
  const intent = turn.intent ? turn.intent.replace(/_/g, " ") : "answer";
  return el("details", { class: "trace" }, [el("summary", {}, [el("span", { class: "trace__label", text: "Inside this turn" }), el("span", { text: intent })]), body]);
}

function outcome(ex) {
  if (ex.resolved_safely) return ["pine", "Resolved safely"];
  if (ex.resolved) return ["amber", "Resolved, not safely"];
  return ["rose", "Failed"];
}

function viewer(examples) {
  const pick = $("examplePick");
  const card = $("viewer");
  const count = $("stepCount");
  const prev = $("prevStep");
  const next = $("nextStep");
  const all = $("allSteps");
  let ex = null;
  let steps = [];
  let shown = 0;

  for (const e of examples) pick.append(el("option", { value: e.id, text: `${e.title} (${outcome(e)[1].toLowerCase()})` }));

  function paint(revealed) {
    const list = card.querySelector(".turns");
    list.innerHTML = "";
    steps.slice(0, shown).forEach((s, i) => {
      const item = el("li", { class: i === shown - 1 && revealed ? "is-new" : "" });
      if (s.who === "customer") {
        item.append(el("div", { class: "msg msg--user" }, el("div", { class: "bubble", text: s.turn.user })));
      } else {
        const flag = flagFor(s.turn, ex.gate);
        item.append(
          el("div", { class: "msg msg--agent" }, [
            el("div", { class: "bubble", text: s.turn.agent }),
            flag ? el("span", { class: `flag flag--${flag[0]}`, text: flag[1] }) : null,
            turnTrace(s.turn),
          ]),
        );
      }
      list.append(item);
    });
    count.textContent = `Step ${shown} of ${steps.length}`;
    prev.disabled = shown <= 1;
    next.disabled = shown >= steps.length;
    all.hidden = shown >= steps.length;
  }

  function load(id) {
    ex = examples.find((e) => e.id === id);
    steps = ex.turns.flatMap((turn) => [{ who: "customer", turn }, { who: "agent", turn }]);
    shown = 1;
    const [tone, label] = outcome(ex);
    card.innerHTML = "";
    card.append(
      el("div", { class: "viewer__head" }, [
        el("h3", { text: ex.title }),
        el("p", { class: "viewer__task", text: `Task: ${ex.task}` }),
        el("p", { class: "viewer__meta" }, [
          el("span", { class: `tag tag--${tone}`, text: label }),
          el("span", { class: `tag tag--${ex.gate === "on" ? "plain" : "rose"}`, text: `Gate ${ex.gate}` }),
          el("span", { text: `${ex.run}, ${ex.task_id}, try ${ex.trial + 1}` }),
        ]),
        el("p", { class: "viewer__note", text: ex.note }),
      ]),
      el("ol", { class: "turns" }),
    );
    paint(false);
  }

  pick.addEventListener("change", () => load(pick.value));
  prev.addEventListener("click", () => {
    shown = Math.max(1, shown - 1);
    paint(false);
  });
  next.addEventListener("click", () => {
    shown = Math.min(steps.length, shown + 1);
    paint(true);
    card.querySelector(".turns li:last-child")?.scrollIntoView({ block: "nearest" });
  });
  all.addEventListener("click", () => {
    shown = steps.length;
    paint(false);
  });
  load(examples[0].id);
}

async function main() {
  const [results, examples] = await Promise.all([
    fetch("./data/results.json").then((r) => r.json()),
    fetch("./data/transcripts.json").then((r) => r.json()),
  ]);
  const h = results.headline;
  passkChart($("passk"), h);
  forest(document.querySelector('[data-forest="headline"]'), [
    { label: "Resolved", d: h.difference.resolved },
    { label: "Resolved safely", d: h.difference.resolved_safely },
  ]);
  forest(document.querySelector('[data-forest="judge"]'), [
    { label: "Before the judge fix", d: h.judge_fix.before.difference },
    { label: "After the judge fix", d: h.judge_fix.after.difference },
  ]);
  failureBars($("failureBars"), results.failures);
  categories($("categories"), h.categories);
  renderCompare(examples);
  viewer(examples);
  animateOnView();
}

main().catch((err) => {
  for (const id of ["passk", "failureBars", "categories"]) {
    const node = $(id);
    if (node) node.textContent = "The chart data did not load. The tables on this page carry the same numbers.";
  }
  console.error(err);
});
