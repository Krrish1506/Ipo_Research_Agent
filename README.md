# Agentic AI IPO Analyst

An automated, intelligent financial analyst that scrapes IPO data, enriches missing details using a self-healing AI pipeline, orchestrates data in Google Sheets, and generates professional Wealth Manager investment reports.

## 🚀 Overview
The **IPO Research Agent (Version 7.0)** is a full-stack, automated ETL solution designed for high-net-worth individual (HNI) wealth management. It tracks upcoming, open, and recently closed IPOs. It utilizes a FastAPI backend to orchestrate web scraping, context-aware data enrichment via Google Gemini (with a resilient 5-model fallback), and automated HTML reporting.

## 🌟 Key Features
* **Automated Web Scraping**: Periodically fetches raw calendar data from IPOWatch.
* **Self-Healing Data Pipeline**: Automatically identifies missing data (like industry or notes), adds them to a "Missing Details" task queue, and batch-enriches them in the next cycle using AI.
* **Smart Deduplication & Merge Logic**: Merges newly enriched data with existing spreadsheet records based on "completeness scores" without overwriting valid data like prices and dates with empty strings.
* **Rate-Limit Resilience**: Implements a 5-model Gemini rotation (Primary: `gemini-3.0-flash`) to gracefully bypass free-tier API quotas, alongside batch operations to respect Google Sheets write limits.
* **Wealth Manager Persona**: Analyzes data and calculates "Confidence Scores" and listing gains. Provides explicit **FULL APPLY**, **MODERATE**, or **AVOID** decisions based on risk and demand (GMP%).
* **Professional HTML Dashboard**: Generates an interactive, token-optimized report of the top 15 actionable (open/upcoming) IPOs.

## 🏗️ Architecture & Data Pipeline
The project runs via a separated Extract-Transform-Load (ETL) pipeline using a **Three-Tab Google Sheets Architecture** (`recent data`, `ipolist`, `Missing Details`).

1. **Phase 1: Process Task Queue (`main.py`)**: Reads the "Missing Details" queue, batch-calls Gemini to find missing fields, merges them perfectly into existing records, and clears the queue.
2. **Phase 2: Scrape & Extract (`main.py`)**: Scrapes the web for new IPOs, extracts JSON using AI, and dumps raw data into the `recent data` tab.
3. **Phase 3: Organize & Heal (`organizer.py`)**: Merges `recent data` into the main database (`ipolist`), dedupes records, populates the `Missing Details` queue for the next run, and sorts by status and date.

## 🛠️ Technology Stack
* **Language:** Python 3.x
* **Framework:** FastAPI / Uvicorn
* **AI Engine:** Google Gemini (5-Model Fallback: `3.0-flash`, `2.5-flash`, `3.1-flash-lite`, `2.5-flash-lite`, `1.5-flash`)
* **Data Extraction:** BeautifulSoup4 & Requests
* **Database Integration:** Google Sheets API (`gspread`, `google-auth`)
* **Deployment:** Render.com
* **Automation:** Activepieces (Open Source Orchestration)

## ⚙️ Setup & Installation

### Local Development
1. **Clone the repo & setup virtual environment:**
   ```bash
   python -m venv venv
   venv\Scripts\activate  # Windows
   pip install -r requirements.txt
   ```
2. **Environment Variables**: Create a `.env` file:
   ```env
   GOOGLE_API_KEY=your_gemini_api_key
   GOOGLE_SHEET_URL=https://docs.google.com/spreadsheets/d/[sheet_id]/edit
   ```
3. **Google Sheets Authentication**: Place your Service Account `credentials.json` in the root folder. Share your Google Sheet with the client email found in your `credentials.json`.
4. **Run Locally**:
   ```bash
   python main.py
   # Access http://localhost:8000
   ```

### Production Deployment (Render)
The repository is production-ready for Render.com:
* Set the environment variables in the Render dashboard: `GOOGLE_API_KEY`, `GOOGLE_SHEET_URL`, and `GOOGLE_CREDENTIALS_JSON` (paste the entire contents of your `credentials.json` as a single JSON string).
* **Build Command**: `pip install -r requirements.txt`
* **Start Command**: `uvicorn main:app --host 0.0.0.0 --port 8000`

## 🔄 Workflow & Orchestration
This repository is designed to be fully automated via **Activepieces**:
1. Activepieces hits the `/update_sheet` endpoint on a schedule. This triggers the master pipeline (`main.py` + `organizer.py`) in the background.
2. Activepieces waits ~4 minutes for the pipeline to finish.
3. It then hits the `/report` endpoint to fetch the generated HTML financial dashboard.
4. Activepieces emails the beautiful HTML report directly to the client.

## ⚖️ Disclaimer
This project is AI-generated for educational purposes only. It does not constitute financial advice. Please consult a certified financial advisor before investing.
