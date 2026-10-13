const TOUR_STEP_MS = 8000;
const REPO = "https://github.com/akallam04/shopify-support-agent";
let tour = null;

async function tourData() {
  const names = ["tour", "results", "summary"];
  const [conversation, results, summary] = await Promise.all(
    names.map((name) =>
      fetch(`./data/${name}.json`).then((res) => {
        if (!res.ok) throw new Error(`${name} ${res.status}`);
        return res.json();
      }),
    ),
  );
  return { conversation, results, summary };
}

function lastOf(selector) {
  const all = logEl.querySelectorAll(selector);
  return all[all.length - 1] || null;
}

function spotlight(target, text) {
  logEl.querySelectorAll(".is-spotlight").forEach((n) => n.classList.remove("is-spotlight"));
  logEl.querySelectorAll(".caption").forEach((n) => n.remove());
  if (!target) return;
  target.classList.add("is-spotlight");
  const msg = target.closest(".msg") || target;
  const note = el("p", { class: "caption", role: "note", text });
  msg.after(note);
  note.scrollIntoView({ block: "nearest", behavior: REDUCED_MOTION.matches ? "auto" : "smooth" });
}

function lockReplayButtons() {
  for (const b of logEl.querySelectorAll(".confirm__actions button")) b.disabled = true;
}

function tourEnd(results, summary) {
  const h = results.headline;
  const v = results.hardening;
  const p3 = (x) => x.toFixed(3);
  const wrap = el("div", { class: "msg msg--agent msg--card" }, el("div", { class: "tour-end" }, [
    el("h3", { text: "What you just saw, measured" }),
    el("ul", {}, [
      el("li", { text: `In ${h.on.conversations} simulated conversations per setting, changes the customer never agreed to fell from ${h.off.unsafe_conversations} to ${h.on.unsafe_conversations}, with no significant difference in resolution (${h.on.resolved} against ${h.off.resolved} of ${h.on.conversations}).` }),
      el("li", { text: `Resolved safely: pass^1 ${p3(h.off.pass_hat_k_safe[0])} to ${p3(h.on.pass_hat_k_safe[0])}, and on all 4 tries of a task (pass^4) ${p3(h.off.pass_hat_k_safe[3])} to ${p3(h.on.pass_hat_k_safe[3])}.` }),
      el("li", { text: `After the v2.2 hardening: ${v.tasks.resolved_safely} of ${v.tasks.conversations} new review tasks and ${v.main.resolved_safely} of ${v.main.conversations} main tasks resolved safely, ${v.tasks.unsafe_writes + v.main.unsafe_writes} unsafe writes.` }),
    ]),
    el("ul", {}, summary.guarantees.map((g) => el("li", { text: `${g.title}. ${g.text}` }))),
    el("p", { class: "tour-end__links" }, [
      el("a", { href: REPO, target: "_blank", rel: "noopener noreferrer", text: "Code on GitHub" }),
      el("a", { href: "./how-it-works.html", text: "How it works" }),
      el("a", { href: `${REPO}/blob/main/docs/write-up.md`, target: "_blank", rel: "noopener noreferrer", text: "Technical write-up" }),
    ]),
    el("h3", { text: "Now try it yourself" }),
  ]));
  renderStarters(wrap.firstChild);
  logEl.append(wrap);
  wrap.scrollIntoView({ block: "nearest", behavior: REDUCED_MOTION.matches ? "auto" : "smooth" });
}

function tourSteps(data) {
  const [ask, yes] = data.conversation.turns;
  return [
    () => addUser(ask.user),
    () => {
      addAgent(ask.response);
      lockReplayButtons();
      spotlight(lastOf(".confirm"), "The agent proposed this change. Code checked the store policy first, and the card lists the checks it passed. Nothing happens until you say yes.");
    },
    () => spotlight(lastOf(".brain .rail"), "Under every reply, the steps it took. The gate is holding here, waiting for your yes. Tap a rail to see each step's time, tools, and checks."),
    () => {
      addUser(yes.user);
      addAgent(yes.response);
      spotlight(logEl.querySelector(".confirm .sandbox-tag"), "Your yes went to the gate, and the card now records it. Code ran the change once.");
    },
    () => spotlight(lastOf(".outcome"), "The result is the store's own receipt, not the model's wording."),
    () => {
      spotlight(null, "");
      tourEnd(data.results, data.summary);
    },
  ];
}

function tourBar(data) {
  const next = el("button", { class: "chip-btn", type: "button", text: "Next" });
  const skip = el("button", { class: "chip-btn", type: "button", text: "Skip" });
  next.addEventListener("click", () => advance());
  skip.addEventListener("click", () => stopTour({ welcome: true }));
  const bar = el("div", { class: "tour-bar", id: "tourBar", role: "status" }, [
    el("p", {}, [el("strong", { text: "Replay of a real conversation" }), document.createTextNode(` with this assistant, recorded ${data.conversation.recorded_on}. Nothing is sent.`)]),
    el("div", { class: "tour-bar__actions" }, [next, skip]),
  ]);
  return { bar, next };
}

function advance() {
  if (!tour) return;
  clearTimeout(tour.timer);
  const step = tour.steps[tour.at];
  tour.at += 1;
  if (step) step();
  if (tour.at >= tour.steps.length) {
    tour.next.remove();
    return;
  }
  if (!REDUCED_MOTION.matches) tour.timer = setTimeout(advance, tour.at === 1 ? 1500 : TOUR_STEP_MS);
}

function stopTour({ welcome = false } = {}) {
  if (!tour) return;
  clearTimeout(tour.timer);
  tour = null;
  clearChat();
  if (welcome) renderWelcome();
}

async function startTour() {
  if (!chatEl.classList.contains("is-open")) openChat({ focus: false });
  expandChat();
  if (tour) stopTour();
  clearChat();
  let data;
  try {
    data = await tourData();
  } catch {
    const retry = actionButton("Try again", startTour);
    logEl.append(el("div", { class: "msg msg--agent msg--failed" }, el("div", { class: "bubble", text: "The replay did not load." })), el("div", { class: "actions-row", id: "actions" }, retry));
    return;
  }
  const { bar, next } = tourBar(data);
  logEl.append(bar);
  tour = { steps: tourSteps(data), at: 0, timer: null, next };
  advance();
}

document.addEventListener(
  "click",
  (e) => {
    if (e.target.closest("[data-tour]")) {
      e.preventDefault();
      startTour();
      return;
    }
    if (tour && e.target.closest("[data-ask], .starter")) stopTour();
  },
  true,
);

formEl.addEventListener("submit", () => stopTour(), true);
