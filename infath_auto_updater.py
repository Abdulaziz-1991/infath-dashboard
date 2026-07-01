"""
محدّث آلي لبيانات لوحة متابعة مزادات إنفاذ — نسخة v3 (بيانات مزايدات حقيقية)
=============================================================================
يسحب بيانات المزادات من المنصات الست، يفلتر إنفاذ، ثم لكل مزاد منتهٍ
يزور صفحة تفاصيله عبر Selenium ويستخرج "أعلى سومة" من كل أصل فعلياً.
"""

import json, time, argparse, logging, re, os
from datetime import datetime, timedelta, timezone
from dataclasses import dataclass
import requests

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s [%(levelname)s] %(message)s",
                    datefmt="%H:%M:%S")
log = logging.getLogger("infath-updater")

HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
KSA_TZ  = timezone(timedelta(hours=3))

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
    detail_link: str = ""   # رابط صفحة التفاصيل لجلب المزايدات
    sold_assets: int = 0
    total_value: int = 0


# ──────────────────────────────────────────────
# مشترك: تحليل السعر
# ──────────────────────────────────────────────
def _parse_price(v) -> int:
    if v is None: return 0
    if isinstance(v, (int, float)): return int(v)
    cleaned = re.sub(r"[^\d]", "", str(v))
    return int(cleaned) if cleaned else 0


# ──────────────────────────────────────────────
# Selenium — Driver مشترك
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
    chrome_bin   = os.environ.get("CHROME_BIN", "/usr/bin/chromium")
    driver_bin   = os.environ.get("CHROMEDRIVER_PATH", "/usr/bin/chromedriver")
    if os.path.exists(chrome_bin):
        opts.binary_location = chrome_bin
    if os.path.exists(driver_bin):
        service = Service(driver_bin)
    else:
        from webdriver_manager.chrome import ChromeDriverManager
        service = Service(ChromeDriverManager().install())
    return webdriver.Chrome(service=service, options=opts)


# ──────────────────────────────────────────────
# استخراج بيانات المزايدات من صفحة التفاصيل
# ──────────────────────────────────────────────
def _extract_bids_from_page(driver, url: str, By, wait=5) -> tuple:
    """
    يزور صفحة تفاصيل مزاد ويستخرج مجموع أعلى سومة على كل أصل.
    يعمل مع جميع المنصات الست لأنها جميعاً تعرض "أعلى سومة" بنفس الأسلوب.
    يُعيد (sold_count, total_value).
    """
    try:
        driver.get(url)
        time.sleep(wait)
        html   = driver.page_source
        body   = driver.find_element(By.TAG_NAME, "body").text
        lines  = body.split("\n")

        bids = []

        # ── أسلوب 1: ابحث عن "أعلى سومة" / "أعلى مزايدة" في نص الصفحة
        trigger_words = ("أعلى سومة", "أعلى مزايدة", "highest bid", "current bid",
                         "المزايدة الحالية", "آخر سومة")
        for i, line in enumerate(lines):
            if any(w in line.lower() for w in trigger_words):
                # ابحث في السطور المجاورة عن رقم منطقي
                search_range = lines[max(0,i-3): i+4]
                for sl in search_range:
                    amt = _parse_price(sl)
                    if amt >= 100:          # 100 ر.س كحد أدنى منطقي
                        bids.append(amt)
                        break

        if bids:
            log.info(f"  ✓ أسلوب-1: {len(bids)} مزايدة, إجمالي {sum(bids):,}")
            return len(bids), sum(bids)

        # ── أسلوب 2: regex على HTML خام
        for pat in (
            r'أعلى\s*سومة[^<\d]{0,60}?([\d,٠-٩]+)',
            r'أعلى\s*مزايدة[^<\d]{0,60}?([\d,٠-٩]+)',
            r'"currentBid[^"]*"\s*:\s*"?([\d]+)',
            r'"highestBid[^"]*"\s*:\s*"?([\d]+)',
            r'"current_bid"\s*:\s*"?([\d]+)',
            r'"highest_bid"\s*:\s*"?([\d]+)',
            r'"bidAmount"\s*:\s*"?([\d]+)',
        ):
            found = re.findall(pat, html, re.IGNORECASE)
            vals  = [_parse_price(v) for v in found if _parse_price(v) >= 100]
            if vals:
                log.info(f"  ✓ أسلوب-2 ({pat[:30]}): {len(vals)} مزايدة, إجمالي {sum(vals):,}")
                return len(vals), sum(vals)

        # ── أسلوب 3: عناصر خضراء أو بكلاس bid
        try:
            bid_els = driver.find_elements(
                By.XPATH,
                "//*[contains(@class,'bid') or contains(@class,'green') "
                "or contains(@class,'highest') or contains(@class,'current-bid') "
                "or contains(@class,'سومة')]"
            )
            seen, total = set(), 0
            for el in bid_els:
                amt = _parse_price(el.text)
                if amt >= 100 and amt not in seen:
                    seen.add(amt)
                    total += amt
            if seen:
                log.info(f"  ✓ أسلوب-3 (CSS class): {len(seen)} مزايدة, إجمالي {total:,}")
                return len(seen), total
        except Exception:
            pass

    except Exception as e:
        log.warning(f"  خطأ في جلب تفاصيل {url}: {e}")

    log.info(f"  لم تُعثر على مزايدات في {url}")
    return 0, 0


# ──────────────────────────────────────────────
# رابط التفاصيل لكل منصة
# ──────────────────────────────────────────────
def _detail_url(a: Auction) -> str:
    """يبني رابط صفحة تفاصيل المزاد لكل منصة"""
    if a.detail_link:
        return a.detail_link
    pid = a.id
    if a.platform == "mobasher":
        raw = pid.replace("MO-","")
        return f"https://mobasher.sa/auctions/t-container-details/{raw}"
    if a.platform == "wasalt":
        raw = pid.replace("WA-","")
        return f"https://auction.wasalt.sa/auction-group/{raw}"
    if a.platform == "saudia":
        raw = pid.replace("SA-","")
        return f"https://auctions.com.sa/auction/{raw}"
    if a.platform == "soum":
        raw = re.sub(r"SO-","", pid)
        return f"https://soum.tech/auctions/{raw}/assets"
    if a.platform == "aldal":
        return f"https://app.aldalauctions.sa/?tab=ended#auctions"
    if a.platform == "dar":
        raw = pid.replace("DA-","")
        return f"https://darauction.com/ar/auction/{raw}"
    return a.link


# ──────────────────────────────────────────────
# إثراء المنتهية ببيانات المزايدات الحقيقية (Selenium)
# ──────────────────────────────────────────────
def enrich_ended_with_bids(ended: list) -> list:
    """
    يزور صفحة تفاصيل كل مزاد منتهٍ عبر Selenium driver واحد مشترك
    ويستخرج بيانات "أعلى سومة" الحقيقية لكل أصل.
    يعمل على جميع المنصات الست.
    """
    if not ended:
        return ended
    log.info(f"[إثراء] جلب بيانات مزايدات {len(ended)} مزاد منتهٍ عبر Selenium...")
    from selenium.webdriver.common.by import By
    driver = get_driver()
    try:
        for a in ended:
            if a.sold_assets > 0 or a.total_value > 0:
                continue          # لديها بيانات بالفعل، تخطّ
            url = _detail_url(a)
            log.info(f"  [{a.platform}] {a.name[:30]} → {url}")
            sold, total = _extract_bids_from_page(driver, url, By, wait=5)
            a.sold_assets = sold
            a.total_value = total
    finally:
        driver.quit()
    enriched = sum(1 for a in ended if a.total_value > 0)
    log.info(f"[إثراء] ✓ تم إثراء {enriched} من {len(ended)} مزاد بالبيانات الحقيقية")
    return ended


# ──────────────────────────────────────────────
# مباشر — Discovery API
# ──────────────────────────────────────────────
def fetch_mobasher():
    log.info("[مباشر] جلب البيانات عبر Discovery API...")
    base   = "https://discovery-api.prod.mobasher.sa/api/v1/discovery/auctions"
    params = "pageSize=100&includeTotal=true&category=REAL_ESTATES"
    all_items, cursor, pages = [], None, 0
    while pages < 30:
        url = f"{base}?{params}" + (f"&cursor={cursor}" if cursor else "")
        r   = requests.get(url, headers=HEADERS, timeout=20)
        r.raise_for_status()
        d = r.json()
        all_items.extend(d.get("items", []))
        cursor = d.get("nextCursor")
        pages += 1
        if not cursor:
            break

    now    = datetime.now(timezone.utc)
    past24 = now - timedelta(hours=24)
    auctions = []
    for a in all_items:
        if a.get("auctionType") != "INFATH_TIMED":
            continue
        status = a.get("status")
        ended_at = ""
        if   status == "LIVE":      st = "live"
        elif status == "SCHEDULED": st = "soon"
        elif status == "CLOSED":
            st      = "ended"
            end_str = a.get("effectiveEndTimeUtc") or a.get("scheduledEndTimeUtc")
            if not end_str: continue
            try:
                end_dt = datetime.fromisoformat(end_str.replace("Z", "+00:00"))
                if not (past24 <= end_dt <= now): continue
                ended_at = end_dt.astimezone(KSA_TZ).strftime("%d %b — %I:%M %p")
            except Exception: continue
        else:
            continue
        doc_id = a.get("documentId", "")
        auctions.append(Auction(
            id=doc_id, platform="mobasher",
            name=a.get("title", ""),
            city=(a.get("cityNamesAr") or [""])[0],
            status=st, assets=a.get("productCount") or 0,
            start=(a.get("startTimeUtc") or "")[:10],
            end=(a.get("scheduledEndTimeUtc") or a.get("effectiveEndTimeUtc") or "")[:10],
            end_iso=a.get("scheduledEndTimeUtc") or a.get("effectiveEndTimeUtc") or "",
            ended_at=ended_at,
            link=f"https://mobasher.sa/auctions/t-container-details/{doc_id}",
            detail_link=f"https://mobasher.sa/auctions/t-container-details/{doc_id}",
        ))
    log.info(f"[مباشر] ✓ {len(auctions)} مزاد إنفاذ")
    return auctions


# ──────────────────────────────────────────────
# وصلت — SSR __NEXT_DATA__
# ──────────────────────────────────────────────
def fetch_wasalt():
    log.info("[وصلت] جلب البيانات عبر صفحات SSR...")
    all_auctions, seen = [], {}
    for page in range(1, 26):
        r = requests.get(f"https://auction.wasalt.sa/?page={page}",
                         headers=HEADERS, timeout=20)
        if r.status_code != 200: continue
        m = re.search(r'<script id="__NEXT_DATA__"[^>]*>([\s\S]*?)</script>', r.text)
        if not m: continue
        data = json.loads(m.group(1))
        ac   = data.get("props", {}).get("pageProps", {}).get("auctionCollection", {})
        for a in ac.get("auctions", []):
            seen[a["id"]] = a
        if len(seen) >= ac.get("count", 0):
            break

    now    = datetime.now(timezone.utc)
    past24 = now - timedelta(hours=24)
    auctions = []
    for a in seen.values():
        if a.get("sellerSlug") != "au-infath": continue
        name = a.get("arabicGroupName", "")
        if "test" in name.lower() or "تجربة" in name: continue
        status = a.get("status")
        ended_at = ""
        if   status == "live":     st = "live"
        elif status == "upcoming": st = "soon"
        elif status == "past":
            st      = "ended"
            end_str = a.get("endDate")
            if not end_str: continue
            try:
                end_dt = datetime.fromisoformat(end_str.replace("Z", "+00:00"))
                if not (past24 <= end_dt <= now): continue
                ended_at = end_dt.astimezone(KSA_TZ).strftime("%d %b — %I:%M %p")
            except Exception: continue
        else:
            continue
        auc_id = str(a.get("id"))
        addr   = a.get("address") or ""
        city   = (addr[0] if isinstance(addr, list) else addr).split(",")[0]
        auctions.append(Auction(
            id=auc_id, platform="wasalt", name=name, city=city,
            status=st, assets=len(a.get("auctionItems") or []),
            start=(a.get("startDate") or "")[:10],
            end=(a.get("endDate") or "")[:10],
            end_iso=a.get("endDate") or "",
            ended_at=ended_at,
            link=f"https://auction.wasalt.sa/auction-group/{auc_id}",
            detail_link=f"https://auction.wasalt.sa/auction-group/{auc_id}",
        ))
    log.info(f"[وصلت] ✓ {len(auctions)} مزاد إنفاذ")
    return auctions


# ──────────────────────────────────────────────
# السعودية للمزادات — API
# ──────────────────────────────────────────────
def fetch_saudia():
    log.info("[السعودية للمزادات] جلب البيانات...")
    base     = "https://auctions.com.sa/api/get_auction_filter"
    all_data = []
    for page in range(1, 6):
        url = f"{base}?pathname=%2Fauctions_filter&code=real_estate&categ_ids=4&page={page}"
        r   = requests.get(url, headers=HEADERS, timeout=20)
        d   = r.json()
        if not d.get("data"): break
        all_data.extend(d["data"])
        if page >= d.get("nb_page", 1): break
    r_end = requests.get(
        f"{base}?pathname=%2Fauctions_filter&code=real_estate&categ_ids=4&state_key=end&page=1",
        headers=HEADERS, timeout=20)
    all_data.extend(r_end.json().get("data", []))

    now    = datetime.now(KSA_TZ)
    past24 = now - timedelta(hours=24)
    auctions, seen_ids = [], set()
    for a in all_data:
        if a["id"] in seen_ids: continue
        seen_ids.add(a["id"])
        if "إنفاذ" not in (a.get("charger", {}).get("name") or ""): continue
        state = a.get("auction_state", {}).get("type")
        ended_at = ""
        if   state == "current": st = "live"
        elif state == "new":     st = "soon"
        elif state == "end":
            st = "ended"
            try:
                end_dt = datetime.strptime(a["end_at"], "%Y-%m-%d %H:%M:%S").replace(tzinfo=KSA_TZ)
                if not (past24 <= end_dt <= now): continue
                ended_at = end_dt.strftime("%d %b — %I:%M %p")
            except Exception: continue
        else:
            continue
        base_url    = (a.get("base_url") or "").strip("/")
        detail_link = (f"https://auctions.com.sa/{base_url}"
                       if base_url and not base_url.startswith("http")
                       else base_url or f"https://auctions.com.sa/auction/{a['id']}")
        auctions.append(Auction(
            id=str(a["id"]), platform="saudia",
            name=a.get("title", ""),
            city=(a.get("city") or "").replace("منطقة ", ""),
            status=st, assets=a.get("total_products") or 0,
            start=(a.get("start_at") or "")[:10],
            end=(a.get("end_at") or "")[:10],
            ended_at=ended_at,
            link="https://auctions.com.sa/auctions_filter",
            detail_link=detail_link,
        ))
    log.info(f"[السعودية للمزادات] ✓ {len(auctions)} مزاد إنفاذ")
    return auctions


# ──────────────────────────────────────────────
# الدال — Selenium
# ──────────────────────────────────────────────
def fetch_aldal():
    log.info("[الدال] جلب البيانات عبر Selenium...")
    from selenium.webdriver.common.by import By
    driver   = get_driver()
    auctions = []
    try:
        # ─ جارية وقادمة ─
        for tab, status in (("running", "live"), ("coming", "soon")):
            driver.get(f"https://app.aldalauctions.sa/?tab={tab}#auctions")
            time.sleep(3)
            cards = driver.find_elements(By.CSS_SELECTOR, ".cards-wrapper .card")
            for c in cards:
                imgs = [img.get_attribute("src") for img in c.find_elements(By.TAG_NAME, "img")]
                if not any("68a4ccae1afb6" in (s or "") for s in imgs): continue
                try:   title = c.find_element(By.CSS_SELECTOR, "h2,h3,h4").text.strip()
                except: title = "مزاد الدال"
                m = re.search(r"الأصول\s*(\d+)", c.text)
                try:   lnk = c.find_element(By.TAG_NAME,"a").get_attribute("href") or ""
                except: lnk = ""
                auctions.append(Auction(
                    id=f"AL-{abs(hash(title))%10000}", platform="aldal",
                    name=title, city="", status=status,
                    assets=int(m.group(1)) if m else 0,
                    link=lnk or f"https://app.aldalauctions.sa/?tab={tab}#auctions",
                    detail_link=lnk,
                ))
        # ─ منتهية ─
        for ended_tab in ("ended", "closed", "past"):
            driver.get(f"https://app.aldalauctions.sa/?tab={ended_tab}#auctions")
            time.sleep(3)
            cards = driver.find_elements(By.CSS_SELECTOR, ".cards-wrapper .card")
            if not cards: continue
            for c in cards:
                imgs = [img.get_attribute("src") for img in c.find_elements(By.TAG_NAME, "img")]
                if not any("68a4ccae1afb6" in (s or "") for s in imgs): continue
                try:   title = c.find_element(By.CSS_SELECTOR,"h2,h3,h4").text.strip()
                except: title = "مزاد الدال المنتهي"
                m = re.search(r"الأصول\s*(\d+)", c.text)
                try:   lnk = c.find_element(By.TAG_NAME,"a").get_attribute("href") or ""
                except: lnk = ""
                auctions.append(Auction(
                    id=f"AL-E-{abs(hash(title))%10000}", platform="aldal",
                    name=title, city="", status="ended",
                    assets=int(m.group(1)) if m else 0,
                    link=lnk or f"https://app.aldalauctions.sa/?tab={ended_tab}#auctions",
                    detail_link=lnk,
                ))
            if any(a.status == "ended" for a in auctions): break
    finally:
        driver.quit()
    log.info(f"[الدال] ✓ {len(auctions)} مزاد إنفاذ")
    return auctions


# ──────────────────────────────────────────────
# سومتك — Selenium
# ──────────────────────────────────────────────
def fetch_soum():
    log.info("[سومتك] جلب البيانات عبر Selenium...")
    from selenium.webdriver.common.by import By
    driver   = get_driver()
    auctions = []
    try:
        for status_param, status in (("ongoing","live"), ("upcoming","soon"), ("ended","ended")):
            driver.get(f"https://soum.tech/auctions?status={status_param}&page=1")
            time.sleep(3)
            cards = driver.find_elements(By.TAG_NAME, "article")
            for c in cards:
                html = c.get_attribute("innerHTML")
                if "نفاذ" not in html and "Infath" not in html: continue
                try:   title = c.find_element(By.CSS_SELECTOR,"h2,h3").text.strip()
                except: title = "مزاد سومتك"
                m = re.search(r"الأصول\s*(\d+)", c.text)
                try:
                    raw_link = c.find_element(By.TAG_NAME,"a").get_attribute("href") or ""
                except: raw_link = ""
                m_id = re.search(r"/auctions/(\d+)", raw_link)
                auc_id = m_id.group(1) if m_id else str(abs(hash(title))%100000)
                detail = f"https://soum.tech/auctions/{auc_id}/assets"
                auctions.append(Auction(
                    id=f"SO-{auc_id}", platform="soum",
                    name=title, city="", status=status,
                    assets=int(m.group(1)) if m else 0,
                    link=raw_link or f"https://soum.tech/auctions?status={status_param}",
                    detail_link=detail,
                ))
    finally:
        driver.quit()
    log.info(f"[سومتك] ✓ {len(auctions)} مزاد إنفاذ")
    return auctions


# ──────────────────────────────────────────────
# دار المزادات — Selenium
# ──────────────────────────────────────────────
def fetch_dar():
    log.info("[دار المزادات] جلب البيانات عبر Selenium...")
    from selenium.webdriver.common.by import By
    driver   = get_driver()
    auctions = []
    try:
        driver.get("https://darauction.com/ar")
        time.sleep(3)
        try:
            driver.find_element(By.XPATH, "//*[contains(text(),'الجميع')]").click()
            time.sleep(2)
        except Exception: pass
        infath_imgs = driver.find_elements(By.CSS_SELECTOR,
                                            'img[alt="infath"], img[alt="InfathWhite"]')
        for img in infath_imgs:
            try:
                card = img.find_element(By.XPATH,
                    "./ancestor::*[contains(text(),'رقم المزاد')][1]")
            except Exception: continue
            text = card.text
            if "رقم المزاد" not in text: continue
            if   "جاري"  in text: status = "live"
            elif "قادم"  in text or "قريب" in text: status = "soon"
            elif "منتهي" in text or "انتهى" in text: status = "ended"
            else: status = "live"
            m_id     = re.search(r"رقم المزاد\s*[\:\-]?\s*(\d+)", text)
            m_assets = re.search(r"(\d+)\s*(?:الأصول|أصول|أصل)", text)
            lines    = [l for l in text.split("\n") if l.strip()]
            name     = next((l for l in lines if len(l)>5 and not re.match(r"^\d",l)
                             and "رقم المزاد" not in l and "الأصول" not in l), "مزاد دار")
            dar_id   = m_id.group(1) if m_id else str(abs(hash(name))%10000)
            try:
                lnk = card.find_element(By.TAG_NAME,"a").get_attribute("href") or ""
            except: lnk = f"https://darauction.com/ar/auction/{dar_id}"
            auctions.append(Auction(
                id=f"DA-{dar_id}", platform="dar",
                name=name, city="", status=status,
                assets=int(m_assets.group(1)) if m_assets else 0,
                link=lnk, detail_link=lnk,
            ))
    finally:
        driver.quit()
    log.info(f"[دار المزادات] ✓ {len(auctions)} مزاد إنفاذ")
    return auctions


# ──────────────────────────────────────────────
# بناء JSON
# ──────────────────────────────────────────────
def build_payload(all_auctions):
    live  = [a for a in all_auctions if a.status == "live"]
    soon  = [a for a in all_auctions if a.status == "soon"]
    ended = [a for a in all_auctions if a.status == "ended"]
    now   = datetime.now(KSA_TZ)

    active_payload = [{
        "id": a.id, "platform": a.platform, "name": a.name, "city": a.city,
        "status": a.status, "totalAssets": a.assets,
        "start": a.start, "end": a.end, "endISO": a.end_iso, "link": a.link,
    } for a in live + soon]

    ended_payload = [{
        "id": a.id, "platform": a.platform, "name": a.name, "city": a.city,
        "totalAssets": a.assets, "soldAssets": a.sold_assets,
        "totalValue": a.total_value, "endedAt": a.ended_at, "link": a.link,
    } for a in ended]

    return {
        "last_updated":       now.isoformat(),
        "last_updated_label": now.strftime("%d %b %Y — %I:%M %p"),
        "summary": {
            "total_live":         len(live),
            "total_soon":         len(soon),
            "total_ended_24h":    len(ended),
            "total_assets_active": sum(a.assets for a in live + soon),
        },
        "platforms":  [{"id": k, **v} for k, v in PLATFORM_META.items()],
        "auctions":   active_payload,
        "ended":      ended_payload,
    }


# ──────────────────────────────────────────────
# الدورة الرئيسية
# ──────────────────────────────────────────────
def run_update(use_selenium=True, output_file="infath_data.json"):
    log.info("═"*55)
    log.info("بدء دورة تحديث بيانات مزادات إنفاذ")
    log.info("═"*55)

    all_auctions = []

    # ─ المنصات السريعة (API) ─
    for fn in (fetch_mobasher, fetch_wasalt, fetch_saudia):
        try:
            all_auctions.extend(fn())
        except Exception as e:
            log.error(f"✗ {fn.__name__}: {e}")

    # ─ المنصات التي تحتاج Selenium ─
    if use_selenium:
        for fn in (fetch_aldal, fetch_soum, fetch_dar):
            try:
                all_auctions.extend(fn())
            except Exception as e:
                log.error(f"✗ {fn.__name__}: {e}")

        # ─ إثراء المنتهية ببيانات المزايدات الحقيقية ─
        ended = [a for a in all_auctions if a.status == "ended"]
        if ended:
            try:
                enriched = enrich_ended_with_bids(ended)
                # استبدل المنتهية في القائمة الكلية بالنسخة المُثرَاة
                ended_ids = {a.id for a in ended}
                all_auctions = [a for a in all_auctions if a.id not in ended_ids] + enriched
            except Exception as e:
                log.error(f"✗ enrich_ended_with_bids: {e}")
    else:
        log.info("تخطي Selenium (--no-selenium)")

    payload = build_payload(all_auctions)
    tmp     = output_file + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    os.replace(tmp, output_file)

    s = payload["summary"]
    log.info("═"*55)
    log.info(f"✅ {s['total_live']} جارٍ | {s['total_soon']} قادم | "
             f"{s['total_ended_24h']} منتهٍ | {s['total_assets_active']} أصل نشط")
    log.info(f"   حُفظ في: {os.path.abspath(output_file)}")
    log.info("═"*55)
    return payload


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--schedule",    action="store_true")
    p.add_argument("--interval",    type=int, default=20)
    p.add_argument("--no-selenium", action="store_true")
    p.add_argument("--output",      default="infath_data.json")
    args = p.parse_args()
    use_sel = not args.no_selenium
    if args.schedule:
        import schedule as sched
        sched.every(args.interval).minutes.do(
            run_update, use_selenium=use_sel, output_file=args.output)
        run_update(use_selenium=use_sel, output_file=args.output)
        while True:
            sched.run_pending()
            time.sleep(10)
    else:
        run_update(use_selenium=use_sel, output_file=args.output)

if __name__ == "__main__":
    main()
