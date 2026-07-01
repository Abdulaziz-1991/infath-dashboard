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
        auctions.append(Auction(
            id=str(a.get("id")),
            platform="wasalt",
            name=name,
            city=(a.get("address") or "").split(",")[0],
            status=st,
            assets=len(a.get("auctionItems") or []),
            start=(a.get("startDate") or "")[:10],
            end=(a.get("endDate") or "")[:10],
            end_iso=a.get("endDate") or "",
            ended_at=ended_at,
            link=f"https://auction.wasalt.sa/auction-group/{a.get('id')}",
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


def fetch_aldal():
    log.info("[الدال] جلب البيانات عبر Selenium (فحص شارة Infath)...")
    from selenium.webdriver.common.by import By
    driver = get_driver()
    auctions = []
    try:
        for tab, status in (("running", "live"), ("coming", "soon")):
            driver.get(f"https://app.aldalauctions.sa/?tab={tab}#auctions")
            time.sleep(3)
            cards = driver.find_elements(By.CSS_SELECTOR, ".cards-wrapper .card")
            for c in cards:
                imgs = [img.get_attribute("src") for img in c.find_elements(By.TAG_NAME, "img")]
                is_infath = any("68a4ccae1afb6" in (s or "") for s in imgs)
                if not is_infath:
                    continue
                try:
                    title = c.find_element(By.CSS_SELECTOR, "h2,h3,h4").text.strip()
                except Exception:
                    title = "مزاد الدال"
                text = c.text
                m = re.search(r"الأصول\s*(\d+)", text)
                assets = int(m.group(1)) if m else 0
                auctions.append(Auction(
                    id=f"AL-{abs(hash(title)) % 10000}", platform="aldal", name=title,
                    city="", status=status, assets=assets,
                    link=f"https://app.aldalauctions.sa/?tab={tab}#auctions",
                ))
    finally:
        driver.quit()
    log.info(f"[الدال] ✓ {len(auctions)} مزاد إنفاذ")
    return auctions


def fetch_soum():
    log.info("[سومتك] جلب البيانات عبر Selenium (فحص شارة Infath)...")
    from selenium.webdriver.common.by import By
    driver = get_driver()
    auctions = []
    try:
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
                    auctions.append(Auction(
                        id=f"SO-{abs(hash(title)) % 10000}", platform="soum", name=title,
                        city="", status=status, assets=assets,
                        link=f"https://soum.tech/auctions?status={status_param}",
                    ))
                pag = driver.find_elements(By.XPATH, "//button[contains(text(),'2')]")
                if page >= 2 or not pag:
                    break
                page += 1
    finally:
        driver.quit()
    log.info(f"[سومتك] ✓ {len(auctions)} مزاد إنفاذ")
    return auctions


def fetch_dar():
    log.info("[دار المزادات] جلب البيانات عبر Selenium (فحص img[alt=infath])...")
    from selenium.webdriver.common.by import By
    driver = get_driver()
    auctions = []
    try:
        driver.get("https://darauction.com/ar")
        time.sleep(3)
        driver.find_element(By.XPATH, "//*[contains(text(),'الجميع')]").click()
        time.sleep(2)
        infath_imgs = driver.find_elements(By.CSS_SELECTOR, 'img[alt="infath"], img[alt="InfathWhite"]')
        for img in infath_imgs:
            try:
                card = img.find_element(By.XPATH, "./ancestor::*[contains(text(),'رقم المزاد')][1]")
            except Exception:
                continue
            text = card.text
            if "رقم المزاد" not in text:
                continue
            status = "live" if "جاري" in text else ("soon" if "قادم" in text else "ended")
            m_id = re.search(r"رقم المزاد\s*(\d+)", text)
            m_assets = re.search(r"(\d+)\s*الأصول", text)
            lines = [l for l in text.split("\n") if l.strip()]
            name = lines[2] if len(lines) > 2 else "مزاد دار"
            auctions.append(Auction(
                id=f"DA-{m_id.group(1) if m_id else '0'}", platform="dar", name=name,
                city="", status=status,
                assets=int(m_assets.group(1)) if m_assets else 0,
                link="https://darauction.com/ar",
            ))
    finally:
        driver.quit()
    log.info(f"[دار المزادات] ✓ {len(auctions)} مزاد إنفاذ")
    return auctions


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
            "totalAssets": a.assets, "endedAt": a.ended_at, "link": a.link,
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
