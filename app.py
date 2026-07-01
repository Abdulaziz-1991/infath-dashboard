"""
app.py — تطبيق إنفاذ للمزادات مع تحديث تلقائي كامل (٦ منصات)
"""

import os
import time
import logging
import threading
import json
from flask import Flask, send_from_directory, jsonify

from infath_auto_updater import run_update

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("infath-app")

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_FILE = os.path.join(BASE_DIR, "infath_data.json")
INTERVAL_MINUTES = 20
USE_SELENIUM = True

app = Flask(__name__, static_folder=None)


def updater_loop():
    """يشغّل دورة تحديث واحدة كل INTERVAL_MINUTES دقيقة — يبدأ فوراً عند التشغيل"""
    while True:
        try:
            log.info("═══ بدء دورة تحديث تلقائية ═══")
            run_update(use_selenium=USE_SELENIUM, output_file=DATA_FILE)
        except Exception as e:
            log.error(f"فشلت دورة التحديث: {e}", exc_info=True)
        log.info(f"التحديث القادم بعد {INTERVAL_MINUTES} دقيقة...")
        time.sleep(INTERVAL_MINUTES * 60)


@app.route("/")
def index():
    return send_from_directory(BASE_DIR, "infath_live_dashboard.html")


@app.route("/infath_data.json")
def data_file():
    """يخدم ملف البيانات مع إلغاء الكاش دائماً حتى تقرأ اللوحة أحدث نسخة"""
    response = send_from_directory(BASE_DIR, "infath_data.json")
    response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate"
    response.headers["Pragma"] = "no-cache"
    return response


@app.route("/api/status")
def status():
    """نقطة فحص سريعة — تفتحها في المتصفح لتتحقق أن البيانات تُحدَّث بشكل صحيح"""
    try:
        with open(DATA_FILE, "r", encoding="utf-8") as f:
            d = json.load(f)
        return jsonify({
            "status": "ok",
            "last_updated": d.get("last_updated_label", "—"),
            "summary": d.get("summary", {}),
        })
    except Exception as e:
        return jsonify({"status": "error", "detail": str(e)}), 500


@app.route("/<path:filename>")
def static_files(filename):
    return send_from_directory(BASE_DIR, filename)


if __name__ == "__main__":
    # خيط واحد فقط — يبدأ فوراً ثم يكرر كل INTERVAL_MINUTES دقيقة
    threading.Thread(target=updater_loop, daemon=True).start()

    port = int(os.environ.get("PORT", 5000))
    log.info(f"تشغيل الخادم على المنفذ {port}...")
    app.run(host="0.0.0.0", port=port)
