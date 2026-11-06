import { useEffect, useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";
import Modal from "../components/Modal";
import ScopePicker from "../components/ScopePicker";
import { apiFetch, setConsumerKey } from "../api/client";
import { useList } from "../lib/useList";
import { PageHeader } from "../components/ui.jsx";
import { IcArrowRight, IcCamera, IcCheck, IcCopy, IcEdit, IcBranch, IcExternal, IcZone, IcKey, IcPause, IcPlay, IcPlus, IcRefresh, IcSearch, IcTrash } from "../components/icons.jsx";

// Faollik: qachon oxirgi marta API'ga murojaat qilgan
const STALE_DAYS = 30;
function activity(c) {
  if (!c.enabled) return { cls: "off", text: "O'chirilgan" };
  if (!c.last_used_at) return { cls: "never", text: "Hali ishlatilmagan" };
  const days = (Date.now() - new Date(c.last_used_at).getTime()) / 86400000;
  if (days > STALE_DAYS) return { cls: "stale", text: `${Math.floor(days)} kundan beri jim` };
  return { cls: "on", text: "Faol" };
}
const TABS = [["all", "Hammasi"], ["on", "Faol"], ["stale", "Jim"], ["never", "Ishlatilmagan"], ["off", "O'chirilgan"]];

const AV = ["#3b82f6", "#8b5cf6", "#0ea5a3", "#f59e0b", "#ec4899", "var(--accent)"];
const avColor = (s) => AV[[...s].reduce((a, c) => a + c.charCodeAt(0), 0) % AV.length];

function relLong(iso) {
  if (!iso) return "—";
  const s = (Date.now() - new Date(iso).getTime()) / 1000;
  if (s < 60) return "hozirgina";
  if (s < 3600) return `${Math.floor(s / 60)} daqiqa oldin`;
  if (s < 86400) return `${Math.floor(s / 3600)} soat oldin`;
  if (s < 2592000) return `${Math.floor(s / 86400)} kun oldin`;
  return `${Math.floor(s / 2592000)} oy oldin`;
}

export default function Clients() {
  const nav = useNavigate();
  const ents = useList("/branches");
  const facs = useList("/zones");
  const { data, loading, error, reload } = useList("/auth/clients");

  const [allCams, setAllCams] = useState([]);
  const [camsLoaded, setCamsLoaded] = useState(false);
  const [q, setQ] = useState("");
  const [form, setForm] = useState(null);
  const [created, setCreated] = useState(null);
  const [busy, setBusy] = useState(false);
  const [formError, setFormError] = useState(null);

  async function ensureCams() {
    if (camsLoaded) return;
    try { const r = await apiFetch("/cameras?limit=1000"); setAllCams(r || []); setCamsLoaded(true); } catch { /* ignore */ }
  }

  function openNew() { setForm({ name: "", scope: { all: true } }); setFormError(null); ensureCams(); }
  function openEdit(c) {
    setForm({ id: c.id, name: c.name, scope: c.scopes?.all ? { all: true } : { branches: c.scopes?.branches || [], zones: c.scopes?.zones || [], cameras: c.scopes?.cameras || [] } });
    setFormError(null); ensureCams();
  }

  const scopeIsEmpty = (sc) => !sc.all && !(sc.branches?.length || sc.zones?.length || sc.cameras?.length);

  async function save() {
    if (!form.name.trim()) { setFormError("Nom kiriting"); return; }
    if (scopeIsEmpty(form.scope)) { setFormError("Kamida bitta kamera/hudud/filial tanlang yoki «Barcha kameralar»ni belgilang"); return; }
    setBusy(true); setFormError(null);
    try {
      if (form.id) { await apiFetch(`/auth/clients/${form.id}`, { method: "PATCH", json: { name: form.name, scopes: form.scope } }); setForm(null); reload(); }
      else { const r = await apiFetch("/auth/clients", { method: "POST", json: { name: form.name, scopes: form.scope } }); setForm(null); setCreated(r); reload(); }
    } catch (e) { setFormError(e.message); } finally { setBusy(false); }
  }

  async function rotateKey() {
    if (!confirm("Yangi kalit yaratiladi va eski kalit DARHOL ishlamay qoladi. Davom etamizmi?")) return;
    setBusy(true); setFormError(null);
    try { const r = await apiFetch(`/auth/clients/${form.id}/rotate-key`, { method: "POST" }); setForm(null); setCreated(r); reload(); }
    catch (e) { setFormError(e.message); } finally { setBusy(false); }
  }

  async function rotateFor(c) {
    if (!confirm(`"${c.name}" uchun yangi kalit yaratiladi va eski kalit DARHOL ishlamay qoladi. Davom etamizmi?`)) return;
    try { const r = await apiFetch(`/auth/clients/${c.id}/rotate-key`, { method: "POST" }); setCreated(r); reload(); }
    catch (e) { alert(e.message); }
  }

  async function toggle(c) { await apiFetch(`/auth/clients/${c.id}`, { method: "PATCH", json: { enabled: !c.enabled } }); reload(); }
  async function remove(c) {
    if (!confirm(`"${c.name}" API mijozi o'chiriladi. Uning kaliti darhol ishlamay qoladi. Davom etamizmi?`)) return;
    await apiFetch(`/auth/clients/${c.id}`, { method: "DELETE" }); reload();
  }
  function tryInIntegration() { setConsumerKey(created.api_key); nav("/integration"); }

  function scopeLabel(sc) {
    if (sc?.all) return "Barcha kameralar";
    const parts = [];
    if (sc?.branches?.length) parts.push(`${sc.branches.length} filial`);
    if (sc?.zones?.length) parts.push(`${sc.zones.length} hudud`);
    if (sc?.cameras?.length) parts.push(`${sc.cameras.length} kamera`);
    return parts.join(", ") || "—";
  }

  const [tab, setTab] = useState("all");
  const entName = useMemo(() => Object.fromEntries(ents.data.map((e) => [e.id, e.name])), [ents.data]);
  const facName = useMemo(() => Object.fromEntries(facs.data.map((f) => [f.id, f.name])), [facs.data]);
  const inTab = (c, t) => t === "all" || activity(c).cls === t;
  const rows = useMemo(() => data.filter((c) => inTab(c, tab) && (!q.trim() || c.name.toLowerCase().includes(q.trim().toLowerCase()))),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [data, q, tab]);
  const count = (t) => data.filter((c) => inTab(c, t)).length;

  // Ruxsat yorliqlari — id'lar o'rniga NOMLAR
  function scopeChips(sc) {
    if (sc?.all) return [{ k: "all", icon: <IcCamera size={12} />, t: "Barcha kameralar" }];
    const out = [];
    for (const id of sc?.branches || []) out.push({ k: "e" + id, icon: <IcBranch size={12} />, t: entName[id] || "o'chirilgan filial" });
    for (const id of sc?.zones || []) out.push({ k: "f" + id, icon: <IcZone size={12} />, t: facName[id] || "o'chirilgan hudud" });
    if (sc?.cameras?.length) out.push({ k: "c", icon: <IcCamera size={12} />, t: `${sc.cameras.length} ta tanlangan kamera` });
    return out;
  }

  return (
    <div>
      <PageHeader
        eyebrow="Integratsiya"
        title="API mijozlar"
        subtitle="Tashqi tizimlar kameralarni API kalit orqali ko'radi. Har mijozga faqat ruxsat berilgan filial/hudud/kameralar ko'rinadi."
        tag={null}
        rightSlot={<button className="btn" onClick={openNew}><IcPlus size={16} />Yangi API mijoz</button>}
      />

      <div className="ent-summary card">
        <div className="es"><span className="v">{data.length}</span><span className="l">API mijoz</span></div>
        <div className="es"><span className="v" style={{ color: "var(--online)" }}>{count("on")}</span><span className="l">faol</span><em>oxirgi {STALE_DAYS} kun</em></div>
        <div className="es"><span className="v">{count("never")}</span><span className="l">hali ishlatilmagan</span></div>
        <div className="es"><span className="v" style={count("stale") ? { color: "var(--error)" } : undefined}>{count("stale")}</span><span className="l">jim</span><em>{STALE_DAYS}+ kun murojaatsiz</em></div>
      </div>

      {error && <div className="error-banner">{error}</div>}

      <div className="card clip cam-panel">
        <div className="cam-filters">
          <div className="search-inline">
            <IcSearch size={15} />
            <input className="input" placeholder="Mijoz nomi bo'yicha qidirish…" value={q} onChange={(e) => setQ(e.target.value)} />
          </div>
          <button className="btn ghost" onClick={() => nav("/integration")}><IcExternal size={15} />API hujjatlari</button>
        </div>
        <div className="list-tabs" role="tablist">
          {TABS.map(([k, l]) => (
            <button key={k} role="tab" aria-selected={tab === k} title={k === "stale" ? `${STALE_DAYS} kundan ko'p murojaat qilmagan` : undefined} className={`lt${tab === k ? " on" : ""}`} onClick={() => setTab(k)}>
              {l}<span className="n">{count(k)}</span>
            </button>
          ))}
        </div>
      </div>

      <div className="fac-grid">
        {rows.map((c) => {
          const act = activity(c);
          const pct = c.cameras_allowed ? Math.round((c.online_allowed / c.cameras_allowed) * 100) : 0;
          return (
            <div className={`card cli-card${c.enabled ? "" : " disabled"}`} key={c.id}>
              <div className="cl-head">
                <span className="cl-av" style={{ background: avColor(c.name) }}>{c.name.slice(0, 1).toUpperCase()}</span>
                <div className="cl-ttl">
                  <div className="nm" title={c.name}>{c.name}</div>
                  <div className="meta">yaratilgan {new Date(c.created_at).toLocaleDateString("ru-RU")}</div>
                </div>
                <span className={`cl-act ${act.cls}`} title={c.last_used_at ? new Date(c.last_used_at).toLocaleString("ru-RU") : ""}><i />{act.text}</span>
              </div>

              <div className="cl-scope">
                <div className="cap">Ruxsat</div>
                <div className="chips">
                  {scopeChips(c.scopes).map((x) => <span className="chip" key={x.k}>{x.icon}{x.t}</span>)}
                </div>
              </div>

              <div className="cl-stats">
                <div><b>{c.cameras_allowed}</b><span>kamera ko'ra oladi</span></div>
                <div><b className="on">{c.online_allowed}</b><span>hozir onlayn</span></div>
                <div><b>{relLong(c.last_used_at)}</b><span>oxirgi murojaat</span></div>
              </div>
              <div className="ent-bar" title={`${c.online_allowed} / ${c.cameras_allowed} onlayn`}><span style={{ width: `${pct}%` }} /></div>

              <div className="fc-foot">
                <button className="btn soft sm" onClick={() => openEdit(c)}><IcEdit size={14} />Ruxsat</button>
                <button className="btn soft sm" onClick={() => toggle(c)}>{c.enabled ? <><IcPause size={14} />O'chirish</> : <><IcPlay size={14} />Yoqish</>}</button>
                <div className="ec-act">
                  <button className="btn-sq" title="Kalitni yangilash (eskisi darhol ishlamay qoladi)" onClick={() => rotateFor(c)}><IcRefresh size={14} /></button>
                  <button className="btn-sq danger" title="Mijozni o'chirish" onClick={() => remove(c)}><IcTrash size={14} /></button>
                </div>
              </div>
            </div>
          );
        })}
      </div>

      {!loading && data.length === 0 && (
        <div className="empty">API mijozlar yo'q. Tashqi tizim kameralarni ko'rishi uchun «Yangi API mijoz» bilan kalit yarating.</div>
      )}
      {!loading && data.length > 0 && rows.length === 0 && <div className="empty">Bu bo'limda mijoz yo'q.</div>}

      {form && (
        <Modal wide title={form.id ? "API mijoz ruxsatini tahrirlash" : "Yangi API mijoz"} onClose={() => setForm(null)}
          footer={<>
            <button className="btn ghost" onClick={() => setForm(null)}>Bekor</button>
            <button className="btn" onClick={save} disabled={busy}>{busy ? "…" : form.id ? "Saqlash" : "Yaratish"}</button>
          </>}>
          {formError && <div className="error-banner">{formError}</div>}
          <div className="field">
            <label className="label">Nom (masalan: "Monitoring tizimi")</label>
            <input className="input" value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} autoFocus />
          </div>
          <ScopePicker branches={ents.data} zones={facs.data} cameras={allCams} value={form.scope} onChange={(scope) => setForm({ ...form, scope })} />
          {form.id && (
            <div style={{ borderTop: "1px solid var(--border)", marginTop: 18, paddingTop: 14 }}>
              <div style={{ fontSize: 12.5, color: "var(--dim)", marginBottom: 8, lineHeight: 1.5 }}>API kalit hash holda saqlanadi — uni qayta ko'rsatib bo'lmaydi. Kalitni yo'qotgan bo'lsangiz, yangisini yarating.</div>
              <button className="btn ghost" type="button" onClick={rotateKey} disabled={busy}>Kalitni yangilash</button>
            </div>
          )}
        </Modal>
      )}

      {created && (
        <Modal title="API kalit yaratildi" onClose={() => setCreated(null)}
          footer={<>
            <button className="btn ghost" onClick={() => setCreated(null)}>Yopish</button>
            <button className="btn" onClick={tryInIntegration}>Integratsiyada sinash <IcArrowRight size={15} /></button>
          </>}>
          <p style={{ fontSize: 13.5, color: "var(--dim)", marginTop: 0 }}>Bu kalit <b style={{ color: "var(--danger)" }}>faqat hozir</b> ko'rsatiladi — nusxalab oling.</p>
          <KeyBox value={created.api_key} />
          <p style={{ fontSize: 12.5, color: "var(--faint)", marginBottom: 6 }}>Ulanish namunalari:</p>
          <pre className="codeblock">{`# REST — kameralar ro'yxati
curl -H "X-API-Key: ${created.api_key}" \\
  ${window.location.origin}/api/v1/cameras

# WebSocket — real vaqt
${window.location.origin.replace(/^http/, "ws")}/ws/consumer?api_key=${created.api_key}`}</pre>
        </Modal>
      )}
    </div>
  );
}

function IcInfoWrap() { return <IcKey size={22} />; }

function KeyBox({ value }) {
  const [done, setDone] = useState(false);
  return (
    <div style={{ display: "flex", gap: 8, marginBottom: 16 }}>
      <div style={{ flex: 1, padding: "12px 14px", background: "var(--surface2)", border: "1px solid var(--accent)", borderRadius: 10, fontFamily: "var(--mono)", fontSize: 13, fontWeight: 600, letterSpacing: 0.3, color: "var(--text)", userSelect: "all", wordBreak: "break-all" }}>{value || "—"}</div>
      <button className="btn ghost" style={{ flex: "none" }} onClick={() => { navigator.clipboard?.writeText(value); setDone(true); setTimeout(() => setDone(false), 1400); }}>{done ? <IcCheck size={15} /> : <IcCopy size={15} />}{done ? "Olindi" : "Nusxa"}</button>
    </div>
  );
}
