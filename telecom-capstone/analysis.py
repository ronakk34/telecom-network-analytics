from pathlib import Path
import sqlite3
import numpy as np
import pandas as pd

REQUIRED_FILES = {
    "subscribers": "capstone_subscribers.csv",
    "kpi": "capstone_network_kpi.csv",
    "maintenance": "capstone_maintenance_windows.csv",
    "traffic": "capstone_cell_traffic_monthly.csv",
}

def _read_csv(data_dir, filename):
    path = Path(data_dir) / filename
    if not path.exists():
        raise FileNotFoundError(
            f"Missing {path}. Put the four capstone CSV files in the data/ folder."
        )
    return pd.read_csv(path)

def load_sources(data_dir="data"):
    return {
        name: _read_csv(data_dir, filename)
        for name, filename in REQUIRED_FILES.items()
    }

def prepare_subscribers(raw):
    s = raw.copy()
    report = {
        "rows_before": len(s),
        "distinct_customer_ids": s["customer_id"].nunique(),
        "duplicate_customer_ids": int(s.duplicated(["customer_id"]).sum()),
    }

    if "region" in s:
        s["region"] = s["region"].astype("string").str.strip().str.title()

    s = s.drop_duplicates(subset=["customer_id"], keep="first").copy()

    for col in ["avg_monthly_gb", "days_since_last_recharge"]:
        if col in s:
            s[col + "_was_missing"] = s[col].isna().astype(int)
            s[col] = pd.to_numeric(s[col], errors="coerce")
            median = s[col].median()
            s[col] = s[col].fillna(0 if pd.isna(median) else median)

    report["rows_after"] = len(s)
    report["rows_removed"] = report["rows_before"] - report["rows_after"]
    report["regions_after"] = int(s["region"].nunique()) if "region" in s else None
    report["imputed_avg_monthly_gb"] = int(s.get("avg_monthly_gb_was_missing", pd.Series(dtype=int)).sum())
    report["imputed_days_since_recharge"] = int(s.get("days_since_last_recharge_was_missing", pd.Series(dtype=int)).sum())
    return s, report

def prepare_kpi(raw, maintenance):
    kp = raw.copy()
    report = {
        "rows_before": len(kp),
        "distinct_cells": int(kp["cell_id"].nunique()),
        "duplicate_cell_timestamp": int(kp.duplicated(["cell_id", "reading_ts"]).sum()),
        "blank_latency_before_dedup": int(kp["latency_ms"].isna().sum()),
        "negative_throughput": int((pd.to_numeric(kp["throughput_mbps"], errors="coerce") < 0).sum()),
        "downtime_rows": int((pd.to_numeric(kp["downtime_min"], errors="coerce").fillna(0) > 0).sum()),
    }

    kc = kp.drop_duplicates(["cell_id", "reading_ts"], keep="first").copy()
    kc["ts"] = pd.to_datetime(kc["reading_ts"], errors="coerce")
    for col in ["latency_ms", "throughput_mbps", "downtime_min",
                "drop_call_rate_pct", "active_users"]:
        if col in kc:
            kc[col] = pd.to_numeric(kc[col], errors="coerce")

    mw = maintenance.copy()
    mw["window_start"] = pd.to_datetime(mw["window_start"], errors="coerce")
    mw["window_end"] = pd.to_datetime(mw["window_end"], errors="coerce")

    in_maint = pd.Series(False, index=kc.index)
    for _, w in mw.iterrows():
        in_maint |= (
            (kc["cell_id"] == w["cell_id"]) &
            (kc["ts"] >= w["window_start"]) &
            (kc["ts"] < w["window_end"])
        )

    blank = kc["latency_ms"].isna()
    outage = blank & ~in_maint & (kc["downtime_min"].fillna(0) > 0)

    report.update({
        "rows_after_dedup": len(kc),
        "blank_latency_after_dedup": int(blank.sum()),
        "blank_inside_maintenance": int((blank & in_maint).sum()),
        "blank_during_unplanned_outage": int(outage.sum()),
        "blank_unexplained_collector_loss": int((blank & ~in_maint & ~outage).sum()),
    })

    kc["in_maintenance"] = in_maint.astype(bool)
    kc["latency_missing_reason"] = np.select(
        [
            blank & in_maint,
            outage,
            blank & ~in_maint & ~outage,
        ],
        [
            "Planned maintenance",
            "Unplanned outage",
            "Unexplained collector loss",
        ],
        default="Observed"
    )

    # A negative throughput value is treated as an invalid counter rollover.
    negative = kc["throughput_mbps"] < 0
    kc.loc[negative, "throughput_mbps"] = np.nan

    return kp, kc, mw, report

def load_kpi_to_sqlite(kc, db_path="capstone_rt.db"):
    con = sqlite3.connect(db_path)
    con.executescript("""
    CREATE TABLE IF NOT EXISTS kpi_readings (
        reading_ts TEXT NOT NULL,
        cell_id TEXT NOT NULL,
        region TEXT,
        latency_ms REAL,
        throughput_mbps REAL,
        downtime_min INTEGER NOT NULL DEFAULT 0,
        drop_call_rate_pct REAL,
        active_users INTEGER,
        in_maintenance INTEGER NOT NULL DEFAULT 0,
        latency_missing_reason TEXT,
        UNIQUE(cell_id, reading_ts)
    );
    CREATE INDEX IF NOT EXISTS ix_cell_ts
    ON kpi_readings(cell_id, reading_ts);
    """)
    cols = [
        "reading_ts", "cell_id", "region", "latency_ms",
        "throughput_mbps", "downtime_min", "drop_call_rate_pct",
        "active_users", "in_maintenance", "latency_missing_reason"
    ]
    frame = kc.copy()
    frame["in_maintenance"] = frame["in_maintenance"].astype(int)
    frame = frame[cols].astype(object).where(pd.notna(frame[cols]), None)
    records = frame.values.tolist()
    con.executemany("""
        INSERT OR REPLACE INTO kpi_readings
        (reading_ts, cell_id, region, latency_ms, throughput_mbps,
         downtime_min, drop_call_rate_pct, active_users,
         in_maintenance, latency_missing_reason)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, records)
    con.commit()
    count = con.execute("SELECT COUNT(*) FROM kpi_readings").fetchone()[0]
    con.close()
    return count

def make_hourly(kc):
    d = kc.copy()
    d["hour"] = d["ts"].dt.floor("h")
    return (d.groupby(["cell_id", "hour"], as_index=False)
             .agg(avg_latency=("latency_ms", "mean"),
                  peak_latency=("latency_ms", "max"),
                  readings=("latency_ms", "count")))

def incident_grain_metrics(kc, cell_id="CELL-DEL-03", date="2026-08-03"):
    d = kc[
        (kc["cell_id"] == cell_id) &
        (kc["reading_ts"].astype(str).str.startswith(date))
    ].copy()
    if d.empty:
        return pd.DataFrame(), {
            "peak_15m": np.nan, "highest_4h_mean": np.nan,
            "understated_pct": np.nan, "raw_breaches_100ms": 0,
            "aggregate_breaches_100ms": 0
        }

    d["hour_num"] = d["ts"].dt.hour
    d["four_hour_bucket"] = (d["hour_num"] // 4)
    four = (d.groupby("four_hour_bucket", as_index=False)
             .agg(avg_latency=("latency_ms", "mean"),
                  peak_latency=("latency_ms", "max"),
                  readings=("latency_ms", "count")))
    peak = d["latency_ms"].max()
    high4 = four["avg_latency"].max()
    metrics = {
        "peak_15m": float(peak) if pd.notna(peak) else np.nan,
        "highest_4h_mean": float(high4) if pd.notna(high4) else np.nan,
        "understated_pct": float((peak-high4)/peak*100) if pd.notna(peak) and peak != 0 and pd.notna(high4) else np.nan,
        "raw_breaches_100ms": int((d["latency_ms"] > 100).sum()),
        "aggregate_breaches_100ms": int(high4 > 100) if pd.notna(high4) else 0,
    }
    return four, metrics

def build_alerts(kp, kc, maintenance, threshold=70):
    raw_condition = (
        (pd.to_numeric(kp["latency_ms"], errors="coerce") > threshold) |
        (pd.to_numeric(kp["downtime_min"], errors="coerce").fillna(0) > 0)
    )
    dedup_condition = (
        (kc["latency_ms"] > threshold) |
        (kc["downtime_min"].fillna(0) > 0)
    )
    suppressed_condition = dedup_condition & ~kc["in_maintenance"]

    fire = kc.loc[suppressed_condition].sort_values(["cell_id", "ts"]).copy()
    runs = []
    for cell, group in fire.groupby("cell_id"):
        times = group["ts"].dropna().tolist()
        if not times:
            continue
        start = prev = times[0]
        n = 1
        for current in times[1:]:
            if current - prev == pd.Timedelta(minutes=15):
                n += 1
            else:
                runs.append({"cell_id": cell, "start": start, "intervals": n})
                start = current
                n = 1
            prev = current
        runs.append({"cell_id": cell, "start": start, "intervals": n})

    persistent = pd.DataFrame([r for r in runs if r["intervals"] >= 3])
    if persistent.empty:
        persistent = pd.DataFrame(columns=["cell_id", "start", "intervals"])

    stages = pd.DataFrame([
        {"stage": "Raw rule", "alerts_or_intervals": int(raw_condition.sum()), "removed": 0},
        {"stage": "After de-duplication", "alerts_or_intervals": int(dedup_condition.sum()),
         "removed": int(raw_condition.sum() - dedup_condition.sum())},
        {"stage": "After maintenance suppression", "alerts_or_intervals": int(suppressed_condition.sum()),
         "removed": int(dedup_condition.sum() - suppressed_condition.sum())},
        {"stage": "After 3-interval persistence", "alerts_or_intervals": int(persistent["intervals"].sum()),
         "removed": int(suppressed_condition.sum() - persistent["intervals"].sum())},
        {"stage": "After hysteresis grouping", "alerts_or_intervals": int(len(persistent)),
         "removed": int(persistent["intervals"].sum() - len(persistent))},
    ])
    return stages, persistent

def forecast_traffic(raw_traffic, horizon=6):
    t = raw_traffic.copy()
    t["Month"] = pd.to_datetime(t["Month"].astype(str), errors="coerce")
    t["Traffic_GB"] = pd.to_numeric(t["Traffic_GB"], errors="coerce")
    t["Capacity_GB"] = pd.to_numeric(t["Capacity_GB"], errors="coerce")
    t = t.dropna(subset=["Cell", "Month"]).sort_values(["Cell", "Month"])
    t["Traffic_GB"] = t.groupby("Cell")["Traffic_GB"].transform(
        lambda x: x.interpolate().bfill().ffill()
    )

    results = []
    for cell, g in t.groupby("Cell"):
        g = g.sort_values("Month").reset_index(drop=True)
        valid = g["Traffic_GB"].dropna()
        capacity = g["Capacity_GB"].dropna()
        if len(valid) < 2 or capacity.empty:
            continue

        y = np.log(np.maximum(g["Traffic_GB"].to_numpy(dtype=float), 1e-9))
        x = np.arange(len(y))
        slope, intercept = np.polyfit(x, y, 1)
        residual = y - (intercept + slope*x)
        month_numbers = g["Month"].dt.month.to_numpy()
        seasonal = pd.Series(residual).groupby(month_numbers).mean()

        future_months = pd.date_range(
            g["Month"].iloc[-1] + pd.offsets.MonthBegin(1),
            periods=horizon, freq="MS"
        )
        fx = np.arange(len(y), len(y) + horizon)
        seasonal_component = np.array([seasonal.get(m.month, 0.0) for m in future_months])
        forecast = np.exp(intercept + slope*fx + seasonal_component)
        cap = float(capacity.iloc[-1])

        for month, value in zip(future_months, forecast):
            results.append({
                "Cell": cell,
                "Month": month,
                "Forecast_GB": round(float(value), 1),
                "Capacity_GB": cap,
                "util_pct": round(float(value / cap * 100), 1) if cap > 0 else np.nan,
            })

    return pd.DataFrame(results), t

def revenue_risk(subscribers, kc):
    s = subscribers.copy()
    required = {"customer_id", "home_cell_id", "arpu"}
    if not required.issubset(s.columns):
        return pd.DataFrame()

    s["arpu"] = pd.to_numeric(s["arpu"], errors="coerce")
    exposure = (s.dropna(subset=["home_cell_id"])
                  .groupby("home_cell_id")
                  .agg(subscribers=("customer_id", "nunique"),
                       mean_arpu=("arpu", "mean"))
                  .reset_index()
                  .rename(columns={"home_cell_id": "cell_id"}))
    exposure["monthly_revenue_proxy"] = exposure["subscribers"] * exposure["mean_arpu"]

    quality = (kc.groupby("cell_id")
                 .agg(p95_latency=("latency_ms", lambda x: x.quantile(0.95)),
                      avg_latency=("latency_ms", "mean"),
                      downtime_intervals=("downtime_min", lambda x: int((x.fillna(0) > 0).sum())),
                      max_latency=("latency_ms", "max"))
                 .reset_index())

    risk = quality.merge(exposure, on="cell_id", how="left")
    risk["degraded"] = risk["p95_latency"] > 70
    risk["revenue_at_risk_proxy"] = np.where(
        risk["degraded"], risk["monthly_revenue_proxy"], 0
    )
    return risk.sort_values("revenue_at_risk_proxy", ascending=False)

def prepare_all(data_dir="data", db_path="capstone_rt.db"):
    src = load_sources(data_dir)
    subscribers, sub_report = prepare_subscribers(src["subscribers"])
    kp, kc, maintenance, kpi_report = prepare_kpi(src["kpi"], src["maintenance"])
    db_count = load_kpi_to_sqlite(kc, db_path)
    forecast, traffic_clean = forecast_traffic(src["traffic"])
    alert_stages, persistent_alerts = build_alerts(kp, kc, maintenance)
    revenue = revenue_risk(subscribers, kc)

    return {
        "sources": src,
        "subscribers": subscribers,
        "kpi_raw": kp,
        "kpi": kc,
        "maintenance": maintenance,
        "traffic": traffic_clean,
        "forecast": forecast,
        "alert_stages": alert_stages,
        "persistent_alerts": persistent_alerts,
        "revenue": revenue,
        "subscriber_report": sub_report,
        "kpi_report": kpi_report,
        "db_count": db_count,
    }
