#!/bin/sh
# Backend root EMAS, `app` foydalanuvchisi sifatida ishlaydi (audit 2026-11-03).
#
# Root qadam faqat bitta: doimiy `ezvizdata` volume'i (EZVIZ_TOKEN_DIR) eski
# deploy'larda root nomidan yozilgan — egasini `app` ga o'tkazamiz, keyin
# imtiyozlarni butunlay tashlab (`setpriv`, capability'larsiz) asosiy buyruqqa o'tamiz.
# Konteyner allaqachon root'siz ishga tushirilgan bo'lsa (`--user`) — to'g'ridan.
set -e

if [ "$(id -u)" = "0" ]; then
    dir="${EZVIZ_TOKEN_DIR:-/tmp/vizora-ezviz}"
    mkdir -p "$dir"
    chown -R app:app "$dir"
    # HOME ham almashadi: asyncpg ~/.postgresql/ ni o'qiydi, /root esa endi yopiq.
    export HOME=/home/app USER=app
    exec setpriv --reuid=app --regid=app --init-groups --inh-caps=-all --bounding-set=-all "$@"
fi
exec "$@"
