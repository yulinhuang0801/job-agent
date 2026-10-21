# Job Agent

A web app that interviews a job seeker, builds their profile, scores their fit for each job, writes a tailored resume and cover letter (as PDFs), and tracks every application.

The AI runs on DeepSeek (OpenAI-compatible API), so it works from both the US and China. An optional backup (Google Gemini by default) takes over automatically when DeepSeek is busy, slow or down. Only the conversational and judgment steps call the model (interview, stories, skills check, profile, fit scoring, writing documents). PDFs, job-page reading and the tracker are plain code and use no tokens.

## Run it

```bash
./run.sh
```

Then open http://127.0.0.1:8000, go to **Settings** and paste your DeepSeek API key. The key is saved only in `data/settings.json` on this computer.

Tests:
- `bash tests/run_smoke.sh` runs every smoke test with a fake model, so it needs no key.
- `.venv/bin/python tests/calibration/run_calibration.py` checks the grader against expected scores. It uses your key.

## How it flows

The app has three areas, plus Guide and Settings in the top corner:
- **Home**: today's plan and your mentor team.
- **Prepare**, in numbered steps with progress badges: 1 About you → 2 Profile → 3 Stories → 4 Skills check. **Prepare** opens the first step you haven't finished, and each step ends with a link to the next.
- **Apply**: Find (database, web search, import) → My jobs (cards to work on each firm, or a table to track them all) → Network (alumni and contacts) → Calendar (every date, plus when recruiting happens).

Old links like `#tracker` or `#mentors` still work.

0. **Home** (mentors): your **lead mentor** reads all your progress (profile, stories, skills check, every firm and posting, follow-ups due, notes from the other coaches) and writes today's plan: where you stand, 3–6 concrete tasks with one-click buttons, progress stats against your daily outreach goal, and the program's milestones. Chat with the lead mentor or with three coaches:
   - **Behavioral coach**: mock interviews using your own stories, feedback (STAR, "I" vs "we", results, length), tightened versions you can save.
   - **Technical coach**: teaches your weakest areas and gives practice problems with hints before answers.
   - **Resume coach**: reviews your resume line by line and proposes rewrites you can apply with one click (never adds facts).
   Coaches suggest actions as buttons; nothing changes until you click.

**Connected memory.** Everything shares one memory (`data/memory.json`):
- **Facts** the intake interview and every coach save as you talk ("Only wants remote roles", "Interview at Palazzo on Nov 3"). Every agent reads them: mentors, the interview and Stories coaches, the skills check, company prep, fit scoring, resume/cover-letter and email writers, form answers. See and delete them under Mentors → What your team knows.
- **Update buttons** any coach can offer: rewrite a resume line, add an experience/project/education, change targets, update a skill level, update a job's status or contact, add a question, save a story.
  - **Put a date on the Calendar:** an online assessment, interview, coffee chat or deadline you mention goes onto that job. If the firm isn't tracked yet, it starts tracking it.
  - **Add a person to Network.**
- **What each coach sees:**
  - The **behavioral coach** sees each answer's rubric grade and biggest fix, plus your interviews and assessments in the next three weeks.
  - The **technical coach** sees the skills check and those same upcoming assessments.
  - The **lead mentor** also sees recruiting windows that are open now for your fields, your weakest graded answers, and new postings from job-alert emails.
- **Skills check → profile:** graded levels go straight into your profile as verified skills. Skills you edited by hand are left alone.
- **Activity log** (profile updates, finished stories, graded checks, emails sent, status changes) that the lead mentor reads.
- **Profile updates merge, never overwrite**: "Update my profile" only fills gaps and adds items, bullets and skills; it never deletes, never changes how items are identified, and never touches fields you or a coach edited by hand (those are pinned).
1. **About you** (the intake chat): the coach asks about goals, education, experience, projects, the skills you use, and contact info, one question at a time.
   - **Upload CV** (PDF, .docx, .txt): the coach reads it and only asks about what's missing.
   - **🎤 Voice**: talk instead of typing. Uses the browser's voice typing (Chrome, Edge, Safari) by default, or a speech-to-text service from Settings (e.g. SiliconFlow SenseVoice, which works in China). **🔊 Read replies aloud** uses the computer's built-in voices.
   - **Web lookups** (optional Tavily key in Settings): the coach looks up course codes, small companies and labs you mention, and shows "🔎 Looked up: …" in the chat. Without a key it asks you what they are.
2. **Stories**: behavioral questions from the question bank (teamwork, conflict, failure, ownership, plus optional extras). The coach asks up to 3 follow-ups per story until it has the situation, task, what *you* did, the result, and what you learned, and rates how convincing each story is.
3. **Skills check**: 4–10 questions from a bank of about 175 in 29 areas, grouped by field:
   - **Software & data:** Python, algorithms, SQL, backend, frontend, systems, system design, ML, NLP, data, statistics, Java, C/C++, Git.
   - **Finance & business:** accounting, audit, tax & GAAP, valuation, M&A/LBO, markets, consulting cases, Excel.
   - **Engineering:** mechanical, electrical, civil & structural.
   - **Science & health:** biochemistry & molecular biology, lab & research methods, healthcare & clinical.
   - **Marketing & design:** marketing & brand, product & UX design.
   - Questions are picked for your major and target roles, or from areas you choose. Coding questions use the VS Code editor (Monaco). Calculations (dilutions, beam moments, filter values, CAC/LTV, depreciation, risk reduction) are typed out with working and graded on method, the final number and units.
   - Graded 0–4 against a rubric, with feedback and a model answer; tested skills show as verified (✓) in your profile.
   - **Outside question sources** (all MIT-licensed, downloaded when first used and cached in `data/qlib/`, never copied into this repo):
     - **LeetCode problems** through [alfa-leetcode-api](https://github.com/alfaarghya/alfa-leetcode-api) (falls back to LeetCode's public API), picked from the [Tech Interview Handbook](https://www.techinterviewhandbook.org) topic lists (array, string, tree, graph, dynamic programming… 18 topics; essential problems first, ones you've done are skipped). Each comes with the statement, examples and starter code in Python, Java, C++, JavaScript, TypeScript, C or Go, and is graded like a coding interview (approach, correct code, edge cases from the handbook, stated complexity). Paste any LeetCode link under My technical questions to add it.
     - **[Open Interview](https://github.com/open-interview/open-interview)** concept questions (system design, backend, databases, ML, generative AI, networking, security… 27 channels) with community-written reference answers the grader compares against. Easy and medium only, since its "advanced" ones are senior-level.
     - The AI picker can add one or two of these on its own for software, data and quant targets.
   - **Prepare for a specific company**: researches the company's interview process and values, then adds tailored behavioral questions to Stories and technical questions to the Skills check. Technical questions follow the role's field: a Genentech protein-sciences internship gets lab, assay and dilution questions; a Big 4 assurance role gets audit and GAAP. Matching questions from the bank are shown to the AI as examples of style and difficulty. Uses the behavioral questions the Tech Interview Handbook lists for that company (Airbnb, Amazon, ByteDance, Palantir, Stripe and others) and Open Interview questions tagged with it.
     - **Official employer pages first.** Company prep reads the company's own interview, hiring-process and values pages before any third-party search results, and cites them. Some come from a hand-checked registry (`app/official_sources.json`): Amazon, Google, Meta, Microsoft, Netflix, Atlassian, Stripe, Apple, Anthropic, Goldman Sachs, JPMorgan Chase, Bain, P&G, PepsiCo, and McKinsey (link only). For other companies it searches the company's own domain only; Jane Street and Deloitte, for example, were found this way.
     - **Polite fetching** (`app/official.py`): every page is checked against the site's robots.txt, following the standard rules (wildcards, longest match wins). Pages that are blocked, or whose robots.txt can't be read, are never fetched. Requests to one site are at least two seconds apart, and pages are cached on your computer for 30 days.
     - **The real job:** prep also adds the matching O*NET occupation's tasks and tools, so technical questions fit the actual work.
   - **Questions for your occupation (O*NET):** pick from occupations matched to your target roles, or search all 1,016. The AI writes questions from that occupation's real tasks, technologies and knowledge areas, with calculations for numeric fields; they're labelled as AI-written. This is the best option for majors the built-in bank covers thinly. The data downloads once (about 20 MB) from Find → Data sources or the Skills page.
   - **How grading works**:
     - **Stories** are scored 1–4 on fixed rubric dimensions, with written descriptions of each level and two worked examples, at temperature 0. STAR stories are scored on situation and stakes, your own actions (×2), result (×2), reflection, and whether the story answers the question. Short answers are scored on answering the question, specific evidence (×2), structure, and how personal they are.
     - Each graded answer is also tagged with the 1–3 **NACE career-readiness competencies** it shows: career and self-development, communication, critical thinking, equity and inclusion, leadership, professionalism, teamwork, technology. Each tag gets a proficiency level on a 1–5 scale like OPM's structured-interview ratings (awareness, basic, intermediate, advanced, expert) and a quote that is checked against your answer. **Competency coverage** on the Stories page shows which competencies have no story yet, and the coaches see it too.
     - The grader must quote your exact words for each score. The app checks every quote, and a 3 or 4 without a real quote drops to 2.
     - Each graded story shows the biggest single improvement. Finished answers are graded automatically; **Grade all** grades the rest.
     - **Python coding answers** run against real test cases from [LeetCodeDataset](https://huggingface.co/datasets/newfacade/LeetCodeDataset) (Apache-2.0, about 2,870 problems, downloaded once). They run in a separate process with time, CPU, memory and file limits. **▶ Run tests** works while you answer.
     - Test results set a floor and a ceiling: code that doesn't run, or passes nothing, scores at most 1; passing every test (when there are 10 or more) scores at least 2. The grader checks failed cases itself, because some dataset tests expect one specific answer when several are valid. "Any order" answers count as passes when the problem allows it.
     - Other languages are graded by reading the code.
     - **Was this fair?** buttons save your verdict, the answer and the grade to `data/grade_feedback.jsonl`. No public dataset of scored behavioral answers exists, so these votes are how a better grader gets built later.
     - **Calibration set** (`tests/calibration/`): 16 behavioral answers and 15 technical answers (including biochemistry, engineering, marketing, audit and healthcare) with expected score ranges. Run `.venv/bin/python tests/calibration/run_calibration.py --repeat 2` after changing a grading prompt or model. It fails if fewer than 85% of scores land in range, and it reports how often a second run disagrees. With DeepSeek Flash: 100% in range; 2 of 16 behavioral and 1 of 8 technical answers scored a little differently on a second run. The expected scores were written by the same person as the rubric, so this catches regressions; it doesn't prove the grades match real interviewers.
   - **My questions**: add your own behavioral and technical questions (stored in `data/custom_questions.json`).
4. **Build my profile**: turns the CV, interview, stories, skills check and anything looked up into `data/profile.json`, plus an honest evaluation (strengths, gaps, roles to target). Download a general CV from the Profile tab.
5. **Find → Database** (default): a local SQLite database (`data/jobs_db.sqlite`) built from public sources, with Handshake-style filters:
   - **SEC Form ADV** (monthly): ~24,000 investment advisers (PE, VC, hedge funds, real estate, wealth managers) with employee counts, private-fund types, fund assets and websites. Exempt reporting advisers are mostly small private fund managers.
   - **SEC Form D** (quarterly, last 4 quarters by default): ~55,000 new funds and startups that just raised money (VC, PE, hedge, biotech, software, healthcare, energy…), with ~166,000 people named on the filings (executive officers, directors, general partners).
   - **Company job boards** (Greenhouse, Lever, Ashby public APIs; ~250 boards, add more by link) plus the SimplifyJobs internship lists: ~21,000 open postings.
   - Filters: job type (internship, co-op, new grad, full-time, part-time), job function (~24), industry (~30), term, state, country, remote, visa hints ("No sponsorship", "US citizens only"); for firms: industry, category, employees, state, small firms only, has website, has named people.
   - **Job alert emails**: LinkedIn, Handshake, Indeed, Glassdoor, ZipRecruiter, Wellfound, Simplify, Jobright, Built In, WayUp, RippleMatch and Google send job recommendations by email. Job Agent reads **only those alerts** from your inbox over IMAP (read-only: nothing is marked read, moved or deleted; LinkedIn messages and other mail are skipped from the headers, before any body is downloaded), pulls out each job's title, company, location and link, and adds them to the database (source "LinkedIn alert", "Handshake alert"…). Set it up in Settings → Job alert emails with an app password (Gmail, iCloud, Yahoo) or authorization code (QQ, 163, 126); it checks every 3 hours while the app runs. Without that, paste an alert or upload it as an .eml file in Find → Data sources. This replaces logging into LinkedIn or Handshake, which their terms forbid (and unofficial "LinkedIn MCP" servers do exactly that with your session cookie, risking a ban).
   - Update each source from Find → Data sources. SEC downloads need a contact email (Settings → Firm database).
6. **Find → Web search / Import**: search by category (search funds, small PE, boutique banks, small VC, boutique consulting, family offices, quant firms, tech startups, company job boards), pull from SimplifyJobs' summer and off-season internship lists or Hacker News "Who is hiring?", or search your own sites and phrases, with optional location, industry and role. Uses Google results (Serper.dev) when that key is saved, otherwise Tavily. The AI screens results with the boutique-internship rules (under ~30 employees, no big-name firms, search funds still searching). Databases that need a login (Axial, SearchFunder) and LinkedIn are browsed by hand and pasted or imported as a CSV.
7. **My jobs** (Cards view): one workspace per firm or posting:
   1. **Research & contact**: reads the firm's home, team, about, contact and careers pages; writes a summary and specific hooks; picks the senior person to contact; finds their email (published on the site, Hunter.io if you add a key, or a guess from the firm's email pattern, clearly marked). If there's no email, it points you to Apollo.io.
   2. **Cold email** (with LinkedIn note and 7-day follow-up) in the voice of your example emails, opened as a draft in Gmail, Outlook or your mail app; or **Application form**: paste the form or its link, and walk through every question with drafted answers; download all answers as a .txt.
   3. **Fit score, tailored resume and cover letter** (addressed to the contact).
   4. **Next steps**:
      - The application deadline (read from the posting when you score fit).
      - A work-authorization check, such as "US citizens only" or "no sponsorship now or in the future", compared with your status.
      - Events: online assessments, interviews, coffee chats, info sessions and career fairs. Adding an assessment or interview moves the job forward (Applied → Online assessment → Interviewing).
      - **Prepare** writes a plan for the event with one-click steps: a skills check on the right topics, a mock with a coach, or company prep. It also lists likely questions and explains how each assessment platform works (HireVue, CodeSignal, HackerRank, Codility, game-based tests, SHL/Aon reasoning tests, modeling tests, written cases).
      - **Timed practice** works like HireVue: 30 seconds to prepare, 2 minutes to answer, then a rubric grade.
      - After an interview, a **thank-you note** is drafted from what you talked about, to send within a day.
      - **Offers**: pay, dates, the decision deadline, housing and return-offer chances. Drafts are available to ask for more time, negotiate (only with a real reason), accept or decline.
8. **My jobs** (Table view): status, contact, applied/contacted date, follow-up due date and notes; export to CSV for Excel or Google Sheets.
9. **Network**: track alumni, recruiters and other contacts (how you're connected, status, next follow-up).
   - **Find people at a firm**: opens LinkedIn and Google searches for the firm plus your school in your own browser (Job Agent never logs into LinkedIn), and lists people named in the firm's SEC filings.
   - Drafts the coffee-chat request (LinkedIn note under 300 characters, plus an email), a follow-up, the thank-you after a chat and a referral request. **Prepare for a chat** writes how to introduce yourself, 6–8 questions, and what to avoid.
   - Logging a chat sets a thank-you for the next day; marking someone Messaged sets a follow-up in 7 days.
10. **Calendar**: every deadline, assessment, interview, thank-you note, follow-up and offer deadline, grouped into overdue, next 7 days and later. Each item has a link to add it to Google Calendar, and the whole calendar downloads as an .ics file for Google Calendar, Outlook or Apple Calendar.
   - **When recruiting happens**: typical US windows for 12 fields (investment banking, boutiques and small PE, consulting, software, quant, data, product, accounting, marketing, biotech, engineering, policy), placed on your own summers using your graduation year (freshman, sophomore and junior summer), with advice for each. They're typical patterns, not guarantees.
   - The lead mentor's daily plan puts anything due in the next two days first.
11. **Guide**: the outreach playbook, how to find emails with Apollo.io, what to do after you apply, and work authorization for F-1 students (CPT, OPT, and how to read "no sponsorship" lines; general information, not legal advice). Find → Database can hide US-citizen-only and no-sponsorship postings.

## Layout

| Path | What it is |
|---|---|
| `app/main.py` | API routes |
| `app/llm.py` | Model calls with automatic fallback to the backup provider, and the web-search tool loop |
| `app/services.py` | Optional outside services: Tavily web search, speech-to-text |
| `app/resume_text.py` | Reads text out of uploaded CVs |
| `app/assess.py` | Stories (behavioral STAR follow-ups) and the Skills check |
| `app/question_bank.json` | Behavioral and technical question bank (~25 behavioral types, ~100 technical questions) |
| `app/prep.py` | Company-specific interview prep |
| `app/memory.py` | Shared memory: facts, activity log, pinned profile fields |
| `app/profile_merge.py` | Safe profile updates: AI proposes a patch, code applies it without deleting or overwriting edits |
| `app/mentors.py` | Lead mentor (daily plan, progress snapshot) and the behavioral, technical and resume coaches |
| `static/editor.js` | VS Code (Monaco) code editor for the Skills check, with a basic fallback |
| `app/db.py` | Local firm and job database (SQLite): schema, search with filters and counts |
| `app/crawlers.py` | Importers: SEC Form ADV, SEC Form D, company job boards, SimplifyJobs (background, with progress) |
| `app/taxonomy.py` | Handshake-style categories and the rules that assign them |
| `app/alerts.py` | Job-alert emails (IMAP read-only, or pasted/uploaded) → postings |
| `app/pipeline.py` | Events, calendar and .ics, assessment prep, thank-you notes, offers, networking |
| `app/timelines.py` | Typical recruiting windows by field, placed on your own summers |
| `static/pipeline.js` | Next steps on job cards, the Network and Calendar pages |
| `app/official.py` | Robots-aware fetcher and official employer page lookup (registry in `app/official_sources.json`) |
| `app/onet.py` | O*NET occupations: download, role matching, occupation profiles, questions by occupation |
| `app/grading.py` | Rubric grading for stories and short answers, quote checks, "was this fair?" log |
| `app/coderun.py` | Runs Python answers against LeetCodeDataset test cases with time and resource limits |
| `tests/calibration/` | Fixed answers with expected grades; `run_calibration.py` is the grading regression test |
| `app/qlibrary.py` | Outside questions: Tech Interview Handbook, LeetCode (alfa-leetcode-api), Open Interview |
| `app/sourcing.py` | Find tab: category searches, SimplifyJobs lists, Hacker News, your sites, AI screening, list/CSV import |
| `app/research.py` | Reads a firm's website, finds the contact and their email (incl. email patterns, Hunter.io) |
| `app/outreach.py` | Cold emails, application-form walkthrough, .txt export |
| `.claude/skills/boutique-outreach/` | Claude Code skill with the outreach playbook |
| `app/prompts.py` | Prompts for interview, profile, fit and tailoring |
| `app/pdf.py` | Resume and cover-letter PDF rendering (embeds a Chinese font when needed) |
| `app/jobs_fetch.py` | Reads job descriptions from posting URLs |
| `app/store.py` | JSON-file storage in `data/` |
| `static/` | The web page (plain HTML, CSS and JS; no build step) |

## Data sources and credits

- **O*NET:** includes information from the O*NET 31.0 Database by the U.S. Department of Labor, Employment and Training Administration (USDOL/ETA), used under the [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/) license. O*NET® is a trademark of USDOL/ETA. Job Agent selected and reformatted this information (titles, core tasks, technologies, top knowledge areas) to write practice questions; USDOL/ETA has not approved, endorsed, or tested these modifications.
- **Frameworks:** competency names follow NACE's career readiness competencies, with our own short descriptions. Proficiency levels follow the style of OPM's structured-interview rating scales.
- **Employer pages:** only fetched where robots.txt allows. They're used as prep material on your computer and are linked, not republished.
- **Other sources:** SEC EDGAR, LeetCodeDataset (Apache-2.0), Tech Interview Handbook (MIT) and Open Interview (MIT); see above.

## Optional services (Settings)

| Service | Used for | Free tier |
|---|---|---|
| DeepSeek | All AI steps | Pay as you go |
| Google Gemini (Flash-Lite by default) | Automatic backup when DeepSeek is busy | Free key from Google AI Studio |
| Tavily | Find searches, interview look-ups, company prep | 1,000 searches a month |
| Serper.dev | Google results for Find | 2,500 searches to start |
| Hunter.io | Email lookups when a site doesn't list one | About 50 lookups a month |
| Your email (IMAP app password) | Job alerts from LinkedIn, Handshake, Indeed and others | Free |
| AssemblyAI or SiliconFlow (or any OpenAI-style transcription) | 🎤 voice when the browser's voice typing doesn't work | See provider |

## Not built yet

- Creating drafts directly in Gmail or Outlook through their APIs (needs a Google Cloud / Microsoft app registration and OAuth). For now the app opens a pre-filled draft via a compose link. The same OAuth would let Outlook.com users connect their inbox for job alerts (Microsoft turned off app passwords for it).
- A real sandbox for running code when hosted for many users (a container or Judge0). Today's limits suit one person running their own code on their own computer.
- Checking the grader against human ratings (for example, career-center staff scoring the calibration answers).
- Chrome extension that fills application forms (the user clicks Submit)
- Accounts and a real database for hosting many users
- Crawling JavaScript-heavy sites at scale (Crawl4AI or Firecrawl would replace `app/research.py`'s fetcher; Jina Reader is used as a fallback today)
- FINRA BrokerCheck for boutique investment banks, and discovering more company job boards automatically
