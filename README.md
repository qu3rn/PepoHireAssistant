# PepoHireAssistant / cv-sender

A **local** job application assistant that helps you semi-automatically apply to job offers.  
It evaluates offers, stores your application history, opens job pages in a real browser, fills forms with your profile data, uploads your CV, and **stops before the final submit** so you can review everything manually.

> ⚠️ **Safety notice**  
> This tool is **not** a spam bot.  
> `require_manual_confirm` is `true` by default and the final "Send" button is **never** clicked automatically.  
> Do not use this tool to bypass CAPTCHAs, login protection, or bot detection systems.

---

## Features

- Score job offers deterministically (role, salary, tech stack, location)
- Optional LLM refinement via **LM Studio** (local, offline)
- Browser automation with **Playwright** – fills forms, uploads CV
- Portal-specific fillers for RocketJobs, Pracuj.pl, LinkedIn (+ generic fallback)
- JSON-file storage for offers and application history
- Rich CLI with `typer`
- **Batch URL import** – paste multiple job offer URLs and import + score them in one click
- **Local Streamlit UI** – dashboard, offer management, application history, profile and settings editor

---

## Installation

```bash
# Clone the repository
git clone https://github.com/qu3rn/PepoHireAssistant.git
cd PepoHireAssistant

# Create a virtual environment
python -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate

# Install dependencies
pip install -r requirements.txt
pip install -e .

# Install Playwright browsers
playwright install chromium
```

---

## Configuration

```bash
# Create local config files from the bundled examples
cv-sender init
```

Then edit:

| File | Purpose |
|---|---|
| `config/profile.yaml` | Your personal data (name, email, CV path, salary expectations …) |
| `config/settings.yaml` | Search criteria and scoring thresholds |

---

## LM Studio Setup (optional)

1. Download [LM Studio](https://lmstudio.ai/) and load a model.
2. Start the local server on `http://localhost:1234`.
3. Set `lm_studio.enabled: true` in `config/settings.yaml`.

The app works without LM Studio – it falls back to deterministic scoring automatically.

---

## Example Usage

```bash
# Add a job offer manually
cv-sender add-offer

# Score all saved offers
cv-sender score-offers

# List offers with score >= apply threshold
cv-sender list --show offers --decision apply

# Open an offer in the browser, fill the form, and wait for your review
cv-sender apply --offer-id <id>

# Launch the Streamlit web UI (default: http://localhost:8501)
cv-sender ui

# Launch on a custom host/port
cv-sender ui --host 0.0.0.0 --port 8080
```

> ⚠️ **Form filling safety**  
> The "Fill application form" button in the UI calls the same Playwright automation as the CLI.  
> It fills the form and uploads your CV, then **stops and waits** – it never clicks the final Submit button.  
> You must review and submit the form yourself in the browser window that opens.

---

## First MVP flow

This is the recommended end-to-end flow for using the UI to apply for a job manually.

### 1. Start the UI

```bash
cv-sender ui
# opens http://localhost:8501 in your browser
```

### 2. Add an offer manually

1. Go to **Offers** in the sidebar.
2. Expand **➕ Add offer manually**.
3. Fill in at minimum: **Job title** and **Offer URL**.
4. Click **Save offer**.

The offer is saved to `data/offers.json`. Duplicate URLs are rejected automatically.

### 3. Score the offer

In the offer card that appears, click **Re-score**.

The scorer runs deterministically (role match, tech stack, salary, location).  
If LM Studio is running and `lm_studio.enabled: true` in settings, the LLM result is merged in automatically.  
If LM Studio is unavailable, a warning is shown and deterministic scoring is used.

The score and decision (`apply` / `maybe` / `skip`) are saved immediately.

### 4. Fill the application form

Click **Fill application form** on the offer card.

What happens:
- The offer URL opens in a real (non-headless) Chromium window.
- The GenericFiller tries to click the "Apply" button, fill name / email / phone / city / LinkedIn / portfolio fields, and upload your CV from `profile.cv_path`.
- Data-processing consent is checked if `profile.consents.data_processing` is `true`.
- The browser window closes after filling (no waiting prompt when called from the UI).
- An **Application** record is created in `data/applications.json` with status `ready_to_send`.
- A `form_filled` event is appended to the record.
- The UI shows: **"Application form has been filled. Please review it manually before submitting."**

> ⚠️ **The form is never submitted automatically.**  
> To get an interactive browser session where you can review the filled form before the window closes, use the CLI instead:
> ```bash
> cv-sender apply --offer-id <id>
> ```
> This keeps the browser open and waits for you to press Enter in the terminal.

### 5. Track the application

Go to **Applications** in the sidebar.

- Change the status (e.g. `sent`, `reply_received`, `interview`, `offer`, `rejected`).
- Add notes.
- Click **Save changes** – status changes are persisted and a `status_changed` event is appended.

---

## Batch URL import

### How it works

1. Go to **Offers** in the sidebar.
2. Expand **📋 Batch import URLs**.
3. Paste job offer URLs – one per line – into the text area.
4. Optionally set a **Source override** (e.g. `linkedin`), otherwise the source is inferred from the URL hostname.
5. Leave **Auto-score after import** checked to score each new offer immediately.
6. Adjust **Max URLs per batch** (default: 20, hard limit: 50).
7. Click **Import URLs**.

After import, a summary table shows the result for every URL:

| Column | Meaning |
|---|---|
| Status | `imported`, `duplicate`, `failed`, `invalid`, `skipped_limit` |
| ID | First 8 characters of the offer UUID |
| Title | Derived from the URL path |
| Score | Deterministic score (if auto-score was enabled) |
| Decision | `apply` / `maybe` / `skip` |
| Error | Reason for failure / warning |

### Limits

| Setting | Value |
|---|---|
| Default max URLs | 20 |
| Hard max URLs | **50** (enforced server-side) |

URLs beyond the limit are marked `skipped_limit`.

### Duplicate handling

- **Within input**: if you paste the same URL twice, only the first occurrence is imported.
- **Against storage**: if an offer with the same normalized URL already exists, the import is skipped.
- Normalization strips trailing slashes, URL fragments, and well-known tracking parameters (`utm_source`, `utm_medium`, `utm_campaign`, `fbclid`, `gclid`, etc.). Job-board-specific query parameters (e.g. `id=12345`) are preserved.

### Important limitations

> **This is not a crawler.**  
> No HTTP requests are made to the job offer pages. Each URL is stored as an offer with a title derived from the URL path only. You must fill in company name, salary, and other details manually (or let the scorer infer what it can from the URL and title).

> **Some sites may block automated page fetching.**  
> If you later add scraping/extraction, be aware that many job boards use bot detection, CAPTCHAs, and login walls. Never bypass these protections.

---

## Supported job boards

| Board | Domain | Extraction strategy |
|---|---|---|
| **RocketJobs** | `rocketjobs.pl` | `__NEXT_DATA__` (Next.js embedded state) → JSON-LD fallback |
| **JustJoinIT** | `justjoin.it` | `__NEXT_DATA__` → JSON-LD fallback |
| **NoFluffJobs** | `nofluffjobs.com` | JSON-LD `JobPosting` → `__NEXT_DATA__` fallback |
| **Pracuj.pl** | `pracuj.pl` | JSON-LD `JobPosting` → `__NEXT_DATA__` fallback |
| **Generic** | *(any other site)* | JSON-LD `JobPosting` → `__NEXT_DATA__` generic traversal → `<title>` tag |

### Extraction strategy (priority order)

1. **JSON-LD `JobPosting`** – most reliable; standardised schema.org format.
2. **`__NEXT_DATA__` embedded state** – Next.js sites embed full page data in a `<script id="__NEXT_DATA__">` tag; source-specific field mappings extract title, company, salary, skills, etc.
3. **DOM / `<title>` tag** – last resort; extracts title only.

Each extracted offer shows **Extraction details** (collapsible) in the Offers page with:
- `extraction_source`: which strategy succeeded (`json_ld`, `embedded_state`, `dom`, `url_only`)
- `extraction_confidence`: 0–100 % of key fields populated
- Any warnings (e.g. low confidence, fallback used)

### Important limitations

- **No login / CAPTCHA bypass** – protected or login-walled pages will return no useful data. The offer is saved with URL-only mode (title derived from URL path).
- **Site structure changes** – job boards sometimes redesign their pages. Extraction may degrade until updated.
- **Not a crawler** – only the single offer URL is fetched; search result pages are not scraped.
- **Bot detection** – some sites may block automated requests. The server falls back gracefully and saves the offer with URL-only data.

---

## Save to Job Assistant – bookmarklet

The bookmarklet lets you import a job offer from your browser with a single click, without leaving the job offer page.

### Quick setup

**1. Start the Streamlit UI** (if not already running):

```bash
cv-sender ui
# → http://localhost:8501
```

**2. Start the bookmarklet server** (in a separate terminal):

```bash
cv-sender bookmarklet-server
# → http://127.0.0.1:8765
```

**3. Create the browser bookmark:**

1. Open your browser's bookmarks bar.
2. Create a new bookmark (right-click the bookmarks bar → *Add page…*).
3. Set the **name** to: `Save to Job Assistant`
4. Set the **URL / address** to this JavaScript code (copy the entire line):

```javascript
javascript:(()=>{const u=encodeURIComponent(location.href);window.open('http://localhost:8765/import?url='+u,'_blank');})()
```

You can also copy the code from the **Bookmarklet** page in the Streamlit UI.

**4. Use it:**

1. Open any job offer page in your browser.
2. Click the **Save to Job Assistant** bookmark.
3. A new tab opens showing the import result.
4. Switch to the Streamlit UI → **Offers** to review, score, and manage the imported offer.

### API endpoints

| Endpoint | Description |
|---|---|
| `GET /health` | Returns `{"status": "ok"}` |
| `GET /import?url=<encoded-url>` | Imports the offer and returns an HTML result page |

### Limitations

- **No page scraping** – only the URL is stored. Title is derived from the URL path. Fill in company, salary, and description manually.
- **Local only** – the server binds to `127.0.0.1`. It is not accessible from other machines.
- Auto-score runs the deterministic scorer (+ LLM if LM Studio is enabled in settings).
- The server does not bypass CAPTCHAs, logins, or bot detection.

---

## File Structure

```
cv-sender/
├── src/cv_sender/
│   ├── cli.py          # CLI commands (including `cv-sender ui`)
│   ├── config.py       # YAML config loading/saving
│   ├── models.py       # Pydantic models
│   ├── storage.py      # JSON file storage
│   ├── scorer.py       # Deterministic + LLM scoring
│   ├── llm.py          # LM Studio integration
│   ├── browser.py      # Playwright session management
│   ├── form_filler.py  # Orchestrates form filling
│   ├── form_debug.py   # Debug artifacts: StepLogger, form snapshot, detection helpers
│   ├── services.py     # Business-logic service layer (used by UI & CLI)
│   ├── url_utils.py    # URL validation, normalization, source inference
│   ├── bookmarklet_server.py  # FastAPI local server for the bookmarklet
│   ├── ui.py           # Streamlit web UI
│   └── portals/        # Portal-specific form fillers
│       ├── base.py
│       ├── generic.py
│       ├── rocketjobs.py
│       ├── pracuj.py
│       ├── justjoin.py
│       ├── nofluffjobs.py
│       └── linkedin.py
└── src/cv_sender/extractors/  # Source-specific offer extractors
    ├── __init__.py     # Registry + HTTP fetch + public extract_offer()
    ├── base.py         # OfferDraft, BaseExtractor, normalization helpers
    ├── generic.py      # Generic fallback (JSON-LD → __NEXT_DATA__ → title)
    ├── rocketjobs.py   # RocketJobs extractor
    ├── justjoin.py     # JustJoinIT extractor
    ├── nofluffjobs.py  # NoFluffJobs extractor
    └── pracuj.py       # Pracuj.pl extractor
├── config/
│   ├── profile.example.yaml
│   └── settings.example.yaml
├── data/
│   ├── offers.json             (created by the app)
│   ├── applications.json       (created by the app)
│   └── debug/form_filling/     (debug artifacts, one folder per run)
└── tests/
    ├── test_config.py
    ├── test_models.py
    ├── test_scorer.py
    ├── test_services.py
    ├── test_batch_import.py
    ├── test_bookmarklet_server.py
    ├── test_extractors.py
    ├── test_storage.py
    ├── test_form_filler.py
    └── test_form_debug.py
```

---

## Form filling

Click **Fill application form** from the offer card to open the job board in a browser, auto-fill your profile data, and pause for your review before you hit _Submit_.

### Supported fillers

| Portal | Source key | Notes |
|---|---|---|
| RocketJobs | `rocketjobs.pl` | Name, email, phone, LinkedIn, GitHub; CV upload |
| JustJoinIT | `justjoin.it` | Standard apply form; warns if login required |
| NoFluffJobs | `nofluffjobs.com` | Email, phone, salary; warns if login / registration wall detected |
| Pracuj.pl | `pracuj.pl` | Warns if login wall detected |
| Generic fallback | `*` | Label/placeholder heuristics for any other board |

### Safety rules

- **`auto_submit` is always `False`** – the form is never submitted automatically.
- Login walls, CAPTCHAs, and bot-detection mechanisms are **not** bypassed.
- If a source-specific filler fails entirely, the generic heuristic filler is tried as a fallback.
- The result always carries a `status` (`filled` / `partial` / `failed`), lists of filled/missing fields, and any warnings.

### Debug mode

To debug form filling, set the following in `config/settings.yaml`:

```yaml
form_filling:
  debug: true       # take a screenshot on failure (saved to data/debug/screenshots/)
  slow_mo_ms: 500   # slow down each Playwright action by 500 ms
  headless: false   # keep the browser window visible (default)
```

### Known limitations

- Sites that require login or registration cannot be filled without prior manual authentication.
- CAPTCHAs must be solved manually.
- File-upload fields backed by hidden `<input type="file">` elements may not work on all sites.
- Job boards served through external ATS (e.g., Greenhouse, Lever, Workday) are handled by the generic filler only.
- Site structure changes may break specific selectors; report issues if a filler stops working.

---

## Debugging form filling

When a fill is `partial` or `failed`, the app automatically captures debug artifacts and displays them in the UI.

### Where debug files are stored

```
data/debug/form_filling/<run_id>/
├── metadata.json       # Run summary: filler, status, fields, warnings, error
├── step_log.json       # Chronological action log (no sensitive values)
├── form_snapshot.json  # Sanitized list of detected form fields (no input values)
└── screenshot.png      # Browser screenshot at time of failure
```

Each run has a UUID `run_id`. Artifacts are always written for non-successful fills; all artifacts are written when `debug: true`.

### How to enable debug mode

In `config/settings.yaml`:

```yaml
form_filling:
  debug: true             # master switch: enables all artifact collection
  slow_mo_ms: 250         # slow down Playwright actions (ms) for easier inspection
  headless: false         # keep browser visible
  screenshot_on_failure: true   # save screenshot.png on partial/failed
  save_form_snapshot: true      # save sanitized form field list
  save_step_log: true           # save action-by-action log
```

To disable individual artifacts while keeping `debug: true`, set the relevant flag to `false`.

### Viewing debug output in the UI

1. Open the **Debug** page from the sidebar.
2. The page lists the 50 most recent runs with status, filler, and field counts.
3. Select any run to view:
   - Step log table (action, target selector, status, message)
   - Detected fields table (tag, type, name, placeholder, label)
   - Screenshot (if available)
4. The **Applications** page shows a **Form filling debug** expander for each application that has a matching debug run.
5. Immediately after filling, the fill result panel includes a **Form filling debug** expander and two retry buttons:
   - **Retry with same filler** – re-runs the source-specific filler.
   - **Retry with GenericFiller** – bypasses the source-specific filler and uses label/placeholder heuristics.

### Privacy note

Debug snapshots are designed to not contain sensitive personal data:

- `step_log.json` records **which action was taken** and **which selector was targeted**, but **never the value typed**.
  - Correct: `{ "action": "fill_email", "target": "label:Email", "status": "success" }`
  - Wrong (never stored): `{ "action": "fill_email", "value": "my@email.com" }`
- `form_snapshot.json` records form field structure (tag, type, name, placeholder, label text) but **never the current value** of any input.
- `screenshot.png` captures the browser window at the time of failure. Depending on the page, this may include some pre-filled text. Keep screenshots private or disable them with `screenshot_on_failure: false`.

### Known limitations

- Screenshots may capture partially filled form content depending on timing.
- Some CAPTCHA and bot-detection pages may not render correctly in a headless browser; the step log will contain a `detect_captcha` or `detect_blocked_page` entry.
- Login walls are detected by URL pattern and the presence of a password field; they are logged but never bypassed.
- Debug artifacts accumulate over time; `data/debug/form_filling/` can be cleared manually at any time.

---

## CV Profiles

Configure multiple CV files so the right one is uploaded automatically for each job offer.

### Configuration

Add a `cv_profiles` section to `config/profile.yaml`:

```yaml
default_cv_id: "frontend_react"   # fallback when scores are tied

cv_profiles:
  - id: "frontend_react"
    name: "Frontend React CV"
    path: "data/cv/frontend_react.pdf"
    target_roles: ["Frontend Developer", "React Developer"]
    technologies: ["React", "TypeScript", "Next.js"]
    seniority: ["Mid", "Senior"]
    priority: 100    # higher = preferred on ties
    active: true

  - id: "fullstack"
    name: "Fullstack CV"
    path: "data/cv/fullstack.pdf"
    target_roles: ["Fullstack Developer", "Software Engineer"]
    technologies: ["Node.js", "React", "TypeScript", "PostgreSQL"]
    seniority: ["Senior"]
    priority: 80
    active: true
```

When `cv_profiles` is empty, the legacy `cv_path` field is used for all offers (backward-compatible).

### Automatic selection scoring

Each active CV is scored against the offer title + description + technology list:

| Criteria | Points |
|---|---|
| Any `target_roles` entry found in offer text | +40 |
| Each `technologies` entry found in offer | +10 (max 40) |
| Any `seniority` entry found in offer title | +15 |
| `priority` bonus | `priority ÷ 10` (max 10) |
| File missing or profile inactive | −100 (disqualified) |

The highest-scoring profile is selected.  Ties are broken by `priority`, then by `default_cv_id`.

### UI

- **Profile page → CV Profiles**: shows a table of all configured profiles with a file-exists indicator.  Edit `config/profile.yaml` to add or modify profiles.
- **Offer card → CV selection**: shows the auto-recommended CV (with reasons) and a dropdown to override before filling.
- **Applications page**: shows the CV name used for each application.

---

## Application answer generation

When filling application forms, the assistant can automatically generate short answers for free-text questions (textareas) using a layered strategy.

### How it works

1. **Rules** — deterministic answers for salary, English level, availability, notice period
2. **Templates** — fill-in-the-blank answers for motivation/experience questions using your `answer_profile`
3. **LM Studio** (optional) — uses your local LLM for complex questions not covered by rules/templates

### Safety rules

- Answers are **never** auto-submitted; all answers should be reviewed before submission.
- Generated answers never invent experience — only facts from `answer_profile` and the offer are used.
- A hallucination guard warns if the answer mentions technologies not present in your profile or the offer.
- Low-confidence answers (below `min_confidence_to_autofill`) are logged but not filled automatically.

### Configuration

Add to `config/settings.yaml`:

```yaml
answer_profile:
  short_bio: "Your bio here"
  years_experience: "5 years"
  strongest_skills: [React, TypeScript]
  motivation_general: "I look for product-focused teams..."
  salary_b2b: "18000 PLN net + VAT"
  english_level: "C1"
```

### Preview answers

In the UI, open any offer card and click **"Preview application answers"** to see what answers would be generated before filling any form.

### LM Studio (optional)

Enable `answers.use_llm: true` in `settings.yaml`. The LLM is called only for question types not covered by rules or templates. If LM Studio is unavailable, the answer is left empty and a warning is added.

### Limitations

- Textarea detection relies on `<label>`, `aria-label`, or `placeholder` — complex custom form widgets may not be detected.
- Answers are at most `max_answer_chars` (600) characters.
- Only textareas on the initially visible page are processed; dynamically added fields may be missed.

---

## Roadmap

- [ ] Automated offer scraping from job boards
- [ ] Cover-letter generation via LLM
- [ ] Multi-step form navigation (LinkedIn Easy Apply wizard)
- [ ] Email notification on application status changes
- [x] Web UI dashboard
- [x] Manual offer entry in UI
- [x] End-to-end MVP flow (add → score → fill → track)
- [x] Batch URL import with normalization and duplicate detection
- [x] Save to Job Assistant bookmarklet (local FastAPI receiver)
- [x] Source-specific offer extractors (RocketJobs, JustJoinIT, NoFluffJobs, Pracuj.pl)
- [x] CV profiles with automatic per-offer selection
- [x] Follow-up reminders and application lifecycle tracking
- [x] Gmail read-only integration for detecting recruiter replies
- [x] Google Calendar integration and interview scheduler
- [x] Analytics dashboard with funnel, response rates, source/CV/tech performance, and CSV export

---

## Follow-up tracking

After marking an application as **sent**, the assistant automatically schedules a
follow-up reminder.

### How due dates are calculated

The due date is computed by adding `default_follow_up_after_days` (default: 5) calendar
days to `sent_at`. When `allow_weekend_due_dates` is `false` (default), Saturday and
Sunday are skipped — the date is rolled forward to the next Monday.
All timestamps are stored in UTC.

### Marking a follow-up sent

Click **"Follow-up sent"** on the application card or in the "Follow-ups due now" panel
on the dashboard. This sets `status = follow_up_sent` and records `last_contact_at`.

### Snoozing reminders

Click **"Snooze 2 days"** to defer the reminder. The snooze is stored in
`reminder_snoozed_until`; the application re-appears in the due panel once it expires.

### Stale / no-response detection

Applications in `sent`, `follow_up_due`, or `follow_up_sent` status with no activity
for `mark_no_response_after_days` (default: 14) days are counted in the Dashboard stale
metric.

### Emails are never sent automatically

The follow-up message draft shown in the application detail is for **manual use only**.
Copy and paste it into your email client. The app never sends emails.

### Configuration

```yaml
follow_up:
  enabled: true
  # Days after sending before showing the follow-up reminder
  default_follow_up_after_days: 5
  # Days after last contact before marking as "no response / stale"
  mark_no_response_after_days: 14
  # How far ahead to show upcoming due reminders in the dashboard
  show_due_within_days: 3
  # When false, Saturday/Sunday due dates are moved to the next Monday
  allow_weekend_due_dates: false
```

---

## Gmail read-only integration

The app can scan your Gmail inbox in **read-only** mode to detect recruiter or company
replies and suggest status updates for your applications.

> No emails are sent, deleted, archived, or modified.  
> Full email bodies are never stored by default.  
> OAuth tokens and credentials are never logged or committed.

### How to create Google Cloud OAuth credentials

1. Go to [https://console.cloud.google.com](https://console.cloud.google.com).
2. Create a new project (or select an existing one).
3. Enable the **Gmail API**: _APIs & Services → Library → Gmail API → Enable_.
4. Create credentials: _APIs & Services → Credentials → Create Credentials → OAuth client ID_.
5. Choose **Desktop app** as the application type.
6. Download the JSON file.
7. Save it to `config/google_credentials.json` (the path is configurable).

### Where to place the credentials file

Default path: `config/google_credentials.json`

This path is listed in `.gitignore` and will never be committed.

### How to enable Gmail in settings

Set `gmail.enabled: true` in `config/settings.yaml`, or use the **Settings** page in the UI.

```yaml
gmail:
  enabled: true
  credentials_path: "config/google_credentials.json"
  token_path: "config/google_token.json"
  scan_days_back: 30
  max_results: 100
  store_snippet: true
  store_email_body: false
  auto_update_status: false
```

### How to start the OAuth flow

Open the **Gmail** page in the UI and click **Scan**.
A browser window will open asking you to sign in with your Google account and grant
read-only access.

The token is saved to `config/google_token.json` and reused for all future scans.
This file is listed in `.gitignore`.

### Read-only scope

The app requests only one OAuth scope:

```
https://www.googleapis.com/auth/gmail.readonly
```

This scope allows listing and reading messages. It does **not** allow sending,
deleting, or modifying any email or label.

### How to scan emails

1. Open the **Gmail** page in the Streamlit UI.
2. Click **Scan last 7 days** or **Scan last 30 days**.
3. Review matches in the **Email matches** table.
4. Click **Apply suggestion** to update the application status, or **Ignore** to dismiss.

Suggestions are **never applied automatically** (`auto_update_status` defaults to `false`).

### Email matching strategy

Emails are matched to applications using a score:

| Signal | Points |
|---|---|
| Company name in from/subject/snippet | +50 |
| Job title in subject/snippet | +30 |
| Sender domain resembles company name | +20 |
| Application sent within last 45 days | +15 |
| ≥2 recruiter keywords present | +10 |
| Marketing/newsletter keywords detected | −50 |

Only matches scoring ≥ 50 are recorded.

### Classification

Emails are classified by keyword rules:

| Classification | Triggers |
|---|---|
| `rejection` | "unfortunately", "nie możemy zaprosić", "decided not to move forward" |
| `interview_invitation` | "interview", "rozmowa", "availability", "termin", "next step" |
| `offer` | "job offer", "oferta współpracy" |
| `automated_confirmation` | "thank you for applying", "dziękujemy za aplikację" |
| `reply_received` | Other recruiter keywords |
| `unknown` | No strong keywords matched |

If LM Studio is enabled, it is used as a fallback for ambiguous emails.

### What data is stored locally

| Field | Stored |
|---|---|
| Gmail message ID | Always (for deduplication) |
| Thread ID | Always |
| Sender name / email | Always |
| Subject | Always |
| Snippet (≤200 chars) | When `store_snippet: true` (default) |
| Full email body | Only when `store_snippet: false` AND `store_email_body: true` |
| OAuth token | `config/google_token.json` (gitignored) |
| OAuth credentials | `config/google_credentials.json` (gitignored) |

Data is stored in `data/email_matches.json` (gitignored).

### Known limitations

- Only emails matching job-related keywords are fetched (reduces noise but may miss some replies).
- The matching heuristic is rule-based; unusual company names or indirect replies may not match.
- Gmail API quota: ~1 billion units/day for free; scanning 100 messages uses ~200 units.
- Calendar events are **not** created automatically even when an interview invitation is detected.
- Only the `gmail.readonly` scope is requested; modifying labels is not supported.


---

## Interview scheduling and Google Calendar

The app lets you track interviews and, optionally, create Google Calendar events.

### Quick start

1. **Schedule manually** — go to the **Interviews** page and fill in the _Schedule new_ form.
2. **Schedule from a Gmail match** — on the **Gmail** page, find an _interview invitation_ match
   and click **Schedule interview**.
3. Interviews appear in the **Interviews** page and the **Dashboard** upcoming section.

### Enabling Google Calendar (optional)

Calendar events are **never created automatically**. You must opt in every time.

#### Setup steps

1. Reuse the same `config/google_credentials.json` file from Gmail setup (see above).  
   Both Gmail and Calendar share the same OAuth 2.0 client, but each has its own token file.
2. Enable the **Google Calendar API**: _APIs & Services → Library → Calendar API → Enable_.
3. Set `calendar.enabled: true` in `config/settings.yaml`:

```yaml
calendar:
  enabled: true
  credentials_path: "config/google_credentials.json"   # same file as Gmail
  token_path: "config/google_calendar_token.json"      # separate token
  calendar_id: "primary"
  timezone: "Europe/Warsaw"
  default_interview_duration_minutes: 60
  create_calendar_events: true          # must be true to allow event creation
  add_reminders: true
  reminder_minutes_before: [1440, 60]  # 24 h and 1 h before
```

4. The first time you create a Calendar event, a browser window will open for OAuth authorisation.
   The token is saved to `config/google_calendar_token.json` (gitignored).

#### OAuth scope

```
https://www.googleapis.com/auth/calendar.events
```

This scope allows creating, updating, and deleting events in your calendar. It does **not**
allow reading other people's calendars or accessing calendar settings.

#### Privacy and security notes

- `config/google_calendar_token.json` and `config/google_credentials.json` are gitignored.
  **Never commit them.**
- Calendar events are only created after you check the _Create calendar event_ checkbox and
  confirm — there is no background or automatic event creation.
- The `calendar.create_calendar_events` setting must be `true` (opt-in) for event creation
  to be allowed at all.
- Setting `create_calendar_events: false` (the default) disables the checkbox in the UI even
  if the calendar integration is otherwise enabled.

#### Interview data stored locally

Interviews are saved to `data/interviews.json` (gitignored). Fields:

| Field | Description |
|---|---|
| `id` | UUID |
| `application_id` | Link to `Application` |
| `interview_at` | Scheduled datetime (UTC) |
| `duration_minutes` | Duration |
| `interview_type` | `phone` / `video` / `onsite` / `technical` / `hr` / `unknown` |
| `status` | `scheduled` / `completed` / `cancelled` / `rescheduled` |
| `source` | `manual` / `gmail` / `calendar` |
| `calendar_event_id` | Google Calendar event id (empty when no event created) |


---

## Analytics

The **Analytics** page (in the Streamlit sidebar) gives a local-only view of your job-search performance.

### Metrics shown

| Section | What you see |
|---|---|
| **Funnel** | Offers imported → scored → ready → sent → replies → interviews → offers |
| **Response rates** | Response %, interview %, rejection %, offer % (all vs sent) |
| **Time metrics** | Average and median days to first reply; days to interview; activity in last 7/30 days |
| **Weekly activity** | Bar chart of sent applications, replies, and interviews per ISO week |
| **Source performance** | Per-portal sent/reply/interview counts and response rate |
| **CV profile performance** | Per-CV sent/reply/interview counts and response rate |
| **Technology performance** | Per-technology sent/reply/interview counts and response rate |
| **Salary analysis** | Average salary ranges for sent vs replied vs interviewed; response rate by salary bucket |
| **Insights** | Deterministic plain-English observations derived from the data above |

### How to interpret response rate and interview rate

- **Response rate** = replies ÷ sent × 100. Includes interview invitations, rejections, and offers.
- **Interview rate** = interviews ÷ sent × 100. Only counts `INTERVIEW` status.
- A response rate above 20-30 % is generally good for a focused search.
- If response rate is high but interview rate is low, your CV is working but something is failing
  at the interview stage.

### Exporting data

Click **Export analytics CSV** on the Analytics page.  
A CSV with four sections is downloaded: Source Performance, CV Profile Performance,
Weekly Activity, and Technology Performance.

### Optional AI summary

If **LM Studio** is enabled in Settings, an **"Generate AI summary"** button appears.  
It sends only aggregated statistics (counts and percentages) — no email content, company names,
or personal details — to the local LLM and displays a short coaching summary.

### Privacy

All analytics run entirely locally. No data is sent to any external service.
The Analytics page reads from `data/applications.json`, `data/offers.json`, and `data/interviews.json`.

---

## Job Search Collection and Rapid Apply Queue

The **Job Search** page automates finding job offers from public job boards and builds a
prioritised apply queue so you can act on the best leads quickly.

### How it works

1. **Configure criteria** — set role keywords, technologies, locations, salary minimum, and which
   boards to search (JustJoinIT, RocketJobs, NoFluffJobs, Pracuj.pl).
2. **Collect offers** — the app fetches the public JSON APIs of each enabled board, filters by your
   criteria, deduplicates, imports, and scores each offer automatically.
3. **Build the queue** — offers with `decision = apply` or `maybe` that haven't been applied to yet
   are assembled into a prioritised rapid-apply queue (sorted by score + source bonus).
4. **Work through the queue** — open each offer, use the Bookmarklet to fill the form, then mark it
   as Sent in the queue.

### Important constraints

| Rule | Detail |
|---|---|
| **No auto-submit ever** | The form filler always stops before the Submit button. You must click it yourself. |
| **No login / no CAPTCHA bypass** | All collectors use only public unauthenticated endpoints. |
| **LinkedIn is stub-only** | LinkedIn requires authentication. Use the Bookmarklet or manual URL import. |
| **Rate limiting** | `request_delay_seconds` (default 1.5 s) adds a polite pause between paginated requests. |

### CLI commands

```bash
# Collect from all enabled sources using your settings.yaml criteria
cv-sender collect-jobs

# Collect from specific sources only
cv-sender collect-jobs --source justjoin --source rocketjobs

# Use the emergency React/Frontend preset (ignores settings.yaml keywords)
cv-sender collect-jobs --emergency

# Build / refresh the apply queue from scored offers
cv-sender build-queue

# Fill the next queued offer form (never auto-submits)
cv-sender fill-next
```

### Emergency React mode

Toggle **Emergency React/Frontend mode** on the Job Search page (or pass `--emergency` to the CLI)
to instantly pre-fill criteria for React / TypeScript / Next.js roles without touching the config file.

### Configuration

All options live under `job_search:` in `config/settings.yaml`:

```yaml
job_search:
  enabled: false          # master switch
  keywords: [React Developer, Frontend Developer]
  technologies: [React, TypeScript, Next.js]
  locations: [Remote, Poland]
  seniority: [Mid, Senior]
  contract_types: [B2B, UoP]
  min_salary_b2b: 0       # 0 = no minimum
  require_salary: false
  max_offers_per_source: 30
  max_total_offers: 100
  exclude_keywords: [Angular, PHP, WordPress]
  request_delay_seconds: 1.5
  sources:
    justjoin:    {enabled: true}
    rocketjobs:  {enabled: true}
    nofluffjobs: {enabled: true}
    pracuj:      {enabled: true}
    linkedin:    {enabled: false}   # stub only
```

### Data files

| File | Contents |
|---|---|
| `data/apply_queue.json` | The current rapid-apply queue (auto-generated, gitignored) |

---

## Rapid Apply Session

The **Rapid Apply** page provides a focused, one-at-a-time flow for working through the apply
queue with minimal friction. Every action is deliberately manual — the tool fills forms for you
but never submits them.

### Starting a session

1. Navigate to **Rapid Apply** in the sidebar.
2. If the queue is empty, click **Build Queue from existing offers** or use
   **Run Emergency React Search + Build Queue** to collect fresh React/Frontend leads first.
3. The highest-priority queued item is presented automatically.

### Session workflow

| Step | Action | Button |
|---|---|---|
| 1 | Fill the application form (browser opens, fields are auto-populated) | `1️⃣ Fill this application` |
| 2 | Review the filled form manually in the browser, then submit it yourself | *(manual click)* |
| 3 | Return to the app and confirm the application was sent | `2️⃣ Mark as sent` |
| Skip | Pass on this offer with an optional reason | `3️⃣ Skip` |
| Retry | Re-run the filler after a failure | `🔄 Retry fill` |
| Retry Generic | Use the site-agnostic filler as fallback | `🔁 Retry (Generic)` |
| Next | Move to the next item without changing status | `⏭️ Next` |
| Open offer | Open the original posting in a new tab | `🌐 Open offer` |

**Keyboard hints (shown on screen):** `1 Fill · 2 Mark sent · 3 Skip`

### Safety invariants

| Rule | Detail |
|---|---|
| **No auto-submit ever** | `fill_application_form` is always called with `auto_submit=False`. |
| **Failed items stay retryable** | A fill failure sets status `FAILED`, not a terminal state. You can retry or use the generic filler. |
| **No CAPTCHA bypass** | The filler fills fields only; login walls and CAPTCHAs require manual handling. |

### Sidebar filters

Use the session filters sidebar to narrow the queue:

- **Min score** — hide offers below a score threshold
- **Source** — show only offers from a specific job board
- **Exclude failed items** — hide previously failed fill attempts

### Skip reasons

When skipping you can record a reason: `low salary`, `poor fit`, `duplicate`, `login required`,
`broken form`, `not interested`, `other`. The reason is stored on the queue item and appended as
an event to the linked application.

### Stopping a session

Click **🛑 Stop session** to reset all in-memory session state. The queue is preserved; you can
resume any time.

---

## Apply Campaigns

An **Apply Campaign** is a named, time-boxed goal — for example *"React Frontend Sprint — send 25
applications today"*. Campaigns track progress across multiple Rapid Apply sessions and warn you
when the queue is running low.

### Creating a campaign

Navigate to **Campaigns → Create Campaign**.

| Field | Description |
|---|---|
| Campaign name | Free text label (e.g. "React Sprint 2026-05-27") |
| Target | Number of applications to send (default: 25) |
| Target date | Deadline (default: today) |
| Goal type | `applications_sent` / `interviews` / `follow_ups` / `mixed` |
| Keywords | Job title search terms, one per line |
| Technologies | Tech stack filter, one per line |
| Locations | Location filter, one per line |
| Sources | Which job boards to collect from |
| Min score | Minimum offer score to include (0 = no filter) |
| Min salary B2B | Salary floor (0 = no filter) |
| Include follow-ups | Whether to track follow-up tasks as campaign activities |

**React Emergency Sprint preset**: click the **React Emergency Sprint** button to populate all
fields with sensible defaults for a React/Frontend job hunt:

```
name:         React Frontend Sprint
target:       25
keywords:     React Developer, Frontend Developer, Frontend Engineer
technologies: React, TypeScript, Next.js
sources:      JustJoinIT, RocketJobs, NoFluffJobs, Pracuj
min_score:    60
```

### Collecting offers into a campaign

1. On the **Active Campaigns** tab, find your campaign.
2. Click **Collect more offers** to run the search using the campaign criteria.
3. Click **Build/rebuild queue** to score new offers and attach matching queue items
   to the campaign automatically.

Alternatively, collect offers from the **Job Search** page and then rebuild the queue from the
Campaigns dashboard — unattached items matching the campaign's sources and score threshold are
attached automatically.

### Processing a campaign in Rapid Apply

1. Click **Start Rapid Apply** on the campaign card.  This sets the campaign as the active session
   context and navigates you to the Rapid Apply page.
2. The page shows a **campaign progress banner** at the top with target, sent, remaining, and a
   progress bar.
3. Only queue items assigned to the campaign are shown — the session is narrowed to campaign items.
4. Fill → review → submit manually → **Mark as sent**.  Every sent/skipped action records a
   campaign activity and updates the campaign counters.
5. When the sent count reaches the target, the campaign is automatically marked **Completed** and
   you see a "Target reached!" message.

### How progress is counted

| Counter | What increments it |
|---|---|
| Sent | Each time you click "Mark as sent" inside the campaign session |
| Filled | Each fill action that succeeds (FILLED or PARTIAL) |
| Skipped | Each skip action |
| Failed | Each fill that fails |
| Follow-ups | Manually recorded follow-up activities (if `include_follow_ups` is on) |

Progress % = `sent / target × 100` (capped at 100%).

A **queue shortage warning** appears when `remaining > queued_available` — i.e. you do not have
enough queued items to reach your target without collecting more offers.

### Safety note

The campaign mode does **not** auto-submit any application form.  The flow is always:

1. Tool fills the form (Playwright, no submit)
2. You review the pre-filled form in the browser
3. You click Submit yourself
4. You return to the app and click **Mark as sent**

### Campaign statuses

| Status | Meaning |
|---|---|
| active | Being worked on |
| paused | Temporarily stopped; resume from dashboard |
| completed | Target reached or manually marked complete |
| archived | Dismissed, no longer shown in active list |

### Data files

| File | Contents |
|---|---|
| `data/campaigns.json` | Campaign definitions |
| `data/campaign_activities.json` | Per-event activity log |

---

## Collector Diagnostics

Every time you collect offers from job boards (via the **Job Search** page or a Campaign), the app
produces a **Collection Diagnostics** report that explains exactly what was found, what was
imported, and why each offer was accepted, rejected, or flagged.

### Where to view the report

- **Job Search page** — a diagnostics panel appears immediately after collection, showing a
  source-by-source summary, suggestions for loosening filters, and a collapsible table of all
  rejected offers.
- **Campaigns → Collection Diagnostics tab** — shows the most recent diagnostics report in the
  same format, accessible at any time after collection.

### Source summary table

| Column | Meaning |
|---|---|
| Source | Job board name |
| Status | `ok` / `failed` — whether the collector returned results or threw an error |
| Found | Total offers returned by the board |
| Accepted | Offers that passed all filters and were imported |
| Duplicates | Offers already in storage (same normalised URL) |
| Rejected | Offers that failed one or more filter criteria |
| Duration | Time taken to collect from this source (seconds) |

### Why offers are rejected — reason codes

| Code | Meaning |
|---|---|
| `duplicate_url` | Offer URL already exists in `data/offers.json` |
| `already_applied` | You already have an application for this offer |
| `missing_salary` | Offer has no salary data and `require_salary` is `true` |
| `salary_below_minimum` | Advertised salary is below `min_salary_b2b` |
| `excluded_keyword` | Offer title or description contains one of your `exclude_keywords` |
| `no_required_keyword_match` | None of your `keywords` appear in the offer title or description |
| `no_required_technology_match` | Offer's tech stack doesn't overlap with your `technologies` list |
| `wrong_location` | Offer location doesn't match your `locations` filter |
| `wrong_seniority` | Offer seniority level doesn't match your filter |
| `wrong_contract_type` | Offer contract type doesn't match your `contract_types` filter |
| `low_score` | Offer scored below the campaign `min_score` threshold |
| `protected_page` | Collector detected a login wall or bot-protection page |
| `login_required` | Board requires authentication to access this listing |
| `captcha` | CAPTCHA challenge detected; automated collection cannot proceed |
| `import_failed` | Offer passed filters but storage write failed |
| `unknown` | Unexpected error during evaluation |

### Suggestions for loosening filters

After collection, the diagnostics engine analyses the rejection reasons and generates plain-English
suggestions such as:

- _"5 offers were rejected for salary_below_minimum. Consider lowering min_salary_b2b."_
- _"3 offers had no keyword match. Consider adding 'Fullstack Developer' to your keywords."_
- _"JustJoinIT returned 0 offers — the source may be temporarily unavailable."_

Suggestions are shown as an expandable list above the rejected-offers table.

### "Import anyway" — force-importing a rejected offer

In the rejected offers table, each row has an **Import anyway** button. Clicking it calls
`force_import_collected_offer`, which:

1. Creates an `Offer` from the raw collected data.
2. Writes it to `data/offers.json` (skips if the URL already exists).
3. Optionally runs auto-scoring.
4. Optionally adds the offer to the apply queue.

Use this when you want an offer that was filtered out for a threshold reason (e.g. salary slightly
below your minimum) without permanently changing your criteria settings.

> The offer is marked `manually_overridden: true` in the diagnostics log so you can track which
> imports bypassed filters.

### Data file

| File | Contents |
|---|---|
| `data/collection_diagnostics.json` | Rolling log of the last 20 collection runs, keyed by `run_id` |

Each run contains:
- `run_id` — UUID
- `started_at` / `finished_at` — ISO timestamps
- `criteria` — the search criteria snapshot used for this run
- `source_summaries` — per-source statistics
- `decisions` — one `CollectedOfferDecision` record per offer
- `global_warnings` — any cross-source warnings
- `suggestions` — plain-English filter-loosening suggestions

The file keeps at most **20 runs**; older runs are evicted automatically.

### Limitations

- Collectors only use **public unauthenticated APIs** — no login, no CAPTCHA bypass.
- LinkedIn is a stub only and is never collected automatically.
- The diagnostics report reflects the state at collection time; if you later change your criteria,
  historical reports are not retroactively updated.
- `force_import` does not re-run extraction — it uses the raw data already collected.

---

## Data Cleanup

During development and testing it is common to accumulate fake or unwanted offers.
The **Data Cleanup** page (sidebar → _Data Cleanup_) and the `cv-sender cleanup` CLI commands
let you delete offers safely.

### Bulk delete selected offers (UI)

1. Open **Data Cleanup** in the sidebar.
2. In **Section 1**, click the trash icon on any row in the offers table to mark it for deletion.
3. Tick the confirmation checkbox.
4. Click **Delete selected offers**.

A backup is created automatically before deletion (unless you uncheck the option).

### Delete by filter (UI)

1. Open **Data Cleanup → Section 2**.
2. Set one or more filters (source, decision, score, text search, created-before date, dev/test only).
3. Click **Preview matching offers** to see what would be deleted.
4. Tick the confirmation checkbox, then click **Delete matching offers**.

### Danger zone (UI)

Expand **Section 3** to access:

| Action | Typed confirmation required |
|---|---|
| Delete ALL offers | `DELETE OFFERS` |
| Clear apply queue | checkbox only |
| Clear collection diagnostics | checkbox only |
| Clear debug data | checkbox only |
| Full dev cleanup | `DEV CLEANUP` |

**Full dev cleanup** deletes dev/test offers, clears the apply queue, and clears collection
diagnostics in one shot.  Applications are **not** deleted unless you explicitly enable the
_Delete related applications_ option.

### Related cleanup options

Every delete action exposes four independent toggles:

| Option | Default | Description |
|---|---|---|
| Delete related queue items | **on** | Remove apply-queue entries for deleted offers |
| Delete related quality reports | **on** | Detach campaign-activity references |
| Delete related applications | **off** | Delete application records linked to deleted offers |
| Delete debug runs | **off** | Remove form-filling debug run directories |

> ⚠ **Delete related applications** permanently removes sent application history.
> Leave this off unless you are certain.

### CLI commands

```bash
# Preview dev/test offers (no deletion)
cv-sender cleanup offers --dev-only

# Delete dev/test offers (with confirmation)
cv-sender cleanup offers --dev-only --yes

# Delete offers from a specific source
cv-sender cleanup offers --source rocketjobs --yes

# Delete all offers
cv-sender cleanup offers --all --yes

# Clear the apply queue
cv-sender cleanup queue --yes

# Full dev cleanup (offers + queue + diagnostics)
cv-sender cleanup dev-data --yes
```

All destructive CLI commands require `--yes` or they print a preview and exit without deleting.

### How backups work

Before every bulk or all-delete operation, the app copies data files to:

```
data/backups/YYYYMMDD_HHMMSS_<reason>/
```

Files backed up: `offers.json`, `applications.json`, `apply_queue.json`,
`campaigns.json`, `campaign_activities.json`, `collection_diagnostics.json`.

A `metadata.json` file records the timestamp, reason, and operation name.

**To restore from a backup**, copy the files you need back to `data/`:

```bash
copy data\backups\20260527_120000_bulk_delete\offers.json data\offers.json
```

### Dev / test offer detection

The heuristic used by `--dev-only` and the UI filter flags an offer as dev/test if:

- `source` is `"dev"`, `"test"`, or `"example"`, **or**
- title / company contains a whole-word match for `test`, `dev`, `example`, `demo`, `fake`, `dummy`, **or**
- URL contains `example.com`, `localhost`, `127.0.0.1`, or `/test`.

Real offer titles like "Senior React **Developer**" do **not** match because "dev" is matched as a
whole word only.

### Backup failure safety

If backup creation fails (e.g. disk full), the delete operation is **aborted** and an error is
returned.  Pass `--no-backup` (CLI) or uncheck _Create backup before deleting_ (UI) only if you
are sure you do not need a snapshot.
