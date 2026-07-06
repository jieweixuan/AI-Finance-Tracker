# 个人收支记账系统

基于 Python Flask、SQLite、HTML/CSS 的个人收支记录管理系统，支持日常记账、分类筛选、收支统计、AI 自然语言记账和月度消费分析。

## 功能

- 用户注册、登录、退出
- 收入/支出记录新增、删除、列表查看
- 按类型、分类、日期范围筛选
- CSV 导出
- 收入总额、支出总额、结余、分类统计
- DeepSeek API 自然语言记账识别
- DeepSeek API 月度消费分析

## 本地运行

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe app.py
```

打开：

```text
http://127.0.0.1:5000/login
```

## 环境变量

不要把真实密钥写入代码或提交到 GitHub。复制 `.env.example` 的字段到你的部署平台环境变量中。

必填：

```text
SECRET_KEY=替换为随机长字符串
DEEPSEEK_API_KEY=你的 DeepSeek API Key
```

可选：

```text
DEEPSEEK_BASE_URL=https://api.deepseek.com
DEEPSEEK_MODEL=deepseek-v4-flash
DEEPSEEK_TIMEOUT=20
DATABASE_PATH=finance_tracker.db
FLASK_DEBUG=0
```

## 部署

项目包含两类部署配置：

- `vercel.json` + `api/index.py`：用于 Vercel Python Serverless 部署。
- `Procfile` + `runtime.txt`：用于 Render/Railway 等支持 Gunicorn 的 Python 平台。

注意：SQLite 在 Serverless 平台上通常不是持久化存储。Vercel 部署时默认使用临时目录数据库，适合课程演示，不适合正式长期保存数据。正式上线建议换成托管数据库。

## 安全说明

- Flask `SECRET_KEY` 从环境变量读取。
- 用户密码使用 Werkzeug 哈希保存，旧明文密码会在启动时自动迁移为哈希。
- 表单写操作加入 CSRF token。
- 删除记录限定当前登录用户。
- 默认关闭 Flask debug。
- 响应添加基础安全头。
- `.gitignore` 排除 `.env`、本地 SQLite 数据库、虚拟环境和缓存文件。

## 文档

详细使用说明见 [USER_MANUAL.md](USER_MANUAL.md)。
