# Project2 Server Simulation Window Deployment

This folder contains the deployment notes for the isolated Project2 server-side simulation workflow.

## Safety Boundaries

- Do not reuse `/opt/apps/DBRSI/frontend/build`.
- Do not modify the existing `dbrsi` nginx site.
- Keep the public monitor window read-only.
- Do not affect the three running production accounts.
- Do not affect any current open production position.

## Recommended Server Layout

- project root: `/opt/apps/project2`
- public read-only monitor page: `/opt/apps/project2-sim-window`
- public monitor data root: `/opt/apps/project2-sim-data`
- simulation env file: `/opt/apps/project2/.env.simulation`
- local-only credential page assets: `/opt/apps/project2/server_sim_control`

## Public Monitor

- URL: `http://45.77.247.235:8081/`
- Content: `server_test_window/index.html` plus static JSON artifacts written by `run_server_sim_batch.py`
- Rule: keep this page read-only

## Local-Only Credential Entry

- Start the control server with:
  - `python scripts/run_sim_config_server.py --host 127.0.0.1 --port 18081 --env-file /opt/apps/project2/.env.simulation --page-dir /opt/apps/project2/server_sim_control`
- Access it only through SSH port forwarding:
  - `ssh -L 18081:127.0.0.1:18081 <user>@45.77.247.235`
  - `http://127.0.0.1:18081/`
- This page only writes:
  - `BINANCE_API_KEY`
  - `BINANCE_API_SECRET`
- Keep `LIVE_LOG_PATH=tmp/server/live_runtime.jsonl` fixed.
- Do not expose this control page through public nginx.

## Files In This Folder

- `project2-sim-window.nginx.conf`
  - nginx server block for the public read-only monitor page
- `run_project2_sim_batch.sh`
  - bounded batch runner for live-vs-sim sampling
- `run_project2_sim_forever.sh`
  - long-running daemon entrypoint for continuous RWUSD simulation observation
- `project2-sim-runner.service`
  - systemd unit template for keeping the RWUSD simulation daemon alive
- `server_preflight_checklist.md`
  - pre-deploy and post-deploy operator checklist

## Recommended Deployment Order

1. Sync the repo to `/opt/apps/project2`.
2. Copy `server_test_window/index.html` to `/opt/apps/project2-sim-window`.
3. Keep `server_sim_control/index.html` inside `/opt/apps/project2/server_sim_control`.
4. Create `/opt/apps/project2-sim-data`.
5. Start the local-only credential server on `127.0.0.1:18081`.
6. Open the forwarded local page and save simulation credentials.
7. Run one bounded batch with `run_project2_sim_batch.sh`.
8. Confirm `/opt/apps/project2-sim-data/latest.json` exists.
9. Validate the public read-only monitor page on `8081`.
10. If the bounded batch looks healthy, switch to `run_project2_sim_forever.sh` or install `project2-sim-runner.service`.
