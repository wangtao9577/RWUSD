# RWUSD 策略 PDF 偏差审计（2026-07-03）

## 结论

当前实现已经覆盖了这套策略最核心的主线：

1. 在 `BTCUSDT / ETHUSDT / SOLUSDT` 三标的中筛选单一主标的运行。
2. 在同一主标的上做等额多空对冲。
3. 单边达到净盈利阈值后，只平盈利腿。
4. 进入 `REBALANCING` 后优先立即补回对冲腿。
5. 已实现利润先进入 `ProfitBucket`，随后在风险允许时从 `PM -> Spot -> RWUSD` 做利润沉淀。
6. 当 `uniMMR` 等风险条件恶化时，允许 `RWUSD -> Spot -> PM` 做保证金回补。

按当前项目内置口径，PDF 核心完成度仍然是 `88% (8/1/2)`。

这次额外补齐的是“长期持续模拟运行”的守护层，不改变上面的核心策略完成度口径。

## 一、已实现

### 1. 三标的候选池 + 单一主标的边界

- 候选池显式限定为三标的，且运行时只选一个主标的。
- 证据：
  - `src/market/selector.py`
  - `src/app/live_runner.py`
  - `src/infra/simulation_batch_report.py`

### 2. 同标的双向对冲状态机

- 已有 `IDLE -> OPENING_HEDGE -> HEDGED -> TAKING_PROFIT -> REBALANCING -> HEDGED/PAUSED` 主状态流。
- 证据：
  - `src/domain/enums.py`
  - `src/strategy/hedge_engine.py`
  - `src/app/live_runner.py`

### 3. 单边止盈，只平盈利腿

- 在 `HEDGED` 状态下，如果 `LONG` 或 `SHORT` 的未实现盈利达到阈值，会触发单边止盈。
- 平仓执行在 `_execute_take_profit_close(...)`，只关闭盈利侧对应持仓。
- 证据：
  - `src/strategy/hedge_engine.py`
  - `src/app/live_runner.py`
  - `src/strategy/pnl_manager.py`

### 4. 止盈后优先立即补回对冲腿

- 止盈完成后进入 `REBALANCING`。
- 如果风险不阻断、且允许当前评估点立即补腿，默认决策是 `restore_now`。
- 这和你强调的“先平盈利腿，立刻再开一单对冲，保证对冲完整性，再做后续资金动作”是一致的。
- 证据：
  - `src/strategy/rebalance.py`
  - `src/app/live_runner.py`

### 5. 已实现利润沉淀到 RWUSD

- 止盈后，净盈利先进入 `ProfitBucket.realized_pnl_available_for_deposit`。
- 之后在风险允许时，`TransferPlanner.plan_sweep(...)` 计算可沉淀金额。
- 执行路径是：
  - `transfer_pm_to_spot(...)`
  - `subscribe_rwusd(...)`
- 证据：
  - `src/strategy/pnl_manager.py`
  - `src/portfolio/transfers.py`
  - `src/app/live_runner.py`
  - `src/exchange/binance_account.py`

### 6. RWUSD 利息累计

- 运行周期中会对 `rwusd_principal` 进行利息累计。
- 证据：
  - `src/portfolio/yield_accrual.py`
  - `src/app/live_runner.py`
  - `src/backtest/engine.py`

### 7. 风险约束和暂停/降风险能力

- 已有 `soft_unimmr`、`hard_unimmr`、`max_drawdown`、`redeem_unimmr`。
- 风险动作包含：
  - `should_pause`
  - `should_reduce`
  - `should_redeem_topup`
- 证据：
  - `src/risk/rules.py`
  - `src/app/live_runner.py`
  - `src/backtest/engine.py`

### 8. 前端监控和长期模拟守护层

- 前端已能展示 `RWUSD` 独立卡片，不影响现有三个正式账户。
- 已补 `run_server_sim_daemon.py` + `systemd` 守护方案，使其可像 `BOLL` 一样持续模拟运行。
- 证据：
  - `scripts/run_server_sim_daemon.py`
  - `ops/server_sim_window/run_project2_sim_forever.sh`
  - `ops/server_sim_window/project2-sim-runner.service`
  - `_shadow/DBRSI-main/backend/services/project2_simulation.py`

## 二、部分实现

### 1. RWUSD 回补路径已经有，但金额策略偏粗

- 当前 `plan_redeem(...)` 的逻辑是：
  - 一旦 `uniMMR < redeem_unimmr`
  - 就直接使用 `bucket.rwusd_redeemable`
- 这意味着现在更像“把可赎回 RWUSD 全额作为回补额度候选”，而不是“只计算补足当前保证金缺口所需的精确金额”。
- 这符合“有回补路径”，但不够像你描述的那种精细化资金循环。
- 证据：
  - `src/portfolio/transfers.py`
  - `src/app/live_runner.py`
  - `src/backtest/engine.py`

### 2. 回测复用了很多规则模块，但还不是完整同一状态机

- 回测已经复用了：
  - `SymbolSelector`
  - `HarvestRule`
  - `PnlManager`
  - `RebalancePlanner`
  - `RiskRuleSet`
  - `TransferPlanner`
- 但 `BacktestEngine` 仍然是“每次运行构造新的 `HedgeEngine` 做一个压缩决策窗口”，没有完全复用 `LiveRunner` 的连续持久状态流。
- 所以它更接近“规则近似一致”，还不是“和实盘完全同一条连续状态机”。
- 证据：
  - `src/backtest/engine.py`
  - `src/app/live_runner.py`

### 3. 选币切换有分差阈值，但没有显式时间型 cooldown

- 当前有 `switch_threshold`，能抑制小分差频繁切换。
- 但 PDF 里提到的“加入 cooldown 机制”，现在数据模型里虽然有 `cooldown_symbol` 字段，实际选择器没有时间冷却逻辑。
- 这意味着“防抖有了，时间冷却还没有完整落地”。
- 证据：
  - `src/market/selector.py`
  - `src/domain/models.py`

### 4. Preflight 已检查 RWUSD 路径存在，但没把整条路径查全

- 当前 `PreflightChecker` 会检查：
  - PM 账户
  - UM Hedge Mode
  - 候选标的下单规则
  - user stream
  - `transfer_pm_to_spot`
  - `subscribe_rwusd`
- 但它没有显式检查：
  - `redeem_rwusd`
  - `transfer_spot_to_pm`
- 所以“利润沉淀路径”检查了，“风险回补路径”还没查全。
- 证据：
  - `src/preflight/checker.py`

## 三、未实现

### 1. 真实 WebSocket / 实盘执行链路

- 项目里已经有 user stream 和 live runner 骨架。
- 但当前 `RWUSD` 这条线上主运行形态仍然是“基于真实市场/账户输入的 dry-run 模拟”。
- 也就是说，实盘下单链路还没有按你要的那种真实执行模式闭环上线。
- 证据：
  - `src/infra/simulation_batch_report.py`
  - `docs/server-simulation-runbook.md`

### 2. USDC Maker 执行适配

- 现在已经有 `USDC` 路由偏好和 `GTX LIMIT` maker-only 恢复路由设计。
- 但它目前主要还停留在执行偏好层和 restore 路由层，整套“以 USDC 交易对做免手续费执行”的完整实盘闭环还没真正落地。
- 项目内置完成度也把这项明确标成 `pending`。
- 证据：
  - `src/strategy/leg_route.py`
  - `src/infra/simulation_batch_report.py`

## 四、最关键的偏差点

### 偏差点 A：回补金额不是“按缺口精算”

这会直接影响你最关心的资金循环本质。

你要的是：

1. 双边对冲先持续采集利润。
2. 已实现利润逐步转成 `RWUSD` 吃利息。
3. 风险需要时，只赎回“补足缺口所需的金额”。

现在实现的是：

1. 有回补路径。
2. 但回补金额上限更接近“全部可赎回余额”，不够精细。

这项我认为应该优先进入下一轮修正。

### 偏差点 B：回测不是完整连续状态机

这会影响我们后面拿“回测结果”去和 PDF 做高置信度对照。

如果后面你要重点看：

- 月化
- 年化
- 回撤
- 资金循环稳定性

那么更接近实盘的连续回测链路会非常重要。

### 偏差点 C：前端当前的“Current Return / Monthly / Annualized”主要还是上一个完整批次口径

当前前端已能同时展示：

- 当前运行批次
- 上一个完整批次

但收益指标仍主要来自最近一个完成批次，而不是当前正在运行批次的完整动态收益曲线。

这不影响守护运行本身，但会影响你对“当前这轮模拟”的直观判断。

## 五、下一步优先级建议

如果目标是尽快把这套策略进一步向 PDF 靠拢，我建议优先级如下：

1. 先修 `redeem_topup` 金额逻辑
   - 从“全额可赎回”改成“按风险缺口精算”
2. 再补时间型 `cooldown`
   - 让选币切换更接近 PDF 描述
3. 再改连续回测链路
   - 让 backtest 和 live simulation 更接近同一条连续状态机
4. 最后再推进真实执行层
   - WebSocket / 实盘下单
   - USDC Maker 执行适配

## 六、我对当前策略状态的简化判断

一句话总结：

这套策略现在已经不是“空壳监控页”了，而是一套已经具备核心利润采集主线的 RWUSD 对冲收割模拟系统；但在“回补精算、连续回测一致性、真实执行适配”这三处，离你要的 PDF 完整实盘版本还有明显距离。
