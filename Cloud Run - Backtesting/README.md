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

## Deploy to Cloud Run
```powershell
$PROJECT_ID="your-gcp-project-id"
$REGION="us-central1"
$SERVICE_NAME="backtest-webhook"

gcloud auth login
gcloud config set project $PROJECT_ID
gcloud services enable run.googleapis.com cloudbuild.googleapis.com artifactregistry.googleapis.com

gcloud run deploy $SERVICE_NAME --source . --region $REGION --allow-unauthenticated
```

Get URL:
```powershell
$URL = gcloud run services describe $SERVICE_NAME --region $REGION --format "value(status.url)"
$URL
```

Test deployed service:
```powershell
$resp = Invoke-RestMethod -Uri "$URL/webhook/backtest" -Method Post -ContentType "application/json" -Body $payload -TimeoutSec 300
$resp.performance_csv
$resp.trade_history_csv
```

## Notes
- If `node server.js` fails with port in use, set another port:
```powershell
$env:PORT=19081
$env:BACKTEST_PORT=19001
node server.js
```
- Keep `PORT` and `BACKTEST_PORT` different.
