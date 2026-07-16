# RWUSD 策略完成度与持续模拟方案

日期：2026-07-08

## 1. 当前结论

当前仓库已经实现了这套 RWUSD 策略的大部分核心闭环，按现有项目内审计口径，PDF 核心完成度可视为约 `92%`。

这里的 `92%` 指的是：

- 单主标的对冲主线已打通
- 止盈后立即补腿已打通
- 利润沉淀到 RWUSD 已打通
- 风险触发时 RWUSD 赎回回补已打通
- 回补金额已升级为按 `reserve_gap / uniMMR gap` 取大值的近似精算
- 前端 RWUSD 测试窗口与服务器持续模拟运行已打通

但它不等于“完整实盘版已完成”，因为以下两块主闭环仍未完成，且有一块细节仍需继续精修：

- 回补金额虽已进入近似精算，但还不是 Binance 风险引擎级精确公式
- 回测与 live simulation 还不是完全同一条连续状态机
- 真实实盘执行层和完整 USDC Maker 实盘闭环还未落地

## 2. 已完成

### 2.1 PDF 核心主线

- 三标的候选池已固定为 `BTCUSDT / ETHUSDT / SOLUSDT`
- 任一时刻只运行单一主标的
- 同一 underlying 的等量多空对冲状态机已实现
- 单边止盈时只平盈利腿
- 止盈后优先执行 `restore_now`
- 对冲恢复后重新回到 `HEDGED`
- 显式时间型 `cooldown` 已接入选币状态机

关键代码：

- `src/market/selector.py`
- `src/strategy/hedge_engine.py`
- `src/strategy/rebalance.py`
- `src/app/live_runner.py`

### 2.2 RWUSD 资金循环

- 已实现利润先进入 `ProfitBucket`
- 满足条件时执行 `PM -> Spot -> RWUSD`
- 已有 RWUSD 利息累计逻辑
- 风险触发时允许 `RWUSD -> Spot -> PM` 回补

关键代码：

- `src/strategy/pnl_manager.py`
- `src/portfolio/transfers.py`
- `src/portfolio/yield_accrual.py`
- `src/app/live_runner.py`
- `src/exchange/binance_account.py`

### 2.3 风控主线

- 已有 `soft_unimmr / hard_unimmr / max_drawdown / redeem_unimmr`
- 已有 `pause / reduce_risk / redeem_topup` 决策分支
- 已在 live simulation 与 backtest 中复用大部分风险规则

关键代码：

- `src/risk/rules.py`
- `src/app/live_runner.py`
- `src/backtest/engine.py`

### 2.4 服务器持续模拟与前端监控

- `RWUSD` 已作为独立测试账户接入 DBRSI 前端
- 服务器持续模拟 runner 已接通
- `RWUSD` 前端卡片已正常显示并持续刷新
- 后端已禁用三正式 `BOLL` 账户自动恢复，避免影响现有正式账户
- `Project2 Simulation` 轻量视图已上线，解决前端接口超时

关键代码/脚本：

- `_shadow/DBRSI-main/backend/services/project2_simulation.py`
- `_shadow/DBRSI-main/backend/services/runtime_restore_policy.py`
- `_shadow/DBRSI-main/backend/main.py`
- `scripts/run_server_sim_batch.py`
- `scripts/run_server_sim_daemon.py`
- `ops/server_sim_window/project2-sim-runner.service`

## 3. 部分完成

### 3.1 回测一致性

- backtest 已复用 `SymbolSelector / HarvestRule / PnlManager / RebalancePlanner / RiskRuleSet / TransferPlanner`
- `BacktestRuntimeState` 已落地，`run()` 已支持统一 runtime state 输入，`run_sequence()` 已返回下一轮连续状态
- 连续状态已覆盖 `current_symbol / phase / pending_rebalance_side / last_symbol_switch_minute / profit_bucket`
- 回测已开始镜像 live dry-run 的虚拟执行状态，当前已覆盖 `sim_long_qty / sim_short_qty / sim_last_mark_price / sim_take_profit_count / sim_restore_count`
- 但 backtest 仍不是和 `LiveRunner` 完全同一条连续状态机

结论：

- 规则层大体一致
- 事件流与持久状态层还不是完全等价

### 3.2 选币冷却

- 当前已有 `switch_threshold` 抑制频繁切换
- 已新增显式时间型 `cooldown`
- `LiveRunner / BacktestEngine / SQLite state` 已共享该状态

结论：

- 冷却机制已落地
- 但冷却时长参数仍需要持续模拟观察后再细调

### 3.3 USDC Maker 适配

- 当前已有执行偏好层设计和部分路由能力
- `open_hedge / restore_now / recover_missing_leg` 已具备走 `USDC` 路由的框架
- 但完整实盘执行闭环还未落地

结论：

- 设计和部分实现有了
- 仍应视为 `partial / pending`

### 3.4 回补金额精算

- 当前 `redeem_topup` 已不再只看 `available_balance` 缺口
- 已新增 `uniMMR` 风险缺口口径
- 实际赎回金额按 `max(reserve_gap, unimmr_gap)` 计算
- 最终仍受 `rwusd_redeemable` 与 `min_redeem` 约束

结论：

- 已从“粗放回补”升级为“风险缺口近似精算”
- 但还不是 Binance 官方风险引擎级精确公式

## 4. 未完成

### 4.1 Real execution websocket 闭环

当前主运行形态仍是：

- 真实行情输入
- 强制 dry-run 执行

还不是：

- 真实用户流
- 真实订单成交
- 真实仓位回写
- 真实执行闭环

### 4.2 完整实盘 USDC Maker 闭环

当前还没有完成：

- 真正以 `USDC-PERP` 为主执行通道的完整真实下单闭环
- maker 超时 / fallback / 状态恢复的完整实盘逻辑

## 5. 当前服务器状态

截至 2026-07-08 当前轮排查结果：

- DBRSI 前端首页可正常打开
- 账户总数为 `4`
- `RWUSD` 卡片已显示
- `RWUSD` 当前显示为 `运行中`
- 三个正式 `BOLL` 当前显示为 `已停止`

这证明：

- 前端测试窗口已恢复
- 后端状态接口已恢复
- RWUSD 持续模拟链路已在跑
- 不会影响已有三正式账户

## 6. 持续模拟测试方案

### 6.1 目标

在不接真实下单的前提下，让服务器持续基于真实市场数据运行 RWUSD 策略，形成足够长的样本窗口，用于验证：

- 选币是否稳定
- 止盈与补腿是否正常发生
- RWUSD 本金是否持续增长
- `redeem_topup` 是否过于频繁
- drawdown 和 `uniMMR` 压力是否可控

### 6.2 运行形态

建议继续使用当前链路：

- `project2-sim-runner.service`
- `scripts/run_server_sim_daemon.py`
- `scripts/run_server_sim_batch.py`
- 数据根目录 `/opt/apps/project2-sim-data`

理由：

- 已接上前端
- 已接上 runner-state / supervisor-state / latest.json
- 已形成持续观察闭环
- 不会碰正式 DBRSI 实盘账户

### 6.3 观察周期

建议先跑两段：

1. 短周期稳定性观察：`24-48 小时`
2. 中周期策略观察：`5-7 天`

### 6.4 重点观察指标

- `selected_symbol_counts`
- `rebalance_action_counts`
- `profit_sweep_count`
- `redeem_topup_count`
- `take_profit_count`
- `restore_count`
- `rwusd_principal`
- `rwusd_interest_accrued`
- `harvest_buffer`
- `max_drawdown_pct`
- `runtime_days`：给月化/年化提供样本时长上下文，避免短样本被误读

### 6.5 观察通过标准

第一阶段先不看“赚多少钱”，先看“闭环是否健康”：

- 不长期卡在 `hold`
- 能看到 `open_hedge -> take_profit -> restore_now`
- `RWUSD Principal` 能缓慢增长
- `redeem_topup_count` 不异常偏高
- 没有明显失控切币或长时间单边裸露

## 7. 下一步优先级

建议按这个顺序继续：

1. 继续补齐更完整的 live/backtest 执行状态镜像，并做长周期连续模拟验证
2. 再推进真实执行层与完整 USDC Maker 闭环
3. 最后再按 Binance 联合保证金真实反馈继续细化 `redeem_topup` 精确公式

## 8. 一句话判断

当前 RWUSD 已经不是“空壳测试面板”，而是一套能持续跑、能前端展示、能演练 PDF 核心闭环的服务器模拟系统；但距离“完整 PDF 实盘执行版”，还差连续回测一致性、真实执行闭环，以及 `redeem_topup` 公式继续细化这三块。
