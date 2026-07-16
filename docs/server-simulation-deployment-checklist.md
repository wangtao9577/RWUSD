# Server Simulation Deployment Checklist

这份清单专门面向当前目标：

- 新策略测试监控窗口单独部署
- 不影响现有 DBRSI 三账户正式运行
- 不影响当前已开持仓
- 先做 simulation/batch 对标，再接真实 WebSocket

## A. 当前已完成

- [x] 本地 `server_test_window/index.html` 已修复为只读测试窗口
- [x] 本地页面已完成浏览器端到端验收
- [x] 实盘止盈后真实平仓链路已修复
- [x] 回测已补齐 `restore_now / restore_later / reduce_risk / profit_sweep / redeem` 统计
- [x] 本地 `preflight / runtime / config` 测试已通过

## B. 当前服务器现状

- [x] 服务器当前只有 `/opt/apps/DBRSI`
- [x] nginx 当前只有 `dbrsi` 站点
- [x] 当前没有 `/opt/apps/project2`
- [x] 当前没有 `/opt/apps/project2-sim-window`
- [x] 当前没有 `/opt/apps/project2-sim-data`
- [x] 当前没有 simulation 环境文件与 batch 数据目录
- [x] `8081` 当前未见监听占用

## C. 上服务器前必须准备

1. 代码目录
   - 创建 `/opt/apps/project2`
   - 同步当前本地 `project2` 代码

2. 独立窗口目录
   - 创建 `/opt/apps/project2-sim-window`
   - 拷贝 `server_test_window/index.html`

3. 独立数据目录
   - 创建 `/opt/apps/project2-sim-data`

4. simulation 环境文件
   - 创建 `/opt/apps/project2/.env.simulation`
   - 确认：
     - `LIVE_DRY_RUN=true`
     - `CANDIDATE_SYMBOLS=BTCUSDT,ETHUSDT,SOLUSDT`

## D. 第一轮服务器执行顺序

1. 不改 nginx，先只跑 batch
2. 执行 `run_project2_sim_batch.sh`
3. 确认输出：
   - `latest.json`
   - `live-vs-sim-comparison.json`
   - live/simulation summary
   - live/simulation snapshot
4. 确认生成目录都在 `/opt/apps/project2-sim-data`
5. 再新增 nginx `8081` 站点
6. 浏览器验收测试窗口

## E. 真实 WebSocket 前置门槛

只有以下条件都满足，才建议进入真实 WebSocket 联调：

- [ ] simulation batch 已能稳定生成真实对标数据
- [ ] 测试窗口可稳定展示真实 batch 数据
- [ ] 不需要复用现有 DBRSI API 写接口
- [ ] 所有运行仍保持 dry-run / sim 安全边界

## F. 暂不应执行

- [ ] 不直接改现有 `dbrsi` nginx 站点
- [ ] 不把测试窗口挂到现有正式前端里
- [ ] 不在未跑通真实 batch 前直接上真实 WebSocket
- [ ] 不在未隔离目录前把新代码混放进 `/opt/apps/DBRSI`
