const express = require('express');
const { spawn } = require('child_process');

const app = express();
app.use(express.json());
const BACKTEST_PORT = process.env.BACKTEST_PORT || "8001";

function sleep(ms) {
    return new Promise(resolve => setTimeout(resolve, ms));
}

async function callPythonBacktest(payload, maxAttempts = 8) {
    let lastErr = null;
    for (let attempt = 1; attempt <= maxAttempts; attempt++) {
        try {
            const response = await fetch(`http://127.0.0.1:${BACKTEST_PORT}/backtest`, {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify(payload)
            });

            const text = await response.text();
            let json;
            try {
                json = JSON.parse(text);
            } catch {
                throw new Error(`Python backend returned non-JSON response (status ${response.status})`);
            }

            if (!response.ok) {
                throw new Error(json?.detail || json?.error || `Python backend HTTP ${response.status}`);
            }
            return json;
        } catch (err) {
            lastErr = err;
            if (attempt < maxAttempts) {
                // Exponential backoff for cold starts while uvicorn is booting.
                await sleep(Math.min(4000, 250 * Math.pow(2, attempt - 1)));
                continue;
            }
        }
    }
    throw lastErr || new Error("Unable to reach Python backend");
}

function extractTpSl(text) {
    const res = { tp_atr_mult: null, sl_atr_mult: null, tp_pct: null, sl_pct: null, atr_col: "atr_value", time_limit_hours: null };
    const slAtrMatch = text.match(/(?:sl|stop loss).*?([\d\.]+)\s*\*\s*(atr\w*)/i);
    if (slAtrMatch) { res.sl_atr_mult = parseFloat(slAtrMatch[1]); res.atr_col = slAtrMatch[2]; }
    const tpAtrMatch = text.match(/(?:tp|take profit).*?([\d\.]+)\s*\*\s*(atr\w*)/i);
    if (tpAtrMatch) { res.tp_atr_mult = parseFloat(tpAtrMatch[1]); res.atr_col = tpAtrMatch[2]; }
    const tpPctMatch = text.match(/(?:tp|take profit).*?([\d\.]+)\s*%/i);
    if (tpPctMatch) res.tp_pct = parseFloat(tpPctMatch[1]) / 100;
    const slPctMatch = text.match(/(?:sl|stop loss).*?([\d\.]+)\s*%/i);
    if (slPctMatch) res.sl_pct = parseFloat(slPctMatch[1]) / 100;
    const timeMatch = text.match(/(\d+)\s*(?:h|hour)/i);
    if (timeMatch) res.time_limit_hours = parseInt(timeMatch[1]);
    return res;
}

function toPython(logicStr, type = "") {
    if (!logicStr) return "";
    let logic = logicStr.trim();
    logic = logic.replace(/\/\*[\s\S]*?\*\//g, "");
    logic = logic.replace(/\/\/.*/g, "");

    function getBalancedContent(str, startChar, endChar) {
        let count = 0;
        let start = str.indexOf(startChar);
        if (start === -1) return null;
        for (let i = start; i < str.length; i++) {
            if (str[i] === startChar) count++;
            else if (str[i] === endChar) count--;
            if (count === 0) return { content: str.substring(start + 1, i), full: str.substring(start, i + 1), endIndex: i + 1 };
        }
        return null;
    }

    const labels = ["Logic:", "Entry Logic:", "Exit Logic:", "Entry Short:", "Entry Long:", "Exit Short:", "Exit Long:", "Short Entry:", "Long Entry:", "Short Exit:", "Long Exit:", "LONG:", "SHORT:", "TP:", "SL:", "Exit:", "Stop Loss:", "Take Profit:"];
    let lines = logic.split('\n').map(line => {
        let l = line.trim();
        if (/^(const|let|var)\s+\w+\s*=/.test(l)) l = l.replace(/^(const|let|var)\s+\w+\s*=\s*/, '');
        labels.forEach(label => {
            if (l.toLowerCase().startsWith(label.toLowerCase())) l = l.substring(label.length).trim();
        });
        return l;
    }).filter(l => l.length > 0);
    logic = lines.join(' ').trim();

    if (/^\s*if\s*\b/i.test(logic)) {
        const balanced = getBalancedContent(logic, "(", ")");
        if (balanced) {
            const remainder = logic.substring(balanced.endIndex).trim();
            if (remainder.startsWith("{") || /\breturn\b/i.test(remainder)) logic = balanced.content.trim();
            else logic = logic.replace(/^\s*if\s*\b/i, "").trim();
        } else {
            logic = logic.replace(/^\s*if\s*\b/i, "").trim();
        }
    } else if (logic.includes("{") && logic.includes("}")) {
        const bodyMatch = logic.match(/\{([\s\S]*)\}/);
        if (bodyMatch) {
            let body = bodyMatch[1].trim();
            const returnMatch = body.match(/return\s+([\s\S]*?);?\s*$/);
            if (returnMatch) logic = returnMatch[1];
            else logic = body;
        }
    }

    logic = logic.replace(/historical\[(\d+)\]\.(\w+)/g, "$2_prev$1");
    logic = logic.replace(/current\.(\w+)/g, "$1");
    logic = logic.replace(/(\w+)\.diff\(\)\s*>\s*0/gi, "($1 > $1_prev)");
    logic = logic.replace(/(\w+)\.diff\(\)\s*<\s*0/gi, "($1 < $1_prev)");
    logic = logic.replace(/\bAND\b/gi, 'and').replace(/\bOR\b/gi, 'or').replace(/\bNOT\b/gi, 'not');

    logic = logic.replace(/(\w+)\s+(crosses\s+(above|below))\s+([\w\d\.-]+)/gi, (match, ind, p2, op, target) => {
        const isCol = /^[a-zA-Z_]\w*$/.test(target);
        const targetPrev = isCol ? `${target}_prev` : target;
        return op.toLowerCase().includes("above") ? `(${ind} > ${target} and ${ind}_prev <= ${targetPrev})` : `(${ind} < ${target} and ${ind}_prev >= ${targetPrev})`;
    });

    if (type) {
        const parts = logic.split(/\b(OR|AND|or|and)\b/g);
        logic = parts.map(p => {
            let trimmed = p.trim();
            if (/^(OR|AND|or|and)$/i.test(trimmed)) return trimmed;
            if (/^(\d+(\.\d+)?%?|fixed\s*\d+(\.\d+)?%?)$/i.test(trimmed.toLowerCase().replace(/^(tp|sl|stop loss|take profit|exit):?\s*/i, ""))) return "";
            if ((trimmed.toLowerCase().includes('entry_price') || trimmed.toLowerCase().includes('sl') || trimmed.toLowerCase().includes('tp')) && !/[<>=]/.test(trimmed)) {
                let subP = (trimmed.match(/\(/g) || []).length;
                let subC = (trimmed.match(/\)/g) || []).length;
                if (subC > subP) trimmed = trimmed.replace(/\)+$/, "");
                return type === 'long' ? `(price_value <= ${trimmed})` : `(price_value >= ${trimmed})`;
            }
            return p;
        }).filter(p => p.length > 0).join(' ');
    }

    logic = logic.replace(/\b(const|let|var)\b/g, "");
    logic = logic.replace(/%/g, "");
    logic = logic.replace(/[.`*;]+$/, "").trim();

    let openC = (logic.match(/\(/g) || []).length;
    let closeC = (logic.match(/\)/g) || []).length;
    while (closeC > openC) { logic = logic.replace(/\)(?=[^)]*$)/, ""); closeC--; }
    while (openC > closeC) { logic += ")"; openC++; }
    return logic;
}

function getNeededCols(logicStrings, schema) {
    const found = new Set();
    const reserved = new Set(["and", "or", "not", "if", "else", "true", "false", "none", "entry_price", "price_value", "tp", "sl", "time", "exit", "force", "condition", "atr", "below", "above", "crosses", "return", "long", "short"]);
    logicStrings.forEach(s => {
        const words = s.match(/\b[a-zA-Z_]\w*\b/g) || [];
        words.forEach(w => {
            const cleanW = w.replace(/_prev\d*$/, "");
            if (!reserved.has(cleanW.toLowerCase()) && isNaN(cleanW)) found.add(cleanW);
        });
    });
    ["open", "high", "low", "close", "volume", "timestamp", "atr_value"].forEach(std => found.add(std));
    if (schema && schema.length > 0) {
        const schemaMap = schema.reduce((acc, s) => { acc[s.toLowerCase()] = s; return acc; }, {});
        return Array.from(found).map(col => schemaMap[col.toLowerCase()] || col);
    }
    return Array.from(found);
}

function cleanLogic(text) {
    if (!text.includes("|")) return text;
    const parts = text.split("|");
    const logicPart = parts.find(p => /[<>=]|prev|diff|crosses|between/i.test(p));
    return logicPart ? logicPart.trim() : parts[0].trim();
}

app.post('/webhook/backtest', async (req, res) => {
    try {
        const input = req.body;
        const entryLong = input["Entry Long"] || input.entry_long || "";
        const entryShort = input["Entry Short"] || input.entry_short || "";
        const exitLong = input["Exit Long"] || input.exit_long || "";
        const exitShort = input["Exit Short"] || input.exit_short || "";
        const tableName = input["Table Name"] || input.table || "BTC_4H_TAAPI_Indicator_snapshot";
        const exitTable = input["Supporting Table"] || input.exit_table || "BTC_1H_TAAPI_Indicator_snapshot";
        const schemaCols = input.schema_cols || ["close", "high", "low", "open", "volume", "timestamp", "atr_value"];

        const commission = input.commission || "0.0005";
        const cash = input.cash || "100000";
        const margin = input.margin || "0.33";

        const exitLongClean = cleanLogic(exitLong);
        const exitShortClean = cleanLogic(exitShort);

        const data = {
            entry_long: toPython(entryLong, 'long'),
            entry_short: toPython(entryShort, 'short'),
            exit_long: toPython(exitLongClean, 'long'),
            exit_short: toPython(exitShortClean, 'short'),
            tp_atr_mult: null, sl_atr_mult: null, tp_pct: null, sl_pct: null, atr_col: "atr_value",
            time_limit_hours: null
        };

        const tpSlLong = extractTpSl(exitLong);
        const tpSlShort = extractTpSl(exitShort);
        ["tp_atr_mult", "sl_atr_mult", "tp_pct", "sl_pct", "atr_col", "time_limit_hours"].forEach(k => {
            data[k] = tpSlLong[k] || tpSlShort[k] || data[k];
        });

        data.needed_cols = getNeededCols([data.entry_long, data.entry_short, data.exit_long, data.exit_short], schemaCols);

        const payload = {
            logic: data,
            table: tableName,
            exit_table: exitTable,
            commission: parseFloat(commission),
            cash: parseFloat(cash),
            margin: parseFloat(margin)
        };

        const resultJson = await callPythonBacktest(payload);
        // Keep response as JSON so UI can read structured metrics and CSV text together.
        res.json(resultJson);
    } catch (err) {
        const msg = err && err.message ? err.message : String(err);
        res.status(500).json({ error: "Failed executing Python strategy", details: msg });
    }
});

const PORT = process.env.PORT || 8080;
app.listen(PORT, () => {
    const pythonExecutable = process.platform === "win32" ? "python" : "python3";
    const pythonProcess = spawn(pythonExecutable, ["-m", "uvicorn", "backtest:app", "--port", BACKTEST_PORT, "--host", "127.0.0.1"]);

    pythonProcess.stdout.on('data', (chunk) => {
        console.log("FastAPI STDOUT:", chunk.toString());
    });
    pythonProcess.stderr.on('data', (chunk) => {
        console.error("FastAPI STDERR:", chunk.toString());
    });
    pythonProcess.on('close', (code) => {
        console.log("FastAPI exited with code", code);
    });
});
