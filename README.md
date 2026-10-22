# LLM Quota Balance Monitor

macOS 菜单栏应用，监控 MiniMax Token Plan 和 DeepSeek 余额。

需要 [uv](https://github.com/astral-sh/uv)。依赖自动安装，无需手动 pip install。

## 配置

首次运行会在项目根目录生成 `config.json` 模板：

```json
{
  "enabled": ["deepseek", "minimax", "aikeybest", "niumacode", "kimi", "codex", "typesafe"],
  "deepseek_api_key": "sk-xxx",
  "minimax_subscription_key": "eyJxxx",
  "aikeybest_token": "sk-xxx",
  "kimi_api_key": "sk-xxx",
  "typesafe_cookie": "完整的 Cookie 请求头内容"
}
```

`enabled` 控制启用的 provider。删除某项 = 关闭该 provider,菜单栏显示 `Disabled`,不再请求对应接口。

注意：
- MiniMax 的 Token Plan 接口需要 **Subscription Key**（订阅密钥），不是普通 API Key。在 MiniMax 控制台的 Billing > Token Plan 页面获取。
- AiKeyBest 使用 New API 系统，在 https://api.aikeybest.cn/query 页面输入 API Key 可查询额度。
- Kimi 使用 Platform API Key,在 Moonshot 控制台 API Keys 页面创建。
- Codex 无需配置 API Key，直接通过本机 Codex/ChatGPT 登录态读取套餐用量；需已安装并登录 Codex 或 ChatGPT 桌面应用。
- TypeSafe 的公开 API Key 接口不提供余额查询，也没有可稳定指定的单个 session Cookie。请登录 `https://console.typesafe.ai/settings/billing`，打开浏览器开发者工具的 Network，刷新页面，选择该页面请求，在 Request Headers 中复制完整的 `Cookie` 请求头（整行 `Cookie: a=b; c=d` 或冒号后的值均可）到 `typesafe_cookie`。不要只复制其中某个 Cookie；登录态过期后需要重新复制。
- 如果已将 TypeSafe Cookie 导入 macOS 钥匙串，`typesafe_cookie` 可以留空；程序优先使用配置值，配置为空时读取钥匙串服务 `llm-quota-balance-monitor.typesafe-cookie`。

## 菜单栏应用

### 前台运行

```bash
uv run monitor.py
```

### 后台运行

```bash
./start.sh
```

停止后台进程：

```bash
./stop.sh
```

菜单栏显示 `DS:¥X.XX MM:XX% AK:XX% NM:X.XX KM:XX% CX:XX% TS:$X.XX`。Codex 的 `CX` 显示当前套餐周期剩余比例，耗尽时显示 `CX:OUT`，不会读取或保存 access token。TypeSafe 的 `TS` 显示控制台 Billing 页面的美元余额。Kimi 默认显示 Weekly 剩余百分比；程序每天最多发送一次最小探测请求判断订阅周期是否耗尽。其他余额数据每 30 分钟刷新。

### 测试 Kimi 月度权益耗尽

可通过一次性的环境变量模拟接口返回“月度总权益已耗尽”，不会修改真实 Key 或额度：

```bash
./stop.sh
KIMI_SIMULATE_TOTAL_QUOTA_OUT=1 ./start.sh
```

刷新完成后菜单栏应显示 `KM:OUT`。测试结束后务必不带环境变量重启，恢复真实数据：

```bash
./stop.sh
./start.sh
```

## 查看状态（终端输出）

```bash
uv run monitor.py --status
```

直接在终端输出当前余额，无需启动 GUI。
