const test = require("node:test");
const assert = require("node:assert");
const { createConversation } = require("../../frontend/conversation.js");

test("a reply that started before a reset cannot update the new chat", async () => {
  const conversation = createConversation();
  const chat = { history: ["Please cancel order #1023"], token: "old-session" };
  const request = conversation.begin();
  const slowReply = new Promise((resolve) => setTimeout(() => resolve({ response: "old reply", session_state: "old-token" }), 20));
  conversation.reset();
  chat.history = [];
  chat.token = null;
  const data = await slowReply;
  if (request.isCurrent()) {
    chat.history.push(data.response);
    chat.token = data.session_state;
  }
  assert.deepStrictEqual(chat, { history: [], token: null });
  assert.strictEqual(request.signal.aborted, true);
});

test("a request started after the reset is current", () => {
  const conversation = createConversation();
  conversation.begin();
  conversation.reset();
  const fresh = conversation.begin();
  assert.strictEqual(fresh.isCurrent(), true);
  assert.strictEqual(fresh.signal.aborted, false);
});
