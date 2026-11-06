"""HTTP/WebSocket kirish nuqtalari — FAQAT router'lar va so'rov tekshiruvi.

  admin/     panel (/api/*): cookie JWT + CSRF Origin tekshiruvi
  consumer/  tashqi tizimlar: REST /api/v1/* va WS /ws/consumer (API kalit + scope)
  media/     oqim protokollari: /hls, /whep, /ws/stream (stream token)

Biznes-mantiq bu yerda yozilmaydi — `vizora.services` / `vizora.sources` / `vizora.media`
chaqiriladi."""
