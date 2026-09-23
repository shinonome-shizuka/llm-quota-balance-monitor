# LLM Quota Balance Monitor

macOS 菜单栏应用，监控 MiniMax Token Plan 和 DeepSeek 余额。

需要 [uv](https://github.com/astral-sh/uv)。依赖自动安装，无需手动 pip install。

## 配置

首次运行会在项目根目录生成 `config.json` 模板：

```json
{
  "enabled": ["deepseek", "minimax", "aikeybest", "niumacode", "kimi", "codex"],
  "deepseek_api_key": "sk-xxx",
  "minimax_subscription_key": "eyJxxx",
  "aikeybest_token": "sk-xxx",
  "kimi_api_key": "sk-xxx"
}
```

`enabled` 控制启用的 provider。删除某项 = 关闭该 provider,菜单栏显示 `Disabled`,不再请求对应接口。

注意：
- MiniMax 的 Token Plan 接口需要 **Subscription Key**（订阅密钥），不是普通 API Key。在 MiniMax 控制台的 Billing > Token Plan 页面获取。
- AiKeyBest 使用 New API 系统，在 https://api.aikeybest.cn/query 页面输入 API Key 可查询额度。
- Kimi 使用 Platform API Key,在 Moonshot 控制台 API Keys 页面创建。
- Codex 无需配置 API Key，直接通过本机 Codex/ChatGPT 登录态读取套餐用量；需已安装并登录 Codex 或 ChatGPT 桌面应用。

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

菜单栏显示 `DS:¥X.XX MM:XX% AK:XX% NM:X.XX KM:XX% CX:XX%`。Codex 的 `CX` 显示当前套餐周期剩余比例，耗尽时显示 `CX:OUT`，不会读取或保存 access token。Kimi 默认显示 Weekly 剩余百分比；程序每天最多发送一次最小探测请求判断订阅周期是否耗尽。其他余额数据每 30 分钟刷新。

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
