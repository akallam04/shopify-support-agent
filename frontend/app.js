const API_BASE = document.querySelector('meta[name="api-base"]')?.content || "";

const widgetEl = document.querySelector(".widget");
const launcherEl = document.getElementById("launcher");
const panelEl = document.getElementById("panel");
const teaserEl = document.getElementById("teaser");
const teaserCloseEl = document.getElementById("teaserClose");
const badgeEl = document.getElementById("badge");
const minimizeEl = document.getElementById("minimize");
const restartEl = document.getElementById("restart");
const soundEl = document.getElementById("sound");
const messagesEl = document.getElementById("messages");
const formEl = document.getElementById("composer");
const inputEl = document.getElementById("input");
const sendEl = document.getElementById("send");

const gridEl = document.getElementById("grid");
const gridEmptyEl = document.getElementById("gridEmpty");
const gearNoteEl = document.getElementById("gearNote");
const chipbarEl = document.getElementById("chipbar");
const navLinksEl = document.getElementById("navLinks");
const searchEl = document.getElementById("search");
const searchToggleEl = document.getElementById("searchToggle");
const searchInputEl = document.getElementById("searchInput");
const cartToggleEl = document.getElementById("cartToggle");
const cartCloseEl = document.getElementById("cartClose");
const cartDrawerEl = document.getElementById("cartDrawer");
const cartItemsEl = document.getElementById("cartItems");
const cartTotalEl = document.getElementById("cartTotal");
const scrimEl = document.getElementById("scrim");
const dataTableEl = document.getElementById("dataTable");

// full conversation, sent to the agent every turn since the api is stateless
let history = [];
let opened = false;

const SUGGESTIONS = [
  { label: "Do you have waterproof jackets?", text: "Do you have waterproof jackets?" },
  { label: "What is your return policy?", text: "What is your return policy?" },
  {
    label: "Track my order #1001",
    text: "Where is my order #1001? My email is maya.thompson@example.com",
  },
  { label: "Do you ship to Canada?", text: "Do you ship to Canada?" },
];

const INTENT_LABELS = {
  product: "Product",
  policy: "Policy",
  order: "Order lookup",
  smalltalk: "Greeting",
  handoff: "Escalation",
  out_of_scope: "Out of scope",
  injection: "Blocked",
};

// storefront catalog, mirrors the seeded products the agent answers from
const PRODUCTS = [
  { name: "Stormline Rain Jacket", price: 179.99, stock: "In stock", art: "jacket", cat: "apparel" },
  { name: "Glacier Point Down Parka", price: 329.99, stock: "In stock", art: "parka", cat: "apparel" },
  { name: "Trailblazer Merino Base Layer", price: 84.99, stock: "In stock", art: "layer", cat: "apparel" },
  { name: "Sierra Sun Hoody", price: 69.95, stock: "In stock", art: "hoody", cat: "apparel" },
  { name: "Summit Ridge 2-Person Tent", price: 289.99, stock: "In stock", art: "tent", cat: "camp" },
  { name: "Ember 750 Sleeping Bag", price: 249.99, stock: "In stock", art: "bag", cat: "camp" },
  { name: "Basecamp Titanium Cook Set", price: 119.99, stock: "In stock", art: "pot", cat: "camp" },
  { name: "Drift Camp Chair", price: 74.99, stock: "In stock", art: "chair", cat: "camp" },
  { name: "Cascade 40L Backpack", price: 149.95, stock: "In stock", art: "pack", cat: "gear" },
  { name: "Alpine Crossing Boots", price: 219.99, stock: "Low stock", art: "boot", cat: "gear" },
  { name: "Peakfinder Headlamp 600", price: 59.99, stock: "In stock", art: "lamp", cat: "gear" },
  { name: "Wander Insulated Bottle", price: 39.95, stock: "In stock", art: "bottle", cat: "gear" },
];

const ART = {
  jacket: '<path d="M70 40 L100 28 L130 40 L138 96 H62 Z" /><path d="M100 28 V96" />',
  parka: '<path d="M68 42 L100 30 L132 42 L136 98 H64 Z" /><path d="M100 30 V98 M84 52 h-8 M124 52 h-8" />',
  layer: '<path d="M74 40 L100 32 L126 40 L132 92 H68 Z" /><path d="M88 36 a12 8 0 0 0 24 0" />',
  hoody: '<path d="M72 44 L100 32 L128 44 L134 96 H66 Z" /><path d="M86 34 a16 12 0 0 0 28 0" />',
  tent: '<path d="M100 30 L145 96 H55 Z" /><path d="M100 30 V96 M78 96 L100 62 L122 96" />',
  bag: '<rect x="58" y="46" width="84" height="48" rx="24" /><path d="M78 46 v48 M122 46 v48" />',
  pot: '<path d="M72 52 h56 v34 a8 8 0 0 1 -8 8 h-40 a8 8 0 0 1 -8 -8 Z" /><path d="M128 60 h10 M62 60 h10 M84 52 v-8 h32 v8" />',
  chair: '<path d="M74 46 v34 h52 v-34" /><path d="M70 96 l10 -16 M130 96 l-10 -16 M74 80 h52" />',
  pack: '<rect x="70" y="38" width="60" height="58" rx="14" /><path d="M86 38 v-8 a14 14 0 0 1 28 0 v8 M70 64 h60" />',
  boot: '<path d="M72 34 h26 v34 l40 16 v20 H72 Z" /><path d="M72 84 h66" />',
  lamp: '<rect x="72" y="50" width="42" height="30" rx="9" /><path d="M114 58 l22 -10 v34 l-22 -10 M72 65 h-14" />',
  bottle: '<path d="M88 38 h24 v10 l6 12 v40 a6 6 0 0 1 -6 6 h-24 a6 6 0 0 1 -6 -6 v-40 l6 -12 Z" /><path d="M82 74 h36" />',
};

const CART = [
  { name: "Stormline Rain Jacket", variant: "Medium", price: 179.99, qty: 1, art: "jacket" },
  { name: "Wander Insulated Bottle", variant: "Slate", price: 39.95, qty: 1, art: "bottle" },
];

// real seeded orders, surfaced so a visitor knows what they can actually look up
const TEST_ORDERS = [
  { id: "#1001", email: "maya.thompson@example.com", state: "Shipped, has tracking", tone: "ok" },
  { id: "#1002", email: "maya.thompson@example.com", state: "Paid, not shipped yet", tone: "" },
  { id: "#1003", email: "maya.thompson@example.com", state: "Cancelled and refunded", tone: "warn" },
  { id: "#1014", email: "grace.kim@example.com", state: "Payment pending", tone: "warn" },
];

const URL_RE = /(https?:\/\/[^\s]+)/g;
const CITATION_RE = /\s*\[([a-z0-9][a-z0-9-]*)\]/g;

let activeFilter = "all";
let activeQuery = "";

/* ---------- storefront ---------- */
function productSvg(art, h = 132) {
  return `<svg class="card__art" viewBox="0 0 200 ${h}" aria-hidden="true">
      <rect width="200" height="${h}" fill="#eef6fb" />
      <circle cx="163" cy="30" r="15" fill="#dbeefb" />
      <g fill="none" stroke="#4a9fe0" stroke-width="3.4" stroke-linejoin="round" stroke-linecap="round">${ART[art]}</g>
    </svg>`;
}

function renderStore() {
  const q = activeQuery.trim().toLowerCase();
  const items = PRODUCTS.filter(
    (p) =>
      (activeFilter === "all" || p.cat === activeFilter) &&
      (!q || p.name.toLowerCase().includes(q) || p.cat.includes(q)),
  );

  gridEl.innerHTML = items
    .map(
      (p, i) => `
    <article class="card" style="animation-delay:${0.04 + i * 0.05}s">
      ${productSvg(p.art)}
      <div class="card__body">
        <div class="card__name">${p.name}</div>
        <div class="card__meta">
          <span class="card__price">$${p.price.toFixed(2)}</span>
          <span class="card__stock">${p.stock}</span>
        </div>
      </div>
    </article>`,
    )
    .join("");

  gridEmptyEl.hidden = items.length > 0;
  const scope = activeFilter === "all" ? "" : ` in ${activeFilter}`;
  gearNoteEl.textContent = q
    ? `${items.length} result${items.length === 1 ? "" : "s"} for "${activeQuery}"${scope}`
    : `${items.length} products${scope}, live inventory from the store`;
}

function setFilter(next) {
  activeFilter = next;
  for (const el of document.querySelectorAll("[data-filter]")) {
    el.classList.toggle("is-active", el.dataset.filter === next);
  }
  renderStore();
}

for (const el of [chipbarEl, navLinksEl]) {
  el.addEventListener("click", (e) => {
    const btn = e.target.closest("[data-filter]");
    if (!btn) return;
    setFilter(btn.dataset.filter);
    if (el === navLinksEl) document.getElementById("gear").scrollIntoView({ block: "start" });
  });
}

searchToggleEl.addEventListener("click", () => {
  const open = searchEl.classList.toggle("is-open");
  searchToggleEl.setAttribute("aria-expanded", String(open));
  if (open) searchInputEl.focus();
  else {
    searchInputEl.value = "";
    activeQuery = "";
    renderStore();
  }
});

searchInputEl.addEventListener("input", () => {
  activeQuery = searchInputEl.value;
  renderStore();
  if (activeQuery) document.getElementById("gear").scrollIntoView({ block: "start" });
});

/* cart */
function renderCart() {
  cartItemsEl.innerHTML = CART.map(
    (item) => `
    <li class="drawer__item">
      <span class="drawer__thumb">
        <svg viewBox="60 25 80 80" aria-hidden="true">
          <g fill="none" stroke="#4a9fe0" stroke-width="4" stroke-linejoin="round" stroke-linecap="round">${ART[item.art]}</g>
        </svg>
      </span>
      <span class="drawer__meta">
        <span class="drawer__name">${item.name}</span>
        <span class="drawer__qty">${item.variant} &middot; qty ${item.qty}</span>
      </span>
      <span class="drawer__price">$${(item.price * item.qty).toFixed(2)}</span>
    </li>`,
  ).join("");
  const total = CART.reduce((sum, i) => sum + i.price * i.qty, 0);
  cartTotalEl.textContent = `$${total.toFixed(2)}`;
  document.getElementById("cartCount").textContent = String(
    CART.reduce((n, i) => n + i.qty, 0),
  );
}

function openCart() {
  cartDrawerEl.classList.add("is-open");
  cartDrawerEl.setAttribute("aria-hidden", "false");
  cartToggleEl.setAttribute("aria-expanded", "true");
  scrimEl.hidden = false;
}

function closeCart() {
  cartDrawerEl.classList.remove("is-open");
  cartDrawerEl.setAttribute("aria-hidden", "true");
  cartToggleEl.setAttribute("aria-expanded", "false");
  scrimEl.hidden = true;
}

cartToggleEl.addEventListener("click", () =>
  cartDrawerEl.classList.contains("is-open") ? closeCart() : openCart(),
);
cartCloseEl.addEventListener("click", closeCart);
scrimEl.addEventListener("click", closeCart);

/* test data table */
function renderTestData() {
  dataTableEl.innerHTML = TEST_ORDERS.map(
    (o) => `
    <div class="datarow">
      <span class="datarow__id">${o.id}</span>
      <span class="datarow__email">${o.email}</span>
      <span class="datarow__state ${o.tone ? "datarow__state--" + o.tone : ""}">${o.state}</span>
      <button class="datarow__ask" data-ask="Where is my order ${o.id}? My email is ${o.email}">Ask about it</button>
    </div>`,
  ).join("");
}

/* ---------- chat ---------- */
function extractCitations(text) {
  const ids = [];
  const clean = text
    .replace(CITATION_RE, (_, id) => {
      ids.push(id);
      return "";
    })
    .replace(/\s*[—–]\s*/g, " - ")
    .replace(/[ \t]{2,}/g, " ")
    .replace(/ ([.,!?])/g, "$1")
    .trim();
  return { clean, ids: [...new Set(ids)] };
}

function prettySource(id) {
  return id.replace(/^policy-/, "").replace(/-/g, " ");
}

function buildChips(target, delayBase = 0.26) {
  const chips = document.createElement("div");
  chips.className = "chips";
  SUGGESTIONS.forEach((s, i) => {
    const chip = document.createElement("button");
    chip.className = "chip";
    chip.type = "button";
    chip.textContent = s.label;
    chip.style.animationDelay = `${delayBase + i * 0.07}s`;
    chip.addEventListener("click", () => {
      inputEl.value = s.text;
      formEl.requestSubmit();
    });
    chips.appendChild(chip);
  });
  target.appendChild(chips);
}

function renderWelcome() {
  const wrap = document.createElement("div");
  wrap.className = "welcome";
  wrap.id = "welcome";
  wrap.innerHTML = `
    <div class="emblem">
      <svg class="emblem__ring" viewBox="0 0 128 128" aria-hidden="true">
        <defs>
          <path id="ring" d="M64,64 m-50,0 a50,50 0 1,1 100,0 a50,50 0 1,1 -100,0" />
        </defs>
        <text><textPath href="#ring">AURORA OUTFITTERS &#183; CUSTOMER SUPPORT &#183; </textPath></text>
      </svg>
      <div class="emblem__core">
        <svg viewBox="0 0 40 40" aria-hidden="true">
          <path class="peak" d="M5 32 L15 13 L21 23 L26 15 L35 32 Z" />
          <circle class="sun" cx="29" cy="10" r="3.2" />
        </svg>
      </div>
    </div>
    <div class="welcome__title">How can we <em>help?</em></div>
    <p class="welcome__sub">Ask about our gear, track an order, or check our shipping and return policies.</p>`;
  buildChips(wrap);
  messagesEl.appendChild(wrap);
}

function linkify(bubble, text) {
  // model text is plain, but order replies carry raw tracking urls, make them clickable
  let last = 0;
  text.replace(URL_RE, (url, _g, offset) => {
    bubble.appendChild(document.createTextNode(text.slice(last, offset)));
    const a = document.createElement("a");
    a.href = url;
    a.target = "_blank";
    a.rel = "noopener noreferrer";
    a.textContent = url;
    bubble.appendChild(a);
    last = offset + url.length;
    return url;
  });
  bubble.appendChild(document.createTextNode(text.slice(last)));
}

function addMessage(role, text, meta) {
  document.getElementById("welcome")?.remove();
  document.getElementById("actions")?.remove();

  const msg = document.createElement("div");
  msg.className = `msg msg--${role}`;

  const { clean, ids } = role === "agent" ? extractCitations(text) : { clean: text, ids: [] };
  const bubble = document.createElement("div");
  bubble.className = "msg__bubble";
  linkify(bubble, clean);
  msg.appendChild(bubble);

  if (ids.length) {
    const sources = document.createElement("div");
    sources.className = "msg__sources";
    for (const id of ids) {
      const tag = document.createElement("span");
      tag.className = "source";
      tag.textContent = prettySource(id);
      sources.appendChild(tag);
    }
    msg.appendChild(sources);
  }
  if (meta) {
    const metaEl = document.createElement("div");
    metaEl.className = "msg__meta";
    const label = INTENT_LABELS[meta.intent] || "Answer";
    metaEl.innerHTML = `<span>${label}</span> &middot; ${meta.latency_s}s`;
    msg.appendChild(metaEl);
  }
  messagesEl.appendChild(msg);
  messagesEl.scrollTop = messagesEl.scrollHeight;
}

// after a reply, offer a way out: more examples or a clean slate
function renderActions() {
  document.getElementById("actions")?.remove();
  const row = document.createElement("div");
  row.className = "actions";
  row.id = "actions";

  const examples = document.createElement("button");
  examples.className = "action";
  examples.type = "button";
  examples.innerHTML =
    '<svg viewBox="0 0 24 24"><path d="M4 6h16M4 12h16M4 18h10" /></svg> Show examples';
  examples.addEventListener("click", () => {
    row.remove();
    const wrap = document.createElement("div");
    wrap.className = "welcome";
    wrap.id = "welcome";
    wrap.style.margin = "6px 0 0";
    buildChips(wrap, 0.04);
    messagesEl.appendChild(wrap);
    messagesEl.scrollTop = messagesEl.scrollHeight;
  });

  const fresh = document.createElement("button");
  fresh.className = "action";
  fresh.type = "button";
  fresh.innerHTML =
    '<svg viewBox="0 0 24 24"><path d="M20 12a8 8 0 1 1-2.3-5.6" /><path d="M20 4v4h-4" /></svg> New conversation';
  fresh.addEventListener("click", resetChat);

  row.append(examples, fresh);
  messagesEl.appendChild(row);
  messagesEl.scrollTop = messagesEl.scrollHeight;
}

function resetChat() {
  history = [];
  messagesEl.innerHTML = "";
  renderWelcome();
  inputEl.focus();
}

function showTyping() {
  const msg = document.createElement("div");
  msg.className = "msg msg--agent";
  msg.id = "typing";
  msg.innerHTML = `<div class="msg__bubble dots"><span></span><span></span><span></span></div>`;
  messagesEl.appendChild(msg);
  messagesEl.scrollTop = messagesEl.scrollHeight;
}

function setBusy(busy) {
  inputEl.disabled = busy;
  sendEl.disabled = busy;
  if (!busy) inputEl.focus();
}

async function send(text) {
  addMessage("user", text);
  history.push({ role: "user", content: text });
  setBusy(true);
  showTyping();

  // the backend scales to zero, so the first request after idle can cold start
  // and return a transient 503, retry a couple of times before giving up
  try {
    const data = await postWithRetry();
    document.getElementById("typing")?.remove();
    addMessage("agent", data.response, { intent: data.intent, latency_s: data.latency_s });
    history.push({ role: "assistant", content: data.response });
    ping();
    renderActions();
  } catch (err) {
    document.getElementById("typing")?.remove();
    addMessage(
      "agent",
      "Sorry, I could not reach the support service just now. Please try again in a moment.",
    );
    renderActions();
    console.error(err);
  } finally {
    setBusy(false);
  }
}

async function postWithRetry(attempts = 3, delayMs = 2500) {
  for (let i = 0; i < attempts; i++) {
    try {
      const res = await fetch(`${API_BASE}/chat`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ messages: history }),
      });
      if (res.ok) return res.json();
      if (res.status !== 503 || i === attempts - 1) throw new Error(`request failed (${res.status})`);
    } catch (err) {
      if (i === attempts - 1) throw err;
    }
    await new Promise((r) => setTimeout(r, delayMs));
  }
}

/* notification sound, synthesized so there is no audio file to ship */
const MUTE_KEY = "aurora_chat_muted";
let audioCtx = null;
let muted = localStorage.getItem(MUTE_KEY) === "1";

function unlockAudio() {
  if (audioCtx) return;
  const Ctx = window.AudioContext || window.webkitAudioContext;
  if (!Ctx) return;
  audioCtx = new Ctx();
}

// two soft sine notes with a quick decay, quiet enough to sit under a page
function ping(notes = [880, 1174.7], gain = 0.05) {
  if (muted || !audioCtx) return;
  if (audioCtx.state === "suspended") audioCtx.resume();
  notes.forEach((freq, i) => {
    const t = audioCtx.currentTime + i * 0.1;
    const osc = audioCtx.createOscillator();
    const amp = audioCtx.createGain();
    osc.type = "sine";
    osc.frequency.setValueAtTime(freq, t);
    amp.gain.setValueAtTime(0, t);
    amp.gain.linearRampToValueAtTime(gain, t + 0.012);
    amp.gain.exponentialRampToValueAtTime(0.0001, t + 0.26);
    osc.connect(amp).connect(audioCtx.destination);
    osc.start(t);
    osc.stop(t + 0.3);
  });
}

function syncSoundButton() {
  soundEl.classList.toggle("is-muted", muted);
  soundEl.setAttribute("aria-pressed", String(!muted));
  soundEl.setAttribute("aria-label", muted ? "Unmute notification sound" : "Mute notification sound");
}

soundEl.addEventListener("click", () => {
  muted = !muted;
  localStorage.setItem(MUTE_KEY, muted ? "1" : "0");
  syncSoundButton();
  if (!muted) ping([1046.5], 0.04);
});

// browsers only allow audio after a gesture, so arm the context on the first one
for (const evt of ["pointerdown", "keydown"]) {
  window.addEventListener(evt, unlockAudio, { once: true });
}

/* ---------- widget shell ---------- */
const CLOSED_KEY = "aurora_chat_closed";

function hideTeaser() {
  if (teaserEl.hidden) return;
  teaserEl.classList.add("is-leaving");
  setTimeout(() => {
    teaserEl.hidden = true;
    teaserEl.classList.remove("is-leaving");
  }, 240);
}

function openChat() {
  widgetEl.classList.add("is-open");
  launcherEl.setAttribute("aria-expanded", "true");
  panelEl.setAttribute("aria-hidden", "false");
  hideTeaser();
  badgeEl.hidden = true;
  if (!opened) {
    opened = true;
    renderWelcome();
  }
  setTimeout(() => inputEl.focus(), 280);
}

function closeChat() {
  sessionStorage.setItem(CLOSED_KEY, "1");
  widgetEl.classList.remove("is-open");
  launcherEl.setAttribute("aria-expanded", "false");
  panelEl.setAttribute("aria-hidden", "true");
  launcherEl.focus();
}

// anything on the page can hand a question to the assistant
function askFromPage(text) {
  closeCart();
  openChat();
  setTimeout(() => {
    inputEl.value = text;
    formEl.requestSubmit();
  }, 320);
}

document.addEventListener("click", (e) => {
  const asker = e.target.closest("[data-ask]");
  if (asker) {
    askFromPage(asker.dataset.ask);
    return;
  }
  if (e.target.closest("[data-open-chat]")) {
    closeCart();
    openChat();
  }
});

launcherEl.addEventListener("click", () => {
  widgetEl.classList.contains("is-open") ? closeChat() : openChat();
});

minimizeEl.addEventListener("click", closeChat);
restartEl.addEventListener("click", resetChat);

teaserCloseEl.addEventListener("click", (e) => {
  e.stopPropagation();
  hideTeaser();
  badgeEl.hidden = true;
});

teaserEl.addEventListener("click", openChat);

document.addEventListener("keydown", (e) => {
  if (e.key !== "Escape") return;
  if (cartDrawerEl.classList.contains("is-open")) closeCart();
  else if (widgetEl.classList.contains("is-open")) closeChat();
});

formEl.addEventListener("submit", (e) => {
  e.preventDefault();
  const text = inputEl.value.trim();
  if (!text) return;
  inputEl.value = "";
  send(text);
});

/* opening behaviour: this is a demo, so the panel introduces itself.
   it springs open from the launcher a beat after load, which shows the
   visitor where the widget lives instead of leaving a mystery circle. */
if (sessionStorage.getItem(CLOSED_KEY)) {
  setTimeout(() => {
    if (widgetEl.classList.contains("is-open")) return;
    teaserEl.hidden = false;
    badgeEl.hidden = false;
  }, 3200);
} else {
  setTimeout(() => {
    if (widgetEl.classList.contains("is-open")) return;
    openChat();
    ping();
  }, 1100);
}

syncSoundButton();
renderStore();
renderCart();
renderTestData();
