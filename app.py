"""
app.py — تطبيق واحد يجمع بين عرض اللوحة وتحديث البيانات تلقائياً
====================================================================
مصمم خصيصاً للنشر على Render.com (الباقة المجانية) بدون أي أوامر طرفية.

ماذا يفعل:
  1. يشغّل خيط (thread) في الخلفية يستدعي run_update() من infath_auto_updater.py
     كل INTERVAL_MINUTES دقيقة، فيُحدّث ملف infath_data.json.
  2. يخدم (serve) ملفي infath_live_dashboard.html و infath_data.json كملفات ثابتة
     عبر نفس عنوان الموقع — فاللوحة تقرأ الملف بـ fetch محلي تماماً كما هو مصمم.

ملاحظة مهمة: تم تعطيل منصات الدال/سومتك/دار افتراضياً (use_selenium=False)
لأنها تحتاج متصفح Chrome حقيقي، وهذا غير متاح بسهولة على الباقة المجانية
من Render. ثلاث منصات (مباشر، وصلت، السعودية) تعمل بدون أي قيد.
"""

import os
import time
import logging
import threading
from flask import Flask, send_from_directory

from infath_auto_updater import run_update

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("infath-app")

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_FILE = os.path.join(BASE_DIR, "infath_data.json")
INTERVAL_MINUTES = 20
USE_SELENIUM = True  # الحل الكامل: المنصات الست جميعها (مباشر، وصلت، السعودية، الدال، سومتك، دار)

app = Flask(__name__, static_folder=None)


def updater_loop():
    while True:
        try:
            log.info("بدء دورة تحديث تلقائية...")
            run_update(use_selenium=USE_SELENIUM, output_file=DATA_FILE)
        except Exception as e:
            log.error(f"فشلت دورة التحديث: {e}")
        time.sleep(INTERVAL_MINUTES * 60)


@app.route("/")
def index():
    return send_from_directory(BASE_DIR, "infath_live_dashboard.html")


@app.route("/<path:filename>")
def static_files(filename):
    return send_from_directory(BASE_DIR, filename)


if __name__ == "__main__":
    # شغّل دورة تحديث أولى فوراً قبل فتح الموقع (حتى لا تكون اللوحة فارغة)
    threading.Thread(target=lambda: run_update(use_selenium=USE_SELENIUM, output_file=DATA_FILE),
                      daemon=True).start()
    # ثم شغّل الخيط الدوري الدائم
    threading.Thread(target=updater_loop, daemon=True).start()

    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
