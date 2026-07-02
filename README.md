# ai-usage-reporter

零依赖的 Python ≥3.10 CLI，把 `tokscale graph` 统计出的每日每模型 token 用量上报到
[ai-plan-insight](../ai-plan-insight) 面板。以系统原生定时器运行：macOS 用
launchd，Linux 用 systemd 用户单元。

## 工作原理

每个 `run` 周期：

1. 以 `TZ=Asia/Shanghai` 调用 `tokscale graph --since <今天(UTC+8) - lookback_days>`，
   让 tokscale 按 UTC+8 的自然日分桶。
2. 把 `contributions[].clients[]` 摊平成 insight 的 `points[]`，同一天同模型跨
   client 的记录按 input/output 求和合并。
3. 将 `{"source_id","source_label","reported_at","points"}` POST 到
   `{insight_url}/api/usage/report`（配置了 `auth_token` 时附带 `X-Report-Key`）。
   ai-plan-insight 按 `(date, source_id, model_id)` UPSERT，重复运行不会重复计数；
   一旦服务端收到日期晚于 D 的上报，D 日即封板，之后本地历史数据被清理或重写
   也不会再影响面板。
4. POST 失败（insight 不可达 / 非 2xx）时，把本次 payload 存到
   `~/.local/state/ai-usage-reporter/pending.json`（单文件——每次 payload 都是
   全窗口快照，只保留最新一份失败的即可），下次运行时先重放它再发送新快照。

## 安装

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
| 3 | tokscale 二进制缺失 / 不可执行 |
| 4 | tokscale 非零退出 |
| 5 | tokscale 输出不是合法 JSON |
| 6 | insight 不可达（连接拒绝 / 超时） |
| 7 | insight 返回非 2xx |

## 测试

    python -m pytest -q

设计文档：`docs/superpowers/specs/2026-07-02-reporter-design.md`。
