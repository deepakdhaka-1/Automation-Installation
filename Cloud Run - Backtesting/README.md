# Backtesting Cloud Run Webhook

Webhook service that converts strategy rules into a backtest, then returns:
- `performance_csv`: performance matrix for BOTH/LONG/SHORT across 6M/3M/1M/1W/1D
- `trade_history_csv`: trade history for BOTH mode over last 6 months

## Project Files
- `server.js`: Node.js webhook server (`/webhook/backtest`), starts Python backend
- `backtest.py`: Strategy parser, Supabase loader, backtest engine, CSV builders
- `Dockerfile`: Container image for Cloud Run
- `requirements.txt`: Python dependencies
- `package.json`: Node dependencies and start script

## Requirements
- Node.js 18+
- Python 3.10+
- Internet access to Supabase

## Install
```powershell
npm install
pip install -r requirements.txt
```

## Run Locally
```powershell
$env:PORT=8080
$env:BACKTEST_PORT=8001
node server.js
```

Server endpoint:
- `POST http://localhost:8080/webhook/backtest`

## PowerShell Test Command
```powershell
$payload = @{
  "Entry Long"       = "close AND (rsi_value < 35 OR stoch_K < 20 OR willr_value < -80 OR mfi_value < 20) AND close < ema_value AND bbw_value > 7.7354"
  "Exit Long"        = "close <= (entry_price * 0.985) OR close >= (entry_price * 1.033)"
  "Entry Short"      = "close AND (dpo_value > 1.3127 OR msw_msw_lead > -0.2486 OR bbands_LowerBand > 173.2582) AND close > ema_value"
  "Exit Short"       = "close >= (entry_price + (2 * atr_value)) OR close <= (entry_price - (4.4 * atr_value))"
  "Table Name"       = "BTC_4H_TAAPI_Indicator_snapshot"
  "Supporting Table" = "BTC_1H_TAAPI_Indicator_snapshot"
  "commission"       = 0.0005
  "cash"             = 100000
  "margin"           = 0.33
} | ConvertTo-Json -Depth 6

$resp = Invoke-RestMethod -Uri "http://127.0.0.1:8080/webhook/backtest" `
  -Method Post `
  -ContentType "application/json" `
  -Body $payload `
  -TimeoutSec 300

"PERFORMANCE CSV:"
$resp.performance_csv

"`nTRADE HISTORY CSV (6M BOTH):"
$resp.trade_history_csv
```

## Request Payload
```json
{
  "Entry Long": "close AND rsi_value < 35",
  "Exit Long": "close <= (entry_price * 0.985)",
  "Entry Short": "close AND dpo_value > 1.3",
  "Exit Short": "close >= (entry_price + (2 * atr_value))",
  "Table Name": "BTC_4H_TAAPI_Indicator_snapshot",
  "Supporting Table": "BTC_1H_TAAPI_Indicator_snapshot",
  "commission": 0.0005,
  "cash": 100000,
  "margin": 0.33
}
```

## Response Shape
```json
{
  "success": true,
  "performance_csv": "period,mode,return_pct,trades,winrate,pf,mdd,sharpe,...",
  "trade_history_csv": "entry_time,exit_time,type,entry_price,exit_price,pnl,reason,...",
  "report_text": "Pretty text report"
}
```

## Test via cURL (Linux / Mac / Cloud Shell)
```bash
curl -X POST http://localhost:8080/webhook/backtest \
  -H "Content-Type: application/json" \
  -d '{
    "Entry Long": "close AND rsi_value < 35",
    "Exit Long": "close <= (entry_price * 0.985)",
    "Entry Short": "close AND dpo_value > 1.3",
    "Exit Short": "close >= (entry_price + (2 * atr_value))",
    "Table Name": "BTC_4H_TAAPI_Indicator_snapshot",
    "Supporting Table": "BTC_1H_TAAPI_Indicator_snapshot",
    "commission": 0.0005,
    "cash": 100000,
    "margin": 0.33
  }'
```

## Deploy to Cloud Run (CLI & Cloud Shell)

If you're deploying from Google Cloud Shell (or WSL), make sure all your files (`server.js`, `backtest.py`, `package.json`, `requirements.txt`, `Dockerfile`) are exactly synced with your local environment before running out the deployment step.

```bash
# 1. Authenticate and set project (if not already in Cloud Shell)
gcloud auth login
gcloud config set project your-gcp-project-id

# 2. Enable necessary GCP services needed for Cloud Run
gcloud services enable run.googleapis.com cloudbuild.googleapis.com artifactregistry.googleapis.com

# 3. Deploy the service (replace asia-east1 with your preferred region)
gcloud run deploy backtest-webhook \
  --source . \
  --region asia-east1 \
  --allow-unauthenticated
```

Get your live URL:
```bash
gcloud run services describe backtest-webhook --region asia-east1 --format "value(status.url)"
```

### Test live deployed service
Using **Powershell**:
```powershell
$URL = "YOUR_CLOUD_RUN_URL"
$resp = Invoke-RestMethod -Uri "$URL/webhook/backtest" -Method Post -ContentType "application/json" -Body $payload -TimeoutSec 300
$resp.performance_csv
```

Using **cURL**:
```bash
curl -X POST "https://backtest-webhook-881471550102.asia-east1.run.app/webhook/backtest" \
-H "Content-Type: application/json" \
-d '{
  "entry_long": "close AND (rsi_value < 35 OR stoch_K < 20 OR willr_value < -80 OR mfi_value < 20) AND close < ema_value AND bbw_value > 7.7354",
  "exit_long": "close <= (entry_price * 0.985) OR close >= (entry_price * 1.033)",
  "entry_short": "close AND (dpo_value > 1.3127 OR msw_msw_lead > -0.2486 OR bbands_LowerBand > 173.2582) AND close > ema_value",
  "exit_short": "close >= (entry_price + (2 * atr_value)) OR close <= (entry_price - (4.4 * atr_value))",
  "table_name": "BTC_4H_TAAPI_Indicator_snapshot",
  "supporting_table": "BTC_1H_TAAPI_Indicator_snapshot",
  "commission": 0.0005,
  "cash": 100000,
  "margin": 0.33
}'
```

## Troubleshooting & Notes
- **Port Conflicts**: If `node server.js` fails with a port in use error locally, set alternate ports:
```powershell
$env:PORT=19081
$env:BACKTEST_PORT=19001
node server.js
```
- Keep `PORT` and `BACKTEST_PORT` different.
- **Syncing Code**: Always ensure your local code structure is completely ported/replicated to the environment where you fire `gcloud run deploy`. Missing or corrupted `.py` or `.js` files will cause container crashes internally with 500 errors.
