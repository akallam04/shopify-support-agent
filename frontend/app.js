const API_BASE = document.querySelector('meta[name="api-base"]')?.content || "";

const messagesEl = document.getElementById("messages");
const formEl = document.getElementById("composer");
const inputEl = document.getElementById("input");
const sendEl = document.getElementById("send");

// full conversation, sent to the agent every turn since the api is stateless
const history = [];

const SUGGESTIONS = [
  "Do you have waterproof jackets?",
  "What is your return policy?",
  "Where is my order #1001? (maya.thompson@example.com)",
  "Do you ship to Canada?",
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

const URL_RE = /(https?:\/\/[^\s]+)/g;

function renderWelcome() {
  const wrap = document.createElement("div");
  wrap.className = "welcome";
  wrap.id = "welcome";
  wrap.innerHTML = `
    <div class="welcome__badge">
      <svg viewBox="0 0 40 40" aria-hidden="true">
        <path class="peak" d="M4 33 L14 12 L20 22 L25 14 L36 33 Z" />
        <circle class="sun" cx="29" cy="9" r="3.4" />
      </svg>
    </div>
    <div>
      <div class="welcome__title">How can we help?</div>
      <p class="welcome__sub">Ask about our gear, track an order, or check our shipping and return policies.</p>
    </div>`;
  const chips = document.createElement("div");
  chips.className = "chips";
  SUGGESTIONS.forEach((text, i) => {
    const chip = document.createElement("button");
    chip.className = "chip";
    chip.type = "button";
    chip.textContent = text;
    // stagger each chip in after the intro copy has landed
    chip.style.animationDelay = `${0.26 + i * 0.07}s`;
    chip.addEventListener("click", () => {
      inputEl.value = text;
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
  const bubble = document.createElement("div");
  bubble.className = "msg__bubble";
  linkify(bubble, text);
  msg.appendChild(bubble);
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
  msg.innerHTML = `<div class="msg__bubble wave"><span></span><span></span><span></span><span></span><span></span></div>`;
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

formEl.addEventListener("submit", (e) => {
  e.preventDefault();
  const text = inputEl.value.trim();
  if (!text) return;
  inputEl.value = "";
  send(text);
});

renderWelcome();
inputEl.focus();
