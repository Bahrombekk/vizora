// Backend API mijozi.
// Admin JWT endi HttpOnly cookie'da (vz_admin) — JS o'qiy olmaydi (XSS himoyasi).
// localStorage'da faqat rol saqlanadi (sir emas, UI ko'rsatish uchun).
const ROLE_KEY = "vz_role";

export const getRole = () => localStorage.getItem(ROLE_KEY);
// Cookie HttpOnly bo'lgani uchun JS uni ko'ra olmaydi — rol mavjudligini
// "kirgan" belgisi sifatida ishlatamiz; cookie eskirsa 401 -> /login.
export const isAuthed = () => !!getRole();
export function clearAuth() {
  localStorage.removeItem(ROLE_KEY);
}

export async function login(username, password) {
  const body = new URLSearchParams({ username, password });
  // credentials: cookie (vz_admin) o'rnatilishi/yuborilishi uchun.
  let r;
  try {
    r = await fetch("/api/auth/login", {
      method: "POST",
      body,
      credentials: "same-origin",
    });
  } catch {
    const e = new Error("Serverga ulanib bo'lmadi — tarmoqni tekshiring");
    e.kind = "network";
    throw e;
  }
  if (!r.ok) {
    // Avval HAR QANDAY xato "parol noto'g'ri" deb ko'rsatilardi — jumladan 429 (bloklangan)
    // va 5xx. Foydalanuvchi bloklanganini bilmay qayta-qayta urinardi.
    let detail = "";
    try { detail = (await r.json()).detail || ""; } catch { /* JSON emas */ }
    const e = new Error(
      r.status === 429 ? (detail || "Juda ko'p urinish — birozdan keyin qayta urinib ko'ring")
        : r.status >= 500 ? "Server javob bermayapti — birozdan keyin urinib ko'ring"
          : "Login yoki parol noto'g'ri");
    e.kind = r.status === 429 ? "locked" : r.status >= 500 ? "server" : "auth";
    throw e;
  }
  const d = await r.json();
  localStorage.setItem(ROLE_KEY, d.role);  // tokenni SAQLAMAYMIZ (cookie'da)
  return d;
}

export async function logout() {
  try {
    await fetch("/api/auth/logout", { method: "POST", credentials: "same-origin" });
  } catch {
    /* baribir tozalaymiz */
  }
  clearAuth();
  window.location.href = "/login";
}

// Joriy foydalanuvchi (cookie'dagi JWT'dan) — {username, role}.
export const getMe = () => apiFetch("/auth/me");

// Parol almashtirish. Backend cookie'ni yangi token bilan yangilaydi —
// sessiya uzilmaydi, qayta login shart emas.
export function changePassword(currentPassword, newPassword) {
  return apiFetch("/auth/change-password", {
    method: "POST",
    json: { current_password: currentPassword, new_password: newPassword },
  });
}

// path '/api' bilan boshlanadi: apiFetch('/branches')
export async function apiFetch(path, { json, ...opts } = {}) {
  const headers = { ...(opts.headers || {}) };
  if (json !== undefined) {
    headers["Content-Type"] = "application/json";
    opts.body = JSON.stringify(json);
  }
  // credentials: vz_admin cookie'ni yuborish uchun (Authorization header o'rniga).
  const r = await fetch(`/api${path}`, { ...opts, headers, credentials: "same-origin" });
  if (r.status === 401) {
    clearAuth();
    window.location.href = "/login";
    throw new Error("Sessiya tugadi");
  }
  if (!r.ok) {
    let msg = r.statusText;
    try {
      const e = await r.json();
      msg = e.detail || msg;
    } catch {
      /* ignore */
    }
    const err = new Error(typeof msg === "string" ? msg : "So'rov xatosi");
    err.status = r.status;
    throw err;
  }
  if (r.status === 204) return null;
  return r.json();
}

// ===== Iste'molchi (tashqi tizim) API — api_key bilan, JWT'siz =====
// Bu "klient taraf" ulanishi: Integratsiya sahifasi shu orqali kameralarni
// xuddi tashqi tizim kabi oladi (/api/v1/*).
// Kalit sessionStorage'da — faqat shu tab yopilguncha. localStorage'da u diskda
// abadiy qolardi va shu brauzerga kirgan har kim (yoki har qanday XSS) uni olib,
// mijozga berilgan barcha kameralarni ko'ra olardi (audit 2026-11-03).
const CKEY = "vz_consumer_key";
try { localStorage.removeItem(CKEY); } catch { /* eski versiyadan qolgan kalitni o'chiramiz */ }
export const getConsumerKey = () => {
  try { return sessionStorage.getItem(CKEY) || ""; } catch { return ""; }
};
export const setConsumerKey = (k) => {
  try {
    if (k) sessionStorage.setItem(CKEY, k);
    else sessionStorage.removeItem(CKEY);
  } catch { /* yopiq storage — kalit faqat xotirada */ }
};

export async function consumerFetch(path, key = getConsumerKey()) {
  if (!key) throw new Error("API kalit kiritilmagan");
  const r = await fetch(`/api/v1${path}`, { headers: { "X-API-Key": key } });
  if (!r.ok) {
    let msg = r.statusText;
    try {
      const e = await r.json();
      msg = e.detail || msg;
    } catch {
      /* ignore */
    }
    const err = new Error(typeof msg === "string" ? msg : "So'rov xatosi");
    err.status = r.status;
    throw err;
  }
  return r.json();
}

// Pagination uchun: {items, total} qaytaradi (X-Total-Count header'idan).
export async function apiList(path) {
  const r = await fetch(`/api${path}`, { credentials: "same-origin" });
  if (r.status === 401) { clearAuth(); window.location.href = "/login"; throw new Error("Sessiya tugadi"); }
  if (!r.ok) throw new Error((await r.json().catch(() => ({}))).detail || "So'rov xatosi");
  const total = parseInt(r.headers.get("X-Total-Count") || "0", 10);
  const items = await r.json();
  return { items, total: total || items.length };
}
