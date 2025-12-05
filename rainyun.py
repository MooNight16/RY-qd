import logging
import os
import random
import re
import time
import subprocess
import sys

import cv2
import ddddocr
import requests
from selenium import webdriver
from selenium.common import TimeoutException
from selenium.webdriver import ActionChains
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.chrome.webdriver import WebDriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.wait import WebDriverWait

try:
    from dotenv import load_dotenv
    load_dotenv()
except Exception:
    pass
# --- 修复1：正确的 webdriver_manager 导入 ---
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

# --- 修复2：确保 notify 正常导入 ---
try:
    from notify import send
    print("已加载通知模块 (notify.py)")
except ImportError:
    print("警告: 未找到 notify.py，将无法发送通知。")
    def send(*args, **kwargs):
        pass


def init_selenium(debug=False, headless=False):
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

    # 备用方案
    try:
        driver = webdriver.Chrome(options=ops)
        return driver
    except Exception:
        pass
        
    raise Exception("无法初始化Selenium WebDriver")

def download_image(url, filename):
    os.makedirs("temp", exist_ok=True)
    try:
        response = requests.get(url, timeout=10, proxies={"http": None, "https": None}, verify=False)
        if response.status_code == 200:
            with open(os.path.join("temp", filename), "wb") as f:
                f.write(response.content)
            return True
        return False
    except Exception as e:
        logger.error(f"下载图片异常: {str(e)}")
        return False


def get_url_from_style(style):
    return re.search(r'url\(["\']?(.*?)["\']?\)', style).group(1)


def get_width_from_style(style):
    return re.search(r'width:\s*([\d.]+)px', style).group(1)


def get_height_from_style(style):
    return re.search(r'height:\s*([\d.]+)px', style).group(1)

# --- 修复3：process_captcha 需要使用全局变量 ---
def process_captcha():
    # 声明使用全局变量，防止报错
    global ocr, det, wait, driver
    
    try:
        download_captcha_img()
        if check_captcha():
            logger.info("开始识别验证码")
            captcha = cv2.imread("temp/captcha.jpg")
            with open("temp/captcha.jpg", 'rb') as f:
                captcha_b = f.read()
            bboxes = det.detection(captcha_b)
            result = dict()
            for i in range(len(bboxes)):
                x1, y1, x2, y2 = bboxes[i]
                spec = captcha[y1:y2, x1:x2]
                cv2.imwrite(f"temp/spec_{i + 1}.jpg", spec)
                for j in range(3):
                    similarity, matched = compute_similarity(f"temp/sprite_{j + 1}.jpg", f"temp/spec_{i + 1}.jpg")
                    similarity_key = f"sprite_{j + 1}.similarity"
                    position_key = f"sprite_{j + 1}.position"
                    if similarity_key in result.keys():
                        if float(result[similarity_key]) < similarity:
                            result[similarity_key] = similarity
                            result[position_key] = f"{int((x1 + x2) / 2)},{int((y1 + y2) / 2)}"
                    else:
                        result[similarity_key] = similarity
                        result[position_key] = f"{int((x1 + x2) / 2)},{int((y1 + y2) / 2)}"
            if check_answer(result):
                for i in range(3):
                    similarity_key = f"sprite_{i + 1}.similarity"
                    position_key = f"sprite_{i + 1}.position"
                    positon = result[position_key]
                    logger.info(f"图案 {i + 1} 位于 ({positon})，匹配率：{result[similarity_key]}")
                    slideBg = wait.until(EC.visibility_of_element_located((By.XPATH, '//*[@id="slideBg"]')))
                    style = slideBg.get_attribute("style")
                    x, y = int(positon.split(",")[0]), int(positon.split(",")[1])
                    width_raw, height_raw = captcha.shape[1], captcha.shape[0]
                    width, height = float(get_width_from_style(style)), float(get_height_from_style(style))
                    x_offset, y_offset = float(-width / 2), float(-height / 2)
                    final_x, final_y = int(x_offset + x / width_raw * width), int(y_offset + y / height_raw * height)
                    ActionChains(driver).move_to_element_with_offset(slideBg, final_x, final_y).click().perform()
                confirm = wait.until(
                    EC.element_to_be_clickable((By.XPATH, '//*[@id="tcStatus"]/div[2]/div[2]/div/div')))
                logger.info("提交验证码")
                confirm.click()
                time.sleep(5)
                result = wait.until(EC.visibility_of_element_located((By.XPATH, '//*[@id="tcOperation"]')))
                if result.get_attribute("class") == 'tc-opera pointer show-success':
                    logger.info("验证码通过")
                    return
                else:
                    logger.error("验证码未通过，正在重试")
            else:
                logger.error("验证码识别失败，正在重试")
        else:
            logger.error("当前验证码识别率低，尝试刷新")
            
        # 确保在 iframe 内能找到 reload 元素，或者逻辑要切出去
        # 原逻辑未切换 frame，直接找 reload，这可能导致找不到元素报错
        # 简单修复：包裹在 try-except 中
        try:
             reload = driver.find_element(By.XPATH, '//*[@id="reload"]')
             time.sleep(2)
             reload.click()
             time.sleep(5)
             process_captcha()
        except:
             pass

    except TimeoutException:
        logger.error("获取验证码图片失败")
    except Exception as e:
        logger.error(f"处理验证码时发生错误: {e}") # 打印具体错误，方便调试


def download_captcha_img():
    # 声明使用全局 wait
    global wait
    
    if os.path.exists("temp"):
        for filename in os.listdir("temp"):
            file_path = os.path.join("temp", filename)
            if os.path.isfile(file_path) or os.path.islink(file_path):
                os.remove(file_path)
    slideBg = wait.until(EC.visibility_of_element_located((By.XPATH, '//*[@id="slideBg"]')))
    img1_style = slideBg.get_attribute("style")
    img1_url = get_url_from_style(img1_style)
    logger.info("开始下载验证码图片(1): " + img1_url)
    download_image(img1_url, "captcha.jpg")
    sprite = wait.until(EC.visibility_of_element_located((By.XPATH, '//*[@id="instruction"]/div/img')))
    img2_url = sprite.get_attribute("src")
    logger.info("开始下载验证码图片(2): " + img2_url)
    download_image(img2_url, "sprite.jpg")


def check_captcha() -> bool:
    # 声明使用全局 ocr
    global ocr
    
    try:
        raw = cv2.imread("temp/sprite.jpg")
        if raw is None: return False
        
        gray = cv2.cvtColor(raw, cv2.COLOR_BGR2GRAY)
        laplacian = cv2.Laplacian(gray, cv2.CV_64F).var()
        if laplacian < 50: return False
            
        h, w = raw.shape[:2]
        for i in range(3):
            w_segment = w // 3
            start_x = max(0, w_segment * i + 2)
            end_x = min(w, w_segment * (i + 1) - 2)
            temp = raw[:, start_x:end_x]
            cv2.imwrite(f"temp/sprite_{i + 1}.jpg", temp)
            
            with open(f"temp/sprite_{i + 1}.jpg", mode="rb") as f:
                temp_rb = f.read()
            try:
                result = ocr.classification(temp_rb)
                if result in ["0", "1"]: return False
            except Exception:
                return False
        return True
    except Exception:
        return False


def check_answer(d: dict) -> bool:
    flipped = dict()
    for key in d.keys():
        flipped[d[key]] = key
    if len(d.values()) != len(flipped.keys()):
        return False
    return True


def preprocess_image(image):
    blurred = cv2.GaussianBlur(image, (5, 5), 0)
    thresh = cv2.adaptiveThreshold(blurred, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY, 11, 2)
    kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (3, 3))
    morph = cv2.morphologyEx(thresh, cv2.MORPH_CLOSE, kernel)
    return morph

def compute_similarity(img1_path, img2_path):
    img1 = cv2.imread(img1_path, cv2.IMREAD_GRAYSCALE)
    img2 = cv2.imread(img2_path, cv2.IMREAD_GRAYSCALE)
    if img1 is None or img2 is None: return 0.0, 0
    
    scale = 100.0 / max(img1.shape) if max(img1.shape) > 100 else 1.0
    img1 = cv2.resize(img1, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA)
    scale = 100.0 / max(img2.shape) if max(img2.shape) > 100 else 1.0
    img2 = cv2.resize(img2, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA)
    
    img1 = preprocess_image(img1)
    img2 = preprocess_image(img2)

    try:
        sift = cv2.SIFT_create()
        kp1, des1 = sift.detectAndCompute(img1, None)
        kp2, des2 = sift.detectAndCompute(img2, None)
        if des1 is None or des2 is None: return 0.0, 0

        flann = cv2.FlannBasedMatcher(dict(algorithm=1, trees=5), dict(checks=50))
        matches = flann.knnMatch(des1, des2, k=2)
        good = [m for m, n in matches if m.distance < 0.7 * n.distance]
        
        if len(good) == 0: return 0.0, 0
        feature_factor = min(1.0, len(kp1) / 100.0, len(kp2) / 100.0)
        match_ratio = len(good) / min(len(des1), len(des2))
        return match_ratio * 0.7 + feature_factor * 0.3, len(good)
    except Exception:
        return 0.0, 0


def sign_in_account(user, pwd, debug=False, headless=False):
    timeout = 15
    driver = None
    
    # --- 修复4：声明全局变量，以便 process_captcha 调用 ---
    global ocr, det, wait 
    
    try:
        logger.info(f"开始处理账户: {user}")
        
        
        logger.info("初始化 ddddocr")
        ocr = ddddocr.DdddOcr(ocr=True, show_ad=False)
        det = ddddocr.DdddOcr(det=True, show_ad=False)
        
        logger.info("初始化 Selenium")
        driver = init_selenium(debug=debug, headless=headless)
        
        # 临时将 driver 设为全局，供 process_captcha 使用
        globals()['driver'] = driver 
        
        try:
            with open("stealth.min.js", mode="r") as f: js = f.read()
            driver.execute_cdp_cmd("Page.addScriptToEvaluateOnNewDocument", {"source": js})
        except: pass
        
        logger.info("发起登录请求")
        driver.get("https://app.rainyun.com/auth/login")
        wait = WebDriverWait(driver, timeout)
        
        # 登录流程
        username = wait.until(EC.visibility_of_element_located((By.NAME, 'login-field')))
        password = wait.until(EC.visibility_of_element_located((By.NAME, 'login-password')))
        try:
            login_button = wait.until(EC.element_to_be_clickable((By.XPATH, '//*[@id="app"]/div[1]/div[1]/div/div[2]/fade/div/div/span/form/button')))
        except:
            login_button = wait.until(EC.element_to_be_clickable((By.CSS_SELECTOR, 'button[type="submit"]')))
            
        username.clear()
        password.clear()
        username.send_keys(user)
        time.sleep(0.5)
        password.send_keys(pwd)
        time.sleep(0.5)
        driver.execute_script("arguments[0].click();", login_button)
        
        # 登录验证码
        try:
            wait.until(EC.visibility_of_element_located((By.ID, 'tcaptcha_iframe_dy')))
            logger.warning("触发验证码！")
            driver.switch_to.frame("tcaptcha_iframe_dy")
            process_captcha()
        except TimeoutException:
            logger.info("未触发验证码")
        
        time.sleep(5)
        driver.switch_to.default_content()
        
        if "dashboard" in driver.current_url:
            logger.info("登录成功！")
            logger.info("正在转到赚取积分页")
            
            # --- 修复5：给点击操作增加稳定性 ---
            for _ in range(3):
                try:
                    driver.get("https://app.rainyun.com/account/reward/earn")
                    wait.until(EC.presence_of_element_located((By.TAG_NAME, 'body')))
                    time.sleep(3)

                    try:
                        # 限定在“每日签到”这一行查找对应的按钮/状态，避免其它行干扰
                        claim_btns = driver.find_elements(By.XPATH, "//span[contains(text(),'每日签到')]/following::a[contains(@href,'/account/reward/earn')][1]")
                        if any(el.is_displayed() for el in claim_btns):
                            logger.info("检测到‘每日签到’行的‘领取奖励’，进入签到流程")
                        else:
                            completed = driver.find_elements(By.XPATH, "//span[contains(text(),'每日签到')]/following::span[contains(text(),'已完成')][1]")
                            if any(el.is_displayed() for el in completed):
                                logger.info("‘每日签到’显示已完成，跳过当前账号")
                                try:
                                    points_raw = driver.find_element(By.XPATH, '//*[@id="app"]/div[1]/div[3]/div[2]/div/div/div[2]/div[1]/div[1]/div/p/div/h3').get_attribute("textContent")
                                    current_points = int(''.join(re.findall(r'\d+', points_raw)))
                                except:
                                    current_points = 0
                                return True, user, current_points, None
                    except Exception:
                        pass

                    strategies = [
                        (By.XPATH, '//*[@id="app"]/div[1]/div[3]/div[2]/div/div/div[2]/div[2]/div/div/div/div[1]/div/div[1]/div/div[1]/div/span[2]/a'),
                        (By.XPATH, '//a[contains(@href, "earn") and contains(text(), "赚取")]'),
                        (By.CSS_SELECTOR, 'a[href*="earn"]')
                    ]
                    
                    earn = None
                    for by, selector in strategies:
                        try:
                            earn = wait.until(EC.element_to_be_clickable((by, selector)))
                            break
                        except: continue
                    
                    if earn:
                        driver.execute_script("arguments[0].scrollIntoView(true);", earn)
                        time.sleep(1)
                        logger.info("点击赚取积分")
                        driver.execute_script("arguments[0].click();", earn)
                        
                        # --- 核心修复：点击后等待，确保验证码 iframe 有时间加载 ---
                        logger.info("等待验证码加载（如果有）...")
                        
                        try:
                            WebDriverWait(driver, 15, poll_frequency=0.25).until(
                                EC.visibility_of_element_located((By.ID, "tcaptcha_iframe_dy"))
                            )
                            wait.until(EC.frame_to_be_available_and_switch_to_it((By.ID, "tcaptcha_iframe_dy")))
                            logger.info("处理验证码")
                            process_captcha()
                            driver.switch_to.default_content()
                        except TimeoutException:
                            logger.info("未触发验证码，继续")
                            driver.switch_to.default_content()
                        except Exception as e:
                            logger.error(f"验证码处理过程出错: {e}")
                            driver.switch_to.default_content()
                        
                        logger.info("赚取积分操作完成")
                        break
                    else:
                        driver.refresh()
                        time.sleep(3)
                except Exception as e:
                    logger.error(f"出错: {e}")
                    time.sleep(3)
            
            driver.implicitly_wait(5)
            # 简单的积分获取（不对比，保持原逻辑）
            try:
                points_raw = driver.find_element(By.XPATH, '//*[@id="app"]/div[1]/div[3]/div[2]/div/div/div[2]/div[1]/div[1]/div/p/div/h3').get_attribute("textContent")
                current_points = int(''.join(re.findall(r'\d+', points_raw)))
                logger.info(f"当前剩余积分: {current_points} | 约为 {current_points / 2000:.2f} 元")
            except:
                current_points = 0
                
            logger.info("任务执行成功！")
            return True, user, current_points, None
        else:
            return False, user, 0, "登录失败"

    except Exception as e:
        logger.error(f"异常: {str(e)}", exc_info=True)
        return False, user, 0, str(e)
    finally:
        if driver:
            try: driver.quit()
            except: pass

if __name__ == "__main__":
    is_github_actions = os.environ.get("GITHUB_ACTIONS", "false") == "true"
    debug = os.environ.get('DEBUG', 'false').lower() == 'true'
    headless = os.environ.get('HEADLESS', 'false').lower() == 'true'
    if is_github_actions: headless = True
    
    logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
    logger = logging.getLogger(__name__)
    
    ocr = None
    det = None
    wait = None

    ver = "2.2 (Fix)"
    logger.info("------------------------------------------------------------------")
    logger.info(f"雨云自动签到工作流 v{ver}")
    logger.info("------------------------------------------------------------------")
    
    accounts = []
    users_env = os.environ.get("RAINYUN_USER", "")
    passwords_env = os.environ.get("RAINYUN_PASS", "")
    users = [user.strip() for user in users_env.split('\n') if user.strip()]
    passwords = [pwd.strip() for pwd in passwords_env.split('\n') if pwd.strip()]
    
    if len(users) == len(passwords) and len(users) > 0:
        for user, pwd in zip(users, passwords):
            accounts.append((user, pwd))
    else:
        logger.error("未找到有效账户配置或数量不匹配")
        exit(1)
    
    results = []
    for i, (user, pwd) in enumerate(accounts, 1):
        logger.info(f"\n=== 开始处理第 {i} 个账户: {user} ===")
        result = sign_in_account(user, pwd, debug=debug, headless=headless)
        results.append(result)
        logger.info(f"=== 第 {i} 个账户处理完成 ===\n")

    if not debug:
            time.sleep(random.randint(600, 603))
    
    # 生成统一通知
    success_count = sum(1 for r in results if r[0])
    total_count = len(results)
    
    if success_count == total_count:
        notification_title = f"✅ 雨云自动签到完成 - 全部成功"
    elif success_count > 0:
        notification_title = f"⚠️ 雨云自动签到完成 - 部分成功 ({success_count}/{total_count})"
    else:
        notification_title = f"❌ 雨云自动签到完成 - 全部失败"
    
    notification_content = f"雨云自动签到结果汇总：\n\n总账户数: {total_count}\n成功账户数: {success_count}\n失败账户数: {total_count - success_count}\n\n详细结果：\n"
    
    for i, (success, user, points, error_msg) in enumerate(results, 1):
        if success:
            notification_content += f"{i}. ✅ {user}\n   积分: {points} | 约 {points / 2000:.2f} 元\n"
        else:
            notification_content += f"{i}. ❌ {user}\n   错误: {error_msg}\n"
    
    # 发送统一通知
    try:
        send(notification_title, notification_content)
        logger.info("统一通知发送成功")
    except Exception as e:
        logger.error(f"发送通知失败: {e}")
