# scam-job-posting-detector

CSC 699 independent study project about detecting fraudulent job postings using an LLM-based agent.

Currently, the project uses BeautifulSoup to extract text from saved job posting HTML files and save it as JSON for later LLM processing.

## How to run

Install the dependency:

```powershell
python -m pip install -r requirements.txt
```

Open a job posting in your browser, expand the full description, and save it as a `.html` file in `data/input/`.

Run:

```powershell
python scripts/prepare_jobs.py
```

Find the prepared text JSON files in `data/output/`.

`scripts/extract_job.py` is the starting point for LLM extraction into the job schema; the LLM is not connected yet.
