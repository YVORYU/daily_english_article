# Daily English Article Pusher

每日自动抓取英语新闻文章（CET-6 / IELTS 难度），翻译为中文，推送到飞书。

推送格式：中文问候 → 文章标题（中英） → 英文原文 → 中文翻译

---

## 目录

- [功能特性](#功能特性)
- [推送模式](#推送模式)
- [快速开始](#快速开始)
- [部署方式](#部署方式)
- [日常管理](#日常管理)
- [配置参考](#配置参考)
- [翻译服务](#翻译服务)
- [常见问题](#常见问题)

---

## 功能特性

- 自动抓取 Breaking News English 最新文章
- 优先选择 Level 6（CET-6 / IELTS）难度文章
- 智能过滤练习题，只保留正文
- 支持五种翻译引擎：腾讯云（推荐）/ MyMemory / LibreTranslate / DeepL / OpenAI
- 支持两种飞书推送模式：Webhook（群聊）/ App（私聊）
- 自动去重，不会重复推送同一篇文章
- 翻译标题和正文，双语文章推送
- 支持 systemd 定时器，每天自动推送

---

## 推送模式

| 模式 | 推送目标 | 难度 | 推荐指数 |
|------|---------|------|---------|
| webhook | 群聊 | 简单 | 企业版推荐 |
| app | 个人私聊 | 中等 | 个人版推荐 |

### Webhook 模式（群聊推送）

> 需要飞书企业版，个人版不支持自定义群机器人。

1. 打开飞书，创建一个群聊（或使用已有群聊）
2. 点击群名 → 设置 → 机器人 → 添加机器人 → 自定义机器人
3. 给机器人取名（如"每日英语"）
4. 复制 Webhook URL（格式：`https://open.feishu.cn/open-apis/bot/v2/hook/xxxxxxx`）
5. 可选：设置签名密钥增加安全性

### App 模式（私聊推送）

> 飞书个人版可用，消息直接推送到个人聊天。

1. 登录 [飞书开放平台](https://open.feishu.cn/app)，创建应用
2. 在权限管理中开通 `im:message:send_as_bot`
3. 在应用能力中开启机器人能力
4. 创建应用版本并发布（至少草稿版）
5. 获取 `App ID`、`App Secret`、`open_id`

#### 如何获取 open_id

**方法一：API 调试台（最简单）**

在飞书开放平台 → 你的应用 → API 调试台，搜索"根据手机号获取用户 ID"接口调用即可。

**方法二：让机器人发消息获取**

应用发布后给机器人发一条消息，在应用后台的事件订阅中查看发送者的 open_id。

---

## 快速开始

### 1. 克隆项目

```bash
git clone <your-repo-url>
cd daily_english_article
```

### 2. 安装依赖

```bash
pip install -r requirements.txt
```

### 3. 配置环境变量

```bash
cp .env.example .env
nano .env
```

**App 模式（个人版推荐）：**

```ini
FEISHU_PUSH_MODE=app
FEISHU_APP_ID=cli_xxxxxxxxxxxxxxxxxxxx
FEISHU_APP_SECRET=你的App Secret
FEISHU_RECEIVER_ID=ou_xxxxxxxxxxxxxxxxxxxx
TRANSLATION_PROVIDER=tencent
TENCENT_SECRET_ID=你的SecretId
TENCENT_SECRET_KEY=你的SecretKey
```

**Webhook 模式（企业版推荐）：**

```ini
FEISHU_PUSH_MODE=webhook
FEISHU_WEBHOOK_URL=https://open.feishu.cn/open-apis/bot/v2/hook/你的webhook地址
FEISHU_WEBHOOK_SECRET=
TRANSLATION_PROVIDER=tencent
TENCENT_SECRET_ID=你的SecretId
TENCENT_SECRET_KEY=你的SecretKey
```

### 4. 测试运行

```bash
python daily_english_article.py
```

配置正确的话，飞书会收到一条推送消息。

---

## 部署方式

### 方式一：一键部署脚本（推荐）

项目自带 `deploy.sh` 脚本，在服务器上执行即可完成所有配置。

```bash
chmod +x deploy.sh
sudo ./deploy.sh
```

#### 部署后确认配置

```bash
sudo cat /opt/english-article/.env
sudo nano /opt/english-article/.env   # 如需修改
```

#### 手动测试一次

```bash
sudo systemctl start daily-english.service
sudo journalctl -u daily-english.service -f
```

### 方式二：手动部署

```bash
# 1. 上传到服务器
scp -r daily_english_article/ user@server:~/

# 2. 安装
ssh user@server
cd ~/daily_english_article
sudo mkdir -p /opt/english-article/data
sudo cp daily_english_article.py requirements.txt .env /opt/english-article/
cd /opt/english-article
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
deactivate

# 3. 安装 systemd 服务和定时器
sudo cp ~/daily_english_article/daily-english.service /etc/systemd/system/
sudo cp ~/daily_english_article/daily-english.timer /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable daily-english.timer
sudo systemctl start daily-english.timer
```

---

## 日常管理

### 定时任务管理

```bash
# 查看定时器状态
sudo systemctl status daily-english.timer

# 手动触发一次推送
sudo systemctl start daily-english.service

# 暂停 / 恢复 / 禁用
sudo systemctl stop daily-english.timer
sudo systemctl start daily-english.timer
sudo systemctl disable daily-english.timer
```

### 修改推送时间

```bash
sudo nano /etc/systemd/system/daily-english.timer
# 修改 OnCalendar，例如：
#   每天早上 7:00 → OnCalendar=*-*-* 07:00:00
#   仅工作日 8:00 → OnCalendar=*-*-* 08:00:00 Mon-Fri
sudo systemctl daemon-reload
sudo systemctl restart daily-english.timer
```

### 查看日志

```bash
sudo journalctl -u daily-english.service --no-pager -n 50
sudo journalctl -u daily-english.service -f
```

### 重新推送已发过的文章

```bash
rm /opt/english-article/data/sent_articles.json
sudo systemctl start daily-english.service
```

### 彻底卸载

```bash
# 1. 停止并禁用定时器和服务
sudo systemctl stop daily-english.timer
sudo systemctl disable daily-english.timer
sudo systemctl stop daily-english.service

# 2. 删除 systemd 配置
sudo rm /etc/systemd/system/daily-english.service
sudo rm /etc/systemd/system/daily-english.timer
sudo systemctl daemon-reload

# 3. 删除项目文件（含推送记录、日志、虚拟环境）
sudo rm -rf /opt/english-article

# 4. （可选）撤销腾讯云子用户密钥
# https://console.cloud.tencent.com/cam/capi → 删除对应密钥
```

---

## 配置参考

完整 `.env` 配置项：

```ini
# -- 推送模式（必填） --
# app = 私聊推送（个人版可用），webhook = 群聊推送（需企业版）
FEISHU_PUSH_MODE=app

# -- App 模式（FEISHU_PUSH_MODE=app 时必填） --
FEISHU_APP_ID=cli_xxxxxxxxxxxxxxxxxxxx
FEISHU_APP_SECRET=xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
FEISHU_RECEIVER_ID=ou_xxxxxxxxxxxxxxxxxxxx

# -- Webhook 模式（FEISHU_PUSH_MODE=webhook 时必填） --
FEISHU_WEBHOOK_URL=https://open.feishu.cn/open-apis/bot/v2/hook/xxxxxxxxxxxxxxx
FEISHU_WEBHOOK_SECRET=

# -- 翻译服务（默认 tencent） --
TRANSLATION_PROVIDER=tencent

# -- 腾讯云翻译（TRANSLATION_PROVIDER=tencent 时必填） --
TENCENT_SECRET_ID=AKIDxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
TENCENT_SECRET_KEY=xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx

# -- 其他翻译服务（可选） --
TRANSLATION_API_KEY=
TRANSLATION_API_URL=

# -- 数据目录 --
DATA_DIR=./data
```

---

## 翻译服务

### 腾讯云机器翻译（推荐）

- 免费 500 万字符/月，国内稳定可用
- 需注册腾讯云获取 SecretId/SecretKey
- 支持子账号 + 自定义策略限制只允许翻译权限

**设置步骤：**

1. 注册腾讯云：https://cloud.tencent.com
2. 创建密钥：https://console.cloud.tencent.com/cam/capi
3. 开通机器翻译：https://console.cloud.tencent.com/tmt
4. （推荐）创建子用户并绑定自定义策略，限制仅 `tmt:TextTranslate` 权限

```ini
TRANSLATION_PROVIDER=tencent
TENCENT_SECRET_ID=你的SecretId
TENCENT_SECRET_KEY=你的SecretKey
```

### MyMemory（免费，无需注册）

- 免费，无需任何注册和密钥
- 国内可直接访问
- 匿名用户每天 5000 字符

```ini
TRANSLATION_PROVIDER=google
TRANSLATION_API_KEY=你的邮箱    # 可选，提高额度
```

### LibreTranslate（免费，不太稳定）

- 免费，无需 API Key
- 公共服务器经常不可用

```ini
TRANSLATION_PROVIDER=libre
```

也可自建服务器：

```bash
docker run -d -p 5000:5000 libretranslate/libretranslate
```

```ini
TRANSLATION_PROVIDER=libre
TRANSLATION_API_URL=http://localhost:5000/translate
```

### DeepL（翻译质量好）

- 翻译质量优秀，需注册获取 API Key
- 免费版每月 50 万字符

```ini
TRANSLATION_PROVIDER=deepl
TRANSLATION_API_KEY=你的DeepL API Key
```

### OpenAI（智能翻译）

- 翻译流畅，需付费 API Key
- 支持自定义代理地址

```ini
TRANSLATION_PROVIDER=openai
TRANSLATION_API_KEY=sk-你的OpenAI Key
TRANSLATION_API_URL=               # 留空或填代理地址
```

---

## 常见问题

| 错误信息 | 原因 | 解决 |
|---------|------|------|
| `FEISHU_WEBHOOK_URL is empty` | .env 缺失 | 检查 .env 文件 |
| `Token error` | App ID/Secret 错误 | 检查 FEISHU_APP_ID 和 FEISHU_APP_SECRET |
| `Send failed: permission denied` | 权限不足 | 开通 `im:message:send_as_bot` |
| `Tencent TMT error` | 翻译配置错误 | 检查 TENCENT_SECRET_ID/SECRET_KEY，确认已开通 TMT 服务 |
| `No articles found` | 网站抓取失败 | 检查服务器能否访问 breakingnewsenglish.com |
| `Already sent, skipping` | 今天已推送过 | 正常去重，见 `data/sent_articles.json` |

### Q: 如何限制腾讯云密钥只用于翻译？

创建子用户并绑定自定义策略，策略内容：

```json
{
  "version": "2.0",
  "statement": [
    {
      "effect": "allow",
      "action": "tmt:TextTranslate",
      "resource": "*"
    }
  ]
}
```

---

## 运行架构

```
systemd 定时器 (daily-english.timer)
  |  每天早上 8:00 触发
  v
Python 脚本运行
  |-- 1. 抓取 breakingnewsenglish.com 最新文章
  |-- 2. 过滤练习题，只保留正文
  |-- 3. 调用翻译引擎（腾讯云 / DeepL / ...）
  |-- 4. 组装中英双语消息
  |-- 5. 飞书推送
  |     |-- Webhook → 群聊
  |     +-- App → 私聊
  +-- 6. 记录去重，退出
```

---

## 文件结构

```
daily_english_article/
  |-- daily_english_article.py    # 主脚本
  |-- requirements.txt            # Python 依赖
  |-- .env.example                # 配置模板
  |-- .env                        # 实际配置（不上传）
  |-- deploy.sh                   # 一键部署脚本
  |-- daily-english.service       # systemd 服务
  |-- daily-english.timer         # systemd 定时器
  +-- README.md

部署后服务器：
/opt/english-article/
  |-- daily_english_article.py
  |-- .env
  |-- .venv/
  |-- requirements.txt
  |-- data/
  |   |-- sent_articles.json
  |   +-- article_*.json
  +-- logs/
```