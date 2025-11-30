import os
import json
import requests
import re
import uvicorn
from fastapi import FastAPI
from fastapi.responses import HTMLResponse
from dotenv import load_dotenv
import google.generativeai as genai

# 1. Load Secrets
load_dotenv()
GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY")
IPO_SHEET_API = os.getenv("IPO_SHEET_API")

# 2. Setup Gemini
if not GOOGLE_API_KEY:
    raise ValueError("GOOGLE_API_KEY not found in .env file")

genai.configure(api_key=GOOGLE_API_KEY)
model = genai.GenerativeModel('gemini-2.5-flash') 

app = FastAPI()

# --- HELPER FUNCTIONS ---

def get_sheet_data():
    """Reads data from your Google Sheet via Sheet.best"""
    if not IPO_SHEET_API:
        return {"error": "IPO_SHEET_API not found in .env file"}
    try:
        response = requests.get(IPO_SHEET_API)
        return response.json()
    except Exception as e:
        return {"error": str(e)}

def clean_and_parse_json(text):
    """Smartly extracts JSON from Gemini's response, ignoring extra chatter."""
    try:
        # 1. Try standard parsing first
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    try:
        # 2. If that fails, look for the first '[' and last ']'
        # This fixes issues where Gemini adds text before or after the JSON
        match = re.search(r'\[.*\]', text, re.DOTALL)
        if match:
            json_str = match.group(0)
            return json.loads(json_str)
    except Exception:
        pass
        
    # 3. Last resort cleanup (remove markdown tags manually)
    try:
        clean_text = text.replace("```json", "").replace("```", "").strip()
        return json.loads(clean_text)
    except Exception as e:
        return None

def analyze_ipo(ipo_data):
    """Sends IPO data to Gemini for a decision"""
    data_str = json.dumps(ipo_data)
    prompt = f"""
    You are a strict Financial Analyst AI. 
    Here is the data for upcoming IPOs: {data_str}
    
    Instructions:
    1. Analyze GMP, Price Band, and Subscription data.
    2. Decide if I should APPLY, AVOID, or WATCH.
    3. Give a Score (0-100).
    
    Output Format:
    Return ONLY a raw JSON list. 
    Do not use Markdown. Do not add explanations outside the JSON.
    Example:
    [
      {{
        "company": "Name",
        "decision": "APPLY",
        "score": 90,
        "reason": "High GMP"
      }}
    ]
    """
    try:
        response = model.generate_content(prompt)
        text_response = response.text
        
        # Use our smart cleaner
        parsed_data = clean_and_parse_json(text_response)
        
        if parsed_data:
            return parsed_data
        else:
            return [{"company": "Error", "decision": "ERROR", "score": 0, "reason": "Could not parse Gemini response: " + text_response[:100]}]
            
    except Exception as e:
        return [{"company": "Error", "decision": "ERROR", "score": 0, "reason": str(e)}]

def generate_html_report(analysis_results):
    """Converts JSON analysis into a Beautiful HTML Page with PDF Download"""
    
    html_rows = ""
    for item in analysis_results:
        # Color coding
        color = "#e74c3c" # Red (Avoid) or Error
        bg_color = "#fdedec"
        decision = item.get('decision', 'N/A')
        
        if decision == "APPLY":
            color = "#27ae60" # Green
            bg_color = "#eafaf1"
        elif decision == "WATCH":
            color = "#f39c12" # Orange
            bg_color = "#fef9e7"
            
        html_rows += f"""
        <div class="ipo-card" style="border-left: 5px solid {color}; background-color: {bg_color}; padding: 15px; margin-bottom: 15px; border-radius: 5px; box-shadow: 0 2px 4px rgba(0,0,0,0.1);">
            <h3 style="margin: 0; color: #2c3e50;">{item.get('company', 'Unknown')}</h3>
            <p style="margin: 5px 0;">
                <strong style="color: {color}; font-size: 16px;">{decision}</strong> 
                <span style="color: #555; font-weight: bold;">(Score: {item.get('score', 0)}/100)</span>
            </p>
            <p style="margin: 5px 0; font-size: 14px; color: #555; line-height: 1.4;">{item.get('reason', '')}</p>
        </div>
        """

    full_html = f"""
    <html>
    <head>
        <title>Daily IPO Report</title>
        <script src="https://cdnjs.cloudflare.com/ajax/libs/html2pdf.js/0.10.1/html2pdf.bundle.min.js"></script>
        <script>
            function downloadPDF() {{
                const element = document.getElementById('report-content');
                const opt = {{
                    margin:       0.5,
                    filename:     'IPO_Analysis_Report.pdf',
                    image:        {{ type: 'jpeg', quality: 0.98 }},
                    html2canvas:  {{ scale: 2 }},
                    jsPDF:        {{ unit: 'in', format: 'letter', orientation: 'portrait' }}
                }};
                html2pdf().set(opt).from(element).save();
            }}
        </script>
    </head>
    <body style="font-family: Arial, sans-serif; max-width: 700px; margin: auto; padding: 20px; background-color: #f4f6f7;">
        
        <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 20px;">
            <h2 style="color: #2c3e50; margin: 0;"></h2> 
            <button onclick="downloadPDF()" style="background-color: #e74c3c; color: white; border: none; padding: 12px 20px; border-radius: 5px; cursor: pointer; font-weight: bold; font-size: 14px; box-shadow: 0 2px 5px rgba(0,0,0,0.2);">
                📄 Download PDF
            </button>
        </div>

        <div id="report-content" style="background-color: white; padding: 30px; border-radius: 10px;">
            <h2 style="color: #2c3e50; text-align: center; border-bottom: 2px solid #ecf0f1; padding-bottom: 15px; margin-top: 0;">🚀 Daily IPO Agent Report</h2>
            <p style="text-align: center; color: #7f8c8d; font-size: 12px; margin-bottom: 25px;">
                Strict Financial Analysis | Powered by Gemini AI
            </p>
            
            {html_rows}
            
            <p style="font-size: 10px; color: #bdc3c7; text-align: center; margin-top: 30px; border-top: 1px solid #ecf0f1; padding-top: 10px;">
                Disclaimer: This report is AI-generated for informational purposes only. Not financial advice.
            </p>
        </div>
    </body>
    </html>
    """
    return full_html

# --- API ENDPOINTS ---

@app.get("/")
def home():
    return {"message": "IPO Analyst AI is Ready. Go to /report to see the PDF view."}

@app.get("/analyze")
def run_analysis_json():
    raw_data = get_sheet_data()
    results = analyze_ipo(raw_data)
    return results

@app.get("/report", response_class=HTMLResponse)
def run_analysis_html():
    raw_data = get_sheet_data()
    results = analyze_ipo(raw_data)
    html_content = generate_html_report(results)
    return html_content

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000)