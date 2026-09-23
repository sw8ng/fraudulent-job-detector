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

Find the structured JSON files in `data/output/`. Running again replaces matching outputs.

To preview the prepared text without calling the LLM or saving files:

```powershell
python scripts/prepare_jobs.py
```
