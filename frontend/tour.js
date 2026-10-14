const REPO = "https://github.com/akallam04/shopify-support-agent";
const TOUR_STEP_MS = [4500, 8000, 7000, 7000];
const TAP_MS = 1600;
const PLACEHOLDER = inputEl.placeholder;
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

function plain(summary, key) {
  return summary.guarantees.find((g) => g.key === key).plain;
}

function countWord(n) {
  return n === 0 ? "zero" : String(n);
}

function tourCopy(data) {
  const h = data.results.headline;
  return [
    { title: "A customer wants to return a jacket that's too big.", text: "They ask the store's assistant, the same way they would text a person." },
    { title: plain(data.summary, "rules"), text: "Then it shows exactly what it will do: which jacket, the return fee, and the refund." },
    { title: plain(data.summary, "yes"), text: "Here the customer taps Yes. If they tapped No, nothing would change." },
    { title: "Only then is the return filed.", text: plain(data.summary, "facts") },
    {
      title: "Why it matters",
      text: `Without this safety check, ${h.off.unsafe_conversations} of ${h.off.conversations} test conversations included a change the customer never approved. With it: ${countWord(h.on.unsafe_conversations)}.`,
    },
  ];
}

function userBubble(text) {
  return el("div", { class: "msg msg--user" }, el("div", { class: "bubble", text }));
}

function replayCard(pending) {
  const card = buildConfirmCard(pending, () => {});
  card.classList.add("confirm--replay");
  const actions = card.querySelector(".confirm__actions");
  actions.setAttribute("aria-hidden", "true");
  for (const b of actions.querySelectorAll("button")) b.tabIndex = -1;
  return card;
}

function statCard(h) {
  const tile = (cls, label, n) =>
    el("div", { class: `tour-stat__tile ${cls}` }, [
      el("p", { class: "tour-stat__label", text: label }),
      el("p", { class: "tour-stat__num" }, [el("strong", { text: String(n) }), document.createTextNode(` of ${h.on.conversations}`)]),
    ]);
  return el("div", { class: "msg msg--agent msg--card" }, el("div", { class: "tour-stat" }, [
    el("p", { class: "tour-stat__head", text: "Test conversations with a change the customer never approved" }),
    el("div", { class: "tour-stat__tiles" }, [
      tile("tour-stat__tile--off", "Without the safety check", h.off.unsafe_conversations),
      tile("tour-stat__tile--on", "With the safety check", h.on.unsafe_conversations),
    ]),
  ]));
}

function scrollToShow(target, edge = "top") {
  const box = target.getBoundingClientRect();
  const view = logEl.getBoundingClientRect();
  const delta = edge === "top" ? box.top - view.top - 12 : box.bottom - view.bottom + 12;
  logEl.scrollTo({ top: logEl.scrollTop + delta, behavior: REDUCED_MOTION.matches ? "auto" : "smooth" });
}

function mark(items, now) {
  for (const item of items) item.classList.toggle("tour-past", !now.includes(item));
}

function tapYes(card, onDone) {
  const button = card.querySelector(".confirm__actions .btn--primary");
  button.classList.add("is-tapping");
  button.append(el("span", { class: "tap", "aria-hidden": "true" }));
  tour.anim = setTimeout(onDone, TAP_MS);
}

function renderScene(at, animate, prev) {
  const { conversation, results } = tour.data;
  const [ask, yes] = conversation.turns;
  const add = (node, step, after = null) => {
    node.classList.toggle("tour-seen", step <= prev);
    if (after) after.after(node);
    else logEl.append(node);
    return node;
  };
  logEl.replaceChildren(el("p", { class: "tour-label", text: `Replay of a real conversation, recorded ${conversation.recorded_on}` }));
  const asked = add(userBubble(ask.user), 0);
  if (at === 0) return scrollToShow(asked);
  const card = replayCard(ask.response.pending);
  const cardMsg = add(el("div", { class: "msg msg--agent msg--card" }, card), 1);
  if (at === 1) {
    mark([asked, cardMsg], [cardMsg]);
    return scrollToShow(cardMsg);
  }
  const yesSaid = () => {
    settleCard(card, "confirmed");
    return add(userBubble(yes.user), 2, cardMsg);
  };
  if (at === 2) {
    mark([asked, cardMsg], [cardMsg]);
    if (animate && !REDUCED_MOTION.matches) {
      scrollToShow(card.querySelector(".confirm__actions"), "bottom");
      return tapYes(card, () => scrollToShow(yesSaid(), "bottom"));
    }
    return scrollToShow(yesSaid(), "bottom");
  }
  const said = yesSaid();
  const outcome = add(el("div", { class: "msg msg--agent msg--card" }, buildOutcome("done", cleanText(yes.response.response).clean)), 3);
  if (at === 3) {
    mark([asked, cardMsg, said, outcome], [outcome]);
    return scrollToShow(outcome);
  }
  const stat = add(statCard(results.headline), 4);
  mark([asked, cardMsg, said, outcome, stat], [stat]);
  scrollToShow(stat.querySelector(".tour-stat__tiles"), "bottom");
}

function iconButton(cls, label, markup, onClick, after = false) {
  const parts = [svg(markup), el("span", { text: label })];
  const b = el("button", { class: cls, type: "button" }, after ? parts.reverse() : parts);
  b.addEventListener("click", onClick);
  return b;
}

const PAUSE_ICON = '<path d="M8 5v14M16 5v14" />';
const PLAY_ICON = '<path d="M8 5.5v13l10.5-6.5z" />';

function buildPanel(data) {
  const steps = tourCopy(data);
  const segments = steps.map(() => el("span", { class: "tour-progress__seg" }, el("i")));
  const count = el("p", { class: "tour-panel__count" });
  const title = el("h3", { class: "tour-panel__title", id: "tourTitle" });
  const text = el("p", { class: "tour-panel__text" });
  const back = iconButton("btn btn--quiet tour-panel__back", "Back", '<path d="M15 6l-6 6 6 6" />', () => go(tour.at - 1));
  const play = iconButton("btn btn--quiet tour-panel__play", "Pause", PAUSE_ICON, togglePlay);
  const next = iconButton("btn btn--primary tour-panel__next", "Next", '<path d="M9 6l6 6-6 6" />', () => go(tour.at + 1), true);
  const controls = el("div", { class: "tour-panel__controls" }, [back, play, next]);
  const tryIt = el("button", { class: "btn btn--primary", type: "button", text: "Try it yourself" });
  tryIt.addEventListener("click", () => {
    stopTour({ welcome: true });
    logEl.querySelector(".starter")?.focus({ preventScroll: true });
  });
  const finish = el("div", { class: "tour-panel__finish", hidden: "" }, [
    tryIt,
    el("a", { class: "btn btn--quiet", href: "./how-it-works.html", text: "See how it was tested" }),
    el("a", { class: "btn btn--quiet", href: REPO, target: "_blank", rel: "noopener noreferrer", text: "View the code" }),
  ]);
  const exit = el("button", { class: "link-btn tour-panel__exit", type: "button", text: "Exit tour" });
  exit.addEventListener("click", () => stopTour({ welcome: true }));
  const panel = el("div", { class: "tour-panel", id: "tourPanel", role: "region", "aria-labelledby": "tourTitle" }, [
    el("div", { class: "tour-panel__top" }, [count, el("div", { class: "tour-progress", "aria-hidden": "true" }, segments), exit]),
    el("div", { class: "tour-panel__copy", "aria-live": "polite" }, [title, text]),
    controls,
    finish,
  ]);
  return { panel, steps, segments, count, title, text, back, play, next, controls, finish };
}

function setPlayButton() {
  const { play } = tour.ui;
  play.replaceChildren(svg(tour.playing ? PAUSE_ICON : PLAY_ICON), el("span", { text: tour.playing ? "Pause" : "Play" }));
  play.setAttribute("aria-label", tour.playing ? "Pause the tour" : "Play the tour");
  tour.ui.panel.classList.toggle("is-paused", !tour.playing);
}

function schedule() {
  clearTimeout(tour.timer);
  const ms = TOUR_STEP_MS[tour.at];
  if (!tour.playing || ms === undefined) return;
  tour.startedAt = Date.now();
  tour.timer = setTimeout(() => go(tour.at + 1), tour.remaining);
}

function go(at) {
  if (!tour || at < 0 || at >= tour.ui.steps.length) return;
  clearTimeout(tour.timer);
  clearTimeout(tour.anim);
  const prev = tour.at;
  const forward = at === prev + 1;
  tour.at = at;
  const { steps, segments, count, title, text, back, play, controls, finish, panel } = tour.ui;
  count.textContent = `${at + 1} of ${steps.length}`;
  count.setAttribute("aria-label", `Step ${at + 1} of ${steps.length}`);
  title.textContent = steps[at].title;
  text.textContent = steps[at].text;
  segments.forEach((s, i) => {
    s.classList.toggle("is-done", i < at);
    s.classList.toggle("is-now", i === at);
    s.style.setProperty("--step-ms", `${TOUR_STEP_MS[i] || 0}ms`);
  });
  const last = at === steps.length - 1;
  back.disabled = at === 0;
  controls.hidden = last;
  finish.hidden = !last;
  play.hidden = last;
  panel.dataset.step = String(at + 1);
  tour.remaining = TOUR_STEP_MS[at];
  segments[at].replaceChildren(el("i"));
  renderScene(at, forward, prev);
  if (last) finish.querySelector("button").focus({ preventScroll: true });
  schedule();
}

function togglePlay() {
  if (!tour) return;
  if (tour.playing) {
    clearTimeout(tour.timer);
    tour.remaining = Math.max(0, tour.remaining - (Date.now() - tour.startedAt));
  }
  tour.playing = !tour.playing;
  setPlayButton();
  schedule();
}

function lockComposer(on) {
  inputEl.disabled = on;
  sendEl.disabled = on;
  inputEl.placeholder = on ? "Tour playing" : PLACEHOLDER;
}

function stopTour({ welcome = false } = {}) {
  if (!tour) return;
  clearTimeout(tour.timer);
  clearTimeout(tour.anim);
  tour.ui?.panel.remove();
  tour = null;
  chatEl.classList.remove("is-touring");
  lockComposer(false);
  clearChat();
  if (welcome) renderWelcome();
}

function tourFailed() {
  const retry = el("button", { class: "btn btn--primary", type: "button", text: "Try again" });
  const exit = el("button", { class: "btn btn--quiet", type: "button", text: "Exit tour" });
  retry.addEventListener("click", startTour);
  exit.addEventListener("click", () => stopTour({ welcome: true }));
  const panel = el("div", { class: "tour-panel", id: "tourPanel", role: "alert" }, [
    el("p", { class: "tour-panel__title", text: "The tour didn't load." }),
    el("p", { class: "tour-panel__text", text: "Check your connection and try again." }),
    el("div", { class: "tour-panel__controls" }, [exit, retry]),
  ]);
  tour.ui = { panel };
  formEl.before(panel);
}

async function startTour() {
  if (!chatEl.classList.contains("is-open")) openChat({ focus: false });
  expandChat();
  stopTour();
  tour = { at: -1, playing: !REDUCED_MOTION.matches, timer: null, anim: null, remaining: 0, startedAt: 0 };
  chatEl.classList.add("is-touring");
  clearChat();
  lockComposer(true);
  const run = tour;
  let data;
  try {
    data = await tourData();
  } catch {
    if (tour === run) tourFailed();
    return;
  }
  if (tour !== run) return;
  tour.data = data;
  tour.ui = buildPanel(data);
  formEl.before(tour.ui.panel);
  setPlayButton();
  go(0);
  tour.ui.next.focus({ preventScroll: true });
}

document.addEventListener(
  "click",
  (e) => {
    if (e.target.closest("[data-tour]")) {
      e.preventDefault();
      startTour();
      return;
    }
    if (tour && e.target.closest("#close")) stopTour();
  },
  true,
);

document.addEventListener(
  "keydown",
  (e) => {
    if (!tour || !tour.ui?.steps) return;
    if (e.key === "Escape") {
      e.stopImmediatePropagation();
      stopTour({ welcome: true });
    } else if (e.key === "ArrowRight" && !e.target.closest("input, textarea")) go(tour.at + 1);
    else if (e.key === "ArrowLeft" && !e.target.closest("input, textarea")) go(tour.at - 1);
  },
  true,
);
