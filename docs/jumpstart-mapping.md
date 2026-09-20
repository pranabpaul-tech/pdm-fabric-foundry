# Real-Time Manufacturing Jumpstart -> PdM mapping

Workspace `pdm-manufacturing-jumpstart` (id in `state.json['jumpstart_workspace']`), Eventhouse / KQL database
`ManufacturingRealtimeAnalytics`. Installed with `fabric-jumpstart` 0.1.12 from the local machine
(`jumpstart.install("real-time-manufacturing", workspace_id=...)`, Python 3.13, `FABRIC_JUMPSTART_TOKEN_CREDENTIAL=AzureCliCredential`).

The PdM layer is **added on top** (`artifacts/kql-jumpstart/`, applied with
`python src/pdmops/setup/03_kql_schema.py --target jumpstart`). No Jumpstart object is changed; every PdM
name is new (`pdm_*` functions in folder `PdM`, `mv_machine_*` views, six new tables).

## Source -> PdM

| PdM concept | Jumpstart source | Mapping |
|---|---|---|
| `asset_id` | `machines_internal.machine_id` (101-108) | `tostring(machine_id)`; SAP `equipment_number` kept for notification hand-off |
| Asset dimension | `machines_internal` + `sites_internal` (SAP master data, in the Eventhouse) | `pdm_asset_dim()`; `asset_class` derived from `machine_name` (valve, bearing, motor, gearbox, compressor) |
| Telemetry | `sensors_parsed` (tall: one row per sensor reading; temperature C, pressure bar, vibration mm/s) | `mv_machine_1m` / `mv_machine_1h` pivot to wide with `avgif`; `pdm_telemetry()` adds asset attributes |
| Live asset state | - | `pdm_asset_latest()` |
| Silent sensor | - | `pdm_silent_assets(minutes)` |
| Real-time anomaly | - | `pdm_anomalies(lookback, threshold, min_history)`; returns nothing until `min_history` (default 30 min) of real data exists |
| Quality / OEE parts | `production_quality`; Jumpstart functions `Availability()`, `Performance()`, `Quality()` | reused as-is (not redefined); PdM adds no new OEE logic yet |
| Alerts, dispositions, approvals, predictions, kill switch | none | new tables `alert_event`, `alert_disposition`, `approval_event`, `prediction_stream`, `pdm_config`, function `pdm_alerts_enabled()` |
| Labelled downtime | none | new table `downtime_raw` (empty) |

## Gaps in the source data (these limit what PdM can honestly claim)

1. **No downtime / stop events.** The Jumpstart's `Availability()` infers uptime from production activity in a 10-minute
   window. MTBF, MTTR and the planned-vs-unplanned split cannot be computed until `downtime_raw` is fed (Eventstream, or seed data).
2. **No current sensor.** Signals are vibration, temperature, pressure only. `snapshot.py` still lists `current_a`.
3. **No criticality, no manufacturer, `equipment_category` is `M` for all** - nothing to weight risk by; none was invented.
4. **Synthetic noise.** Each sensor is uniform random per reading (temperature 30-50, pressure 1-5, vibration 0.5-1.8), independent of machine type,
   so there is no real degradation signal. The simulator has a `SENSOR_BIAS` flag that would add +21 C and +3.5 mm/s to machine 103 (Gear Box,
   Munich); it is `False` in the shipped notebook (only the quality bias is on).
5. **Volume:** roughly 150 tall rows per minute across 8 machines (about 220k/day). Trends should read `mv_machine_1m`, not `sensors_parsed`.
6. **Activator and KQL Queryset** are listed in the Jumpstart definition but were **not present** in the workspace after install.

## What did not work as documented (and the fix)

- `PostDeploymentNotebook` fails when run via the Fabric job API: `%pip` needs the run parameter `_inlineInstallationEnabled=true`
  (`executionData.parameters`).
- Even then its last cell, `%run SimulateMachineData`, fails inside the run. Everything before it succeeded (master data, OneLake shortcuts,
  orchestration pipeline started). **Fix: run `SimulateMachineData` as its own job** (same parameter). It loops for up to 2 hours
  (`time() - start_time_nb > 7200`), sending one batch roughly every second; cancel the job to stop consuming capacity.
- Fabric capacity `pdmopsf8` was found **paused** (suspended 2026-09-20 00:35 UTC by a principal other than this session) and had to be resumed first.
- `.create-or-alter materialized-view ... with (backfill = true)` is only valid for a view that does not exist yet; `03_kql_schema.py`
  now drops the option for existing views.

## Still pointing at the old Eventhouse

The hosted Foundry agent (`pdm-orchestrator`, version 6), `snapshot.py`, `main.py` and `scripts/ask_agent.py` still read the hand-built
`pdmops-eventhouse` (tables `telemetry_enriched`, `mv_asset_1m`, columns `vibration_rms`, `temp_c`, `current_a`, `pressure_bar`).
Re-pointing them is deferred with the rest of the Foundry work. It needs: `snapshot.py` to query `pdm_telemetry()` with signals
`vibration_mms`, `temp_c`, `pressure_bar` (no current); new Eventhouse URI env vars; updated agent instructions; a new agent version.
