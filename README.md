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
│   ├── services.py     # Business-logic service layer (used by UI & CLI)
│   ├── ui.py           # Streamlit web UI
│   └── portals/        # Portal-specific fillers
│       ├── base.py
│       ├── generic.py
│       ├── rocketjobs.py
│       ├── pracuj.py
│       └── linkedin.py
├── config/
│   ├── profile.example.yaml
│   └── settings.example.yaml
├── data/
│   ├── offers.json          (created by the app)
│   └── applications.json    (created by the app)
└── tests/
    ├── test_config.py
    ├── test_models.py
    ├── test_scorer.py
    ├── test_services.py
    └── test_storage.py
```

---

## Roadmap

- [ ] Automated offer scraping from job boards
- [ ] Cover-letter generation via LLM
- [ ] Multi-step form navigation (LinkedIn Easy Apply wizard)
- [ ] Email notification on application status changes
- [x] Web UI dashboard
- [x] Manual offer entry in UI
- [x] End-to-end MVP flow (add → score → fill → track)
