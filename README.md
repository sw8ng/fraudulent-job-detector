# scam-job-posting-detector
CSC 699 Independent Study Project: Phishing Job Offering Detection Using LLM-Based Agent

## Extract Job

The script reads locally saved HTML files and uses BeautifulSoup to extract text
for your own LLM. It accepts pages from any website, including LinkedIn, Indeed,
ZipRecruiter, and company career sites. No browser automation or API key is needed.

Install the required package:

```powershell
python -m pip install -r requirements.txt
```

Open the job posting in your browser, expand the full description, and wait for
the content to load. Save each page as an HTML file (`.html` or `.htm`) directly
inside the `input` folder beside the script, then run:

```powershell
python .\extract_job.py
```

There are no prompts. Each HTML file produces a JSON file in `output`, for example
`input/job.html` becomes `output/job.html.json`. Both folders are resolved relative
to the script, regardless of the directory where you run it. Subfolders (including
browser-created asset folders) are skipped. Rerunning replaces matching outputs;
original HTML files are left unchanged. A failed file is reported and processing
continues with the other files; the script exits with a nonzero status if any fail.

Each JSON file contains text for your LLM to fit to your job schema:

```json
{
  "source_file": "job.html",
  "raw_posting_text": "Software Engineer\nCompany name\nJob description..."
}
```

The function `extract_job(html_path)` returns the page text as a string. It does
not perform LLM extraction; the script packages its text into JSON. Text may include navigation and other
page content; it is not restricted to the job description. Link destinations
and HTML attributes are not included by `get_text()`; retain the original HTML
if your LLM needs those details.

Only content present in the saved HTML can be extracted. The script does not run
JavaScript, load companion assets or iframe pages, or retrieve missing content.
Check the output to ensure the description was saved, especially for dynamic sites.
