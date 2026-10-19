# Job Agent

A web app that interviews a job seeker, builds their profile, scores their fit for each job, writes a tailored resume and cover letter (as PDFs), and tracks every application.

The AI runs on DeepSeek (OpenAI-compatible API), so it works from both the US and China. An optional backup (Google Gemini by default) takes over automatically when DeepSeek is busy, slow or down. Only the conversational and judgment steps call the model (interview, stories, skills check, profile, fit scoring, writing documents). PDFs, job-page reading and the tracker are plain code and use no tokens.

## Run it

```bash
./run.sh
```

Then open http://127.0.0.1:8000, go to **Settings** and paste your DeepSeek API key. The key is saved only in `data/settings.json` on this computer.

## How it flows

1. **Interview**: the coach asks about goals, education, experience, projects, the skills you use, and contact info, one question at a time.
   - **Upload CV** (PDF, .docx, .txt): the coach reads it and only asks about what's missing.
   - **🎤 Voice**: talk instead of typing. Uses the browser's voice typing (Chrome, Edge, Safari) by default, or a speech-to-text service from Settings (e.g. SiliconFlow SenseVoice, which works in China). **🔊 Read replies aloud** uses the computer's built-in voices.
   - **Web lookups** (optional Tavily key in Settings): the coach looks up course codes, small companies and labs you mention, and shows "🔎 Looked up: …" in the chat. Without a key it asks you what they are.
2. **Stories**: behavioral questions from the question bank (teamwork, conflict, failure, ownership, plus optional extras). The coach asks up to 3 follow-ups per story until it has the situation, task, what *you* did, the result, and what you learned, and rates how convincing each story is.
3. **Skills check**: about 5 technical questions picked from the question bank for your target roles and listed skills, graded 0–4 against a rubric, with feedback and a model answer. Tested skills show as verified (✓) in your profile.
4. **Build my profile**: turns the CV, interview, stories, skills check and anything looked up into `data/profile.json`, plus an honest evaluation (strengths, gaps, roles to target). Download a general CV from the Profile tab.
5. **Find**: search the web by category (search funds, small PE, boutique banks, small VC, boutique consulting, family offices, or job postings on Greenhouse/Lever/Ashby), with optional location and industry. The AI screens results with the boutique-internship rules (under ~30 employees, no big-name firms, search funds still searching). Databases that need a login (Axial, SearchFunder) and LinkedIn are browsed by hand and pasted or imported as a CSV.
6. **Jobs**: one workspace per firm or posting:
   1. **Research & contact**: reads the firm's home, team, about, contact and careers pages; writes a summary and specific hooks; picks the senior person to contact; finds their email (published on the site, Hunter.io if you add a key, or a guess from the firm's email pattern, clearly marked). If there's no email, it points you to Apollo.io.
   2. **Cold email** (with LinkedIn note and 7-day follow-up) in the voice of your example emails, opened as a draft in Gmail, Outlook or your mail app; or **Application form**: paste the form or its link, and walk through every question with drafted answers; download all answers as a .txt.
   3. **Fit score, tailored resume and cover letter** (addressed to the contact).
7. **Tracker**: status, contact, applied/contacted date, follow-up due date and notes; export to CSV for Excel or Google Sheets.
8. **Guide**: the outreach playbook, including how to find emails with Apollo.io.

## Layout

| Path | What it is |
|---|---|
| `app/main.py` | API routes |
| `app/llm.py` | Model calls with automatic fallback to the backup provider, and the web-search tool loop |
| `app/services.py` | Optional outside services: Tavily web search, speech-to-text |
| `app/resume_text.py` | Reads text out of uploaded CVs |
| `app/assess.py` | Stories (behavioral STAR follow-ups) and the Skills check |
| `app/question_bank.json` | Behavioral and technical question bank; edit it to add questions |
| `app/sourcing.py` | Find tab: category searches, AI screening, list/CSV import |
| `app/research.py` | Reads a firm's website, finds the contact and their email (incl. email patterns, Hunter.io) |
| `app/outreach.py` | Cold emails, application-form walkthrough, .txt export |
| `.claude/skills/boutique-outreach/` | Claude Code skill with the outreach playbook |
| `app/prompts.py` | Prompts for interview, profile, fit and tailoring |
| `app/pdf.py` | Resume and cover-letter PDF rendering (embeds a Chinese font when needed) |
| `app/jobs_fetch.py` | Reads job descriptions from posting URLs |
| `app/store.py` | JSON-file storage in `data/` |
| `static/` | The web page (plain HTML, CSS and JS; no build step) |

## Optional services (Settings)

| Service | Used for | Free tier |
|---|---|---|
| DeepSeek | All AI steps | Pay as you go |
| Google Gemini (Flash-Lite by default) | Automatic backup when DeepSeek is busy | Free key from Google AI Studio |
| Tavily | Find tab searches, interview look-ups | 1,000 searches a month |
| Hunter.io | Email lookups when a site doesn't list one | About 50 lookups a month |
| AssemblyAI or SiliconFlow (or any OpenAI-style transcription) | 🎤 voice when the browser's voice typing doesn't work | See provider |

## Not built yet

- Creating drafts directly in Gmail or Outlook through their APIs (needs a Google Cloud / Microsoft app registration and OAuth). For now the app opens a pre-filled draft via a compose link.
- Chrome extension that fills application forms (the user clicks Submit)
- Accounts and a real database for hosting many users
- Crawling JavaScript-heavy sites at scale (Crawl4AI or Firecrawl would replace `app/research.py`'s fetcher; Jina Reader is used as a fallback today)
