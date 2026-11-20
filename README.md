# scam-job-posting-detector

CSC 699 independent study project about detecting fraudulent job postings using an LLM-based agent.

## AI assistance disclosure

I use AI tools to help turn my ideas and vision for this project into working
software. I review AI-assisted contributions and include only code that I
understand and can explain. I take full responsibility for the code I include,
the project's design decisions, testing, limitations, and final results.

Content analysis uses examples to distinguish employer-paid compensation from
applicant payment demands. Tool selection highlights unanswered checks and allows
two initial rounds and one additional opportunity when a successful application
destination still has pending content or association checks. Tools remain
optional; missing employer authentication is reported separately from posting risk.

The project uses BeautifulSoup and a selected local model through Ollama to turn saved job posting HTML into structured JSON.

## Quick workflow commands

Assessments now have a hard **300-second limit**. Comparisons have a **600-second
batch budget**. On expiry, the assessment worker is terminated, completed evidence
is saved in a partial report, and unfinished work is labeled `timed_out` or `not_run`.
No extra model call is made to invent a final answer. Timed-out comparisons are
retained on resume; use `--force` only when you intentionally want to retry them.
Every selected model uses the same limits. A comparison starts a case only when
the full per-assessment allowance remains in the batch budget; otherwise it marks
the case `not_run` rather than shortening its deadline. Set `--max-seconds` and `--max-total-seconds` on
comparison or pipeline commands to change the budgets; single assessments accept
`--max-seconds`. The pipeline also limits extraction and keeps completed outputs.

Tool selection and final assessment omit long URL queries from model-visible text while
keeping exact source URLs for tool execution and final evidence. All advertised
verification tools use stored posting values, so the model can request them with
empty arguments. Final summaries are prompted to use 2-4 sentences and at most
90 words. These shared rules apply to any model selected with `--model`.

Application-page collection reuses the HTML text cleanup used for saved postings.
After URL Inspection records a successful destination, Qwen or Llama may request
its title, text, links and HTTP status as additional evidence. It does not rerun
job-field extraction, submit forms, or assign a fraud verdict. Existing assessment
commands are unchanged; use `--force` to rerun saved assessments with this tool.

To collect a known destination separately, without running a model:

```powershell
python scripts/collect_application_page.py "https://stripe.com/careers/listing/software-engineer-early-career-immediate-start/8212508?gh_src=73vnei" --output local/evaluation/stripe-application-page.json
```

Page evidence is saved in assessment `evidence_sources` and `investigation.tool_calls`.
Collection is optional within this bounded selection workflow. URL Inspection uses
the posting's exact URL without requiring the model to retype it. This version
reads public HTML and embedded JobPosting JSON-LD, with exact-job public API fallback for supported Ashby/Greenhouse URLs. It does not execute JavaScript, click Apply, log in or decode
job-board redirect wrappers. No Indeed-specific handling is integrated. HTTP errors
and missing text are limitations, not fraud evidence. Even successful collection
may contain only boilerplate. Downloads are capped at two megabytes and retained
text at 12,000 characters, with truncation disclosed.

Save your HTML/PDF postings in `data/input` and start Ollama. You can keep each
step separate:

```powershell
python scripts/extract_job.py --model qwen3.5:9b
python scripts/compare_models.py --models qwen3.5:9b llama3.1:8b --input-dir data/output --output local/evaluation/actual-postings-comparison --force
Start-Process "local/evaluation/actual-postings-comparison/comparison.html"
```

Wait for each command to finish before starting the next. Comparison uses the
saved extracted JSON; `--force` requests fresh assessments, not fresh extraction.
`--input-dir` includes every JSON file directly in that folder, so remove stale
outputs if you use that separate command. You can still use `--inputs` for
specific files instead.

Alternatively, run the same steps with one optional command:

```powershell
python scripts/run_pipeline.py --extract-model qwen3.5:9b --models qwen3.5:9b llama3.1:8b --force
```

The shortcut extracts missing outputs, compares only outputs corresponding to
current source files, and opens the visual. It adds no synthetic inputs and
never deletes files. Existing extraction outputs are kept. Add
`--force-extraction` only when you also want to re-extract existing inputs.
Omit `--force` to reuse matching completed assessments, or add `--no-open` to
save the page without opening a browser. Both model choices remain explicit.

## Clear generated outputs

```powershell
python scripts/clear_outputs.py
```

Permanently deletes JSON files under `data/output` and `data/assessments`, including
nested assessment folders. It keeps source inputs, folder structure, `.gitkeep`
files and all `local/evaluation` results. Run extraction again afterward if you
want to assess the original inputs.

## How to run

Install the dependencies:

```powershell
python -m pip install -r requirements.txt
```

Install and start [Ollama](https://ollama.com/), then download the model once:

```powershell
ollama pull qwen3.5:9b
```

Open a job posting in your browser, expand the full description, and save it as
HTML or print it to PDF in `data/input/`. PDFs must contain selectable text;
image-only scans need OCR, which is not included. Embedded PDF links are kept
when available, but a printed PDF may omit the application URL.

Run:

```powershell
python scripts/extract_job.py --model qwen3.5:9b
```

Find the structured JSON files in `data/output/`. Files with matching JSON outputs
use `posting.json` for HTML and `posting.pdf.json` for PDF, so the two formats
do not overwrite or skip each other. Assessment accepts either JSON output.
Files with existing matching outputs
are skipped before calling the selected model. To re-extract postings, add `--force` or remove the matching JSON first. Existing outputs are skipped even if the input HTML has changed.

Extraction uses three model passes: general fields, education/experience, and the
full description. Field definitions map headings such as `Duties` to
responsibilities and `Skills` to qualifications. Passes use the original posting
independently, so copying the description does not replace extracting its fields.
Information may appear in multiple fields; genuinely missing facts stay `null`.
This takes longer than a single model call, and model output still needs review.


To preview the prepared text without calling the LLM or saving files:

```powershell
python scripts/prepare_jobs.py
```

## Investigate an application link

Assessment now begins with a content-analysis pass with the selected local model before company
search or URL checks. It saves `content_analysis` with quoted observations,
unverified claims to check, and limitations. Python excludes statements whose
supporting quotes are absent from the supplied posting text. Quotes can be
checked mechanically; their interpretation still needs review. These initial
observations are supplied to the final assessment and are not a fraud verdict.
Content analysis focuses on the extracted description when present and validates
quotes against the original text, allowing layout whitespace differences. Two
narrow rules mark explicit gift-card payment and bank-password requests as
concerns while retaining the model's original label; negations and warnings
disable these rules. Clear anti-fee/credential warnings are retained as context,
with any corrected model label and interpretation preserved for review.

Assessment also checks up to four HTML pages from the candidate company website
and its careers links for a matching job heading and location. Results distinguish
`matching_posting_found`, `not_found_in_checked_pages`, and `could_not_check`.
This supports association with a candidate site, not employer identity verification.
Pages requiring JavaScript may not be checkable, and location spelling differences
can prevent a match. The selected model selects this tool when association evidence is useful
and a company website and job title are available. Skipped checks are saved as
`not_requested`. The selected model can choose useful checks across two initial
selection rounds, with a third opportunity only for pending application checks
after a successful destination. Each tool executes at most once. Tool requests and
returned evidence are saved in `tool_calls`. Careers links in page navigation
and footers are preserved, and links matching the job title take priority.
Known tracking variants are grouped, and resolved pages are checked once.
If a homepage exposes no careers links, common `/careers` and `/jobs` paths may
be probed, with that limitation recorded. The linked-page crawl is bounded to four HTML pages/eight attempts. If it finds no match, up to two targeted searches can retrieve at most three additional candidates, including job-specific links on a fetched careers index. Search results must stay within the candidate company domain or its recorded linked hiring-platform board. Snippets alone never establish a match.

```powershell
python scripts/check_employer_association.py "https://company.example/careers" "Software Engineer" --location "San Francisco, CA"
```

The final assessment uses an explicit output budget and retries once if truncated,
reusing the gathered evidence. Longer PDFs may use more local memory and time.
Summaries also receive explicitly scoped tool facts. Narrow mechanical checks
detect unobserved redirects, unsupported official-site wording, selected HTTP-status misattributions and overly certain fraud wording. Detected errors get one wording-correction
attempt, then a factual fallback if necessary. A correction keeps the model's risk label; an unsuccessful correction produces an insufficient-evidence fallback. The final prompt receives a separate evidence packet rather than the full tool-selection transcript;
`investigation.summary_review` records the original wording and outcome. This
does not verify every sentence or establish the accuracy of the risk label.

To test this step independently on an extracted job JSON:

```powershell
python scripts/analyze_content.py "data/output/AI Engineer - San Francisco, CA 94107 - Indeed.json" --model qwen3.5:9b
```

The components can also be run separately:

```powershell
python scripts/follow_redirects.py "https://example.com"
python scripts/get_domain_info.py example.com
```

Application URL inspection and redirect tracing do not call RDAP or return
registration dates, domain age or registrar information. Company website domain
investigation is separate: run `get_domain_info.py` with the candidate company
website to retrieve its registration date, age and registrar. The selected model can also
request the separate `get_company_domain_info` tool when a company website was
supplied or selected. Its registration evidence is retained separately from
application inspection and made available to the final assessment. Registration
age alone does not verify a company or establish fraud.

The first external-evidence tool follows HTTP redirects, records the destination
and HTTPS use, and compares registrable domains with the employer website supplied in the posting. It returns evidence,
not a fraud verdict.

Run the tool independently, without the selected model:

```powershell
python scripts/inspect_application_url.py https://example.com --company-website https://www.example.com
```

To let the selected model request the tool and assess an extracted posting:

```powershell
python scripts/assess_job.py "data/output/AI Engineer - San Francisco, CA 94107 - Indeed.json" --model qwen3.5:9b --output data/assessments/indeed-ai-engineer.json
```

The saved result starts with `assessment` (`risk_level` and `summary`),
`claim_checks`, `key_findings`, `unresolved`, `not_checked`, `evidence_sources`, and
`recommended_action`. Claim checks include a status, reason, and evidence IDs.
An unperformed check is `not_checked`; an attempted but inconclusive check is
`unresolved`. Successful checks are `supported` for their specific claims:
recorded company-domain registration, collected application
text, job matches, or destination association. `different_domain` records a completed
comparison with differing domains; it is not automatically fraud evidence.
Company/recruiter identity and website ownership are described under `verification_limits`,
not as checks these tools promise to complete. `candidate_company_website` identifies
the supplied or selected site. The previous broad company/recruiter claim fields have
been replaced with these narrower checks. Existing saved reports keep their original
format; use `--force` when intentionally rerunning an assessment.
The `unresolved` list contains only inconclusive checks; `not_checked` lists
unperformed checks separately. Neither list implies suspicious evidence.

Detailed `content_analysis`, `company_lookup`, `company_website_used`,
`employer_association`, and `tool_calls` are preserved under `investigation`.
Evidence IDs in claims and findings are checked against `evidence_sources`.
The final summary is generated by the selected model and still needs review against the
recorded evidence. Original posting files and old assessments are unchanged;
rerun assessment to generate the new format.
Each executed tool entry records both the model's requested `arguments` and the
`effective_arguments` actually used. If no company website was selected or
supplied, an unselected URL proposed by the selected model is ignored and inspection runs with
`company_website: null`. The application URL must still match the posting exactly.
The selected model is told which tools actually executed and to disclose relevant unperformed
checks. A posting without an application URL can still receive company search
and employer-association evidence; URL inspection is unavailable in that case.

For a professor demo, show the input posting, the tool request, the redirect
evidence, and the final explanation. The Indeed example lacks a
company website, so the new lookup first searches for a likely site for Hiresort.
If no unique candidate is selected, domain comparison remains `null`.

### Automatically find a company website

When `company` is present and `company_website` is missing, assessment searches
for a likely company website before the selected model's URL investigation. This implements
the website discovery step described on slide 9 of the Week 4 deck. It uses the
extracted company name; it does not invent a missing employer name.

Company search uses [DDGS](https://pypi.org/project/ddgs/) with at most five
results and no page scraping. No API key, paid search credits, or paid LLM tokens
are required. DDGS contacts external search engines; searches can be rate-limited
or blocked. Existing company websites skip the search. Install the updated
requirements before running. The lookup does not read `.env` or use Firecrawl.

Test company lookup independently:

```powershell
python scripts/find_company_website.py "Hiresort"
```

You can supply context directly when testing discovery:

```powershell
python scripts/find_company_website.py "Hiresort" --location "San Francisco" --job-title "AI Engineer"
```

Name matching ignores accents and common trailing legal suffixes (Inc., LLC,
Ltd., Corporation), and checks every title segment, including `About Us | Acme`.
It avoids loose substring matching and records `name_match_reasons`. Multiple
pages from the same domain retain their evidence. A contextual follow-up may
also recover an empty initial search or improve a previously nonmatching
candidate. After a follow-up, one name match becomes `candidate_found`, multiple
matches remain `ambiguous`, and zero matches become `no_matching_candidate`.
Only ambiguous candidates go through model selection in assessment;
the standalone command only gathers evidence.

The lookup filters common job boards/directories and compares the company name
with candidate domain names or page titles. One matching domain becomes a
`candidate_found`; conflicting matches remain `ambiguous` with no website
selected by the initial heuristic. If ambiguous, one additional search uses the
available location, job title, or company description. Both queries and candidate
evidence are retained, including candidates absent from the second search.
Assessment then makes one additional call to the selected local model to compare the candidates
with the posting. Only candidates with `name_match: true` are passed to this
selection call and permitted in its output. Other results remain in the saved
search evidence. If none match, selection skips the selected model and returns "No matching
candidate" with an unresolved result. It may select only a matching website or leave the result
unresolved. The saved `company_lookup.selection` contains `selected_url`,
`match_strength`, `reason`, and `remaining_uncertainty`; invalid model selections
remain unresolved. No extra search or selection call is made for a unique match.
Search URLs, titles, and snippets are retained for review. These are
simple heuristics: spelling differences may prevent a match, and an impersonator
can match a company name. `verified` is always `false`; this step does not verify
company identity. A selected candidate is passed to the URL tool and retained
separately from the original extracted posting. Search failures are recorded and
do not prevent assessment or application URL investigation.

Important limits:

- Only HTTP redirects are followed; JavaScript navigation, login flows, and form
  submissions are not executed. An old application link may return an HTTP error.
- Domain comparison uses the bundled Public Suffix List, so `jobs.example.com`
  matches `example.com`. The supplied company website is not independently verified.
- RDAP may fail or omit the registration date. Missing values remain `null`, and
  failures appear in `errors` of the separate company-domain lookup. Its
  `lookup_success` refers to RDAP; application inspection uses only
  `redirect_lookup_success` for the HTTP redirect trace.
- Domain mismatch, recent registration, and HTTPS each require context. Legitimate
  employers use third-party hiring platforms; HTTPS does not establish legitimacy.
- URLs must use HTTP/HTTPS and resolve to public addresses, checked at each hop.
  This is a local research tool, not a hardened public URL-fetching service; DNS
  validation and connection resolution are separate operations.
- Requests have 15-second timeouts, at most ten application redirects, and bounded
  RDAP responses. Checks issue GET requests and do not submit applications.

The integration follows [Ollama's tool-calling workflow](https://docs.ollama.com/capabilities/tool-calling).
Registration requests use the [RDAP.org bootstrap service](https://about.rdap.org/).

Run the offline checks:

```powershell
python -m unittest discover -s tests -v
```

## Company-domain checks

Company-domain checks accept only the selected company website, may execute once,
and share the bounded selection workflow. Rejected or unresolved company
candidates cannot be investigated through this tool. Registration lookup failures
remain limitations. Careers evidence records per-URL HTTP status so errors on
`/careers`, `/jobs` or an application endpoint are distinguished from a successfully
read company page. Summary checks cover specific contradictions; they do not
validate every sentence or the model's risk decision.

## Select a model and compare performance

Every extraction, content-analysis and assessment command requires `--model`.
There is no default model. Use an installed Ollama tag, for example:

```powershell
ollama pull llama3.1:8b
python scripts/assess_job.py "data/output/your-posting.json" --model llama3.1:8b --output local/llama-assessment.json
python scripts/assess_job.py "data/output/your-posting.json" --model qwen3.5:9b --output local/qwen-assessment.json
```

Compare only the posting files you supply:

```powershell
python scripts/compare_models.py --models qwen3.5:9b llama3.1:8b --inputs "data/output/AI Engineer - San Francisco, CA 94107 - Indeed.json" --output local/evaluation/actual-comparison
```

Open the visuals:

```powershell
Start-Process "local/evaluation/actual-comparison/comparison.html"
```

No synthetic examples are added. Add more file paths after `--inputs`, or use
`--input-dir data/output` instead to collect the folder automatically. The page includes model/case filters, timing bars, completion and
failure status, tool usage and expandable responses. Raw/corrected observations remain in saved JSON.
It displays saved results; the filters never run a model.

**Repeat the same command to continue.** Matching completed results are checked
from local files before contacting Ollama or running verification tools. They are
skipped and marked as saved results in the visual. Failed or missing assessments
are retried. Both the posting data and model must match; a missing or damaged
report cannot be reused. Older comparison folders can be resumed using their
saved input snapshots. Existing report files are preserved, and the previous
experiment is backed up when a new selection or fresh work replaces it.

**Add `--force` for a fresh run.** Use it when you want fresh timing or external
evidence, or after changing prompts, tool behavior, settings or model weights.
Saved measurements retain their original timings; they are not new speed tests.
Use a different output folder to keep separate experiments. `--repeats 3` requests
three distinct measurements per input/model; completed repetitions are still
reused unless `--force` is present.

Standalone assessments also skip an existing `--output` when its input fingerprint
and selected model match. An older standalone report without that fingerprint,
or an output for a different input/model, stops immediately with instructions
instead of silently running or overwriting it. Use another output path or
`--force`. Without `--output`, an assessment has no saved file to reuse.

Extraction already checks for existing outputs before parsing or calling the
model. Use `--force` to re-extract changed source files, and separate output
folders when comparing extraction models.

Runs are sequential. Network conditions, model loading and caching affect timings.
Real postings have no independently established authenticity labels. This
comparison measures assessment on common extracted JSON, not extraction quality.

Current context sizes stay at 16K for content/extraction and 32K for planning
and final assessment. Model compatibility, memory use and JSON/tool-call behavior
still need evaluation; selecting a model does not guarantee equal results.
For separate extraction experiments, avoid reusing existing outputs:

```powershell
python scripts/extract_job.py --model llama3.1:8b --output-dir local/extraction/llama
```

Content analysis allows up to 3,072 output tokens for either model, with the existing
16K input context. More output room may reduce truncation but does not fix model
repetition. Tool counts in comparisons describe behavior, not a quality score;
skipping optional tools is not automatically incorrect. Use `--force` to measure
the new setting rather than reuse an older assessment.

Content analysis returns up to six observations and six verification claims, with
up to four model limitations. The response is bounded during generation to prevent
unlimited observation lists. Exact duplicate items are omitted from processed
evidence while the original model response is retained. Reaching the item limit
is recorded as a limitation; this does not establish that the model interpreted
the posting correctly. Use `--force` to compare fresh runs after this change.

The comparison visual uses a black theme with white IBM Plex Mono text. Font
files and their license are included locally; the font is embedded in generated
HTML so the page works offline.

Click **Inspect** to expand an assessment across the full table width. Long
content flows into two columns on wide screens and one column on smaller screens.

The timing chart uses one shared legend for individual model stages and verification tools. Measured calls follow execution rounds, placing requested tools before final assessment. Unmeasured time is labeled separately because its position was not recorded. Hover over a segment to see its call name and measured seconds. Unmeasured work remains separate; saved company-search time is not estimated.


Employer Association records discovered careers/job links with their source page. An exact observed application destination linked through that path can support destination association, including external hiring platforms. Merely sharing the hiring platform domain or returning HTTP 200 is insufficient.


## Workflow and evidence evaluation

The existing comparison dashboard now displays separate efficiency and workflow/evidence
summaries. Quality evaluation reads saved assessment files; it does not call a model,
contact websites, or change the saved assessments. No combined score is produced.

Create one ground-truth JSON per posting and keep it in a folder of your choice.
The worked example is `local/evaluation/week11/ground-truth/rtx-hwil.json` when using
this workspace's evaluation inputs. Its expected/useful tool choices are an example
to review, not a published benchmark answer key.

- `schema_version`, `case_id`, `input_fingerprint`: identify the case and exact extracted input. The fingerprint is already present in comparison cases.
- `expected_tools`: checks that should be selected. `useful_tools` includes these and any acceptable optional checks. Duplicate requests earn no extra credit.
- `important_findings`: expected finding IDs, evidence types and optional exact source quotes. Matching applies to evidence and structured IDs, not wording of the final summary.
- `unsupported_claims`: known checks to apply. Supported rules are `redirect_claim_without_observed_redirect`, `open_claim_with_closed_evidence`, `forbidden_finding` (with `finding_id`), and `structured_mismatch` (with `path` and `equals`). These do not detect every possible hallucination.
- `structured_expectations`: optional `path`/`equals` checks against report fields.

Tool/finding entries can have a `when` condition. For example, a tool entry can be
`{"name":"collect_application_page","when":{"evidence_type":"url_inspection","field":"http_status","equals":200}}`.
Conditions can also use `path`/`equals`, or `evidence_type`/`source_quote` to require
a specific recorded source passage. Use `path`/`exists` for checks conditional on an available candidate website. Inapplicable checks are excluded and reported.

Evaluate an existing comparison, using a **new output folder** each time:

```powershell
python scripts/compare_models.py --evaluate-saved local/evaluation/week10/full-efficiency-comparison/comparison.json --ground-truth-dir local/evaluation/week11/ground-truth --output local/evaluation/quality-review
Start-Process "local/evaluation/quality-review/comparison.html"
```

For a normal model comparison, add `--ground-truth-dir` to the existing command to
score each saved result automatically. The same ground truth works for any model.
Changing a case file causes evaluation to use the new definitions; the output
records the definitions, their fingerprint and the evaluator version.

Tool recall counts distinct expected tools validly requested. Tool precision divides
distinct useful valid selections by all requests, including rejected and repeated
requests. HTTP errors such as 403 are evidence-access failures, not automatically
bad selections. Finding recall counts supported expected IDs in the final assessment.
Conditional findings measure retention of facts in recorded evidence; they cannot score important facts that were never collected. A readable HTML shell is not proof that a full job description or application form was reached. Invalid quotations can fail a typed finding even when the underlying observation is true.
Known-claim count reports detected violations; the rate is the fraction of scorable
assessments with at least one. Missing data and zero denominators display N/A.

Current finding IDs are `posting_closed`, `application_page_unavailable`,
`no_http_redirects`, `application_domain_matches`, `application_domain_differs`,
`company_registration_observed`, `applicant_payment_request`, `job_match_found`.
New IDs need a corresponding evidence-support rule before they can earn credit.

New assessments request model-selected `assessment.findings` with `id`, `quote`,
and `evidence_ids`. Tags do not earn credit without supporting cited evidence.
Generation offers only finding choices supported by the actual recorded evidence, including valid IDs and exact text quotes. The model selects which choices to retain, or none; Python does not insert final findings or consult ground truth. Duplicate/unsupported choices and detected factual wording errors receive one correction attempt and otherwise a factual fallback. Clear unqualified employer-authentication claims are rejected because these checks do not authenticate identity. Raw drafts and validation issues remain recorded. This measures assisted evidence selection, so scores should not be presented as an unchanged free-generation task.
Python-generated `key_findings` are not used as proof of model retention. Older
untagged reports and factual fallback summaries show N/A for finding recall; they
still support applicable trace and known-claim checks. Evaluation targets the final
pipeline output, including corrections/fallbacks, not an isolated raw model answer.

Existing timing data is retained. The efficiency summary derives averages and token
totals from existing measurements; missing token counters or API cost display N/A.
Reported API cost is reserved for future provider measurements. No pricing or API
integration is added here.

The methodology adapts stage-level evaluation from [T-Eval](https://aclanthology.org/2024.acl-long.515/),
structured tool validation from [BFCL](https://proceedings.mlr.press/v267/patil25a.html),
and the evidence/faithfulness distinction from [RAGAS](https://aclanthology.org/2024.eacl-demo.16/).
The case definitions and metrics are project-specific; no benchmark datasets,
embeddings or LLM judge are used. Scoring stored evidence is repeatable, but separate
live runs can receive different search results or website responses.


### Separate posting conclusions and reuse existing evidence

Reports and the existing dashboard now show posting match and availability separately from the model's fraud-risk assessment. A recorded closure notice can establish `closed` even when risk is inconclusive. Matching job content establishes an observed listing, not independent identity or acceptance of applications. A title, HTTP 200, or JavaScript/login shell alone establishes neither an open job nor a full application page.

To regenerate only final assessments from a saved comparison, use a new output folder:

```powershell
python scripts/compare_models.py --reassess-saved local/evaluation/week11/expanded/comparison/comparison.json --ground-truth-dir local/evaluation/week11/expanded/ground-truth --output local/evaluation/week11/expanded/my-evidence-replay --max-seconds 300 --max-total-seconds 1800
Start-Process "local/evaluation/week11/expanded/my-evidence-replay/comparison.html"
```

This requires the original models in Ollama. It reuses each run's exact input and tool evidence without extraction, search or tool execution. Original incomplete attempts are preserved without retry. Both case and batch limits apply. The new times and tokens measure final generation only; tool metrics still describe the source trace and are not measurements of the improved live search. Results retain evidence provenance and source performance separately. Source reports are never overwritten.

Collection supports embedded JobPosting data and exact-job public endpoints documented by [Ashby](https://developers.ashbyhq.com/docs/public-job-posting-api) and [Greenhouse](https://docs.greenhouse.io/job-board.html). These data describe postings on the observed board; they do not authenticate the employer. Public HTML, structured-data and API access can still fail or omit material content.


## Claude and GPT assessments

The same assessment workflow supports local Ollama models, Claude Sonnet 5.5 (`claude-sonnet-5-5`) and GPT-6.2 Sol (`gpt-6.2-sol`). All use the same content checks, Python verification tools, source binding, evidence validation and final conclusions. Extraction remains local; existing extracted inputs can be reused without another extraction pass.

Install dependencies using the Python interpreter that runs this repository:

```powershell
python -m pip install -r requirements.txt
```

Add `ANTHROPIC_API_KEY` and `OPENAI_API_KEY` locally to the ignored `.env` file, or set them in the process environment. Existing environment values take precedence. Keys are never included in model evidence, commands, saved reports or logs. Local-only runs do not require either key. Cloud-only comparisons do not require Ollama; a mixed comparison needs a working Ollama server for its local models.

Run fresh full assessments on the reviewed Week 11 inputs, preserving previous results in a new folder:

```powershell
python scripts/compare_models.py --models qwen3.5:9b claude-sonnet-5-5 gpt-6.2-sol --input-dir local/evaluation/week11/expanded/inputs --ground-truth-dir local/evaluation/week11/expanded/ground-truth --output local/evaluation/new-three-model-run --max-seconds 300 --max-total-seconds 3600
Start-Process "local/evaluation/new-three-model-run/comparison.html"
```

This executes fresh content analysis, model-selected tools and final assessments. The standard case/batch deadlines still apply. Matching completed results in that output folder are reused when resuming without `--force`. Authentication/model access is checked before starting missing work. SDK automatic retries are disabled; API failures retain partial evidence rather than fabricating a verdict. Earlier result folders are not changed.

Provider settings are recorded explicitly: Qwen keeps its existing local context/output settings; Sonnet 5.5 uses `between_tools` thinking and provider-controlled sampling; GPT-6.2 Sol uses Responses with low reasoning and `store=false`. Sonnet 5.5 does not accept temperature or disabled thinking, and GPT-6.2 Sol rejects reasoning `none`. These platform settings differ, so the comparison is of the same workflow rather than identical inference engines. The adapters preserve native tool-call IDs and required thinking/reasoning history between tool rounds. Neither enables a provider's built-in web tools.

Native API input/output usage, response/model IDs, available cache/reasoning counters and effective settings appear in the existing performance records. API-only generation durations are unavailable, so native tokens/s remains unavailable rather than being inferred from total request latency. Standard uncached token-price estimates appear in a separate estimated-cost column; actual billed API cost remains N/A. Estimates use the documented rates checked November 20, 2026, exclude unsupported cache-priced requests and are not invoices. Refused/incomplete outputs remain explicitly handled by the assessment pipeline.

Provider references: [Sonnet 5.5 settings/pricing](https://platform.claude.com/docs/en/models/sonnet-5-5/overview), [Claude structured outputs](https://platform.claude.com/docs/en/build-with-claude/structured-outputs), [GPT-6.2 Sol](https://developers.openai.com/api/docs/models/gpt-6.2-sol), [OpenAI function calling](https://developers.openai.com/api/docs/guides/function-calling), [OpenAI structured outputs](https://developers.openai.com/api/docs/guides/structured-outputs). Other API model IDs may have different parameter requirements; the listed models are the validated integration targets.


The dashboard's workflow scores compare configured tool expectations and retained evidence facts. Files passed through `--ground-truth-dir` currently define those expectations; they do not label postings genuine or fraudulent. Expected-tool selection and fact retention are not classification precision, recall or accuracy. No authenticity reference labels are scored by this evaluator. A genuine-only collection can reveal false alarms and abstentions, but cannot establish sensitivity to fraudulent postings; labeled scam examples are needed for that evaluation. An insufficient-evidence outcome is an abstention, not a fraud prediction. Report abstentions separately rather than counting them as correct classifications.

The efficiency table shows estimated API cost only. Local Ollama rows are marked local; cloud rows use recorded token usage and documented rates. The always-unavailable invoice-cost column has been removed. Assessment reports and historical measurements remain unchanged by presentation updates.


Access-limited sources are neutral: failed Indeed/ZipRecruiter Apply wrappers are skipped, and blocked or unavailable checks are excluded from final risk evidence. Careers discovery follows actual company links, respects robots.txt, and stops when access fails. Job availability stays unknown unless readable evidence establishes otherwise. Current source inputs use L_; dataset labels and filenames are hidden from assessment prompts. Historical saved assessments keep their original results until explicitly rerun.


Employer job IDs are an optional matching signal. The system separates employer IDs from LinkedIn listing IDs, checks exact identifiers alongside title and location, and narrows company-site searches when an employer ID is available. Missing IDs are neutral. A match supports association with an opening and does not authenticate the employer.
