from pathlib import Path
import streamlit as st
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go

from analysis import (
    prepare_all, make_hourly, incident_grain_metrics
)

st.set_page_config(
    page_title="Telecom Network Analytics",
    page_icon="📡",
    layout="wide"
)

st.title("📡 Telecom Network Analytics")
st.caption("Track B · Network Quality and Revenue Optimisation")

BASE_DIR = Path(__file__).resolve().parent

@st.cache_data(show_spinner="Loading and preparing capstone datasets...")
def get_data():
    return prepare_all(
        data_dir=BASE_DIR / "data",
        db_path=BASE_DIR / "capstone_rt.db"
    )

try:
    data = get_data()
except Exception as exc:
    st.error(f"Could not load the project data: {exc}")
    st.info("Check that all four CSV files are inside the data/ folder and that their column names match the capstone files.")
    st.stop()

# @st.cache_data(show_spinner="Loading and preparing capstone datasets...")
# def get_data():
#     return prepare_all(data_dir="data", db_path="capstone_rt.db")

# try:
#     data = get_data()
# except Exception as exc:
#     st.error(f"Could not load the project data: {exc}")
#     st.info("Check that all four CSV files are inside the data/ folder and that their column names match the capstone files.")
#     st.stop()

pages = [
    "Executive Overview",
    "Data Quality",
    "Network Analysis",
    "Alert Monitoring",
    "Traffic Forecast",
    "Revenue Optimisation",
]
page = st.sidebar.radio("Dashboard pages", pages)
st.sidebar.divider()
st.sidebar.caption("Filters")

kpi = data["kpi"].copy()
cells = sorted(kpi["cell_id"].dropna().unique().tolist())
regions = sorted(kpi["region"].dropna().astype(str).unique().tolist()) if "region" in kpi else []

selected_region = st.sidebar.selectbox("Region", ["All"] + regions)
selected_cell = st.sidebar.selectbox("Cell", ["All"] + cells)

filtered = kpi.copy()
if selected_region != "All" and "region" in filtered:
    filtered = filtered[filtered["region"].astype(str) == selected_region]
if selected_cell != "All":
    filtered = filtered[filtered["cell_id"] == selected_cell]

if page == "Executive Overview":
    st.subheader("Network health at a glance")
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("KPI readings", f"{len(filtered):,}")
    avg_latency = filtered["latency_ms"].mean()
    c2.metric("Average latency", f"{avg_latency:.2f} ms" if pd.notna(avg_latency) else "N/A")
    c3.metric("Downtime", f"{filtered['downtime_min'].fillna(0).sum():,.0f} min")
    c4.metric("Cells monitored", f"{filtered['cell_id'].nunique():,}")

    c5, c6, c7 = st.columns(3)
    c5.metric("Persistent alert groups", len(data["persistent_alerts"]))
    high_util = data["forecast"].query("util_pct > 85")["Cell"].nunique() if not data["forecast"].empty else 0
    c6.metric("Cells forecast above 85%", high_util)
    revenue_total = data["revenue"]["revenue_at_risk_proxy"].sum() if not data["revenue"].empty else 0
    c7.metric("Revenue-at-risk proxy", f"₹{revenue_total:,.0f}/month")

    st.markdown("### Latency trend")
    trend = filtered.sort_values("ts")
    fig = px.line(trend, x="ts", y="latency_ms", color="cell_id",
                  labels={"ts": "Reading time", "latency_ms": "Latency (ms)", "cell_id": "Cell"})
    fig.add_hline(y=70, line_dash="dash", annotation_text="70 ms alert threshold")
    st.plotly_chart(fig, use_container_width=True)

    st.markdown("### Cells with highest p95 latency")
    if not data["revenue"].empty:
        top = data["revenue"].sort_values("p95_latency", ascending=False).head(10)
        st.dataframe(top, use_container_width=True, hide_index=True)

elif page == "Data Quality":
    st.subheader("Data quality and governed dataset")
    sr = data["subscriber_report"]
    kr = data["kpi_report"]

    st.markdown("#### Subscriber dataset")
    a, b, c, d = st.columns(4)
    a.metric("Rows before cleaning", f"{sr['rows_before']:,}")
    b.metric("Duplicate customer IDs", f"{sr['duplicate_customer_ids']:,}")
    c.metric("Rows after cleaning", f"{sr['rows_after']:,}")
    d.metric("Regions after standardisation", sr["regions_after"] if sr["regions_after"] is not None else "N/A")
    st.write({
        "avg_monthly_gb values imputed": sr["imputed_avg_monthly_gb"],
        "days_since_last_recharge values imputed": sr["imputed_days_since_recharge"],
    })

    st.markdown("#### Network KPI dataset")
    st.json(kr)
    st.caption(f"SQLite currently contains {data['db_count']:,} unique cell/timestamp readings.")
    st.markdown("#### Missing latency classification")
    counts = data["kpi"]["latency_missing_reason"].value_counts().rename_axis("reason").reset_index(name="rows")
    fig = px.bar(counts, x="reason", y="rows", color="reason")
    st.plotly_chart(fig, use_container_width=True)
    st.markdown("#### Throughput after repair")
    st.write(f"Negative throughput values converted to NULL: {kr['negative_throughput']}")

elif page == "Network Analysis":
    st.subheader("Network quality analysis")
    st.caption("Use raw 15-minute readings for incident detection; aggregates are for reporting.")
    if filtered.empty:
        st.warning("No rows match the selected filters.")
    else:
        fig = px.line(filtered.sort_values("ts"), x="ts", y="latency_ms", color="cell_id",
                      labels={"ts": "Time", "latency_ms": "Latency (ms)"})
        fig.add_hline(y=100, line_dash="dash", annotation_text="100 ms")
        st.plotly_chart(fig, use_container_width=True)

        hourly = make_hourly(filtered)
        st.markdown("#### Hourly averages and peaks")
        fig2 = px.bar(hourly, x="hour", y="avg_latency", color="cell_id",
                      hover_data=["peak_latency", "readings"],
                      labels={"hour": "Hour", "avg_latency": "Average latency (ms)"})
        st.plotly_chart(fig2, use_container_width=True)

    st.markdown("#### Incident grain comparison")
    cell_for_incident = st.text_input("Incident cell ID", "CELL-DEL-03")
    incident_date = st.text_input("Incident date (YYYY-MM-DD)", "2026-08-03")
    four_hour, metrics = incident_grain_metrics(data["kpi"], cell_for_incident, incident_date)
    m1, m2, m3 = st.columns(3)
    m1.metric("True 15-minute peak", f"{metrics['peak_15m']:.2f} ms" if pd.notna(metrics["peak_15m"]) else "N/A")
    m2.metric("Highest 4-hour mean", f"{metrics['highest_4h_mean']:.2f} ms" if pd.notna(metrics["highest_4h_mean"]) else "N/A")
    m3.metric("Understatement", f"{metrics['understated_pct']:.1f}%" if pd.notna(metrics["understated_pct"]) else "N/A")
    st.write({
        "Raw readings above 100 ms": metrics["raw_breaches_100ms"],
        "Four-hour aggregate breaches above 100 ms": metrics["aggregate_breaches_100ms"],
    })
    if not four_hour.empty:
        st.dataframe(four_hour, use_container_width=True, hide_index=True)

elif page == "Alert Monitoring":
    st.subheader("NOC alert monitoring")
    st.caption("Rule: latency above 70 ms OR downtime, excluding planned maintenance; require 3 consecutive 15-minute intervals.")
    stages = data["alert_stages"]
    st.dataframe(stages, use_container_width=True, hide_index=True)
    fig = px.bar(stages, x="stage", y="alerts_or_intervals", text="alerts_or_intervals",
                 labels={"stage": "Alert processing stage", "alerts_or_intervals": "Count"})
    fig.update_layout(xaxis_tickangle=-20)
    st.plotly_chart(fig, use_container_width=True)

    st.markdown("#### Persistent alert groups")
    alerts = data["persistent_alerts"]
    if alerts.empty:
        st.info("No persistent alert groups were found using this rule.")
    else:
        st.dataframe(alerts.sort_values("intervals", ascending=False),
                     use_container_width=True, hide_index=True)
        st.write("Each interval represents 15 minutes. A group of 3 intervals lasts at least 45 minutes.")
    st.warning("Design consideration: maintenance suppression must be scoped to the affected cell and exact maintenance window. A new fault during maintenance needs an independent safety/critical-outage override or post-window review.")

elif page == "Traffic Forecast":
    st.subheader("Six-month traffic forecast")
    fc = data["forecast"]
    if fc.empty:
        st.warning("No forecast could be calculated. Check Month, Cell, Traffic_GB and Capacity_GB columns.")
    else:
        fc_view = fc.copy()
        if selected_cell != "All":
            fc_view = fc_view[fc_view["Cell"] == selected_cell]
        fig = px.line(fc_view, x="Month", y="Forecast_GB", color="Cell",
                      hover_data=["Capacity_GB", "util_pct"],
                      labels={"Forecast_GB": "Forecast traffic (GB)", "Month": "Month"})
        st.plotly_chart(fig, use_container_width=True)
        st.markdown("#### Forecast capacity utilisation")
        fig2 = px.bar(fc_view, x="Cell", y="util_pct", color="util_pct",
                      facet_col="Month", facet_col_wrap=3,
                      labels={"util_pct": "Capacity utilisation (%)"})
        fig2.add_hline(y=85, line_dash="dash", annotation_text="85% watch threshold")
        st.plotly_chart(fig2, use_container_width=True)

        latest = data["traffic"].sort_values("Month").groupby("Cell").tail(1).copy()
        latest["current_util_pct"] = latest["Traffic_GB"] / latest["Capacity_GB"] * 100
        st.markdown("#### Current utilisation")
        st.dataframe(latest[["Cell", "Month", "Traffic_GB", "Capacity_GB", "current_util_pct"]]
                     .sort_values("current_util_pct", ascending=False),
                     use_container_width=True, hide_index=True)
        st.caption("Forecasts are trend-based estimates from historical data, not guaranteed demand. Review model assumptions before making investment decisions.")

elif page == "Revenue Optimisation":
    st.subheader("Revenue exposure by cell")
    risk = data["revenue"].copy()
    if selected_cell != "All" and not risk.empty:
        risk = risk[risk["cell_id"] == selected_cell]
    if risk.empty:
        st.warning("Revenue analysis unavailable. Check subscriber columns customer_id, home_cell_id and arpu, and ensure home_cell_id matches cell_id.")
    else:
        a, b, c = st.columns(3)
        a.metric("Cells matched", risk["cell_id"].nunique())
        b.metric("Degraded cells (p95 > 70 ms)", int(risk["degraded"].sum()))
        c.metric("Revenue-at-risk proxy", f"₹{risk['revenue_at_risk_proxy'].sum():,.0f}/month")

        fig = px.bar(risk.sort_values("revenue_at_risk_proxy", ascending=False),
                     x="cell_id", y="revenue_at_risk_proxy", color="p95_latency",
                     hover_data=["subscribers", "mean_arpu", "p95_latency", "downtime_intervals"],
                     labels={"cell_id": "Cell", "revenue_at_risk_proxy": "Monthly revenue exposure proxy (₹)"})
        st.plotly_chart(fig, use_container_width=True)
        st.dataframe(risk.sort_values("revenue_at_risk_proxy", ascending=False),
                     use_container_width=True, hide_index=True)
        st.info("Revenue-at-risk is an exposure proxy: it ranks monthly subscriber revenue served by degraded cells. It is not a measured estimate of revenue actually lost.")
