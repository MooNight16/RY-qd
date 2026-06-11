"""
不移之火论坛自动签到脚本
支持多用户、GitHub Actions定时运行
"""

import logging
import os
import random
import time

from selenium import webdriver
from selenium.webdriver import ActionChains
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.wait import WebDriverWait

try:
    from dotenv import load_dotenv
    load_dotenv()
except Exception:
    pass

# --- webdriver_manager 导入 ---
try:
    from webdriver_manager.chrome import ChromeDriverManager
    try:
        from webdriver_manager.core.utils import ChromeType
    except ImportError:
        ChromeType = None
except ImportError:
    print("webdriver_manager未安装，将使用备用方式")
    ChromeDriverManager = None
    ChromeType = None

# --- notify 通知模块导入 ---
try:
    from notify import send
    print("已加载通知模块 (notify.py)")
except ImportError:
    print("警告: 未找到 notify.py，将无法发送通知。")
    def send(*args, **kwargs):
        pass

# --- 配置 ---
LOGIN_URL = "https://www.byzhihuo.com/member.php?mod=logging&action=login&referer="
SIGN_URL = "https://www.byzhihuo.com/plugin.php?id=k_misign:sign"

# GitHub Actions环境检测
IS_GITHUB_ACTIONS = os.environ.get("GITHUB_ACTIONS", "false") == "true"

# 调试模式
DEBUG = os.environ.get('DEBUG', 'false').lower() == 'true'
HEADLESS = os.environ.get('HEADLESS', 'false').lower() == 'true'
if IS_GITHUB_ACTIONS:
    HEADLESS = True


def init_driver():
    """初始化Chrome驱动"""
    ops = Options()

    # GitHub Actions 或 headless 模式
    if HEADLESS or IS_GITHUB_ACTIONS:
        for option in ['--headless', '--no-sandbox', '--disable-dev-shm-usage', '--disable-gpu']:
            ops.add_argument(option)

    ops.add_argument('--window-size=1920,1080')
    ops.add_argument('--disable-blink-features=AutomationControlled')
    ops.add_argument('--no-proxy-server')
    ops.add_argument('--lang=zh-CN')

    # 设置真实的User-Agent
    ops.add_argument("--user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/149.0.0.0 Safari/537.36")

    # 禁用自动化标志
    ops.add_experimental_option("excludeSwitches", ["enable-automation"])
    ops.add_experimental_option("useAutomationExtension", False)

    # 非调试模式时分离浏览器
    if DEBUG and not IS_GITHUB_ACTIONS:
        ops.add_experimental_option("detach", True)

    try:
        if ChromeDriverManager:
            if ChromeType and hasattr(ChromeType, 'GOOGLE'):
                manager = ChromeDriverManager(chrome_type=ChromeType.GOOGLE)
            else:
                manager = ChromeDriverManager()
            driver_path = manager.install()
            service = Service(driver_path)
            driver = webdriver.Chrome(service=service, options=ops)
            return driver
    except Exception as e:
        logger.error(f"webdriver-manager失败: {e}")

    # 备用方案：直接使用系统Chrome
    try:
        driver = webdriver.Chrome(options=ops)
        return driver
    except Exception:
        pass

    raise Exception("无法初始化Selenium WebDriver")


def apply_stealth(driver):
    """应用反检测脚本"""
    try:
        with open("stealth.min.js", mode="r") as f:
            js = f.read()
        driver.execute_cdp_cmd("Page.addScriptToEvaluateOnNewDocument", {"source": js})
    except Exception:
        # 如果没有stealth.min.js，使用内置脚本
        driver.execute_cdp_cmd("Page.addScriptToEvaluateOnNewDocument", {
            "source": """
                Object.defineProperty(navigator, 'webdriver', {
                    get: () => undefined
                });
                Object.defineProperty(navigator, 'plugins', {
                    get: () => [1, 2, 3, 4, 5]
                });
                Object.defineProperty(navigator, 'languages', {
                    get: () => ['zh-CN', 'zh', 'en']
                });
                window.chrome = { runtime: {} };
            """
        })


def login(driver, username, password):
    """登录论坛"""
    logger.info(f"正在登录用户: {username}")
    driver.get(LOGIN_URL)
    time.sleep(5)

    try:
        # 等待页面加载
        WebDriverWait(driver, 15).until(
            EC.presence_of_element_located((By.NAME, "username"))
        )
        time.sleep(5)

        # 输入用户名
        logger.info("输入用户名...")
        driver.find_element(By.NAME, "username").clear()
        driver.find_element(By.NAME, "username").send_keys(username)
        time.sleep(1)

        # 输入密码
        logger.info("输入密码...")
        driver.find_element(By.NAME, "password").clear()
        driver.find_element(By.NAME, "password").send_keys(password)
        time.sleep(1)

        # 点击登录按钮
        logger.info("点击登录按钮...")
        login_btn = driver.find_element(By.CSS_SELECTOR, "button.pnc")
        login_btn.click()

        # 等待登录完成
        time.sleep(8)

        # 检查是否登录成功
        if "游客" not in driver.page_source:
            logger.info(f"用户 {username} 登录成功")
            return True
        else:
            logger.warning(f"用户 {username} 登录可能失败，仍为游客状态")
            return False

    except Exception as e:
        logger.error(f"登录异常: {e}")
        return False


def solve_slider(driver):
    """处理滑块验证"""
    logger.info("检测到滑块验证，正在处理...")
    time.sleep(5)

    try:
        # 等待滑块元素出现
        slider = WebDriverWait(driver, 10).until(
            EC.presence_of_element_located((By.CLASS_NAME, "nc_iconfont.btn_slide"))
        )

        # 获取背景宽度
        bg_element = driver.find_element(By.CLASS_NAME, "nc_bg")
        bg_size = bg_element.size
        logger.info(f"滑块背景宽度: {bg_size['width']}")

        # 计算滑动距离
        distance = random.randint(80, 180)

        # 执行滑动操作
        action = ActionChains(driver)
        action.click_and_hold(slider)
        action.move_by_offset(distance, 0)
        action.release()
        action.perform()

        time.sleep(2)
        logger.info("滑块验证已尝试")
        return True

    except Exception as e:
        logger.error(f"滑块验证处理异常: {e}")
        return False


def check_signed(driver):
    """检查是否已签到"""
    try:
        visited = driver.find_element(By.CSS_SELECTOR, "span.btnvisted")
        if visited:
            return True
    except:
        pass
    return False


def sign_in(driver):
    """签到"""
    logger.info("正在打开签到页面...")
    driver.get(SIGN_URL)
    time.sleep(5)

    # 检查是否已签到
    if check_signed(driver):
        logger.info("今日已签到，无需重复签到")
        return True, "已签到"

    # 检查是否需要滑块验证
    try:
        slider_check = driver.find_elements(By.CLASS_NAME, "nc_wrapper")
        if slider_check:
            logger.info("检测到滑块验证...")
            solve_slider(driver)
            time.sleep(5)

            # 再次检查是否已签到
            if check_signed(driver):
                logger.info("滑块验证后签到成功")
                return True, "签到成功"
    except:
        pass

    # 尝试点击签到按钮
    try:
        jd_sign_btn = driver.find_element(By.ID, "JD_sign")
        if jd_sign_btn and jd_sign_btn.is_displayed():
            logger.info("找到JD_sign签到按钮，点击...")
            jd_sign_btn.click()
            time.sleep(5)

            # 检查签到结果
            if check_signed(driver):
                logger.info("签到成功！")
                return True, "签到成功"
    except:
        pass

    # 尝试查找其他签到按钮
    try:
        buttons = driver.find_elements(By.TAG_NAME, "button")
        for btn in buttons:
            if "签" in btn.text and "已" not in btn.text:
                if btn.is_displayed():
                    logger.info(f"找到签到按钮: {btn.text}")
                    btn.click()
                    time.sleep(5)

                    if check_signed(driver):
                        logger.info("签到成功！")
                        return True, "签到成功"
    except:
        pass

    logger.warning("未能找到签到按钮，可能已签到或页面结构不同")
    return False, "未找到签到按钮"


def sign_in_account(username, password):
    """执行单个账户的签到"""
    driver = None

    try:
        logger.info(f"========== 开始处理账户: {username} ==========")

        if not DEBUG:
            time.sleep(random.randint(5, 10))

        logger.info("初始化浏览器驱动...")
        driver = init_driver()
        apply_stealth(driver)

        # 登录
        if not login(driver, username, password):
            return False, username, "登录失败", "登录失败"

        # 签到
        success, message = sign_in(driver)
        logger.info(f"账户 {username} 签到结果: {message}")

        logger.info(f"========== 账户 {username} 处理完成 ==========\n")
        return success, username, message, None

    except Exception as e:
        logger.error(f"异常: {e}", exc_info=True)
        return False, username, None, str(e)

    finally:
        if driver:
            try:
                driver.quit()
            except:
                pass


def main():
    """主函数"""
    global logger

    # 设置日志
    log_level = logging.DEBUG if DEBUG else logging.INFO
    logging.basicConfig(
        level=log_level,
        format='%(asctime)s - %(levelname)s - %(message)s',
        handlers=[
            logging.StreamHandler(),
            logging.FileHandler('signin.log', encoding='utf-8')
        ]
    )
    logger = logging.getLogger(__name__)

    logger.info("=" * 60)
    logger.info("不移之火论坛自动签到脚本")
    logger.info("=" * 60)

    # 读取多用户配置
    accounts = []
    users_env = os.environ.get("BYZHIUO_USER", "")
    passwords_env = os.environ.get("BYZHIUO_PASS", "")

    users = [user.strip() for user in users_env.split('\n') if user.strip()]
    passwords = [pwd.strip() for pwd in passwords_env.split('\n') if pwd.strip()]

    if len(users) == len(passwords) and len(users) > 0:
        for user, pwd in zip(users, passwords):
            accounts.append((user, pwd))
        logger.info(f"加载了 {len(accounts)} 个账户")
    else:
        logger.error("未找到有效账户配置或数量不匹配")
        logger.info("请设置环境变量 BYZHIUO_USER 和 BYZHIUO_PASS（多用户用换行分隔）")
        exit(1)

    # 逐个账户执行签到
    results = []
    for i, (username, password) in enumerate(accounts, 1):
        logger.info(f"\n>>> 开始处理第 {i}/{len(accounts)} 个账户: {username}")
        result = sign_in_account(username, password)
        results.append(result)
        logger.info(f">>> 第 {i}/{len(accounts)} 个账户处理完成")

        # 账户间等待，避免频繁请求
        if i < len(accounts):
            wait_time = random.randint(30, 60)
            logger.info(f"等待 {wait_time} 秒后处理下一个账户...")
            time.sleep(wait_time)

    # 生成统计结果
    success_count = sum(1 for r in results if r[0])
    total_count = len(results)

    logger.info("\n" + "=" * 60)
    logger.info("签到结果汇总")
    logger.info("=" * 60)
    for success, username, message, error in results:
        status = "✅ 成功" if success else "❌ 失败"
        detail = message or error or "未知"
        logger.info(f"{status} - {username} - {detail}")
    logger.info("=" * 60)
    logger.info(f"总计: {success_count}/{total_count} 成功")

    # 发送通知
    if success_count == total_count:
        notification_title = f"✅ 不移之火签到完成 - 全部成功 ({success_count}/{total_count})"
    elif success_count > 0:
        notification_title = f"⚠️ 不移之火签到完成 - 部分成功 ({success_count}/{total_count})"
    else:
        notification_title = f"❌ 不移之火签到完成 - 全部失败"

    notification_content = "\n".join([
        f"{'✅' if r[0] else '❌'} {r[1]}: {r[2] or r[3]}"
        for r in results
    ])

    try:
        send(notification_title, notification_content)
        logger.info("通知已发送")
    except Exception as e:
        logger.warning(f"发送通知失败: {e}")

    logger.info("脚本执行完毕")


if __name__ == "__main__":
    logger = None
    main()
