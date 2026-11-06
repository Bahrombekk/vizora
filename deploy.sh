#!/usr/bin/env bash
# Serverda qo'lda deploy (CI/CD'siz ham ishlatsa bo'ladi).
# Ishlatish:  ./deploy.sh
set -euo pipefail

cd "$(dirname "$0")"

# --- Xavfsizlik: to'g'ri serverdamizmi? -------------------------------------
# 2026-09 da loyiha yangi serverga ko'chdi. CI/CD'dagi SSH_HOST eskisiga qarab
# qolsa, deploy jimgina NOTO'G'RI serverga tushib ketardi. Shuning uchun har bir
# deploy target serverda marker fayl bo'lishini talab qiladi.
#   Yangi serverni ro'yxatdan o'tkazish:
#     mkdir -p /etc/vizora && echo vizora-prod-01 > /etc/vizora/deploy-target
EXPECTED_TARGET="vizora-prod-01"
MARKER_FILE="/etc/vizora/deploy-target"

if [[ ! -f "$MARKER_FILE" ]]; then
  echo "XATO: $MARKER_FILE topilmadi — bu server deploy target sifatida ro'yxatdan o'tmagan." >&2
  echo "      Ehtimol CI/CD noto'g'ri (eski) serverga ulanyapti. Deploy to'xtatildi." >&2
  exit 1
fi

ACTUAL_TARGET="$(tr -d '[:space:]' < "$MARKER_FILE")"
if [[ "$ACTUAL_TARGET" != "$EXPECTED_TARGET" ]]; then
  echo "XATO: noto'g'ri server. Kutilgan '$EXPECTED_TARGET', topilgani '$ACTUAL_TARGET'." >&2
  echo "      Deploy to'xtatildi." >&2
  exit 1
fi

echo "==> Target tasdiqlandi: $ACTUAL_TARGET ($(hostname))"
# ---------------------------------------------------------------------------

# --- Compose fayllari: GPU overlay -----------------------------------------
# DIQQAT: `.env` dagi COMPOSE_FILE bu skriptda ISHLAMAYDI — quyidagi ANIQ `-f`
# bayroqlari uni bekor qiladi. Shuning uchun overlay aynan shu yerda tanlanadi.
#   VZ_GPU=1     -> majburiy yoqish
#   VZ_GPU=0     -> majburiy o'chirish (GPU bor bo'lsa ham CPU'da transkod)
#   VZ_GPU=auto  -> standart: hostda ishlaydigan NVIDIA runtime bo'lsa qo'shiladi

# Hostda ishlaydigan NVIDIA runtime bormi? (drayver + container toolkit)
has_nvidia_runtime() {
  command -v nvidia-smi >/dev/null 2>&1 || return 1
  nvidia-smi -L >/dev/null 2>&1 || return 1
  docker info --format '{{.Runtimes}}' 2>/dev/null | grep -q nvidia
}

COMPOSE_ARGS=(-f docker-compose.prod.yml)
GPU_MODE="${VZ_GPU:-auto}"
USE_GPU=no

if [[ "$GPU_MODE" == "1" ]]; then
  USE_GPU=yes
elif [[ "$GPU_MODE" == "auto" ]] && has_nvidia_runtime; then
  USE_GPU=yes
fi

if [[ "$USE_GPU" == "yes" ]]; then
  if [[ ! -f docker-compose.gpu.yml ]]; then
    echo "XATO: VZ_GPU yoqilgan, lekin docker-compose.gpu.yml topilmadi." >&2
    exit 1
  fi
  COMPOSE_ARGS+=(-f docker-compose.gpu.yml)
  echo "==> GPU overlay YOQILDI (NVENC) — docker-compose.gpu.yml"
else
  echo "==> GPU overlay o'chiq — transkod CPU'da (libx264)"
fi

COMPOSE=(docker compose "${COMPOSE_ARGS[@]}")
# ---------------------------------------------------------------------------

echo "==> Kod yangilanmoqda..."
# Faqat belgilangan branch deploy qilinadi (audit 2026-11-03): ilgari qaysi branch
# checkout qilingan bo'lsa o'sha tortilardi — sinov branch'i jimgina prod'ga chiqib
# ketishi mumkin edi. Boshqasini ataylab deploy qilish: DEPLOY_BRANCH=<nom>.
DEPLOY_BRANCH="${DEPLOY_BRANCH:-main}"
CUR_BRANCH="$(git rev-parse --abbrev-ref HEAD)"
if [[ "$CUR_BRANCH" != "$DEPLOY_BRANCH" ]]; then
  echo "XATO: serverda '$CUR_BRANCH' branch'i checkout qilingan, deploy '$DEPLOY_BRANCH' uchun." >&2
  echo "      git checkout $DEPLOY_BRANCH  (yoki DEPLOY_BRANCH=$CUR_BRANCH bash deploy.sh)" >&2
  exit 1
fi
git pull --ff-only origin "$DEPLOY_BRANCH"

echo "==> Image'lar build qilinmoqda..."
"${COMPOSE[@]}" build

# Migratsiyani app'dan OLDIN ishga tushiramiz: app startup'i `users` jadvalini
# so'raydi, shuning uchun bo'sh bazada app migratsiyasiz crash-loop'ga tushadi.
# `run` postgres/redis'ni (depends_on) ko'taradi va healthy bo'lishini kutadi;
# alembic idempotent — baza allaqachon head'da bo'lsa, hech narsa qilmaydi.
echo "==> Migratsiya (alembic upgrade head)..."
"${COMPOSE[@]}" run --rm app alembic upgrade head

echo "==> Konteynerlar ishga tushirilmoqda..."
"${COMPOSE[@]}" up -d

echo "==> Eski image'lar tozalanmoqda..."
docker image prune -f

echo "==> Deploy tugadi. Holat:"
"${COMPOSE[@]}" ps
