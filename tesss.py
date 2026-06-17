import logging
import os
import random
import re
import time

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

# 关闭 https 证书警告
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# --- 加载 .env 环境变量 ---
try:
    from dotenv import load_dotenv
    load_dotenv()
except Exception:
    pass

# --- webdriver_manager 导入（GitHub Actions 环境需要） ---
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
    ChromeDriverManager = None
    ChromeType = None

# --- 通知模块导入 ---
try:
    from notify import send
    logger_notify = True
except ImportError:
    def send(*args, **kwargs):
        pass
    logger_notify = False

# ===== 站点配置 =====
LOGIN_URL = "https://www.byzhihuo.com/member.php?mod=logging&action=login&referer="
SIGN_URL = "https://www.byzhihuo.com/plugin.php?id=k_misign:sign"
TEMP_DIR = "temp"

# ===== 本地 Chrome / chromedriver 路径配置（仅本地使用）=====
LOCAL_CHROME_PATH = os.environ.get("LOCAL_CHROME_PATH", r"C:\Program Files\Google\Chrome\Application\chrome.exe")
LOCAL_CHROMEDRIVER_PATH = os.environ.get("LOCAL_CHROMEDRIVER_PATH", r"E:\QQBot\chromedriver149\chromedriver.exe")

# ===== 多账户配置 =====
# 环境变量格式：ACCOUNTS="user1:pass1,user2:pass2,user3:pass3"
def get_accounts():
    """从环境变量 ACCOUNTS 读取账户列表，格式: user1:pass1,user2:pass2"""
    accounts_str = os.environ.get("ACCOUNTS", "")
    if not accounts_str:
        # 兼容旧的单账户环境变量
        user = os.environ.get("USERNAME", "zzxmoon")
        pwd = os.environ.get("PASSWORD", "1234qwer")
        if user and pwd:
            return [(user, pwd)]
        return []
    accounts = []
    for item in accounts_str.split(","):
        item = item.strip()
        if ":" in item:
            user, pwd = item.split(":", 1)
            accounts.append((user.strip(), pwd.strip()))
    return accounts

# ===== 日志配置 =====
logging.basicConfig(level=logging.INFO,
                    format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)


# ----------------------------- 工具函数 -----------------------------

def get_url_from_style(style):
    m = re.search(r'url\(["\']?(.*?)["\']?\)', style or "")
    return m.group(1) if m else None


def get_width_from_style(style):
    m = re.search(r'width:\s*([\d.]+)px', style or "")
    return float(m.group(1)) if m else None


def get_height_from_style(style):
    m = re.search(r'height:\s*([\d.]+)px', style or "")
    return float(m.group(1)) if m else None


def get_left_from_style(style):
    m = re.search(r'left:\s*([\d.]+)px', style or "")
    return float(m.group(1)) if m else None


# ----------------------------- Selenium -----------------------------

def init_selenium(debug=False, headless=False):
    """初始化 Selenium WebDriver，兼容本地和 GitHub Actions 环境。"""
    ops = webdriver.ChromeOptions()

    is_github_actions = os.environ.get("GITHUB_ACTIONS", "false") == "true"

    # GitHub Actions 环境自动 headless
    if headless or is_github_actions:
        for option in ['--headless', '--no-sandbox', '--disable-dev-shm-usage', '--disable-gpu']:
            ops.add_argument(option)

    ops.add_argument('--window-size=1920,1080')
    ops.add_argument('--disable-blink-features=AutomationControlled')
    ops.add_argument('--no-proxy-server')
    ops.add_argument('--lang=zh-CN')

    if debug and not is_github_actions:
        ops.add_experimental_option("detach", True)

    # 优先使用 webdriver_manager（GitHub Actions 环境）
    if ChromeDriverManager:
        try:
            if ChromeType and hasattr(ChromeType, 'GOOGLE'):
                manager = ChromeDriverManager(chrome_type=ChromeType.GOOGLE)
            else:
                manager = ChromeDriverManager()
            driver_path = manager.install()
            service = Service(driver_path)
            driver = webdriver.Chrome(service=service, options=ops)
            logger.info("使用 webdriver_manager 初始化 WebDriver")
            return driver
        except Exception as e:
            logger.warning(f"webdriver_manager 初始化失败: {e}")

    # 备选：直接使用 ChromeDriver（无需指定路径）
    try:
        driver = webdriver.Chrome(options=ops)
        logger.info("使用系统 ChromeDriver 初始化 WebDriver")
        return driver
    except Exception:
        pass

    # 最后尝试本地指定路径
    if os.path.exists(LOCAL_CHROME_PATH) and os.path.exists(LOCAL_CHROMEDRIVER_PATH):
        ops.binary_location = LOCAL_CHROME_PATH
        service = Service(LOCAL_CHROMEDRIVER_PATH)
        driver = webdriver.Chrome(service=service, options=ops)
        logger.info("使用本地 Chrome 路径初始化 WebDriver")
        return driver

    raise Exception("无法初始化 Selenium WebDriver，请检查 Chrome 和 ChromeDriver 是否安装")


# ----------------------------- 验证码图片下载 -----------------------------

def download_image(url, filename):
    """下载图片到 ./temp 目录。"""
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
        logger.error(f"下载图片失败 status={response.status_code}: {url}")
        return False
    except Exception as e:
        logger.error(f"下载图片异常: {e} | url={url}")
        return False


def clear_temp():
    """清空 ./temp 目录。"""
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


def switch_to_captcha_iframe(driver, timeout=15):
    """点击签到按钮后腾讯验证码会嵌在 iframe 中，先切到该 iframe。"""
    end_time = time.time() + timeout
    while time.time() < end_time:
        frames = driver.find_elements(
            By.CSS_SELECTOR,
            'iframe[id*="tcaptcha"], iframe[src*="captcha.qq.com"], '
            'iframe[id*="captcha"], iframe[src*="captcha"]'
        )
        for frame in frames:
            try:
                driver.switch_to.frame(frame)
                if driver.find_elements(By.ID, "slideBg"):
                    logger.info("已切换到包含 #slideBg 的验证码 iframe")
                    return True
                driver.switch_to.default_content()
            except Exception:
                try:
                    driver.switch_to.default_content()
                except Exception:
                    pass

        all_frames = driver.find_elements(By.CSS_SELECTOR, "iframe, frame")
        for frame in all_frames:
            try:
                driver.switch_to.frame(frame)
                if driver.find_elements(By.ID, "slideBg"):
                    logger.info("已切换到包含 #slideBg 的 iframe（兜底匹配）")
                    return True
                driver.switch_to.default_content()
            except Exception:
                try:
                    driver.switch_to.default_content()
                except Exception:
                    pass

        time.sleep(0.5)

    driver.switch_to.default_content()
    return False


def download_captcha_img(driver, wait):
    """下载腾讯滑块验证码的背景图与滑块图到 ./temp，并返回缩放比例。"""
    clear_temp()

    slide_bg = wait.until(
        EC.visibility_of_element_located((By.XPATH, '//*[@id="slideBg"]'))
    )
    img1_url = slide_bg.get_attribute("src")
    if not img1_url:
        logger.error("未读取到 #slideBg 的 src")
        return False, 1.0, 0
    logger.info(f"开始下载背景图片(1): {img1_url}")
    if not download_image(img1_url, "bg.jpg"):
        return False, 1.0, 0

    bg_display_width = slide_bg.size['width']

    slide_block = wait.until(
        EC.visibility_of_element_located((By.XPATH, '//*[@id="slideBlock"]'))
    )
    img2_url = slide_block.get_attribute("src")
    if not img2_url:
        logger.error("未读取到 #slideBlock 的 src")
        return False, 1.0, 0
    logger.info(f"开始下载滑块图片(2): {img2_url}")
    if not download_image(img2_url, "sprite.png"):
        return False, 1.0, 0

    from PIL import Image
    bg_img = Image.open(os.path.join(TEMP_DIR, "bg.jpg"))
    bg_actual_width = bg_img.size[0]
    scale_ratio = bg_display_width / bg_actual_width
    logger.info(f"背景图实际宽度: {bg_actual_width}, 网页显示宽度: {bg_display_width}, 缩放比例: {scale_ratio:.4f}")

    sprite_img = Image.open(os.path.join(TEMP_DIR, "sprite.png"))
    sprite_actual_width = sprite_img.size[0]
    logger.info(f"滑块图片实际宽度: {sprite_actual_width}")

    logger.info(
        f"验证码图片已保存：{os.path.join(TEMP_DIR, 'bg.jpg')}、"
        f"{os.path.join(TEMP_DIR, 'sprite.png')}"
    )
    return True, scale_ratio, sprite_actual_width


def get_slide_dis(scale_ratio=1.0, sprite_width=0):
    """使用 ddddocr 计算滑块需要移动的距离，并根据缩放比例调整。"""
    ocr = ddddocr.DdddOcr(det=False, ocr=False, show_ad=False)

    sprite_path = os.path.join(TEMP_DIR, "sprite.png")
    bg_path = os.path.join(TEMP_DIR, "bg.jpg")

    with open(sprite_path, 'rb') as f:
        sprite_bytes = f.read()
    with open(bg_path, "rb") as f:
        bg_bytes = f.read()

    result = ocr.slide_match(sprite_bytes, bg_bytes, simple_target=True)
    target = result["target"]

    if len(target) == 4:
        x1, y1, x2, y2 = target
        logger.info(f"识别缺口坐标 左上({x1},{y1}) 右下({x2},{y2})")
    elif len(target) == 2:
        x1, x2 = target
        logger.info(f"识别缺口坐标 x1={x1}, x2={x2}")
    else:
        x1 = target[0]
        logger.info(f"识别缺口坐标 x1={x1}")

    adjusted_distance = int(x1 * scale_ratio - sprite_width * scale_ratio)
    logger.info(f"原始距离: {x1}, 缩放比例: {scale_ratio:.4f}, 滑块宽度: {sprite_width}, 减去滑块宽度后: {adjusted_distance}")
    return adjusted_distance


def get_tracks(distance):
    """
    生成模拟人工拖动的轨迹。
    使用先加速后减速的策略，步数控制在 8-15 步，总耗时约 0.3-0.8 秒。
    """
    tracks = []
    current = 0
    total_steps = random.randint(8, 15)

    for i in range(total_steps):
        progress = i / total_steps

        if progress < 0.3:
            base_move = distance / total_steps * 1.5
        elif progress < 0.7:
            base_move = distance / total_steps * 1.2
        else:
            base_move = distance / total_steps * 0.8

        move = base_move * random.uniform(0.85, 1.15)
        move = round(move, 2)

        current += move
        if current > distance:
            move = distance - (current - move)
            current = distance

        tracks.append(move)

    tracks.extend([random.uniform(-1.5, -0.5), random.uniform(0.5, 1.5)])
    return tracks


def drag_slider(driver, distance):
    """模拟人工拖动滑块。"""
    try:
        slider = WebDriverWait(driver, 10).until(
            EC.element_to_be_clickable((By.ID, "slideBlock"))
        )
    except:
        slider = WebDriverWait(driver, 10).until(
            EC.element_to_be_clickable((By.CSS_SELECTOR, ".tcaptcha-drag-button, .slide-button, #tcaptcha_drag_button"))
        )

    tracks = get_tracks(distance)
    logger.info(f"生成拖动轨迹，共 {len(tracks)} 步，目标距离: {distance}")

    action = ActionChains(driver)
    action.click_and_hold(slider).perform()
    time.sleep(random.uniform(0.05, 0.1))

    for track in tracks:
        action.move_by_offset(track, random.uniform(-1, 1)).perform()
        time.sleep(random.uniform(0.005, 0.015))

    time.sleep(random.uniform(0.05, 0.1))
    action.release().perform()
    logger.info("滑块拖动完成，已释放鼠标")
    time.sleep(3)


def solve_captcha(driver, wait):
    """完整的验证码解决流程。"""
    success, scale_ratio, sprite_width = download_captcha_img(driver, wait)
    if not success:
        return False, "下载验证码图片失败"

    try:
        distance = get_slide_dis(scale_ratio, sprite_width)
    except Exception as e:
        logger.error(f"ddddocr 识别失败: {e}", exc_info=True)
        return False, f"ddddocr 识别失败: {e}"

    try:
        drag_slider(driver, distance)
    except Exception as e:
        logger.error(f"拖动滑块失败: {e}", exc_info=True)
        return False, f"拖动滑块失败: {e}"

    time.sleep(5)
    page_source = driver.page_source

    if "验证成功" in page_source or "success" in page_source.lower():
        logger.info("验证码验证成功")
        return True, "验证成功"

    try:
        driver.find_element(By.ID, "slideBg")
        logger.warning("验证码仍在，可能需要重试")
        return False, "验证未通过，滑块仍在"
    except NoSuchElementException:
        logger.info("验证码元素消失，验证通过")
        return True, "验证通过"


# ----------------------------- 业务流程 -----------------------------

def do_login(driver, wait, user, pwd):
    """执行不移之火论坛登录流程。"""
    logger.info(f"打开登录页：{LOGIN_URL}")
    driver.get(LOGIN_URL)

    username_input = wait.until(
        EC.visibility_of_element_located((By.NAME, "username"))
    )
    password_input = wait.until(
        EC.visibility_of_element_located((By.NAME, "password"))
    )

    username_input.clear()
    username_input.send_keys(user)
    time.sleep(0.5)
    password_input.clear()
    password_input.send_keys(pwd)
    time.sleep(0.5)

    login_btn = wait.until(
        EC.element_to_be_clickable(
            (By.CSS_SELECTOR, 'button[name="loginsubmit"]'))
    )
    logger.info("点击登录按钮")
    driver.execute_script("arguments[0].click();", login_btn)

    time.sleep(5)
    driver.switch_to.default_content()

    try:
        driver.find_element(By.NAME, "username")
        page_text = driver.page_source
        if "欢迎您回来" in page_text or "现在将转入登录前页面" in page_text:
            logger.info("登录成功（欢迎页）")
            return True
        logger.error("登录失败：仍停留在登录页")
        return False
    except NoSuchElementException:
        logger.info("登录成功")
        return True


def do_sign(driver, wait):
    """执行 K Misign 签到，处理验证码并完成签到。"""
    logger.info(f"打开签到页：{SIGN_URL}")
    driver.get(SIGN_URL)
    time.sleep(3)

    try:
        sign_btn = wait.until(
            EC.element_to_be_clickable((By.ID, "JD_sign"))
        )
    except TimeoutException:
        logger.error("未找到签到按钮 #JD_sign")
        return False, "未找到签到按钮"

    logger.info("点击签到按钮 #JD_sign")
    try:
        driver.execute_script("arguments[0].click();", sign_btn)
    except Exception as e:
        logger.error(f"点击签到按钮异常：{e}")
        return False, str(e)

    try:
        wait.until(EC.visibility_of_element_located((By.ID, 'tcaptcha_iframe')))
        logger.warning("触发验证码！")
        driver.switch_to.frame("tcaptcha_iframe")
    except TimeoutException:
        logger.info("未触发验证码，检查签到结果")
        time.sleep(2)
        page_text = driver.page_source
        if "签到成功" in page_text or "已签到" in page_text:
            return True, "签到成功"
        return True, "签到完成（未检测到验证码）"
    except Exception as e:
        logger.error(f"处理验证码异常：{e}", exc_info=True)
        driver.switch_to.default_content()
        return False, f"处理验证码异常：{e}"

    success, msg = solve_captcha(driver, wait)
    driver.switch_to.default_content()

    if not success:
        return False, f"验证码处理失败: {msg}"

    time.sleep(3)
    page_text = driver.page_source

    if "签到成功" in page_text:
        return True, "签到成功"
    elif "已签到" in page_text:
        return True, "今日已签到"
    elif "恭喜你" in page_text:
        return True, "签到完成"
    else:
        return True, "签到流程完成"


def sign_in_account(user, pwd, debug=False, headless=False):
    """单个账户签到流程。"""
    timeout = 20
    driver = None
    try:
        logger.info(f"开始处理账户：{user}")
        driver = init_selenium(debug=debug, headless=headless)
        wait = WebDriverWait(driver, timeout)

        if not do_login(driver, wait, user, pwd):
            return False, user, "登录失败"

        success, msg = do_sign(driver, wait)
        return success, user, msg

    except Exception as e:
        logger.error(f"异常：{e}", exc_info=True)
        return False, user, str(e)
    finally:
        if driver and not debug:
            try:
                driver.quit()
            except Exception:
                pass


def run_all_accounts(debug=False, headless=False):
    """多账户签到主流程，每个账户运行后等待一分钟。"""
    accounts = get_accounts()
    if not accounts:
        logger.error("未配置任何账户，请设置环境变量 ACCOUNTS（格式: user1:pass1,user2:pass2）")
        send("不移之火签到", "未配置任何账户，请设置环境变量 ACCOUNTS")
        return

    logger.info(f"共加载 {len(accounts)} 个账户")
    ver = "2.0 (Multi-Account + GitHub Actions)"
    logger.info("------------------------------------------------------------------")
    logger.info(f"不移之火论坛自动签到 v{ver}")
    logger.info("------------------------------------------------------------------")

    results = []
    total = len(accounts)

    for index, (user, pwd) in enumerate(accounts, 1):
        logger.info(f"===== 第 {index}/{total} 个账户 =====")
        success, user, msg = sign_in_account(user, pwd, debug=debug, headless=headless)

        if success:
            logger.info(f"[成功] {user} | {msg}")
            results.append(f"[成功] {user} | {msg}")
        else:
            logger.info(f"[失败] {user} | {msg}")
            results.append(f"[失败] {user} | {msg}")

        # 每个账户运行后等待一分钟（最后一个账户不需要等待）
        if index < total:
            logger.info(f"等待 60 秒后处理下一个账户...")
            time.sleep(60)

    # 汇总结果
    success_count = sum(1 for r in results if "[成功]" in r)
    fail_count = total - success_count

    summary = "\n".join(results)
    title = f"不移之火签到完成: {success_count}成功/{fail_count}失败"

    logger.info("------------------------------------------------------------------")
    logger.info(f"签到汇总: {success_count} 成功, {fail_count} 失败, 共 {total} 个账户")
    for r in results:
        logger.info(r)
    logger.info("------------------------------------------------------------------")

    # 推送通知
    try:
        send(title, summary)
        logger.info("通知推送成功")
    except Exception as e:
        logger.error(f"通知推送失败: {e}")


if __name__ == "__main__":
    debug = os.environ.get('DEBUG', 'false').lower() == 'true'
    headless = os.environ.get('HEADLESS', 'false').lower() == 'true'

    run_all_accounts(debug=debug, headless=headless)
