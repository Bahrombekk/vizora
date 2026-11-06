# Aniq versiya (audit 2026-11-03): `3.12-slim` har build'da jimgina yangilanardi.
FROM python:3.12.13-slim

# ffmpeg/ffprobe — RTSP probe va kerak bo'lganda transkod uchun
RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Root'siz ishlash: internetdan keladigan oqim/so'rovlarni tahlil qiladigan jarayon
# (ffmpeg, PyAV, bulut javoblari) buzilsa ham konteynerda root bo'lmasin.
# Entrypoint faqat volume egasini to'g'rilab, `app` ga o'tadi (docker-entrypoint.sh).
RUN useradd -r -u 10001 -m -d /home/app app \
    && chmod 0755 /app/docker-entrypoint.sh   # dev: ./server mount qilinadi — sh orqali
ENTRYPOINT ["sh", "/app/docker-entrypoint.sh"]

EXPOSE 8000

# --timeout-graceful-shutdown: Monitoring sahifasi ochiq SSE ulanishini
# (/api/monitoring/stream) ushlab turadi va u HECH QACHON yopilmaydi.
# Busiz uvicorn to'xtatishda "Waiting for connections to close" da qotib
# qoladi -> `docker compose restart`/deploy Docker SIGKILL'ini kutadi.
#
# --ws-per-message-deflate false: brauzer doim permessage-deflate taklif qiladi va
# uvicorn HAR VIDEO KADRNI zlib bilan siqardi — allaqachon siqilgan H.264/HEVC uchun
# foydasiz, lekin asosiy event loop'da. py-spy: 40 WS tomoshabinda eng issiq funksiya
# (`PerMessageDeflate.encode`), panel API'si 5-12 s javob berardi ("sayt osiladi").
CMD ["uvicorn", "vizora.main:app", "--host", "0.0.0.0", "--port", "8000", "--timeout-graceful-shutdown", "10", "--ws-per-message-deflate", "false"]
