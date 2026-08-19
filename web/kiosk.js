/* Kiosk client.
 *
 * Wiring: Anam owns the avatar (video + STT + TTS lip-sync); Claude owns the
 * conversation, on the server, reached over /ws. This file is the glue:
 *
 *   Anam transcript ──▶ ws {user_text} ──▶ Brain ──▶ ws {speak} ──▶ Anam talk stream
 *                                          └──────▶ ws {ui}    ──▶ overlays here
 *
 * With no ANAM_API_KEY the kiosk runs in dev mode: placeholder avatar,
 * browser speech recognition in, browser speech synthesis out. Same protocol,
 * so everything else is exercised for real.
 */

const $ = (id) => document.getElementById(id);
const stage = $("stage");

let CONFIG = { store_name: "", persona_name: "", anam_enabled: false, brain_enabled: false };
let ws = null;
let anam = null;            // Anam client
let talkStream = null;      // current Anam talk stream (one per response)
let micLive = false;        // open-mic state
let devRecognition = null;  // dev-mode webkitSpeechRecognition
let lastForwarded = "";     // last user transcript sent, to dedupe history events
let captionsOn = false;     // CC toggle
let soundOn = true;         // sound toggle - muting never stops the lip sync
let captionBuf = [];        // sentences of the current response, for the caption
let saidRecently = [];      // what SHE just said, to spot the speakers feeding the mic
const ECHO_MIN_LEN = 24;    // shorter than this is always treated as a real person
const ECHO_WINDOW_MS = 6000;

/* ------------------------------------------------------------- utilities */

function money(p) {
  if (p == null || p === "") return "";
  const s = String(p);
  if (s.trim().startsWith("$")) return s;
  const n = parseFloat(s.replace(/[^\d.]/g, ""));
  return Number.isFinite(n) ? `$${n.toFixed(2)}` : s;
}

function toast(msg, ms = 4000) {
  const el = $("toast");
  el.textContent = msg;
  el.hidden = false;
  clearTimeout(el._t);
  el._t = setTimeout(() => (el.hidden = true), ms);
}

/* This file is served from <app-root>/static/kiosk.js, so its own module URL
   tells us the app root exactly — "/" when hosted at the domain root, or
   "/hannah/" when hosted under a subpath. Every API and WebSocket URL is built
   from BASE so the kiosk works in both places with no config. */
const BASE = new URL("../", import.meta.url).pathname;
const api = (path) => BASE + path.replace(/^\//, "");

function send(obj) {
  if (ws && ws.readyState === WebSocket.OPEN) ws.send(JSON.stringify(obj));
}

function setState(value) {
  stage.dataset.state = value;
  $("state-label").textContent =
    { attract: "Idle", waking: "Waking", listening: "Listening", thinking: "Thinking",
      speaking: "Speaking", presenting: "Presenting", farewell: "Goodbye" }[value] || value;
}

/* ------------------------------------------------------------- websocket */

function connectWS() {
  const proto = location.protocol === "https:" ? "wss" : "ws";
  ws = new WebSocket(`${proto}://${location.host}${BASE}ws`);
  ws.onopen = () => send({ type: "hello", client: "web", version: "1.0" });
  ws.onclose = () => setTimeout(connectWS, 1500);
  ws.onmessage = (ev) => {
    let msg;
    try { msg = JSON.parse(ev.data); } catch { return; }
    handleServer(msg);
  };
}

function handleServer(msg) {
  switch (msg.type) {
    case "state":
      setState(msg.value);
      break;
    case "speak":
      speak(msg.text);
      break;
    case "speak_end":
      endSpeech();
      break;
    case "ui":
      handleUI(msg.action, msg.payload || {});
      break;
    case "error":
      if (msg.code === "brain_offline") {
        toast("Conversation brain is offline — set ANTHROPIC_API_KEY. Menus still work.");
      }
      break;
  }
}

/* ------------------------------------------------------------- speech out */

function renderCaption() {
  $("caption-text").textContent = captionBuf.join(" ");
  $("captions").hidden = !captionsOn || captionBuf.length === 0;
}

/** Normalise for comparison: lowercase, letters and digits only. */
function _norm(t) {
  return String(t || "").toLowerCase().replace(/[^a-z0-9 ]/g, " ").replace(/\s+/g, " ").trim();
}

/** On a kiosk the speakers sit next to the microphone, so Anam's STT hears her
    own voice and reports it as the customer talking - she then answers herself,
    over and over. Drop any "transcript" that is really her own words coming
    back, plus grunts too short to be a sentence. */
function looksLikeEcho(text) {
  const n = _norm(text);
  if (!n) return true;                           // empty
  // Anything a person would plausibly say goes straight through. Only a long
  // phrase repeated back almost verbatim is treated as the speakers feeding
  // the mic - matching loosely here silently swallows real customers
  // ("a cigar", "today") because those words appear in her own sentences.
  if (n.length < ECHO_MIN_LEN) return false;
  const now = Date.now();
  saidRecently = saidRecently.filter((s) => now - s.t < ECHO_WINDOW_MS);
  return saidRecently.some((s) => s.text.length >= ECHO_MIN_LEN && s.text.includes(n));
}

function speak(text) {
  saidRecently.push({ text: _norm(text), t: Date.now() });
  if (saidRecently.length > 24) saidRecently.shift();
  captionBuf.push(text);
  if (captionBuf.length > 6) captionBuf.shift();   // keep the caption readable
  renderCaption();
  if (anam) {
    if (!talkStream) talkStream = anam.createTalkMessageStream();
    talkStream.streamMessageChunk(text + " ", false);
  } else if ("speechSynthesis" in window) {
    const u = new SpeechSynthesisUtterance(text);
    u.rate = 1.0; u.pitch = 1.0;
    speechSynthesis.speak(u);
  }
}

function endSpeech() {
  if (talkStream) {
    try { talkStream.endMessage(); } catch {}
    talkStream = null;
  }
}

/** Stop her mid-sentence the instant the customer starts talking. */
function stopSpeaking() {
  captionBuf = [];
  renderCaption();
  if (talkStream) {
    try { talkStream.endMessage(); } catch {}
    talkStream = null;
  }
  if (anam) {
    // The SDK has changed the name of this across versions; take whichever
    // exists. Never call stopStreaming() - that tears down the whole session.
    for (const m of ["interruptPersona", "interrupt", "stopTalking"]) {
      if (typeof anam[m] === "function") {
        try { anam[m](); break; } catch (e) { console.warn("interrupt via " + m + " failed", e); }
      }
    }
  }
  if ("speechSynthesis" in window) { try { speechSynthesis.cancel(); } catch {} }
}

function applySound() {
  const v = $("persona-video");
  if (v) { v.muted = !soundOn; v.volume = soundOn ? 1 : 0; }
  const btn = $("btn-sound");
  btn.classList.toggle("is-on", soundOn);
  btn.setAttribute("aria-pressed", String(soundOn));
  btn.textContent = soundOn ? "\u{1F50A}" : "\u{1F507}";
}

function applyCaptions() {
  const btn = $("btn-cc");
  btn.classList.toggle("is-on", captionsOn);
  btn.setAttribute("aria-pressed", String(captionsOn));
  renderCaption();
}

/* ------------------------------------------------------------- anam */

async function initAnam() {
  const r = await fetch(api("api/anam-session-token"), { method: "POST" });
  if (!r.ok) throw new Error(`token: ${r.status}`);
  const { sessionToken } = await r.json();

  const sdk = await import("https://esm.sh/@anam-ai/js-sdk@latest");
  const { AnamEvent } = await import("https://esm.sh/@anam-ai/js-sdk@latest/dist/module/types");

  anam = sdk.createClient(sessionToken);
  await anam.streamToVideoElement("persona-video");
  $("persona-video").hidden = false;
  $("dev-avatar").hidden = true;

  // User finished a sentence -> newest history entry with role "user"
  anam.addListener(AnamEvent.MESSAGE_HISTORY_UPDATED, (messages) => {
    const last = messages[messages.length - 1];
    if (last && last.role === "user" && last.content && last.content !== lastForwarded) {
      if (looksLikeEcho(last.content)) {
        console.debug("dropped echo/noise:", last.content);
        return;                                  // her own voice, not a customer
      }
      lastForwarded = last.content;
      onUserSpoke();
      send({ type: "user_text", text: last.content });
    }
  });

  anam.addListener(AnamEvent.TALK_STREAM_INTERRUPTED, () => {
    talkStream = null;
    onUserSpoke();               // they talked over her - get her back on screen
    send({ type: "interrupted" });
  });

  micLive = true;
  updateMicButton();
}

function updateMicButton() {
  const btn = $("btn-mic");
  btn.classList.toggle("live", micLive);
  btn.classList.toggle("muted", !micLive);
  $("mic-label").textContent = anam
    ? (micLive ? "Listening" : "Muted")
    : (micLive ? "Listening…" : "Tap to talk");
}

function toggleMic() {
  send({ type: "touch", target: "mic" });
  if (anam) {
    // open-mic by default; the button is a mute toggle
    try { micLive ? anam.muteInputAudio() : anam.unmuteInputAudio(); } catch {}
    micLive = !micLive;
  } else {
    devMicToggle();
  }
  updateMicButton();
}

/* ------------------------------------------------------------- dev mode voice */

function devMicToggle() {
  const SR = window.SpeechRecognition || window.webkitSpeechRecognition;
  if (!SR) { toast("No speech recognition in this browser — use the text bar."); return; }
  if (micLive) { devRecognition?.stop(); micLive = false; return; }
  devRecognition = new SR();
  devRecognition.lang = "en-US";
  devRecognition.continuous = true;
  devRecognition.interimResults = false;
  devRecognition.onresult = (e) => {
    const text = e.results[e.results.length - 1][0].transcript.trim();
    if (text) { onUserSpoke(); send({ type: "user_text", text }); }
  };
  devRecognition.onend = () => { if (micLive) devRecognition.start(); };
  devRecognition.start();
  micLive = true;
}

/* ------------------------------------------------------------- ui actions */

function handleUI(action, p) {
  switch (action) {
    case "show_product_frame": showFrame(p); break;
    case "show_media": showMedia(p); break;
    case "open_menu": openMenu(p.menu_id); break;
    case "close_overlay": closeAll(); break;
    case "show_web_page": openPip(p.url, p.title || "", p.id || "page"); break;
  }
}

let frameProduct = null;

/* ---- frame minimize / restore ----------------------------------------
   Rule: the customer must always be able to see her while talking to her.
   The moment they speak, a shown product frame shrinks to a bottom-right
   thumbnail; tapping the thumbnail restores it. A new recommendation always
   comes back full size. */

function frameIsShown() {
  return !$("product-frame").hidden;
}

function minimizeFrame() {
  const el = $("product-frame");
  if (el.hidden || el.classList.contains("minimized")) return;
  el.classList.add("minimized");
}

function restoreFrame() {
  $("product-frame").classList.remove("minimized");
}

/** Called whenever the customer speaks or types.
    Two rules: she stops talking immediately, and she stays visible. */
function onUserSpoke() {
  stopSpeaking();
  if (frameIsShown()) minimizeFrame();
}

function showFrame(p) {
  frameProduct = p;
  restoreFrame();               // a fresh recommendation is always full size
  $("frame-img").src = p.image_url || "";
  $("frame-brand").textContent = p.brand || "";
  $("frame-title").textContent = p.title || "";
  $("frame-price").textContent = p.price ? `${money(p.price)}${p.price_stale ? " (confirm at counter)" : ""}` : "";
  const dl = $("frame-attrs");
  dl.innerHTML = "";
  const rows = { Size: p.size, Wrapper: p.wrapper, Strength: p.strength, ...(p.attributes || {}) };
  for (const [k, v] of Object.entries(rows)) {
    if (!v) continue;
    const dt = document.createElement("dt"); dt.textContent = k;
    const dd = document.createElement("dd"); dd.textContent = v;
    dl.append(dt, dd);
  }
  $("frame-video").hidden = !p.has_video;
  $("frame-3d").hidden = !p.has_model_3d;
  $("product-frame").hidden = false;
  $("menu-drawer").hidden = true;
  stage.classList.add("frame-open");
  renderCaption();
}

function showMedia(p) {
  const slot = $("media-slot");
  slot.innerHTML = "";
  if (p.kind === "video") {
    const v = document.createElement("video");
    v.src = p.url; v.controls = true; v.autoplay = true; v.playsInline = true;
    slot.append(v);
  } else if (p.kind === "model_3d") {
    const mv = document.createElement("model-viewer");
    mv.src = p.url;
    mv.setAttribute("camera-controls", "");
    mv.setAttribute("auto-rotate", "");
    mv.setAttribute("touch-action", "none");
    mv.setAttribute("shadow-intensity", "1");
    mv.setAttribute("environment-image", "neutral");
    mv.setAttribute("exposure", "1.15");
    slot.append(mv);
  } else {
    const img = document.createElement("img");
    img.src = p.url;
    slot.append(img);
  }
  $("media-overlay").hidden = false;
}

let qrLibPromise = null;
function loadQRLib() {
  if (!qrLibPromise) {
    qrLibPromise = new Promise((res, rej) => {
      const s = document.createElement("script");
      s.src = "https://cdnjs.cloudflare.com/ajax/libs/qrcodejs/1.0.0/qrcode.min.js";
      s.onload = res; s.onerror = rej;
      document.head.append(s);
    });
  }
  return qrLibPromise;
}

async function openPip(url, title, id) {
  $("pip-title").textContent = title || "";
  const el = $("pip-overlay");
  el.dataset.pageId = id;
  el.hidden = false;

  const crossOrigin = /^https?:/i.test(url) && new URL(url).origin !== location.origin;
  const iframe = $("pip-frame");
  const qr = $("pip-qr");
  if (crossOrigin) {
    // Retail sites (incl. Shopify) send frame-ancestors 'none' — an iframe
    // would be blank. Hand the page to the customer's phone instead.
    iframe.hidden = true;
    qr.hidden = false;
    qr.querySelector(".qr-url").textContent = url.replace(/^https?:\/\//, "");
    const box = qr.querySelector(".qr-box");
    box.innerHTML = "";
    try {
      await loadQRLib();
      new window.QRCode(box, { text: url, width: 480, height: 480,
        colorDark: "#0a0a0a", colorLight: "#f2ead8",
        correctLevel: window.QRCode.CorrectLevel.M });
    } catch (e) { console.error("QR render failed", e); }
  } else {
    qr.hidden = true;
    iframe.hidden = false;
    iframe.src = url;
  }
}

function closeAll() {
  $("product-frame").hidden = true;
  restoreFrame();               // don't leave .minimized stuck for the next frame
  stage.classList.remove("frame-open");
  $("media-overlay").hidden = true;
  $("pip-overlay").hidden = true;
  $("menu-drawer").hidden = true;
  $("media-slot").innerHTML = "";
  $("pip-frame").src = "about:blank";
}

/* ------------------------------------------------------------- menus */

const menuStack = [];  // [{label, render}]

async function openMenu(rootId) {
  $("menu-drawer").hidden = false;
  menuStack.length = 0;
  await pushMenu("Browse", renderRoot);
  // If the brain asked for a specific submenu, drill straight into it.
  if (rootId && rootId !== "root") {
    const menus = await (await fetch(api("api/menus"))).json();
    const cat = menus.categories.find((c) => c.toLowerCase().includes(String(rootId).toLowerCase().replace(/-/g, " ")));
    if (cat) await pushMenu(cat, () => renderProducts({ category: cat }));
    else if (String(rootId).toLowerCase().includes("show")) {
      await pushMenu("Showpieces", () => renderProducts({ showpieces: 1 }));
    }
  }
}

async function pushMenu(label, render) {
  menuStack.push({ label, render });
  await drawMenu();
}

async function popMenu() {
  if (menuStack.length > 1) menuStack.pop();
  await drawMenu();
}

async function drawMenu() {
  const top = menuStack[menuStack.length - 1];
  $("menu-crumb").textContent = menuStack.map((m) => m.label).join(" › ");
  $("menu-back").hidden = menuStack.length <= 1;
  $("menu-panel").innerHTML = "";
  await top.render();
}

async function renderRoot() {
  const menus = await (await fetch(api("api/menus"))).json();
  const list = document.createElement("div");
  list.className = "menu-list";
  const add = (label, fn) => {
    const b = document.createElement("button");
    b.innerHTML = `<span>${label}</span><span class="chev">›</span>`;
    b.onclick = fn;
    list.append(b);
  };
  for (const c of menus.categories) {
    add(c, () => { send({ type: "touch", target: "menu", id: c }); pushMenu(c, () => renderProducts({ category: c })); });
  }
  if (menus.brands.length > 1) {
    add("Shop by brand", () => pushMenu("Brands", () => renderBrands(menus.brands)));
  }
  if (menus.showpieces > 0) {
    add("✦ Showpieces — video & 3D", () => { send({ type: "touch", target: "menu", id: "showpieces" }); pushMenu("Showpieces", () => renderProducts({ showpieces: 1 })); });
  }
  $("menu-panel").append(list);
}

function renderBrands(brands) {
  const list = document.createElement("div");
  list.className = "menu-list";
  for (const b of brands) {
    const btn = document.createElement("button");
    btn.innerHTML = `<span>${b}</span><span class="chev">›</span>`;
    btn.onclick = () => { send({ type: "touch", target: "menu", id: b }); pushMenu(b, () => renderProducts({ brand: b })); };
    list.append(btn);
  }
  $("menu-panel").append(list);
}

async function renderProducts(query) {
  const qs = new URLSearchParams(query).toString();
  const { products } = await (await fetch(api(`api/products?${qs}`))).json();
  const grid = document.createElement("div");
  grid.className = "product-grid";
  for (const p of products) {
    const card = document.createElement("div");
    card.className = "product-card";
    const badges = [p.has_video ? "▶ video" : "", p.has_model_3d ? "⬡ 3D" : ""].filter(Boolean).join("  ");
    card.innerHTML = `
      <div class="thumb">${p.image_url ? `<img src="${p.image_url}" alt="">` : ""}</div>
      <div class="info">
        <div class="t">${p.title || p.handle}</div>
        ${p.price ? `<div class="p">${money(p.price)}</div>` : ""}
        ${badges ? `<div class="badges">${badges}</div>` : ""}
      </div>`;
    card.onclick = async () => {
      send({ type: "touch", target: "product_grid", handle: p.handle });
      if (!CONFIG.brain_enabled) {
        // brainless dev fallback: open the frame ourselves
        const full = await (await fetch(api(`api/product/${p.handle}`))).json();
        showFrame(full);
      }
    };
    grid.append(card);
  }
  $("menu-panel").append(grid);
}

/* ------------------------------------------------------------- events */

$("attract").addEventListener("click", async () => {
  send({ type: "wake", trigger: "touch" });
  setState("waking");
  if (CONFIG.anam_enabled && !anam) {
    try { await initAnam(); }
    catch (e) { console.error(e); toast("Avatar stream failed — running without video."); enterDevAvatar(); }
  }
});

$("btn-menu").onclick = () => {
  if ($("menu-drawer").hidden) openMenu();
  else $("menu-drawer").hidden = true;
};
$("btn-mic").onclick = toggleMic;
$("btn-close").onclick = () => { closeAll(); send({ type: "touch", target: "close" }); };

document.querySelectorAll(".x").forEach((x) => {
  x.onclick = (e) => {
    e.stopPropagation();
    const what = x.dataset.close;
    if (what === "frame") { $("product-frame").hidden = true; restoreFrame(); send({ type: "touch", target: "close" }); }
    if (what === "media") { $("media-overlay").hidden = true; $("media-slot").innerHTML = ""; send({ type: "touch", target: "close" }); }
    if (what === "menu") { $("menu-drawer").hidden = true; }
    if (what === "pip") {
      const id = $("pip-overlay").dataset.pageId;
      $("pip-overlay").hidden = true;
      $("pip-frame").src = "about:blank";
      send({ type: "touch", target: "close", id });
    }
  };
});

$("menu-back").onclick = popMenu;

// While minimized, ANY tap on the thumbnail just brings the frame back up -
// it never jumps straight to the product page from the thumbnail.
$("product-frame").addEventListener("click", (e) => {
  if (!$("product-frame").classList.contains("minimized")) return;
  if (e.target.closest(".x")) return;          // ✕ still closes
  e.stopPropagation();
  restoreFrame();
  if (frameProduct) send({ type: "touch", target: "product_frame", handle: frameProduct.handle });
}, true);                                       // capture: beat the handlers below

// Tapping the frame's image opens the product page picture-in-picture.
document.querySelector(".frame-media").onclick = () => {
  if (!frameProduct) return;
  send({ type: "touch", target: "product_frame", handle: frameProduct.handle });
  openProductPage();
};
$("frame-page").onclick = openProductPage;
$("frame-cart").onclick = addToCart;

$("btn-cc").onclick = () => { captionsOn = !captionsOn; applyCaptions(); };
$("btn-sound").onclick = () => { soundOn = !soundOn; applySound(); };
$("btn-type").onclick = () => {
  const f = $("dev-input");
  f.hidden = !f.hidden;
  if (!f.hidden) $("dev-text").focus();
};
$("frame-video").onclick = () => frameProduct && showMediaFor("video");
$("frame-3d").onclick = () => frameProduct && showMediaFor("model_3d");

function openProductPage() {
  if (!frameProduct) return;
  const domain = CONFIG.store_domain || "cigarinc.com";
  openPip(`https://${domain}/products/${frameProduct.handle}`, frameProduct.title, "product");
}

/* Add to cart. Shopify cart permalinks are /cart/<variant_id>:<qty>, so we look
   up the live variant id first. The customer finishes on their own phone -
   cigarinc.com sends frame-ancestors 'none' and x-frame-options: DENY, so the
   page physically cannot be shown inside the kiosk. */
async function addToCart() {
  if (!frameProduct) return;
  const domain = CONFIG.store_domain || "cigarinc.com";
  send({ type: "touch", target: "product_frame", handle: frameProduct.handle });
  try {
    const full = await (await fetch(api(`api/product/${frameProduct.handle}`))).json();
    if (full.variant_id) {
      openPip(`https://${domain}/cart/${full.variant_id}:1`,
              `${frameProduct.title} — in your cart`, "cart");
      return;
    }
  } catch (e) { console.warn("variant lookup failed", e); }
  // No variant id (Shopify overlay off or unreachable): send them to the
  // product page rather than a cart link we cannot build honestly.
  toast("Opening the product page — add it there.");
  openProductPage();
}

async function showMediaFor(kind) {
  const full = await (await fetch(api(`api/product/${frameProduct.handle}`))).json();
  const url = kind === "video" ? full.video_url : full.model_3d_url;
  if (url) showMedia({ handle: full.handle, kind, url });
}

$("dev-input").addEventListener("submit", (e) => {
  e.preventDefault();
  const text = $("dev-text").value.trim();
  if (text) { onUserSpoke(); send({ type: "user_text", text }); $("dev-text").value = ""; }
});

function enterDevAvatar() {
  $("persona-video").hidden = true;
  $("dev-avatar").hidden = false;
  $("dev-input").hidden = false;   // dev mode: show it straight away
}

/* ------------------------------------------------------------- boot */

(async function boot() {
  // Show the attract screen BEFORE fetching anything. Until #stage carries
  // data-state, the CSS hides #attract entirely — and on a cold start the
  // config fetch can take the better part of a minute, which showed the
  // customer a completely black screen with no logo on it.
  setState("attract");
  try { CONFIG = await (await fetch(api("api/config"))).json(); } catch {}
  $("store-name").textContent = CONFIG.store_name || "";
  $("attract-title").textContent = CONFIG.store_name || "Welcome";
  $("attract-sub").textContent = CONFIG.persona_name
    ? `Tap anywhere — ${CONFIG.persona_name} is here to help`
    : "Tap anywhere to begin";
  if (!CONFIG.anam_enabled) enterDevAvatar();
  applySound();                  // sound on, captions off, to start
  applyCaptions();
  setState("attract");
  connectWS();
})();
