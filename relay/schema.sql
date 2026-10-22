-- Job Agent shared-key relay (Cloudflare D1). Apply with: npx wrangler d1 execute job-agent-relay --remote --file schema.sql
CREATE TABLE IF NOT EXISTS codes (code TEXT PRIMARY KEY, name TEXT NOT NULL, active INTEGER NOT NULL DEFAULT 1, created TEXT NOT NULL);
-- Running totals: "ai:<day>" (yuan, including money held for calls in progress), "tavily:day:<day>", "tavily:month:<month>",
-- "serper:day:<day>", "serper:total", "hunter:month:<month>". Days and months are Beijing time.
CREATE TABLE IF NOT EXISTS counters (key TEXT PRIMARY KEY, value REAL NOT NULL DEFAULT 0);
-- Who used what, for the owner's usage report.
CREATE TABLE IF NOT EXISTS usage (day TEXT NOT NULL, code TEXT NOT NULL, service TEXT NOT NULL, calls INTEGER NOT NULL DEFAULT 0,
  cost REAL NOT NULL DEFAULT 0, PRIMARY KEY (day, code, service));
