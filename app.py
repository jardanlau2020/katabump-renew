#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import re
import sys
import time
import subprocess
from datetime import date, datetime

import requests
from seleniumbase import SB

# 从环境变量获取账号密码和 TG 配置
EMAIL        = os.environ.get("KATABUMP_EMAIL") or ""    # 登录邮箱
PASSWORD     = os.environ.get("KATABUMP_PASSWORD") or "" # 账号密码
TG_CHAT_ID   = os.environ.get("TG_CHAT_ID") or ""        # tg通知 chat id(可选)
TG_BOT_TOKEN = os.environ.get("TG_BOT_TOKEN") or ""      # tg通知bot token(可选)

BASE_URL = "https://dashboard.katabump.com"  # 网站链接

#  Telegram 推送模块
def send_tg_message(status_icon, status_text, time_left=""):
    if not TG_BOT_TOKEN or not TG_CHAT_ID:
        print("ℹ️ 未配置 TG_BOT_TOKEN 或 TG_CHAT_ID，跳过 Telegram 推送。")
        return

    # 获取北京时间 (UTC+8)
    local_time = time.gmtime(time.time() + 8 * 3600)
    current_time_str = time.strftime("%Y-%m-%d %H:%M:%S", local_time)

    # 邮箱脱敏：保留用户名前2位和后2位，中间用****代替
    if '@' in EMAIL:
        name, domain = EMAIL.split('@', 1)
        if len(name) > 4:
            masked_email = f"{name[:2]}****{name[-2:]}@{domain}"
        else:
            masked_email = f"{name}@{domain}"
    else:
        masked_email = EMAIL[:2] + '****'

    text = (
        f"🇫🇷 katabump 续期通知\n\n"
        f"{status_icon} {status_text}\n"
        f"👤 续期账户: {masked_email}\n"
        f"⏱️ 续期时间: {current_time_str}"
    )

    url = f"https://api.telegram.org/bot{TG_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": TG_CHAT_ID,
        "text": text
    }
    
    try:
        r = requests.post(url, json=payload, timeout=10)
        if r.status_code == 200:
            print("📩 Telegram 通知发送成功！")
        else:
            print(f"⚠️ Telegram 通知发送失败: {r.text}")
    except Exception as e:
        print(f"⚠️ Telegram 通知发送异常: {e}")

#  页面注入脚本
_EXPAND_JS = """
(function() {
    var ts = document.querySelector('input[name="cf-turnstile-response"]');
    if (!ts) return 'no-turnstile';
    var el = ts;
    for (var i = 0; i < 20; i++) {
        el = el.parentElement;
        if (!el) break;
        var s = window.getComputedStyle(el);
        if (s.overflow === 'hidden' || s.overflowX === 'hidden' || s.overflowY === 'hidden')
            el.style.overflow = 'visible';
        el.style.minWidth = 'max-content';
    }
    document.querySelectorAll('iframe').forEach(function(f){
        if (f.src && f.src.includes('challenges.cloudflare.com')) {
            f.style.width = '300px'; f.style.height = '65px';
            f.style.minWidth = '300px';
            f.style.visibility = 'visible'; f.style.opacity = '1';
        }
    });
    return 'done';
})()
"""

_EXISTS_JS = """
(function(){
    return document.querySelector('input[name="cf-turnstile-response"]') !== null;
})()
"""

_SOLVED_JS = """
(function(){
    var i = document.querySelector('input[name="cf-turnstile-response"]');
    return !!(i && i.value && i.value.length > 20);
})()
"""

_WININFO_JS = """
(function(){
    return {
        sx: window.screenX || 0,
        sy: window.screenY || 0,
        oh: window.outerHeight,
        ih: window.innerHeight
    };
})()
"""

# ===== 自动续期相关 =====

# 在模态框内查找 iframe 并展开，返回点击坐标
_ALTCHA_EXPAND_JS = """
(function() {
    var modal = document.querySelector('div.modal.show') || document;
    var iframes = modal.querySelectorAll('iframe');
    for (var i = 0; i < iframes.length; i++) {
        var r = iframes[i].getBoundingClientRect();
        if (r.width > 0 && r.height > 0) {
            iframes[i].style.width  = '300px';
            iframes[i].style.height = '150px';
            iframes[i].style.minWidth  = '300px';
            iframes[i].style.minHeight = '150px';
            iframes[i].style.visibility = 'visible';
            iframes[i].style.opacity = '1';
            var el = iframes[i];
            for (var j = 0; j < 10; j++) {
                el = el.parentElement;
                if (!el) break;
                el.style.overflow = 'visible';
            }
            var r2 = iframes[i].getBoundingClientRect();
            return { cx: Math.round(r2.x + 30), cy: Math.round(r2.y + r2.height / 2) };
        }
    }
    return null;
})()
"""

# 检测 ALTCHA 是否已验证通过
_ALTCHA_SOLVED_JS = """
(function(){
    var modal = document.querySelector('div.modal.show') || document;
    // hidden input 有值
    var inputs = modal.querySelectorAll('input[type="hidden"]');
    for (var i = 0; i < inputs.length; i++) {
        var n = (inputs[i].name || '').toLowerCase();
        if ((n.includes('altcha') || n.includes('captcha')) &&
            inputs[i].value && inputs[i].value.length > 20) return true;
    }
    // checkbox 变为 disabled
    var cbs = modal.querySelectorAll('input[type="checkbox"]');
    for (var j = 0; j < cbs.length; j++) {
        if (cbs[j].disabled) return true;
    }
    // widget data-state 属性
    var w = modal.querySelector('[data-state="verified"],.altcha--verified,.altcha-verified');
    if (w) return true;
    return false;
})()
"""

#  底层输入工具
def js_fill_input(sb, selector: str, text: str):
    safe_text = text.replace('\\', '\\\\').replace('"', '\\"')
    sb.execute_script(f"""
    (function(){{
        var el = document.querySelector('{selector}');
        if (!el) return;
        var nativeInputValueSetter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, "value").set;
        if (nativeInputValueSetter) {{
            nativeInputValueSetter.call(el, "{safe_text}");
        }} else {{
            el.value = "{safe_text}";
        }}
        el.dispatchEvent(new Event('input', {{ bubbles: true }}));
        el.dispatchEvent(new Event('change', {{ bubbles: true }}));
    }})()
    """)

def _activate_window():
    for cls in ["chrome", "chromium", "Chromium", "Chrome", "google-chrome"]:
        try:
            r = subprocess.run(["xdotool", "search", "--onlyvisible", "--class", cls], capture_output=True, text=True, timeout=3)
            wids = [w for w in r.stdout.strip().split("\n") if w.strip()]
            if wids:
                subprocess.run(["xdotool", "windowactivate", "--sync", wids[0]], timeout=3, stderr=subprocess.DEVNULL)
                time.sleep(0.2)
                return
        except Exception:
            pass
    try:
        subprocess.run(["xdotool", "getactivewindow", "windowactivate"], timeout=3, stderr=subprocess.DEVNULL)
    except Exception:
        pass

# def _xdotool_click(x: int, y: int):
#     _activate_window()
#     try:
#         subprocess.run(["xdotool", "mousemove", "--sync", str(x), str(y)], timeout=3, stderr=subprocess.DEVNULL)
#         time.sleep(0.15)
#         subprocess.run(["xdotool", "click", "1"], timeout=2, stderr=subprocess.DEVNULL)
#     except Exception:
#         os.system(f"xdotool mousemove {x} {y} click 1 2>/dev/null")

#  人机验证处理（使用 SeleniumBase 内置 uc_gui_click_captcha）
def handle_turnstile(sb) -> bool:
    print("🔍 处理 Cloudflare Turnstile 验证...")
    time.sleep(2)

    # 检查是否已静默通过
    if sb.execute_script(_SOLVED_JS):
        print("✅ 已静默通过")
        return True

    # 尝试展开 Turnstile（防止被父容器 overflow:hidden 裁剪）
    for _ in range(3):
        try: sb.execute_script(_EXPAND_JS)
        except Exception: pass
        time.sleep(0.5)

    # 使用 SeleniumBase 内置 uc_gui_click_captcha 处理 Turnstile
    # 该方法自动完成：检测验证码类型 → 定位 iframe → 计算坐标 → PyAutoGUI 平滑点击
    for attempt in range(6):
        if sb.execute_script(_SOLVED_JS):
            print(f"✅ Turnstile 通过（第 {attempt} 次尝试）")
            return True

        print(f"🖱️ 第 {attempt + 1} 次调用 uc_gui_click_captcha...")
        try:
            sb.uc_gui_click_captcha()
        except Exception as e:
            print(f"⚠️ uc_gui_click_captcha 调用异常: {e}")

        # 等待验证结果（最多 8 秒）
        for _ in range(16):
            time.sleep(0.5)
            if sb.execute_script(_SOLVED_JS):
                print(f"✅ Turnstile 通过（第 {attempt + 1} 次尝试）")
                return True

        print(f"⚠️ 第 {attempt + 1} 次未通过，重试...")

    print("  ❌ Turnstile 6 次均失败")
    return False

#  账户登录
def login(sb) -> bool:
    print(f"🌐 打开登录页面: {BASE_URL}/auth/login")
    sb.uc_open_with_reconnect(BASE_URL + "/auth/login", reconnect_time=8)
    time.sleep(8)

    # 先等待 Cloudflare 验证通过（最多等 30 秒）
    print("⏳ 等待 Cloudflare 验证通过...")
    cf_passed = False
    for i in range(30):
        page_src = sb.get_page_source() or ""
        if 'input[name="email"]' in page_src.lower() or 'name="email"' in page_src.lower():
            cf_passed = True
            print(f"✅ Cloudflare 验证已通过（{i+1}s）")
            break
        time.sleep(1)
    if not cf_passed:
        print("⚠️ Cloudflare 验证可能未通过，继续尝试...")

    try:
        sb.wait_for_element('input[type="email"]', timeout=15)
    except Exception:
        # 尝试大写选择器作为后备
        try:
            sb.wait_for_element('input[type="Email"]', timeout=5)
        except Exception:
            print("❌ 页面未加载出登录表单")
            cur_url = sb.get_current_url()
            page_title = sb.get_title() or ""
            print(f"  当前 URL: {cur_url}")
            print(f"  当前标题: {page_title}")
            sb.save_screenshot("login_load_fail.png")
            return False

    print("🍪 关闭可能的 Cookie 弹窗...")
    try:
        for btn in sb.find_elements("button"):
            if "Accept" in (btn.text or ""):
                btn.click()
                time.sleep(0.5)
                break
    except Exception:
        pass

    print(f"📧 填写邮箱...")
    js_fill_input(sb, 'input[type="email"]', EMAIL)
    time.sleep(1)
    
    print("🔑 填写密码...")
    js_fill_input(sb, 'input[type="password"]', PASSWORD)
    time.sleep(3)

    # 等待 Turnstile 验证框出现（最多 10 秒）
    print("⏳ 等待 Turnstile 验证框出现...")
    ts_found = False
    for i in range(10):
        if sb.execute_script(_EXISTS_JS):
            ts_found = True
            print(f"✅ 检测到 Turnstile（{i+1}s）")
            break
        time.sleep(1)

    if ts_found:
        if not handle_turnstile(sb):
            print("❌ 登录界面的 Turnstile 验证失败")
            sb.save_screenshot("login_turnstile_fail.png")
            return False
    else:
        print("ℹ️ 未检测到 Turnstile")

    print("🖱️ 敲击回车提交表单...")
    sb.press_keys('input[name="password"]', '\n')

    print("⏳ 等待登录跳转...")
    for _ in range(12):
        time.sleep(1)
        cur_url = sb.get_current_url().split('?')[0].lower()
        page_title = sb.get_title() or ""
        if cur_url.startswith(f"{BASE_URL}/dashboard") or "Dashboard | KataBump" in page_title.lower():
            break

    cur_url = sb.get_current_url().split('?')[0].lower()
    page_title = sb.get_title() or ""
    if cur_url.startswith(f"{BASE_URL}/dashboard") or "Dashboard | KataBump" in page_title.lower():
        print(f"✅ 登录成功！(URL: {sb.get_current_url()}, Title: {page_title})")
        return True
        
    print(f"❌ 登录失败，页面未跳转到账户页。(URL: {sb.get_current_url()}, Title: {page_title})")
    sb.save_screenshot("login_failed.png")
    return False

# ===== 自动续期流程 =====

def _read_alert(sb):
    """读取页面第一个 Bootstrap alert 的文本，找不到返回空串"""
    try:
        el = sb.find_element("div.alert", timeout=4)
        return (el.text or "").strip()
    except Exception:
        return ""


_EXPIRY_DATE_PATTERNS = (
    r"[Ee]xpir\w*[^\d]{0,30}(\d{4}[-/]\d{1,2}[-/]\d{1,2})",
    r"[Ee]xpir\w*[^\d]{0,30}(\d{1,2}[-/]\d{1,2}[-/]\d{4})",
    r"到期[^\d]{0,12}(\d{4}[-/]\d{1,2}[-/]\d{1,2})",
    r"(\d{4}[-/]\d{1,2}[-/]\d{1,2})[^\d]{0,20}[Rr]enew",
)
_EXPIRY_DAYS_PATTERNS = (
    r"[Rr]enew\w*[^\d]{0,20}(\d+)\s*(?:days?|天)",
    r"(\d+)\s*(?:days?|天)[^\n]{0,20}(?:left|remaining|after|後|后)",
    r"(?:剩餘|剩余|remaining)[^\d]{0,12}(\d+)\s*(?:days?|天)",
)


def _parse_dt(text):
    """把抓到的日期串解析成 date；认不出来返回 None。"""
    if not text:
        return None
    for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%d-%m-%Y", "%d/%m/%Y",
                "%m/%d/%Y", "%d.%m.%Y"):
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    return None


def _read_expiry(sb):
    """读服务器详情页的到期信息，返回 (kind, value)，kind ∈ {"date","days"}。

    冷王 #89：续期结果只认「到期时间后移」，所以必须先有一个可比的读数。
    读不到一律返回 (None, None)，上层按 UNKNOWN 处理 —— 绝不猜成功。
    """
    try:
        text = sb.get_text("body") or ""
    except Exception as e:
        print(f"⚠️  读页面文本失败：{e}")
        return None, None
    text = " ".join(text.split())
    for pat in _EXPIRY_DATE_PATTERNS:
        m = re.search(pat, text)
        if m:
            d = _parse_dt(m.group(1))
            if d:
                return "date", d
    for pat in _EXPIRY_DAYS_PATTERNS:
        m = re.search(pat, text, re.I)
        if m:
            try:
                return "days", int(m.group(1))
            except (TypeError, ValueError):
                continue
    print("⚠️  页面上没识别出到期时间（日期和剩余天数都没有）")
    return None, None


def _expiry_moved(before, after) -> bool:
    """只有「到期日」读数能证明续期生效。

    冷王 #89 复审：`days` 抓的是「Renew(able) in N days」这种**倒计时**，
    不是到期日。续期成功后倒计时会重置回同样的天数，`after > before` 不成立 ——
    拿它当判据会天天误红。所以这里只有 kind == "date" 才有资格判成功。
    """
    if not before or not after:
        return False
    if before[0] != "date" or after[0] != "date":
        return False
    return after[1] > before[1]


CRITICAL_DAYS = 1          # 剩 <= 1 天或已过期 = 临界，不能静默跳过


def _is_critical(before) -> bool:
    """到期读数是否已经到了不能静默跳过的地步（照 1cehost 的 is_critical 语义）。"""
    if not before or before[0] != "date":
        return False
    return (before[1] - date.today()).days <= CRITICAL_DAYS


def _judge_not_yet(before):
    """页面说「还不能续期」时的结论。

    冷王 #89 复审：这里原来直接 exit 0 —— 服务器已经过期将删、页面照样说
    can't renew，于是永久绿灯。现在必须先读一次到期日再判。
    """
    if _is_critical(before):
        left = (before[1] - date.today()).days
        detail = ("已过期 %d 天" % -left) if left < 0 else ("剩 %d 天" % left)
        print(f"❌ 页面说未到窗口，但到期日 {before[1]}（{detail}）已临界 → 判失败")
        send_tg_message("❌", "续期失败（未到窗口但已临界）",
                        f"到期 {before[1]}（{detail}）")
        return "failed"
    if before and before[0] == "date":
        print(f"ℹ️  未到续期窗口，到期日 {before[1]}，尚有缓冲 → 跳过")
        send_tg_message("ℹ️", "⚠️ 未到续期时间", f"到期 {before[1]}")
        return "not_yet"
    print("❌ 页面说未到窗口，但读不到到期日 → 无法确认是否临近过期")
    send_tg_message("⚠️", "结果未知（未到窗口且读不到到期日）",
                    "无法确认是否临近过期，请人工看一眼")
    return "unknown"


def _goto_server_detail(sb):
    """进入服务器详情页。返回 (nav, not_yet_hint)。

    nav ∈ {"ok","failed"}；not_yet_hint 表示首页是否已出现「还不能续期」提示。

    冷王 #89 复审：原来一看到 can't renew 就直接 return —— **根本没进详情页、
    没读到期日**，于是「一直 can't renew」= 永久绿灯，哪怕服务器已经过期将删。
    现在照常进详情页，把这条提示当信号带下去，读了到期日再由 _judge_not_yet 判。
    """
    print("\n🖥️  正在进入服务器续期页...")
    time.sleep(5)

    not_yet_hint = False
    alert_text = _read_alert(sb)
    if alert_text and "can't renew" in alert_text.lower():
        print(f"ℹ️  首页提示还不能续期: {alert_text}")
        not_yet_hint = True

    # 多种选择器尝试查找 See 链接
    selectors = [
        'a[href*="/servers/edit?id="]',
        'td a[href*="/servers/edit"]',
        'table a[href*="/servers/edit"]',
        'table td a',
    ]

    see_link = None
    for sel in selectors:
        try:
            see_link = sb.find_element(sel, timeout=8)
            print(f"✅ 通过选择器找到链接: {sel}")
            break
        except Exception:
            continue

    # 选择器全部失败，尝试通过文本内容查找
    if see_link is None:
        print("⚠️ 选择器未命中，尝试文本匹配...")
        try:
            for a in sb.find_elements("a"):
                if (a.text or "").strip().lower() == "see":
                    see_link = a
                    print("✅ 通过文本 'See' 找到链接")
                    break
        except Exception:
            pass

    if see_link is None:
        # 打印调试信息帮助排查
        cur_url = sb.get_current_url()
        title = sb.get_title() or ""
        print(f"❌ 未找到 'See' 链接")
        print(f"当前 URL: {cur_url}")
        print(f"页面标题: {title}")
        try:
            links = sb.find_elements("a")
            print(f"     页面共 {len(links)} 个链接:")
            for a in links[:20]:
                href = a.get_attribute("href") or ""
                txt  = (a.text or "").strip()[:30]
                if href:
                    print(f"       - [{txt}] -> {href}")
        except Exception:
            pass
        sb.save_screenshot("servers_page_fail.png")
        # 冷王 #89：找不到服务器入口不是「没到窗口」，是页面/登录坏了，必须非绿。
        return "failed", not_yet_hint

    print("🖱️  点击 'See' 进入服务器详情页...")
    see_link.click()
    time.sleep(5)
    print(f"📄 当前页面: {sb.get_current_url()}")
    return "ok", not_yet_hint


def _open_renew_modal(sb) -> bool:
    """滚动到 Renew 按钮并点击，打开模态框"""
    print("\n🔄 查找 Renew 按钮...")
    try:
        renew_btn = sb.find_element('button[data-bs-target="#renew-modal"]', timeout=10)
    except Exception:
        try:
            renew_btn = sb.find_element('button.btn.btn-outline-primary', timeout=5)
        except Exception:
            print("  ❌ 未找到 Renew 按钮")
            return False

    sb.execute_script("""
        (function(){
            var btn = document.querySelector('button[data-bs-target="#renew-modal"]')
                     || document.querySelector('button.btn.btn-outline-primary');
            if (btn) btn.scrollIntoView({behavior:'smooth',block:'center'});
        })()
    """)
    time.sleep(0.8)
    renew_btn.click()
    print("🖱️ 已点击 Renew 按钮，等待确认框...")
    time.sleep(3)

    try:
        sb.find_element('div.modal.show', timeout=5)
        print("✅ Renew 模态框已弹出")
        return True
    except Exception:
        print("⚠️ 模态框未弹出")
        return False


# def _solve_altcha(sb) -> bool:
#     """处理 ALTCHA 人机验证"""
#     print("\n🔐 处理 ALTCHA 人机验证...")
#     time.sleep(2)
#
#     # 先检查是否已自动通过
#     if sb.execute_script(_ALTCHA_SOLVED_JS):
#         print("✅ ALTCHA 已自动通过")
#         return True
#
#     # 展开模态框内 iframe 并获取坐标
#     coords = None
#     try:
#         coords = sb.execute_script(_ALTCHA_EXPAND_JS)
#     except Exception:
#         pass
#
#     if coords:
#         print(f"  📍 找到模态框内 iframe 坐标: ({coords['cx']}, {coords['cy']})")
#
#     # 最多尝试 3 轮
#     for attempt in range(3):
#         if sb.execute_script(_ALTCHA_SOLVED_JS):
#             print(f"✅ ALTCHA 验证通过（第 {attempt + 1} 轮）")
#             return True
#
#         # 策略 1: xdotool 物理点击 iframe 坐标
#         if coords:
#             try:
#                 wi = sb.execute_script(_WININFO_JS)
#             except Exception:
#                 wi = {"sx": 0, "sy": 0, "oh": 800, "ih": 768}
#             bar = wi["oh"] - wi["ih"]
#             ax  = coords["cx"] + wi["sx"]
#             ay  = coords["cy"] + wi["sy"] + bar
#             print(f"🖱️  ALTCHA点击复选框  ({ax}, {ay})")
#             _xdotool_click(ax, ay)
#
#         # 策略 2: SeleniumBase 原生点击模态框内 iframe 元素
#         try:
#             iframes = sb.find_elements('div.modal.show iframe')
#             for iframe in iframes:
#                 try:
#                     iframe.click()
#                     print("🖱️  SeleniumBase 点击模态框 iframe")
#                 except Exception:
#                     pass
#         except Exception:
#             pass
#
#         # 策略 3: JS 遍历模态框内所有可点击元素
#         sb.execute_script("""
#             (function(){
#                 var modal = document.querySelector('div.modal.show');
#                 if (!modal) return;
#                 // 点击 iframe
#                 var iframes = modal.querySelectorAll('iframe');
#                 for (var i = 0; i < iframes.length; i++) {
#                     iframes[i].click();
#                     iframes[i].dispatchEvent(new MouseEvent('click', {bubbles:true}));
#                 }
#                 // 点击含 checkbox 的 label
#                 var labels = modal.querySelectorAll('label');
#                 for (var j = 0; j < labels.length; j++) {
#                     var txt = (labels[j].textContent || '').toLowerCase();
#                     if (txt.includes('robot') || txt.includes('captcha') || txt.includes('verify'))
#                         labels[j].click();
#                 }
#                 // 点击 checkbox
#                 var cbs = modal.querySelectorAll('input[type="checkbox"]');
#                 for (var k = 0; k < cbs.length; k++) {
#                     if (!cbs[k].disabled) {
#                         cbs[k].click();
#                         cbs[k].dispatchEvent(new MouseEvent('click', {bubbles:true}));
#                     }
#                 }
#             })()
#         """)
#
#         # 等待验证结果
#         for _ in range(6):
#             time.sleep(1)
#             if sb.execute_script(_ALTCHA_SOLVED_JS):
#                 print(f"✅ ALTCHA 验证通过（第 {attempt + 1} 轮）")
#                 return True
#
#         print(f"  ⚠️ 第 {attempt + 1} 轮未通过，重试...")
#         # 重新获取坐标（iframe 可能已重新渲染）
#         try:
#             new_coords = sb.execute_script(_ALTCHA_EXPAND_JS)
#             if new_coords:
#                 coords = new_coords
#         except Exception:
#             pass
#
#     print("  ❌ ALTCHA 3 轮均失败")
#     return False


def _submit_renew(sb):
    """点击模态框内的 Renew 提交按钮"""
    print("🖱️  点击模态框中的 Renew 按钮...")
    try:
        submit = sb.find_element('div.modal-footer button.btn.btn-primary', timeout=10)
        submit.click()
    except Exception:
        sb.execute_script("""
            (function(){
                var m = document.querySelector('button.btn.btn-primary');
                if (!m) return;
                var bs = m.querySelectorAll('button');
                for (var i = 0; i < bs.length; i++)
                    if (/renew/i.test(bs[i].textContent)) bs[i].click();
            })()
        """)
    time.sleep(8)


def _confirm_renew(sb, before):
    """点完 Renew 之后确认结果，返回 "ok" / "failed" / "unknown"。

    冷王 #89：页面 alert 文案只做日志，判定只认「到期时间后移」。
    原来只要文案里出现 renewed / success / extended 就发 ✅ —— 那正是假绿来源。
    读不到续期前读数就是未知，读不到续期后读数也是未知，两者都不许当成功。
    """
    print("\n📋 确认续期结果（以到期时间为准）...")
    alert_text = _read_alert(sb)
    if not alert_text:
        time.sleep(3)
        alert_text = _read_alert(sb)
    if alert_text:
        # 只记录，不参与判定
        print(f"📩 页面提示（仅日志，不作判据）: {alert_text}")
    else:
        print("ℹ️  没有 alert 提示，继续用到期时间复核")

    if not before or before[0] != "date":
        # 冷王 #89 复审：days 是「Renew in N days」倒计时，续期后本来就会重置，
        # 不能当到期日判据；读不到就更没有基准。两种都只能算未知。
        print("❌ 续期前没有可用的到期日读数（读不到，或只有倒计时）→ 结果未知")
        send_tg_message("⚠️", "结果未知（续期前读不到到期日）",
                        "倒计时不能当到期日用，请人工确认")
        return "unknown"

    after = (None, None)
    moved = False
    for i in range(1, 4):
        time.sleep(10)
        try:
            sb.refresh()
            time.sleep(3)
        except Exception as e:
            print(f"⚠️  刷新页面失败：{e}，重试进入详情页")
            try:
                _goto_server_detail(sb)
            except Exception:
                pass
        after = _read_expiry(sb)
        print(f"🔁 第 {i} 轮复核：续期前 {before[1]} → 现在 {after[1]}")
        if _expiry_moved(before, after):
            moved = True
            break

    if moved:
        print(f"✅ 到期时间已后移：{before[1]} → {after[1]}")
        send_tg_message("✅", "续期成功", f"{before[1]} → {after[1]}")
        return "ok"

    if not after or after[0] != "date":
        print("❌ 续期后读不到到期日（或只读到倒计时）→ 无法确认")
        send_tg_message("⚠️", "结果未知（续期后读不到到期日）",
                        f"续期前 {before[1]}，续期后没读到")
        return "unknown"

    print(f"❌ 到期日没有后移：{before[1]} → {after[1]}")
    send_tg_message("❌", "续期失败（到期日未变）", f"{before[1]} → {after[1]}")
    return "failed"


def renew_server(sb):
    """登录成功后调用：详情页 -> 抓到期读数 -> Renew -> 提交 -> 复核。

    返回 "ok" / "not_yet" / "failed" / "unknown"，由 main 映射成退出码。
    原来这个函数没有返回值，main 也不 sys.exit —— 于是不管续没续上 run 都是绿的。
    """
    print("\n" + "#" * 25)
    print("  开始自动续期流程")
    print("#" * 25)

    nav, not_yet_hint = _goto_server_detail(sb)
    if nav != "ok":
        return nav

    before = _read_expiry(sb)
    print(f"📅 续期前到期读数: {before[0]}={before[1]}")

    # 冷王 #89 复审：未到窗口不再直接绿灯，先看到期日是否已临界
    alert_now = _read_alert(sb) or ""
    if not_yet_hint or "can't renew" in alert_now.lower():
        return _judge_not_yet(before)

    if not _open_renew_modal(sb):
        # 打不开续期弹窗：没进过续期尝试，不能算成功
        print("❌ 打不开续期弹窗，本轮未执行续期")
        send_tg_message("❌", "续期失败（打不开续期弹窗）", "需人工检查")
        return "failed"

    # altcha_ok = _solve_altcha(sb)
    # if not altcha_ok:
    #     print("⚠️ ALTCHA 验证未通过，仍尝试提交 Renew...")

    _submit_renew(sb)
    return _confirm_renew(sb, before)


#  脚本执行入口 (可选代理)
def main():
    print("#" * 25)
    print("   katabump 自动登录续期")
    print("#" * 25)

    IS_PROXY = os.environ.get("IS_PROXY", "false").lower() == "true"
    proxy_str = os.environ.get("PROXY_SERVER", "").strip() or "http://127.0.0.1:1081"
    sb_kwargs = {"uc": True, "headless": False}

    if IS_PROXY:
        print(f"🔗 挂载代理: {proxy_str}")
        sb_kwargs["proxy"] = proxy_str
    else:
        print("🌐 未使用代理，直连访问")
    
    print("🚀 启动浏览器...")
    # 浏览器起不来 / SB 抛异常时也要有明确结论，不留 UnboundLocalError
    outcome = "failed"
    with SB(**sb_kwargs) as sb:
        # print("✅ 浏览器已启动")
        try:
            sb.open("https://api.ip.sb/ip")
            print(f"📍  当前出口IP: {sb.get_text('body')}")
        except Exception:
            pass

        if login(sb):
            outcome = renew_server(sb)   # 登录成功后自动续期
        else:
            print("\n❌ 登录失败，终止后续续期操作。")
            send_tg_message("❌", "登录失败", "未知")
            outcome = "failed"

    # 冷王 #89：只有「真续上」和「明确未到窗口」可以绿灯；
    # 读不到天数 / 鉴权失败 / 页面进不去 一律非 0。
    if outcome in ("ok", "not_yet"):
        print(f"\n🏁 结束：{outcome}")
        sys.exit(0)
    print(f"\n❌ 结束：{outcome}（exit 1）")
    sys.exit(1)

if __name__ == "__main__":
    main()
