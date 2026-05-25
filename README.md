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
```

---

## File Structure

```
cv-sender/
├── src/cv_sender/
│   ├── cli.py          # CLI commands
│   ├── config.py       # YAML config loading
│   ├── models.py       # Pydantic models
│   ├── storage.py      # JSON file storage
│   ├── scorer.py       # Deterministic + LLM scoring
│   ├── llm.py          # LM Studio integration
│   ├── browser.py      # Playwright session management
│   ├── form_filler.py  # Orchestrates form filling
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
    ├── test_models.py
    ├── test_scorer.py
    └── test_storage.py
```

---

## Roadmap

- [ ] Automated offer scraping from job boards
- [ ] Cover-letter generation via LLM
- [ ] Multi-step form navigation (LinkedIn Easy Apply wizard)
- [ ] Email notification on application status changes
- [ ] Web UI dashboard
