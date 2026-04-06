#!/usr/bin/env python3
import sys
import pandas as pd
import numpy as np
import os
import json
import re
import time
import io
import csv
from typing import Dict, Any, List
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

# ==========================================
# 1. CONFIGURATION
# ==========================================
class Config:
    SUPABASE_URL = "https://zqjujdflevmzgozwhqzg.supabase.co"
    SUPABASE_KEY = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6InpxanVqZGZsZXZtemdvendocXpnIiwicm9sZSI6ImFub24iLCJpYXQiOjE3NjQ2OTYzODEsImV4cCI6MjA4MDI3MjM4MX0.JKqYMB4yr8Fp4gRJKTORswyuj_qt_wNWrPH1Yb4bYKY"
    CASH = 1000000
    COMMISSION = 0.0003
    MARGIN = 0.33
    ALLOW_FLIPPING = True
    MODE = 'both'

def is_downtime(dt_iso):
    ist_time = dt_iso + pd.Timedelta(hours=5, minutes=30)
    weekday = ist_time.weekday()
    hour = ist_time.hour
    if weekday == 5 and hour >= 7: return True
    if weekday == 6 and hour < 19: return True
    return False

# ==========================================
# 2. DATA LOADER (SUPABASE)
# ==========================================

_DATA_CACHE = {}  # In-memory cache for Cloud Run warm instances
_CACHE_TIME = {}

def load_data(url, key, table_name, needed_cols, max_retries=5, debug=False):
    # Sort columns for cache key consistency
    cache_key = f"{table_name}_{','.join(sorted(needed_cols))}"
    
    # Simple TTL cache: expire after 15 minutes to stay fresh
    now = time.time()
    if cache_key in _DATA_CACHE and now - _CACHE_TIME.get(cache_key, 0) < 900:
        return _DATA_CACHE[cache_key].copy()

    from supabase import create_client
    supabase = create_client(url, key)
    
    for attempt in range(max_retries):
        try:
            try:
                schema_res = supabase.table(table_name).select("*").limit(1).execute()
                if schema_res.data:
                    col_map = {c.lower(): c for c in schema_res.data[0].keys()}
                    fetch_cols = [col_map[c.lower()] for c in needed_cols if c.lower() in col_map]
                    for essential in ['open','high','low','close','timestamp']:
                        if essential in col_map and col_map[essential] not in fetch_cols:
                            fetch_cols.append(col_map[essential])
                else:
                    fetch_cols = list(set(needed_cols + ['open','high','low','close','timestamp']))
            except Exception:
                fetch_cols = list(set(needed_cols + ['open','high','low','close','timestamp']))

            cols_str = ",".join(list(set(fetch_cols)))
            
            # Determine optimal limit for 6 months (180 days)
            t_upper = table_name.upper()
            if '4H' in t_upper: limit = 1100
            elif '1H' in t_upper: limit = 4500
            elif '15M' in t_upper: limit = 17500
            elif '5M' in t_upper: limit = 52000
            else: limit = 15000
            
            # Fetch chunks concurrently to drastically speed up network I/O
            all_data = []
            
            for offset in range(0, limit, 1000):
                res = supabase.table(table_name).select(cols_str).order('timestamp', desc=True).range(offset, offset + 999).execute()
                if not res.data: break
                all_data.extend(res.data)
                if len(res.data) < 1000: break
            
            if not all_data:
                raise ValueError(f"No data found in table {table_name}")

            df = pd.DataFrame(all_data)
            df.columns = [c.lower() for c in df.columns]
            ts_col = 'timestamp' if 'timestamp' in df.columns else [c for c in df.columns if 'time' in c][0]

            # Match original n8n 2.py behavior: normalize timestamp, coerce indicator numerics when valid,
            # and fill numeric gaps for stable signal evaluation.
            df[ts_col] = pd.to_numeric(df[ts_col], errors='coerce')
            df = df.dropna(subset=[ts_col])
            ts_sample = df[ts_col].iloc[0]
            unit = 's' if ts_sample < 1e12 else 'ms'
            df[ts_col] = pd.to_datetime(df[ts_col], unit=unit)
            df.set_index(ts_col, inplace=True)

            for col in df.columns:
                if col != ts_col:
                    converted = pd.to_numeric(df[col], errors='coerce')
                    # Preserve non-numeric categorical fields; coerce only mostly numeric series.
                    if converted.notna().mean() >= 0.5:
                        df[col] = converted

            num_cols = df.select_dtypes(include='number').columns
            if len(num_cols) > 0:
                df[num_cols] = df[num_cols].ffill().bfill()

            final_df = df[~df.index.duplicated(keep='first')].sort_index()
            
            # Store in cache
            _DATA_CACHE[cache_key] = final_df.copy()
            _CACHE_TIME[cache_key] = time.time()
            return final_df
            
        except Exception as e:
            if attempt < max_retries - 1:
                wait_time = 2 ** attempt
                time.sleep(wait_time)
            else:
                raise e

# ==========================================
# 3. BACKTEST ENGINE
# ==========================================
class Position:
    def __init__(self, size, entry_price, entry_time, is_long):
        self.size = size
        self.entry_price = entry_price
        self.entry_time = entry_time
        self.is_long = is_long

class BaseStrategy:
    def __init__(self, full_data, cash, commission, leverage):
        self.full_data = full_data
        self.cash, self.commission, self.leverage = cash, commission, leverage
        self.position, self.closed_pnls, self.trade_history = None, [], []
        self.tp_price = self.sl_price = 0
        self.data_15m = None
        if len(full_data) > 1: self.duration = full_data.index[1] - full_data.index[0]
        else: self.duration = pd.Timedelta(hours=1)
        
    def buy(self, price, time, duration=pd.Timedelta(hours=1)):
        if self.position: return
        self.position = Position(0.99, price, time + duration, True)

    def sell(self, price, time, duration=pd.Timedelta(hours=1)):
        if self.position: return
        self.position = Position(0.99, price, time + duration, False)

    def _close(self, current_price, reason, exit_time):
        if not self.position: return
        pnl_pct = (current_price - self.position.entry_price) / self.position.entry_price if self.position.is_long else (self.position.entry_price - current_price) / self.position.entry_price
        final_pnl = (pnl_pct * self.leverage) - (self.commission * 2 * self.leverage)
        self.closed_pnls.append(final_pnl)
        
        entry_time_str = self.position.entry_time.isoformat() if hasattr(self.position.entry_time, 'isoformat') else str(self.position.entry_time)
        exit_time_str = exit_time.isoformat() if hasattr(exit_time, 'isoformat') else str(exit_time)
        
        self.trade_history.append({
            'entry_time': entry_time_str, 
            'exit_time': exit_time_str, 
            'type': 'LONG' if self.position.is_long else 'SHORT', 
            'entry_price': round(float(self.position.entry_price), 2), 
            'exit_price': round(float(current_price), 2), 
            'pnl': round(float(final_pnl * 100), 2), 
            'reason': reason
        })
        self.position = None
        self.tp_price = self.sl_price = 0
        return True

class MyStrategy(BaseStrategy):
    def init(self, logic_data, debug=False, mode='both'):
        self.logic = logic_data
        Config.MODE = mode
        self.compiled = {}
        for key in ['entry_long', 'entry_short', 'exit_long', 'exit_short']:
            if self.logic.get(key):
                s = self.logic[key].lower()
                s = re.sub(r'\b(and|or|not)\b', lambda m: m.group(0).lower(), s, flags=re.IGNORECASE)
                s = re.sub(r'\b(and|or|not)\s*$', '', s, flags=re.IGNORECASE).strip()
                self.logic[key] = s
                try:
                    self.compiled[key] = compile(s, '<string>', 'eval')
                except Exception:
                    self.compiled[key] = None
        self.processed_timestamps = set()
        self.freq_15m = pd.Timedelta(minutes=15)
        if self.data_15m is not None and len(self.data_15m) > 1:
            self.freq_15m = self.data_15m.index[1] - self.data_15m.index[0]

    def _check_tp_sl_time(self, t, h, l, c):
        if not self.position: return False
        
        if self.logic.get('time_limit_hours'):
            duration = (t - self.position.entry_time).total_seconds() / 3600
            if duration >= self.logic['time_limit_hours']:
                self._close(c, f"Time Exit ({self.logic['time_limit_hours']}h)", t)
                return True

        entry = self.position.entry_price
        if self.position.is_long:
            if (self.logic.get('sl_pct') and l <= entry * (1 - self.logic['sl_pct'])) or (self.sl_price and l <= self.sl_price):
                exit_p = self.sl_price or entry * (1 - self.logic['sl_pct'])
                self._close(exit_p, "SL Hit", t)
                return True
            if (self.logic.get('tp_pct') and h >= entry * (1 + self.logic['tp_pct'])) or (self.tp_price and h >= self.tp_price):
                exit_p = self.tp_price or entry * (1 + self.logic['tp_pct'])
                self._close(exit_p, "TP Hit", t)
                return True
        else:
            if (self.logic.get('sl_pct') and h >= entry * (1 + self.logic['sl_pct'])) or (self.sl_price and h >= self.sl_price):
                exit_p = self.sl_price or entry * (1 + self.logic['sl_pct'])
                self._close(exit_p, "SL Hit", t)
                return True
            if (self.logic.get('tp_pct') and l <= entry * (1 - self.logic['tp_pct'])) or (self.tp_price and l <= self.tp_price):
                exit_p = self.tp_price or entry * (1 - self.logic['tp_pct'])
                self._close(exit_p, "TP Hit", t)
                return True
        return False

    _EVAL_GLOBALS = {"__builtins__": {}, "short": "short", "long": "long", "buy": "buy", "sell": "sell"}

    def _safe_eval(self, key, ctx):
        if not self.compiled.get(key): return False
        try:
            return bool(eval(self.compiled[key], self._EVAL_GLOBALS, ctx))
        except Exception:
            return False

    def next(self, current_idx):
        t = self.full_data.index[current_idx]
        if t in self.processed_timestamps: return
        self.processed_timestamps.add(t)
        
        row = self.full_data.iloc[current_idx]
        duration = self.duration

        if is_downtime(t):
            if self.position: self._close(row['close'], "Downtime", t + duration)
            return

        if self.position:
            found_exit = False
            if self.data_15m is not None:
                sub = self.data_15m[(self.data_15m.index >= t) & (self.data_15m.index < t + duration)]
                if not sub.empty:
                    for t_sub, r_sub in sub.iterrows():
                        if self._check_tp_sl_time(t_sub + self.freq_15m, r_sub['high'], r_sub['low'], r_sub['close']):
                            found_exit = True
                            break
                else:
                    found_exit = self._check_tp_sl_time(t + duration, row['high'], row['low'], row['close'])
            else:
                found_exit = self._check_tp_sl_time(t + duration, row['high'], row['low'], row['close'])
            
            if found_exit: return

        ctx = {c.lower(): row[c.lower()] for c in self.logic['needed_cols'] if c.lower() in row}
        
        for i in range(1, 11):
            if current_idx >= i:
                prev_row = self.full_data.iloc[current_idx - i]
                for c in self.logic['needed_cols']:
                    lc = c.lower()
                    if lc in prev_row:
                        suffix = "_prev" if i == 1 else f"_prev{i}"
                        ctx[f"{lc}{suffix}"] = prev_row[lc]

        entry_price_val = self.position.entry_price if self.position else 0
        ctx.update({
            'close': row['close'], 'high': row['high'], 'low': row['low'],
            'price_value': row['close'], 'price': row['close'], 
            'entry_price': entry_price_val, 'entry': entry_price_val
        })

        if self.position and self.position.entry_time <= t:
            if self.position.is_long:
                if self._safe_eval('exit_long', ctx):
                    self._close(row['close'], "Exit Long Signal", t + duration)
            else:
                if self._safe_eval('exit_short', ctx):
                    self._close(row['close'], "Exit Short Signal", t + duration)

            if not self.position:
                ctx.update({'entry_price': 0, 'entry': 0}) 

        if not self.position:
            is_long  = self._safe_eval('entry_long', ctx) if Config.MODE in ['both','long'] else False
            is_short = self._safe_eval('entry_short', ctx) if Config.MODE in ['both','short'] else False

            if is_long:
                self.buy(row['close'], t, duration=duration)
                if self.logic.get('tp_atr_mult'): self.tp_price = row['close'] + (self.logic['tp_atr_mult'] * ctx.get(self.logic.get('atr_col', 'atr_value'), 0))
                if self.logic.get('sl_atr_mult'): self.sl_price = row['close'] - (self.logic['sl_atr_mult'] * ctx.get(self.logic.get('atr_col', 'atr_value'), 0))
            elif is_short:
                self.sell(row['close'], t, duration=duration)
                if self.logic.get('tp_atr_mult'): self.tp_price = row['close'] - (self.logic['tp_atr_mult'] * ctx.get(self.logic.get('atr_col', 'atr_value'), 0))
                if self.logic.get('sl_atr_mult'): self.sl_price = row['close'] + (self.logic['sl_atr_mult'] * ctx.get(self.logic.get('atr_col', 'atr_value'), 0))

def run_period(df, df_15m, logic, mode, cash, comm, margin):
    bt = MyStrategy(df, cash, comm, round(1/margin))
    bt.data_15m = df_15m
    bt.init(logic, mode=mode)
    
    length = len(df)
    for i in range(1, length):
        bt.next(i)
        
    if bt.position:
        bt._close(df['close'].iloc[-1], "Finalize", df.index[-1])
        
    pnls = bt.closed_pnls
    simple_return_pct = sum(pnls) * 100 if len(pnls) > 0 else 0
    compounded_return_pct = (np.prod([1 + p for p in pnls]) - 1) * 100 if len(pnls) > 0 else 0
    trades = len(pnls)
    wr = (len([p for p in pnls if p > 0]) / len(pnls) * 100) if pnls else 0
    
    pos_sum = sum([p for p in pnls if p > 0])
    neg_sum = abs(sum([p for p in pnls if p < 0]))
    pf = pos_sum / neg_sum if neg_sum > 0 else (10.0 if pos_sum > 0 else 0)
    
    cum_ret = np.cumprod([1 + p for p in pnls]) if pnls else [1]
    peak = np.maximum.accumulate(cum_ret)
    dd = (cum_ret - peak) / peak
    mdd = np.min(dd) * 100 if pnls else 0

    sharpe = 0.0
    if len(pnls) >= 2:
        trades_per_year = 252.0
        ann_mean = np.mean(pnls) * trades_per_year
        ann_std = np.std(pnls, ddof=1) * np.sqrt(trades_per_year)
        if ann_std > 1e-9:
            sharpe = ann_mean / ann_std
    
    return {
        'mode': mode,
        'return_pct': round(float(simple_return_pct), 2),
        'simple_return_pct': round(float(simple_return_pct), 2),
        'compounded_return_pct': round(float(compounded_return_pct), 2),
        'trades': trades,
        'winrate': round(float(wr), 2),
        'pf': round(float(pf), 2),
        'mdd': round(float(mdd), 2),
        'sharpe': round(float(sharpe), 2),
        'hist': bt.trade_history,
        '_pnls': pnls
    }


def build_trade_records_csv(trade_history):
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow([
        'entry_time', 'exit_time', 'type',
        'entry_price', 'exit_price', 'pnl', 'reason'
    ])

    for trade in trade_history:
        writer.writerow([
            trade.get('entry_time', ''),
            trade.get('exit_time', ''),
            trade.get('type', ''),
            trade.get('entry_price', ''),
            trade.get('exit_price', ''),
            trade.get('pnl', ''),
            trade.get('reason', ''),
        ])

    return buffer.getvalue()


def build_trade_records_csv_all(results):
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow([
        'period', 'mode', 'entry_time', 'exit_time', 'type',
        'entry_price', 'exit_price', 'pnl', 'reason'
    ])

    for res in results:
        period = res.get('period', '')
        mode = res.get('mode', '')
        for trade in res.get('hist', []):
            writer.writerow([
                period,
                mode,
                trade.get('entry_time', ''),
                trade.get('exit_time', ''),
                trade.get('type', ''),
                trade.get('entry_price', ''),
                trade.get('exit_price', ''),
                trade.get('pnl', ''),
                trade.get('reason', ''),
            ])

    return buffer.getvalue()


def build_performance_csv(performance_rows):
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(['period', 'mode', 'return_pct', 'trades', 'winrate', 'pf', 'mdd', 'sharpe'])
    for r in performance_rows:
        writer.writerow([
            r.get('period', ''),
            r.get('mode', ''),
            r.get('return_pct', 0),
            r.get('trades', 0),
            r.get('winrate', 0),
            r.get('pf', 0),
            r.get('mdd', 0),
            r.get('sharpe', 0),
        ])
    return buffer.getvalue()


def build_report_text(performance_rows, primary_history):
    periods = ['6 Months', '3 Months', '1 Month', '1 Week', '1 Day']
    modes = ['both', 'long', 'short']

    by_key = {(r.get('period'), r.get('mode')): r for r in performance_rows}

    lines = []
    lines.append("=" * 105)
    lines.append(f"{'MARKET PHASE ADAPTIVE STRATEGY - Performance Report':^105}")
    lines.append("=" * 105)
    lines.append(f"{'Period':<12} | {'Mode':<6} | {'Return':>10} | {'Trd':>4} | {'Win Rate':>8} | {'PF':>5} | {'MDD%':>8} | {'Sharpe':>7}")
    lines.append("-" * 105)

    for period in periods:
        for i, mode in enumerate(modes):
            r = by_key.get((period, mode), {})
            ret = float(r.get('return_pct', 0) or 0)
            trades = int(r.get('trades', 0) or 0)
            wr = float(r.get('winrate', 0) or 0)
            pf = float(r.get('pf', 0) or 0)
            mdd = float(r.get('mdd', 0) or 0)
            sharpe = float(r.get('sharpe', 0) or 0)
            period_label = period if i == 0 else ''
            mode_label = mode if mode == 'both' else ('long ↑' if mode == 'long' else 'short ↓')
            lines.append(
                f"{period_label:<12} | {mode_label:<6} | {ret:>+9.2f}% | {trades:>4} | {wr:>7.2f}% | {pf:>5.2f} | {mdd:>7.2f}% | {sharpe:>7.2f}"
            )
        lines.append("-" * 105)

    lines.append("")
    lines.append("=" * 105)
    lines.append(f"{'TRADE HISTORY (6 MONTHS - BOTH)':^105}")
    lines.append("=" * 105)
    lines.append(f"{'Entry Time':<20} | {'Exit Time':<20} | {'Type':<7} | {'Entry':>10} | {'Exit':>10} | {'PnL %':>8} | Reason")
    lines.append("-" * 105)

    for t in primary_history:
        entry_time = str(t.get('entry_time', ''))[:19]
        exit_time = str(t.get('exit_time', ''))[:19]
        ttype = str(t.get('type', ''))
        entry = float(t.get('entry_price', 0) or 0)
        exitp = float(t.get('exit_price', 0) or 0)
        pnl = float(t.get('pnl', 0) or 0)
        reason = str(t.get('reason', ''))
        lines.append(f"{entry_time:<20} | {exit_time:<20} | {ttype:<7} | {entry:>10.2f} | {exitp:>10.2f} | {pnl:>+7.2f}% | {reason}")

    lines.append("-" * 105)
    return "\n".join(lines)


from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

app = FastAPI()


def extract_tp_sl(text: str) -> Dict[str, Any]:
    res = {
        "tp_atr_mult": None,
        "sl_atr_mult": None,
        "tp_pct": None,
        "sl_pct": None,
        "atr_col": "atr_value",
        "time_limit_hours": None,
    }
    if not text:
        return res

    sl_atr = re.search(r"(?:sl|stop loss).*?([\d\.]+)\s*\*\s*(atr\w*)", text, re.IGNORECASE)
    if sl_atr:
        res["sl_atr_mult"] = float(sl_atr.group(1))
        res["atr_col"] = sl_atr.group(2)

    tp_atr = re.search(r"(?:tp|take profit).*?([\d\.]+)\s*\*\s*(atr\w*)", text, re.IGNORECASE)
    if tp_atr:
        res["tp_atr_mult"] = float(tp_atr.group(1))
        res["atr_col"] = tp_atr.group(2)

    tp_pct = re.search(r"(?:tp|take profit).*?([\d\.]+)\s*%", text, re.IGNORECASE)
    if tp_pct:
        res["tp_pct"] = float(tp_pct.group(1)) / 100

    sl_pct = re.search(r"(?:sl|stop loss).*?([\d\.]+)\s*%", text, re.IGNORECASE)
    if sl_pct:
        res["sl_pct"] = float(sl_pct.group(1)) / 100

    time_match = re.search(r"(\d+)\s*(?:h|hour)", text, re.IGNORECASE)
    if time_match:
        res["time_limit_hours"] = int(time_match.group(1))

    return res


def clean_logic(text: str) -> str:
    if not text:
        return ""
    if "|" not in text:
        return text
    parts = [p.strip() for p in text.split("|")]
    for p in parts:
        if re.search(r"[<>=]|prev|diff|crosses|between", p, re.IGNORECASE):
            return p
    return parts[0] if parts else ""


def _balanced_paren_content(s: str):
    start = s.find("(")
    if start == -1:
        return None
    depth = 0
    for i in range(start, len(s)):
        if s[i] == "(":
            depth += 1
        elif s[i] == ")":
            depth -= 1
            if depth == 0:
                return s[start + 1:i], i + 1
    return None


def to_python_logic(logic_str: str, side: str = "") -> str:
    if not logic_str:
        return ""

    logic = logic_str.strip()
    logic = re.sub(r"/\*[\s\S]*?\*/", "", logic)
    logic = re.sub(r"//.*", "", logic)

    labels = [
        "Logic:", "Entry Logic:", "Exit Logic:", "Entry Short:", "Entry Long:",
        "Exit Short:", "Exit Long:", "Short Entry:", "Long Entry:", "Short Exit:",
        "Long Exit:", "LONG:", "SHORT:", "TP:", "SL:", "Exit:",
        "Stop Loss:", "Take Profit:",
    ]

    cleaned_lines = []
    for line in logic.split("\n"):
        l = line.strip()
        l = re.sub(r"^(const|let|var)\s+\w+\s*=\s*", "", l)
        for label in labels:
            if l.lower().startswith(label.lower()):
                l = l[len(label):].strip()
        if l:
            cleaned_lines.append(l)
    logic = " ".join(cleaned_lines).strip()

    if re.match(r"^\s*if\s*\b", logic, re.IGNORECASE):
        balanced = _balanced_paren_content(logic)
        if balanced:
            content, end_idx = balanced
            remainder = logic[end_idx:].strip()
            if remainder.startswith("{") or re.search(r"\breturn\b", remainder, re.IGNORECASE):
                logic = content.strip()
            else:
                logic = re.sub(r"^\s*if\s*\b", "", logic, flags=re.IGNORECASE).strip()
        else:
            logic = re.sub(r"^\s*if\s*\b", "", logic, flags=re.IGNORECASE).strip()
    elif "{" in logic and "}" in logic:
        body = re.search(r"\{([\s\S]*)\}", logic)
        if body:
            content = body.group(1).strip()
            ret = re.search(r"return\s+([\s\S]*?);?\s*$", content, re.IGNORECASE)
            logic = ret.group(1) if ret else content

    logic = re.sub(r"historical\[(\d+)\]\.(\w+)", r"\2_prev\1", logic)
    logic = re.sub(r"current\.(\w+)", r"\1", logic)
    logic = re.sub(r"(\w+)\.diff\(\)\s*>\s*0", r"(\1 > \1_prev)", logic, flags=re.IGNORECASE)
    logic = re.sub(r"(\w+)\.diff\(\)\s*<\s*0", r"(\1 < \1_prev)", logic, flags=re.IGNORECASE)
    logic = re.sub(r"\bAND\b", "and", logic, flags=re.IGNORECASE)
    logic = re.sub(r"\bOR\b", "or", logic, flags=re.IGNORECASE)
    logic = re.sub(r"\bNOT\b", "not", logic, flags=re.IGNORECASE)

    def crosses_repl(match):
        ind, op, target = match.group(1), match.group(3), match.group(4)
        is_col = re.match(r"^[a-zA-Z_]\w*$", target) is not None
        target_prev = f"{target}_prev" if is_col else target
        if "above" in op.lower():
            return f"({ind} > {target} and {ind}_prev <= {target_prev})"
        return f"({ind} < {target} and {ind}_prev >= {target_prev})"

    logic = re.sub(r"(\w+)\s+(crosses\s+(above|below))\s+([\w\d\.-]+)", crosses_repl, logic, flags=re.IGNORECASE)

    if side:
        parts = re.split(r"\b(OR|AND|or|and)\b", logic)
        rebuilt = []
        for p in parts:
            trimmed = p.strip()
            if re.match(r"^(OR|AND|or|and)$", trimmed):
                rebuilt.append(trimmed)
                continue
            if re.match(r"^(\d+(\.\d+)?%?|fixed\s*\d+(\.\d+)?%?)$", re.sub(r"^(tp|sl|stop loss|take profit|exit):?\s*", "", trimmed, flags=re.IGNORECASE).lower()):
                continue
            if ("entry_price" in trimmed.lower() or "sl" in trimmed.lower() or "tp" in trimmed.lower()) and not re.search(r"[<>=]", trimmed):
                adjusted = re.sub(r"\)+$", "", trimmed)
                cmp = "<=" if side == "long" else ">="
                rebuilt.append(f"(price_value {cmp} {adjusted})")
            else:
                rebuilt.append(p)
        logic = " ".join([x for x in rebuilt if x.strip()])

    logic = re.sub(r"\b(const|let|var)\b", "", logic)
    logic = logic.replace("%", "")
    logic = re.sub(r"[.`*;]+$", "", logic).strip()

    open_paren = logic.count("(")
    close_paren = logic.count(")")
    while close_paren > open_paren:
        logic = re.sub(r"\)(?=[^)]*$)", "", logic)
        close_paren -= 1
    while open_paren > close_paren:
        logic += ")"
        close_paren += 1

    return logic


def get_needed_cols(logic_strings: List[str], schema_cols: List[str]) -> List[str]:
    found = set()
    reserved = {
        "and", "or", "not", "if", "else", "true", "false", "none", "entry_price",
        "price_value", "tp", "sl", "time", "exit", "force", "condition", "atr", "below",
        "above", "crosses", "return", "long", "short",
    }

    for s in logic_strings:
        words = re.findall(r"\b[a-zA-Z_]\w*\b", s or "")
        for w in words:
            clean_w = re.sub(r"_prev\d*$", "", w)
            if clean_w.lower() not in reserved and not clean_w.isnumeric():
                found.add(clean_w)

    for std in ["open", "high", "low", "close", "volume", "timestamp", "atr_value"]:
        found.add(std)

    if schema_cols:
        schema_map = {s.lower(): s for s in schema_cols}
        return [schema_map.get(c.lower(), c) for c in found]
    return list(found)


def parse_input_payload(payload: Dict[str, Any]):
    entry_long = payload.get("Entry Long") or payload.get("entry_long") or ""
    entry_short = payload.get("Entry Short") or payload.get("entry_short") or ""
    exit_long = payload.get("Exit Long") or payload.get("exit_long") or ""
    exit_short = payload.get("Exit Short") or payload.get("exit_short") or ""
    table_name = payload.get("Table Name") or payload.get("table") or "BTC_4H_TAAPI_Indicator_snapshot"
    exit_table = payload.get("Supporting Table") or payload.get("exit_table") or "BTC_1H_TAAPI_Indicator_snapshot"
    schema_cols = payload.get("schema_cols") or ["close", "high", "low", "open", "volume", "timestamp", "atr_value"]

    commission = float(payload.get("commission", 0.0005))
    cash = float(payload.get("cash", 100000))
    margin = float(payload.get("margin", 0.33))

    exit_long_clean = clean_logic(exit_long)
    exit_short_clean = clean_logic(exit_short)

    logic = {
        "entry_long": to_python_logic(entry_long, "long"),
        "entry_short": to_python_logic(entry_short, "short"),
        "exit_long": to_python_logic(exit_long_clean, "long"),
        "exit_short": to_python_logic(exit_short_clean, "short"),
        "tp_atr_mult": None,
        "sl_atr_mult": None,
        "tp_pct": None,
        "sl_pct": None,
        "atr_col": "atr_value",
        "time_limit_hours": None,
    }

    tp_sl_long = extract_tp_sl(exit_long)
    tp_sl_short = extract_tp_sl(exit_short)
    for k in ["tp_atr_mult", "sl_atr_mult", "tp_pct", "sl_pct", "atr_col", "time_limit_hours"]:
        logic[k] = tp_sl_long.get(k) or tp_sl_short.get(k) or logic[k]

    logic["needed_cols"] = get_needed_cols(
        [logic["entry_long"], logic["entry_short"], logic["exit_long"], logic["exit_short"]],
        schema_cols,
    )

    return logic, table_name, exit_table, commission, cash, margin

class BacktestRequest(BaseModel):
    logic: Dict[str, Any]
    table: str = "BTC_1H_TAAPI_Indicator_snapshot"
    exit_table: str = "BTC_15min_TAAPI_Indicator_snapshot"
    commission: float = 0.0003
    cash: float = 1000000.0
    margin: float = 0.33

@app.post("/backtest")
def run_backtest(req: BacktestRequest):
    if not Config.SUPABASE_URL or not Config.SUPABASE_KEY:
        raise HTTPException(status_code=500, detail="Missing SUPABASE_URL or SUPABASE_KEY")

    logic = req.logic
    try:
        df = load_data(Config.SUPABASE_URL, Config.SUPABASE_KEY, req.table, logic['needed_cols'])
        try:
            df_15m = load_data(Config.SUPABASE_URL, Config.SUPABASE_KEY, req.exit_table, logic['needed_cols'])
        except Exception:
            df_15m = None

        periods = [
            ('6 Months', 180),
            ('3 Months', 90),
            ('1 Month', 30),
            ('1 Week', 7),
            ('1 Day', 1),
        ]
        results = []
        primary_result = None
        for label, days in periods:
            cutoff = df.index[-1] - pd.Timedelta(days=days)
            pdf = df[df.index >= cutoff]
            if pdf.empty:
                continue

            period_df_15m = None
            if df_15m is not None:
                period_df_15m = df_15m[df_15m.index >= pdf.index[0]]

            for mode in ['both', 'long', 'short']:
                res = run_period(pdf, period_df_15m, logic, mode, req.cash, req.commission, req.margin)
                # Match original n8n report display logic.
                if label == '6 Months':
                    res['return_pct'] = res.get('simple_return_pct', res['return_pct'])
                else:
                    res['return_pct'] = res.get('compounded_return_pct', res['return_pct'])
                res.pop('_pnls', None)
                res['period'] = label
                results.append(res)
                if label == '6 Months' and mode == 'both':
                    primary_result = res

        if primary_result is None and results:
            primary_result = results[0]

        if primary_result is None:
            primary_result = {
                'period': '6 Months',
                'mode': 'both',
                'return_pct': 0,
                'trades': 0,
                'winrate': 0,
                'pf': 0,
                'mdd': 0,
                'hist': [],
            }

        trade_history = primary_result.get('hist', [])
        performance_primary = {
            'period': primary_result.get('period', '6 Months'),
            'mode': primary_result.get('mode', 'both'),
            'return_pct': primary_result.get('return_pct', 0),
            'trades': primary_result.get('trades', 0),
            'winrate': primary_result.get('winrate', 0),
            'pf': primary_result.get('pf', 0),
            'mdd': primary_result.get('mdd', 0),
        }

        performance_all = [
            {
                'period': r.get('period', ''),
                'mode': r.get('mode', ''),
                'return_pct': r.get('return_pct', 0),
                'trades': r.get('trades', 0),
                'winrate': r.get('winrate', 0),
                'pf': r.get('pf', 0),
                'mdd': r.get('mdd', 0),
                'sharpe': r.get('sharpe', 0),
            }
            for r in results
        ]

        full_trade_history = []
        for r in results:
            for trade in r.get('hist', []):
                full_trade_history.append({
                    'period': r.get('period', ''),
                    'mode': r.get('mode', ''),
                    'entry_time': trade.get('entry_time', ''),
                    'exit_time': trade.get('exit_time', ''),
                    'type': trade.get('type', ''),
                    'entry_price': trade.get('entry_price', ''),
                    'exit_price': trade.get('exit_price', ''),
                    'pnl': trade.get('pnl', ''),
                    'reason': trade.get('reason', ''),
                })

        # Required output: trade history for BOTH mode in last 6 months only.
        trade_history_6m_both = []
        for r in results:
            if r.get('period') == '6 Months' and r.get('mode') == 'both':
                trade_history_6m_both = r.get('hist', [])
                break

        performance_csv = build_performance_csv(performance_all)
        trade_history_csv = build_trade_records_csv(trade_history_6m_both)
        report_text = build_report_text(performance_all, trade_history)
        return {
            "success": True,
            "performance_csv": performance_csv,
            "trade_history_csv": trade_history_csv,
            "report_text": report_text,
        }
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/webhook/backtest")
def webhook_backtest(payload: Dict[str, Any]):
    logic, table_name, exit_table, commission, cash, margin = parse_input_payload(payload)
    req = BacktestRequest(
        logic=logic,
        table=table_name,
        exit_table=exit_table,
        commission=commission,
        cash=cash,
        margin=margin,
    )
    return run_backtest(req)

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=int(os.getenv("PORT", "8080")))
