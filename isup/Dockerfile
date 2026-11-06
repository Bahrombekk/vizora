# ISUP 5.0 ko'prik — Hikvision ISUP SDK (Linux64) + ffmpeg (copy, transkodsiz).
# sdk/ va inc/ REPODA YO'Q (Hikvision litsenziyasi, MLA) — qurishdan oldin qo'yiladi,
# qarang isup/README.md §3.
FROM ubuntu:22.04 AS build
RUN apt-get update && apt-get install -y --no-install-recommends g++ && rm -rf /var/lib/apt/lists/*
COPY inc/ /src/inc/
COPY sdk/ /opt/isup/lib/
COPY bridge.cpp /src/
RUN test -f /opt/isup/lib/libHCISUPCMS.so || { echo "isup/sdk/ bo'sh — Hikvision ISUP SDK .so fayllarini qo'ying (isup/README.md)"; exit 1; }
RUN g++ -O2 -std=c++17 -Wall -I/src/inc /src/bridge.cpp -o /src/isup-bridge \
    -L/opt/isup/lib -lHCISUPCMS -lHCISUPStream -lhpr -lHCNetUtils -lpthread \
    -Wl,-rpath,/opt/isup/lib

FROM ubuntu:22.04
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg curl ca-certificates tzdata \
    && rm -rf /var/lib/apt/lists/*
COPY sdk/ /opt/isup/lib/
COPY --from=build /src/isup-bridge /opt/isup/lib/isup-bridge
ENV LD_LIBRARY_PATH=/opt/isup/lib:/opt/isup/lib/HCAapSDKCom TZ=Asia/Tashkent
WORKDIR /opt/isup/lib
# Root EMAS: ko'prik internetdan kelgan ma'lumotni (ISUP) tahlil qiladi — SDK'da xato
# bo'lsa ham hujumchi konteynerda root bo'lib qolmasin. Portlar 1024 dan yuqori.
RUN useradd -r -u 10001 -d /opt/isup isup && mkdir -p /opt/isup/logs && chown -R isup /opt/isup/logs
USER isup
EXPOSE 17660/tcp 17660/udp 17661/tcp 8091/tcp
HEALTHCHECK --interval=30s --timeout=3s --start-period=10s \
    CMD curl -fsS -m 2 http://localhost:${API_PORT:-8091}/health || exit 1
CMD ["/opt/isup/lib/isup-bridge"]
