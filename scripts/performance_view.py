"""Write a portable HTML comparison with no server or external chart libraries."""

import json
import base64
from pathlib import Path


def embedded_font(filename):
    """Keep the font inside the HTML so the visual also works offline."""
    font_path = Path(__file__).resolve().parent / "assets" / "ibm-plex-mono" / filename
    return base64.b64encode(font_path.read_bytes()).decode("ascii")


def timing_parts(run, tool_calls, company_search=False):
    """Order measured calls by selection round; keep unrecorded time explicit."""
    labels = {
        "content_analysis": "Content Analysis",
        "company_selection": "Company Candidate Selection",
        "tool_selection": "Tool Selection",
        "final_assessment": "Final Assessment",
        "summary_correction": "Summary Correction",
        "inspect_application_url": "URL Inspection",
        "check_employer_association": "Employer Association",
        "collect_application_page": "Application-Page Collection",
        "get_company_domain_info": "Company Domain Investigation",
    }
    parts = []
    counts = {}
    calls = []
    used_tools = set()
    selection_round = 0
    for call in run.get("model_calls", []):
        stage = call.get("stage", "model")
        calls.append((stage, call.get("seconds", 0), "Model"))
        if stage != "tool_selection":
            continue
        selection_round += 1
        requested = [request["name"] for request in call.get("raw_tool_calls", [])]
        for index, tool in enumerate(tool_calls):
            if index in used_tools:
                continue
            if tool.get("round") is not None:
                belongs_here = tool["round"] == selection_round
            else:
                # Older reports have no round number; match recorded requests.
                belongs_here = tool.get("tool") in requested
                if belongs_here:
                    requested.remove(tool["tool"])
            if belongs_here:
                calls.append((tool.get("tool", "tool"), tool.get("seconds", 0), "Tool"))
                used_tools.add(index)
    for index, tool in enumerate(tool_calls):
        if index not in used_tools:
            calls.append((tool.get("tool", "tool"), tool.get("seconds", 0), "Tool (order unrecorded)"))
    remaining = max(0, run.get("seconds") or 0)
    for name, seconds, kind in calls:
        counts[name] = counts.get(name, 0) + 1
        label = labels.get(name, name.replace("_", " ").title())
        if kind == "Model":
            label += " " + str(counts[name])
        label = kind + ": " + label
        measured = min(max(0, seconds or 0), remaining)
        remaining = max(0, remaining - measured)
        parts.append({"name": name, "label": label, "seconds": measured})
    if remaining > 0:
        label = "Unmeasured Time (Position Unrecorded)"
        if company_search:
            label += " (Includes Company Search)"
        parts.append({"name": "unmeasured", "label": label, "seconds": remaining})
    return parts


def write_dashboard(experiment, path):
    # Enrich the display from saved reports without changing the experiment JSON.
    saved_runs = experiment.get("runs", [])
    experiment = dict(experiment)
    experiment["runs"] = []
    for original in saved_runs:
        run = dict(original)
        investigation = {}
        filename = run.get("report")
        if filename and Path(filename).name == filename:
            try:
                report = json.loads((path.parent / filename).read_text(encoding="utf-8"))
                investigation = report.get("investigation", {})
                run['conclusion'] = report.get('conclusion')
            except (OSError, ValueError):
                pass
        calls = investigation.get("tool_calls", run.get("tool_calls", []))
        if investigation.get('evidence_reuse'):
            calls = []
            run['evidence_reused'] = True
        run["timing_parts"] = timing_parts(run, calls, investigation.get("company_lookup") is not None and not investigation.get('evidence_reuse'))
        experiment["runs"].append(run)
    data = json.dumps(experiment, ensure_ascii=True).replace("<", "\\u003c")
    template = r'''<!doctype html>
<html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Model Performance Comparison</title>
<style>
@font-face{font-family:'IBM Plex Mono';src:url(data:font/woff2;base64,__FONT_REGULAR__) format('woff2');font-weight:400;font-style:normal;font-display:swap}
@font-face{font-family:'IBM Plex Mono';src:url(data:font/woff2;base64,__FONT_BOLD__) format('woff2');font-weight:600 700;font-style:normal;font-display:swap}
html{background:#050505;color-scheme:dark}
body{font:14px 'IBM Plex Mono',monospace;background:#050505;color:#f5f5f5;margin:0;padding:32px;max-width:1450px;margin:auto}
h1{margin-bottom:8px}p{max-width:1000px;line-height:1.5}label{display:inline-block;margin:14px 22px 14px 0}
select{font:inherit;color:#fff;background:#161616;padding:8px;border:1px solid #444;border-radius:6px;max-width:100%}section{background:#111;border:1px solid #2a2a2a;padding:22px;margin:20px 0;border-radius:12px;overflow:auto}
.bar-row{margin:15px 0}.bar-label{font-size:14px;margin-bottom:5px}.bar{display:flex;height:23px;min-width:2px;border-radius:4px;overflow:hidden}
.legend{display:flex;flex-wrap:wrap;gap:12px 22px;margin-bottom:24px;font-size:12px}.legend span{display:inline-flex;align-items:center}.legend b{display:inline-block;width:12px;height:12px;margin-right:7px;flex-shrink:0}
table{border-collapse:collapse;width:100%;font-size:13px}th,td{text-align:left;padding:11px 9px;border-bottom:1px solid #333;vertical-align:top}th{white-space:nowrap;font-weight:600}
pre{font:12px 'IBM Plex Mono',monospace;white-space:pre-wrap;overflow-wrap:anywhere;background:#080808;border:1px solid #333;padding:12px}.failed{color:#ff8b95;font-weight:600}a{color:#8fc6ff}summary{cursor:pointer}select:focus-visible,summary:focus-visible,a:focus-visible{outline:2px solid #8fc6ff;outline-offset:3px}
.status-completed{color:#7ee2a8}.status-failed{color:#ff8b95}.status-timed-out{color:#f1b65b}.status-not-run{color:#aaa}.status-completed,.status-failed,.status-timed-out,.status-not-run{display:inline-block;padding:3px 7px;border:1px solid currentColor;border-radius:4px;margin-bottom:6px}.risk{display:block}.risk-low{color:#8fc6ff}.risk-medium{color:#f1b65b}.risk-high{color:#ff8b95}.risk-unknown{color:#c9b8ff}
.expanded-cell{padding:18px 0}.expanded-panel{padding:18px;border:1px solid #333;border-left:3px solid #62a8ff;background:#0a0a0a}.expanded-panel h3{font-size:14px;margin:0 0 14px;overflow-wrap:anywhere}.expanded-content{margin-bottom:0}.expanded-content.two-columns{column-count:2;column-gap:30px;column-rule:1px solid #333}tr[hidden]{display:none}
@media(max-width:1000px){.expanded-content.two-columns{column-count:1}}
</style>
<h1>Model Performance Comparison</h1>
<label>Model <select id="model"><option value="">All models</option></select></label>
<label>Case <select id="case"><option value="">All cases</option></select></label>
<p id="overview"></p>
<section><h2>Efficiency Summary</h2><table><thead><tr><th>Model</th><th>Completed / Attempted</th><th>Mean Completed Runtime</th><th>Recorded Input Tokens</th><th>Recorded Output Tokens</th><th>Estimated API Cost (USD)</th></tr></thead><tbody id="efficiency"></tbody></table></section>
<section><h2>Workflow and Evidence Checks</h2><table><thead><tr><th>Model</th><th>Runs Checked</th><th>Expected Tools Selected</th><th>Useful Tool Selections</th><th>Expected Facts Retained</th><th>Fact-check Coverage</th><th>Known-rule Violations</th><th>Reports Flagged by Rules</th></tr></thead><tbody id="quality"></tbody></table></section>
<section><h2>Time per Run: Individual Calls</h2><div id="timing-legend" class="legend"></div><div id="bars"></div></section>
<section><h2>Assessment Results</h2><table><thead><tr><th>Model / case</th><th>Status / risk</th><th>Total seconds</th><th>Tokens/s</th><th>Tools / rejections</th><th>Details</th></tr></thead><tbody id="results"></tbody></table></section>
<script id="data" type="application/json">__DATA__</script>
<script>
// This page only displays saved JSON. Filters never call a model.
const experiment = JSON.parse(document.getElementById('data').textContent);
const runs = experiment.runs;

function addElement(tag, text, parent) {
    const item = document.createElement(tag);
    if (text !== undefined) {
        item.textContent = text;
    }
    if (parent) {
        parent.appendChild(item);
    }
    return item;
}

function fillFilter(elementId, field) {
    const select = document.getElementById(elementId);
    const seen = new Set();
    for (const run of runs) {
        const value = run[field];
        if (!seen.has(value)) {
            const option = addElement('option', value, select);
            option.value = value;
            seen.add(value);
        }
    }
    select.addEventListener('change', render);
}

function displayValue(value) {
    if (value === undefined || value === null) {
        return '?';
    }
    return String(value);
}

const colors = {
        content_analysis: '#62a8ff', company_selection: '#49c5d6',
        tool_selection: '#ab96ff', final_assessment: '#7ee2a8',
        summary_correction: '#ef91cb', inspect_application_url: '#f1b65b',
        check_employer_association: '#ff885e', collect_application_page: '#e5df70',
        get_company_domain_info: '#54d2a3',
        unmeasured: '#888888'
};

function drawLegend(selected) {
    const legend = document.getElementById('timing-legend');
    legend.replaceChildren();
    const seen = new Set();
    for (const run of selected) {
        for (const part of run.timing_parts || []) {
            if (seen.has(part.name)) continue;
            seen.add(part.name);
            const item = addElement('span', undefined, legend);
            const swatch = addElement('b', undefined, item);
            swatch.style.background = colors[part.name] || '#d5a5e8';
            // Repeated calls share one legend entry; the tooltip retains the call number.
            item.appendChild(document.createTextNode(part.label.replace(/ \d+$/, '').replace(/ \(Includes Company Search\)$/, '')));
        }
    }
}

function drawTiming(run, maximum, container) {
    const row = addElement('div', undefined, container);
    row.className = 'bar-row';
    let label = run.model + ' | ' + run.case + ' | ' + run.seconds + 's | ' + run.status;
    if (run.reused) {
        label += ' (saved measurement)';
    }
    const heading = addElement('div', label, row);
    heading.className = 'bar-label';
    if (run.status === 'failed') {
        heading.classList.add('failed');
    }
    const bar = addElement('div', undefined, row);
    bar.className = 'bar';
    bar.style.width = (100 * run.seconds / maximum) + '%';
    for (const part of run.timing_parts || []) {
        const color = colors[part.name] || '#d5a5e8';
        const text = part.label + ': ' + part.seconds.toFixed(2) + 's';
        const segment = addElement('span', undefined, bar);
        segment.style.background = color;
        segment.style.width = (run.seconds > 0 ? 100 * part.seconds / run.seconds : 0) + '%';
        segment.title = text;
    }
}

function addResultRow(run, table) {
    const row = addElement('tr', undefined, table);
    row.className = 'result-row';
    let status = run.status;
    if (run.reused) {
        status += ' (saved result)';
    }
    let tools = 'None';
    if (run.tools && run.tools.length > 0) {
        tools = run.tools.join(', ');
    }
    let rejections = 0;
    if (run.tool_rejections !== undefined) {
        rejections = run.tool_rejections;
    }
    addElement('td', run.model + ' | ' + run.case + ' (repeat ' + run.repetition + ')', row);
    const statusCell = addElement('td', undefined, row);
    const statusLabel = addElement('span', status, statusCell);
    if (run.status === 'completed') {
        statusLabel.className = 'status-completed';
    } else if (run.status === 'failed') {
        statusLabel.className = 'status-failed';
    } else if (run.status === 'timed_out') {
        statusLabel.className = 'status-timed-out';
    } else if (run.status === 'not_run') {
        statusLabel.className = 'status-not-run';
    }
    const riskLabel = addElement('span', displayValue(run.risk), statusCell);
    if (run.conclusion) {
        addElement('div', 'Posting: ' + run.conclusion.posting_match.status.replaceAll('_', ' '), statusCell);
        addElement('div', 'Availability: ' + run.conclusion.availability.status.replaceAll('_', ' '), statusCell);
    }
    if (run.evidence_reused) addElement('div', 'Saved evidence; final generation only', statusCell);
    riskLabel.className = 'risk';
    if (run.risk === 'low') {
        riskLabel.classList.add('risk-low');
    } else if (run.risk === 'medium') {
        riskLabel.classList.add('risk-medium');
    } else if (run.risk === 'high') {
        riskLabel.classList.add('risk-high');
    } else {
        riskLabel.classList.add('risk-unknown');
    }
    const values = [
        run.seconds === null ? 'Not run' : run.seconds,
        displayValue(run.tokens_per_second),
        tools + ' / ' + rejections
    ];
    for (const value of values) {
        addElement('td', String(value), row);
    }
    const cell = addElement('td', undefined, row);
    const details = addElement('details', undefined, cell);
    addElement('summary', 'Inspect', details);
    // Put the content in its own full-width row, not inside the narrow button cell.
    const expandedRow = addElement('tr', undefined, table);
    expandedRow.className = 'expanded-row';
    expandedRow.hidden = true;
    expandedRow.id = 'assessment-details-' + table.children.length;
    details.setAttribute('aria-controls', expandedRow.id);
    const expandedCell = addElement('td', undefined, expandedRow);
    expandedCell.colSpan = 6;
    expandedCell.className = 'expanded-cell';
    const panel = addElement('div', undefined, expandedCell);
    panel.className = 'expanded-panel';
    addElement('h3', run.model + ' | ' + run.case, panel);
    if (run.report) {
        const link = addElement('a', 'Full saved assessment', panel);
        link.href = run.report;
    }
    const recordedDetails = {
        error: run.error,
        reused: run.reused,
        summary_review: run.summary_review,
        conclusion: run.conclusion,
        model_calls: run.model_calls,
        quality: run.quality
    };
    const text = JSON.stringify(recordedDetails, null, 2);
    const content = addElement('pre', text, panel);
    content.className = 'expanded-content';
    if (text.length > 1800) {
        content.classList.add('two-columns');
    }
    details.addEventListener('toggle', function () {
        expandedRow.hidden = !details.open;
    });
}

function fraction(numerator, denominator) {
    if (!denominator) return 'N/A';
    return (100 * numerator / denominator).toFixed(1) + '% (' + numerator + '/' + denominator + ')';
}

function drawSummaries(selected) {
    const efficiency = document.getElementById('efficiency');
    const qualityTable = document.getElementById('quality');
    efficiency.replaceChildren();
    qualityTable.replaceChildren();
    const models = [...new Set(selected.map(run => run.model))];
    for (const model of models) {
        const rows = selected.filter(run => run.model === model);
        const attempted = rows.filter(run => run.status !== 'not_run');
        const completed = rows.filter(run => run.status === 'completed');
        const durations = completed.filter(run => typeof run.seconds === 'number');
        const mean = durations.length ? (durations.reduce((sum, run) => sum + run.seconds, 0) / durations.length).toFixed(2) + 's' : 'N/A';
        const calls = attempted.flatMap(run => run.model_calls || []);
        const allTokens = attempted.length && attempted.every(run => run.model_calls && run.model_calls.length) &&
            calls.every(call => call.status === 'completed' && Number.isFinite(call.prompt_eval_count) && Number.isFinite(call.eval_count));
        const input = allTokens ? calls.reduce((sum, call) => sum + call.prompt_eval_count, 0) : 'N/A';
        const output = allTokens ? calls.reduce((sum, call) => sum + call.eval_count, 0) : 'N/A';
        const efficiencyRow = addElement('tr', undefined, efficiency);
        const localOnly = rows.every(run => run.provider === 'ollama' || (typeof run.model === 'string' && run.model.includes(':')));
        const estimatedCost = attempted.length && attempted.every(run => Number.isFinite(run.estimated_api_cost_usd)) ?
            attempted.reduce((sum, run) => sum + run.estimated_api_cost_usd, 0).toFixed(4) : (localOnly ? '— (local)' : 'Unavailable');
        for (const value of [model, fraction(completed.length, attempted.length), mean, input, output, estimatedCost]) addElement('td', String(value), efficiencyRow);
        const evaluated = rows.filter(run => run.quality && run.quality.status === 'evaluated');
        function pooled(field) {
            let numerator = 0, denominator = 0;
            for (const run of evaluated) {
                const metric = run.quality[field];
                if (metric) { numerator += metric.numerator; denominator += metric.denominator; }
            }
            return fraction(numerator, denominator);
        }
        const findingCoverage = evaluated.filter(run => run.quality.finding_recall && run.quality.finding_recall.denominator > 0).length;
        const claimRuns = evaluated.filter(run => run.quality.unsupported_claims && Number.isFinite(run.quality.unsupported_claims.count));
        const violations = claimRuns.reduce((sum, run) => sum + run.quality.unsupported_claims.count, 0);
        const flagged = claimRuns.filter(run => run.quality.unsupported_claims.count > 0).length;
        const qualityRow = addElement('tr', undefined, qualityTable);
        for (const value of [model, evaluated.length + '/' + rows.length, pooled('tool_recall'), pooled('tool_precision'),
            pooled('finding_recall'), findingCoverage + '/' + evaluated.length, claimRuns.length ? violations : 'N/A', fraction(flagged, claimRuns.length)]) {
            addElement('td', String(value), qualityRow);
        }
    }
}

function render() {
    const chosenModel = document.getElementById('model').value;
    const chosenCase = document.getElementById('case').value;
    const selected = [];
    let completed = 0;
    let failed = 0;
    let reused = 0;
    let timedOut = 0;
    let notRun = 0;
    let maximum = 1;
    for (const run of runs) {
        if (chosenModel && run.model !== chosenModel) {
            continue;
        }
        if (chosenCase && run.case !== chosenCase) {
            continue;
        }
        selected.push(run);
        maximum = Math.max(maximum, run.seconds);
        if (run.status === 'completed') {
            completed += 1;
        }
        if (run.status === 'failed') {
            failed += 1;
        }
        if (run.reused) {
            reused += 1;
        }
        if (run.status === 'timed_out') timedOut += 1;
        if (run.status === 'not_run') notRun += 1;
    }
    const modeLabel = experiment.mode === 'saved_evidence_reassessment' ? 'Saved Evidence: Final Generation Only | ' :
        (experiment.mode === 'saved_evaluation' ? 'Saved Assessment Evaluation | ' : '');
    document.getElementById('overview').textContent = modeLabel + selected.length + ' results | ' +
        completed + ' completed | ' + failed + ' failed | ' + reused + ' reused' +
        (timedOut ? ' | ' + timedOut + ' timed out' : '') + (notRun ? ' | ' + notRun + ' not started' : '');
    drawSummaries(selected);
    drawLegend(selected);
    const bars = document.getElementById('bars');
    const table = document.getElementById('results');
    bars.replaceChildren();
    table.replaceChildren();
    for (const run of selected) {
        if (typeof run.seconds === 'number') drawTiming(run, maximum, bars);
        addResultRow(run, table);
    }
}

fillFilter('model', 'model');
fillFilter('case', 'case');
render();
</script></html>'''
    template = template.replace("__FONT_REGULAR__", embedded_font("IBMPlexMono-Regular.woff2"))
    template = template.replace("__FONT_BOLD__", embedded_font("IBMPlexMono-SemiBold.woff2"))
    path.write_text(template.replace("__DATA__", data), encoding="utf-8")
