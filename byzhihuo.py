import os
import random
import time
from selenium.common import TimeoutException
from selenium.webdriver import ActionChains
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.wait import WebDriverWait
import ddddocr
import logging

# ===================== 复用原有公共导入 & 兼容代码 =====================
try:
    from dotenv import load_dotenv
    load_dotenv()
except Exception:
    pass

try:
    from webdriver_manager.chrome import ChromeDriverManager
    try:
        from webdriver_manager.core.utils import ChromeType
    except ImportError:
        try:
            from webdriver_manager.chrome import ChromeType
        except ImportError:
            ChromeType = None
except ImportError:
    print("webdriver_manager未安装，将使用备用方式")
    ChromeDriverManager = None
    ChromeType = None

try:
    from notify import send
    print("已加载通知模块 (notify.py)")
except ImportError:
    print("警告: 未找到 notify.py，将无法发送通知。")
    def send(*args, **kwargs):
        pass

# ===================== 复用原有 Selenium 初始化函数（完全不动） =====================
def init_selenium(debug=False, headless=False):
    from selenium import webdriver
    from selenium.webdriver.chrome.service import Service
    ops = webdriver.ChromeOptions()
    if headless or os.environ.get("GITHUB_ACTIONS", "false") == "true":
        for option in ['--headless', '--no-sandbox', '--disable-dev-shm-usage', '--disable-gpu']:
            ops.add_argument(option)
    ops.add_argument('--window-size=1920,1080')
    ops.add_argument('--disable-blink-features=AutomationControlled')
    ops.add_argument('--no-proxy-server')
    ops.add_argument('--lang=zh-CN')

    is_github_actions = os.environ.get("GITHUB_ACTIONS", "false") == "true"
    if debug and not is_github_actions:
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
        print(f"webdriver-manager失败: {e}")

    try:
        driver = webdriver.Chrome(options=ops)
        return driver
    except Exception:
        pass

    raise Exception("无法初始化Selenium WebDriver")

# ===================== 不移之火 专属滑块函数 =====================
def slide_verify(driver, wait):
    """处理登录/签到滑块，滑块完成后等待8秒"""
    try:
        wait.until(EC.presence_of_element_located((By.XPATH, '//canvas[@class="slider-bg"]')))
        time.sleep(1)

        # 截图识别滑块
        bg_img = driver.find_element(By.XPATH, '//canvas[@class="slider-bg"]').screenshot_as_png
        slide_img = driver.find_element(By.XPATH, '//img[@class="slider-block"]').screenshot_as_png

        # ddddocr 识别偏移
        ocr = ddddocr.DdddOcr(det=False, ocr=False)
        res = ocr.slide_match(slide_img, bg_img, simple_target=True)
        offset = res["target"]
        logger.info(f"滑块识别偏移量: {offset}")

        # 模拟真人滑动
        slider = driver.find_element(By.CLASS_NAME, "slider-handle")
        action = ActionChains(driver)
        action.click_and_hold(slider).pause(0.3)
        action.move_by_offset(offset - 6, 0).pause(0.25)
        action.move_by_offset(6, 0).pause(0.6)
        action.release().perform()

        # 滑块后固定等待8秒（你的要求）
        logger.info("滑块验证完成，等待8秒...")
        time.sleep(8)
        return True
    except TimeoutException:
        logger.info("当前无滑块验证")
        return True
    except Exception as e:
        logger.error(f"滑块验证异常: {str(e)}")
        return False

# ===================== 单账号签到主逻辑 =====================
def sign_in_byzh(user, pwd, debug=False, headless=False):
    """不移之火 账号登录+签到"""
    driver = None
    timeout = 15
    LOGIN_URL = "https://www.byzhihuo.com/member.php?mod=logging&action=login"
    SIGN_URL = "https://www.byzhihuo.com/plugin.php?id=k_misign:sign"

    try:
        logger.info(f"开始处理账号: {user}")
        if not debug:
            time.sleep(random.randint(3, 8))

        # 初始化浏览器
        driver = init_selenium(debug=debug, headless=headless)
        wait = WebDriverWait(driver, timeout)

        # 1. 打开登录页
        driver.get(LOGIN)
        time.sleep(3)

        # 2. 填写账号密码
        username_input = wait.until(EC.visibility_of_element_located((By.NAME, "username")))
        pwd_input = wait.until(EC.visibility_of_element_located((By.NAME, "password")))
        username_input.clear()
        username_input.send_keys(user)
        pwd_input.clear()
        pwd_input.send_keys(pwd)

        # 3. 提交登录
        submit_btn = wait.until(EC.element_to_be_clickable((By.XPATH, '//button[@type="submit"]')))
        driver.execute_script("arguments[0].click();", submit_btn)
        time.sleep(2)

        # 4. 处理登录滑块
        slide_verify(driver, wait)

        # 校验是否变为游客（跳search页面=登录失败）
        time.sleep(6)
        current_url = driver.current_url
        if "search.php" in current_url or "member.php?mod=logging" in current_url:
            return False, user, "登录失败，跳转游客页面/未退出登录页"
        logger.info("账号登录成功")

        # 5. 登录后等待3秒 再跳转签到页（你的要求）
        logger.info("登录成功，等待3秒跳转签到页...")
        time.sleep(3)
        driver.get(SIGN_URL)
        time.sleep(3)

        # 6. 执行签到
        try:
            sign_btn = wait.until(EC.element_to_be_clickable((By.XPATH, '//button[contains(text(),"签到")]')))
            driver.execute_script("arguments[0].click();", sign_btn)
            time.sleep(2)
            # 签到滑块
            slide_verify(driver, wait)
        except TimeoutException:
            # 无签到按钮 = 今日已签
            logger.info("检测到今日已完成签到")

        # 7. 签到结果校验 & 日志
        page_text = driver.page_source
        if "签到成功" in page_text or "连续签到" in page_text:
            logger.info("【日志】✅ 签到成功")
            return True, user, "签到成功"
        elif "今天已经签到" in page_text or "今日已签到" in page_text:
            logger.info("【日志】✅ 今日已签到")
            return True, user, "今日已签到"
        else:
            logger.warning("【日志】⚠️ 操作完成，未识别签到状态")
            return True, user, "操作完成，状态未知"

    except Exception as e:
        err_msg = f"运行异常: {str(e)}"
        logger.error(err_msg, exc_info=True)
        return False, user, err_msg
    finally:
        if driver:
            try:
                driver.quit()
            except:
                pass

# ===================== 程序入口（同雨云代码风格） =====================
if __name__ == "__main__":
    # 环境判断
    is_github_actions = os.environ.get("GITHUB_ACTIONS", "false") == "true"
    debug = os.environ.get('DEBUG', 'false').lower() == 'true'
    headless = os.environ.get('HEADLESS', 'false').lower()
    if is_github_actions:
        headless = True

    # 日志初始化
    logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
    logger = logging.getLogger(__name__)

    ver = "1.0"
    logger.info("------------------------------------------------------------------")
    logger.info(f"不移之火 自动签到工作流 v{ver}")
    logger.info("------------------------------------------------------------------")

    # 读取 GitHub 环境变量 多账号
    users_env = os.environ.get("BYZHIHUO_USER", "")
    passwords_env = os.environ.get("BYZHIHUO_PASS", "")
    users = [u.strip() for u in users_env.split('\n') if u.strip()]
    passwords = [p.strip() for p in passwords_env.split('\n') if u.strip()]

    accounts = []
    if len(users) == len(passwords) and len(users) > 0:
        for u, p in zip(users, passwords):
            accounts.append((u, p))
    else:
        logger.error("未配置账号密码或账号密码数量不匹配")
        exit(1)

    # 批量执行账号
    results = []
    for idx, (user, pwd) in enumerate(accounts, 1):
        logger.info(f"\n=== 开始处理第 {idx} 个账号: {user} ===")
        res = sign_in_byzh(user, pwd, debug=debug, headless=headless)
        results.append(res)
        logger.info(f"=== 第 {idx} 个账号处理完成 ===\n")
        time.sleep(40)

    # 统计结果 + 推送通知（复用原有notify）
    success_count = sum(1 for r in results if r[0])
    total_count = len(results)

    if success_count == total_count:
        title = "✅ 不移之火自动签到 - 全部成功"
    elif success_count > 0:
        title = f"⚠️ 不移之火自动签到 - 部分成功({success_count}/{total_count})"
    else:
        title = "❌ 不移之火自动签到 - 全部失败"

    content = f"不移之火签到汇总\n总账号: {total_count}\n成功: {success_count}\n失败: {total_count - success_count}\n\n详细记录:\n"
    for i, (ok, user, msg) in enumerate(results, 1):
        if ok:
            content += f"{i}. ✅ {user} : {msg}\n"
        else:
            content += f"{i}. ❌ {user} : {msg}\n"

    # 发送通知
    try:
        send(title, content)
        logger.info("通知推送成功")
    except Exception as e:
        logger.error(f"通知推送失败: {str(e)}")
