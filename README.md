# scam-job-posting-detector

CSC 699 independent study project about detecting fraudulent job postings using an LLM-based agent.

## AI assistance disclosure

I use AI tools to help turn my ideas and vision for this project into working
software. I review AI-assisted contributions and include only code that I
understand and can explain. I take full responsibility for the code I include,
the project's design decisions, testing, limitations, and final results.

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

## Investigate an application link

Assessment now begins with a local Qwen content-analysis pass before company
search or URL checks. It saves `content_analysis` with quoted observations,
unverified claims to check, and limitations. Python excludes statements whose
supporting quotes are absent from the supplied posting text. Quotes can be
checked mechanically; their interpretation still needs review. These initial
observations are supplied to the final assessment and are not a fraud verdict.

To test this step independently on an extracted job JSON:

```powershell
python scripts/analyze_content.py "data/output/AI Engineer - San Francisco, CA 94107 - Indeed.json"
```

The components can also be run separately:

```powershell
python scripts/follow_redirects.py "https://example.com"
python scripts/get_domain_info.py example.com
```

Redirect tracing does not call RDAP. Domain lookup accepts a domain or URL and
looks up its registrable domain without visiting the application page. The
combined inspection tool traces the destination first, then looks up that domain.

The first external-evidence tool follows HTTP redirects, records the destination
and HTTPS use, retrieves RDAP registration information, and compares registrable
domains with the employer website supplied in the posting. It returns evidence,
not a fraud verdict.

Run the tool independently, without Qwen:

```powershell
python scripts/inspect_application_url.py https://example.com --company-website https://www.example.com
```

To let Qwen request the tool and assess an extracted posting:

```powershell
python scripts/assess_job.py "data/output/AI Engineer - San Francisco, CA 94107 - Indeed.json" --output data/assessments/indeed-ai-engineer.json
```

The saved result contains `company_lookup` (website search evidence),
`company_website_used`, `tool_calls` (Qwen's requested arguments and returned
evidence), and `assessment` (risk level and explanation). The posting is preserved.
Each executed tool entry records both the model's requested `arguments` and the
`effective_arguments` actually used. If no company website was selected or
supplied, an unselected URL proposed by Qwen is ignored and inspection runs with
`company_website: null`. The application URL must still match the posting exactly.
If Qwen does not request the tool, the trace is empty and it is told to disclose
that no external investigation occurred. Postings without an application URL
receive an assessment from the supplied posting only.

For a professor demo, show the input posting, the tool request, the redirect and
registration evidence, and the final explanation. The Indeed example lacks a
company website, so the new lookup first searches for a likely site for Hiresort.
If no unique candidate is selected, domain comparison remains `null`.

### Automatically find a company website

When `company` is present and `company_website` is missing, assessment searches
for a likely company website before Qwen's URL investigation. This implements
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
Only ambiguous candidates go through Qwen selection in assessment;
the standalone command only gathers evidence.

The lookup filters common job boards/directories and compares the company name
with candidate domain names or page titles. One matching domain becomes a
`candidate_found`; conflicting matches remain `ambiguous` with no website
selected by the initial heuristic. If ambiguous, one additional search uses the
available location, job title, or company description. Both queries and candidate
evidence are retained, including candidates absent from the second search.
Assessment then makes one additional local Qwen call to compare the candidates
with the posting. Only candidates with `name_match: true` are passed to this
selection call and permitted in its output. Other results remain in the saved
search evidence. If none match, selection skips Qwen and returns "No matching
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
- Domain comparison uses the bundled Public Suffix List, so `jobs.example.co.uk`
  matches `example.co.uk`. The supplied company website is not independently verified.
- RDAP may fail or omit the registration date. Missing values remain `null`, and
  failures appear in `errors`. `lookup_success` refers to RDAP, while
  `redirect_lookup_success` refers to completing the HTTP redirect trace.
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
