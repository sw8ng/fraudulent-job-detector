# scam-job-posting-detector

CSC 699 independent study project about detecting fraudulent job postings using an LLM-based agent.

The project uses BeautifulSoup and a local Qwen model through Ollama to turn saved job posting HTML into structured JSON.

## How to run

Install the dependencies:

```powershell
python -m pip install -r requirements.txt
```

Install and start [Ollama](https://ollama.com/), then download the model once:

```powershell
ollama pull qwen3.5:9b
```

Open a job posting in your browser, expand the full description, and save it as a `.html` file in `data/input/`.

Run:

```powershell
python scripts/extract_job.py
```

Find the structured JSON files in `data/output/`. Files with matching JSON outputs
are skipped before calling Qwen. To re-extract a posting, delete its matching JSON
and run again. Existing outputs are skipped even if the input HTML has changed.

Extraction uses three Qwen passes: general fields, education/experience, and the
full description. Field definitions map headings such as `Duties` to
responsibilities and `Skills` to qualifications. Passes use the original posting
independently, so copying the description does not replace extracting its fields.
Information may appear in multiple fields; genuinely missing facts stay `null`.
This takes longer than a single model call, and model output still needs review.


To preview the prepared text without calling the LLM or saving files:

```powershell
python scripts/prepare_jobs.py
```
