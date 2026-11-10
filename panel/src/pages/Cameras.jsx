import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import Modal from "../components/Modal";
import Player from "../player/Player";
import CamSnapshot from "../player/CamSnapshot";
import KeyBox from "../components/KeyBox";
import Pager from "../components/Pager";
import { relTime, statusLabel } from "../lib/format";
import { apiList, apiFetch, getRole } from "../api/client";
import { useList } from "../lib/useList";
import { feedFor } from "../lib/theme.jsx";
import { PageHeader } from "../components/ui.jsx";
import {
  IcCameraOff, IcCheck, IcCloud, IcEdit, IcExport, IcEye, IcEyeOff, IcGrid, IcKey, IcList, IcLock, IcMapPin, IcPlus,
  IcRefresh, IcSearch, IcServer, IcStar, IcTrash, IcUpload, IcVideo,
} from "../components/icons.jsx";

const PAGE = 24;

// --- RTSP manzilini maydonlardan yig'ish ---
// Operator rtsp://... ni qo'lda yozmaydi: IP, port, login, parol va kanalni kiritadi,
// URL esa brend shabloni bo'yicha shu yerda quriladi. Login/parol URL-kodlanadi
// (backend check_rtsp_url `$ \ " '` va bo'sh joyni rad etadi; `@ : /` esa URL'ni buzadi).
// "To'liq URL" rejimi nostandart kameralar uchun saqlanib qoladi.
const RTSP_BRANDS = {
  hikvision: {
    label: "Hikvision / HiWatch",
    main: (ch) => `/Streaming/Channels/${ch}01`,
    sub: (ch) => `/Streaming/Channels/${ch}02`,
  },
  dahua: {
    label: "Dahua / Imou",
    main: (ch) => `/cam/realmonitor?channel=${ch}&subtype=0`,
    sub: (ch) => `/cam/realmonitor?channel=${ch}&subtype=1`,
  },
  generic: { label: "Boshqa (yo'lni o'zim yozaman)" },
};

// encodeURIComponent `'` ni kodlamaydi, backend esa uni rad etadi — shuni qo'shimcha kodlaymiz.
const encCred = (s) => encodeURIComponent(s).replace(/'/g, "%27");
const normPath = (p) => { const t = (p || "").trim(); return !t ? "" : t.startsWith("/") ? t : "/" + t; };

/** {main, sub, error} — oddiy rejimda maydonlardan, URL rejimida qo'lda yozilganidan. */
function rtspUrls(form, { mask = false } = {}) {
  if (form.conn_mode === "url") return { main: form.rtsp_url.trim(), sub: form.rtsp_sub_url.trim(), error: null };
  const host = (form.rtsp_host || "").trim();
  if (!host) return { main: "", sub: "", error: null };
  if (!/^[A-Za-z0-9.\-]+$/.test(host)) return { main: "", sub: "", error: "IP manzil noto'g'ri (masalan 192.168.1.64)" };
  const port = String(form.rtsp_port || "554").trim();
  if (!/^\d{1,5}$/.test(port) || +port < 1 || +port > 65535) return { main: "", sub: "", error: "Port 1–65535 oralig'ida bo'lsin" };
  const ch = Math.max(1, parseInt(form.rtsp_channel, 10) || 1);
  const user = (form.rtsp_user || "").trim();
  const pass = form.rtsp_pass || "";
  const cred = user ? `${encCred(user)}${pass ? ":" + (mask ? "•••" : encCred(pass)) : ""}@` : "";
  const base = `rtsp://${cred}${host}:${port}`;
  const brand = RTSP_BRANDS[form.rtsp_brand] || RTSP_BRANDS.hikvision;
  const mainPath = brand.main ? brand.main(ch) : normPath(form.rtsp_path) || "/";
  const subPath = brand.sub ? brand.sub(ch) : normPath(form.rtsp_sub_path);
  return {
    main: base + mainPath,
    sub: form.rtsp_with_sub && subPath ? base + subPath : "",
    error: null,
  };
}

const RTSP_FIELDS = {
  conn_mode: "simple", rtsp_brand: "hikvision", rtsp_host: "", rtsp_port: "554", rtsp_user: "admin",
  rtsp_pass: "", rtsp_channel: 1, rtsp_path: "", rtsp_sub_path: "", rtsp_with_sub: true,
};

const empty = {
  source_type: "rtsp", name: "", zone_id: "", rtsp_url: "", rtsp_sub_url: "", ...RTSP_FIELDS,
  ezviz_account_id: "", ezviz_serial: "", ezviz_channel: 1, ezviz_verify_code: "", ezviz_drop_corrupt: false, ezviz_use_substream: false,
  isup_device_id: "", isup_channel: 1,
  codec: "auto", transcode: false, enabled: true, keep_warm: false,
};
// --- Hover-prewarm ---
// Bulut kamerasini ochish vaqtining KATTA qismi bulut bilan qo'l siqishga ketadi
// (o'lchandi: batareyali EZVIZ'da VTM 2.4s + stream.start() 4.6s). Foydalanuvchi
// kartaga sichqoncha keltirganda publisher'ni OLDINDAN boshlaymiz, shunda "Ko'rish"
// bosilganda oqim allaqachon tayyor bo'ladi (issiq oqimda birinchi kadr ~30ms).
// Ehtiyot choralari: 350ms debounce (grid bo'ylab yugurtirish publisher ochmasin),
// kamera bo'yicha memo (bir marta), sahifa bo'yicha jami chegara.
const PREWARM_DELAY_MS = 350;
const PREWARM_MAX = 14;          // bitta sahifa ko'rishida ko'pi bilan shuncha kamera
const PREWARM_TTL_MS = 4 * 60 * 1000;
const _prewarmed = new Map();    // id -> vaqt

function prewarmCamera(id, status) {
  if (status !== "online") return;                       // oflayn kamerani uyg'otmaymiz
  const now = Date.now();
  for (const [k, t] of _prewarmed) if (now - t >= PREWARM_TTL_MS) _prewarmed.delete(k);
  if (_prewarmed.has(id)) return;                        // yaqinda qilingan
  if (_prewarmed.size >= PREWARM_MAX) return;            // sahifa chegarasi
  _prewarmed.set(id, now);
  apiFetch(`/cameras/${id}/prewarm`, { method: "POST" }).catch(() => {
    _prewarmed.delete(id);                               // xato bo'lsa qayta urinish mumkin
  });
}

/** Kartaga/qatorga qo'yiladigan hover hodisalari (debounce bilan). */
function usePrewarmHover() {
  const timer = useMemo(() => ({ id: null }), []);
  const enter = useCallback((cam) => {
    clearTimeout(timer.id);
    timer.id = setTimeout(() => prewarmCamera(cam.id, cam.status), PREWARM_DELAY_MS);
  }, [timer]);
  const leave = useCallback(() => clearTimeout(timer.id), [timer]);
  useEffect(() => () => clearTimeout(timer.id), [timer]);
  return { onMouseEnter: enter, onMouseLeave: leave };
}

// "no_stream" — tarmoqda bor-u video bermaydi (alohida kategoriya): sariq.
const sColor = (s) => (s === "online" ? "var(--online)" : (s === "error" || s === "no_stream") ? "var(--error)" : "var(--offline)");
const badgeCls = (s) => (s === "online" ? "on" : (s === "error" || s === "no_stream") ? "err" : "off");
const srcLabel = (c) => (c.source_type === "ezviz" ? "EZVIZ" : c.source_type === "isup" ? "ISUP" : "RTSP");
const codecLabel = (c) => (c.transcode ? `${c.codec} →264` : c.codec);
// Holat tablari: soni joriy filtrlar bo'yicha (GET /cameras/counts)
const TABS = [["", "Hammasi", "all"], ["problem", "Muammoli", "problem"], ["online", "Onlayn", "online"], ["offline", "Oflayn", "offline"]];
const SORTS = [["new", "Yangi qo'shilgan"], ["name", "Nom (A→Z)"], ["status", "Muammolilar tepada"], ["seen", "Yaqinda ko'rilgan"]];
const VIEW_KEY = "vz_cam_view";
// Telefonda almashtirgich yashirin — doim kartalar (jadval u yerda baribir kartaga aylanadi)
function useNarrow(q = "(max-width: 760px)") {
  const [m, setM] = useState(() => window.matchMedia(q).matches);
  useEffect(() => {
    const mq = window.matchMedia(q);
    const on = () => setM(mq.matches);
    mq.addEventListener("change", on);
    return () => mq.removeEventListener("change", on);
  }, [q]);
  return m;
}

export default function Cameras() {
  const ents = useList("/branches");
  const facs = useList("/zones");
  const accounts = useList("/ezviz-accounts");
  const prewarm = usePrewarmHover();   // kartaga hover -> oqimni oldindan tayyorlash

  // Boshqa sahifadan filtr bilan kelish mumkin: /cameras?branch_id=… yoki ?zone_id=…
  const [fEnt, setFEnt] = useState(() => new URLSearchParams(location.search).get("branch_id") || "");
  const [fFac, setFFac] = useState(() => new URLSearchParams(location.search).get("zone_id") || "");
  const [fSource, setFSource] = useState("");
  // EZVIZ hisoblar sahifasidan: /cameras?ezviz_account_id=… (select yo'q — yorliq bilan ko'rsatiladi)
  const [fAcc, setFAcc] = useState(() => new URLSearchParams(location.search).get("ezviz_account_id") || "");
  const [fStatus, setFStatus] = useState("");
  const [sort, setSort] = useState("new");
  const [q, setQ] = useState("");
  const [page, setPage] = useState(0);
  const [rows, setRows] = useState([]);
  // Qidiruv BITTA kameraga qisqarganda — foydalanuvchi aynan shuni bosadi, shuning
  // uchun oqimni hoziroq tayyorlaymiz (bulut qo'l siqishi bosishdan oldin ketadi).
  useEffect(() => {
    if (rows.length === 1 && q.trim()) prewarmCamera(rows[0].id, rows[0].status);
  }, [rows, q]);
  const [total, setTotal] = useState(0);
  const [counts, setCounts] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);

  const [view, setViewState] = useState(() => { try { return localStorage.getItem(VIEW_KEY) || "grid"; } catch { return "grid"; } });
  const narrow = useNarrow();
  const shownView = narrow ? "grid" : view;
  const setView = (v) => { setViewState(v); try { localStorage.setItem(VIEW_KEY, v); } catch { /* */ } };
  const [form, setForm] = useState(null);
  const [watch, setWatch] = useState(null);
  const [importOpen, setImportOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const [formError, setFormError] = useState(null);
  const topRef = useRef(null);

  const facById = useMemo(() => Object.fromEntries(facs.data.map((f) => [f.id, f])), [facs.data]);
  const facOptions = fEnt ? facs.data.filter((f) => f.branch_id === fEnt) : facs.data;
  const anyFilter = fEnt || fFac || fSource || fAcc || q.trim();

  const fetchCameras = useCallback(async (silent) => {
    if (!silent) setLoading(true);
    const base = new URLSearchParams();
    if (fEnt) base.set("branch_id", fEnt);
    if (fFac) base.set("zone_id", fFac);
    if (fSource) base.set("source_type", fSource);
    if (fAcc) base.set("ezviz_account_id", fAcc);
    if (q.trim()) base.set("q", q.trim());
    const qs = new URLSearchParams(base);
    if (fStatus) qs.set("status", fStatus);
    qs.set("sort", sort);
    qs.set("limit", PAGE);
    qs.set("offset", page * PAGE);
    try {
      const [{ items, total }, cnt] = await Promise.all([
        apiList(`/cameras?${qs}`),
        apiFetch(`/cameras/counts?${base}`).catch(() => null),
      ]);
      setRows(items); setTotal(total); setError(null);
      if (cnt) setCounts(cnt);
      const ezIds = items.filter((c) => c.source_type === "ezviz" && c.enabled).map((c) => c.id);
      if (ezIds.length) apiFetch("/cameras/prewarm", { method: "POST", json: { ids: ezIds } }).catch(() => {});
    } catch (e) { setError(e.message); } finally { setLoading(false); }
  }, [fEnt, fFac, fSource, fAcc, fStatus, sort, q, page]);

  useEffect(() => {
    fetchCameras(false);
    const id = setInterval(() => fetchCameras(true), 10000);
    return () => clearInterval(id);
  }, [fetchCameras]);

  useEffect(() => { setPage(0); }, [fEnt, fFac, fSource, fAcc, fStatus, sort, q]);
  const goPage = (p) => {
    setPage(p);
    const el = topRef.current;
    if (el && el.getBoundingClientRect().top < 0) el.scrollIntoView({ behavior: "smooth", block: "start" });
  };

  async function save() {
    setBusy(true); setFormError(null);
    try {
      const isEz = form.source_type === "ezviz";
      const isIsup = form.source_type === "isup";
      const fac = form.zone_id || null;
      const isupId = form.isup_device_id.trim();
      if (isIsup && !isupId) throw new Error("ISUP Device ID kiriting yoki ro'yxatdan tanlang");
      if (isIsup && !/^[A-Za-z0-9_-]{1,64}$/.test(isupId)) throw new Error("Device ID'da faqat lotin harf, raqam, _ va - bo'lishi mumkin");
      if (form.id) {
        const payload = { name: form.name, enabled: form.enabled, keep_warm: !!form.keep_warm, source_type: form.source_type, zone_id: fac };
        if (isIsup) {
          payload.isup_device_id = isupId;
          payload.isup_channel = Number(form.isup_channel) || 1;
          if (getRole() === "admin" && form.isup_key) payload.isup_key = form.isup_key;
        } else if (isEz) {
          if (!form.ezviz_account_id) throw new Error("EZVIZ hisobini tanlang");
          if (!form.ezviz_serial) throw new Error("EZVIZ seriya raqamini kiriting");
          payload.ezviz_account_id = form.ezviz_account_id;
          payload.ezviz_serial = form.ezviz_serial;
          payload.ezviz_channel = Number(form.ezviz_channel) || 1;
          payload.ezviz_drop_corrupt = !!form.ezviz_drop_corrupt;
          payload.ezviz_use_substream = !!form.ezviz_use_substream;
          if (form.clear_code) payload.ezviz_verify_code = "";
          else if (form.ezviz_verify_code) payload.ezviz_verify_code = form.ezviz_verify_code;
        } else {
          const u = rtspUrls(form);
          if (u.error) throw new Error(u.error);
          payload.codec = form.codec; payload.transcode = form.transcode;
          if (u.main) payload.rtsp_url = u.main;
          if (form.clear_sub) payload.rtsp_sub_url = "";
          else if (u.sub) payload.rtsp_sub_url = u.sub;
        }
        await apiFetch(`/cameras/${form.id}`, { method: "PATCH", json: payload });
      } else {
        let payload;
        if (isIsup) {
          payload = {
            name: form.name, zone_id: fac, source_type: "isup",
            isup_device_id: isupId, isup_channel: Number(form.isup_channel) || 1, enabled: form.enabled,
            isup_key: getRole() === "admin" && form.isup_key ? form.isup_key : null,
          };
        } else if (isEz) {
          if (!form.ezviz_account_id) throw new Error("EZVIZ hisobini tanlang");
          if (!form.ezviz_serial) throw new Error("EZVIZ seriya raqamini kiriting");
          payload = {
            name: form.name, zone_id: fac, source_type: "ezviz",
            ezviz_account_id: form.ezviz_account_id, ezviz_serial: form.ezviz_serial,
            ezviz_channel: Number(form.ezviz_channel) || 1,
            ezviz_verify_code: form.ezviz_verify_code || null,
            ezviz_drop_corrupt: !!form.ezviz_drop_corrupt,
            ezviz_use_substream: !!form.ezviz_use_substream, enabled: form.enabled,
          };
        } else {
          const u = rtspUrls(form);
          if (u.error) throw new Error(u.error);
          if (!u.main) throw new Error(form.conn_mode === "url" ? "RTSP URL kiriting" : "Kameraning IP manzilini kiriting");
          payload = {
            name: form.name, zone_id: fac, source_type: "rtsp", rtsp_url: u.main,
            rtsp_sub_url: u.sub || null, codec: form.codec,
            transcode: form.transcode, enabled: form.enabled,
          };
        }
        payload.keep_warm = !!form.keep_warm;
        await apiFetch("/cameras", { method: "POST", json: payload });
      }
      setForm(null); fetchCameras(false);
    } catch (e) { setFormError(e.message); } finally { setBusy(false); }
  }

  async function remove(c) {
    if (!confirm(`"${c.name}" kamerasi o'chiriladi. Davom etamizmi?`)) return;
    await apiFetch(`/cameras/${c.id}`, { method: "DELETE" });
    fetchCameras(false);
  }

  const [exporting, setExporting] = useState(false);
  async function exportCsv() {
    setExporting(true);
    try {
      const r = await fetch("/api/cameras/export", { credentials: "same-origin" });
      if (!r.ok) { alert(r.status === 403 ? "Faqat admin eksport qila oladi" : "Eksport xatosi"); return; }
      const blob = await r.blob();
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      const d = new Date().toISOString().slice(0, 10);
      a.href = url; a.download = `kameralar-${d}.csv`;
      document.body.appendChild(a); a.click(); a.remove();
      URL.revokeObjectURL(url);
    } catch { alert("Eksport xatosi"); }
    finally { setExporting(false); }
  }

  const openNew = () => { setForm({ ...empty, zone_id: fFac || "" }); setFormError(null); };
  const openEdit = (c) => {
    setForm({
      id: c.id, source_type: c.source_type || "rtsp", zone_id: c.zone_id || "", name: c.name,
      rtsp_url: "", rtsp_sub_url: "", has_sub: c.has_sub, clear_sub: false, ...RTSP_FIELDS,
      ezviz_account_id: c.ezviz_account_id || "", ezviz_serial: c.ezviz_serial || "", ezviz_channel: c.ezviz_channel || 1,
      ezviz_verify_code: "", has_verify_code: c.has_verify_code, clear_code: false,
      ezviz_drop_corrupt: !!c.ezviz_drop_corrupt, ezviz_use_substream: !!c.ezviz_use_substream,
      isup_device_id: c.isup_device_id || "", isup_channel: c.isup_channel || 1,
      codec: c.codec, transcode: c.transcode, enabled: c.enabled, keep_warm: !!c.keep_warm,
    });
    setFormError(null);
  };
  const resetFilters = () => { setFEnt(""); setFFac(""); setFSource(""); setFAcc(""); setQ(""); };
  const place = (c) => [facById[c.zone_id]?.name, c.branch_name].filter(Boolean).join(" · ") || "Biriktirilmagan";

  return (
    <div>
      <PageHeader
        eyebrow="Kameralar"
        title="Kameralar"
        subtitle="Kameralarni qo'shing, tahrirlang va jonli ko'ring. Kartaga sichqoncha keltirsangiz oqim oldindan tayyorlanadi."
        tag={null}
        rightSlot={
          <div className="cam-actions">
            <button className="btn ghost" onClick={exportCsv} disabled={exporting} title="Barcha kameralar, CSV (Excel)"><IcExport size={16} /><span className="lbl">{exporting ? "…" : "Eksport"}</span></button>
            <button className="btn ghost" onClick={() => setImportOpen(true)} title="CSV'dan ko'p kamera qo'shish"><IcUpload size={16} /><span className="lbl">Import</span></button>
            <button className="btn" onClick={openNew}><IcPlus size={16} />Yangi kamera</button>
          </div>
        }
      />

      <div className="card clip cam-panel" ref={topRef} style={{ scrollMarginTop: 80 }}>
        {/* Filtrlar */}
        <div className="cam-filters">
          <div className="search-inline">
            <IcSearch size={15} />
            <input className="input" placeholder="Kamera nomi bo'yicha qidirish…" value={q} onChange={(e) => setQ(e.target.value)} />
          </div>
          <select className="select" value={fEnt} onChange={(e) => { setFEnt(e.target.value); setFFac(""); }}>
            <option value="">Barcha filiallar</option>
            {ents.data.map((e) => <option key={e.id} value={e.id}>{e.name}</option>)}
          </select>
          <select className="select" value={fFac} onChange={(e) => setFFac(e.target.value)}>
            <option value="">Barcha hududlar</option>
            {facOptions.map((f) => <option key={f.id} value={f.id}>{f.name}</option>)}
          </select>
          <select className="select" value={fSource} onChange={(e) => setFSource(e.target.value)}>
            <option value="">Barcha manbalar</option>
            <option value="rtsp">RTSP (simli)</option>
            <option value="ezviz">EZVIZ / Hik (bulut)</option>
            <option value="isup">ISUP (bulutsiz)</option>
          </select>
          <select className="select" value={sort} onChange={(e) => setSort(e.target.value)} title="Saralash">
            {SORTS.map(([k, l]) => <option key={k} value={k}>{l}</option>)}
          </select>
          <div className="seg view-seg">
            <button className={view === "grid" ? "on" : ""} onClick={() => setView("grid")} title="Kartalar"><IcGrid size={15} /></button>
            <button className={view === "table" ? "on" : ""} onClick={() => setView("table")} title="Jadval"><IcList size={15} /></button>
          </div>
        </div>
        {/* Holat tablari */}
        <div className="list-tabs" role="tablist">
          {TABS.map(([k, l, key]) => (
            <button key={k || "all"} role="tab" aria-selected={fStatus === k}
              className={`lt${fStatus === k ? " on" : ""}${k === "problem" ? " warn" : ""}`} onClick={() => setFStatus(k)}>
              {l}<span className="n">{counts ? counts[key] : "…"}</span>
            </button>
          ))}
          {fAcc && <button className="lt acc" onClick={() => setFAcc("")} title="Hisob filtrini olib tashlash">Hisob: {accounts.data.find((a) => a.id === fAcc)?.label || "…"} ✕</button>}
          {anyFilter && <button className="lt reset" onClick={resetFilters}>Filtrni tozalash ✕</button>}
        </div>
      </div>

      {error && <div className="error-banner">{error}</div>}

      {shownView === "grid" && (
        <div className="cam-grid">
          {rows.map((c, i) => (
            <div className={`cam-card${c.enabled === false ? " disabled" : ""}`} key={c.id}
              onMouseEnter={() => prewarm.onMouseEnter(c)} onMouseLeave={prewarm.onMouseLeave}>
              <div className="cam-feed" style={{ background: feedFor(i) }}>
                <CamSnapshot id={c.id} active={c.status === "online"} />
                <div className="stat-pill">
                  <span className="dot" style={{ background: sColor(c.status) }} />
                  <span className="t">{statusLabel(c.status)}</span>
                </div>
                {c.status === "online" && c.last_seen_at && <div className="ts-badge" title="Oxirgi tasdiqlangan aloqa">{relTime(c.last_seen_at)}</div>}
                {c.status !== "online" && (
                  <div className="offline-overlay">
                    <IcCameraOff size={26} sw={1.6} />
                    <span>{c.status === "no_stream" ? "Qurilma javob beradi — video yo'q" : c.enabled === false ? "O'chirilgan" : "Aloqa mavjud emas"}</span>
                    {c.last_seen_at && <em>oxirgi aloqa {relTime(c.last_seen_at)}</em>}
                  </div>
                )}
                <button className="play-hover" onClick={() => setWatch(c)} title="Jonli ko'rish">
                  <span className="circle"><IcEye size={20} /></span>
                </button>
              </div>
              <div className="cam-body">
                <div className="top">
                  <div className="name" title={c.name}>{c.name}{c.always_warm && <IcStar size={14} className="star" />}</div>
                  <div style={{ display: "flex", gap: 5, flex: "none" }}>
                    <span className={`chip${c.source_type === "rtsp" ? "" : " accent"}`}>{srcLabel(c)}</span>
                    {c.codec && c.codec !== "auto" && <span className="chip">{codecLabel(c)}</span>}
                  </div>
                </div>
                <div className="zone"><IcMapPin size={13} />{place(c)}</div>
                <div className="cam-hover">
                  <button className="btn soft" onClick={() => setWatch(c)}><IcEye size={15} />Ko'rish</button>
                  <button className="btn-sq" title="Tahrirlash" onClick={() => openEdit(c)}><IcEdit size={15} /></button>
                  <button className="btn-sq danger" title="O'chirish" onClick={() => remove(c)}><IcTrash size={15} /></button>
                </div>
              </div>
            </div>
          ))}
          {!loading && rows.length === 0 && (
            <div className="empty" style={{ gridColumn: "1/-1" }}>
              Kameralar topilmadi.{anyFilter && <> <button className="link-btn" onClick={resetFilters}>Filtrni tozalash</button></>}
            </div>
          )}
        </div>
      )}

      {shownView === "table" && (
        <div className="card clip">
          <div className="table-scroll">
            <table className="table mon-table">
              <thead><tr>
                <th>Kamera nomi</th><th>Holat</th><th>Filial / Hudud</th><th>Kodek</th><th>Oxirgi ko'rish</th><th style={{ textAlign: "right" }}>Harakatlar</th>
              </tr></thead>
              <tbody>
                {rows.map((c) => (
                  <tr key={c.id} className={c.enabled === false ? "disabled" : ""}
                    onMouseEnter={() => prewarm.onMouseEnter(c)} onMouseLeave={prewarm.onMouseLeave}>
                    <td>
                      <div className="cam-cell">
                        <span className="nm">{c.name}{c.always_warm && <IcStar size={13} className="star" />}</span>
                        <span className="src">{srcLabel(c)}{c.enabled === false ? " · o'chirilgan" : ""}</span>
                      </div>
                    </td>
                    <td className="st"><span className={`badge ${badgeCls(c.status)}`}><span className="dot" />{statusLabel(c.status)}</span></td>
                    <td className="muted" data-label="Filial / Hudud">{place(c)}</td>
                    <td className="muted" data-label="Kodek">{c.codec ? codecLabel(c) : "—"}</td>
                    <td className="muted" data-label="Oxirgi ko'rish" title={c.last_seen_at ? new Date(c.last_seen_at).toLocaleString("ru-RU") : ""}>{relTime(c.last_seen_at)}</td>
                    <td className="act">
                      <div className="row-actions">
                        <button className="btn-sq" style={{ width: 32, height: 32 }} title="Jonli ko'rish" onClick={() => setWatch(c)}><IcEye size={15} /><span className="bl">Ko'rish</span></button>
                        <button className="btn-sq" style={{ width: 32, height: 32 }} title="Tahrirlash" onClick={() => openEdit(c)}><IcEdit size={14} /><span className="bl">Tahrirlash</span></button>
                        <button className="btn-sq danger" style={{ width: 32, height: 32 }} title="O'chirish" onClick={() => remove(c)}><IcTrash size={14} /></button>
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          {!loading && rows.length === 0 && <div className="empty">Kameralar topilmadi.</div>}
        </div>
      )}

      <Pager page={page} size={PAGE} total={total} onPage={goPage} className="cam-pager" />

      {form && (
        <CameraForm form={form} setForm={setForm} zones={facs.data} accounts={accounts.data} busy={busy} formError={formError} onSave={save} onClose={() => setForm(null)} />
      )}

      {importOpen && (
        <ImportModal onClose={() => setImportOpen(false)} onDone={() => { setImportOpen(false); fetchCameras(false); }} />
      )}

      {watch && <Player camera={watch} zoneName={facById[watch.zone_id]?.name} onClose={() => setWatch(null)} />}
    </div>
  );
}

// --- Kamera formasi ---
// Bo'limlar: manba (katta kartalar) → asosiy → ulanish (RTSP yoki bulut) → qo'shimcha.
// Saqlash mantig'i Cameras.save() da — bu yerda faqat `form` holati to'ldiriladi.
function Section({ n, title, sub, children }) {
  return (
    <div className="cf-sec">
      <div className="cf-sec-h"><span className="n">{n}</span><div><b>{title}</b>{sub && <span>{sub}</span>}</div></div>
      {children}
    </div>
  );
}

function Toggle({ id, checked, onChange, title, hint }) {
  return (
    <label className="cf-toggle" htmlFor={id}>
      <input id={id} type="checkbox" checked={checked} onChange={(e) => onChange(e.target.checked)} />
      <span className="sw" />
      <span className="tx"><b>{title}</b>{hint && <em>{hint}</em>}</span>
    </label>
  );
}

function CameraForm({ form, setForm, zones, accounts, busy, formError, onSave, onClose }) {
  const [testing, setTesting] = useState(false);
  const [testResult, setTestResult] = useState(null);
  const [showUrl, setShowUrl] = useState(false);
  const isEz = form.source_type === "ezviz";
  const isIsup = form.source_type === "isup";
  const isRtsp = !isEz && !isIsup;
  const set = (patch) => setForm((f) => ({ ...f, ...patch }));
  const nameMissing = !form.name.trim();
  const footHint = nameMissing ? "Nom majburiy"
    : isEz ? (form.ezviz_serial ? `${form.ezviz_serial} · CH${form.ezviz_channel || 1}` : "Qurilmani tanlang")
    : isIsup ? (form.isup_device_id ? `ISUP ${form.isup_device_id} · CH${form.isup_channel || 1}` : "Device ID kiriting")
    : (rtspUrls(form).main || form.id ? "" : form.conn_mode === "url" ? "RTSP URL kiriting" : "IP manzil kiriting");

  const urls = isRtsp ? rtspUrls(form) : { main: "", sub: "", error: null };
  const preview = isRtsp && form.conn_mode !== "url" ? rtspUrls(form, { mask: true }) : null;
  const brand = RTSP_BRANDS[form.rtsp_brand] || RTSP_BRANDS.hikvision;
  const setConn = (patch) => { set(patch); setTestResult(null); };

  async function testRtsp() {
    if (urls.error) { setTestResult({ ok: false, msg: urls.error }); return; }
    if (!urls.main) { setTestResult({ ok: false, msg: form.conn_mode === "url" ? "Avval RTSP URL kiriting" : "Avval IP manzilni kiriting" }); return; }
    setTesting(true); setTestResult(null);
    try {
      const r = await apiFetch("/cameras/test", { method: "POST", json: { rtsp_url: urls.main } });
      if (r.ok) {
        setTestResult({ ok: true, msg: `Ulandi · ${String(r.codec || "?").toUpperCase()} · ${r.width}×${r.height}${r.codec === "h265" ? " · brauzer uchun H.264 ga o'giriladi" : ""}` });
        set({ codec: r.codec || form.codec, transcode: r.codec === "h265" ? true : form.transcode });
      } else setTestResult({ ok: false, msg: form.conn_mode === "url"
        ? "Ulanib bo'lmadi — URL, login/parol yoki tarmoqni tekshiring"
        : "Ulanib bo'lmadi — IP, login/parol, kanal yoki tarmoqni tekshiring" });
    } catch (e) { setTestResult({ ok: false, msg: e.message }); } finally { setTesting(false); }
  }

  return (
    <Modal
      wide
      title={<>{form.id ? <IcEdit size={17} /> : <IcPlus size={17} />}{form.id ? "Kamerani tahrirlash" : "Yangi kamera"}</>}
      onClose={onClose}
      footer={<>
        <span className="cf-foot-hint">{footHint}</span>
        <button className="btn ghost" onClick={onClose}>Bekor</button>
        <button className="btn" onClick={onSave} disabled={busy}>{busy ? "Saqlanmoqda…" : form.id ? "Saqlash" : "Qo'shish"}</button>
      </>}
    >
      {formError && <div className="error-banner">{formError}</div>}

      {/* Manba turi — katta kartalar */}
      <div className="cf-src">
        <button type="button" className={`cf-src-card${isRtsp ? " on" : ""}`} onClick={() => set({ source_type: "rtsp" })}>
          <span className="ic"><IcVideo size={20} /></span>
          <span><b>RTSP · IP kamera</b><em>Simli kamera yoki NVR, lokal tarmoqda</em></span>
        </button>
        <button type="button" className={`cf-src-card${isIsup ? " on" : ""}`} onClick={() => set({ source_type: "isup" })}>
          <span className="ic isup"><IcServer size={20} /></span>
          <span><b>ISUP · Hikvision</b><em>Qurilma o'zi serverga ulanadi — bulutsiz, tez</em></span>
        </button>
        <button type="button" className={`cf-src-card${isEz ? " on" : ""}`} onClick={() => set({ source_type: "ezviz" })}>
          <span className="ic cloud"><IcCloud size={20} /></span>
          <span><b>EZVIZ / Hik-Connect</b><em>Bulut orqali — RTSP kerak emas</em></span>
        </button>
      </div>

      <Section n="1" title="Asosiy" sub="Panelda ko'rinadigan nom va joylashuv">
        <div className="cf-grid">
          <div className="field">
            <label className="label">Nom</label>
            <input className={`input${nameMissing && formError ? " invalid" : ""}`} value={form.name} placeholder="masalan: Kirish darvozasi" onChange={(e) => set({ name: e.target.value })} autoFocus />
          </div>
          <div className="field">
            <label className="label">Hudud <span className="opt">ixtiyoriy</span></label>
            <select className="select" value={form.zone_id} onChange={(e) => set({ zone_id: e.target.value })}>
              <option value="">— biriktirilmagan —</option>
              {zones.map((f) => <option key={f.id} value={f.id}>{f.name}</option>)}
            </select>
          </div>
        </div>
        <Toggle id="en" checked={form.enabled} onChange={(v) => set({ enabled: v })} title="Faol"
          hint={form.enabled ? "Holati tekshiriladi va ko'rish mumkin" : "O'chiq — oqim ham, holat tekshiruvi ham to'xtaydi"} />
        <Toggle id="kw" checked={!!form.keep_warm} onChange={(v) => set({ keep_warm: v })} title="Doim issiq tutish"
          hint={form.keep_warm
            ? (isRtsp || form.source_type === "isup"
              ? "Oqim tomoshabinsiz ham doimiy olinadi — ko'rish bir zumda ochiladi (doimiy trafik)"
              : "Bulutdan oqim 24/7 olinadi — bir zumda ochiladi, lekin doimiy CPU/GPU va kamera trafigi (batareyali kameraga tavsiya etilmaydi)")
            : "O'chiq — oqim faqat kimdir ko'rayotganda olinadi"} />
      </Section>

      {isRtsp && (
        <Section n="2" title="Ulanish (RTSP)" sub={form.id
          ? "Bo'sh qoldirilsa — saqlangan ulanish o'zgarmaydi"
          : "Login va parol shifrlangan holda saqlanadi"}>
          <div className="seg cf-seg cf-mode">
            <button type="button" className={form.conn_mode !== "url" ? "on" : ""} onClick={() => setConn({ conn_mode: "simple" })}>IP · login · parol</button>
            <button type="button" className={form.conn_mode === "url" ? "on" : ""} onClick={() => setConn({ conn_mode: "url" })}>To'liq RTSP URL</button>
          </div>

          {form.conn_mode !== "url" ? (<>
            <div className="cf-grid">
              <div className="field">
                <label className="label">Kamera turi</label>
                <select className="select" value={form.rtsp_brand} onChange={(e) => setConn({ rtsp_brand: e.target.value })}>
                  {Object.entries(RTSP_BRANDS).map(([k, b]) => <option key={k} value={k}>{b.label}</option>)}
                </select>
              </div>
              <div className="cf-grid cf-ipport">
                <div className="field">
                  <label className="label">IP manzil</label>
                  <input className="input mono" inputMode="decimal" autoComplete="off" spellCheck={false} value={form.rtsp_host}
                    placeholder={form.id ? "(o'zgartirmaslik uchun bo'sh)" : "192.168.1.64"}
                    onChange={(e) => setConn({ rtsp_host: e.target.value })} />
                </div>
                <div className="field">
                  <label className="label">Port</label>
                  <input className="input mono" inputMode="numeric" value={form.rtsp_port} placeholder="554"
                    onChange={(e) => setConn({ rtsp_port: e.target.value.replace(/\D/g, "").slice(0, 5) })} />
                </div>
              </div>
            </div>
            <div className="cf-grid">
              <div className="field">
                <label className="label">Login</label>
                <input className="input" autoComplete="off" spellCheck={false} value={form.rtsp_user} placeholder="admin"
                  onChange={(e) => setConn({ rtsp_user: e.target.value })} />
              </div>
              <div className="field">
                <label className="label">Parol</label>
                <div className="cf-url">
                  <input className="input" type={showUrl ? "text" : "password"} autoComplete="new-password" spellCheck={false} value={form.rtsp_pass}
                    onChange={(e) => setConn({ rtsp_pass: e.target.value })} />
                  <button type="button" className="btn-sq" title={showUrl ? "Yashirish" : "Ko'rsatish"} onClick={() => setShowUrl((v) => !v)}>{showUrl ? <IcEyeOff size={15} /> : <IcEye size={15} />}</button>
                </div>
              </div>
            </div>
            {brand.main ? (
              <div className="field cf-chan">
                <label className="label">Kanal <span className="opt">NVR'da — kamera raqami</span></label>
                <input className="input mono" inputMode="numeric" value={form.rtsp_channel}
                  onChange={(e) => setConn({ rtsp_channel: e.target.value.replace(/\D/g, "").slice(0, 3) })} />
              </div>
            ) : (
              <div className="cf-grid">
                <div className="field">
                  <label className="label">Oqim yo'li</label>
                  <input className="input mono" spellCheck={false} value={form.rtsp_path} placeholder="/stream1"
                    onChange={(e) => setConn({ rtsp_path: e.target.value })} />
                </div>
                <div className="field">
                  <label className="label">Sub-stream yo'li <span className="opt">ixtiyoriy</span></label>
                  <input className="input mono" spellCheck={false} value={form.rtsp_sub_path} placeholder="/stream2"
                    onChange={(e) => setConn({ rtsp_sub_path: e.target.value })} />
                </div>
              </div>
            )}
            <label className="cf-check">
              <input type="checkbox" checked={!!form.rtsp_with_sub} disabled={form.clear_sub} onChange={(e) => setConn({ rtsp_with_sub: e.target.checked })} />
              Sub-stream ham qo'shilsin — past sifat, devor va sekin tarmoq uchun
              {form.id && form.has_sub && !form.clear_sub && <span className="saved"> · hozir sozlangan</span>}
            </label>
            <div className="cf-test-row">
              <div className="cf-preview mono" title="Shu manzil saqlanadi">
                {urls.error ? <span className="bad">{urls.error}</span>
                  : preview?.main ? preview.main
                  : <span className="faint">IP manzil kiritilgach, ulanish manzili shu yerda ko'rinadi</span>}
              </div>
              <button type="button" className="btn soft" onClick={testRtsp} disabled={testing || !urls.main || !!urls.error}>{testing ? "Sinalmoqda…" : "Sinash"}</button>
            </div>
          </>) : (<>
            <div className="field">
              <label className="label">Asosiy oqim URL</label>
              <div className="cf-url">
                <input className="input" type={showUrl ? "text" : "password"} autoComplete="off" spellCheck={false} value={form.rtsp_url}
                  placeholder={form.id ? "(o'zgartirmaslik uchun bo'sh qoldiring)" : "rtsp://user:parol@10.0.0.5:554/Streaming/Channels/101"}
                  onChange={(e) => setConn({ rtsp_url: e.target.value })} />
                <button type="button" className="btn-sq" title={showUrl ? "Yashirish" : "Ko'rsatish"} onClick={() => setShowUrl((v) => !v)}>{showUrl ? <IcEyeOff size={15} /> : <IcEye size={15} />}</button>
                <button type="button" className="btn soft" onClick={testRtsp} disabled={testing || !form.rtsp_url}>{testing ? "Sinalmoqda…" : "Sinash"}</button>
              </div>
            </div>
            <div className="field">
              <label className="label">
                Sub-stream URL <span className="opt">ixtiyoriy · past sifat, devor va sekin tarmoq uchun</span>
                {form.id && form.has_sub && !form.clear_sub && <span className="saved"> · sozlangan</span>}
              </label>
              <input className="input" type={showUrl ? "text" : "password"} autoComplete="off" value={form.rtsp_sub_url} placeholder="rtsp://user:parol@10.0.0.5:554/Streaming/Channels/102" disabled={form.clear_sub}
                onChange={(e) => set({ rtsp_sub_url: e.target.value })} />
            </div>
          </>)}
          {testResult && <div className={`cf-test ${testResult.ok ? "ok" : "bad"}`}>{testResult.ok ? <IcCheck size={14} /> : <IcCameraOff size={14} />}{testResult.msg}</div>}
          {form.id && form.has_sub && (
            <label className="cf-check"><input type="checkbox" checked={form.clear_sub} onChange={(e) => set({ clear_sub: e.target.checked })} />Sub-stream'ni o'chirish</label>
          )}
          <div className="cf-grid">
            <div className="field">
              <label className="label">Kodek</label>
              <div className="seg cf-seg">
                {[["auto", "Avto"], ["h264", "H.264"], ["h265", "H.265"]].map(([v, l]) => (
                  <button type="button" key={v} className={form.codec === v ? "on" : ""} onClick={() => set({ codec: v, transcode: v === "h265" ? true : v === "h264" ? false : form.transcode })}>{l}</button>
                ))}
              </div>
              <div className="cf-hint">Avto — birinchi tekshiruvda aniqlanadi.</div>
            </div>
            <Toggle id="tc" checked={form.transcode} onChange={(v) => set({ transcode: v })} title="H.264 ga transkod"
              hint="HEVC kameralar uchun — brauzer va tashqi tizimlar H.264 ni ochadi (CPU/GPU sarflaydi)" />
          </div>
        </Section>
      )}

      {isEz && <EzvizFields form={form} setForm={setForm} accounts={accounts} />}
      {isIsup && <IsupFields form={form} setForm={setForm} zones={zones} />}
    </Modal>
  );
}

// --- ISUP maydonlari ---
// Qurilma panelda qo'shilmasdan ham serverga ulanadi (isup-bridge), shuning uchun
// Device ID'ni qo'lda yozish shart emas — hozir ulanganlar ro'yxatidan tanlanadi.
const fmtUptime = (s) => (s < 60 ? `${s} s` : s < 3600 ? `${Math.floor(s / 60)} daq` : s < 86400 ? `${Math.floor(s / 3600)} soat` : `${Math.floor(s / 86400)} kun`);

function IsupFields({ form, setForm, zones }) {
  const admin = getRole() === "admin";
  const [busy, setBusy] = useState(false);
  const [info, setInfo] = useState(null);
  const [err, setErr] = useState(null);
  const set = (patch) => setForm((f) => ({ ...f, ...patch }));

  const load = useCallback(async () => {
    setBusy(true); setErr(null);
    try {
      const r = await apiFetch("/cameras/isup/devices");
      setInfo(r);
      if (!r.ok) setErr(r.error || "isup-bridge javob bermadi");
    } catch (e) { setErr(e.message); } finally { setBusy(false); }
  }, []);
  useEffect(() => { load(); }, [load]);

  const devices = info?.devices || [];
  const pick = (d) => {
    const free = [1, 2, 3, 4, 5, 6, 7, 8].find((ch) => !d.added_channels.includes(ch)) || 1;
    set({ isup_device_id: d.device_id, isup_channel: d.added_channels.length ? free : 1, name: form.name || d.device_id });
  };
  const host = info?.server_host || "<server IP>";
  const port = info?.server_port || 17660;

  return (
    <>
      <Section n="2" title="Qurilma (ISUP 5.0)" sub="Hozir serverga ulangan qurilmalardan tanlang yoki Device ID'ni yozing">
        <div className="cf-dev-tools" style={{ marginBottom: 8, justifyContent: "space-between" }}>
          <span className="cf-hint" style={{ margin: 0 }}>{info?.ok ? `${devices.length} ta qurilma ulangan` : ""}</span>
          <button type="button" className="btn soft" onClick={load} disabled={busy}><IcRefresh size={14} />{busy ? "Yuklanmoqda…" : "Yangilash"}</button>
        </div>
        {err && <div className="cf-test bad"><IcCameraOff size={14} />{err}</div>}
        {info?.ok && (
          <div className="cf-dev"><div className="cf-dev-list">
            {devices.length === 0 && <div className="empty" style={{ padding: 16 }}>Hozircha hech qaysi qurilma ulanmagan — pastdagi sozlamani qurilmaga kiriting.</div>}
            {devices.map((d) => {
              const live = d.streams.some((s) => s.live);
              return (
                <div key={d.device_id} className={`cf-d${form.isup_device_id === d.device_id ? " sel" : ""}`}>
                  <button type="button" className="cf-d-main" onClick={() => pick(d)} disabled={!d.valid}>
                    <span className={`dot ${live ? "on" : ""}`} title={live ? "Oqim kelyapti" : "Ulangan, oqim so'ralmagan"} />
                    <span className="nm">{d.device_id}<em>{[d.model || d.serial, d.ip, d.firmware, `ulangan ${fmtUptime(d.online_seconds)}`].filter(Boolean).join(" · ")}</em></span>
                    {!d.valid && <span className="chip warn" title="Device ID'da faqat lotin harf, raqam, _ va - bo'lishi mumkin">nom yaroqsiz</span>}
                    {d.added_channels.length > 0 && <span className="chip">qo'shilgan: CH{d.added_channels.join(", CH")}</span>}
                  </button>
                </div>
              );
            })}
          </div></div>
        )}
        <div className="cf-grid">
          <div className="field">
            <label className="label">Device ID</label>
            <input className="input mono" value={form.isup_device_id} placeholder="masalan: TCG406TEST" maxLength={64}
              onChange={(e) => set({ isup_device_id: e.target.value.trim() })} />
          </div>
          <div className="field">
            <label className="label">Kanal <span className="opt">NVR'da 1..N</span></label>
            <input className="input" type="number" min={1} max={512} value={form.isup_channel} onChange={(e) => set({ isup_channel: e.target.value })} />
          </div>
        </div>
      </Section>

      <IsupKeyPick form={form} set={set} admin={admin} zones={zones} />

      <Section n="4" title="Qurilmani sozlash" sub="Bir marta, qurilmaning web-interfeysida: Configuration → Network → Advanced → Platform Access → ISUP">
        <div className="cf-isup-steps">
          <span>Protocol Version</span><b>v5.0</b>
          <span>Server Address</span><b className="mono">{host}</b>
          <span>Port</span><b className="mono">{port}</b>
          <span>Device ID</span><b className="mono">{form.isup_device_id || "—"}</b>
          <span>Encryption Key</span><b>{form.isup_key_value ? <span className="mono">{form.isup_key_value}</span> : "3-bo'limdagi kalit"}</b>
        </div>
        <div className="cf-hint">Save → 10–20 s da «Registration Status: Online». Substream doim ochiq turadi (holat, devor, rasm); asosiy oqim faqat ko'rilganda ochiladi (~0.3 s). H.265 kamera brauzer uchun H.264 ga o'giriladi.</div>
      </Section>
    </>
  );
}

// --- ISUP kalit (kamera formasi) ---
// Ikki yo'l: "Hudud kaliti" — hududning bitta umumiy kaliti; "Ulanish ma'lumoti" —
// hududda oldindan yaratilgan tayyor juftlik (Device ID + o'z kaliti) dan tanlash:
// Device ID o'zi to'ladi. Odatda kamera qo'lda qo'shilmaydi ham — ulanish ma'lumoti
// bilan ulangan qurilmaning kamerasi o'zi paydo bo'ladi. Faqat admin.
function IsupKeyPick({ form, set, admin, zones }) {
  const [slots, setSlots] = useState(null);
  const [fac, setFac] = useState(null);
  const [facKey, setFacKey] = useState(null);
  const [err, setErr] = useState(null);
  const [gen, setGen] = useState(false);
  const fid = form.zone_id;

  const reload = useCallback(async () => {
    if (!admin || !fid) { setSlots(null); setFac(null); setFacKey(null); return; }
    try {
      const [s, fs] = await Promise.all([apiFetch(`/isup/zones/${fid}/slots`), apiFetch("/isup/zones")]);
      const ff = fs.find((x) => x.id === fid) || null;
      setSlots(s); setFac(ff);
      setFacKey(ff?.isup_key_id ? (await apiFetch(`/isup/zones/${fid}/key`)).key : null);
    } catch (e) { setErr(e.message); }
  }, [admin, fid]);
  useEffect(() => { reload(); }, [reload]);

  const free = (slots || []).filter((s) => s.key && !s.cameras.length);
  const slot = (slots || []).find((s) => s.device_id === form.isup_device_id && s.key);
  const mode = form.isup_mode || (slot ? "slot" : "zone");
  // Ulanish ma'lumotining kaliti qurilmaga allaqachon bog'langan; hudud kaliti yo'lida
  // qurilmaga "standart" yoziladi (avval boshqa kalitda bo'lsa ham).
  useEffect(() => {
    set({ isup_key: mode === "zone" ? "default" : null, isup_key_value: mode === "slot" ? slot?.key || null : facKey });
  }, [mode, slot?.key, facKey]); // eslint-disable-line react-hooks/exhaustive-deps

  async function makeFacKey() {
    setGen(true); setErr(null);
    try { await apiFetch(`/isup/zones/${fid}/key`, { method: "POST" }); await reload(); }
    catch (e) { setErr(e.message); } finally { setGen(false); }
  }
  async function makeSlot() {
    setGen(true); setErr(null);
    try {
      const [m] = await apiFetch(`/isup/zones/${fid}/slots`, { method: "POST", json: { count: 1 } });
      await reload();
      set({ isup_mode: "slot", isup_device_id: m.device_id });
    } catch (e) { setErr(e.message); } finally { setGen(false); }
  }

  const facName = zones.find((x) => x.id === fid)?.name;
  return (
    <Section n="3" title="Kalit (Encryption Key)" sub={facName ? `«${facName}» hududidan` : "Kalit hududdan beriladi"}>
      {!admin ? <div className="cf-hint" style={{ marginTop: 0 }}>Kalitni administrator beradi.</div>
        : !fid ? <div className="cf-test bad"><IcCameraOff size={14} />Avval 1-bo'limda hududni tanlang — kalit hududdan beriladi.</div>
        : !slots ? <div className="cf-hint" style={{ marginTop: 0 }}>Yuklanmoqda…</div> : (
          <>
            {err && <div className="cf-test bad"><IcCameraOff size={14} />{err}</div>}
            <div className="cf-src two">
              <button type="button" className={`cf-src-card${mode === "zone" ? " on" : ""}`} onClick={() => set({ isup_mode: "zone" })}>
                <span className="ic"><IcKey size={18} /></span>
                <span><b>Hudud kaliti</b><em>{fac?.isup_key_id ? "Bitta umumiy kalit — Device ID'ni o'zingiz yozasiz" : "Hududda kalit yo'q"}</em></span>
              </button>
              <button type="button" className={`cf-src-card${mode === "slot" ? " on" : ""}`} onClick={() => set({ isup_mode: "slot", isup_device_id: (slot || free[0])?.device_id || form.isup_device_id })}>
                <span className="ic isup"><IcLock size={18} /></span>
                <span><b>Ulanish ma'lumoti</b><em>{free.length ? `${free.length} ta ishlatilmagan — Device ID o'zi to'ladi` : "Ishlatilmagani yo'q — yangisini yarating"}</em></span>
              </button>
            </div>
            {mode === "zone" && !fac?.isup_key_id && (
              <div className="cf-url" style={{ marginBottom: 8 }}>
                <div className="cf-test bad" style={{ flex: 1, margin: 0 }}><IcCameraOff size={14} />Hududda kalit yo'q.</div>
                <button type="button" className="btn soft" onClick={makeFacKey} disabled={gen}><IcKey size={14} />{gen ? "…" : "Hudud kalitini yaratish"}</button>
              </div>
            )}
            {mode === "slot" && (
              <div className="cf-url" style={{ marginBottom: 8 }}>
                <select className="select" value={slot ? slot.device_id : ""} onChange={(e) => set({ isup_device_id: e.target.value })}>
                  {!slot && <option value="">— tanlang —</option>}
                  {(slots || []).filter((s) => s.key && (!s.cameras.length || s.device_id === form.isup_device_id)).map((s) => (
                    <option key={s.id} value={s.device_id}>{s.device_id} — {s.status === "waiting" ? "ishlatilmagan" : s.online ? "ulangan" : "ishlatilgan"}</option>
                  ))}
                </select>
                <button type="button" className="btn soft" onClick={makeSlot} disabled={gen}><IcPlus size={14} />{gen ? "…" : "Yangi yaratish"}</button>
              </div>
            )}
            {form.isup_key_value && <KeyBox value={form.isup_key_value} note={mode === "slot" ? `Device ID: ${form.isup_device_id} · shu kalit faqat shu Device ID bilan ishlaydi.` : "Hudud kaliti — qurilmaning Encryption Key maydoniga yoziladi."} />}
          </>
        )}
    </Section>
  );
}

// --- EZVIZ / Hik-Connect maydonlari ---
function EzvizFields({ form, setForm, accounts }) {
  const [busy, setBusy] = useState(false);
  const [devices, setDevices] = useState(null);
  const [channels, setChannels] = useState(null);
  const [err, setErr] = useState(null);
  const [dq, setDq] = useState("");
  const [onlyNew, setOnlyNew] = useState(true);
  const set = (patch) => setForm((f) => ({ ...f, ...patch }));
  const acc = accounts.find((a) => a.id === form.ezviz_account_id);

  async function discover() {
    if (!form.ezviz_account_id) { setErr("Avval hisobni tanlang"); return; }
    setBusy(true); setErr(null); setDevices(null); setChannels(null);
    try {
      const r = await apiFetch(`/cameras/ezviz/devices?account_id=${form.ezviz_account_id}`);
      if (!r.ok) setErr(r.error || "Bulutga ulanib bo'lmadi");
      else { setDevices(r.devices); setChannels(r.channels || []); }
    } catch (e) { setErr(e.message); } finally { setBusy(false); }
  }

  // Qurilmalar + ularning NVR kanallari birga (kanal o'z qurilmasi ostida)
  const list = useMemo(() => {
    if (!devices) return [];
    const n = dq.trim().toLowerCase();
    const chBy = {};
    for (const c of channels || []) (chBy[c.serial] ||= []).push(c);
    return devices
      .map((d) => ({ ...d, chans: chBy[d.serial] || [] }))
      .filter((d) => !n || d.name.toLowerCase().includes(n) || d.serial.toLowerCase().includes(n)
        || d.chans.some((c) => c.name.toLowerCase().includes(n)))
      .filter((d) => !onlyNew || (d.chans.length ? d.chans.some((c) => !c.already_added) : !d.already_added));
  }, [devices, channels, dq, onlyNew]);

  const pickDevice = (d) => set({ ezviz_serial: d.serial, ezviz_channel: d.chans.length ? d.chans[0].channel : 1, name: form.name || d.name });
  const pickChannel = (c) => set({ ezviz_serial: c.serial, ezviz_channel: c.channel, name: form.name || c.name });

  return (
    <>
      <Section n="2" title="Bulut hisobi va qurilma" sub="Hisobdagi qurilmalarni yuklab, keraklisini tanlang — seriya va kanal o'zi to'ladi">
        <div className="cf-acc">
          <select className="select" value={form.ezviz_account_id} onChange={(e) => { set({ ezviz_account_id: e.target.value }); setDevices(null); setChannels(null); }}>
            <option value="">— hisobni tanlang —</option>
            {accounts.map((a) => <option key={a.id} value={a.id}>{a.provider === "hik" ? "Hik-Connect" : "EZVIZ"} · {a.label} ({a.account})</option>)}
          </select>
          <button type="button" className="btn soft" onClick={discover} disabled={busy || !form.ezviz_account_id}>
            <IcRefresh size={14} />{busy ? "Yuklanmoqda…" : devices ? "Qayta yuklash" : "Qurilmalarni yuklash"}
          </button>
        </div>
        {accounts.length === 0 && <div className="cf-test bad"><IcCameraOff size={14} />Avval «EZVIZ hisoblar» bo'limida hisob qo'shing.</div>}
        {err && <div className="cf-test bad"><IcCameraOff size={14} />{err}</div>}

        {devices && (
          <div className="cf-dev">
            <div className="cf-dev-tools">
              <div className="search-inline">
                <IcSearch size={14} />
                <input className="input" placeholder={`${acc?.label || "Hisob"}: ${devices.length} qurilma — qidirish…`} value={dq} onChange={(e) => setDq(e.target.value)} />
              </div>
              <label className="cf-check"><input type="checkbox" checked={onlyNew} onChange={(e) => setOnlyNew(e.target.checked)} />Faqat qo'shilmaganlar</label>
            </div>
            <div className="cf-dev-list">
              {list.length === 0 && <div className="empty" style={{ padding: 16 }}>{onlyNew ? "Qo'shilmagan qurilma yo'q." : "Topilmadi."}</div>}
              {list.map((d) => {
                const devSel = form.ezviz_serial === d.serial;
                return (
                  <div key={d.serial} className={`cf-d${devSel ? " sel" : ""}`}>
                    <button type="button" className="cf-d-main" onClick={() => pickDevice(d)} disabled={!d.chans.length && d.already_added}>
                      <span className={`dot ${d.online ? "on" : ""}`} />
                      <span className="nm">{d.name}<em>{d.serial}{d.model ? ` · ${d.model}` : ""}</em></span>
                      {d.chans.length > 1 && <span className="chip">NVR · {d.chans.length} kanal</span>}
                      {d.encrypted && <span className="chip warn" title="Tasdiqlash kodi kerak"><IcLock size={11} />shifrlangan</span>}
                      {!d.chans.length && d.already_added && <span className="chip">qo'shilgan</span>}
                    </button>
                    {d.chans.length > 1 && (
                      <div className="cf-chs">
                        {d.chans.map((c) => {
                          const sel = devSel && Number(form.ezviz_channel) === c.channel;
                          return (
                            <button type="button" key={c.channel} className={`cf-ch${sel ? " sel" : ""}${c.already_added ? " added" : ""}`} onClick={() => pickChannel(c)} title={c.already_added ? "Bu kanal allaqachon qo'shilgan" : c.name}>
                              <b>CH{c.channel}</b><span>{c.name}</span>{c.already_added && <IcCheck size={11} />}
                            </button>
                          );
                        })}
                      </div>
                    )}
                  </div>
                );
              })}
            </div>
          </div>
        )}

        <div className="cf-grid three">
          <div className="field">
            <label className="label">Seriya raqami</label>
            <input className="input mono" value={form.ezviz_serial} placeholder="masalan: GK8973616" onChange={(e) => set({ ezviz_serial: e.target.value.trim().toUpperCase() })} />
          </div>
          <div className="field">
            <label className="label">Kanal</label>
            <input className="input" type="number" min={1} value={form.ezviz_channel} onChange={(e) => set({ ezviz_channel: e.target.value })} />
          </div>
          <div className="field">
            <label className="label">
              Tasdiqlash kodi <span className="opt">shifrlanganlar uchun</span>
              {form.id && form.has_verify_code && !form.clear_code && <span className="saved"> · saqlangan</span>}
            </label>
            <input className="input mono" value={form.ezviz_verify_code} disabled={form.clear_code}
              placeholder={form.id && form.has_verify_code ? "(bo'sh = o'zgarmaydi)" : "yorliqdagi 6 harf"}
              onChange={(e) => set({ ezviz_verify_code: e.target.value.trim() })} />
            {form.id && form.has_verify_code && (
              <label className="cf-check"><input type="checkbox" checked={form.clear_code} onChange={(e) => set({ clear_code: e.target.checked })} />Kodni o'chirish</label>
            )}
          </div>
        </div>
      </Section>

      <Section n="3" title="Qo'shimcha" sub="Odatda o'zgartirish shart emas">
        <div className="cf-grid">
          <Toggle id="usesubstream" checked={form.ezviz_use_substream} onChange={(v) => set({ ezviz_use_substream: v })}
            title="Doim substream" hint="Past o'lchamli oqim — yuqori o'lchamda buzilib keladigan kamera uchun" />
          <Toggle id="dropcorrupt" checked={form.ezviz_drop_corrupt} onChange={(v) => set({ ezviz_drop_corrupt: v })}
            title="Buzuq kadrlarni tashlash" hint="Tarmog'i sust kameralar uchun; CPU ko'proq ishlatadi" />
        </div>
        <div className="cf-hint">Bulut oqimi faqat kimdir ko'rayotganda ishlaydi (on-demand) va brauzer uchun H.264 ga o'giriladi.</div>
      </Section>
    </>
  );
}

// --- Bulk import ---
function parseImport(text) {
  const t = text.trim();
  if (!t) return [];
  if (t[0] === "[") return JSON.parse(t);
  return t.split(/\r?\n/).map((line) => line.split(",").map((x) => x.trim())).filter((p) => p.length >= 3 && !/^hudud/i.test(p[0]))
    .map((p) => ({ zone_code: p[0] || null, name: p[1], rtsp_url: p[2], codec: p[3] || "auto" }));
}

function ImportModal({ onClose, onDone }) {
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState(null);
  const [err, setErr] = useState(null);

  const preview = useMemo(() => { try { return { rows: parseImport(text), error: null }; } catch (e) { return { rows: [], error: e.message }; } }, [text]);

  async function readFile(file) {
    if (!file) return;
    setText(await file.text()); setResult(null); setErr(null);
  }

  async function submit() {
    setBusy(true); setErr(null); setResult(null);
    try {
      const rows = parseImport(text);
      if (!rows.length) throw new Error("Bo'sh yoki noto'g'ri format");
      const r = await apiFetch("/cameras/import", { method: "POST", json: rows });
      setResult(r);
      if (r.created > 0 && r.errors.length === 0) setTimeout(onDone, 900);
    } catch (e) { setErr(e.message); } finally { setBusy(false); }
  }

  return (
    <Modal wide title={<><IcUpload size={17} />Kameralarni import qilish (RTSP)</>} onClose={onClose}
      footer={<>
        <span className="cf-foot-hint">{preview.error ? "JSON xato" : preview.rows.length ? `${preview.rows.length} ta kamera tanildi` : ""}</span>
        <button className="btn ghost" onClick={onClose}>Yopish</button>
        <button className="btn" onClick={submit} disabled={busy || !preview.rows.length}>{busy ? "Yuklanmoqda…" : `Import${preview.rows.length ? ` (${preview.rows.length})` : ""}`}</button>
      </>}>
      {err && <div className="error-banner">{err}</div>}
      <div className="cf-imp-top">
        <label className="cf-drop">
          <input type="file" accept=".csv,.txt,.json" onChange={(e) => readFile(e.target.files?.[0])} />
          <IcUpload size={20} /><b>CSV yoki JSON fayl tanlang</b><span>yoki pastga matnni joylashtiring</span>
        </label>
        <div className="cf-imp-fmt">
          <div className="label">CSV ustunlari</div>
          <code>hudud_kodi, nom, rtsp_url[, kodek]</code>
          <div className="cf-hint">hudud_kodi ixtiyoriy (bo'sh — biriktirilmagan); kodek: auto / h264 / h265. Sarlavha qatori bo'lsa o'tkazib yuboriladi.</div>
        </div>
      </div>
      <div className="field">
        <textarea className="input mono" rows={7} style={{ fontSize: 12.5, resize: "vertical" }}
          placeholder={'B001, Kirish, rtsp://user:parol@10.0.0.5:554/Streaming/Channels/101\n, Ombor, rtsp://user:parol@10.0.0.6:554/stream1, h265\n\nyoki JSON:\n[{"zone_code":"B001","name":"Kirish","rtsp_url":"rtsp://..."}]'}
          value={text} onChange={(e) => { setText(e.target.value); setResult(null); }} />
      </div>
      {preview.rows.length > 0 && !result && (
        <div className="cf-prev">
          {preview.rows.slice(0, 6).map((r, i) => (
            <div key={i}><span className="chip">{r.zone_code || "—"}</span><b>{r.name}</b><span className="mono">{String(r.rtsp_url || "").replace(/\/\/[^@/]*@/, "//•••@")}</span><em>{r.codec || "auto"}</em></div>
          ))}
          {preview.rows.length > 6 && <div className="more">… yana {preview.rows.length - 6} ta</div>}
        </div>
      )}
      {preview.error && <div className="cf-test bad"><IcCameraOff size={14} />{preview.error}</div>}
      {result && (
        <div className="cf-result">
          <div className="ok"><IcCheck size={14} />Qo'shildi: <b>{result.created}</b></div>
          {result.skipped > 0 && <div className="bad">O'tkazib yuborildi: <b>{result.skipped}</b></div>}
          {result.errors?.slice(0, 6).map((e, i) => <div key={i} className="err">{e}</div>)}
        </div>
      )}
    </Modal>
  );
}
