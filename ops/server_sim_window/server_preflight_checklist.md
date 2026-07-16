# Server Preflight Checklist

## 1. 隔离确认

- [ ] 不修改 `/opt/apps/DBRSI/frontend/build`
- [ ] 不修改 `/etc/nginx/sites-available/dbrsi`
- [ ] 不复用现有正式前端目录
- [ ] 不复用现有正式 API 路由

## 2. 路径确认

- [ ] `/opt/apps/project2` 已存在
- [ ] `/opt/apps/project2-sim-window` 已存在
- [ ] `/opt/apps/project2-sim-data` 已存在
- [ ] `/opt/apps/project2/.env.simulation` 已存在

## 3. 配置确认

- [ ] `LIVE_DRY_RUN=true`
- [ ] `CANDIDATE_SYMBOLS=BTCUSDT,ETHUSDT,SOLUSDT`
- [ ] `BINANCE_API_KEY` / `BINANCE_API_SECRET` 已填写
- [ ] `LIVE_LOG_PATH` 指向的是 project2 自己要对标的 live log

## 4. Batch 运行确认

- [ ] `python3 scripts/run_server_sim_batch.py ...` 可执行
- [ ] `/opt/apps/project2-sim-data/latest.json` 生成成功
- [ ] 生成了 `live-vs-sim-comparison.json`
- [ ] 生成了 live/simulation summary 与 snapshot

## 5. 窗口确认

- [ ] `index.html` 已部署到 `/opt/apps/project2-sim-window`
- [ ] nginx 独立站点监听 `8081`
- [ ] 页面只读取 `/data/latest.json`
- [ ] 页面不代理 `/start` `/stop` `/close` 或其他写接口

## 6. 联调前最终确认

- [ ] 不影响现有三个账户
- [ ] 不影响当前持仓
- [ ] 不改现有 DBRSI 正式窗口
- [ ] 先验收 simulation/batch，再考虑真实 WebSocket
