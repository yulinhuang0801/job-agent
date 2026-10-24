---
name: Job Agent
version: 1
description: Calm, trustworthy study tool for students looking for internships and jobs. Warm neutral paper, deep ink, one pine-teal accent, friendly tinted status colors.
tokens: static/styles.css (the :root block is the source of truth; this file explains it)
---

# Job Agent design system

This is how Job Agent looks and why. AI agents and people adding UI read this first, then use the tokens in
`static/styles.css` (never raw hex values in components). It follows the DESIGN.md format popularized by Google
Stitch and collected in VoltAgent/awesome-design-md. The look is our own: it borrows *ideas* from Linear (crisp
structure, quiet chrome), Notion (calm reading surfaces, soft tinted callouts) and Airtable (friendly, clearly
mapped status colors), and none of their colors, fonts or assets.

## 1. Visual theme and atmosphere

**A well-kept study desk.** Students open Job Agent when they're anxious about recruiting; the page should feel
calm, organized and on their side. Content comes first; chrome stays quiet; color appears only where it means
something (an action, a status, a score).

- Warm neutral "paper" canvas, white working surfaces, deep ink text.
- One accent, **pine teal**, used for the primary action, the current place, links and focus. It says "go" without
  shouting, and is distinct from LinkedIn/Handshake blues and from the university's orange and blue (we don't want to
  look like an official school site).
- Friendly tinted pills for status (Airtable idea): green done, amber needs attention, red problem, blue info,
  violet interviews.
- Flat by default: 1px hairlines separate things; shadows only for things that float.

## 2. Color palette and roles

Light theme (default). Dark theme redefines the same tokens; components never pick colors per theme.

| Token | Light | Dark | Role |
|---|---|---|---|
| `--bg` | `#F6F6F3` | `#111416` | Page canvas |
| `--surface` | `#FFFFFF` | `#181C1F` | Cards, top bar, inputs, chat bubbles |
| `--surface-2` | `#F0F0EC` | `#1F2427` | Sunken areas: chat log, chips, table hover, code |
| `--text` | `#1C2126` | `#E6E9EB` | Primary ink |
| `--text-2` | `#454D55` | `#C2C8CD` | Secondary text, body copy in dense areas |
| `--muted` | `#68707A` | `#969EA6` | Hints, meta lines, labels |
| `--faint` | `#9AA1A8` | `#6C747B` | Placeholders, disabled |
| `--border` | `#E4E4DF` | `#2A3035` | Hairlines, card borders |
| `--border-strong` | `#D2D2CC` | `#3A4147` | Inputs, secondary buttons |
| `--accent` | `#1F6E68` | `#5FB5AC` | Primary button, active nav, links, focus |
| `--accent-hover` | `#185A55` | `#77C4BC` | Primary hover |
| `--accent-soft` | `#E2EFEC` | `#1A3331` | Selected chips, active tab fill, user chat bubble |
| `--accent-ink` | `#175650` | `#8FD3CB` | Text on `--accent-soft` |
| `--accent-text` | `#FFFFFF` | `#0D1A19` | Text on `--accent` |
| `--ok` / `--ok-soft` | `#1E7B4F` / `#E3F2E9` | `#56C48A` / `#16301F` | Done, ready, strong fit |
| `--warn` / `--warn-soft` | `#9A5B00` / `#FBEFD9` | `#E2AA52` / `#33280F` | Due, needs attention, medium fit |
| `--bad` / `--bad-soft` | `#B4372C` / `#FBE6E3` | `#F07B6E` / `#3A1C19` | Errors, overdue, weak fit |
| `--info` / `--info-soft` | `#2D5DA8` / `#E5EDF9` | `#7FA8EE` / `#1A2740` | Neutral notices, the demo banner |
| `--violet` / `--violet-soft` | `#6A4BB0` / `#EEE9F8` | `#B49CF0` / `#2A2140` | Interview stage |

Rules: text on a tint uses the matching strong color (`--ok` on `--ok-soft`). Body text is always `--text` or
`--text-2` on `--surface`/`--bg` (contrast ≥ 7:1). The accent on white is 5.9:1, fine for text and buttons.

## 3. Typography

- **Family:** Figtree (variable, SIL OFL 1.1), served from `static/fonts/` so it works where Google Fonts is
  blocked. Chinese falls back to PingFang SC, then Microsoft YaHei. Code uses the system monospace.
- **Scale** (px / line-height / weight):

| Role | Size | LH | Weight | Notes |
|---|---|---|---|---|
| Page title (`h1`) | 28 | 1.2 | 650 | letter-spacing -0.015em |
| Section/card title (`h2`) | 17 | 1.35 | 620 | |
| Headline (plan, big statements) | 19 | 1.4 | 620 | |
| Body | 15 | 1.55 | 420 | |
| Small / meta | 13 | 1.45 | 450 | |
| Eyebrow / table header | 12 | 1.3 | 640 | uppercase, letter-spacing 0.06em, `--muted` |
| Numbers (scores, stats) | any | | 650 | `font-variant-numeric: tabular-nums` |

- Headings use `text-wrap: balance`. Reading text stays within ~70 characters (the main column is 860px).

## 4. Layout

- One centered column (`max-width: 860px`); Home is two columns (plan | mentor team) up to 1180px.
- Spacing scale (4-based): 4, 8, 12, 16, 20, 24, 32, 40, 56. Cards are 16px apart; card padding 20/22px.
- Sticky top bar (56px) and, inside Prepare/Apply, a sticky sub-navigation row.
- Phone (≤640px): one column, 16px gutters, nav labels shrink, grids stack.

## 5. Depth and elevation

| Level | Use | Value |
|---|---|---|
| 0 | Page, cards, inputs | no shadow; 1px `--border` |
| 1 | Hovered card, sticky bars | `0 1px 2px rgba(17,24,28,.05), 0 1px 1px rgba(17,24,28,.03)` |
| 2 | Floating things (popovers, toasts) | `0 8px 24px rgba(17,24,28,.12)` |

Dark theme uses the same structure with black shadows at higher opacity; borders do most of the work.

## 6. Shapes

Radius: 6px controls (buttons, inputs, tabs), 10px cards and panels, 14px chat bubbles (the corner nearest the
speaker is 4px), 999px pills (chips, badges, status).

## 7. Components

- **Top bar:** `--surface`, bottom hairline. Wordmark "Job Agent" with a small pine square mark. Main groups (Home,
  Prepare, Apply) are quiet text tabs; the active one gets `--surface-2` fill and `--text` color (Linear-style), not
  a loud accent block.
- **Sub-navigation:** text tabs with a 2px accent underline for the current page; numbered steps use small circles
  that turn accent when current and show a green ✓ pill when done.
- **Buttons** (36px; small 30px):
  - Primary: `--accent` fill, `--accent-text`; hover `--accent-hover`. One per area.
  - Secondary: `--surface` fill, `--border-strong` border, `--accent-ink` text; hover `--accent-soft`.
  - Ghost: transparent, `--border` border, `--text-2` text. Danger: ghost with `--bad` text.
  - Focus: 2px accent outline, 2px offset. Disabled: 45% opacity.
- **Inputs/selects/textarea:** `--surface`, `--border-strong`, 6px radius, 38px tall; focus = accent border plus a
  3px `--focus` ring. Labels 13px/620.
- **Cards:** `--surface`, 1px `--border`, 10px radius, no shadow. Collapsible cards (`details.card`) show a chevron.
- **Chips:** pill, `--surface-2` with `--text-2`; selected = `--accent-soft`/`--accent-ink`; competency chips use the
  level tints (amber 1–2, accent 3, green 4–5); empty = dashed border.
- **Badges and status pills:** 12px/600 pills on tints (`.badge.ok`, `.badge.warn`, `.stage`).
- **Scores** (fit 0–100, match scores): tabular, pill on `--ok-soft` (≥80), `--warn-soft` (60–79), `--bad-soft` (<60).
- **Chat:** log on `--surface-2` with no inner border; assistant bubbles `--surface` with hairline; user bubbles
  `--accent-soft` with `--text` (calm, not a solid color block). Suggestion buttons are small secondary buttons.
- **Tables:** 12px uppercase headers in `--muted`, hairline rows, `--surface-2` row hover.
- **Notices:** tinted panels (`--warn-soft`, `--info-soft`) with 8px radius; the demo banner is `--info-soft`.

## 8. Do's and don'ts

Do:
- Use tokens; add a token here and in `:root` (both themes) before using a new color.
- Keep one primary button per card; everything else secondary or ghost.
- Encode status with tint + word ("Overdue"), never color alone.
- Keep copy plain and specific; buttons say what happens ("Grade all", "Open in My jobs").

Don't:
- Don't copy another product's colors, fonts or logos (including LinkedIn, Handshake, Notion, Linear, Airtable,
  or the university's).
- Don't use gradients, glows, emoji as section markers, or heavy drop shadows on cards.
- Don't use pure black text or pure gray borders; use the warm-neutral tokens.
- Don't load fonts or icons from third-party CDNs (they fail in China and leak usage).

## 9. Responsive behavior

- ≤900px: Home becomes one column (plan above the mentor team).
- ≤640px: brand text hides, nav padding tightens, grids stack, chat bubbles widen to 92%, rubric rows stack.
- Touch targets stay ≥ 30px tall; nothing scrolls sideways except tables (inside `.table-wrap`).

## 10. Agent prompt guide

When adding UI: "Follow DESIGN.md. Use the tokens in static/styles.css. Cards are flat white panels with a hairline
border and 10px radius; one pine-teal primary button per card; statuses are tinted pills; Figtree at the scale
above; check light and dark themes and 390px width."
