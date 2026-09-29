// Run with: node --test tests/frontend.test.cjs
// Isolated DOM/network fakes exercise the actual inline frontend, without a model.
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const assert = require("node:assert/strict");
const { test } = require("node:test");
const crypto = require("node:crypto").webcrypto;
const source = fs.readFileSync(path.join(__dirname, "../index.html"), "utf8").split("<script>")[1].split("</script>")[0];
const SESSION = "scavagent.session_id";
const PENDING = "scavagent.pending_message.v1";
const MESSAGE_ID = "123e4567-e89b-42d3-a456-426614174000";
const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;

class Element {
  constructor(tag = "div") {
    this.tagName = tag; this.children = []; this.value = ""; this.style = {};
    this.className = ""; this.scrollHeight = 44; this.listeners = {}; this._text = ""; this.textChanges = [];
    this.classList = {
      add: name => { this.className += " " + name; },
      remove: name => { this.className = this.className.split(" ").filter(x => x !== name).join(" "); },
      contains: name => this.className.split(" ").includes(name),
    };
  }
  set textContent(value) { this._text = value; this.children = []; this.textChanges.push(value); }
  get textContent() { return this._text + this.children.map(x => x.textContent).join(""); }
  appendChild(node) { this.children.push(node); node.parent = this; return node; }
  append(...nodes) { nodes.forEach(node => this.appendChild(node)); }
  replaceChildren(...nodes) { this.children = []; this._text = ""; this.append(...nodes); }
  setAttribute(key, value) { this[key] = value; }
  addEventListener(key, value) { this.listeners[key] = value; }
  remove() { this.parent.children = this.parent.children.filter(node => node !== this); }
  querySelector(selector) { return this.children.find(node => node.className.split(" ").includes(selector.slice(1))) || null; }
  insertBefore(node, before) { node.parent = this; this.children.splice(this.children.indexOf(before), 0, node); }
}

function fakeClock() {
  let now = Date.now(), sequence = 0;
  const timers = new Map();
  class ClockDate extends Date { static now() { return now; } }
  return {
    Date: ClockDate,
    now: () => now,
    setTimeout(callback, delay = 0) {
      const id = ++sequence;
      timers.set(id, { callback, at: now + delay });
      return id;
    },
    clearTimeout: id => timers.delete(id),
    async advance(milliseconds) {
      const target = now + milliseconds;
      await settled();
      while (true) {
        const next = [...timers.entries()].filter(([, timer]) => timer.at <= target).sort((a, b) => a[1].at - b[1].at)[0];
        if (!next) break;
        const [id, timer] = next;
        now = timer.at; timers.delete(id); timer.callback();
        await settled();
      }
      now = target;
      await settled();
    },
  };
}

function harness(initial = {}, replies = [], options = {}) {
  const elements = Object.fromEntries(["trail", "messages", "message", "send", "status", "composer", "welcome"].map(id => [id, new Element()]));
  const storage = new Map(Object.entries(initial));
  const requests = [], writes = [], geo = [];
  const clock = fakeClock();
  let activeRequests = 0, maxActiveRequests = 0;
  const document = {
    visibilityState: "visible", listeners: {},
    getElementById: id => elements[id],
    createElement: tag => new Element(tag),
    createTextNode: text => { const node = new Element("#text"); node.textContent = text; return node; },
    addEventListener(key, value) { this.listeners[key] = value; },
  };
  const localStorage = {
    getItem(key) { if (options.storageUnavailable) throw Error("Storage unavailable"); return storage.get(key) || null; },
    setItem(key, value) {
      if (options.storageUnavailable) throw Error("Storage unavailable");
      writes.push(key); storage.set(key, value);
    },
    removeItem(key) { if (options.storageUnavailable) throw Error("Storage unavailable"); storage.delete(key); },
  };
  const context = {
    document, localStorage,
    navigator: { geolocation: { getCurrentPosition: (...args) => geo.push(args) } },
    window: { location: { origin: "http://localhost:8876" } },
    URL, crypto, Uint8Array, AbortController, Date: clock.Date,
    setInterval: () => 0, setTimeout: clock.setTimeout, clearTimeout: clock.clearTimeout,
    fetch: async (url, request) => {
      const call = { url, request, storage: Object.fromEntries(storage), at: clock.now() };
      requests.push(call);
      activeRequests += 1; maxActiveRequests = Math.max(maxActiveRequests, activeRequests);
      let onAbort;
      try {
        const reply = replies.shift();
        if (!reply) throw Error("Unexpected fetch: " + url);
        if (reply instanceof Error) throw reply;
        return await Promise.race([
          typeof reply === "function" ? reply(call) : reply,
          new Promise((_, reject) => {
            onAbort = () => reject(Object.assign(new Error("Aborted"), { name: "AbortError" }));
            request.signal.addEventListener("abort", onAbort, { once: true });
            if (request.signal.aborted) onAbort();
          }),
        ]);
      } finally {
        request.signal.removeEventListener("abort", onAbort);
        activeRequests -= 1;
      }
    },
  };
  vm.createContext(context);
  vm.runInContext(source, context);
  return { elements, storage, requests, writes, geo, document, clock, maxActiveRequests: () => maxActiveRequests, run: code => vm.runInContext(code, context) };
}
const response = (body, status = 200) => ({ ok: status >= 200 && status < 300, status, json: async () => body });
const answer = call => response({ response: "Saved reply", session_id: JSON.parse(call.request.body).session_id, tool_calls: [] });
const restored = (session_id, messages = []) => response({ session_id, messages });
const settled = () => new Promise(resolve => setImmediate(resolve));
const posted = h => h.requests.filter(call => call.url === "/chat").map(call => JSON.parse(call.request.body));

test("first send persists a UUID session before its pending turn and uses it for later messages", async () => {
  const h = harness({}, [answer, answer]);
  h.elements.message.value = "start";
  await h.run("send()");
  const [first] = posted(h);
  assert.match(first.session_id, UUID);
  assert.match(first.client_message_id, UUID);
  assert.notEqual(first.session_id, first.client_message_id);
  assert.deepEqual(h.writes.slice(0, 2), [SESSION, PENDING]);
  assert.equal(h.requests[0].storage[SESSION], first.session_id);
  assert.equal(JSON.parse(h.requests[0].storage[PENDING]).session_id, first.session_id);
  h.elements.message.value = "next";
  await h.run("send()");
  const second = posted(h)[1];
  assert.equal(second.session_id, first.session_id);
  assert.notEqual(second.client_message_id, first.client_message_id);
});

test("an invalid stored session is replaced before any request uses it", async () => {
  const h = harness({ [SESSION]: "../x" }, [answer]);
  await settled();
  // Invalid IDs must not be sent to history during startup either.
  assert.deepEqual(h.requests, []);
  h.elements.message.value = "start";
  await h.run("send()");
  const [sent] = posted(h);
  assert.match(sent.session_id, UUID);
  assert.equal(h.storage.get(SESSION), sent.session_id);
  assert.equal(h.requests[0].storage[SESSION], sent.session_id);
  assert.equal(JSON.parse(h.requests[0].storage[PENDING]).session_id, sent.session_id);
  const reload = harness(Object.fromEntries(h.storage), [restored(sent.session_id), answer]);
  await settled();
  reload.elements.message.value = "next";
  await reload.run("send()");
  assert.equal(posted(reload)[0].session_id, sent.session_id);
});

test("lost first response survives reload with the same session, text and request ID even if GPS disappears", async () => {
  const firstTab = harness({}, [new Error("Response lost")]);
  firstTab.geo[0][0]({ coords: { latitude: 40.78, longitude: -73.97, accuracy: 12 }, timestamp: Date.now() });
  firstTab.elements.message.value = "ready";
  void firstTab.run("send()");
  await settled(); // Close this simulated tab during its automatic wait.
  const first = posted(firstTab)[0];
  assert.equal(first.location.source, "browser");
  const reload = harness(Object.fromEntries(firstTab.storage), [restored(first.session_id), answer]);
  await settled();
  await reload.run("send()");
  const retry = posted(reload)[0];
  assert.equal(retry.session_id, first.session_id);
  assert.equal(retry.client_message_id, first.client_message_id);
  assert.equal(retry.message, first.message);
  assert.equal(retry.location, undefined);
  assert(!reload.storage.has(PENDING));
});

test("409 keeps the pending turn for an automatic retry with a refreshed location", async () => {
  const h = harness({}, [response({}, 409), answer]);
  h.elements.message.value = "ready";
  const sending = h.run("send()");
  await settled();
  const first = posted(h)[0];
  assert(h.storage.has(PENDING));
  assert.equal(h.elements.message.readOnly, true);
  assert.equal(posted(h).length, 1);
  h.geo.at(-1)[0]({ coords: { latitude: 40.79, longitude: -73.96, accuracy: 8 }, timestamp: h.clock.now() });
  await h.clock.advance(5000);
  await sending;
  const retry = posted(h)[1];
  assert.equal(retry.session_id, first.session_id);
  assert.equal(retry.client_message_id, first.client_message_id);
  assert.equal(retry.message, first.message);
  assert.equal(retry.location.point.lat, 40.79);
  assert(!h.storage.has(PENDING));
});

for (const existingSession of [null, "existing-session"]) {
  test("legacy null-session pending message migrates with " + (existingSession ? "an existing session" : "a new session"), async () => {
    const initial = { [PENDING]: JSON.stringify({ message: "ready", session_id: null, client_message_id: MESSAGE_ID }) };
    if (existingSession) initial[SESSION] = existingSession;
    const h = harness(initial, [...(existingSession ? [restored(existingSession)] : []), new Error("Response lost")]);
    await settled();
    void h.run("send()");
    await settled(); // Reload while the migrated message is awaiting its reply.
    const sent = posted(h)[0];
    if (existingSession) assert.equal(sent.session_id, existingSession);
    else assert.match(sent.session_id, UUID);
    assert.equal(sent.client_message_id, MESSAGE_ID);
    assert.equal(sent.message, "ready");
    assert.equal(h.storage.get(SESSION), sent.session_id);
    assert.equal(JSON.parse(h.storage.get(PENDING)).session_id, sent.session_id);
    const reload = harness(Object.fromEntries(h.storage), [restored(sent.session_id), answer]);
    await settled();
    await reload.run("send()");
    assert.deepEqual(posted(reload)[0], sent);
  });
}

test("a reply for another session is not rendered or adopted and leaves the same turn retryable", async () => {
  const h = harness({}, [response({ response: "Other session data", session_id: "other-session", tool_calls: [] }), answer]);
  h.elements.message.value = "ready";
  const sending = h.run("send()");
  await settled();
  const first = posted(h)[0];
  assert.equal(h.storage.get(SESSION), first.session_id);
  assert(h.storage.has(PENDING));
  assert(!h.elements.messages.textContent.includes("Other session data"));
  await h.clock.advance(5000);
  await sending;
  assert.deepEqual(posted(h)[1], first);
  assert(!h.storage.has(PENDING));
});

test("repeated pending text does not replace an older restored turn", async () => {
  const session = "saved-session";
  const h = harness({
    [SESSION]: session,
    [PENDING]: JSON.stringify({ message: "ready", session_id: session, client_message_id: MESSAGE_ID }),
  }, [restored(session, [{ role: "user", text: "ready" }, { role: "assistant", text: "Earlier reply", tool_calls: [] }]), answer]);
  await settled();
  await h.run("send()");
  assert.equal(h.elements.messages.children.length, 4);
  assert(h.elements.messages.children[1].textContent.includes("Earlier reply"));
  assert(h.elements.messages.children[3].textContent.includes("Saved reply"));
  assert.equal(posted(h)[0].client_message_id, MESSAGE_ID);
});

test("a pending turn for a different known session is not adopted", async () => {
  const h = harness({
    [SESSION]: "current-session",
    [PENDING]: JSON.stringify({ message: "foreign pending", session_id: "other-session", client_message_id: MESSAGE_ID }),
  }, [restored("current-session"), answer]);
  await settled();
  assert.equal(h.elements.message.value, "");
  h.elements.message.value = "new message";
  await h.run("send()");
  assert.equal(posted(h)[0].session_id, "current-session");
  assert.equal(posted(h)[0].message, "new message");
  assert.notEqual(posted(h)[0].client_message_id, MESSAGE_ID);
});

test("storage being unavailable still preserves the session and request for a same-tab retry", async () => {
  const h = harness({}, [new Error("Response lost"), answer], { storageUnavailable: true });
  h.elements.message.value = "ready";
  const sending = h.run("send()");
  await h.clock.advance(5000);
  await sending;
  assert.match(posted(h)[0].session_id, UUID);
  assert.deepEqual(posted(h)[1], posted(h)[0]);
});

test("safe media and foreground GPS retain their boundaries", async () => {
  const h = harness({}, [answer]);
  for (const url of ["https://evil.invalid/media/x", "data:image/png;base64,a", "/media/%252e%252e/private"]) {
    assert.equal(h.run("safeMediaUrl(" + JSON.stringify(url) + ")"), null);
  }
  assert.equal(h.run('safeMediaUrl("/media/saved")'), "http://localhost:8876/media/saved");
  h.geo[0][0]({ coords: { latitude: 40.78, longitude: -73.97, accuracy: 12 }, timestamp: Date.now() - 91000 });
  assert.equal(h.run("currentLocation()"), null);
  h.document.listeners.visibilitychange();
  assert.equal(h.requests.length, 0);
  h.geo.at(-1)[1]({ code: 1 });
  h.elements.message.value = "typed starting place";
  await h.run("send()");
  assert.equal(posted(h)[0].location, undefined);
});


test("browser abort then two 409s automatically recovers one reply without showing an error", async () => {
  let abortFirst;
  const firstRequest = new Promise((_, reject) => { abortFirst = reject; });
  const h = harness({}, [() => firstRequest, response({}, 409), response({}, 409), answer]);
  const started = h.clock.now();
  h.elements.message.value = "plan a walk";
  const sending = h.run("send()");
  await settled();
  await h.run("send()"); // A manual Send during the original fetch is ignored.
  assert.equal(posted(h).length, 1);
  await h.clock.advance(60000);
  abortFirst(Object.assign(new Error("Safari aborted fetch"), { name: "AbortError" }));
  await settled();
  for (let retry = 0; retry < 3; retry += 1) {
    assert.equal(h.elements.status.textContent, "Still working on your plan…");
    assert.equal(h.elements.send.disabled, true);
    await h.run("send()"); // Also ignored between automatic requests.
    assert.equal(posted(h).length, retry + 1);
    await h.clock.advance(4999);
    assert.equal(posted(h).length, retry + 1);
    await h.clock.advance(1);
  }
  await sending;
  const sends = posted(h);
  assert.equal(sends.length, 4);
  assert.equal(new Set(sends.map(body => body.client_message_id)).size, 1);
  assert.equal(new Set(sends.map(body => body.session_id)).size, 1);
  assert(sends.every(body => body.message === "plan a walk"));
  assert.deepEqual(h.requests.map(call => call.at - started), [0, 65000, 70000, 75000]);
  assert.equal(h.maxActiveRequests(), 1);
  assert.equal(h.elements.messages.children.length, 2);
  assert.equal(h.elements.messages.children.filter(node => node.className === "message assistant").length, 1);
  assert(h.elements.messages.children[1].textContent.includes("Saved reply"));
  assert(!h.elements.status.textChanges.some(value => value.includes("reply didn’t arrive")));
  assert(!h.storage.has(PENDING));
  assert.equal(h.elements.message.disabled, false);
});

test("409s exhaust the 4.5-minute window then preserve the pending turn for manual retry", async () => {
  const replies = Array.from({ length: 54 }, () => response({}, 409));
  const h = harness({}, [...replies, answer]);
  h.elements.message.value = "plan a walk";
  const sending = h.run("send()");
  await h.clock.advance(269999);
  assert.equal(h.elements.status.textContent, "Still working on your plan…");
  assert.equal(h.elements.send.disabled, true);
  assert.equal(posted(h).length, 54);
  const pending = h.storage.get(PENDING);
  await h.clock.advance(1);
  await sending;
  assert.equal(h.elements.status.textContent, "The reply didn’t arrive. Press Send to retry the same message.");
  assert.equal(h.storage.get(PENDING), pending);
  assert.equal(h.elements.send.disabled, false);
  assert.equal(h.elements.message.value, "plan a walk");
  assert.equal(h.elements.messages.children.length, 1);
  await h.clock.advance(30000);
  assert.equal(posted(h).length, 54); // No requests continue beyond the window.
  await h.run("send()");
  assert.deepEqual(posted(h).at(-1), posted(h)[0]);
  assert(!h.storage.has(PENDING));
  assert.equal(h.elements.messages.children.length, 2);
});

test("request timeouts are bounded by the total window and never overlap", async () => {
  const h = harness({}, Array.from({ length: 3 }, () => () => new Promise(() => {})));
  const started = h.clock.now();
  h.elements.message.value = "plan a walk";
  const sending = h.run("send()");
  await h.clock.advance(270000);
  await sending;
  assert.deepEqual(h.requests.map(call => call.at - started), [0, 95000, 190000]);
  assert(h.requests.every(call => call.request.signal.aborted));
  assert.equal(h.maxActiveRequests(), 1);
  assert(h.storage.has(PENDING));
  assert.equal(h.elements.send.disabled, false);
  assert.equal(h.elements.status.textContent, "The reply didn’t arrive. Press Send to retry the same message.");
});

test("a stalled JSON body stays within the request timeout and can recover", async () => {
  const stalled = call => ({
    ok: true, status: 200,
    json: () => new Promise((_, reject) => call.request.signal.addEventListener("abort", () => reject(new Error("Body aborted")), { once: true })),
  });
  const h = harness({}, [stalled, answer]);
  h.elements.message.value = "plan a walk";
  const sending = h.run("send()");
  await h.clock.advance(90000);
  assert.equal(h.elements.status.textContent, "Still working on your plan…");
  await h.clock.advance(5000);
  await sending;
  assert.deepEqual(posted(h)[1], posted(h)[0]);
  assert(!h.storage.has(PENDING));
});

for (const status of [408, 429, 503]) {
  test("transient HTTP " + status + " retries automatically", async () => {
    const h = harness({}, [response({}, status), answer]);
    h.elements.message.value = "plan a walk";
    const sending = h.run("send()");
    await h.clock.advance(5000);
    await sending;
    assert.deepEqual(posted(h)[1], posted(h)[0]);
    assert(!h.storage.has(PENDING));
    assert(!h.elements.status.textChanges.some(value => value.includes("reply didn’t arrive")));
  });
}

test("a permanent 4xx stops immediately and leaves the rejected text editable", async () => {
  const h = harness({}, [response({}, 422)]);
  h.elements.message.value = "plan a walk";
  await h.run("send()");
  assert.equal(h.elements.status.textContent, "The reply didn’t arrive. Press Send to retry the same message.");
  assert.equal(h.elements.message.value, "plan a walk");
  assert.equal(h.elements.message.readOnly, false);
  assert.equal(h.elements.send.disabled, false);
  await h.clock.advance(270000);
  assert.equal(posted(h).length, 1);
});
