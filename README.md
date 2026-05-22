# SNS Operator

全自动 SNS 账号运营系统，支持 X（Twitter）多账号管理。

**功能：** 热点抓取 → AI内容生成（去AI腔）→ TG审阅 → 自动发布 → KOL互动 → 数据报告

---

## 快速开始

### 1. 安装

```bash
git clone https://github.com/PeterYuWXY/sns-operator.git
cd sns-operator
pip3 install -r requirements.txt
playwright install chromium
```

### 2. 配置

```bash
# 复制配置模板
cp config/accounts.json.example config/accounts.json
cp .env.example .env

# 编辑 .env，填入你的 API Key
nano .env
```

**.env 需要填写：**

```bash
# 必填 — Kimi API Key（支持 Kimi Coding / Moonshot，格式自动探测）
KIMI_API_KEY=sk-xxxxxxxxxx

# 可选 — OpenRouter 备用（Kimi 不可用时自动切换）
OPENROUTER_API_KEY=sk-or-xxxxxxxxxx

# 可选 — Telegram Bot（用于草稿审阅推送 & 数据报告）
TELEGRAM_BOT_TOKEN=123456:ABCxxx
TELEGRAM_CHAT_ID=1234567
```

**config/accounts.json 账号配置：**

```json
{
  "account1": {
    "name": "Your Name",
    "handle": "your_x_handle",
    "persona": "Your persona — e.g. Crypto investor / Marketing expert",
    "style": "Your writing style — e.g. direct, data-driven, opinionated",
    "topics": ["topic1", "topic2", "topic3"],
    "tone": "Your tone — e.g. authoritative but approachable",
    "language": "zh",
    "target_followers": 5000,
    "kol_list": []
  }
}
```

> **字段说明**
> - `language`：`"zh"` 中文 / `"en"` 英文
> - `target_followers`：30天目标粉丝数，用于进度报告
> - `kol_list`：指定要互动的 KOL handle 列表，留空 `[]` 则使用 `sources.json` 默认列表

### 3. 导出 X Cookie

1. 登录 [x.com](https://x.com)
2. 安装 [EditThisCookie](https://www.editthiscookie.com/) 浏览器扩展
3. 点击扩展图标 → 导出 → 保存为 `config/cookies/{account_key}_cookies.json`

### 4. 测试

```bash
# 查看所有可用参数
python3 core/orchestrator.py --help

# 冒烟测试（检测配置是否正确）
python3 core/orchestrator.py --mode test
```

---

## 使用

```bash
# 抓取今日热点话题
python3 core/orchestrator.py --mode scout

# 生成内容草稿（dry run，不发布）
python3 core/orchestrator.py --mode write --slot morning --dry

# 生成 + 发布
python3 core/orchestrator.py --mode write --slot morning

# KOL 互动（dry run 预览）
python3 core/orchestrator.py --mode engage --dry

# KOL 互动（正式运行）
python3 core/orchestrator.py --mode engage

# 生成半周数据报告
python3 core/orchestrator.py --mode report
```

---

## 定时运行（Cron）

```bash
# 编辑 crontab.txt，将路径改为你的实际路径
nano crontab.txt

# 安装定时任务
crontab crontab.txt
```

默认时间表：

| 时间 | 任务 |
|------|------|
| 08:00 | 内容生产（早间市场分析）|
| 12:00 | 内容生产（投资洞察）|
| 16:00 | 内容生产（产品/链上数据）|
| 20:00 | 内容生产（互动/总结）|
| 14:00 | KOL 互动 |
| 10:00（周三、日）| 半周数据报告 |

---

## 架构

```
Scout（热点抓取）
    ↓
Writer（Kimi AI 生成 × 账号人设）
    ↓
Quality Gate（AI腔检测 + 质量评分）
    ↓
TG Bot（推送审阅）→ 用户确认
    ↓
Publisher（Playwright 自动发布到 X）
    ↓
Engager（KOL 互动，自动评论）
    ↓
Reporter（半周粉丝增长报告）
```

## 文件结构

```
sns-operator/
├── SKILL.md                    # Claude Code Skill 配置
├── README.md
├── requirements.txt
├── crontab.txt
├── .env.example
├── config/
│   ├── accounts.json.example   # 账号配置模板（需复制并填写）
│   ├── sources.json            # KOL 列表 + 信息源
│   ├── rules.json              # 运营规则参数
│   └── cookies/                # X Cookie（不提交 Git）
├── core/
│   ├── scout.py                # 热点抓取
│   ├── writer_kimi.py          # AI 内容生成
│   ├── publisher_x.py          # X 发布（Playwright）
│   ├── tg_publisher.py         # Telegram 推送
│   ├── engager.py              # KOL 互动
│   ├── reporter.py             # 数据报告
│   ├── fetcher_x.py            # X 数据抓取
│   └── orchestrator.py         # 主控制器
├── utils/
│   ├── ai_detector.py          # AI腔检测（100+ 规则）
│   └── quality_gate.py         # 质量评分
└── logs/                       # 运行日志
```

---

## API 说明

### Kimi API

本项目使用 Moonshot/Kimi API 生成内容。支持两种格式（自动探测）：

- **Kimi Coding API**：Anthropic Messages 格式，endpoint: `https://api.kimi.com/coding/v1/messages`
- **Moonshot 标准 API**：OpenAI 格式，endpoint: `https://api.moonshot.cn/v1/chat/completions`

在 `.env` 中配置 `KIMI_API_BASE` 指向你的实际 endpoint。

### Telegram Bot（可选）

TG Bot 用于：草稿推送审阅、数据报告推送。不配置则内容只在终端展示。

创建 Bot：Telegram 搜索 @BotFather → `/newbot` → 获取 Token。

---

## 注意

- **封号风险**：X 对自动化操作敏感。已内置随机延迟，但仍建议每天评论 ≤ 10 条
- **Cookie 有效期**：X Cookie 约 30 天失效，需定期重新导出
- **账号安全**：`config/accounts.json` 和 `config/cookies/` 已在 `.gitignore` 中，不会上传

---

## License

MIT
