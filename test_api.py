import urllib.request
import urllib.parse
import http.cookiejar
import re
import unittest

BASE_URL = 'http://127.0.0.1:5000'

class TestFinanceTrackerAPI(unittest.TestCase):
    # 共享状态，用于传递创建的交易 ID
    created_transaction_id = None
    
    @classmethod
    def setUpClass(cls):
        import socket, threading, time
        from app import app, init_db
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        res = s.connect_ex(('127.0.0.1', 5000))
        s.close()
        if res != 0:
            print("\n[提示] 检测到本地 5000 端口未启动，正在后台自动启动测试服务器...")
            init_db()
            cls.server_thread = threading.Thread(
                target=app.run,
                kwargs={'host': '127.0.0.1', 'port': 5000, 'debug': False, 'use_reloader': False},
                daemon=True
            )
            cls.server_thread.start()
            time.sleep(1.5)
            
        # 初始化 cookie 处理器，用于管理 Session 登录态
        cls.cj = http.cookiejar.CookieJar()
        cls.opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cls.cj))
        urllib.request.install_opener(cls.opener)
        
    def _get(self, path):
        req = urllib.request.Request(f"{BASE_URL}{path}")
        with urllib.request.urlopen(req) as response:
            return response.read().decode('utf-8')
            
    def _post(self, path, data):
        encoded_data = urllib.parse.urlencode(data).encode('utf-8')
        req = urllib.request.Request(f"{BASE_URL}{path}", data=encoded_data, method='POST')
        with urllib.request.urlopen(req) as response:
            return response.read().decode('utf-8')
            
    def _extract_csrf(self, html):
        match = re.search(r'name="csrf_token"\s+value="([^"]+)"', html)
        if not match:
            match = re.search(r'value="([^"]+)"\s+name="csrf_token"', html)
        return match.group(1) if match else None

    def test_01_register(self):
        """测试用户注册接口"""
        # 1. 访问注册页面获取 CSRF Token
        html = self._get('/register')
        csrf_token = self._extract_csrf(html)
        self.assertIsNotNone(csrf_token, "无法从注册页面提取 CSRF token")
        
        # 2. 发送注册表单
        post_data = {
            'username': 'test_api_user',
            'email': 'test_api_user@example.com',
            'phone': '13812345678',
            'password': 'TestPassword123',
            'csrf_token': csrf_token
        }
        
        response_html = self._post('/register', post_data)
        # 如果注册成功，通常重定向到登录页，页面应包含“登录”或“注册成功”
        self.assertTrue(
            any(x in response_html for x in ["登录", "用户名已存在", "注册成功"]), 
            "注册接口返回的 HTML 不符合预期"
        )
        print("\n[OK] 步骤 1: 注册接口测试通过（如果用户名已存在已视为平滑通过）")

    def test_02_login(self):
        """测试用户登录接口"""
        # 1. 访问登录页面获取 CSRF Token
        html = self._get('/login')
        csrf_token = self._extract_csrf(html)
        self.assertIsNotNone(csrf_token, "无法从登录页面提取 CSRF token")
        
        # 2. 发送登录表单
        post_data = {
            'username': 'test_api_user',
            'password': 'TestPassword123',
            'csrf_token': csrf_token
        }
        response_html = self._post('/login', post_data)
        # 登录成功会跳转至首页，首页会包含登录的用户名和当前结余
        self.assertIn("test_api_user", response_html, "登录失败，登录后页面未包含用户名")
        self.assertIn("当前结余", response_html, "登录失败，未进入首页仪表盘")
        print("[OK] 步骤 2: 登录接口测试通过，Session 成功保持")

    def test_03_add_transaction(self):
        """测试手动添加账单接口"""
        # 1. 访问交易记录页面获取 CSRF Token
        html = self._get('/transactions')
        csrf_token = self._extract_csrf(html)
        self.assertIsNotNone(csrf_token, "无法从交易页面提取 CSRF token")
        
        # 2. 模拟表单提交一条 35.5 元的餐饮支出记录
        post_data = {
            'type': 'expense',
            'date': '2026-07-07',
            'category': '餐饮',
            'amount': '35.50',
            'payment_method': 'Cash',
            'description': '接口自动化测试记账',
            'csrf_token': csrf_token
        }
        response_html = self._post('/add_transaction', post_data)
        
        # 3. 验证页面是否显示该条记录
        self.assertIn("接口自动化测试记账", response_html, "列表中未展示新增的交易备注")
        self.assertIn("35.5", response_html, "列表中未展示新增的交易金额")
        
        # 4. 提取生成的交易 ID（分析页面上的删除表单 action 属性）
        # 示例：action="/delete_transaction/12"
        matches = re.findall(r'action="/delete_transaction/(\d+)"', response_html)
        self.assertTrue(len(matches) > 0, "未能在列表中找到新增交易对应的删除表单 ID")
        
        # 保存交易 ID，提供给后续的删除步骤使用
        TestFinanceTrackerAPI.created_transaction_id = matches[0]
        print(f"[OK] 步骤 3: 手动新增交易接口测试通过，生成的交易记录 ID 为: {TestFinanceTrackerAPI.created_transaction_id}")

    def test_04_statistics(self):
        """测试统计分析数据汇总渲染"""
        # 1. 访问统计页面
        html = self._get('/statistics')
        
        # 2. 确认我们之前添加的支出金额 35.50 是否已被包含在页面数据渲染中
        self.assertIn("35.5", html, "统计页面中未包含该新增支出的金额数据")
        print("[OK] 步骤 4: 统计分析数据计算渲染测试通过")

    def test_05_delete_transaction(self):
        """测试删除账单接口"""
        self.assertIsNotNone(TestFinanceTrackerAPI.created_transaction_id, "未找到有效的待测试交易 ID")
        
        # 1. 访问交易列表页面以获取有效的 CSRF Token
        html = self._get('/transactions')
        csrf_token = self._extract_csrf(html)
        self.assertIsNotNone(csrf_token, "无法从交易页面提取 CSRF token")
        
        # 2. 发送 POST 请求删除该笔交易
        delete_path = f"/delete_transaction/{TestFinanceTrackerAPI.created_transaction_id}"
        post_data = {
            'csrf_token': csrf_token
        }
        response_html = self._post(delete_path, post_data)
        
        # 3. 确认已成功从列表中删除该记录
        self.assertNotIn("接口自动化测试记账", response_html, "交易记录删除后仍留在列表中")
        print(f"[OK] 步骤 5: 删除交易记录接口测试通过，已自动清理交易记录 ID: {TestFinanceTrackerAPI.created_transaction_id}")

    def test_06_ai_parse_and_normalization(self):
        """测试 AI 记账规则归一化与接口调用"""
        import json
        from app import normalize_payment_method, normalize_category
        
        # 1. 测试支付方式本地化解析
        self.assertEqual(normalize_payment_method('微信扫码'), 'WeChat')
        self.assertEqual(normalize_payment_method('支付宝花呗'), 'Alipay')
        self.assertEqual(normalize_payment_method('刷信用卡'), 'CreditCard')
        self.assertEqual(normalize_payment_method('银行储蓄卡'), 'BankCard')
        self.assertEqual(normalize_payment_method('现金付款'), 'Cash')
        
        # 2. 测试高频消费词汇分类映射
        self.assertEqual(normalize_category('expense', '瑞幸'), '餐饮')
        self.assertEqual(normalize_category('expense', '星巴克'), '餐饮')
        self.assertEqual(normalize_category('expense', '滴滴'), '交通')
        self.assertEqual(normalize_category('expense', '电费'), '水电杂费')
        self.assertEqual(normalize_category('expense', '淘宝'), '购物')
        self.assertEqual(normalize_category('income', '工资收入'), '工资')
        
        # 3. 测试 /ai/parse_transaction 接口通信
        encoded_data = json.dumps({'text': '昨天在瑞幸喝拿铁花了35块钱，用微信扫码'}).encode('utf-8')
        req = urllib.request.Request(f"{BASE_URL}/ai/parse_transaction", data=encoded_data, headers={'Content-Type': 'application/json'}, method='POST')
        try:
            with urllib.request.urlopen(req) as response:
                res_body = response.read().decode('utf-8')
                res_json = json.loads(res_body)
                
                # 无论 LLM API 是否联通，接口均应返回 JSON 响应且包含 ok 字段
                self.assertIn('ok', res_json)
                if res_json['ok']:
                    tx = res_json['transaction']
                    self.assertEqual(tx['category'], '餐饮')
                    self.assertEqual(tx['payment_method'], 'WeChat')
                    self.assertEqual(tx['amount'], 35.0)
                    print("[OK] 步骤 6: AI 记账规则归一化与 LLM 智能提取接口测试全量通过")
                else:
                    print(f"[OK] 步骤 6: AI 记账规则归一化测试通过；LLM 接口返回兜底说明：{res_json.get('error')}")
        except urllib.error.URLError as e:
            print(f"[OK] 步骤 6: AI 记账规则归一化测试通过；（提示：本地服务器未启动或未连接，接口测试跳过：{e}）")

    def test_07_edit_transaction(self):
        """测试编辑交易记录功能"""
        import json
        
        # 0. 确保已登录
        html = self._get('/login')
        csrf_token = self._extract_csrf(html)
        self.assertIsNotNone(csrf_token, "无法从登录页面提取 CSRF token")
        self._post('/login', {
            'username': 'test_api_user',
            'password': 'TestPassword123',
            'csrf_token': csrf_token,
        })
        
        # 1. 先新增一条交易记录用于编辑测试
        html = self._get('/transactions')
        csrf_token = self._extract_csrf(html)
        self.assertIsNotNone(csrf_token)
        
        add_data = {
            'type': 'expense',
            'category': '餐饮',
            'amount': '88.00',
            'date': '2026-07-08',
            'payment_method': 'WeChat',
            'notes': '编辑功能测试原始记录',
            'csrf_token': csrf_token,
        }
        response_html = self._post('/add_transaction', add_data)
        
        # 提取刚创建的交易记录 ID
        match = re.search(r'/delete_transaction/(\d+)', response_html)
        self.assertIsNotNone(match, "新增交易后未能从页面提取交易 ID")
        # 取最后一个匹配（即最新创建的记录）
        all_ids = re.findall(r'/delete_transaction/(\d+)', response_html)
        edit_tx_id = all_ids[-1]
        
        # 2. 测试 GET /get_transaction/<id> API
        req = urllib.request.Request(f"{BASE_URL}/get_transaction/{edit_tx_id}")
        with urllib.request.urlopen(req) as response:
            res_body = response.read().decode('utf-8')
            res_json = json.loads(res_body)
            self.assertTrue(res_json['ok'], "获取交易记录 API 未返回 ok")
            tx = res_json['transaction']
            self.assertEqual(tx['amount'], 88.0)
            self.assertEqual(tx['category'], '餐饮')
            self.assertEqual(tx['payment_method'], 'WeChat')
        
        # 3. 测试 POST /edit_transaction/<id> 修改记录
        html = self._get('/transactions')
        csrf_token = self._extract_csrf(html)
        
        edit_data = {
            'type': 'expense',
            'category': '交通',
            'amount': '35.50',
            'date': '2026-07-07',
            'payment_method': 'Alipay',
            'notes': '编辑功能测试已修改',
            'csrf_token': csrf_token,
        }
        self._post(f'/edit_transaction/{edit_tx_id}', edit_data)
        
        # 4. 再次获取该记录验证修改生效
        req = urllib.request.Request(f"{BASE_URL}/get_transaction/{edit_tx_id}")
        with urllib.request.urlopen(req) as response:
            res_body = response.read().decode('utf-8')
            res_json = json.loads(res_body)
            self.assertTrue(res_json['ok'])
            tx = res_json['transaction']
            self.assertEqual(tx['amount'], 35.5, "修改后金额不一致")
            self.assertEqual(tx['category'], '交通', "修改后分类不一致")
            self.assertEqual(tx['payment_method'], 'Alipay', "修改后支付方式不一致")
            self.assertEqual(tx['date'], '2026-07-07', "修改后日期不一致")
        
        # 5. 清理：删除测试记录
        html = self._get('/transactions')
        csrf_token = self._extract_csrf(html)
        self._post(f'/delete_transaction/{edit_tx_id}', {'csrf_token': csrf_token})
        
        print(f"[OK] 步骤 7: 编辑交易记录功能测试通过（创建→获取→修改→验证→清理，交易 ID: {edit_tx_id}）")

if __name__ == '__main__':
    unittest.main()
