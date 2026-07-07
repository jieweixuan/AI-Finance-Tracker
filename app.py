from datetime import date, datetime, timedelta
import json
import os
import re
import secrets
import socket
import sqlite3
import tempfile
import urllib.error
import urllib.request
import mimetypes

mimetypes.add_type('text/css', '.css')
mimetypes.add_type('application/javascript', '.js')

from flask import Flask, flash, jsonify, redirect, render_template, request, session, url_for
from werkzeug.security import check_password_hash, generate_password_hash


app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY') or secrets.token_hex(32)
app.config.update(
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE='Lax',
    SESSION_COOKIE_SECURE=os.environ.get('SESSION_COOKIE_SECURE', '').lower() in ('1', 'true', 'yes'),
)

DEFAULT_DATABASE = os.path.join(tempfile.gettempdir(), 'finance_tracker.db') if os.environ.get('VERCEL') else 'finance_tracker.db'
DATABASE = os.environ.get('DATABASE_PATH', DEFAULT_DATABASE)

TRANSACTION_TYPES = {
    'expense': '支出',
    'income': '收入',
}

CATEGORY_OPTIONS = {
    'expense': ['餐饮', '交通', '购物', '娱乐', '居住', '水电杂费', '教育', '医疗', '旅行', '订阅', '礼品', '其他支出'],
    'income': ['工资', '兼职', '奖金', '理财', '报销', '其他收入'],
}

LEGACY_CATEGORY_LABELS = {
    'Entertainment': '娱乐',
    'Food': '餐饮',
    'Utilities': '水电杂费',
    'Education': '教育',
    'Travel expenses': '旅行',
    'Gifts': '礼品',
    'Rent': '居住',
    'Subscriptions': '订阅',
}

PAYMENT_METHOD_LABELS = {
    'WeChat': '微信支付',
    'Alipay': '支付宝',
    'CreditCard': '信用卡',
    'BankCard': '银行卡/储蓄卡',
    'Cash': '现金',
}

LEGACY_PAYMENT_LABELS = {
    'UPI': 'UPI 电子支付',
}

HASH_PREFIXES = ('scrypt:', 'pbkdf2:', 'argon2:')

CATEGORY_ALIASES = {
    # 餐饮
    '早餐': '餐饮', '午餐': '餐饮', '晚餐': '餐饮', '餐费': '餐饮', '吃饭': '餐饮',
    '咖啡': '餐饮', '奶茶': '餐饮', '星巴克': '餐饮', '瑞幸': '餐饮', '麦当劳': '餐饮',
    '肯德基': '餐饮', '外卖': '餐饮', '买菜': '餐饮', '超市': '餐饮', '零食': '餐饮',
    '水果': '餐饮', '宵夜': '餐饮', '聚餐': '餐饮', '火锅': '餐饮', '喜茶': '餐饮',
    '饿了么': '餐饮', '美团外卖': '餐饮', '早饭': '餐饮', '午饭': '餐饮', '晚饭': '餐饮',
    # 交通
    '打车': '交通', '出租车': '交通', '公交': '交通', '地铁': '交通', '车费': '交通',
    '滴滴': '交通', '高铁': '交通', '火车': '交通', '机票': '交通', '加油': '交通',
    '油费': '交通', '停车': '交通', '停车费': '交通', '打车费': '交通', '过路费': '交通',
    '网约车': '交通', '曹操': '交通', 'T3': '交通', '单车': '交通',
    # 购物
    '淘宝': '购物', '京东': '购物', '网购': '购物', '衣服': '购物', '买衣服': '购物',
    '鞋子': '购物', '包包': '购物', '拼多多': '购物', '天猫': '购物', '日用品': '购物',
    '日用': '购物', '护肤': '购物', '化妆品': '购物', '百货': '购物', '电子产品': '购物',
    # 居住
    '房租': '居住', '租金': '居住', '房贷': '居住', '物业': '居住', '物业费': '居住',
    # 水电杂费
    '电费': '水电杂费', '水费': '水电杂费', '话费': '水电杂费', '宽带': '水电杂费',
    '燃气': '水电杂费', '天然气': '水电杂费', '交话费': '水电杂费', '网费': '水电杂费',
    '手机费': '水电杂费', '杂费': '水电杂费',
    # 娱乐 & 旅行
    '电影': '娱乐', '看电影': '娱乐', '游戏': '娱乐', '充值': '娱乐', 'KTV': '娱乐',
    '唱歌': '娱乐', '演出': '娱乐', '门票': '娱乐', '旅游': '旅行', '度假': '旅行',
    # 医疗 & 教育
    '看病': '医疗', '药': '医疗', '买药': '医疗', '挂号': '医疗', '体检': '医疗',
    '书': '教育', '买书': '教育', '课程': '教育', '学费': '教育', '培训': '教育',
    # 收入别名
    '工资收入': '工资', '薪资': '工资', '发工资': '工资', '薪水': '工资', '月薪': '工资',
    '理财收益': '理财', '利息': '理财', '股票': '理财', '基金': '理财', '分红': '理财',
    '发奖金': '奖金', '年终奖': '奖金', '收报销': '报销', '报销款': '报销', '到账': '其他收入',
    '转账': '其他收入', '收红包': '其他收入', '红包': '其他收入',
}


def get_db():
    conn = sqlite3.connect(DATABASE)
    conn.row_factory = sqlite3.Row
    return conn


def row_to_dict(row):
    return dict(row) if row else None


def csrf_token():
    token = session.get('csrf_token')
    if not token:
        token = secrets.token_urlsafe(32)
        session['csrf_token'] = token
    return token


app.jinja_env.globals['csrf_token'] = csrf_token


def validate_csrf():
    return request.form.get('csrf_token') == session.get('csrf_token')


def is_password_hash(value):
    return bool(value) and value.startswith(HASH_PREFIXES)


def verify_user_password(stored_password, provided_password):
    if is_password_hash(stored_password):
        return check_password_hash(stored_password, provided_password)
    return stored_password == provided_password


@app.after_request
def add_security_headers(response):
    response.headers.setdefault('X-Content-Type-Options', 'nosniff')
    response.headers.setdefault('X-Frame-Options', 'DENY')
    response.headers.setdefault('Referrer-Policy', 'strict-origin-when-cross-origin')
    response.headers.setdefault(
        'Content-Security-Policy',
        "default-src 'self'; "
        "script-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net https://cdnjs.cloudflare.com; "
        "style-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net https://cdnjs.cloudflare.com https://fonts.googleapis.com; "
        "font-src 'self' https://fonts.gstatic.com https://cdnjs.cloudflare.com; "
        "img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'self'; form-action 'self'",
    )
    return response


@app.template_filter('zh_category')
def zh_category(value):
    return LEGACY_CATEGORY_LABELS.get(value, value)


@app.template_filter('zh_payment_method')
def zh_payment_method(value):
    label = PAYMENT_METHOD_LABELS.get(value)
    if not label:
        label = LEGACY_PAYMENT_LABELS.get(value, value)
    return label


@app.template_filter('zh_type')
def zh_type(value):
    return TRANSACTION_TYPES.get(value, value)


def init_db():
    conn = get_db()
    c = conn.cursor()
    c.execute('''
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT NOT NULL,
            email TEXT NOT NULL,
            phone TEXT NOT NULL,
            password TEXT NOT NULL
        )
    ''')
    c.execute('''
        CREATE TABLE IF NOT EXISTS transactions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            amount REAL NOT NULL,
            type TEXT NOT NULL DEFAULT 'expense',
            category TEXT NOT NULL,
            date TEXT NOT NULL,
            description TEXT,
            payment_method TEXT NOT NULL,
            FOREIGN KEY (user_id) REFERENCES users (id)
        )
    ''')

    columns = [row['name'] for row in c.execute("PRAGMA table_info(transactions)").fetchall()]
    if 'type' not in columns:
        c.execute("ALTER TABLE transactions ADD COLUMN type TEXT NOT NULL DEFAULT 'expense'")

    users = c.execute('SELECT id, password FROM users').fetchall()
    for user in users:
        if user['password'] and not is_password_hash(user['password']):
            c.execute('UPDATE users SET password = ? WHERE id = ?', (generate_password_hash(user['password']), user['id']))

    conn.commit()
    conn.close()


init_db()


def is_logged_in():
    return 'username' in session and 'user_id' in session


def parse_amount(value):
    try:
        amount = float(value)
    except (TypeError, ValueError):
        return None
    if amount <= 0:
        return None
    return round(amount, 2)


def normalize_transaction_type(value):
    if value in TRANSACTION_TYPES:
        return value
    if value == '收入':
        return 'income'
    if value == '支出':
        return 'expense'
    return None


def normalize_category(transaction_type, value):
    value = LEGACY_CATEGORY_LABELS.get(value, value)
    value = CATEGORY_ALIASES.get(value, value)
    if transaction_type in CATEGORY_OPTIONS and value in CATEGORY_OPTIONS[transaction_type]:
        return value
    return None


def normalize_payment_method(value):
    if not value:
        return None
    if value in PAYMENT_METHOD_LABELS or value in LEGACY_PAYMENT_LABELS:
        return value
    for key, label in PAYMENT_METHOD_LABELS.items():
        if value == label or value == label.replace(' ', '') or value in label:
            return key
    val_lower = str(value).lower()
    if any(k in val_lower for k in ('微信', 'wechat', 'vx', 'v信', '绿泡泡')):
        return 'WeChat'
    if any(k in val_lower for k in ('支', 'alipay', 'zfb', '花呗')):
        return 'Alipay'
    if any(k in val_lower for k in ('信用', '贷记', 'credit')):
        return 'CreditCard'
    if any(k in val_lower for k in ('储蓄', '银行', '借记', '卡', '转账', 'bank')):
        return 'BankCard'
    if any(k in val_lower for k in ('现金', '纸币', '钞', 'cash')):
        return 'Cash'
    if value == 'UPI' or 'upi' in val_lower:
        return 'UPI'
    return None


def validate_date(value):
    try:
        datetime.strptime(value, '%Y-%m-%d')
        return value
    except (TypeError, ValueError):
        return None


def validate_transaction_payload(payload):
    errors = []
    transaction_type = normalize_transaction_type(payload.get('type'))
    if not transaction_type:
        errors.append('请选择收入或支出类型。')

    category = normalize_category(transaction_type, payload.get('category')) if transaction_type else None
    if transaction_type and not category:
        errors.append('请选择与收支类型匹配的分类。')

    amount = parse_amount(payload.get('amount'))
    if amount is None:
        errors.append('金额必须大于 0。')

    transaction_date = validate_date(payload.get('date'))
    if not transaction_date:
        errors.append('请选择有效日期。')

    payment_method = normalize_payment_method(payload.get('payment_method'))
    if not payment_method:
        errors.append('请选择支付方式。')

    description = (payload.get('description') or payload.get('notes') or '').strip()

    if errors:
        return None, errors

    return {
        'type': transaction_type,
        'category': category,
        'amount': amount,
        'date': transaction_date,
        'payment_method': payment_method,
        'description': description,
    }, []


def get_llm_config():
    return {
        'api_key': (os.environ.get('DEEPSEEK_API_KEY') or os.environ.get('LLM_API_KEY', '')).strip(),
        'base_url': (os.environ.get('DEEPSEEK_BASE_URL') or os.environ.get('LLM_BASE_URL', 'https://api.deepseek.com')).strip().rstrip('/'),
        'model': (os.environ.get('DEEPSEEK_MODEL') or os.environ.get('LLM_MODEL', 'deepseek-v4-flash')).strip(),
        'timeout': float(os.environ.get('DEEPSEEK_TIMEOUT') or os.environ.get('LLM_TIMEOUT', '20') or 20),
    }


def extract_json_object(text):
    if not text:
        raise ValueError('模型返回为空。')
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        match = re.search(r'\{.*\}', text, re.S)
        if not match:
            raise ValueError('模型没有返回 JSON 对象。')
        return json.loads(match.group(0))


def post_chat_completion(messages, schema, schema_name):
    config = get_llm_config()
    if not config['api_key']:
        return None, '未配置 DEEPSEEK_API_KEY，请先在环境变量中配置 DeepSeek API 密钥。'

    response_format = {'type': 'json_object'} if 'deepseek.com' in config['base_url'] else {
        'type': 'json_schema',
        'json_schema': {
            'name': schema_name,
            'strict': True,
            'schema': schema,
        },
    }
    payload = {
        'model': config['model'],
        'messages': messages,
        'temperature': 0,
        'response_format': response_format,
        'max_tokens': 1000,
    }
    request_body = json.dumps(payload, ensure_ascii=False).encode('utf-8')
    req = urllib.request.Request(
        f"{config['base_url']}/chat/completions",
        data=request_body,
        headers={
            'Authorization': f"Bearer {config['api_key']}",
            'Content-Type': 'application/json',
        },
        method='POST',
    )

    try:
        with urllib.request.urlopen(req, timeout=config['timeout']) as response:
            body = response.read().decode('utf-8')
    except urllib.error.HTTPError as exc:
        return None, f'模型接口返回错误：HTTP {exc.code}'
    except urllib.error.URLError as exc:
        return None, f'模型接口连接失败：{exc.reason}'
    except (TimeoutError, socket.timeout):
        return None, '模型接口请求超时，请稍后重试。'

    try:
        data = json.loads(body)
        content = data['choices'][0]['message']['content']
        return extract_json_object(content), None
    except (KeyError, IndexError, TypeError, ValueError, json.JSONDecodeError) as exc:
        return None, f'模型返回格式异常：{exc}'


def transaction_schema():
    return {
        'type': 'object',
        'additionalProperties': False,
        'required': ['date', 'type', 'category', 'amount', 'payment_method', 'description', 'confidence', 'missing_fields'],
        'properties': {
            'date': {'type': 'string', 'description': '交易日期，格式 YYYY-MM-DD'},
            'type': {'type': 'string', 'enum': list(TRANSACTION_TYPES.keys())},
            'category': {'type': 'string', 'enum': CATEGORY_OPTIONS['expense'] + CATEGORY_OPTIONS['income']},
            'amount': {'type': 'number'},
            'payment_method': {'type': 'string', 'enum': list(PAYMENT_METHOD_LABELS.keys())},
            'description': {'type': 'string'},
            'confidence': {'type': 'number'},
            'missing_fields': {'type': 'array', 'items': {'type': 'string'}},
        },
    }


def summary_schema():
    return {
        'type': 'object',
        'additionalProperties': False,
        'required': ['summary', 'top_categories', 'suggestions'],
        'properties': {
            'summary': {'type': 'string'},
            'top_categories': {'type': 'array', 'items': {'type': 'string'}},
            'suggestions': {'type': 'array', 'items': {'type': 'string'}},
        },
    }


def parse_transaction_with_llm(text):
    today_dt = date.today()
    today_str = today_dt.strftime('%Y-%m-%d')
    yesterday_str = (today_dt - timedelta(days=1)).strftime('%Y-%m-%d')
    day_before_str = (today_dt - timedelta(days=2)).strftime('%Y-%m-%d')
    weekdays = ["星期一", "星期二", "星期三", "星期四", "星期五", "星期六", "星期日"]
    weekday_str = weekdays[today_dt.weekday()]

    system_prompt = f"""你是专业、高精准的个人收支记账 AI 识别引擎。你负责分析用户输入的自然语言描述，提取准确的财务交易字段，并严格输出合法的 JSON 对象。

### 【时间维度准则】
1. 今天是 {today_str}（{weekday_str}）。
2. 相对日期推算规范：
   - "昨天" = {yesterday_str}；"前天" = {day_before_str}；"大前天" = 减3天；"明天" = 加1天（如预期支出）。
   - "上周X"：从今天向过去推算最近的那个星期X；"前几天"/"几天前"：默认推算为 {day_before_str}；如果无法确定具体日期，默认使用今天日期 "{today_str}"。
3. 日期必须输出严格的 YYYY-MM-DD 格式。

### 【收支类型 (type) 判断准则】
- expense（支出）：花钱、消费、购买、缴费、付款、发出去的钱、转账给他人。
- income（收入）：工资、兼职、到账、奖金、报销款、理财收益、收红包、别人转给我。

### 【分类 (category) 语义指导】
必须严格从枚举中选择最贴切的分类：
- 餐饮：在外吃饭、外卖、星巴克/瑞幸/咖啡、奶茶、买菜、水果、超市食品、零食、火锅、聚餐。
- 交通：打车、滴滴、地铁、公交、高铁、机票、加油、油费、停车费、过路费、打车费。
- 购物：淘宝/京东/拼多多等网购、衣服、鞋包、日用百货、化妆品、电子产品。
- 居住：房租、租金、房贷、物业费。
- 水电杂费：电费、水费、话费、宽带费、天然气费、交话费、充值话费。
- 娱乐：电影、游戏、演出、门票、KTV。
- 医疗：看病、买药、体检、挂号。
- 教育：买书、课程、培训、学费。
- 旅行：旅游、酒店、度假。
- 工资：工资收入、薪资、发薪水、月薪。
- 奖金：年终奖、奖金、绩效奖。
- 理财：理财收益、股票、基金分红、利息。
- 报销：发票报销、公司报销到账。
- 其他支出 / 其他收入：如果不属于以上分类，选此项。

### 【支付方式 (payment_method) 推断准则】
必须选自枚举：WeChat, Alipay, CreditCard, BankCard, Cash。
1. 关键词对应：
   - 微信 / V信 / vx / 绿泡泡 -> WeChat
   - 支付宝 / 支 / 花呗 / 余额宝 -> Alipay
   - 信用卡 / 贷记卡 / 刷卡 -> CreditCard
   - 银行卡 / 储蓄卡 / 借记卡 / 转账 -> BankCard
   - 现金 / 纸币 -> Cash
2. **兜底推断**：如果用户语句中完全没有提及支付方式，默认填入 WeChat（微信支付）或 Alipay（支付宝），绝不可为空或留空！

### 【金额与备注】
- amount：必须为正浮点数（如 28.0, 35.5）。如果提到“两百/俩百”->200.0，“两块五”->2.5，“三十八”->38.0。
- description：简炼总结交易内容（如“瑞幸生椰拿铁”、“打车去公司”、“交本月房租”），保留用户的核心消费细节。
- confidence：0.0 到 1.0 之间的置信度评分。
- missing_fields：如果某个必填要素用户表达极其模糊或无法确定，将该字段名加入数组。

### 【Few-Shot 典型示例】
示例 1（品牌咖啡 + 昨天 + 微信）：
输入："昨天中午跟同事在瑞幸买了三杯生椰拿铁，花了我58块钱，用微信付款的"
输出：{{"date": "{yesterday_str}", "type": "expense", "category": "餐饮", "amount": 58.0, "payment_method": "WeChat", "description": "瑞幸生椰拿铁", "confidence": 0.98, "missing_fields": []}}

示例 2（发票报销进账 + 前天 + 支付宝）：
输入："前天打车去客户公司，今天发票报销到账了145元，存到支付宝了"
输出：{{"date": "{day_before_str}", "type": "income", "category": "报销", "amount": 145.0, "payment_method": "Alipay", "description": "发票报销到账", "confidence": 0.95, "missing_fields": []}}

示例 3（水电话费 + 模糊支付兜底）：
输入："交了个季度的宽带费和电费一共480块"
输出：{{"date": "{today_str}", "type": "expense", "category": "水电杂费", "amount": 480.0, "payment_method": "WeChat", "description": "交季度宽带费和电费", "confidence": 0.9, "missing_fields": []}}

示例 4（网购日用品 + 信用卡）：
输入："晚上在淘宝上买了一堆纸巾和洗衣液，刷信用卡218.5元"
输出：{{"date": "{today_str}", "type": "expense", "category": "购物", "amount": 218.5, "payment_method": "CreditCard", "description": "淘宝买纸巾和洗衣液", "confidence": 0.96, "missing_fields": []}}

示例 5（发工资 + 银行卡）：
输入："7月份工资到账12500元，直接进储蓄卡了"
输出：{{"date": "{today_str}", "type": "income", "category": "工资", "amount": 12500.0, "payment_method": "BankCard", "description": "7月份工资到账", "confidence": 0.99, "missing_fields": []}}"""
    messages = [
        {'role': 'system', 'content': system_prompt},
        {'role': 'user', 'content': text},
    ]
    result, error = post_chat_completion(messages, transaction_schema(), 'transaction_parse')
    if error:
        return None, error

    payload = {
        'date': result.get('date'),
        'type': result.get('type'),
        'category': result.get('category'),
        'amount': result.get('amount'),
        'payment_method': result.get('payment_method') or 'WeChat',
        'description': result.get('description') or text,
    }
    normalized, errors = validate_transaction_payload(payload)
    if errors:
        return None, 'AI 识别结果不完整：' + ' '.join(errors)

    normalized['confidence'] = result.get('confidence', 0)
    normalized['missing_fields'] = result.get('missing_fields', [])
    return normalized, None


def generate_monthly_summary(user_id):
    current_month = date.today().strftime('%Y-%m')
    conn = get_db()
    rows = conn.execute(
        '''
        SELECT type, category, SUM(amount) AS total
        FROM transactions
        WHERE user_id = ? AND strftime('%Y-%m', date) = ?
        GROUP BY type, category
        ORDER BY total DESC
        ''',
        (user_id, current_month),
    ).fetchall()
    conn.close()

    if not rows:
        return {
            'summary': '本月暂无收支记录，请先新增记录后再生成分析。',
            'top_categories': [],
            'suggestions': ['可以先记录餐饮、交通、工资等日常收支，方便形成月度趋势。'],
        }, None

    facts = [
        {
            'type': TRANSACTION_TYPES.get(row['type'], row['type']),
            'category': zh_category(row['category']),
            'total': round(row['total'] or 0, 2),
        }
        for row in rows
    ]
    messages = [
        {
            'role': 'system',
            'content': (
                '你是个人收支分析助手。根据用户本月收支汇总生成简短中文摘要、主要支出分类和可执行节省建议。'
                '必须返回 json 对象，示例 json：{"summary":"本月支出集中在餐饮。",'
                '"top_categories":["餐饮"],"suggestions":["控制外食频率"]}'
            ),
        },
        {'role': 'user', 'content': json.dumps({'month': current_month, 'records': facts}, ensure_ascii=False)},
    ]
    return post_chat_completion(messages, summary_schema(), 'monthly_summary')


@app.route('/')
def index():
    if not is_logged_in():
        return redirect(url_for('login'))

    user_id = session['user_id']
    username = session['username']
    conn = get_db()
    row = conn.execute(
        '''
        SELECT
            SUM(CASE WHEN type = 'income' THEN amount ELSE 0 END) AS total_income,
            SUM(CASE WHEN type = 'expense' THEN amount ELSE 0 END) AS total_expense
        FROM transactions
        WHERE user_id = ?
        ''',
        (user_id,),
    ).fetchone()
    conn.close()

    total_income = round(row['total_income'] or 0, 2)
    total_expense = round(row['total_expense'] or 0, 2)
    balance = round(total_income - total_expense, 2)
    return render_template(
        'index.html',
        username=username,
        total_income=total_income,
        total_expense=total_expense,
        balance=balance,
    )


@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        if not validate_csrf():
            flash('表单已过期，请刷新页面后重试。', 'error')
            return redirect(url_for('login'))
        username = request.form['username']
        password = request.form['password']
        conn = get_db()
        user = conn.execute(
            'SELECT id, username, password FROM users WHERE username = ?',
            (username,),
        ).fetchone()
        if user and verify_user_password(user['password'], password):
            if not is_password_hash(user['password']):
                conn.execute('UPDATE users SET password = ? WHERE id = ?', (generate_password_hash(password), user['id']))
                conn.commit()
            conn.close()
            session['user_id'] = user['id']
            session['username'] = user['username']
            return redirect(url_for('index'))
        conn.close()
        flash('用户名或密码错误，请重试。', 'error')
    return render_template('login.html')


@app.route('/logout', methods=['POST'])
def logout():
    if not validate_csrf():
        flash('表单已过期，请刷新页面后重试。', 'error')
        return redirect(url_for('index') if is_logged_in() else url_for('login'))
    session.pop('user_id', None)
    session.pop('username', None)
    session.pop('csrf_token', None)
    return redirect(url_for('login'))


@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        if not validate_csrf():
            flash('表单已过期，请刷新页面后重试。', 'error')
            return redirect(url_for('register'))
        username = request.form['username']
        email = request.form['email']
        phone = request.form['phone']
        password = request.form['password']
        conn = get_db()
        existing_user = conn.execute('SELECT * FROM users WHERE username = ?', (username,)).fetchone()
        if existing_user:
            conn.close()
            flash('用户名已存在，请换一个用户名。', 'error')
        else:
            conn.execute(
                'INSERT INTO users (username, email, phone, password) VALUES (?, ?, ?, ?)',
                (username, email, phone, generate_password_hash(password)),
            )
            conn.commit()
            conn.close()
            flash('注册成功，请登录。', 'success')
            return redirect(url_for('login'))
    return render_template('register.html')


@app.route('/transactions')
def transactions():
    if not is_logged_in():
        return redirect(url_for('login'))

    filters = {
        'type': request.args.get('type', '').strip(),
        'category': request.args.get('category', '').strip(),
        'start_date': request.args.get('start_date', '').strip(),
        'end_date': request.args.get('end_date', '').strip(),
    }

    clauses = ['user_id = ?']
    params = [session['user_id']]
    if filters['type'] in TRANSACTION_TYPES:
        clauses.append('type = ?')
        params.append(filters['type'])
    if filters['category']:
        clauses.append('category = ?')
        params.append(filters['category'])
    if validate_date(filters['start_date']):
        clauses.append('date >= ?')
        params.append(filters['start_date'])
    if validate_date(filters['end_date']):
        clauses.append('date <= ?')
        params.append(filters['end_date'])

    conn = get_db()
    rows = conn.execute(
        f"SELECT * FROM transactions WHERE {' AND '.join(clauses)} ORDER BY date DESC, id DESC",
        params,
    ).fetchall()
    conn.close()

    return render_template(
        'transaction.html',
        transactions=[row_to_dict(row) for row in rows],
        username=session['username'],
        filters=filters,
        transaction_types=TRANSACTION_TYPES,
        category_options=CATEGORY_OPTIONS,
        payment_method_labels=PAYMENT_METHOD_LABELS,
    )


@app.route('/add_transaction', methods=['POST'])
def add_transaction():
    if not is_logged_in():
        return redirect(url_for('login'))
    if not validate_csrf():
        flash('表单已过期，请刷新页面后重试。', 'error')
        return redirect(url_for('transactions'))

    normalized, errors = validate_transaction_payload(request.form)
    if errors:
        for error in errors:
            flash(error, 'error')
        return redirect(url_for('transactions'))

    conn = get_db()
    conn.execute(
        '''
        INSERT INTO transactions (user_id, amount, type, category, date, description, payment_method)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        ''',
        (
            session['user_id'],
            normalized['amount'],
            normalized['type'],
            normalized['category'],
            normalized['date'],
            normalized['description'],
            normalized['payment_method'],
        ),
    )
    conn.commit()
    conn.close()
    flash('交易记录已保存。', 'success')
    return redirect(url_for('transactions'))


@app.route('/delete_transaction/<int:transaction_id>', methods=['POST'])
def delete_transaction(transaction_id):
    if not is_logged_in():
        flash('请先登录后再删除交易记录。', 'error')
        return redirect(url_for('login'))
    if not validate_csrf():
        flash('表单已过期，请刷新页面后重试。', 'error')
        return redirect(url_for('transactions'))

    conn = get_db()
    conn.execute('DELETE FROM transactions WHERE id = ? AND user_id = ?', (transaction_id, session['user_id']))
    conn.commit()
    conn.close()
    flash('交易记录已删除。', 'success')
    return redirect(url_for('transactions'))


@app.route('/daily_spending_data')
def daily_spending_data():
    if not is_logged_in():
        return redirect(url_for('login'))

    current_month = date.today().strftime('%Y-%m')
    conn = get_db()
    data = conn.execute(
        '''
        SELECT date, SUM(amount) AS total
        FROM transactions
        WHERE user_id = ? AND type = 'expense' AND strftime('%Y-%m', date) = ?
        GROUP BY date
        ORDER BY date
        ''',
        (session['user_id'], current_month),
    ).fetchall()
    conn.close()
    return jsonify({'labels': [row['date'] for row in data], 'amounts': [row['total'] for row in data]})


@app.route('/monthly_spending_data')
def monthly_spending_data():
    if not is_logged_in():
        return redirect(url_for('login'))

    conn = get_db()
    data = conn.execute(
        '''
        SELECT strftime('%Y-%m', date) AS month, SUM(amount) AS total
        FROM transactions
        WHERE user_id = ? AND type = 'expense'
        GROUP BY month
        ORDER BY month
        ''',
        (session['user_id'],),
    ).fetchall()
    conn.close()
    labels = [datetime.strptime(row['month'], '%Y-%m').strftime('%Y年%m月') for row in data if row['month']]
    amounts = [row['total'] for row in data if row['month']]
    return jsonify({'labels': labels, 'amounts': amounts})


@app.route('/statistics')
def statistics():
    if not is_logged_in():
        return redirect(url_for('login'))

    user_id = session['user_id']
    conn = get_db()
    totals = conn.execute(
        '''
        SELECT
            SUM(CASE WHEN type = 'income' THEN amount ELSE 0 END) AS total_income,
            SUM(CASE WHEN type = 'expense' THEN amount ELSE 0 END) AS total_expense
        FROM transactions
        WHERE user_id = ?
        ''',
        (user_id,),
    ).fetchone()
    category_rows = conn.execute(
        '''
        SELECT type, category, SUM(amount) AS total
        FROM transactions
        WHERE user_id = ?
        GROUP BY type, category
        ORDER BY type, total DESC
        ''',
        (user_id,),
    ).fetchall()
    top_expense_rows = conn.execute(
        '''
        SELECT category, SUM(amount) AS total
        FROM transactions
        WHERE user_id = ? AND type = 'expense'
        GROUP BY category
        ORDER BY total DESC
        LIMIT 5
        ''',
        (user_id,),
    ).fetchall()
    conn.close()

    total_income = round(totals['total_income'] or 0, 2)
    total_expense = round(totals['total_expense'] or 0, 2)
    return render_template(
        'statistics.html',
        total_income=total_income,
        total_expense=total_expense,
        balance=round(total_income - total_expense, 2),
        category_totals=[row_to_dict(row) for row in category_rows],
        top_expense_categories=[row_to_dict(row) for row in top_expense_rows],
    )


@app.route('/ai/parse_transaction', methods=['POST'])
def ai_parse_transaction():
    if not is_logged_in():
        return jsonify({'ok': False, 'error': '请先登录后再使用 AI 记账助手。'}), 401

    text = (request.get_json(silent=True) or {}).get('text', '').strip()
    if not text:
        return jsonify({'ok': False, 'error': '请输入一段记账描述，例如：今天午餐花了28元。'}), 400
    if len(text) > 500:
        return jsonify({'ok': False, 'error': '记账描述不能超过 500 个字符。'}), 400

    parsed, error = parse_transaction_with_llm(text)
    if error:
        return jsonify({'ok': False, 'error': error}), 200
    return jsonify({'ok': True, 'transaction': parsed})


@app.route('/ai/monthly_summary', methods=['POST'])
def ai_monthly_summary():
    if not is_logged_in():
        return jsonify({'ok': False, 'error': '请先登录后再生成分析。'}), 401

    summary, error = generate_monthly_summary(session['user_id'])
    if error:
        return jsonify({'ok': False, 'error': error}), 200
    return jsonify({'ok': True, 'analysis': summary})


@app.route('/ai/voice_to_text', methods=['POST'])
def ai_voice_to_text():
    if not is_logged_in():
        return jsonify({'ok': False, 'error': '请先登录后再使用语音功能。'}), 401

    audio_file = request.files.get('audio')
    if not audio_file:
        return jsonify({'ok': False, 'error': '没有检测到录音数据。'}), 400

    hf_token = (os.environ.get('HUGGINGFACE_API_TOKEN') or '').strip()
    if not hf_token:
        return jsonify({'ok': False, 'error': '服务器未配置 HUGGINGFACE_API_TOKEN。'}), 500

    API_URL = "https://api-inference.huggingface.co/models/openai/whisper-large-v3"
    req = urllib.request.Request(
        API_URL,
        data=audio_file.read(),
        headers={
            'Authorization': f"Bearer {hf_token}",
            'Content-Type': 'audio/wav'
        },
        method='POST'
    )

    try:
        # 设置较大的超时，防 Hugging Face 服务器冷启动
        with urllib.request.urlopen(req, timeout=30) as response:
            body = response.read().decode('utf-8')
            result = json.loads(body)
            transcribed_text = result.get('text', '').strip()
            if not transcribed_text:
                return jsonify({'ok': False, 'error': '未能识别出语音，请大声且清晰地说话。'}), 200
            return jsonify({'ok': True, 'text': transcribed_text})
    except urllib.error.HTTPError as exc:
        try:
            err_detail = json.loads(exc.read().decode('utf-8')).get('error', f'HTTP {exc.code}')
        except Exception:
            err_detail = f'HTTP {exc.code}'
        return jsonify({'ok': False, 'error': f"语音识别接口错误: {err_detail}"}), 500
    except Exception as e:
        return jsonify({'ok': False, 'error': f"语音识别请求失败: {str(e)}"}), 500


if __name__ == '__main__':
    app.run(debug=os.environ.get('FLASK_DEBUG', '').lower() in ('1', 'true', 'yes'))
