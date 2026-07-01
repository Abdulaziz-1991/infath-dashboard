"""
محدّث آلي لبيانات لوحة متابعة مزادات إنفاذ — نسخة v2 (استضافة بسيطة)
=====================================================================
يسحب بيانات المزادات الجارية والقادمة والمنتهية خلال 24 ساعة من المنصات
الست المعتمدة، يفلترها لتشمل مزادات إنفاذ فقط، ثم يكتب ملف بيانات
"infath_data.json" بجانب ملف اللوحة infath_live_dashboard.html.
اللوحة تقرأ هذا الملف مباشرة عبر fetch محلي — لا حاجة لأي خادم تطبيقي،
فقط استضافة الملفين على نفس المجلد عبر أي خادم ملفات ثابتة (static).

المنصات وطريقة التحقق من تبعية المزاد لإنفاذ:
  - مباشر      : auctionType == 'INFATH_TIMED'   (API نظيف)
  - وصلت       : sellerSlug == 'au-infath'         (API نظيف، عبر __NEXT_DATA__)
  - السعودية   : charger.name يحتوي "إنفاذ"        (API نظيف)
  - الدال      : شارة infath بصرية في كرت المزاد    (DOM، Selenium)
  - سومتك      : شارة Infath بصرية على الكرت        (DOM، Selenium)
  - دار المزادات: img[alt="infath"] في كرت المزاد    (DOM، Selenium)

ملاحظة: الدال وسومتك ودار المزادات لا توفر حقل API نظيف لتحديد الجهة
المنفذة، لذلك تحتاج Selenium. مباشر ووصلت والسعودية تُسحب بـ requests
فقط (أسرع وأخف، ولا تحتاج متصفح).

الاستخدام:
  pip install requests selenium webdriver-manager schedule

  python infath_auto_updater.py                       # تشغيل مرة واحدة
  python infath_auto_updater.py --schedule             # تكرار كل 15 دقيقة (افتراضي)
  python infath_auto_updater.py --schedule --interval 30
  python infath_auto_updater.py --no-selenium          # مباشر+وصلت+السعودية فقط (أسرع)
  python infath_auto_updater.py --output /var/www/infath/infath_data.json

ثم استضف infath_live_dashboard.html و infath_data.json في نفس المجلد
على أي خادم ملفات ثابتة، مثال سريع للتجربة محلياً:
  python -m http.server 8000
  افتح: http://localhost:8000/infath_live_dashboard.html
"""

import json
import time
import argparse
import logging
import re
import os
from datetime import datetime, timedelta, timezone
from dataclasses import dataclass, asdict
import requests

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("infath-updater")

HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
KSA_TZ = timezone(timedelta(hours=3))

PLATFORM_META = {
    "wasalt":   {"name": "وصلت للمزادات",     "color": "#7c3aed"},
    "mobasher": {"name": "مباشر للمزادات",     "color": "#1d4ed8"},
    "aldal":    {"name": "الدال للمزادات",     "color": "#0891b2"},
    "dar":      {"name": "دار المزادات",       "color": "#d97706"},
    "saudia":   {"name": "السعودية للمزادات", "color": "#059669"},
    "soum":     {"name": "سومتك",              "color": "#dc2626"},
}


@dataclass
class Auction:
    id: str
    platform: str
    name: str
    city: str
    status: str          # live | soon | ended
    assets: int
    start: str = ""
    end: str = ""
    end_iso: str = ""
    ended_at: str = ""
    link: str = ""
    sold_assets: int = 0    # عدد الأصول التي عليها مزايدة واحدة على الأقل
    total_value: int = 0    # مجموع أعلى مزايدة على كل الأصول


# ──────────────────────────────────────────────
# مباشر — Discovery API (لا يحتاج متصفح)
# ──────────────────────────────────────────────
def fetch_mobasher():
    log.info("[مباشر] جلب البيانات عبر Discovery API...")
    base = "https://discovery-api.prod.mobasher.sa/api/v1/discovery/auctions"
    params = "pageSize=100&includeTotal=true&category=REAL_ESTATES"
    all_items, cursor, pages = [], None, 0
    while pages < 30:
        url = f"{base}?{params}" + (f"&cursor={cursor}" if cursor else "")
        r = requests.get(url, headers=HEADERS, timeout=20)
        r.raise_for_status()
        d = r.json()
        all_items.extend(d.get("items", []))
        cursor = d.get("nextCursor")
        pages += 1
        if not cursor:
            break

    now = datetime.now(timezone.utc)
    past24 = now - timedelta(hours=24)
    auctions = []
    for a in all_items:
        if a.get("auctionType") != "INFATH_TIMED":
            continue
        status = a.get("status")
        ended_at = ""
        if status == "LIVE":
            st = "live"
        elif status == "SCHEDULED":
            st = "soon"
        elif status == "CLOSED":
            st = "ended"
            end_str = a.get("effectiveEndTimeUtc") or a.get("scheduledEndTimeUtc")
            if not end_str:
                continue
            try:
                end_dt = datetime.fromisoformat(end_str.replace("Z", "+00:00"))
                if not (past24 <= end_dt <= now):
                    continue
                ended_at = end_dt.astimezone(KSA_TZ).strftime("%d %b — %I:%M %p")
            except Exception:
                continue
        else:
            continue
        sold_assets, total_value = 0, 0
        if st == "ended":
            sold_assets, total_value = fetch_mobasher_bids(a.get("documentId", ""))
        auctions.append(Auction(
            id=a.get("documentId", ""),
            platform="mobasher",
            name=a.get("title", ""),
            city=(a.get("cityNamesAr") or [""])[0],
            status=st,
            assets=a.get("productCount") or 0,
            start=(a.get("startTimeUtc") or "")[:10],
            end=(a.get("scheduledEndTimeUtc") or a.get("effectiveEndTimeUtc") or "")[:10],
            end_iso=a.get("scheduledEndTimeUtc") or a.get("effectiveEndTimeUtc") or "",
            ended_at=ended_at,
            link=f"https://mobasher.sa/auctions/t-container-details/{a.get('documentId','')}",
            sold_assets=sold_assets,
            total_value=total_value,
        ))
    log.info(f"[مباشر] ✓ {len(auctions)} مزاد إنفاذ (live/soon/ended خلال 24 ساعة)")
    return auctions


# ──────────────────────────────────────────────
# وصلت — صفحات SSR عبر __NEXT_DATA__ (لا يحتاج متصفح)
# ──────────────────────────────────────────────
def fetch_wasalt():
    log.info("[وصلت] جلب البيانات عبر صفحات SSR...")
    all_auctions, seen = [], {}
    for page in range(1, 26):
        r = requests.get(f"https://auction.wasalt.sa/?page={page}", headers=HEADERS, timeout=20)
        if r.status_code != 200:
            continue
        m = re.search(r'<script id="__NEXT_DATA__"[^>]*>([\s\S]*?)</script>', r.text)
        if not m:
            continue
        data = json.loads(m.group(1))
        ac = data.get("props", {}).get("pageProps", {}).get("auctionCollection", {})
        for a in ac.get("auctions", []):
            seen[a["id"]] = a
        if len(seen) >= ac.get("count", 0):
            break

    auctions = []
    now = datetime.now(timezone.utc)
    past24 = now - timedelta(hours=24)
    for a in seen.values():
        if a.get("sellerSlug") != "au-infath":
            continue
        name = a.get("arabicGroupName", "")
        if "test" in name.lower() or "تجربة" in name:
            continue
        status = a.get("status")
        ended_at = ""
        if status == "live":
            st = "live"
        elif status == "upcoming":
            st = "soon"
        elif status == "past":
            st = "ended"
            end_str = a.get("endDate")
            if not end_str:
                continue
            try:
                end_dt = datetime.fromisoformat(end_str.replace("Z", "+00:00"))
                if not (past24 <= end_dt <= now):
                    continue
                ended_at = end_dt.astimezone(KSA_TZ).strftime("%d %b — %I:%M %p")
            except Exception:
                continue
        else:
            continue
        items = a.get("auctionItems") or []
        sold_assets, total_value = 0, 0
        if st == "ended":
            sold_assets, total_value = fetch_wasalt_bids(items)
        auctions.append(Auction(
            id=str(a.get("id")),
            platform="wasalt",
            name=name,
            city=(lambda addr: (addr[0] if isinstance(addr, list) else addr or "").split(",")[0])(a.get("address") or ""),
            status=st,
            assets=len(items),
            start=(a.get("startDate") or "")[:10],
            end=(a.get("endDate") or "")[:10],
            end_iso=a.get("endDate") or "",
            ended_at=ended_at,
            link=f"https://auction.wasalt.sa/auction-group/{a.get('id')}",
            sold_assets=sold_assets,
            total_value=total_value,
        ))
    log.info(f"[وصلت] ✓ {len(auctions)} مزاد إنفاذ (live/soon/ended خلال 24 ساعة)")
    return auctions


# ──────────────────────────────────────────────
# السعودية للمزادات — API نظيف (لا يحتاج متصفح)
# ──────────────────────────────────────────────
def fetch_saudia():
    log.info("[السعودية للمزادات] جلب البيانات عبر get_auction_filter API...")
    base = "https://auctions.com.sa/api/get_auction_filter"
    all_data = []
    for page in range(1, 6):
        url = f"{base}?pathname=%2Fauctions_filter&code=real_estate&categ_ids=4&page={page}"
        r = requests.get(url, headers=HEADERS, timeout=20)
        d = r.json()
        if not d.get("data"):
            break
        all_data.extend(d["data"])
        if page >= d.get("nb_page", 1):
            break

    r_end = requests.get(
        f"{base}?pathname=%2Fauctions_filter&code=real_estate&categ_ids=4&state_key=end&page=1",
        headers=HEADERS, timeout=20,
    )
    all_data.extend(r_end.json().get("data", []))

    auctions = []
    now = datetime.now(KSA_TZ)
    past24 = now - timedelta(hours=24)
    seen_ids = set()
    for a in all_data:
        if a["id"] in seen_ids:
            continue
        seen_ids.add(a["id"])
        if "إنفاذ" not in (a.get("charger", {}).get("name") or ""):
            continue
        state = a.get("auction_state", {}).get("type")
        ended_at = ""
        if state == "current":
            st = "live"
        elif state == "new":
            st = "soon"
        elif state == "end":
            st = "ended"
            try:
                end_dt = datetime.strptime(a["end_at"], "%Y-%m-%d %H:%M:%S").replace(tzinfo=KSA_TZ)
                if not (past24 <= end_dt <= now):
                    continue
                ended_at = end_dt.strftime("%d %b — %I:%M %p")
            except Exception:
                continue
        else:
            continue
        sold_assets, total_value = 0, 0
        if st == "ended":
            sold_assets, total_value = fetch_saudia_bids(str(a["id"]))
        auctions.append(Auction(
            id=str(a["id"]),
            platform="saudia",
            name=a.get("title", ""),
            city=(a.get("city") or "").replace("منطقة ", ""),
            status=st,
            assets=a.get("total_products") or 0,
            start=(a.get("start_at") or "")[:10],
            end=(a.get("end_at") or "")[:10],
            ended_at=ended_at,
            link="https://auctions.com.sa/auctions_filter",
            sold_assets=sold_assets,
            total_value=total_value,
        ))
    log.info(f"[السعودية للمزادات] ✓ {len(auctions)} مزاد إنفاذ")
    return auctions


# ──────────────────────────────────────────────
# الدال + دار المزادات + سومتك — تحتاج Selenium (فحص DOM بصري)
# ──────────────────────────────────────────────
def get_driver():
    from selenium import webdriver
    from selenium.webdriver.chrome.options import Options
    from selenium.webdriver.chrome.service import Service

    opts = Options()
    opts.add_argument("--headless=new")
    opts.add_argument("--no-sandbox")
    opts.add_argument("--disable-dev-shm-usage")
    opts.add_argument("--disable-gpu")
    opts.add_argument(f"user-agent={HEADERS['User-Agent']}")

    # على Docker/Render نثبّت Chromium عبر apt على مسار ثابت — أسرع وأوثق من
    # تنزيل webdriver-manager في كل مرة. إذا لم يوجد، نرجع تلقائياً لـ
    # webdriver-manager (مناسب للتشغيل على جهازك الشخصي مباشرة).
    chrome_bin = os.environ.get("CHROME_BIN", "/usr/bin/chromium")
    driver_bin = os.environ.get("CHROMEDRIVER_PATH", "/usr/bin/chromedriver")

    if os.path.exists(chrome_bin):
        opts.binary_location = chrome_bin

    if os.path.exists(driver_bin):
        service = Service(driver_bin)
    else:
        from webdriver_manager.chrome import ChromeDriverManager
        service = Service(ChromeDriverManager().install())

    return webdriver.Chrome(service=service, options=opts)


def _selenium_bids_from_detail(driver, detail_url: str, By, wait_sec=4):
    """
    يزور صفحة تفاصيل مزاد ويستخرج بيانات المزايدات.
    يبحث عن حقول السعر الخضراء / "أعلى سومة" / "أعلى مزايدة".
    يُعيد (sold_count, total_value).
    """
    try:
        driver.get(detail_url)
        time.sleep(wait_sec)
        sold, total = 0, 0

        # 1) أعلى سومة — سومتك ودار يستخدمان هذا المصطلح
        bid_labels = driver.find_elements(
            By.XPATH,
            "//*[contains(text(),'أعلى سومة') or contains(text(),'أعلى مزايدة')"
            " or contains(text(),'highest bid') or contains(text(),'Highest Bid')]"
        )
        for lbl in bid_labels:
            try:
                # القيمة في نفس العنصر أو العنصر الأب أو الشقيق
                container = lbl.find_element(By.XPATH, "./..")
                raw = container.text
                bid = _parse_price(raw)
                if bid > 0:
                    sold += 1
                    total += bid
            except Exception:
                pass

        # 2) fallback: حقول الأسعار الخضراء (color inline أو class يحتوي green/bid)
        if sold == 0:
            green_els = driver.find_elements(
                By.XPATH,
                "//*[contains(@class,'bid') or contains(@class,'green')"
                " or contains(@class,'highest') or contains(@class,'current-bid')]"
            )
            seen_vals = set()
            for el in green_els:
                raw = el.text.strip()
                bid = _parse_price(raw)
                if bid > 0 and bid not in seen_vals:
                    seen_vals.add(bid)
                    sold += 1
                    total += bid

        return sold, total
    except Exception as e:
        log.debug(f"تعذّر جلب تفاصيل المزاد من {detail_url}: {e}")
        return 0, 0


def _is_within_24h(text: str) -> bool:
    """يحاول تحديد إذا كان نص التاريخ يشير لآخر 24 ساعة — يُعيد True إذا لم يتمكن من التحقق"""
    now = datetime.now(KSA_TZ)
    past24 = now - timedelta(hours=24)
    # ابحث عن أي تاريخ بصيغة YYYY-MM-DD أو DD/MM/YYYY أو DD-MM-YYYY
    for pattern in (r"(\d{4}-\d{2}-\d{2})", r"(\d{2}[/\-]\d{2}[/\-]\d{4})"):
        m = re.search(pattern, text)
        if m:
            ds = m.group(1).replace("/", "-")
            try:
                parts = ds.split("-")
                if len(parts[0]) == 4:   # YYYY-MM-DD
                    dt = datetime(int(parts[0]), int(parts[1]), int(parts[2]), tzinfo=KSA_TZ)
                else:                     # DD-MM-YYYY
                    dt = datetime(int(parts[2]), int(parts[1]), int(parts[0]), tzinfo=KSA_TZ)
                return past24 <= dt <= now
            except Exception:
                pass
    return True  # إذا ما قدرنا نتحقق، نضمّنه احتياطاً


def fetch_aldal():
    log.info("[الدال] جلب البيانات عبر Selenium (live + soon + ended + بيانات مزايدات)...")
    from selenium.webdriver.common.by import By
    driver = get_driver()
    auctions = []
    try:
        # ─── جارية وقادمة ───
        for tab, status in (("running", "live"), ("coming", "soon")):
            driver.get(f"https://app.aldalauctions.sa/?tab={tab}#auctions")
            time.sleep(3)
            cards = driver.find_elements(By.CSS_SELECTOR, ".cards-wrapper .card")
            for c in cards:
                imgs = [img.get_attribute("src") for img in c.find_elements(By.TAG_NAME, "img")]
                if not any("68a4ccae1afb6" in (s or "") for s in imgs):
                    continue
                try:
                    title = c.find_element(By.CSS_SELECTOR, "h2,h3,h4").text.strip()
                except Exception:
                    title = "مزاد الدال"
                text = c.text
                m_assets = re.search(r"الأصول\s*(\d+)", text)
                assets = int(m_assets.group(1)) if m_assets else 0
                # حاول استخراج رابط المزاد
                try:
                    link_el = c.find_element(By.TAG_NAME, "a")
                    link = link_el.get_attribute("href") or f"https://app.aldalauctions.sa/?tab={tab}#auctions"
                except Exception:
                    link = f"https://app.aldalauctions.sa/?tab={tab}#auctions"
                auctions.append(Auction(
                    id=f"AL-{abs(hash(title)) % 10000}", platform="aldal",
                    name=title, city="", status=status, assets=assets, link=link,
                ))

        # ─── منتهية (آخر 24 ساعة) ───
        for ended_tab in ("ended", "closed", "past", "finished"):
            driver.get(f"https://app.aldalauctions.sa/?tab={ended_tab}#auctions")
            time.sleep(3)
            cards = driver.find_elements(By.CSS_SELECTOR, ".cards-wrapper .card")
            if not cards:
                continue
            log.info(f"[الدال] وجد {len(cards)} كرت في tab={ended_tab}")
            now_ksa = datetime.now(KSA_TZ)
            ended_count = 0
            for c in cards:
                imgs = [img.get_attribute("src") for img in c.find_elements(By.TAG_NAME, "img")]
                if not any("68a4ccae1afb6" in (s or "") for s in imgs):
                    continue
                text = c.text
                if not _is_within_24h(text):
                    continue
                try:
                    title = c.find_element(By.CSS_SELECTOR, "h2,h3,h4").text.strip()
                except Exception:
                    title = "مزاد الدال المنتهي"
                m_assets = re.search(r"الأصول\s*(\d+)", text)
                assets = int(m_assets.group(1)) if m_assets else 0
                # وقت الانتهاء
                ended_at = ""
                m_date = re.search(r"\d{4}-\d{2}-\d{2}|\d{2}[/\-]\d{2}[/\-]\d{4}", text)
                if m_date:
                    ended_at = m_date.group(0)
                # رابط التفاصيل لجلب المزايدات
                sold_assets, total_value = 0, 0
                try:
                    link_el = c.find_element(By.TAG_NAME, "a")
                    detail_url = link_el.get_attribute("href") or ""
                    if detail_url:
                        sold_assets, total_value = _selenium_bids_from_detail(driver, detail_url, By)
                        driver.back()
                        time.sleep(2)
                except Exception:
                    detail_url = f"https://app.aldalauctions.sa/?tab={ended_tab}#auctions"
                auctions.append(Auction(
                    id=f"AL-E-{abs(hash(title)) % 10000}", platform="aldal",
                    name=title, city="", status="ended", assets=assets,
                    ended_at=ended_at, link=detail_url or f"https://app.aldalauctions.sa/?tab={ended_tab}#auctions",
                    sold_assets=sold_assets, total_value=total_value,
                ))
                ended_count += 1
            if ended_count > 0:
                log.info(f"[الدال] ✓ {ended_count} مزاد منتهٍ من tab={ended_tab}")
                break  # وجدنا الـ tab الصحيح، لا حاجة للمحاولة مع بقية الأسماء

    finally:
        driver.quit()
    log.info(f"[الدال] ✓ إجمالي {len(auctions)} مزاد إنفاذ")
    return auctions


def fetch_soum():
    log.info("[سومتك] جلب البيانات عبر Selenium (live + soon + ended + بيانات مزايدات)...")
    from selenium.webdriver.common.by import By
    driver = get_driver()
    auctions = []
    try:
        # ─── جارية وقادمة ───
        for status_param, status in (("ongoing", "live"), ("upcoming", "soon")):
            page = 1
            while True:
                driver.get(f"https://soum.tech/auctions?status={status_param}&page={page}")
                time.sleep(3)
                cards = driver.find_elements(By.TAG_NAME, "article")
                if not cards:
                    break
                for c in cards:
                    html = c.get_attribute("innerHTML")
                    if "نفاذ" not in html and "Infath" not in html:
                        continue
                    try:
                        title = c.find_element(By.CSS_SELECTOR, "h2,h3").text.strip()
                    except Exception:
                        title = "مزاد سومتك"
                    text = c.text
                    m = re.search(r"الأصول\s*(\d+)", text)
                    assets = int(m.group(1)) if m else 0
                    # استخرج معرف المزاد من الرابط لبناء رابط التفاصيل
                    try:
                        link_el = c.find_element(By.TAG_NAME, "a")
                        raw_link = link_el.get_attribute("href") or ""
                    except Exception:
                        raw_link = ""
                    m_id = re.search(r"/auctions/(\d+)", raw_link)
                    auc_id = m_id.group(1) if m_id else str(abs(hash(title)) % 100000)
                    auctions.append(Auction(
                        id=f"SO-{auc_id}", platform="soum", name=title,
                        city="", status=status, assets=assets,
                        link=raw_link or f"https://soum.tech/auctions?status={status_param}",
                    ))
                pag = driver.find_elements(By.XPATH, "//button[contains(text(),'2')]")
                if page >= 2 or not pag:
                    break
                page += 1

        # ─── منتهية (آخر 24 ساعة) ───
        driver.get("https://soum.tech/auctions?status=ended&page=1")
        time.sleep(3)
        ended_cards = driver.find_elements(By.TAG_NAME, "article")
        log.info(f"[سومتك] وجد {len(ended_cards)} كرت في صفحة المنتهية")
        for c in ended_cards:
            html = c.get_attribute("innerHTML")
            if "نفاذ" not in html and "Infath" not in html:
                continue
            text = c.text
            if not _is_within_24h(text):
                continue
            try:
                title = c.find_element(By.CSS_SELECTOR, "h2,h3").text.strip()
            except Exception:
                title = "مزاد سومتك المنتهي"
            m_assets = re.search(r"الأصول\s*(\d+)", text)
            assets = int(m_assets.group(1)) if m_assets else 0
            # وقت الانتهاء
            ended_at = ""
            m_date = re.search(r"\d{4}-\d{2}-\d{2}|\d{2}[/\-]\d{2}[/\-]\d{4}", text)
            if m_date:
                ended_at = m_date.group(0)
            # رابط التفاصيل لجلب المزايدات
            try:
                link_el = c.find_element(By.TAG_NAME, "a")
                raw_link = link_el.get_attribute("href") or ""
            except Exception:
                raw_link = ""
            m_id = re.search(r"/auctions/(\d+)", raw_link)
            auc_id = m_id.group(1) if m_id else str(abs(hash(title)) % 100000)
            sold_assets, total_value = 0, 0
            if auc_id:
                detail_url = f"https://soum.tech/auctions/{auc_id}/assets"
                sold_assets, total_value = _selenium_bids_from_detail(driver, detail_url, By)
                driver.back()
                time.sleep(2)
            auctions.append(Auction(
                id=f"SO-{auc_id}", platform="soum", name=title,
                city="", status="ended", assets=assets,
                ended_at=ended_at,
                link=raw_link or "https://soum.tech/auctions?status=ended",
                sold_assets=sold_assets, total_value=total_value,
            ))

    finally:
        driver.quit()
    log.info(f"[سومتك] ✓ إجمالي {len(auctions)} مزاد إنفاذ")
    return auctions


def fetch_dar():
    log.info("[دار المزادات] جلب البيانات عبر Selenium (live + soon + ended + بيانات مزايدات)...")
    from selenium.webdriver.common.by import By
    driver = get_driver()
    auctions = []
    try:
        driver.get("https://darauction.com/ar")
        time.sleep(3)
        # اضغط "الجميع" لعرض جميع المزادات
        try:
            driver.find_element(By.XPATH, "//*[contains(text(),'الجميع')]").click()
            time.sleep(2)
        except Exception:
            pass

        infath_imgs = driver.find_elements(
            By.CSS_SELECTOR, 'img[alt="infath"], img[alt="InfathWhite"]'
        )
        for img in infath_imgs:
            try:
                card = img.find_element(By.XPATH, "./ancestor::*[contains(text(),'رقم المزاد')][1]")
            except Exception:
                continue
            text = card.text
            if "رقم المزاد" not in text:
                continue

            # الحالة
            if "جاري" in text:
                status = "live"
            elif "قادم" in text or "قريب" in text:
                status = "soon"
            elif "منتهي" in text or "انتهى" in text or "مغلق" in text:
                status = "ended"
            else:
                status = "live"

            m_id = re.search(r"رقم المزاد\s*[\:\-]?\s*(\d+)", text)
            m_assets = re.search(r"(\d+)\s*(?:الأصول|أصول|أصل)", text)
            lines = [l for l in text.split("\n") if l.strip()]
            name = next((l for l in lines if len(l) > 5 and not re.match(r"^\d", l)
                         and "رقم المزاد" not in l and "الأصول" not in l), "مزاد دار")
            dar_id = m_id.group(1) if m_id else str(abs(hash(name)) % 10000)

            # وقت الانتهاء + تفاصيل المزايدات للمنتهية
            ended_at = ""
            sold_assets, total_value = 0, 0
            if status == "ended":
                if not _is_within_24h(text):
                    continue
                m_date = re.search(r"\d{4}-\d{2}-\d{2}|\d{2}[/\-]\d{2}[/\-]\d{4}", text)
                if m_date:
                    ended_at = m_date.group(0)
                # حاول النقر على الكرت للوصول لصفحة التفاصيل
                try:
                    link_el = card.find_element(By.TAG_NAME, "a")
                    detail_url = link_el.get_attribute("href") or ""
                    if detail_url:
                        sold_assets, total_value = _selenium_bids_from_detail(driver, detail_url, By)
                        driver.back()
                        time.sleep(3)
                        # أعد الضغط على "الجميع" بعد الرجوع
                        try:
                            driver.find_element(By.XPATH, "//*[contains(text(),'الجميع')]").click()
                            time.sleep(2)
                        except Exception:
                            pass
                except Exception:
                    pass

            # رابط
            try:
                link_el = card.find_element(By.TAG_NAME, "a")
                link = link_el.get_attribute("href") or "https://darauction.com/ar"
            except Exception:
                link = f"https://darauction.com/ar/auction/{dar_id}"

            auctions.append(Auction(
                id=f"DA-{dar_id}", platform="dar", name=name,
                city="", status=status,
                assets=int(m_assets.group(1)) if m_assets else 0,
                ended_at=ended_at, link=link,
                sold_assets=sold_assets, total_value=total_value,
            ))

    finally:
        driver.quit()
    log.info(f"[دار المزادات] ✓ إجمالي {len(auctions)} مزاد إنفاذ")
    return auctions


# ──────────────────────────────────────────────
# جلب بيانات المزايدات لكل أصل (للمزادات المنتهية فقط)
# ──────────────────────────────────────────────

def _parse_price(v):
    """تحويل أي صيغة سعر (نص أو رقم) لعدد صحيح"""
    if v is None:
        return 0
    if isinstance(v, (int, float)):
        return int(v)
    return int(re.sub(r"[^\d]", "", str(v)) or 0)


def fetch_mobasher_bids(auction_id: str):
    """يجلب أصول مزاد مباشر ويُعيد (sold_count, total_value)"""
    try:
        url = (f"https://discovery-api.prod.mobasher.sa/api/v1/discovery/auctions"
               f"/{auction_id}/items?pageSize=200")
        r = requests.get(url, headers=HEADERS, timeout=15)
        if not r.ok:
            return 0, 0
        items = r.json().get("items", [])
        sold = 0
        total = 0
        for it in items:
            bid = _parse_price(it.get("currentBidAmount") or it.get("highestBidAmount"))
            if bid > 0:
                sold += 1
                total += bid
        return sold, total
    except Exception as e:
        log.debug(f"[مباشر] تعذّر جلب أصول المزاد {auction_id}: {e}")
        return 0, 0


def fetch_wasalt_bids(items: list):
    """يحسب المزايدات من قائمة auctionItems الموجودة في __NEXT_DATA__"""
    sold = 0
    total = 0
    for it in items:
        bid = _parse_price(
            it.get("currentBid") or it.get("highestBid") or
            it.get("currentBidAmount") or it.get("highestBidAmount")
        )
        if bid > 0:
            sold += 1
            total += bid
    return sold, total


def fetch_saudia_bids(auction_id: str):
    """يجلب أصول مزاد السعودية للمزادات ويُعيد (sold_count, total_value)"""
    try:
        url = f"https://auctions.com.sa/api/get_auction_products?auction_id={auction_id}"
        r = requests.get(url, headers=HEADERS, timeout=15)
        if not r.ok:
            return 0, 0
        products = r.json().get("data", [])
        sold = 0
        total = 0
        for p in products:
            bid = _parse_price(
                p.get("current_bid") or p.get("highest_bid") or
                p.get("last_bid") or p.get("bid_price")
            )
            if bid > 0:
                sold += 1
                total += bid
        return sold, total
    except Exception as e:
        log.debug(f"[السعودية] تعذّر جلب أصول المزاد {auction_id}: {e}")
        return 0, 0


# ──────────────────────────────────────────────
# بناء JSON وكتابته (هذا ما تقرأه اللوحة عبر fetch محلي)
# ──────────────────────────────────────────────
def build_payload(all_auctions):
    live = [a for a in all_auctions if a.status == "live"]
    soon = [a for a in all_auctions if a.status == "soon"]
    ended = [a for a in all_auctions if a.status == "ended"]
    now = datetime.now(KSA_TZ)

    active_payload = [
        {
            "id": a.id, "platform": a.platform, "name": a.name, "city": a.city,
            "status": a.status, "totalAssets": a.assets, "start": a.start,
            "end": a.end, "endISO": a.end_iso, "link": a.link,
        }
        for a in live + soon
    ]
    ended_payload = [
        {
            "id": a.id, "platform": a.platform, "name": a.name, "city": a.city,
            "totalAssets": a.assets, "soldAssets": a.sold_assets,
            "totalValue": a.total_value, "endedAt": a.ended_at, "link": a.link,
        }
        for a in ended
    ]

    return {
        "last_updated": now.isoformat(),
        "last_updated_label": now.strftime("%d %b %Y — %I:%M %p"),
        "summary": {
            "total_live": len(live),
            "total_soon": len(soon),
            "total_ended_24h": len(ended),
            "total_assets_active": sum(a.assets for a in live + soon),
        },
        "platforms": [{"id": k, **v} for k, v in PLATFORM_META.items()],
        "auctions": active_payload,
        "ended": ended_payload,
    }


def run_update(use_selenium=True, output_file="infath_data.json"):
    log.info("═" * 55)
    log.info("بدء دورة تحديث بيانات مزادات إنفاذ")
    log.info("═" * 55)

    all_auctions = []
    fetchers_fast = [fetch_mobasher, fetch_wasalt, fetch_saudia]
    fetchers_selenium = [fetch_aldal, fetch_soum, fetch_dar]

    for f in fetchers_fast:
        try:
            all_auctions.extend(f())
        except Exception as e:
            log.error(f"✗ خطأ في {f.__name__}: {e}")

    if use_selenium:
        for f in fetchers_selenium:
            try:
                all_auctions.extend(f())
            except Exception as e:
                log.error(f"✗ خطأ في {f.__name__} (تحقق من تثبيت selenium وwebdriver-manager): {e}")
    else:
        log.info("تخطي منصات الدال/سومتك/دار (شغّل بدون --no-selenium لتفعيلها)")

    payload = build_payload(all_auctions)

    tmp_file = output_file + ".tmp"
    with open(tmp_file, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    os.replace(tmp_file, output_file)  # كتابة ذرية لتفادي قراءة ملف ناقص من اللوحة

    s = payload["summary"]
    log.info("═" * 55)
    log.info(f"✅ اكتمل — {s['total_live']} جارٍ | {s['total_soon']} قادم | "
              f"{s['total_ended_24h']} منتهٍ خلال 24 ساعة | {s['total_assets_active']} أصل نشط")
    log.info(f"   تم الحفظ في: {os.path.abspath(output_file)}")
    log.info("═" * 55)
    return payload


def main():
    parser = argparse.ArgumentParser(description="محدّث آلي لبيانات لوحة مزادات إنفاذ (v2 — استضافة بسيطة)")
    parser.add_argument("--schedule", action="store_true", help="تشغيل دوري")
    parser.add_argument("--interval", type=int, default=15, help="الفترة بالدقائق (افتراضي: 15)")
    parser.add_argument("--no-selenium", action="store_true",
                         help="تخطي الدال/سومتك/دار (أسرع، بدون متصفح، فقط مباشر+وصلت+السعودية)")
    parser.add_argument("--output", default="infath_data.json",
                         help="مسار ملف الإخراج — ضعه بجانب infath_live_dashboard.html")
    args = parser.parse_args()

    use_selenium = not args.no_selenium

    if args.schedule:
        import schedule as sched
        log.info(f"وضع الجدولة: كل {args.interval} دقيقة")
        sched.every(args.interval).minutes.do(run_update, use_selenium=use_selenium, output_file=args.output)
        run_update(use_selenium=use_selenium, output_file=args.output)
        while True:
            sched.run_pending()
            time.sleep(10)
    else:
        run_update(use_selenium=use_selenium, output_file=args.output)


if __name__ == "__main__":
    main()
