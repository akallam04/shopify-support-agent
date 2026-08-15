const API_BASE = document.querySelector('meta[name="api-base"]')?.content || "";

const widgetEl = document.querySelector(".widget");
const launcherEl = document.getElementById("launcher");
const panelEl = document.getElementById("panel");
const teaserEl = document.getElementById("teaser");
const teaserCloseEl = document.getElementById("teaserClose");
const badgeEl = document.getElementById("badge");
const minimizeEl = document.getElementById("minimize");
const messagesEl = document.getElementById("messages");
const formEl = document.getElementById("composer");
const inputEl = document.getElementById("input");
const sendEl = document.getElementById("send");
const gridEl = document.getElementById("grid");

// full conversation, sent to the agent every turn since the api is stateless
const history = [];
let opened = false;

// short labels on the chips, fuller prompts sent to the agent
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

// storefront products, mirrors the seeded catalog the agent answers from
const PRODUCTS = [
  { name: "Stormline Rain Jacket", price: "179.99", stock: "In stock", art: "jacket" },
  { name: "Summit Ridge 2-Person Tent", price: "289.99", stock: "In stock", art: "tent" },
  { name: "Ember 750 Sleeping Bag", price: "249.99", stock: "In stock", art: "bag" },
  { name: "Alpine Crossing Boots", price: "219.99", stock: "Low stock", art: "boot" },
  { name: "Cascade 40L Backpack", price: "149.95", stock: "In stock", art: "pack" },
  { name: "Glacier Point Down Parka", price: "329.99", stock: "In stock", art: "parka" },
  { name: "Peakfinder Headlamp 600", price: "59.99", stock: "In stock", art: "lamp" },
  { name: "Wander Insulated Bottle", price: "39.95", stock: "In stock", art: "bottle" },
];

const ART = {
  jacket: '<path d="M70 40 L100 28 L130 40 L138 96 H62 Z" /><path d="M100 28 V96" />',
  tent: '<path d="M100 30 L145 96 H55 Z" /><path d="M100 30 V96 M78 96 L100 62 L122 96" />',
  bag: '<rect x="58" y="46" width="84" height="48" rx="24" /><path d="M78 46 v48 M122 46 v48" />',
  boot: '<path d="M72 34 h26 v34 l40 16 v20 H72 Z" /><path d="M72 84 h66" />',
  pack: '<rect x="70" y="38" width="60" height="58" rx="14" /><path d="M86 38 v-8 a14 14 0 0 1 28 0 v8 M70 64 h60" />',
  parka: '<path d="M68 42 L100 30 L132 42 L136 98 H64 Z" /><path d="M100 30 V98 M84 52 h-8 M124 52 h-8" />',
  lamp: '<rect x="72" y="50" width="42" height="30" rx="9" /><path d="M114 58 l22 -10 v34 l-22 -10 M72 65 h-14" />',
  bottle: '<path d="M88 38 h24 v10 l6 12 v40 a6 6 0 0 1 -6 6 h-24 a6 6 0 0 1 -6 -6 v-40 l6 -12 Z" /><path d="M82 74 h36" />',
};

const URL_RE = /(https?:\/\/[^\s]+)/g;
const CITATION_RE = /\s*\[([a-z0-9][a-z0-9-]*)\]/g;

function renderStore() {
  if (!gridEl) return;
  gridEl.innerHTML = PRODUCTS.map(
    (p, i) => `
    <article class="card" style="animation-delay:${0.05 + i * 0.06}s">
      <svg class="card__art" viewBox="0 0 200 132" aria-hidden="true">
        <rect width="200" height="132" fill="#eef6fb" />
        <circle cx="163" cy="30" r="15" fill="#dbeefb" />
        <g fill="none" stroke="#4a9fe0" stroke-width="3.4" stroke-linejoin="round" stroke-linecap="round">${ART[p.art]}</g>
      </svg>
      <div class="card__body">
        <div class="card__name">${p.name}</div>
        <div class="card__meta">
          <span class="card__price">$${p.price}</span>
          <span class="card__stock">${p.stock}</span>
        </div>
      </div>
    </article>`,
  ).join("");
}

// pull the [id] grounding markers out of the prose and return them separately,
// so the bubble reads cleanly and the sources show as their own tags
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
  const chips = document.createElement("div");
  chips.className = "chips";
  SUGGESTIONS.forEach((s, i) => {
    const chip = document.createElement("button");
    chip.className = "chip";
    chip.type = "button";
    chip.textContent = s.label;
    chip.style.animationDelay = `${0.26 + i * 0.07}s`;
    chip.addEventListener("click", () => {
      inputEl.value = s.text;
      formEl.requestSubmit();
    });
    chips.appendChild(chip);
  });
  wrap.appendChild(chips);
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
  } catch (err) {
    document.getElementById("typing")?.remove();
    addMessage(
      "agent",
      "Sorry, I could not reach the support service just now. Please try again in a moment.",
    );
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

/* widget shell */
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
  widgetEl.classList.remove("is-open");
  launcherEl.setAttribute("aria-expanded", "false");
  panelEl.setAttribute("aria-hidden", "true");
  launcherEl.focus();
}

launcherEl.addEventListener("click", () => {
  widgetEl.classList.contains("is-open") ? closeChat() : openChat();
});

minimizeEl.addEventListener("click", closeChat);

teaserCloseEl.addEventListener("click", (e) => {
  e.stopPropagation();
  hideTeaser();
  badgeEl.hidden = true;
});

teaserEl.addEventListener("click", openChat);

for (const btn of document.querySelectorAll("[data-open-chat]")) {
  btn.addEventListener("click", openChat);
}

document.addEventListener("keydown", (e) => {
  if (e.key === "Escape" && widgetEl.classList.contains("is-open")) closeChat();
});

formEl.addEventListener("submit", (e) => {
  e.preventDefault();
  const text = inputEl.value.trim();
  if (!text) return;
  inputEl.value = "";
  send(text);
});

// the proactive nudge a real support widget does after a beat
setTimeout(() => {
  if (widgetEl.classList.contains("is-open")) return;
  teaserEl.hidden = false;
  badgeEl.hidden = false;
}, 3200);

renderStore();
