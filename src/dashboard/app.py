"""Streamlit dashboard for the log-intelligence pipeline.

Run with `make dashboard`, which exports the AWS-side env vars from
`terraform output` before invoking `streamlit run` on this module.

Four tabs render the metrics in `sql/metrics/` via Athena; the sidebar
surfaces pipeline health (last Glue job run, DLQ depth, last successful
Step Functions execution). Auto-refresh is opt-in to keep the page
responsive when the user is interacting with it.
"""

from __future__ import annotations

import time

import plotly.express as px
import streamlit as st

import queries

st.set_page_config(page_title="Log intelligence", layout="wide")
st.title("Log intelligence")


# --- sidebar: pipeline health & refresh controls ---------------------------

with st.sidebar:
    st.header("Pipeline health")

    latest = queries.latest_pipeline_run()
    if latest is None:
        st.write("No pipeline_runs rows yet — run `make trigger-etl` to populate.")
    else:
        st.metric("Last run status", str(latest.get("status") or "—"))
        st.write("Started:", latest.get("started_at"))
        st.write("Clean records:", latest.get("clean_records_written"))
        st.write("Rejected records:", latest.get("rejected_records"))
        if latest.get("failure_reason"):
            st.error(latest["failure_reason"])

    depth = queries.dlq_depth()
    if depth is not None:
        st.metric("DLQ depth (max, last 15m)", depth)

    last_sfn = queries.last_successful_execution()
    if last_sfn is not None:
        st.write("Last successful ETL:", last_sfn.get("stopDate"))

    st.divider()
    auto_refresh = st.checkbox("Auto-refresh every 30s", value=True)
    if st.button("Refresh now"):
        st.cache_data.clear()
        st.rerun()


# --- main tabs --------------------------------------------------------------

overview_tab, errors_tab, devices_tab, anomalies_tab = st.tabs(
    ["Overview", "Top Errors", "Devices", "Anomalies"]
)

with overview_tab:
    totals = queries.overview_totals()
    c1, c2, c3 = st.columns(3)
    c1.metric("Events (24h)", f"{totals['total_events']:,}")
    c2.metric("Error rate (24h)", f"{totals['error_rate']:.2%}")
    c3.metric("Active devices (24h)", totals["active_devices"])

    hourly = queries.events_per_hour_7d()
    if hourly.empty:
        st.info("No events in the last 7 days.")
    else:
        long_df = hourly.melt(
            id_vars="hour_bucket",
            value_vars=["event_count", "error_count"],
            var_name="series",
            value_name="count",
        )
        fig = px.line(
            long_df,
            x="hour_bucket",
            y="count",
            color="series",
            title="Events and errors per hour (last 7d)",
        )
        st.plotly_chart(fig, use_container_width=True)

with errors_tab:
    top = queries.top_error_codes()
    if top.empty:
        st.info("No errors in the last 7 days.")
    else:
        fig = px.bar(
            top,
            x="error_code",
            y="error_count",
            title="Top error codes (last 7d)",
        )
        st.plotly_chart(fig, use_container_width=True)

    st.subheader("Last 50 errors")
    st.dataframe(queries.recent_errors(50), use_container_width=True)

with devices_tab:
    rates = queries.error_rate_by_device()
    mtbe = queries.mtbe_per_device()
    if rates.empty and mtbe.empty:
        st.info("No device data in the last 24 hours.")
    else:
        # Outer-merge so devices with no recent errors still appear, with
        # the MTBE column blank.
        joined = rates.merge(mtbe, on="device_id", how="outer")
        st.dataframe(joined, use_container_width=True)

with anomalies_tab:
    st.subheader("Silent devices (last 30 min)")
    silent = queries.silent_devices()
    if silent.empty:
        st.write("Every active device produced events in the last 30 minutes.")
    else:
        st.dataframe(silent, use_container_width=True)

    st.subheader("Error bursts (>3σ above per-device baseline)")
    bursts = queries.anomaly_error_burst()
    if bursts.empty:
        st.write("No anomalous device-hours detected.")
    else:
        st.dataframe(bursts, use_container_width=True)

    st.subheader("Error rate by firmware version")
    fw = queries.firmware_cohort_errors()
    if fw.empty:
        st.write("No firmware-cohort data.")
    else:
        st.dataframe(fw, use_container_width=True)


if auto_refresh:
    # Sleep at the bottom so the page renders fully before the rerun fires.
    # The user can still toggle auto-refresh off above to regain interactivity.
    time.sleep(30)
    st.rerun()
