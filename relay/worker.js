// Job Agent shared-key relay: a Cloudflare Worker that lets friends use the owner's DeepSeek, Tavily, Serper and
// Hunter keys through invite codes, with hard limits for everyone together:
//   - AI: at most DAILY_CNY yuan a day (Beijing time). Before each call it holds the most the call could cost
//     (input estimate + max_tokens of output); after the answer it settles to the real cost from DeepSeek's
//     token counts. A call that could push the day over the limit is refused, so the limit is never passed.
//   - Searches and email lookups: per-day and per-month (or one-time) counts kept inside the free plans.
// The keys live only in this Worker's secrets; the apps never see them. Every request needs an active invite code.

const PRICES = { // yuan per 1M tokens at peak: [input cache hit, input cache miss, output]. Off-peak is half.
  "deepseek-flash": [0.04, 2, 8],
  "deepseek-v4-flash": [0.04, 2, 8],
  "deepseek-v4-pro": [0.3, 9, 27],
};
const MAX_OUTPUT = 16000; // tokens per call
const MAX_BODY = 400000; // bytes per request

const num = (v, d) => (v === undefined || v === "" || isNaN(Number(v)) ? d : Number(v));

function limits(env) {
  return {
    ai: num(env.DAILY_CNY, 20),
    tavilyDay: num(env.TAVILY_PER_DAY, 25), tavilyMonth: num(env.TAVILY_PER_MONTH, 900),
    serperDay: num(env.SERPER_PER_DAY, 30), serperTotal: num(env.SERPER_TOTAL, 2000),
    hunterMonth: num(env.HUNTER_PER_MONTH, 40),
  };
}

// Beijing time (UTC+8, no daylight saving).
function beijing(now = new Date()) {
  const t = new Date(now.getTime() + 8 * 3600 * 1000);
  const day = t.toISOString().slice(0, 10);
  const wd = t.getUTCDay(), h = t.getUTCHours();
  const peak = wd >= 1 && wd <= 5 && ((h >= 9 && h < 12) || (h >= 14 && h < 18)); // public holidays count as peak (safe side)
  return { day, month: day.slice(0, 7), peak };
}

const json = (data, status = 200) => new Response(JSON.stringify(data), {
  status, headers: { "content-type": "application/json; charset=utf-8", "access-control-allow-origin": "*" },
});
const fail = (status, message, type = "relay_error") => json({ error: { message, type } }, status);

function bearer(request) {
  const h = request.headers.get("authorization") || "";
  return h.toLowerCase().startsWith("bearer ") ? h.slice(7).trim() : "";
}

async function member(env, code) {
  if (!code) return null;
  return env.DB.prepare("SELECT code, name FROM codes WHERE code = ? AND active = 1").bind(code).first();
}

// Add `amount` to a counter only if it stays within `cap`. Atomic in D1 (one statement).
async function take(env, key, amount, cap) {
  await env.DB.prepare("INSERT OR IGNORE INTO counters (key, value) VALUES (?, 0)").bind(key).run();
  const row = await env.DB.prepare("UPDATE counters SET value = value + ? WHERE key = ? AND value + ? <= ? RETURNING value")
    .bind(amount, key, amount, cap).first();
  return row !== null;
}
async function add(env, key, amount) {
  await env.DB.prepare("INSERT INTO counters (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = value + excluded.value")
    .bind(key, amount).run();
}
async function counter(env, key) {
  const row = await env.DB.prepare("SELECT value FROM counters WHERE key = ?").bind(key).first();
  return row ? row.value : 0;
}
async function logUse(env, day, code, service, cost) {
  await env.DB.prepare("INSERT INTO usage (day, code, service, calls, cost) VALUES (?, ?, ?, 1, ?) " +
    "ON CONFLICT(day, code, service) DO UPDATE SET calls = calls + 1, cost = cost + excluded.cost").bind(day, code, service, cost).run();
}

// ---------- AI (OpenAI-compatible: the app just points its base URL here) ----------

function costOf(prices, peak, usage) {
  const f = peak ? 1 : 0.5;
  const hit = usage.prompt_cache_hit_tokens || 0;
  const miss = usage.prompt_cache_miss_tokens ?? Math.max(0, (usage.prompt_tokens || 0) - hit);
  return f * (hit * prices[0] + miss * prices[1] + (usage.completion_tokens || 0) * prices[2]) / 1e6;
}

async function chat(request, env, who) {
  const raw = await request.text();
  if (raw.length > MAX_BODY) return fail(413, "That request is too large for the shared key.");
  let body;
  try { body = JSON.parse(raw); } catch { return fail(400, "Bad request."); }
  const prices = PRICES[body.model];
  if (!prices) return fail(400, `The shared key only works with: ${Object.keys(PRICES).join(", ")}. Pick deepseek-flash in Settings.`);
  if (body.stream) return fail(400, "Streaming isn't supported by the shared key.");
  body.max_tokens = Math.min(num(body.max_tokens, 4000), MAX_OUTPUT);

  const t = beijing(), f = t.peak ? 1 : 0.5, cap = limits(env).ai;
  const inputTokens = Math.ceil(JSON.stringify(body.messages || []).length + JSON.stringify(body.tools || []).length);
  const hold = f * (inputTokens * prices[1] + body.max_tokens * prices[2]) / 1e6;
  const key = `ai:${t.day}`;
  if (!(await take(env, key, hold, cap))) {
    const spent = await counter(env, key);
    return fail(402, `The shared AI budget for today is used up (¥${spent.toFixed(2)} of ¥${cap}). It resets at midnight Beijing time. ` +
      "To keep going now, add your own DeepSeek key in Settings.", "budget_exhausted");
  }
  let upstream;
  try {
    upstream = await fetch(`${env.DEEPSEEK_BASE || "https://api.deepseek.com"}/chat/completions`, {
      method: "POST", body: JSON.stringify(body),
      headers: { "content-type": "application/json", authorization: `Bearer ${env.DEEPSEEK_KEY}` },
    });
  } catch (e) {
    await add(env, key, -hold);
    return fail(502, "Couldn't reach DeepSeek. Try again in a minute.");
  }
  const text = await upstream.text();
  let cost = hold; // if DeepSeek doesn't say how much it used, keep the whole hold
  if (upstream.ok) {
    try { const u = JSON.parse(text).usage; if (u) cost = costOf(prices, t.peak, u); } catch { /* keep the hold */ }
  } else if (upstream.status >= 400) {
    cost = 0; // DeepSeek doesn't bill failed requests
  }
  await add(env, key, cost - hold);
  await logUse(env, t.day, who.code, "ai", cost);
  if (upstream.status === 401 || upstream.status === 402) {
    return fail(503, "The shared key isn't working on DeepSeek's side right now (the owner may need to top it up). Try again later.");
  }
  return new Response(text, { status: upstream.status, headers: { "content-type": "application/json; charset=utf-8" } });
}

// ---------- Searches and email lookups ----------

async function counted(env, who, service, checks) {
  // checks: [[counterKey, cap], ...]; all or nothing.
  const done = [];
  for (const [key, cap] of checks) {
    if (await take(env, key, 1, cap)) { done.push(key); continue; }
    for (const k of done) await add(env, k, -1);
    return false;
  }
  await logUse(env, beijing().day, who.code, service, 0);
  return true;
}

async function tavily(request, env, who) {
  const t = beijing(), L = limits(env);
  if (!env.TAVILY_KEY) return fail(404, "The shared key has no web search.");
  if (!(await counted(env, who, "tavily", [[`tavily:day:${t.day}`, L.tavilyDay], [`tavily:month:${t.month}`, L.tavilyMonth]]))) {
    return fail(429, "The shared web searches for today are used up; they reset at midnight Beijing time.", "quota_exhausted");
  }
  const body = await request.json().catch(() => ({}));
  body.search_depth = "basic"; // 1 credit each
  body.max_results = Math.min(num(body.max_results, 5), 10);
  const r = await fetch(`${env.TAVILY_BASE || "https://api.tavily.com"}/search`, {
    method: "POST", body: JSON.stringify(body), headers: { "content-type": "application/json", authorization: `Bearer ${env.TAVILY_KEY}` },
  });
  return new Response(await r.text(), { status: r.status, headers: { "content-type": "application/json" } });
}

async function serper(request, env, who) {
  const t = beijing(), L = limits(env);
  if (!env.SERPER_KEY) return fail(404, "The shared key has no Google search.");
  if (!(await counted(env, who, "serper", [[`serper:day:${t.day}`, L.serperDay], ["serper:total", L.serperTotal]]))) {
    return fail(429, "The shared Google searches are used up for now.", "quota_exhausted");
  }
  const body = await request.json().catch(() => ({}));
  body.num = Math.min(num(body.num, 10), 10);
  const r = await fetch(`${env.SERPER_BASE || "https://google.serper.dev"}/search`, {
    method: "POST", body: JSON.stringify(body), headers: { "content-type": "application/json", "x-api-key": env.SERPER_KEY },
  });
  return new Response(await r.text(), { status: r.status, headers: { "content-type": "application/json" } });
}

async function hunter(url, env, who) {
  const t = beijing(), L = limits(env);
  if (!env.HUNTER_KEY) return fail(404, "The shared key has no email finder.");
  if (!(await counted(env, who, "hunter", [[`hunter:month:${t.month}`, L.hunterMonth]]))) {
    return fail(429, "The shared email lookups for this month are used up.", "quota_exhausted");
  }
  const q = new URLSearchParams({ domain: url.searchParams.get("domain") || "", first_name: url.searchParams.get("first_name") || "",
    last_name: url.searchParams.get("last_name") || "", api_key: env.HUNTER_KEY });
  const r = await fetch(`${env.HUNTER_BASE || "https://api.hunter.io"}/v2/email-finder?${q}`);
  return new Response(await r.text(), { status: r.status, headers: { "content-type": "application/json" } });
}

async function status(env) {
  const t = beijing(), L = limits(env);
  const [ai, td, tm, sd, st, hm] = await Promise.all([`ai:${t.day}`, `tavily:day:${t.day}`, `tavily:month:${t.month}`,
    `serper:day:${t.day}`, "serper:total", `hunter:month:${t.month}`].map((k) => counter(env, k)));
  return {
    day: t.day, resets: "midnight Beijing time", peak_prices_now: t.peak,
    ai: { spent_cny: Math.round(ai * 100) / 100, cap_cny: L.ai, left_cny: Math.max(0, Math.round((L.ai - ai) * 100) / 100) },
    web_search: env.TAVILY_KEY ? { today: td, per_day: L.tavilyDay, this_month: tm, per_month: L.tavilyMonth } : null,
    google_search: env.SERPER_KEY ? { today: sd, per_day: L.serperDay, total: st, total_cap: L.serperTotal } : null,
    email_finder: env.HUNTER_KEY ? { this_month: hm, per_month: L.hunterMonth } : null,
  };
}

// ---------- Owner tools (ADMIN_TOKEN) ----------

async function admin(request, env, url) {
  if (!env.ADMIN_TOKEN || bearer(request) !== env.ADMIN_TOKEN) return fail(401, "Admin token required.");
  const path = url.pathname.replace(/^\/admin/, "");
  if (path === "/codes" && request.method === "POST") {
    const { name } = await request.json().catch(() => ({}));
    if (!name) return fail(400, "Give the person's name.");
    const bytes = crypto.getRandomValues(new Uint8Array(12));
    const code = Array.from(bytes, (b) => "abcdefghjkmnpqrstuvwxyz23456789"[b % 31]).join("");
    await env.DB.prepare("INSERT INTO codes (code, name, active, created) VALUES (?, ?, 1, ?)").bind(code, name, new Date().toISOString()).run();
    return json({ name, code, invite: `${url.origin}/join/${code}` });
  }
  if (path === "/codes" && request.method === "GET") {
    const { results } = await env.DB.prepare("SELECT code, name, active, created FROM codes ORDER BY created").all();
    return json({ codes: results });
  }
  const off = path.match(/^\/codes\/([a-z0-9]+)$/);
  if (off && request.method === "DELETE") {
    await env.DB.prepare("UPDATE codes SET active = 0 WHERE code = ?").bind(off[1]).run();
    return json({ ok: true });
  }
  if (path === "/usage") {
    const since = new Date(Date.now() + 8 * 3600e3 - 13 * 86400e3).toISOString().slice(0, 10);
    const { results } = await env.DB.prepare("SELECT u.day, c.name, u.service, u.calls, ROUND(u.cost, 3) AS cost_cny FROM usage u " +
      "LEFT JOIN codes c ON c.code = u.code WHERE u.day >= ? ORDER BY u.day DESC, u.cost DESC").bind(since).all();
    return json({ status: await status(env), last_14_days: results });
  }
  return fail(404, "Unknown admin action.");
}

// The link friends get: a short page saying what it is, the limits, and what to do with it.
function joinPage(url, code, L) {
  const esc = (s) => String(s).replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
  const invite = `${url.origin}/join/${code}`;
  return new Response(`<!doctype html><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Job Agent invite</title><style>body{font:16px/1.55 -apple-system,Segoe UI,Roboto,sans-serif;max-width:40rem;margin:40px auto;padding:0 16px;color:#1d2430}
code{background:#eef2f4;padding:2px 6px;border-radius:4px;word-break:break-all}li{margin:6px 0}</style>
<h1>You're invited to try Job Agent</h1>
<p>This invite lets your copy of Job Agent use a shared AI key, so you don't need your own.</p>
<ol><li>Download Job Agent: <a href="https://github.com/yulinhuang0801/job-agent">github.com/yulinhuang0801/job-agent</a> (Code → Download ZIP) and start it (see the README).</li>
<li>In Job Agent, open <b>Settings → Shared key</b>, paste this invite and press <b>Save settings</b>:<br><code>${esc(invite)}</code></li></ol>
<h2>Limits (shared by everyone using this key)</h2>
<ul><li>AI: ¥${L.ai} a day in total, reset at midnight Beijing time. Once it's used up, AI features pause until then (or add your own DeepSeek key).</li>
<li>Web searches: ${L.tavilyDay} a day; Google searches: ${L.serperDay} a day; email lookups: ${L.hunterMonth} a month.</li>
<li>Your profile, stories and jobs stay on your computer. The text each AI step needs passes through this relay to DeepSeek; the relay keeps only counts of calls and cost, not your text.</li>
<li>Please don't share this invite; it's tied to your name and can be turned off.</li></ul>`, { headers: { "content-type": "text/html; charset=utf-8" } });
}

export default {
  async fetch(request, env) {
    const url = new URL(request.url);
    try {
      if (url.pathname.startsWith("/admin")) return await admin(request, env, url);
      const join = url.pathname.match(/^\/join\/([a-z0-9]+)$/);
      if (join && request.method === "GET") {
        return (await member(env, join[1])) ? joinPage(url, join[1], limits(env)) : new Response("This invite isn't active.", { status: 404 });
      }
      const who = await member(env, bearer(request));
      if (!who) return fail(401, "This invite code isn't active. Ask the person who invited you for a new one.", "invalid_code");
      if (url.pathname === "/v1/chat/completions" && request.method === "POST") return await chat(request, env, who);
      if (url.pathname === "/tavily/search" && request.method === "POST") return await tavily(request, env, who);
      if (url.pathname === "/serper/search" && request.method === "POST") return await serper(request, env, who);
      if (url.pathname === "/hunter/email-finder" && request.method === "GET") return await hunter(url, env, who);
      if (url.pathname === "/status") return json({ name: who.name, ...(await status(env)) });
      return fail(404, "Not found.");
    } catch (e) {
      return fail(500, `Relay error: ${e && e.message ? e.message : e}`);
    }
  },
};
