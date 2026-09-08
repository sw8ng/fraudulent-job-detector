# scam-job-posting-detector
CSC 699 Independent Study Project: Phishing Job Offering Detection  Using LLM-Based Agent

## Extract Job

The current script extracts information from one manually supplied Indeed or
ZipRecruiter job posting using Firecrawl.

Install the required packages:

```powershell
python -m pip install -r requirements.txt
```

Copy `.env.example` to `.env`, then replace the placeholder with your Firecrawl
API key:

```dotenv
FIRECRAWL_API_KEY=your-firecrawl-api-key
```

Run the script:

```powershell
python .\extract_job.py
```

Paste one supported job URL when prompted. The extracted information is saved
to `extracted_job.json` beside the script.
