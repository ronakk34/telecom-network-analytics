# Telecom Network Analytics — Track B

A Streamlit dashboard for the Advanced AI & ML Telecom Data Analytics capstone.

## Project structure

```text
telecom-capstone/
├── app.py
├── analysis.py
├── requirements.txt
├── README.md
├── capstone_rt.db          # created automatically when the app runs
├── data/
│   ├── capstone_subscribers.csv
│   ├── capstone_network_kpi.csv
│   ├── capstone_maintenance_windows.csv
│   └── capstone_cell_traffic_monthly.csv
└── notebooks/
    └── telecom_analysis.ipynb
```

## Setup

1. Put all four supplied CSV files in `data/`.
2. Create and activate a Python virtual environment (optional but recommended).
3. Install dependencies:

   ```bash
   pip install -r requirements.txt
   ```

4. Run:

   ```bash
   streamlit run app.py
   ```

## Dashboard pages

- Executive Overview
- Data Quality (B1–B4)
- Network Analysis (B5–B6)
- Alert Monitoring (B7)
- Traffic Forecast (B8)
- Revenue Optimisation (B9)

## Important assumptions

- Subscriber deduplication keeps the first row for each `customer_id`.
- KPI deduplication keeps the first row for each `(cell_id, reading_ts)`.
- Negative throughput values are treated as invalid counter rollovers and set to NULL.
- Missing latency values are classified as planned maintenance, unplanned outage, or unexplained collector loss.
- Alerts use a 70 ms latency threshold OR positive downtime, suppress exact maintenance windows, and require 3 consecutive 15-minute intervals.
- Forecasting uses log-linear trend plus month-of-year residual seasonality.
- Revenue-at-risk is a prioritisation proxy (subscriber count × mean ARPU for cells with p95 latency > 70 ms), not measured lost revenue.

## Expected CSV columns

### capstone_subscribers.csv
`customer_id`, `region`, `avg_monthly_gb`, `days_since_last_recharge`, `home_cell_id`, `arpu`

### capstone_network_kpi.csv
`reading_ts`, `cell_id`, `region`, `latency_ms`, `throughput_mbps`, `downtime_min`, `drop_call_rate_pct`, `active_users`

### capstone_maintenance_windows.csv
`cell_id`, `window_start`, `window_end`

### capstone_cell_traffic_monthly.csv
`Cell`, `Month`, `Traffic_GB`, `Capacity_GB`

The app will show a clear error if a required file or column is missing. Compare the expected column names with the supplied CSV headers if that happens.
