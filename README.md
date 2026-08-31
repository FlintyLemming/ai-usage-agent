# ai-usage-reporter

零依赖的 Python ≥3.10 CLI，把 `tokscale graph` 统计出的每日每模型 token 用量上报到
[ai-plan-insight](../ai-plan-insight) 面板。以系统原生定时器运行：macOS 用
launchd，Linux 用 systemd 用户单元。

## 工作原理

每个 `run` 周期：

1. 调用 `npx tokscale@latest graph --since <本机今天 - lookback_days>`（npx 按需拉取/缓存
   tokscale，无需预装）。**tokscale 按机器自身的本地时区分桶，且忽略 `TZ` 环境变量**，
   没有任何命令行开关可以改这一点，所以 reporter 的日期口径一律跟随本机本地日：
   `--since` 和 `reported_at` 都用本地日期算。要让某台设备按别的时区统计，只能改这台
   设备的系统时区。
2. 把 `contributions[].clients[]` 摊平成 insight 的 `points[]`，转发全部五类
   token（input、output、cache_read、cache_write、reasoning），同一天同模型跨
   client 的记录按每类 token 求和合并。这样面板的总 token 与 tokscale 的
   `totalTokens` 完全一致（五类互斥相加）。
3. 将 `{"source_id","source_label","reported_at","points"}` POST 到
   `{insight_url}/api/usage/report`（配置了 `auth_token` 时附带 `X-Report-Key`）。
   ai-plan-insight 对 payload 覆盖到的每个未封板日期做整天替换（先删后插），
   重复运行不会重复计数；一旦服务端收到 `reported_at` 晚于 D 的上报，该
   source 的 D 日即封板，之后本地历史数据被清理或重写也不会再影响面板
   （封板按 source 隔离，不影响其他设备上报同一天）。因为 `reported_at` 用的是本机本地日，
   封板恰好发生在本地午夜——当天数据在本地这一天走完之前不会被冻住。
4. POST 失败（insight 不可达 / 非 2xx）时，把本次 payload 存到
   `~/.local/state/ai-usage-reporter/pending.json`（单文件——每次 payload 都是
   全窗口快照，只保留最新一份失败的即可），下次运行时先重放它再发送新快照。

## 安装

前置：机器上需有 Node.js（自带 `npx`）；reporter 通过 `npx tokscale@latest` 按需拉取
tokscale，无需预装。

    pip install -e .[dev]
    ai-usage-reporter install
    # → 生成 ~/.config/ai-usage-reporter/config.json（source_id 预填为 "<hostname>"）
    #   以及 launchd plist（macOS）或 systemd 用户单元（Linux）。
    # 编辑配置确认 `source_id` 后启用：
    #   macOS:  launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/ai-usage-reporter.plist
    #   Linux:  systemctl --user enable --now ai-usage-reporter.timer

注意：`source_id` 是设备在面板中的身份，多台设备必须互不相同，且设定后不要更改
（改名等于新设备，历史会分叉）。

## 运行 / 状态 / 卸载

    ai-usage-reporter run       # 执行一次采集上报（成功退出码 0）
    ai-usage-reporter status    # 查看上次运行结果、pending 状态等
    ai-usage-reporter uninstall # 移除定时器单元（保留配置与状态）

## 退出码

| 退出码 | 含义 |
|---|---|
| 0 | 成功（包括 contributions 为空） |
| 2 | 配置校验失败 |
| 3 | npx 缺失（未安装 Node.js）/ 不可执行 |
| 4 | tokscale 非零退出 |
| 5 | tokscale 输出不是合法 JSON |
| 6 | insight 不可达（连接拒绝 / 超时） |
| 7 | insight 返回非 2xx |

## 测试

    python -m pytest -q

设计文档：`docs/superpowers/specs/2026-07-02-reporter-design.md`。
