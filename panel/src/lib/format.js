// Umumiy formatlash yordamchilari (Dashboard, Monitoring va boshqalar uchun).

// Kamera holati -> CSS rang o'zgaruvchisi. "no_stream" = tarmoqda bor-u
// video bermaydi (alohida kategoriya) -> sariq, oflayn emas.
export const statusColor = (s) =>
  s === "online"
    ? "var(--online)"
    : s === "error" || s === "no_stream"
      ? "var(--error)"
      : "var(--offline)";

// Kamera holati -> o'zbekcha yorliq.
export const statusLabel = (s) =>
  s === "online"
    ? "Onlayn"
    : s === "no_stream"
      ? "Oqim yo'q"
      : s === "error"
        ? "Xatolik"
        : "Oflayn";

// ISO vaqtni "hozir / 4 daq oldin / 2 soat oldin" ko'rinishiga keltiradi.
export function relTime(iso) {
  if (!iso) return "—";
  const d = (Date.now() - new Date(iso).getTime()) / 1000;
  if (d < 60) return "hozir";
  if (d < 3600) return `${Math.floor(d / 60)} daq oldin`;
  if (d < 86400) return `${Math.floor(d / 3600)} soat oldin`;
  return `${Math.floor(d / 86400)} kun oldin`;
}

/** CSV katak — formula in'ektsiyasiga qarshi (`= + - @` / tab / CR bilan boshlansa `'`
 * qo'shiladi: Excel uni formula deb ishga tushirmaydi) va qo'shtirnoq bilan o'raladi. */
export function csvCell(v) {
  let s = v == null ? "" : String(v);
  if (/^[=+\-@\t\r]/.test(s)) s = "'" + s;
  return `"${s.replace(/"/g, '""')}"`;
}
