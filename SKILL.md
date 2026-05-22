---
name: sns-operator
description: >
  SNS 账号运营自动化助手。帮用户在 X（Twitter）、Reddit 等平台完成：热点抓取、
  AI 内容生成（去AI腔）、KOL 互动、质量评分、定时发布、数据报告。
  触发词：/scout /write /engage /report /sns-setup /sns-operator
version: 1.1.0
author: PeterYuWXY
platforms: [macos, linux]
tags: [sns, twitter, x, automation, marketing, content, kol, crypto]
---

# SNS Operator — Claude Code Skill

当用户触发以下任意命令时执行本 skill：
`/scout` `/write` `/engage` `/report` `/sns-setup` `/sns-operator`

---

## 路由规则

根据用户输入的命令分发到对应子任务：

| 命令 | 操作 |
|------|------|
| `/scout` | 抓取今日热点话题，输出可用选题 |
| `/write [话题]` | 生成推文草稿，质量评分，推送 TG 审阅 |
| `/engage` | 对 KOL 推文生成高情商回复，自动发布 |
| `/report` | 生成半周粉丝增长与内容表现报告 |
| `/sns-setup` | 引导用户完成首次配置 |
| `/sns-operator` | 显示菜单，让用户选择操作 |

---

## /sns-setup — 首次配置向导

执行以下步骤：

1. **检查环境**
   ```bash
   python3 --version   # 需要 3.9+
   pip3 show playwright feedparser requests python-telegram-bot 2>&1 | grep -E "Name:|not found"
   ```

2. **如果依赖缺失**，提示用户运行：
   ```bash
   pip3 install -r requirements.txt
   playwright install chromium
   ```

3. **检查配置文件**
   - 检查 `config/accounts.json` 是否存在（不存在则提示从 `accounts.json.example` 复制）
   - 检查 `.env` 是否存在
   - 读取 `.env.example` 展示给用户需要填写的变量

4. **引导账号配置**
   - 询问用户的 X handle
   - 询问账号定位（人设、内容方向、语气）
   - 自动写入 `config/accounts.json`

5. **引导 Cookie 导出**
   说明步骤：
   - 登录 x.com
   - 安装 EditThisCookie 浏览器扩展
   - 导出 Cookies → 保存为 `config/cookies/{account_key}_cookies.json`

6. **引导 TG Bot 配置**（可选）
   - 在 Telegram 搜索 @BotFather → /newbot → 获取 Token
   - 将 Token 和 Chat ID 填入 `.env`

7. **冒烟测试**
   ```bash
   python3 core/orchestrator.py --mode test
   ```

---

## /scout — 热点抓取

1. 运行抓取：
   ```bash
   python3 core/orchestrator.py --mode scout
   ```

2. 展示返回的话题列表，格式：
   ```
   今日热点（来源：Reddit / RSS / KOL）：
   1. [话题] — 来源：r/CryptoCurrency
   2. [话题] — 来源：CoinDesk
   ...
   ```

3. 询问用户：是否要根据这些话题立即生成内容？（接入 /write）

---

## /write [话题] — 内容生成

**参数**：用户可以指定话题，也可以让 scout 自动获取。

1. 如果用户没有指定话题，先运行 scout 获取热点
2. 询问用户：
   - 要为哪个账号生成？（列出 accounts.json 中的账号）
   - 时间段：morning / noon / afternoon / evening
3. 运行生成：
   ```bash
   python3 core/orchestrator.py --mode write --slot {slot} --dry
   ```
4. 展示生成的草稿及质量评分
5. 询问用户：
   - ✅ 直接发布（去掉 --dry 重新运行）
   - ✏️ 修改后发布（接受用户修改，然后发布）
   - ⏭ 跳过
6. 如果用户要修改，接收新文本，用 XPublisher 直接发布：
   ```bash
   python3 -c "
   import asyncio
   from core.publisher_x import post
   asyncio.run(post('{account_key}', '''{modified_text}'''))
   "
   ```

---

## /engage — KOL 互动

1. 先做 dry run 预览：
   ```bash
   python3 core/orchestrator.py --mode engage --dry
   ```

2. 展示将要发布的评论预览（KOL、推文摘要、回复内容、质量分）
3. 询问用户确认，确认后：
   ```bash
   python3 core/orchestrator.py --mode engage
   ```
4. 展示发布结果

---

## /report — 数据报告

```bash
python3 core/orchestrator.py --mode report
```

展示报告内容（同时推送到 TG）。

---

## 内容质量评分说明

向用户解释评分标准：

| 维度 | 权重 | 说明 |
|------|------|------|
| AI腔检测 | -0~48 | 检测 100+ 模板词，越低越好 |
| 内容长度 | ±10 | X 最优 180-260 字符 |
| Hashtag数 | -5/个 | 超过 3 个扣分 |
| 数据/数字 | +5 | 含具体数字加分 |
| 提问结尾 | +6 | 引导互动加分 |
| 个人语气 | +4 | 含「我」「你」等人称加分 |

通过标准：总分 ≥ 70 且 AI腔分 < 50。

---

## 错误处理

- **Cookie 失效**：提示用户重新导出（`config/cookies/{account}_cookies.json`）
- **API Key 缺失**：提示设置 `KIMI_API_KEY` 环境变量
- **Playwright 未安装**：提示 `pip install playwright && playwright install chromium`
- **TG 未配置**：内容改为在终端/Claude 对话中展示，不影响生成

---

## 注意事项

- 账号信息（`config/accounts.json`、`config/cookies/`）在 `.gitignore` 中，不会上传到 Git
- Kimi API Key 也不能提交到 Git
- X 自动化有被封号风险，建议：每天评论不超过 10 条，操作间隔加随机延迟（已内置）
