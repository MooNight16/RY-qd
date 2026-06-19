# -*- coding: utf-8 -*-
"""
不移之火论坛自动签到脚本
修复：
  1. Chrome / chromedriver 版本匹配 → 优先本地驱动，失败回退 Selenium Manager
  2. logger 作用域问题 —— 模块级别初始化
  3. 启动参数优化
"""
import logging
import os
import random
import re
import time
from datetime import datetime

import ddddocr
import requests
import urllib3
from selenium import webdriver
from selenium.common import TimeoutException, NoSuchElementException
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from selenium.webdriver.common.action_chains import ActionChains
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.wait import WebDriverWait

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# ===== 日志配置（模块级别，避免 __main__ 才定义导致的 NameError）=====
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    datefmt='%H:%M:%S'
)
logger = logging.getLogger(__name__)

# ===== 本地 Chrome / chromedriver 路径配置 =====
# 按优先级自动检测 Chrome 可执行文件位置
def _detect_chrome_path():
    candidates = [
        r"C:\Program Files\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
        os.path.expandvars(r"%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe"),
        r"/usr/bin/google-chrome",
        r"/usr/bin/chromium",
    ]
    for p in candidates:
        if os.path.exists(p):
            logger.info(f"[检测] 找到 Chrome: {p}")
            return p
    logger.warning("[检测] 未在常见路径找到 Chrome，使用系统默认")
    return None

# 按优先级检测 chromedriver 路径
def _detect_chromedriver_path():
    candidates = [
        r"E:\QQBot\chromedriver149\chromedriver.exe",
        r"d:\chromedriver\chromedriver.exe",
    ]
    for p in candidates:
        if os.path.exists(p):
            logger.info(f"[检测] 找到 chromedriver: {p}")
            return p
    logger.info("[检测] 未找到本地 chromedriver，将由 Selenium Manager 自动下载")
    return None

LOCAL_CHROME_PATH = _detect_chrome_path()
LOCAL_CHROMEDRIVER_PATH = _detect_chromedriver_path()

# ===== 站点 / 账号配置 =====
LOGIN_URL = "https://www.byzhihuo.com/member.php?mod=logging&action=login&referer="
SIGN_URL = "https://www.byzhihuo.com/plugin.php?id=k_misign:sign"
CREDIT_URL = "https://www.byzhihuo.com/home.php?mod=spacecp&ac=credit&showcredit=1"

# ===== 多用户账号列表 =====
# 方式 1：直接写死（本地调试用，不要提交到 GitHub）
ACCOUNTS_LOCAL = [
    ("moonight16", "1234qwer"),
    ("zzxmoon", "1234qwer"),
    ("zzxmoon1", "1234qwer"),
    ("zzxmoon2", "1234qwer"),
    ("zzxmoon3", "1234qwer"),
]

# 方式 2：从环境变量 BYZH_ACCOUNTS 读取（推荐，用于 GitHub Actions）
# 格式：账号1:密码1;账号2:密码2;账号3:密码3
def _load_accounts():
    env_accounts = os.environ.get("BYZH_ACCOUNTS", "").strip()
    if env_accounts:
        pairs = [p.strip() for p in env_accounts.split(";") if p.strip()]
        accounts = []
        for p in pairs:
            if ":" in p:
                u, pw = p.split(":", 1)
                accounts.append((u.strip(), pw.strip()))
        if accounts:
            logger.info(f"[配置] 从 BYZH_ACCOUNTS 读取到 {len(accounts)} 个账号")
            return accounts
    logger.info(f"[配置] 使用本地 ACCOUNTS，共 {len(ACCOUNTS_LOCAL)} 个账号")
    return ACCOUNTS_LOCAL

ACCOUNTS = _load_accounts()

TEMP_DIR = "temp"
MAX_CAPTCHA_RETRY = 3
CAPTCHA_TIMEOUT = 60

# PushPlus 推送 token（从环境变量读取，GitHub Actions 中配置在 Secrets）
PUSHPLUS_TOKEN = os.environ.get("PUSHPLUS_TOKEN", "").strip()
PUSHPLUS_URL = "https://www.pushplus.plus/send"

# 判断是否在 CI / GitHub Actions 环境运行
IS_CI = bool(os.environ.get("GITHUB_ACTIONS") or os.environ.get("CI"))


# ============================================================
# ----------------------------- 工具函数 ----------------------------
# ============================================================

def get_url_from_style(style):
    m = re.search(r'url\(["\']?(.*?)["\']?\)', style or "")
    return m.group(1) if m else None


def clear_temp():
    if os.path.exists(TEMP_DIR):
        for filename in os.listdir(TEMP_DIR):
            file_path = os.path.join(TEMP_DIR, filename)
            try:
                if os.path.isfile(file_path) or os.path.islink(file_path):
                    os.remove(file_path)
            except Exception:
                pass
    else:
        os.makedirs(TEMP_DIR, exist_ok=True)


def cleanup_temp_final():
    removed = 0
    if os.path.exists(TEMP_DIR):
        for filename in os.listdir(TEMP_DIR):
            file_path = os.path.join(TEMP_DIR, filename)
            try:
                if os.path.isfile(file_path) or os.path.islink(file_path):
                    os.remove(file_path)
                    removed += 1
            except Exception:
                pass
    logger.info(f"[清理] 已删除 temp 目录下 {removed} 个文件")
    return removed


# ============================================================
# ----------------------------- Selenium 初始化 ----------------------------
# ============================================================

def init_selenium(debug=False, headless=False):
    """初始化 Selenium。支持 Windows 本地环境 和 Linux CI 环境。"""
    import platform
    system = platform.system()
    is_linux = (system == "Linux")

    ops = webdriver.ChromeOptions()

    # 根据环境选择参数
    if is_linux:
        # Linux / CI 环境必需参数
        ops.add_argument('--headless=new')
        ops.add_argument('--no-sandbox')
        ops.add_argument('--disable-dev-shm-usage')
        ops.add_argument('--disable-gpu')
        ops.add_argument('--window-size=1920,1080')
        ops.add_argument('--disable-features=VizDisplayCompositor')
        logger.info(f"[初始化] Linux 环境 ({system})，使用 headless 模式")
    else:
        # Windows 本地环境
        ops.add_argument('--window-size=1440,900')
        ops.add_argument('--no-sandbox')
        ops.add_argument('--disable-dev-shm-usage')
        if headless:
            ops.add_argument('--headless=new')
        logger.info(f"[初始化] Windows 环境 ({system})")

    # 通用反检测参数
    ops.add_argument('--disable-blink-features=AutomationControlled')
    ops.add_experimental_option('excludeSwitches', ['enable-automation'])
    ops.add_experimental_option('useAutomationExtension', False)
    ops.add_experimental_option('prefs', {
        'profile.default_content_setting_values.notifications': 2,
        'credentials_enable_service': False,
        'profile.password_manager_enabled': False,
    })

    if debug and not is_linux:
        ops.add_experimental_option("detach", True)

    # Windows 使用本地 chromedriver；Linux 由 Selenium Manager 自动管理
    if not is_linux and os.path.exists(r"d:\chromedriver\chromedriver.exe"):
        chrome_driver_path = r"d:\chromedriver\chromedriver.exe"
    else:
        chrome_driver_path = None

    last_err = None
    for attempt in range(3):
        try:
            if chrome_driver_path:
                service = Service(chrome_driver_path)
                driver = webdriver.Chrome(service=service, options=ops)
            else:
                driver = webdriver.Chrome(options=ops)
            # 先访问一个页面再执行 CDP，避免 Chrome 未完全就绪
            driver.get("about:blank")
            driver.execute_cdp_cmd(
                "Page.addScriptToEvaluateOnNewDocument",
                {"source": "Object.defineProperty(navigator,'webdriver',{get:()=>undefined});"}
            )
            logger.info(f"[初始化] Chrome 启动成功")
            return driver
        except Exception as e:
            last_err = e
            logger.info(f"[初始化] 启动失败(第{attempt + 1}次): {str(e)[:120]}")
            time.sleep(2 + attempt)

    raise RuntimeError(f"无法启动 Chrome: {last_err}")


# ============================================================
# ----------------------------- PushPlus 推送 ----------------------------
# ============================================================

def pushplus_send(title, content, template="txt"):
    """
    发送 PushPlus 推送。
    - title: 推送标题
    - content: 推送正文（纯文本、HTML、JSON 等）
    - template: 'txt' 纯文本 / 'html' HTML / 'markdown' Markdown / 'json' JSON
    返回 (True, msg) 或 (False, 错误信息)
    """
    if not PUSHPLUS_TOKEN:
        return False, "未配置 PUSHPLUS_TOKEN，跳过推送"

    payload = {
        "token": PUSHPLUS_TOKEN,
        "title": title,
        "content": content,
        "template": template,
    }

    try:
        resp = requests.post(PUSHPLUS_URL, json=payload, timeout=15)
        data = resp.json()
        if data.get("code") == 200:
            logger.info(f"[推送] ✓ PushPlus 发送成功")
            return True, "推送成功"
        else:
            logger.info(f"[推送] ✗ PushPlus 返回错误: {data}")
            return False, f"PushPlus 错误: {data}"
    except Exception as e:
        logger.info(f"[推送] ✗ 发送异常: {e}")
        return False, f"推送异常: {e}"


def build_pushplus_report(all_results, start_time, end_time):
    """构建 pushplus 友好的 HTML 报告（带颜色表格）"""
    total = len(all_results)
    success_count = sum(1 for r in all_results if r["success"])
    fail_count = total - success_count
    total_coins = sum(r["coins"] for r in all_results)
    elapsed = (end_time - start_time).total_seconds()
    rate = (success_count / total * 100) if total > 0 else 0

    # 标题
    title = f"🔥 不移之火签到 | {success_count}/{total} 成功"

    # HTML 正文
    html_lines = []
    html_lines.append(f'<div style="font-family: -apple-system, Helvetica, Arial, sans-serif; max-width: 600px; margin: 0 auto; padding: 10px;">')
    html_lines.append(f'<div style="background: linear-gradient(135deg, #667eea 0%, #764ba2 100%); color: #fff; padding: 16px 20px; border-radius: 10px 10px 0 0; font-size: 18px; font-weight: bold; text-align: center;">')
    html_lines.append(f'  🔥 不移之火论坛自动签到')
    html_lines.append(f'</div>')

    # 概览
    html_lines.append(f'<div style="background: #fff; border: 1px solid #e8e8e8; border-radius: 0 0 10px 10px; padding: 16px 20px; font-size: 14px;">')
    html_lines.append(f'  <p style="margin: 6px 0; color: #666;">📅 {end_time.strftime("%Y-%m-%d %H:%M:%S")} &nbsp; ⏱ {int(elapsed)}s &nbsp; 👤 {total} 个账户</p>')
    html_lines.append(f'  <div style="display: flex; justify-content: space-around; text-align: center; margin: 12px 0; padding: 12px 0; background: #f9f9f9; border-radius: 8px;">')
    html_lines.append(f'    <div><div style="color: #52c41a; font-size: 22px; font-weight: bold;">{success_count}</div><div style="color: #999; font-size: 12px;">✓ 成功</div></div>')
    html_lines.append(f'    <div><div style="color: #ff4d4f; font-size: 22px; font-weight: bold;">{fail_count}</div><div style="color: #999; font-size: 12px;">✗ 失败</div></div>')
    html_lines.append(f'    <div><div style="color: #fa8c16; font-size: 22px; font-weight: bold;">+{total_coins}</div><div style="color: #999; font-size: 12px;">🪙 金币</div></div>')
    html_lines.append(f'    <div><div style="color: #1890ff; font-size: 22px; font-weight: bold;">{rate:.0f}%</div><div style="color: #999; font-size: 12px;">成功率</div></div>')
    html_lines.append(f'  </div>')
    html_lines.append(f'</div>')

    # 详细表格
    html_lines.append(f'<div style="margin-top: 14px; background: #fff; border: 1px solid #e8e8e8; border-radius: 10px; overflow: hidden;">')
    html_lines.append(f'  <div style="background: #fafafa; padding: 10px 16px; font-weight: bold; color: #333; font-size: 14px; border-bottom: 1px solid #e8e8e8;">📋 详细结果</div>')
    html_lines.append(f'  <table style="width: 100%; border-collapse: collapse; font-size: 13px;">')
    html_lines.append(f'    <tr style="background: #f5f5f5; color: #666;"><th style="padding: 8px 10px; text-align: left; border-bottom: 1px solid #eee;">#</th><th style="padding: 8px 10px; text-align: left; border-bottom: 1px solid #eee;">账户</th><th style="padding: 8px 10px; text-align: center; border-bottom: 1px solid #eee;">状态</th><th style="padding: 8px 10px; text-align: center; border-bottom: 1px solid #eee;">金币</th><th style="padding: 8px 10px; text-align: left; border-bottom: 1px solid #eee;">消息</th></tr>')

    for i, r in enumerate(all_results, 1):
        status_color = "#52c41a" if r["success"] else "#ff4d4f"
        status_text = "✓ 成功" if r["success"] else "✗ 失败"
        coins_s = f"+{r['coins']}" if r["coins"] > 0 else "—"
        msg = (r["message"] or "").replace("<", "&lt;").replace(">", "&gt;")[:80]
        row_bg = "#fafffb" if r["success"] else "#fff1f0"
        html_lines.append(f'    <tr style="background: {row_bg};"><td style="padding: 8px 10px; border-bottom: 1px solid #f0f0f0; color: #999;">{i}</td><td style="padding: 8px 10px; border-bottom: 1px solid #f0f0f0; color: #333;">{r["username"]}</td><td style="padding: 8px 10px; border-bottom: 1px solid #f0f0f0; color: {status_color}; text-align: center; font-weight: bold;">{status_text}</td><td style="padding: 8px 10px; border-bottom: 1px solid #f0f0f0; color: #fa8c16; text-align: center; font-weight: bold;">{coins_s}</td><td style="padding: 8px 10px; border-bottom: 1px solid #f0f0f0; color: #666;">{msg}</td></tr>')

    html_lines.append(f'  </table>')
    html_lines.append(f'</div>')

    # 失败详情
    if fail_count > 0:
        html_lines.append(f'<div style="margin-top: 14px; background: #fff1f0; border: 1px solid #ffccc7; border-radius: 10px; padding: 14px 18px;">')
        html_lines.append(f'  <div style="font-weight: bold; color: #ff4d4f; margin-bottom: 8px; font-size: 14px;">⚠️ 失败账户详情</div>')
        for r in all_results:
            if not r["success"]:
                msg = (r["message"] or "").replace("<", "&lt;").replace(">", "&gt;")
                html_lines.append(f'  <div style="margin: 4px 0; font-size: 13px; color: #555;">• <span style="color: #333; font-weight: bold;">{r["username"]}</span> → {msg}</div>')
        html_lines.append(f'</div>')

    html_lines.append(f'<div style="margin-top: 16px; text-align: center; color: #bbb; font-size: 12px;">— byzhsign bot —</div>')
    html_lines.append(f'</div>')

    return title, "\n".join(html_lines)


# ============================================================
# ----------------------------- 验证码图片下载 ----------------------------
# ============================================================

def download_image(url, filename):
    os.makedirs(TEMP_DIR, exist_ok=True)
    target = os.path.join(TEMP_DIR, filename)
    try:
        headers = {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/149.0.0.0 Safari/537.36"
            ),
            "Referer": "https://t.captcha.qq.com/",
        }
        response = requests.get(
            url, timeout=15, headers=headers,
            proxies={"http": None, "https": None}, verify=False
        )
        if response.status_code == 200 and response.content:
            with open(target, "wb") as f:
                f.write(response.content)
            return True
        return False
    except Exception:
        return False


def download_captcha_img(driver):
    """下载背景图和滑块图，返回 (ok, scale_ratio)。"""
    clear_temp()
    try:
        slide_bg = WebDriverWait(driver, 10).until(
            EC.visibility_of_element_located((By.ID, "slideBg"))
        )
    except TimeoutException:
        return False, 0.5

    bg_url = slide_bg.get_attribute("src")
    if not bg_url or not download_image(bg_url, "bg.jpg"):
        return False, 0.5

    try:
        slide_block = driver.find_element(By.ID, "slideBlock")
        sprite_url = slide_block.get_attribute("src")
        if sprite_url:
            download_image(sprite_url, "sprite.png")
    except NoSuchElementException:
        pass

    # 动态计算缩放比例（原始图片尺寸 vs 页面实际展示尺寸）
    try:
        info = driver.execute_script("""
            var bg = document.getElementById('slideBg');
            if (!bg) return {nw: 680, dw: 341};
            return {nw: bg.naturalWidth || 680, dw: bg.offsetWidth || bg.clientWidth || 341};
        """)
        natural_w = info.get('nw', 680) or 680
        display_w = info.get('dw', 341) or 341
        scale_ratio = display_w / natural_w
    except Exception:
        scale_ratio = 0.5

    logger.info(f"[验证码] 背景图 {natural_w}→{display_w}px, scale={scale_ratio:.4f}")
    return True, scale_ratio


# ============================================================
# ----------------------------- 滑块距离计算 ----------------------------
# ============================================================

def calculate_slide_distance(scale_ratio, driver):
    """计算滑块拖动距离：ddddocr 识别缺口 + JS getBoundingClientRect 精确计算。"""
    ocr = ddddocr.DdddOcr(det=False, ocr=False, show_ad=False)

    sprite_path = os.path.join(TEMP_DIR, "sprite.png")
    bg_path = os.path.join(TEMP_DIR, "bg.jpg")

    try:
        with open(sprite_path, 'rb') as f:
            sprite_bytes = f.read()
        with open(bg_path, "rb") as f:
            bg_bytes = f.read()
    except Exception as e:
        logger.info(f"[识别] 读取图片失败: {e}")
        return 180

    result = ocr.slide_match(sprite_bytes, bg_bytes, simple_target=False)
    vals = [int(x) for x in result["target"]]

    if len(vals) >= 4:
        gap_center_raw = (vals[0] + vals[2]) / 2
    elif len(vals) >= 2:
        gap_center_raw = vals[0]
    else:
        gap_center_raw = vals[0] if vals else 200

    # 使用 getBoundingClientRect 精确计算：
    # 滑块初始中心位置 → 背景图上缺口位置（换算到页面坐标）
    try:
        info = driver.execute_script("""
            var bg = document.getElementById('slideBg');
            var slider = document.getElementById('slideBlock');
            var track = slider ? slider.parentElement : null;
            if (!bg || !slider) return { ok: false };
            var bg_rect = bg.getBoundingClientRect();
            var slider_rect = slider.getBoundingClientRect();
            var track_rect = track ? track.getBoundingClientRect() : slider_rect;
            return {
                ok: true,
                bg_left: bg_rect.left,
                bg_width: bg_rect.width,
                bg_nw: bg.naturalWidth || 680,
                slider_left: slider_rect.left,
                slider_width: slider_rect.width,
                track_left: track_rect.left,
            };
        """)
        if not info or not info.get('ok', False):
            distance = int(gap_center_raw * scale_ratio) - 26
            logger.info(f"[识别] JS测量失败, 估算距离={distance}px")
            return max(30, min(distance, 330))

        bg_left_page = float(info.get('bg_left', 0))
        slider_left_page = float(info.get('slider_left', 0))
        slider_width_page = float(info.get('slider_width', 55))

        # 缺口中心在页面上的坐标 = 背景图左侧 + 缺口在原图位置 * 缩放比例
        gap_center_page = bg_left_page + gap_center_raw * scale_ratio
        # 滑块中心初始位置
        slider_center_page = slider_left_page + slider_width_page / 2
        distance = int(gap_center_page - slider_center_page)
        distance = max(30, min(distance, 330))

        logger.info(f"[识别] ddddocr={vals}, scale={scale_ratio:.4f}, 距离={distance}px")
        return distance
    except Exception as e:
        logger.info(f"[识别] JS计算异常: {e}")
        distance = int(gap_center_raw * scale_ratio) - 26
        return max(30, min(distance, 330))


# ============================================================
# ----------------------------- 滑块拖动 ----------------------------
# ============================================================

def drag_slider(driver, distance):
    """
    模拟人手拖动滑块：快速加速 → 匀速 → 慢减速微调
    用分步 ActionChains 实现更自然的轨迹
    """
    try:
        slider = WebDriverWait(driver, 10).until(
            EC.visibility_of_element_located((By.ID, "slideBlock"))
        )
    except TimeoutException:
        logger.info("[拖动] 未找到 slideBlock 元素")
        return

    start_time = time.time()
    logger.info(f"[拖动] distance={distance}px, 开始拖动")

    try:
        # 构造分步移动序列：加速 20% → 匀速 50% → 减速 25% → 微调 5%
        # 每步都带上随机 Y 轴偏移和时间停顿，模拟人手抖动
        steps = [
            # 加速段
            (int(distance * 0.10), random.randint(-2, 2), 0.05),
            (int(distance * 0.10), random.randint(-2, 2), 0.04),
            # 匀速段
            (int(distance * 0.15), random.randint(-2, 2), 0.03),
            (int(distance * 0.15), random.randint(-2, 2), 0.03),
            (int(distance * 0.15), random.randint(-2, 2), 0.03),
            (int(distance * 0.10), random.randint(-2, 2), 0.04),
            # 减速段
            (int(distance * 0.08), random.randint(-1, 1), 0.05),
            (int(distance * 0.05), random.randint(-1, 1), 0.06),
            # 微调
            (max(3, distance - int(distance * 0.88)), random.randint(-1, 1), 0.10),
        ]

        # 用一个连续的 ActionChains 模拟平滑拖动
        actions = ActionChains(driver, duration=100)
        actions.click_and_hold(slider)
        actions.pause(0.15)  # 按住后短暂停顿
        for dx, dy, sleep in steps:
            if dx != 0:
                actions.move_by_offset(dx, dy)
            actions.pause(sleep)
        actions.release()
        actions.perform()

        elapsed = time.time() - start_time
        logger.info(f"[拖动] 完成, {len(steps)}步, 耗时 {elapsed:.2f}秒")
    except Exception as e:
        logger.info(f"[拖动] 失败: {e}")

    time.sleep(3)


# ============================================================
# ----------------------------- 验证码解决流程 ----------------------------
# ============================================================

def solve_captcha(driver):
    """解决滑块验证码：下载图片→计算距离→拖动，最多重试 3 次。"""
    for attempt in range(MAX_CAPTCHA_RETRY):
        logger.info(f"[验证码] ▶ 第 {attempt + 1}/{MAX_CAPTCHA_RETRY} 次")

        try:
            WebDriverWait(driver, 15).until(
                EC.visibility_of_element_located((By.ID, "slideBg"))
            )
            time.sleep(1.0)
        except TimeoutException:
            return True, "无需验证码"

        ok, scale_ratio = download_captcha_img(driver)
        if not ok:
            time.sleep(2)
            continue

        distance = calculate_slide_distance(scale_ratio, driver)
        logger.info(f"[验证码] 尝试距离: {distance}px")

        drag_slider(driver, distance)
        time.sleep(3)

        # slideBg 消失 = 验证通过；仍然存在 = 失败，进入下一轮
        try:
            driver.find_element(By.ID, "slideBg")
            logger.info("[验证码] 未通过，进入下一次尝试")
            time.sleep(2)
            continue
        except NoSuchElementException:
            logger.info(f"[验证码] ✓ 通过！距离: {distance}px")
            return True, f"验证码通过 (距离:{distance}px)"

    return False, f"验证码重试{MAX_CAPTCHA_RETRY}次全部失败"


# ============================================================
# ----------------------------- 业务流程 - 登录 ----------------------------
# ============================================================

def do_login(driver, wait, user, pwd):
    logger.info(f"[登录] 打开登录页: {LOGIN_URL}")
    driver.get(LOGIN_URL)

    try:
        username_input = wait.until(EC.visibility_of_element_located((By.NAME, "username")))
        password_input = wait.until(EC.visibility_of_element_located((By.NAME, "password")))
    except TimeoutException:
        return False, "未找到登录输入框"

    username_input.clear()
    username_input.send_keys(user)
    time.sleep(0.5)
    password_input.clear()
    password_input.send_keys(pwd)
    time.sleep(0.5)

    try:
        login_btn = wait.until(EC.element_to_be_clickable((By.CSS_SELECTOR, 'button[name="loginsubmit"]')))
    except TimeoutException:
        return False, "未找到登录按钮"

    logger.info("[登录] 点击登录按钮")
    driver.execute_script("arguments[0].click();", login_btn)
    time.sleep(10)  # 等待登录状态生效
    driver.refresh()  # 刷新页面
    time.sleep(3)
    driver.switch_to.default_content()

    page_text = driver.page_source
    if "欢迎您回来" in page_text or "现在将转入登录前页面" in page_text:
        return True, "登录成功"

    try:
        driver.find_element(By.NAME, "username")
        return False, "账号或密码错误"
    except NoSuchElementException:
        return True, "登录成功"


# ============================================================
# ----------------------------- 业务流程 - 签到状态 / 积分 ----------------------------
# ============================================================

def check_page_sign_status(driver):
    """
    检测签到页是否已签到（轻量级检测，仅供轮询时快速判断）。
    返回 'signed' 或 'unknown'
    真正的金币数据由 CREDIT_URL 表格决定。
    """
    # 检查 JD_sign 按钮是否隐藏/不存在 → 可能已签到
    try:
        btn = driver.find_element(By.ID, "JD_sign")
        btn_display = driver.execute_script(
            "return window.getComputedStyle(arguments[0]).display !== 'none' "
            "&& arguments[0].offsetWidth > 0 && arguments[0].offsetHeight > 0;",
            btn
        )
        if not btn_display:
            logger.info("[签到状态] JD_sign 按钮隐藏，疑似已签到")
            return "signed"
    except NoSuchElementException:
        logger.info("[签到状态] JD_sign 按钮不存在，疑似已签到")
        return "signed"
    except Exception:
        pass

    # 检查 .qdleft .font 文本（仅作参考）
    try:
        font_el = driver.find_element(By.CSS_SELECTOR, ".qdleft .font")
        font_text = font_el.text.strip()
        logger.info(f"[签到状态] .font 文本: '{font_text}'")
    except Exception:
        pass

    return "unknown"


def get_credit_record(driver, wait):
    """
    解析 CREDIT_URL 的表格，查找今天的"签到"记录。
    表格结构：
      <tr>
        <td>签到</td>
        <td>金币 <span class="xi1">+1</span></td>
        <td>2026-06-19 19:57</td>
        <td></td>
      </tr>
    返回 (coins, record_time)，coins > 0 表示签到成功。
    """
    logger.info(f"[积分] 访问积分记录页: {CREDIT_URL}")
    driver.get(CREDIT_URL)
    time.sleep(3)

    today_str = datetime.now().strftime("%Y-%m-%d")
    coins = 0
    record_time = ""
    logger.info(f"[积分] 今天日期: {today_str}")

    # ── 方式1：DOM 级精确解析（最可靠）──
    try:
        rows = driver.find_elements(By.TAG_NAME, "tr")
        logger.info(f"[积分] 找到 {len(rows)} 个 <tr> 行")

        for row in rows:
            try:
                tds = row.find_elements(By.TAG_NAME, "td")
                if len(tds) < 3:
                    continue

                td_texts = [td.text.strip() for td in tds]
                row_text = " | ".join(td_texts)

                # 第一格必须是"签到"
                if "签到" not in td_texts[0]:
                    continue

                # 检查其他 td 是否含今天日期
                found_today = False
                for txt in td_texts:
                    if today_str in txt:
                        found_today = True
                        break

                if not found_today:
                    continue

                logger.info(f"[积分] 找到签到记录行: {row_text}")

                # 从所有 td 中查找金币数值
                # 优先从 <span class="xi1"> 提取
                for td in tds:
                    try:
                        spans = td.find_elements(By.CSS_SELECTOR, "span.xi1")
                        for span in spans:
                            span_text = span.text.strip()
                            if "+" in span_text or "-" in span_text:
                                match = re.search(r'([+\-]\d+)', span_text)
                                if match:
                                    coins = int(match.group(1))
                    except Exception:
                        pass

                # 如果 span 没找到，再从文本中匹配 "金币 +N"
                if coins == 0:
                    for txt in td_texts:
                        if "金币" in txt:
                            match = re.search(r'([+\-]\d+)', txt)
                            if match:
                                coins = int(match.group(1))
                                break

                # 提取时间
                for txt in td_texts:
                    time_match = re.search(r'(\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2})', txt)
                    if time_match:
                        record_time = time_match.group(1)
                        break

                break
            except Exception as row_err:
                logger.info(f"[积分] 解析行异常: {row_err}")
                continue

        if coins != 0:
            logger.info(f"[积分] DOM 解析结果: 金币 {coins:+d}, 时间 {record_time or '-'}")
            return coins, record_time
        else:
            logger.info("[积分] DOM 解析未找到今天的金币记录")
    except Exception as e:
        logger.info(f"[积分] DOM 解析失败: {e}")

    # ── 方式2：JS 全文本解析（兜底）──
    try:
        page_text = driver.execute_script(
            "return document.body.innerText || document.body.textContent || '';"
        ) or ""

        for line in page_text.splitlines():
            line = line.strip()
            if "签到" in line and today_str in line:
                logger.info(f"[积分] 文本文本行匹配: {line}")
                coin_match = re.search(r'([+\-]\d+)\s*金币?|金币?\s*([+\-]\d+)', line)
                if coin_match:
                    coins = int(coin_match.group(1) or coin_match.group(2))
                time_match = re.search(r'(\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2})', line)
                if time_match:
                    record_time = time_match.group(1)
                break
    except Exception as e2:
        logger.info(f"[积分] JS 文本解析失败: {e2}")

    logger.info(f"[积分] 最终结果: 金币 {coins:+d}, 时间 {record_time or '-'}")
    return coins, record_time


# ============================================================
# ----------------------------- 业务流程 - 签到主流程 ----------------------------
# ============================================================

def do_sign(driver, wait):
    """
    正确签到流程（带重试机制）：
    1. 进入 SIGN_URL → 点击 JD_sign
    2. 出现滑块验证码 → 切换 iframe → 拖动滑块
    3. 进入 CREDIT_URL → 查找含"签到"+今天日期的 <tr> 行
    4. 从 <span class="xi1">+1</span> 提取金币
    """
    max_rounds = 1
    for round_i in range(max_rounds):
        logger.info(f"[签到] 打开签到页: {SIGN_URL}")
        driver.get(SIGN_URL)
        time.sleep(3)

        # ── 点击 JD_sign 签到按钮 ──
        try:
            sign_btn = WebDriverWait(driver, 20).until(
                EC.element_to_be_clickable((By.ID, "JD_sign"))
            )
            logger.info("[签到] ✓ 找到 JD_sign 按钮，准备点击")
            ActionChains(driver).move_to_element(sign_btn).pause(0.5).click().perform()
            logger.info("[签到] ✓ 已点击 JD_sign 按钮")
        except TimeoutException:
            logger.info("[签到] ✗ 20秒内未找到 JD_sign 按钮")
            # 兜底：直接去 CREDIT_URL 查有没有今天记录
            coins, rtime = get_credit_record(driver, wait)
            if coins > 0:
                return True, "今日已签到", coins, rtime
            return False, "未找到 JD_sign 签到按钮", 0, ""
        except Exception as e:
            logger.info(f"[签到] 点击 JD_sign 异常: {e}，尝试 JS 方式")
            try:
                driver.execute_script("document.getElementById('JD_sign').click();")
            except Exception as e2:
                logger.info(f"[签到] JS 点击也失败: {e2}")

        # ── 等待并检测滑块验证码 iframe ──
        time.sleep(4)

        captcha_iframe = None
        captcha_detected = False

        # 方式 A：优先找 tcaptcha_iframe
        try:
            iframe_el = WebDriverWait(driver, 12).until(
                EC.presence_of_element_located((By.ID, "tcaptcha_iframe"))
            )
            iframe_display = driver.execute_script(
                "return window.getComputedStyle(arguments[0]).display !== 'none' "
                "&& arguments[0].offsetWidth > 0 && arguments[0].offsetHeight > 0;",
                iframe_el
            )
            if iframe_display:
                captcha_iframe = iframe_el
                captcha_detected = True
                logger.info("[签到] ✓ 检测到 tcaptcha_iframe 滑块验证码")
        except TimeoutException:
            logger.info("[签到] 未找到 tcaptcha_iframe")

        # 方式 B：遍历 src 含关键词的 iframe
        if not captcha_detected:
            try:
                for iframe in driver.find_elements(By.TAG_NAME, "iframe"):
                    src = iframe.get_attribute("src") or ""
                    if "captcha" in src.lower() or "slide" in src.lower() or "tencent" in src.lower():
                        captcha_iframe = iframe
                        captcha_detected = True
                        logger.info(f"[签到] ✓ 检测到验证码 iframe (src={src[:60]})")
                        break
            except Exception:
                pass

        # ── 有验证码则切换 iframe 并拖动 ──
        if captcha_detected:
            try:
                driver.switch_to.frame(captcha_iframe)
                logger.info("[签到] ✓ 已切换到验证码 iframe")
            except Exception as e:
                logger.info(f"[签到] 切换 iframe 异常: {e}")

            time.sleep(2)
            captcha_ok, captcha_msg = solve_captcha(driver)

            try:
                driver.switch_to.default_content()
            except Exception:
                pass

            if not captcha_ok:
                logger.info(f"[签到] ✗ 验证码失败: {captcha_msg}")
                # 仍然去 CREDIT_URL 确认
                coins, rtime = get_credit_record(driver, wait)
                if coins > 0:
                    return True, "签到成功", coins, rtime
                return False, f"验证码失败: {captcha_msg}", 0, ""

            logger.info("[签到] ✓ 验证码通过，等待签到结果写入...")
            time.sleep(3)

        else:
            logger.info("[签到] 未检测到滑块验证码，等待几秒后去积分页确认")
            time.sleep(3)

        # ── 进入 CREDIT_URL 查询今日金币 ──
        coins, rtime = get_credit_record(driver, wait)
        if coins > 0:
            return True, "签到成功", coins, rtime

        # 最后确认：再查一次 CREDIT_URL
        logger.info("[签到] 最后确认：再次查询 CREDIT_URL")
        time.sleep(2)
        coins2, rtime2 = get_credit_record(driver, wait)
        if coins2 > 0:
            return True, "签到成功", coins2, rtime2

    return False, "签到流程结束但未获得金币", 0, ""


# ============================================================
# ----------------------------- 单个账户处理 ----------------------------
# ============================================================

def sign_in_account(driver, user, pwd):
    result = {
        "success": False, "username": user, "message": "",
        "coins": 0, "record_time": "", "login_time": "", "error": ""
    }
    try:
        logger.info(f"[账户] 开始处理: {user}")
        driver.delete_all_cookies()
        try:
            driver.execute_script("window.localStorage.clear(); window.sessionStorage.clear();")
        except Exception:
            pass

        wait = WebDriverWait(driver, 30)
        login_ok, login_msg = do_login(driver, wait, user, pwd)
        if not login_ok:
            result["message"] = login_msg
            result["error"] = login_msg
            return result
        result["login_time"] = datetime.now().strftime("%H:%M:%S")

        success, msg, coins, rtime = do_sign(driver, wait)
        result["success"] = success
        result["message"] = msg
        result["coins"] = coins
        result["record_time"] = rtime
        return result
    except Exception as e:
        logger.info(f"[账户] 异常: {e}")
        result["error"] = str(e)
        result["message"] = f"系统异常: {e}"
        return result


# ============================================================
# ----------------------------- 多用户主流程 ----------------------------
# ============================================================

def run_multi_accounts(debug=False, headless=False):
    if not ACCOUNTS:
        logger.info("未配置任何账户！")
        return

    total = len(ACCOUNTS)
    all_results = []
    start_time_global = datetime.now()

    logger.info("-" * 60)
    logger.info(f"不移之火论坛自动签到 | 共 {total} 个账户")
    logger.info(f"开始时间: {start_time_global.strftime('%Y-%m-%d %H:%M:%S')}")
    logger.info("-" * 60)

    driver = None
    try:
        driver = init_selenium(debug=debug, headless=headless)

        for idx, (username, password) in enumerate(ACCOUNTS, 1):
            logger.info("")
            logger.info(f"[{idx}/{total}] ===== 账户: {username} =====")
            result = sign_in_account(driver, username, password)
            all_results.append(result)

            status = "✓ 成功" if result["success"] else "✗ 失败"
            logger.info(f"[{idx}/{total}] {username} | {status} | {result['message']}")
            if result["coins"] > 0:
                logger.info(f"[{idx}/{total}] {username} | 金币: +{result['coins']}")

            if idx < total:
                logger.info("等待 15 秒后处理下一个账户...")
                time.sleep(15)
    except Exception as e:
        logger.info(f"[主流程] 致命错误: {e}")
    finally:
        if driver:
            try:
                driver.quit()
            except Exception:
                pass

    end_time_global = datetime.now()
    elapsed = (end_time_global - start_time_global).total_seconds()
    success_count = sum(1 for r in all_results if r["success"])
    fail_count = total - success_count
    total_coins = sum(r["coins"] for r in all_results)
    rate = (success_count / total * 100) if total > 0 else 0

    # ======= 简洁汇总报告（PushPlus 友好，无 ANSI 颜色）=======
    print()
    print("=" * 58)
    print(f"  🔥 不移之火论坛自动签到报告")
    print("-" * 58)
    print(f"  📅 时间: {end_time_global.strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"  ⏱ 耗时: {elapsed:.0f} 秒")
    print(f"  👤 账户: {total} 个")
    print(f"  ✓ 成功: {success_count}   ✗ 失败: {fail_count}   🪙 金币: +{total_coins}")
    print(f"  📊 成功率: {rate:.0f}%  ({success_count}/{total})")
    print("-" * 58)

    # 详细结果
    print(f"  {'#':<4}{'账户':<16}{'状态':<10}{'金币':<8}消息")
    print("  " + "-" * 54)
    for i, r in enumerate(all_results, 1):
        status_s = "✓ 成功" if r["success"] else "✗ 失败"
        coins_s = f"+{r['coins']}" if r["coins"] > 0 else "—"
        rtime = r.get("record_time") or r.get("login_time") or ""
        msg = (r["message"] or "")[:50]
        print(f"  {i:<4}{r['username']:<16}{status_s:<10}{coins_s:<8} {msg}")

    # 失败详情
    if fail_count > 0:
        print("-" * 58)
        print(f"  ⚠️ 失败账户详情:")
        for r in all_results:
            if not r["success"]:
                print(f"     • {r['username']}: {r['message']}")

    print("=" * 58)
    print()

    cleanup_temp_final()
    logger.info(f"签到完成 | 成功: {success_count}/{total}, 失败: {fail_count}/{total}, 金币: +{total_coins}")

    # ======= PushPlus 推送 =======
    if PUSHPLUS_TOKEN:
        title, html_content = build_pushplus_report(all_results, start_time_global, end_time_global)
        pushplus_send(title, html_content, template="html")
    else:
        logger.info("[推送] 未配置 PUSHPLUS_TOKEN，跳过推送")


if __name__ == "__main__":
    # CI 环境自动 headless；本地可手动修改
    headless = IS_CI
    debug = False
    run_multi_accounts(debug=debug, headless=headless)
