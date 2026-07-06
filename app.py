from datetime import date, datetime
import json
import os
import re
import secrets
import socket
import sqlite3
import tempfile
import urllib.error
import urllib.request

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
    'UPI': 'UPI 电子支付',
    'Cash': '现金',
}

HASH_PREFIXES = ('scrypt:', 'pbkdf2:', 'argon2:')

CATEGORY_ALIASES = {
    '早餐': '餐饮',
    '午餐': '餐饮',
    '晚餐': '餐饮',
    '餐费': '餐饮',
    '吃饭': '餐饮',
    '咖啡': '餐饮',
    '奶茶': '餐饮',
    '打车': '交通',
    '出租车': '交通',
    '公交': '交通',
    '地铁': '交通',
    '车费': '交通',
    '工资收入': '工资',
    '薪资': '工资',
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
    return PAYMENT_METHOD_LABELS.get(value, value)


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
    if value in PAYMENT_METHOD_LABELS:
        return value
    for key, label in PAYMENT_METHOD_LABELS.items():
        if value == label or value == label.replace(' ', ''):
            return key
    if value in ('现金支付', '现金付款'):
        return 'Cash'
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
    today = date.today().strftime('%Y-%m-%d')
    messages = [
        {
            'role': 'system',
            'content': (
                '你是个人收支记账助手。只从用户文本中提取记账字段，必须返回 json 对象。'
                f'今天日期是 {today}。分类必须从给定枚举中选择；无法确定时选择最接近的分类，'
                '并在 missing_fields 中说明需要用户确认的字段。金额必须是正数；type 只能是 income 或 expense。'
                '示例 json：{"date":"2026-07-06","type":"expense","category":"餐饮","amount":28,'
                '"payment_method":"Cash","description":"午餐","confidence":0.9,"missing_fields":[]}'
            ),
        },
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
        'payment_method': result.get('payment_method') or 'Cash',
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


if __name__ == '__main__':
    app.run(debug=os.environ.get('FLASK_DEBUG', '').lower() in ('1', 'true', 'yes'))
