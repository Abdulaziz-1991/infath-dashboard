# صورة Docker تشمل متصفح Chromium جاهز — يلزم لتشغيل المنصات الثلاث
# التي تحتاج فحص DOM بصري (الدال، سومتك، دار المزادات) بجانب المنصات
# الثلاث ذات الـ API النظيف (مباشر، وصلت، السعودية). هذا هو الحل الكامل
# الذي يغطي المنصات الست معاً.

FROM python:3.11-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
        chromium \
        chromium-driver \
    && rm -rf /var/lib/apt/lists/*

ENV CHROME_BIN=/usr/bin/chromium
ENV CHROMEDRIVER_PATH=/usr/bin/chromedriver
ENV PYTHONUNBUFFERED=1

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

ENV PORT=5000
EXPOSE 5000

CMD ["python", "app.py"]
