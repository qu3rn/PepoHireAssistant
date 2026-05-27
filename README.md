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
