"""Write a portable HTML comparison with no server or external chart libraries."""

import json
import base64
from pathlib import Path


def embedded_font(filename):
    """Keep the font inside the HTML so the visual also works offline."""
    font_path = Path(__file__).resolve().parent / "assets" / "ibm-plex-mono" / filename
    return base64.b64encode(font_path.read_bytes()).decode("ascii")


def write_dashboard(experiment, path):
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
.model{background:#62a8ff}.tool{background:#f1b65b}.other{background:#b79cff}.legend span{margin-right:22px}.legend b{display:inline-block;width:12px;height:12px;margin-right:5px}
table{border-collapse:collapse;width:100%;font-size:13px}th,td{text-align:left;padding:11px 9px;border-bottom:1px solid #333;vertical-align:top}th{white-space:nowrap;font-weight:600}
pre{font:12px 'IBM Plex Mono',monospace;white-space:pre-wrap;overflow-wrap:anywhere;background:#080808;border:1px solid #333;padding:12px}.failed{color:#ff8b95;font-weight:600}a{color:#8fc6ff}summary{cursor:pointer}select:focus-visible,summary:focus-visible,a:focus-visible{outline:2px solid #8fc6ff;outline-offset:3px}
.status-completed{color:#7ee2a8}.status-failed{color:#ff8b95}.status-completed,.status-failed{display:inline-block;padding:3px 7px;border:1px solid currentColor;border-radius:4px;margin-bottom:6px}.risk{display:block}.risk-low{color:#8fc6ff}.risk-medium{color:#f1b65b}.risk-high{color:#ff8b95}.risk-unknown{color:#c9b8ff}
.expanded-cell{padding:18px 0}.expanded-panel{padding:18px;border:1px solid #333;border-left:3px solid #62a8ff;background:#0a0a0a}.expanded-panel h3{font-size:14px;margin:0 0 14px;overflow-wrap:anywhere}.expanded-content{margin-bottom:0}.expanded-content.two-columns{column-count:2;column-gap:30px;column-rule:1px solid #333}tr[hidden]{display:none}
@media(max-width:1000px){.expanded-content.two-columns{column-count:1}}
</style>
<h1>Model Performance Comparison</h1>
<label>Model <select id="model"><option value="">All models</option></select></label>
<label>Case <select id="case"><option value="">All cases</option></select></label>
<p id="overview"></p>
<section><h2>Time per run</h2><p class="legend"><span><b class="model"></b>Model calls</span><span><b class="tool"></b>Verification tools</span><span><b class="other"></b>Other work, including company search</span></p><div id="bars"></div></section>
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
    const modelTime = Math.min(run.model_seconds, run.seconds);
    const toolTime = Math.min(run.tool_seconds || 0, run.seconds - modelTime);
    const otherTime = Math.max(0, run.seconds - modelTime - toolTime);
    const segments = [
        {name: 'model', seconds: modelTime},
        {name: 'tool', seconds: toolTime},
        {name: 'other', seconds: otherTime}
    ];
    for (const part of segments) {
        const segment = addElement('span', undefined, bar);
        segment.className = part.name;
        let percentage = 0;
        if (run.seconds > 0) {
            percentage = 100 * part.seconds / run.seconds;
        }
        segment.style.width = percentage + '%';
        segment.title = part.name + ': ' + part.seconds.toFixed(2) + 's';
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
    }
    const riskLabel = addElement('span', displayValue(run.risk), statusCell);
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
        run.seconds,
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
        model_calls: run.model_calls
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

function render() {
    const chosenModel = document.getElementById('model').value;
    const chosenCase = document.getElementById('case').value;
    const selected = [];
    let completed = 0;
    let failed = 0;
    let reused = 0;
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
    }
    document.getElementById('overview').textContent = selected.length + ' results | ' +
        completed + ' completed | ' + failed + ' failed | ' + reused + ' reused';
    const bars = document.getElementById('bars');
    const table = document.getElementById('results');
    bars.replaceChildren();
    table.replaceChildren();
    for (const run of selected) {
        drawTiming(run, maximum, bars);
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
