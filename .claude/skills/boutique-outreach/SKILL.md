---
name: boutique-outreach
description: Walk a student through finding boutique firms (search funds, small PE, boutique investment banks, small VC, boutique consulting), finding the right senior contact and email (website, Apollo.io, Hunter.io, email patterns), and writing cold emails, LinkedIn notes and follow-ups. Use when the user asks how to find firms, find someone's email, or reach out for an internship, or when working on Job Agent's Find, Jobs or Guide features.
---

# Boutique internship outreach

Job Agent automates most of this (Find tab → Jobs tab → Tracker). Use this checklist to guide the user, or to keep the app's prompts (`app/prompts.py`, `OUTREACH_RULES`) and the Guide tab (`static/index.html`, `#view-guide`) consistent.

## 1. Find firms
- Keyword + location + industry searches: "boutique investment banks NYC", "search funds healthcare". Location barely matters; many of these roles are remote.
- Databases: Axial (search funds, banks), SearchFunder (search funds hiring interns), Private Equity List, CT Acquisitions' search fund partner directory. These need a login or have no API, so the user browses them and pastes or imports a CSV into Find → Directories.
- LinkedIn: company search with an IB/PE/VC/financial services industry filter and 1–50 employees, "similar pages", and upperclassmen's past internships. Never automate LinkedIn; it forbids scraping.
- Volume: 10+ firms a day.

## 2. Narrow down
- Skip firms with more than ~30 employees or famous names.
- Check that the website and LinkedIn names are the same firm (matching logos).
- Prefer firms with past interns (LinkedIn "past company" filter).
- Search funds: prefer ones that haven't closed an acquisition, and searchers from places the student wants to work later.
- Banks: prefer nearby ones.

## 3. Contact and email
- Email a senior person: founder, CEO, managing director, partner, or the searcher.
- Check Team/Leadership and Contact pages first (Jobs → Research firm). Only about 30% of firms publish emails.
- Otherwise use Apollo.io: Search → People → filter by company and title → Access email (free plan has limited monthly credits; prefer Verified). Or Hunter.io Domain Search / RocketReach.
- Email patterns: one known address reveals the format (jwang@firm.com for Jason Wang means msmith@firm.com for Mary Smith). Always mark guesses as "verify before sending".
- In Job Agent, when no confirmed email exists, the job card shows a "Find it yourself" helper: links to the team page, Apollo.io, LinkedIn, Hunter domain search and Google, plus a pattern builder (enter any one person's email at the firm and their name; it builds the contact's email). Walk the user through it in that order.

## 4. Reach out
- Cold email: concise (under ~230 words), specific hook about the firm, one or two relevant experiences, availability (hours per week, start date), resume attached, a direct ask for a short call about a part-time, remote or unpaid internship. Match the student's own example emails in voice, never copy them.
- LinkedIn: InMail can carry the full email; a connection note is limited to 300 characters.
- Follow up after 7 days with no reply.
- Never send on the user's behalf: Job Agent opens a draft in Gmail or Outlook and the user presses Send.
