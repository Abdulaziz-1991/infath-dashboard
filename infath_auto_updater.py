"""
محدّث آلي لبيانات لوحة متابعة مزادات إنفاذ — v5 (مبني على فحص ميداني مباشر)
================================================================================
مصادر البيانات المؤكدة بالفحص الفعلي في المتصفح:

المنصة     │ قائمة المزادات        │ بيانات المزايدات (للمنتهية)
───────────┼───────────────────────┼─────────────────────────────────────────
مباشر      │ Discovery API ✅       │ /auctions/t-details/{itemId} (Selenium)
وصلت       │ __NEXT_DATA__ SSR ✅   │ /auction-group/{id} HTML (Selenium)
السعودية   │ get_auction_filter ✅  │ Selenium → صفحة المزاد
دار        │ /edge/v1/auctions ✅   │ REST API مباشر (is_sold + current_price)
الدال      │ Selenium              │ Selenium → /auction/{id} HTML
سومتك      │ Selenium              │ Selenium → /auctions/{id}/assets HTML
"""

import json, time, re, os, logging, argparse
from datetime import datetime, timedelta, timezone
from dataclasses import dataclass
import requests

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s [%(levelname)s] %(message)s",
                    datefmt="%H:%M:%S")
log = logging.getLogger("infath")

H = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
KSA = timezone(timedelta(hours=3))

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
    id:          str
    platform:    str
    name:        str
    city:        str
    status:      str          # live | soon | ended
    assets:      int
    start:       str = ""
    end:         str = ""
    end_iso:     str = ""
    ended_at:    str = ""
    link:        str = ""
    detail_link: str = ""
    sold_assets: int = 0      # أصول بها مزايدة ≥ 1
    total_value: int = 0      # مجموع أعلى مزايدة على الأصول المباعة


def _p(v) -> int:
    if v is None: return 0
    if isinstance(v, (int, float)): return int(v)
    return int(re.sub(r"[^\d]", "", str(v)) or 0)

def _ksa_now(): return datetime.now(KSA)
def _past24(): return _ksa_now() - timedelta(hours=24)


# ══════════════════════════════════════════════════════════════
# Selenium — driver مشترك مع retry
# ══════════════════════════════════════════════════════════════
def _driver():
    from selenium import webdriver
    from selenium.webdriver.chrome.options import Options
    from selenium.webdriver.chrome.service import Service
    o = Options()
    for a in ("--headless=new","--no-sandbox","--disable-dev-shm-usage","--disable-gpu","--window-size=1280,900"):
        o.add_argument(a)
    o.add_argument(f"user-agent={H['User-Agent']}")
    cb = os.environ.get("CHROME_BIN","/usr/bin/chromium")
    db = os.environ.get("CHROMEDRIVER_PATH","/usr/bin/chromedriver")
    if os.path.exists(cb): o.binary_location = cb
    svc = Service(db) if os.path.exists(db) else __import__('webdriver_manager.chrome',fromlist=['ChromeDriverManager']).ChromeDriverManager().install()
    if isinstance(svc, str):
        from selenium.webdriver.chrome.service import Service as S
        svc = S(svc)
    return webdriver.Chrome(service=svc, options=o)


def _page_text(driver, url, wait=5):
    """زيارة URL وإعادة نص الصفحة بعد تحميل JavaScript"""
    try:
        driver.get(url)
        time.sleep(wait)
        return driver.find_element("tag name","body").text or ""
    except Exception as e:
        log.debug(f"page_text({url}): {e}")
        return ""


# ══════════════════════════════════════════════════════════════
# مباشر — قائمة: Discovery API │ مزايدات: صفحة الأصل الفردي
# ══════════════════════════════════════════════════════════════
def fetch_mobasher(driver=None):
    log.info("[مباشر] جلب عبر Discovery API...")
    base   = "https://discovery-api.prod.mobasher.sa/api/v1/discovery/auctions"
    items, cur, pg = [], None, 0
    while pg < 30:
        url = f"{base}?pageSize=100&includeTotal=true&category=REAL_ESTATES" + (f"&cursor={cur}" if cur else "")
        r = requests.get(url, headers=H, timeout=20); r.raise_for_status()
        d = r.json(); items.extend(d.get("items",[])); cur = d.get("nextCursor"); pg += 1
        if not cur: break

    now = datetime.now(timezone.utc); past24 = now - timedelta(hours=24)
    out = []
    for a in items:
        if a.get("auctionType") != "INFATH_TIMED": continue
        st = a.get("status"); ea = ""
        if   st == "LIVE":      s = "live"
        elif st == "SCHEDULED": s = "soon"
        elif st == "CLOSED":
            s = "ended"
            es = a.get("effectiveEndTimeUtc") or a.get("scheduledEndTimeUtc")
            if not es: continue
            try:
                ed = datetime.fromisoformat(es.replace("Z","+00:00"))
                if not (past24 <= ed <= now): continue
                ea = ed.astimezone(KSA).strftime("%d %b — %I:%M %p")
            except: continue
        else: continue
        did = a.get("documentId","")
        out.append(Auction(
            id=did, platform="mobasher", name=a.get("title",""),
            city=(a.get("cityNamesAr") or [""])[0], status=s,
            assets=a.get("productCount") or 0,
            start=(a.get("startTimeUtc") or "")[:10],
            end=(a.get("scheduledEndTimeUtc") or a.get("effectiveEndTimeUtc") or "")[:10],
            end_iso=a.get("scheduledEndTimeUtc") or a.get("effectiveEndTimeUtc") or "",
            ended_at=ea,
            link=f"https://mobasher.sa/auctions/t-container-details/{did}",
            detail_link=f"https://mobasher.sa/auctions/t-container-details/{did}",
        ))
    log.info(f"[مباشر] ✓ {len(out)} مزاد إنفاذ")

    # ── بيانات المزايدات للمنتهية (Selenium) ──
    if driver:
        ended = [a for a in out if a.status == "ended"]
        for auc in ended:
            try:
                _mobasher_enrich(driver, auc)
            except Exception as e:
                log.debug(f"[مباشر] بيانات مزايدات {auc.id}: {e}")
    return out


def _mobasher_enrich(driver, auc: Auction):
    """يجلب بيانات مزايدات مزاد مباشر منتهٍ من صفحات الأصول الفردية"""
    # 1) جلب روابط الأصول من صفحة المزاد
    txt = _page_text(driver, auc.detail_link, wait=5)
    item_ids = re.findall(r't-details/(\d+)', driver.page_source)
    if not item_ids:
        log.debug(f"[مباشر] لا روابط أصول لمزاد {auc.id}")
        return
    sold, total = 0, 0
    for item_id in item_ids[:auc.assets]:  # لا نتجاوز عدد الأصول المعلن
        try:
            item_txt = _page_text(driver, f"https://mobasher.sa/auctions/t-details/{item_id}", wait=4)
            # "أعلى مزايدة اونلاين  3,290,000 ر.س" أو "سعر الربح  3,290,000 ر.س"
            bid = _p(re.search(r'(?:أعلى مزايدة اونلاين|سعر الربح)\s+([\d,]+)', item_txt, re.IGNORECASE)
                     or re.search(r'([\d,]{5,})\s*ر\.?س', item_txt))
            if bid and bid > 0:
                sold += 1; total += bid
        except Exception as e:
            log.debug(f"[مباشر] item {item_id}: {e}")
    auc.sold_assets = sold; auc.total_value = total
    log.info(f"[مباشر] ✓ {auc.name[:25]}: {sold} مباع / {auc.assets} أصل | {total:,} ر.س")


# ══════════════════════════════════════════════════════════════
# وصلت — قائمة: __NEXT_DATA__ │ مزايدات: auction-group HTML
# ══════════════════════════════════════════════════════════════
def fetch_wasalt(driver=None):
    log.info("[وصلت] جلب عبر __NEXT_DATA__...")
    seen = {}
    for pg in range(1, 26):
        r = requests.get(f"https://auction.wasalt.sa/?page={pg}", headers=H, timeout=20)
        if r.status_code != 200: continue
        m = re.search(r'<script id="__NEXT_DATA__"[^>]*>([\s\S]*?)</script>', r.text)
        if not m: continue
        d = json.loads(m.group(1))
        ac = d.get("props",{}).get("pageProps",{}).get("auctionCollection",{})
        for a in ac.get("auctions",[]): seen[a["id"]] = a
        if len(seen) >= ac.get("count",0): break

    now = datetime.now(timezone.utc); past24 = now - timedelta(hours=24)
    out = []
    for a in seen.values():
        if a.get("sellerSlug") != "au-infath": continue
        name = a.get("arabicGroupName","")
        if "test" in name.lower() or "تجربة" in name: continue
        st = a.get("status"); ea = ""
        if   st == "live":     s = "live"
        elif st == "upcoming": s = "soon"
        elif st == "past":
            s = "ended"
            es = a.get("endDate")
            if not es: continue
            try:
                ed = datetime.fromisoformat(es.replace("Z","+00:00"))
                if not (past24 <= ed <= now): continue
                ea = ed.astimezone(KSA).strftime("%d %b — %I:%M %p")
            except: continue
        else: continue
        adr  = a.get("address") or ""
        city = (adr[0] if isinstance(adr,list) else adr).split(",")[0]
        aid  = str(a.get("id"))
        out.append(Auction(
            id=aid, platform="wasalt", name=name, city=city, status=s,
            assets=len(a.get("auctionItems") or []),
            start=(a.get("startDate") or "")[:10],
            end=(a.get("endDate") or "")[:10],
            end_iso=a.get("endDate") or "", ended_at=ea,
            link=f"https://auction.wasalt.sa/auction-group/{aid}",
            detail_link=f"https://auction.wasalt.sa/auction-group/{aid}",
        ))
    log.info(f"[وصلت] ✓ {len(out)} مزاد إنفاذ")

    # ── بيانات المزايدات للمنتهية (Selenium) ──
    if driver:
        for auc in [a for a in out if a.status == "ended"]:
            try:
                _wasalt_enrich(driver, auc)
            except Exception as e:
                log.debug(f"[وصلت] بيانات مزايدات {auc.id}: {e}")
    return out


def _wasalt_enrich(driver, auc: Auction):
    """يستخرج 'أعلى مزايدة' لكل أصل من صفحة auction-group في وصلت"""
    txt = _page_text(driver, auc.detail_link, wait=6)
    # "أعلى مزايدة:\n294,000"  أو  "أعلى مزايدة: 294,000"
    bids = [_p(v) for v in re.findall(r'أعلى مزايدة:\s*([\d,]+)', txt)]
    sold  = sum(1 for b in bids if b > 0)
    total = sum(b for b in bids if b > 0)
    # "لا يوجد مزايدة" لأصول بلا عروض
    no_bid = len(re.findall(r'لا يوجد مزايدة', txt))
    auc.sold_assets = sold; auc.total_value = total
    log.info(f"[وصلت] ✓ {auc.name[:25]}: {sold} مباع | {no_bid} بلا مزايدة | {total:,} ر.س")


# ══════════════════════════════════════════════════════════════
# السعودية — قائمة: get_auction_filter │ مزايدات: Selenium
# ══════════════════════════════════════════════════════════════
def fetch_saudia(driver=None):
    log.info("[السعودية] جلب عبر get_auction_filter...")
    base = "https://auctions.com.sa/api/get_auction_filter"
    all_data = []
    for pg in range(1,6):
        r = requests.get(f"{base}?pathname=%2Fauctions_filter&code=real_estate&categ_ids=4&page={pg}",
                         headers=H, timeout=20)
        d = r.json()
        if not d.get("data"): break
        all_data.extend(d["data"])
        if pg >= d.get("nb_page",1): break
    re_ = requests.get(
        f"{base}?pathname=%2Fauctions_filter&code=real_estate&categ_ids=4&state_key=end&page=1",
        headers=H, timeout=20)
    all_data.extend(re_.json().get("data",[]))

    now = _ksa_now(); past24 = _past24()
    out, seen = [], set()
    for a in all_data:
        if a["id"] in seen: continue
        seen.add(a["id"])
        if "إنفاذ" not in (a.get("charger",{}).get("name") or ""): continue
        st = a.get("auction_state",{}).get("type"); ea = ""
        if   st == "current": s = "live"
        elif st == "new":     s = "soon"
        elif st == "end":
            s = "ended"
            try:
                ed = datetime.strptime(a["end_at"],"%Y-%m-%d %H:%M:%S").replace(tzinfo=KSA)
                if not (past24 <= ed <= now): continue
                ea = ed.strftime("%d %b — %I:%M %p")
            except: continue
        else: continue
        # base_url هو oe.auctions.com.sa — نبني رابط تفاصيل المزاد منه
        bu  = (a.get("base_url") or "").rstrip("/")
        det = f"{bu}/ar/auctions/{a['id']}" if bu else ""
        out.append(Auction(
            id=str(a["id"]), platform="saudia", name=a.get("title",""),
            city=(a.get("city") or "").replace("منطقة ",""),
            status=s, assets=a.get("total_products") or 0,
            start=(a.get("start_at") or "")[:10],
            end=(a.get("end_at") or "")[:10],
            ended_at=ea,
            link="https://auctions.com.sa/auctions_filter",
            detail_link=det,
        ))
    log.info(f"[السعودية] ✓ {len(out)} مزاد إنفاذ")

    # ── بيانات المزايدات للمنتهية (Selenium) ──
    if driver:
        for auc in [a for a in out if a.status == "ended" and a.detail_link]:
            try:
                _saudia_enrich(driver, auc)
            except Exception as e:
                log.debug(f"[السعودية] بيانات مزايدات {auc.id}: {e}")
    return out


def _saudia_enrich(driver, auc: Auction):
    """يحاول استخراج بيانات مزايدات مزاد السعودية من صفحة تفاصيله"""
    txt = _page_text(driver, auc.detail_link, wait=6)
    # "أعلى سومة: 1,500,000" أو "أعلى مزايدة: X"
    bids = [_p(v) for v in re.findall(r'(?:أعلى سومة|أعلى مزايدة)\s*:?\s*([\d,]+)', txt)]
    if not bids:
        # بحث في page source عن last_bid_price
        bids = [_p(v) for v in re.findall(r'"last_bid_price"\s*:\s*(\d+)', driver.page_source)]
    sold  = sum(1 for b in bids if b > 0)
    total = sum(b for b in bids if b > 0)
    if sold > 0:
        auc.sold_assets = sold; auc.total_value = total
        log.info(f"[السعودية] ✓ {auc.name[:25]}: {sold} مباع | {total:,} ر.س")
    else:
        log.info(f"[السعودية] ℹ {auc.name[:25]}: لا بيانات مزايدات ظاهرة")


# ══════════════════════════════════════════════════════════════
# دار المزادات — REST API /edge/v1/auctions (الأفضل)
# ══════════════════════════════════════════════════════════════
def fetch_dar():
    log.info("[دار المزادات] جلب عبر /edge/v1/auctions REST API...")
    h = {**H, "Accept":"application/json", "Referer":"https://darauction.com/ar"}

    def _assets(status_p):
        out, pg = [], 1
        while pg <= 5:
            r = requests.get(f"https://darauction.com/edge/v1/auctions?status={status_p}&per_page=50&page={pg}",
                             headers=h, timeout=20)
            if not r.ok: break
            d = r.json(); items = d.get("data",[])
            if not items: break
            out.extend(items)
            tp = (d.get("pagination") or {}).get("total_pages",1)
            if pg >= tp: break
            pg += 1
        return out

    now = _ksa_now(); past24 = _past24()

    def _group(assets, s):
        events = {}
        for a in assets:
            ev = a.get("event") or {}
            eid = ev.get("id")
            if not eid: continue
            is_inf = ev.get("is_infath") or "infath" in json.dumps(ev).lower()
            if not is_inf: continue
            if eid not in events:
                events[eid] = {"name":(ev.get("name") or {}).get("ar") or f"مزاد دار {eid}",
                               "start":(ev.get("start_date") or "")[:10],
                               "end":(ev.get("end_date") or "")[:10],
                               "assets":[], "sold":0, "total":0}
            events[eid]["assets"].append(a)
            if s == "ended":
                bid = _p(a.get("current_price"))
                if (a.get("is_sold") or (a.get("bids_count") or 0) > 0) and bid > 0:
                    events[eid]["sold"]  += 1
                    events[eid]["total"] += bid
        out = []
        for eid, ev in events.items():
            ea = ""
            if s == "ended":
                end_str = ev["end"]
                if end_str:
                    for fmt in ("%d/%m/%Y %H:%M","%Y-%m-%d"):
                        try:
                            ed = datetime.strptime(end_str[:16],fmt).replace(tzinfo=KSA)
                            if not (past24 <= ed <= now): ed = None; break
                            ea = ed.strftime("%d %b — %I:%M %p"); break
                        except: pass
                    if not ea: continue
            city = ""
            if ev["assets"]:
                c = ev["assets"][0].get("city") or {}
                city = (c.get("name") or {}).get("ar") or ""
            out.append(Auction(
                id=f"DA-{eid}", platform="dar", name=ev["name"], city=city, status=s,
                assets=len(ev["assets"]), start=ev["start"], end=ev["end"],
                end_iso=ev["end"]+"T00:00:00+03:00", ended_at=ea,
                link="https://darauction.com/ar",
                sold_assets=ev["sold"], total_value=ev["total"],
            ))
        return out

    result = (_group(_assets("ongoing"),"live") +
              _group(_assets("upcoming"),"soon") +
              _group(_assets("ended"),"ended"))
    log.info(f"[دار المزادات] ✓ {len(result)} مزاد إنفاذ")
    return result


# ══════════════════════════════════════════════════════════════
# الدال — Selenium (listing + /auction/{id} HTML للمزايدات)
# ══════════════════════════════════════════════════════════════
def fetch_aldal(driver):
    log.info("[الدال] جلب عبر Selenium...")
    out = []
    now = _ksa_now(); past24 = _past24()
    By = __import__("selenium.webdriver.common.by", fromlist=["By"]).By

    # ─ جارية وقادمة ─
    for tab, s in (("running","live"),("coming","soon")):
        driver.get(f"https://app.aldalauctions.sa/?tab={tab}#auctions"); time.sleep(3)
        for c in driver.find_elements(By.CSS_SELECTOR,".cards-wrapper .card"):
            imgs = [i.get_attribute("src") or "" for i in c.find_elements(By.TAG_NAME,"img")]
            if not any("68a4ccae1afb6" in x for x in imgs): continue
            try: title = c.find_element(By.CSS_SELECTOR,"h2,h3,h4").text.strip()
            except: title = "مزاد الدال"
            m = re.search(r"الأصول\s*(\d+)", c.text)
            try: lnk = c.find_element(By.TAG_NAME,"a").get_attribute("href") or ""
            except: lnk = ""
            out.append(Auction(id=f"AL-{abs(hash(title))%10000}", platform="aldal",
                               name=title, city="", status=s,
                               assets=int(m.group(1)) if m else 0,
                               link=lnk or f"https://app.aldalauctions.sa/?tab={tab}#auctions",
                               detail_link=lnk))

    # ─ منتهية ─
    driver.get("https://app.aldalauctions.sa/?tab=ended#auctions"); time.sleep(3)
    for c in driver.find_elements(By.CSS_SELECTOR,".cards-wrapper .card"):
        imgs = [i.get_attribute("src") or "" for i in c.find_elements(By.TAG_NAME,"img")]
        if not any("68a4ccae1afb6" in x for x in imgs): continue
        try: title = c.find_element(By.CSS_SELECTOR,"h2,h3,h4").text.strip()
        except: title = "مزاد الدال المنتهي"
        text = c.text
        m_a = re.search(r"الأصول\s*(\d+)", text)
        m_d = re.search(r"(\d{4}/\d{2}/\d{2})", text)
        ea  = ""
        if m_d:
            try:
                ed = datetime.strptime(m_d.group(1),"%Y/%m/%d").replace(tzinfo=KSA)
                if not (past24 <= ed <= now): continue
                ea = ed.strftime("%d %b")
            except: pass
        try: lnk = c.find_element(By.TAG_NAME,"a").get_attribute("href") or ""
        except: lnk = ""
        # جلب "أعلى مزايدة" من صفحة تفاصيل المزاد
        sold, total = 0, 0
        if lnk:
            try:
                txt = _page_text(driver, lnk, wait=5)
                bids = [_p(v) for v in re.findall(r'أعلى مزايدة\s*[:\-]?\s*([\d,٠-٩]+)', txt)]
                sold  = sum(1 for b in bids if b > 0)
                total = sum(b for b in bids if b > 0)
                if sold == 0:
                    # بديل: wire:snapshot topBid
                    snaps = driver.find_elements(By.CSS_SELECTOR,"[wire\\:snapshot]")
                    for sn in snaps:
                        try:
                            sd = json.loads(sn.get_attribute("wire:snapshot") or "{}")
                            tb = _p((sd.get("data") or {}).get("topBid"))
                            if tb > 0: sold += 1; total += tb
                        except: pass
            except Exception as e:
                log.debug(f"[الدال] تفاصيل: {e}")
        m_id = re.search(r"/auction/(\d+)", lnk or "")
        out.append(Auction(
            id=f"AL-E-{m_id.group(1) if m_id else abs(hash(title))%10000}",
            platform="aldal", name=title, city="", status="ended",
            assets=int(m_a.group(1)) if m_a else 0,
            ended_at=ea,
            link=lnk or "https://app.aldalauctions.sa/?tab=ended#auctions",
            detail_link=lnk,
            sold_assets=sold, total_value=total,
        ))
    log.info(f"[الدال] ✓ {len(out)} مزاد إنفاذ")
    return out


# ══════════════════════════════════════════════════════════════
# سومتك — Selenium (listing + assets HTML للمزايدات)
# ══════════════════════════════════════════════════════════════
def fetch_soum(driver):
    log.info("[سومتك] جلب عبر Selenium...")
    By = __import__("selenium.webdriver.common.by", fromlist=["By"]).By
    out = []
    now = _ksa_now(); past24 = _past24()

    for sp, s in (("ongoing","live"),("upcoming","soon"),("ended","ended")):
        driver.get(f"https://soum.tech/auctions?status={sp}"); time.sleep(4)
        cards = driver.find_elements(By.TAG_NAME,"article")
        for c in cards:
            html = c.get_attribute("innerHTML") or ""
            if "نفاذ" not in html and "Infath" not in html: continue
            try: title = c.find_element(By.CSS_SELECTOR,"h2,h3").text.strip()
            except: title = "مزاد سومتك"
            m_a = re.search(r"الأصول\s*(\d+)", c.text)
            try: raw_lnk = c.find_element(By.TAG_NAME,"a").get_attribute("href") or ""
            except: raw_lnk = ""
            m_id = re.search(r"/auctions/(\d+)", raw_lnk)
            auc_id = m_id.group(1) if m_id else str(abs(hash(title))%100000)
            assets = int(m_a.group(1)) if m_a else 0
            ea = ""; sold = 0; total = 0

            if s == "ended":
                m_d = re.search(r"(\d{4}-\d{2}-\d{2})", c.text)
                if m_d:
                    try:
                        ed = datetime.strptime(m_d.group(1),"%Y-%m-%d").replace(tzinfo=KSA)
                        if not (past24 <= ed <= now): continue
                        ea = ed.strftime("%d %b")
                    except: pass
                # جلب "أعلى سومة" من صفحة الأصول
                detail_url = f"https://soum.tech/auctions/{auc_id}/assets"
                try:
                    txt = _page_text(driver, detail_url, wait=5)
                    # "أعلى سومة\n1,500,000\nر.س" أو "أعلى سومة 1,500,000"
                    bids = [_p(v) for v in re.findall(r'أعلى سومة\s*[\n\s]*([\d,]+)', txt)]
                    if not bids:
                        bids = [_p(v) for v in re.findall(r'([\d,]{5,})\s*ر\.?س', txt)]
                    sold  = sum(1 for b in bids if b > 0)
                    total = sum(b for b in bids if b > 0)
                    if sold: log.info(f"[سومتك] ✓ {title[:25]}: {sold} مباع | {total:,} ر.س")
                except Exception as e:
                    log.debug(f"[سومتك] {detail_url}: {e}")

            out.append(Auction(
                id=f"SO-{auc_id}", platform="soum", name=title,
                city="", status=s, assets=assets, ended_at=ea,
                link=raw_lnk or f"https://soum.tech/auctions?status={sp}",
                detail_link=f"https://soum.tech/auctions/{auc_id}/assets",
                sold_assets=sold, total_value=total,
            ))
    log.info(f"[سومتك] ✓ {len(out)} مزاد إنفاذ")
    return out


# ══════════════════════════════════════════════════════════════
# بناء JSON
# ══════════════════════════════════════════════════════════════
def build_payload(all_auctions):
    live  = [a for a in all_auctions if a.status=="live"]
    soon  = [a for a in all_auctions if a.status=="soon"]
    ended = [a for a in all_auctions if a.status=="ended"]
    now   = _ksa_now()
    return {
        "last_updated":       now.isoformat(),
        "last_updated_label": now.strftime("%d %b %Y — %I:%M %p"),
        "summary": {
            "total_live":          len(live),
            "total_soon":          len(soon),
            "total_ended_24h":     len(ended),
            "total_assets_active": sum(a.assets for a in live+soon),
        },
        "platforms": [{"id":k,**v} for k,v in PLATFORM_META.items()],
        "auctions":  [{"id":a.id,"platform":a.platform,"name":a.name,"city":a.city,
                       "status":a.status,"totalAssets":a.assets,
                       "start":a.start,"end":a.end,"endISO":a.end_iso,"link":a.link}
                      for a in live+soon],
        "ended":     [{"id":a.id,"platform":a.platform,"name":a.name,"city":a.city,
                       "totalAssets":a.assets,"soldAssets":a.sold_assets,
                       "totalValue":a.total_value,"endedAt":a.ended_at,"link":a.link}
                      for a in ended],
    }


# ══════════════════════════════════════════════════════════════
# الدورة الرئيسية
# ══════════════════════════════════════════════════════════════
def run_update(use_selenium=True, output_file="infath_data.json"):
    log.info("═"*60)
    log.info("بدء دورة تحديث | ٦ منصات | إنفاذ")
    log.info("═"*60)
    all_auctions = []

    if use_selenium:
        # استخدام driver واحد مشترك لجميع المنصات التي تحتاجه
        driver = _driver()
        try:
            # مباشر ووصلت والسعودية: list بدون Selenium، مزايدات مع Selenium
            for fn, uses_driver in ((fetch_mobasher, True),(fetch_wasalt, True),(fetch_saudia, True)):
                try: all_auctions.extend(fn(driver=driver))
                except Exception as e: log.error(f"✗ {fn.__name__}: {e}")
            # دار: REST API بالكامل
            try: all_auctions.extend(fetch_dar())
            except Exception as e: log.error(f"✗ fetch_dar: {e}")
            # الدال وسومتك: Selenium كاملاً
            for fn in (fetch_aldal, fetch_soum):
                try: all_auctions.extend(fn(driver))
                except Exception as e: log.error(f"✗ {fn.__name__}: {e}")
        finally:
            driver.quit()
    else:
        for fn in (fetch_mobasher, fetch_wasalt, fetch_saudia):
            try: all_auctions.extend(fn(driver=None))
            except Exception as e: log.error(f"✗ {fn.__name__}: {e}")
        try: all_auctions.extend(fetch_dar())
        except Exception as e: log.error(f"✗ fetch_dar: {e}")
        log.info("تخطي الدال وسومتك (--no-selenium)")

    payload = build_payload(all_auctions)
    tmp = output_file + ".tmp"
    with open(tmp,"w",encoding="utf-8") as f: json.dump(payload,f,ensure_ascii=False,indent=2)
    os.replace(tmp, output_file)
    s = payload["summary"]
    log.info("═"*60)
    log.info(f"✅ {s['total_live']} جارٍ | {s['total_soon']} قادم | {s['total_ended_24h']} منتهٍ | {s['total_assets_active']} أصل")
    log.info(f"   ← {os.path.abspath(output_file)}")
    log.info("═"*60)
    return payload


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--schedule",    action="store_true")
    p.add_argument("--interval",    type=int, default=20)
    p.add_argument("--no-selenium", action="store_true")
    p.add_argument("--output",      default="infath_data.json")
    args = p.parse_args()
    if args.schedule:
        import schedule as sched
        sched.every(args.interval).minutes.do(run_update, use_selenium=not args.no_selenium, output_file=args.output)
        run_update(use_selenium=not args.no_selenium, output_file=args.output)
        while True: sched.run_pending(); time.sleep(10)
    else:
        run_update(use_selenium=not args.no_selenium, output_file=args.output)

if __name__ == "__main__": main()
