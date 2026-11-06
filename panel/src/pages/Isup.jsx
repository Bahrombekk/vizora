import { useCallback, useEffect, useMemo, useState } from "react";
import Modal from "../components/Modal";
import KeyBox from "../components/KeyBox";
import ZoneIsupModal from "../components/ZoneIsupModal";
import { apiFetch, getRole } from "../api/client";
import { relTime } from "../lib/format";
import { PageHeader } from "../components/ui.jsx";
import {
  IcEdit, IcEye, IcKey, IcLock, IcPlus, IcRefresh, IcSearch, IcServer, IcTrash, IcWarn,
} from "../components/icons.jsx";

// ISUP: kalit HUDUD darajasida (bitta kalit bir nechta hududga yoki hududning o'ziga),
// kerak bo'lsa qurilmaning o'z kaliti. Ustunlik: bloklangan → qurilma → hudud →
// prefiks bo'yicha hudud (ro'yxatda yo'q qurilma) → zaxira ISUP_KEY → rad.
const SRC = {
  device: ["Qurilmaning eski kaliti", "accent"],
  assigned: ["Tanlangan kalit", "accent"],
  zone: ["Hudud standarti", ""],
  default: ["Zaxira kalit", "warn"],
  none: ["Kalit yo'q", "warn"],
  blocked: ["Bloklangan", "warn"],
};
const REJECT = {
  unknown: "kalit topilmadi — reestrga qo'shing yoki hudud prefiksini sozlang",
  blocked: "bloklangan",
  key_mismatch: "qurilmadagi Encryption Key mos emas",
  "no-id": "Device ID o'qilmadi",
};
const TABS = [["devices", "Qurilmalar"], ["keys", "Kalitlar"], ["zones", "Hududlar"]];

export default function Isup() {
  const admin = getRole() === "admin";
  const [tab, setTab] = useState("devices");
  const [ov, setOv] = useState(null);
  const [keys, setKeys] = useState([]);
  const [facs, setFacs] = useState([]);
  const [err, setErr] = useState(null);
  const [loading, setLoading] = useState(true);

  const load = useCallback(async () => {
    setErr(null);
    try {
      const [o, k, f] = await Promise.all([apiFetch("/isup/devices"), apiFetch("/isup/keys"), apiFetch("/isup/zones")]);
      setOv(o); setKeys(k); setFacs(f);
    } catch (e) { setErr(e.message); } finally { setLoading(false); }
  }, []);
  useEffect(() => { load(); const t = setInterval(load, 10000); return () => clearInterval(t); }, [load]);

  const devs = ov?.devices || [];
  const online = devs.filter((d) => d.online).length;
  const rejected = (ov?.rejected || []).filter((r) => r.age < 3600);

  return (
    <div>
      <PageHeader
        eyebrow="Bulutsiz ulanish"
        title="ISUP qurilmalar"
        subtitle="Hikvision qurilmalari ISUP 5.0 orqali serverga o'zi ulanadi. Kalit hududga beriladi — bitta kalit bir nechta hududga yoki hududning o'ziga; kerak bo'lsa qurilmaning o'z kaliti."
        tag={null}
        rightSlot={<button className="btn ghost" onClick={load}><IcRefresh size={16} /><span className="lbl">Yangilash</span></button>}
      />

      <div className="ent-summary card">
        <div className="es"><span className="v">{devs.length}</span><span className="l">qurilma</span></div>
        <div className="es"><span className="v" style={{ color: "var(--online)" }}>{online}</span><span className="l">ulangan</span></div>
        <div className="es"><span className="v">{keys.length}</span><span className="l">kalit</span><em>{facs.filter((f) => f.isup_key_id).length} hududda</em></div>
        <div className="es"><span className="v" style={rejected.length ? { color: "var(--error)" } : undefined}>{rejected.length}</span><span className="l">rad etilgan</span><em>so'nggi soat</em></div>
      </div>

      {err && <div className="error-banner">{err}</div>}
      {ov && !ov.bridge_ok && <div className="error-banner"><IcWarn size={15} /> isup-bridge javob bermayapti — qurilmalar holati va rad etilganlar ko'rinmaydi.</div>}
      {ov?.default_key_allowed && (
        <div className="isup-note"><IcWarn size={15} />
          <span><b>Zaxira kalit yoqiq.</b> Kaliti belgilanmagan qurilma umumiy <span className="mono">ISUP_KEY</span> bilan ulanadi (pilot uchun). Hamma qurilmaga hudud/qurilma kaliti berilgach serverda <span className="mono">ISUP_ALLOW_DEFAULT_KEY=false</span> qiling.</span>
        </div>
      )}

      <div className="card clip">
        <div className="list-tabs" role="tablist">
          {TABS.map(([k, l]) => (
            <button key={k} role="tab" aria-selected={tab === k} className={`lt${tab === k ? " on" : ""}`} onClick={() => setTab(k)}>
              {l}<span className="n">{k === "devices" ? devs.length : k === "keys" ? keys.length : facs.length}</span>
            </button>
          ))}
        </div>
        {loading ? <div className="empty">Yuklanmoqda…</div>
          : tab === "devices" ? <DevicesTab ov={ov} facs={facs} admin={admin} reload={load} rejected={rejected} />
          : tab === "keys" ? <KeysTab keys={keys} facs={facs} admin={admin} reload={load} />
          : <ZonesTab facs={facs} keys={keys} admin={admin} reload={load} />}
      </div>
    </div>
  );
}

// --- Qurilmalar -------------------------------------------------------------
function DevicesTab({ ov, facs, admin, reload, rejected }) {
  const [q, setQ] = useState("");
  const [form, setForm] = useState(null);
  const [shown, setShown] = useState(null);   // {device_id, key, source}
  const rows = useMemo(() => {
    const n = q.trim().toLowerCase();
    return (ov?.devices || []).filter((d) => !n || [d.device_id, d.label, d.zone_name, d.ip].some((x) => (x || "").toLowerCase().includes(n)));
  }, [ov, q]);

  async function reveal(d) {
    try { setShown(await apiFetch(`/isup/effective-key?device_id=${encodeURIComponent(d.device_id)}`)); }
    catch (e) { alert(e.message); }
  }
  async function remove(d) {
    if (!confirm(`${d.device_id} reestrdan o'chiriladi. U qayta ulansa (prefiks yoki zaxira kalit bilan) yana paydo bo'lishi mumkin.`)) return;
    try { await apiFetch(`/isup/devices/${d.id}`, { method: "DELETE" }); reload(); } catch (e) { alert(e.message); }
  }
  async function toggleBlock(d) {
    if (!d.blocked && !confirm(`${d.device_id} bloklanadi — darhol uziladi va qayta ulana olmaydi.`)) return;
    try { await apiFetch(`/isup/devices/${d.id}`, { method: "PATCH", json: { blocked: !d.blocked } }); reload(); } catch (e) { alert(e.message); }
  }

  return (
    <>
      {rejected.length > 0 && (
        <div className="isup-rej">
          <div className="hd"><IcLock size={14} />Ulanishga urinib rad etilganlar</div>
          {rejected.map((r) => (
            <div className="row" key={r.device_id}>
              <span className="mono">{r.device_id}</span>
              <span className="muted">{REJECT[r.reason] || r.reason} · {r.count} marta · {relTime(new Date(Date.now() - r.age * 1000).toISOString())}</span>
              {admin && !r.registered && r.reason === "unknown" && (
                <button className="btn soft sm" onClick={() => setForm({ device_id: r.device_id, label: "", zone_id: "", keymode: "zone", key: "" })}><IcPlus size={13} />Reestrga</button>
              )}
            </div>
          ))}
        </div>
      )}
      <div className="ent-tools" style={{ padding: "12px 14px 0" }}>
        <div className="search-inline"><IcSearch size={15} /><input className="input" placeholder="Device ID, nom, hudud yoki IP…" value={q} onChange={(e) => setQ(e.target.value)} /></div>
        {admin && <button className="btn" onClick={() => setForm({ device_id: "", label: "", zone_id: "", keymode: "zone", key: "" })}><IcPlus size={16} />Qurilma</button>}
      </div>
      <div className="table-scroll">
        <table className="table mon-table">
          <thead><tr><th>Qurilma</th><th>Holat</th><th>Hudud</th><th>Kalit</th><th>Kamera</th><th style={{ textAlign: "right" }}>Harakatlar</th></tr></thead>
          <tbody>
            {rows.map((d) => {
              const [sl, sc] = SRC[d.key_source] || [d.key_source, ""];
              return (
                <tr key={d.id} className={d.blocked ? "disabled" : ""}>
                  <td>
                    <div className="cam-cell">
                      <span className="nm mono">{d.device_id}</span>
                      <span className="src">{[d.label, d.model, d.source === "prefix" ? "prefiks bo'yicha o'zi qo'shildi" : d.source === "auto" ? "o'zi ulandi" : null].filter(Boolean).join(" · ")}</span>
                    </div>
                  </td>
                  <td className="st">
                    <span className={`badge ${d.online ? "on" : "off"}`}><span className="dot" />{d.online ? "Ulangan" : "Uzilgan"}</span>
                    <div className="muted" style={{ fontSize: 12, marginTop: 3 }}>{d.online ? d.ip : d.last_online_at ? relTime(d.last_online_at) : "hali ulanmagan"}</div>
                  </td>
                  <td className="muted" data-label="Hudud">{d.zone_name || "—"}</td>
                  <td data-label="Kalit">
                    <span className={`chip ${sc}`}>{sl}</span>{d.key_label && <div className="muted" style={{ fontSize: 11.5, marginTop: 3 }}>{d.key_label}</div>}
                    {d.online && d.connected_with && !d.connected_with.startsWith(d.key_source) && (
                      <div className="muted" style={{ fontSize: 11.5, marginTop: 3 }} title="Qurilma oxirgi marta shu kalit bilan kirgan">kirgan: {d.connected_with}</div>
                    )}
                  </td>
                  <td data-label="Kamera">{d.cameras}</td>
                  <td className="act"><div className="row-actions">
                    {admin && <button className="btn-sq" title="Qurilmaga yoziladigan kalit" onClick={() => reveal(d)}><IcEye size={15} /></button>}
                    {admin && <button className="btn-sq" title="Tahrirlash" onClick={() => setForm({ ...d, zone_id: d.zone_id || "", keymode: d.has_own_key ? "own" : "zone", key: "" })}><IcEdit size={15} /></button>}
                    {admin && <button className={`btn-sq${d.blocked ? "" : " danger"}`} title={d.blocked ? "Blokdan chiqarish" : "Bloklash"} onClick={() => toggleBlock(d)}><IcLock size={15} /></button>}
                    {admin && <button className="btn-sq danger" title="Reestrdan o'chirish" onClick={() => remove(d)}><IcTrash size={15} /></button>}
                  </div></td>
                </tr>
              );
            })}
            {rows.length === 0 && <tr><td colSpan={6} className="empty">Qurilma yo'q. Qurilma ulanganda (yoki ISUP kamera qo'shilganda) shu yerda paydo bo'ladi.</td></tr>}
          </tbody>
        </table>
      </div>
      {form && <DeviceForm form={form} setForm={setForm} facs={facs} onDone={() => { setForm(null); reload(); }} />}
      {shown && (
        <Modal title={<><IcKey size={17} />{shown.device_id} — qurilmaga yoziladigan kalit</>} onClose={() => setShown(null)}
          footer={<button className="btn" onClick={() => setShown(null)}>Yopish</button>}>
          {shown.key ? <KeyBox value={shown.key} note={`Manba: ${(SRC[shown.source] || [shown.source])[0]}. Qurilma → Platform Access → ISUP → Encryption Key.`} />
            : <div className="cf-test bad"><IcWarn size={14} />Bu qurilma uchun kalit yo'q ({(SRC[shown.source] || [shown.source])[0]}) — hududiga kalit bering yoki o'z kalitini yarating.</div>}
          <SetupHint ov={ov} deviceId={shown.device_id} />
        </Modal>
      )}
    </>
  );
}

function SetupHint({ ov, deviceId }) {
  return (
    <div className="cf-isup-steps" style={{ marginTop: 12 }}>
      <span>Protocol Version</span><b>v5.0</b>
      <span>Server Address</span><b className="mono">{ov?.server_host || "<server IP>"}</b>
      <span>Port</span><b className="mono">{ov?.server_port || 17660}</b>
      <span>Device ID</span><b className="mono">{deviceId || "—"}</b>
    </div>
  );
}

function DeviceForm({ form, setForm, facs, onDone }) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);
  const [result, setResult] = useState(null);
  const set = (p) => setForm((f) => ({ ...f, ...p }));
  const isNew = !form.id;

  async function save() {
    setBusy(true); setError(null);
    try {
      const fac = form.zone_id || null;
      let r;
      if (isNew) {
        const id = form.device_id.trim();
        if (!/^[A-Za-z0-9_-]{1,64}$/.test(id)) throw new Error("Device ID: faqat lotin harf, raqam, _ va -");
        r = await apiFetch("/isup/devices", { method: "POST", json: {
          device_id: id, label: form.label || null, zone_id: fac,
          own_key: form.keymode === "own", key: form.keymode === "own" && form.key ? form.key : null,
        } });
      } else {
        const payload = { label: form.label || null, zone_id: fac };
        if (form.keymode === "zone" && form.has_own_key) payload.own_key = "";
        if (form.keymode === "own" && (form.key || !form.has_own_key || form.regen)) payload.own_key = form.key || "generate";
        r = await apiFetch(`/isup/devices/${form.id}`, { method: "PATCH", json: payload });
      }
      if (r?.key) setResult(r.key); else onDone();
    } catch (e) { setError(e.message); } finally { setBusy(false); }
  }

  if (result) {
    return (
      <Modal title={<><IcKey size={17} />Qurilma kaliti</>} onClose={onDone} footer={<button className="btn" onClick={onDone}>Tayyor</button>}>
        <KeyBox value={result} note="Shu kalitni qurilmaning ISUP sozlamasiga (Encryption Key) yozing. Keyin ham «ko'z» tugmasi orqali ko'rish mumkin." />
      </Modal>
    );
  }
  const fac = facs.find((f) => f.id === form.zone_id);
  return (
    <Modal title={<>{isNew ? <IcPlus size={17} /> : <IcEdit size={17} />}{isNew ? "ISUP qurilma" : form.device_id}</>} onClose={() => setForm(null)}
      footer={<><button className="btn ghost" onClick={() => setForm(null)}>Bekor</button><button className="btn" onClick={save} disabled={busy}>{busy ? "Saqlanmoqda…" : "Saqlash"}</button></>}>
      {error && <div className="error-banner">{error}</div>}
      {isNew && (
        <div className="field">
          <label className="label">Device ID</label>
          <input className="input mono" value={form.device_id} placeholder="masalan: SCH1-KIRISH" onChange={(e) => set({ device_id: e.target.value.trim() })} autoFocus />
        </div>
      )}
      <div className="cf-grid">
        <div className="field">
          <label className="label">Nom <span className="opt">ixtiyoriy</span></label>
          <input className="input" value={form.label || ""} onChange={(e) => set({ label: e.target.value })} />
        </div>
        <div className="field">
          <label className="label">Hudud</label>
          <select className="select" value={form.zone_id} onChange={(e) => set({ zone_id: e.target.value })}>
            <option value="">— biriktirilmagan —</option>
            {facs.map((f) => <option key={f.id} value={f.id}>{f.branch_name ? `${f.branch_name} · ` : ""}{f.name}{f.isup_key_id ? "" : " (kalitsiz)"}</option>)}
          </select>
        </div>
      </div>
      <div className="field">
        <label className="label">Kalit</label>
        <div className="seg cf-seg">
          <button type="button" className={form.keymode === "zone" ? "on" : ""} onClick={() => set({ keymode: "zone" })}>Hudud kaliti</button>
          <button type="button" className={form.keymode === "own" ? "on" : ""} onClick={() => set({ keymode: "own" })}>O'z kaliti</button>
        </div>
        <div className="cf-hint">
          {form.keymode === "zone"
            ? (fac?.isup_key_id ? "Hududning kaliti ishlatiladi." : "Hududda kalit yo'q — zaxira kalit ishlatiladi (yoqiq bo'lsa), aks holda qurilma ulana olmaydi.")
            : "Faqat shu qurilmaga alohida kalit — hudud kalitidan ustun. Bo'sh qoldirilsa tasodifiy yaratiladi."}
        </div>
      </div>
      {form.keymode === "own" && (
        <div className="field">
          <input className="input mono" value={form.key} maxLength={32} placeholder={form.has_own_key ? "(bo'sh = o'zgarmaydi)" : "bo'sh = tasodifiy 16 belgi"} onChange={(e) => set({ key: e.target.value.trim() })} />
          {form.has_own_key && <label className="cf-check"><input type="checkbox" checked={!!form.regen} onChange={(e) => set({ regen: e.target.checked })} />Yangi tasodifiy kalit yaratish</label>}
        </div>
      )}
    </Modal>
  );
}

// --- Kalitlar ---------------------------------------------------------------
// Kalitlar "hovuzi": har kalit BO'SH (hech qaysi hududga berilmagan) yoki BAND (qaysi
// hududlarda, nechta qurilma va kamerada). Yangi kalit generatsiya qilinadi — qo'lda
// yozish ham mumkin. Belgilar qurilma web-interfeysiga qo'lda yoziladi, shuning uchun
// chalkash belgilar (0/O, 1/l/I) yo'q — backend generatori bilan bir xil alifbo.
const KEY_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnpqrstuvwxyz23456789";
function genKey(len = 16) {
  const a = new Uint32Array(len);
  crypto.getRandomValues(a);
  return Array.from(a, (x) => KEY_ALPHABET[x % KEY_ALPHABET.length]).join("");
}
const KEY_FILTERS = [["", "Hammasi"], ["free", "Bo'sh"], ["used", "Band"]];

function KeysTab({ keys, facs, admin, reload }) {
  const [form, setForm] = useState(null);     // {id?, label, key, count, zone_id}
  const [shown, setShown] = useState(null);   // {label, key, note} | {label, list:[{label,key}]}
  const [kf, setKf] = useState("");
  const [busy, setBusy] = useState(false);
  const rows = keys.filter((k) => !kf || (kf === "used" ? k.in_use : !k.in_use));
  const nFree = keys.filter((k) => !k.in_use).length;
  const count = Math.max(1, Math.min(50, Number(form?.count) || 1));

  async function save() {
    setBusy(true);
    try {
      if (form.id) {
        await apiFetch(`/isup/keys/${form.id}`, { method: "PATCH", json: { label: form.label } });
        setForm(null);
      } else {
        const made = [];
        for (let i = 0; i < count; i++) {
          const label = count > 1 ? `${form.label} ${i + 1}` : form.label;
          const r = await apiFetch("/isup/keys", { method: "POST", json: { label, key: count > 1 ? genKey() : form.key } });
          made.push({ id: r.id, label, key: r.key });
        }
        if (form.zone_id && count === 1) {
          const fac = facs.find((f) => f.id === form.zone_id);
          await apiFetch(`/isup/zones/${form.zone_id}`, { method: "PUT", json: { isup_key_id: made[0].id, isup_prefix: fac?.isup_prefix || null } });
        }
        setForm(null);
        setShown(count === 1
          ? { label: made[0].label, key: made[0].key, note: form.zone_id ? "Kalit yaratildi va hududga biriktirildi (band)." : "Kalit yaratildi (bo'sh). Hududga «Hududlar» kartasidagi ISUP sozlamasidan biriktiring." }
          : { label: `${made.length} ta kalit generatsiya qilindi`, list: made });
      }
      reload();
    } catch (e) { alert(e.message); } finally { setBusy(false); }
  }
  async function reveal(k) {
    try { const r = await apiFetch(`/isup/keys/${k.id}/reveal`); setShown({ label: k.label, key: r.key }); } catch (e) { alert(e.message); }
  }
  async function rotate(k) {
    const msg = k.in_use
      ? `"${k.label}" qayta generatsiya qilinadi.\n\n${k.zones.length} ta hududning ${k.devices} ta qurilmasi (${k.cameras} kamera) DARHOL uziladi va har birida yangi kalit yozilmaguncha ulana olmaydi. Davom etamizmi?`
      : `"${k.label}" uchun yangi qiymat generatsiya qilinadi (kalit bo'sh — hech narsa uzilmaydi).`;
    if (!confirm(msg)) return;
    try {
      const r = await apiFetch(`/isup/keys/${k.id}/rotate`, { method: "POST", json: {} });
      setShown({ label: k.label, key: r.key, note: k.in_use ? "Yangi kalit — barcha qurilmalarga yozing." : null });
      reload();
    } catch (e) { alert(e.message); }
  }
  async function remove(k) {
    const msg = k.in_use
      ? `"${k.label}" BAND: ${k.zones.length} ta hudud, ${k.cameras} kamera. O'chirilsa ular kalitsiz qoladi (zaxira kalitga o'tadi yoki ulana olmaydi). Davom etamizmi?`
      : `"${k.label}" o'chiriladi.`;
    if (!confirm(msg)) return;
    try { await apiFetch(`/isup/keys/${k.id}`, { method: "DELETE" }); reload(); } catch (e) { alert(e.message); }
  }

  const keyValid = /^[A-Za-z0-9]{8,32}$/.test(form?.key || "");
  return (
    <>
      <div className="ent-tools" style={{ padding: "12px 14px 0" }}>
        <div className="seg">
          {KEY_FILTERS.map(([v, l]) => (
            <button key={v || "all"} className={kf === v ? "on" : ""} onClick={() => setKf(v)}>
              {l} · {v === "free" ? nFree : v === "used" ? keys.length - nFree : keys.length}
            </button>
          ))}
        </div>
        <span style={{ flex: 1 }} />
        {admin && <button className="btn" onClick={() => setForm({ label: "", key: genKey(), count: 1, zone_id: "" })}><IcKey size={16} />Kalit generatsiya</button>}
      </div>
      <div className="table-scroll">
        <table className="table mon-table">
          <thead><tr><th>Kalit</th><th>Holat</th><th>Hududlar</th><th>Qurilma</th><th>Kamera</th><th>Yaratilgan</th><th style={{ textAlign: "right" }}>Harakatlar</th></tr></thead>
          <tbody>
            {rows.map((k) => (
              <tr key={k.id}>
                <td><div className="cam-cell"><span className="nm"><IcKey size={13} /> {k.label}</span><span className="src">{k.zone_name ? `«${k.zone_name}» hududi ichida` : "umumiy kalit"}{k.rotated_at ? ` · qayta generatsiya ${relTime(k.rotated_at)}` : ""}</span></div></td>
                <td data-label="Holat">{k.in_use ? <span className="chip accent">Band</span> : <span className="chip free">Bo'sh</span>}</td>
                <td data-label="Hududlar">{k.zones.length ? <div className="isup-chips">{k.zones.map((f) => <span key={f.id} className="chip" title={f.branch_name || ""}>{f.name}</span>)}</div> : <span className="muted">—</span>}</td>
                <td data-label="Qurilma">{k.devices}</td>
                <td data-label="Kamera"><b>{k.cameras}</b></td>
                <td className="muted" data-label="Yaratilgan">{relTime(k.created_at)}</td>
                <td className="act"><div className="row-actions">
                  {admin && <button className="btn-sq" title="Ko'rsatish" onClick={() => reveal(k)}><IcEye size={15} /></button>}
                  {admin && <button className="btn-sq" title="Nomini o'zgartirish" onClick={() => setForm({ id: k.id, label: k.label })}><IcEdit size={15} /></button>}
                  {admin && <button className="btn-sq" title="Qayta generatsiya (almashtirish)" onClick={() => rotate(k)}><IcRefresh size={15} /></button>}
                  {admin && <button className="btn-sq danger" title="O'chirish" onClick={() => remove(k)}><IcTrash size={15} /></button>}
                </div></td>
              </tr>
            ))}
            {rows.length === 0 && (
              <tr><td colSpan={7} className="empty">{keys.length ? "Bu filtrda kalit yo'q." : "Kalit yo'q. «Kalit generatsiya» bilan yarating — hozircha qurilmalar faqat zaxira kalit bilan ulanadi."}</td></tr>
            )}
          </tbody>
        </table>
      </div>
      {form && (
        <Modal title={<><IcKey size={17} />{form.id ? "Kalit nomi" : "Kalit generatsiya"}</>} onClose={() => setForm(null)}
          footer={<>
            <button className="btn ghost" onClick={() => setForm(null)}>Bekor</button>
            <button className="btn" onClick={save} disabled={busy || !form.label.trim() || (!form.id && count === 1 && !keyValid)}>
              {busy ? "…" : form.id ? "Saqlash" : count > 1 ? `${count} ta yaratish` : "Yaratish"}
            </button>
          </>}>
          <div className="field">
            <label className="label">Nom</label>
            <input className="input" value={form.label} placeholder="masalan: SCH hududi kaliti" onChange={(e) => setForm({ ...form, label: e.target.value })} autoFocus />
          </div>
          {!form.id && (
            <>
              <div className="cf-grid">
                <div className="field">
                  <label className="label">Nechta <span className="opt">1–50</span></label>
                  <input className="input" type="number" min={1} max={50} value={form.count} onChange={(e) => setForm({ ...form, count: e.target.value })} />
                </div>
                <div className="field">
                  <label className="label">Hududga biriktirish <span className="opt">ixtiyoriy</span></label>
                  <select className="select" value={form.zone_id} disabled={count > 1} onChange={(e) => setForm({ ...form, zone_id: e.target.value })}>
                    <option value="">— bo'sh qoladi —</option>
                    {facs.map((f) => <option key={f.id} value={f.id}>{f.name}{f.isup_key_id ? " (kaliti bor — almashadi)" : ""}</option>)}
                  </select>
                </div>
              </div>
              {count === 1 ? (
                <div className="field">
                  <label className="label">Kalit <span className="opt">8–32 lotin harf/raqam</span></label>
                  <div className="cf-url">
                    <input className={`input mono${keyValid ? "" : " invalid"}`} value={form.key} maxLength={32} onChange={(e) => setForm({ ...form, key: e.target.value.trim() })} />
                    <button type="button" className="btn soft" onClick={() => setForm({ ...form, key: genKey() })}><IcRefresh size={14} />Generatsiya</button>
                  </div>
                  <div className="cf-hint">Chalkash belgilarsiz (0/O, 1/l yo'q) — qurilmaga qo'lda yozish oson.</div>
                </div>
              ) : (
                <div className="cf-hint">{count} ta kalit «{form.label || "…"} 1…{count}» nomlari bilan tasodifiy generatsiya qilinadi va bo'sh holda qoladi.</div>
              )}
            </>
          )}
        </Modal>
      )}
      {shown && (
        <Modal title={<><IcKey size={17} />{shown.label}</>} onClose={() => setShown(null)} footer={<button className="btn" onClick={() => setShown(null)}>Yopish</button>}>
          {shown.list ? (
            <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
              {shown.list.map((x) => <div key={x.id}><div className="label" style={{ marginBottom: 4 }}>{x.label}</div><KeyBox value={x.key} /></div>)}
            </div>
          ) : <KeyBox value={shown.key} note={shown.note} />}
        </Modal>
      )}
    </>
  );
}

// --- Hududlar ---------------------------------------------------------------
// Har hududning ISUP sozlamasi bitta oynada ([[ZoneIsupModal]]) — bu yerda faqat
// umumiy ko'rinish: qaysi kalit (alohida/umumiy), prefiks, qurilmalar.
function ZonesTab({ facs, keys, admin, reload }) {
  const [q, setQ] = useState("");
  const [open, setOpen] = useState(null);
  const keyById = useMemo(() => Object.fromEntries(keys.map((k) => [k.id, k])), [keys]);
  const rows = useMemo(() => {
    const n = q.trim().toLowerCase();
    return facs.filter((f) => !n || [f.name, f.branch_name, f.isup_prefix].some((x) => (x || "").toLowerCase().includes(n)));
  }, [facs, q]);

  return (
    <>
      <div className="ent-tools" style={{ padding: "12px 14px 0" }}>
        <div className="search-inline"><IcSearch size={15} /><input className="input" placeholder="Hudud yoki filial…" value={q} onChange={(e) => setQ(e.target.value)} /></div>
      </div>
      <div className="table-scroll">
        <table className="table mon-table">
          <thead><tr><th>Hudud</th><th>Kalit</th><th>Prefiks</th><th>Qurilma</th><th style={{ textAlign: "right" }} /></tr></thead>
          <tbody>
            {rows.map((f) => {
              const k = keyById[f.isup_key_id];
              return (
                <tr key={f.id}>
                  <td><div className="cam-cell"><span className="nm">{f.name}</span><span className="src">{f.branch_name || ""}</span></div></td>
                  <td data-label="Kalit">
                    {k ? (
                      <div className="cam-cell">
                        <span className="nm"><IcKey size={13} /> {k.label}</span>
                        <span className="src">{k.zones.length > 1 ? `umumiy · ${k.zones.length} hudud` : "alohida"} · {k.cameras} kamera</span>
                      </div>
                    ) : <span className="chip warn">kalit yo'q</span>}
                  </td>
                  <td data-label="Prefiks">{f.isup_prefix ? <span className="mono">{f.isup_prefix}</span> : <span className="muted">—</span>}</td>
                  <td data-label="Qurilma">{f.devices}</td>
                  <td className="act"><div className="row-actions">
                    {admin && <button className="btn soft sm" onClick={() => setOpen(f.id)}><IcServer size={13} />Sozlash</button>}
                  </div></td>
                </tr>
              );
            })}
            {rows.length === 0 && <tr><td colSpan={5} className="empty">Hudud yo'q — avval «Hududlar» bo'limida qo'shing.</td></tr>}
          </tbody>
        </table>
      </div>
      {open && <ZoneIsupModal zoneId={open} onClose={() => setOpen(null)} onSaved={reload} />}
    </>
  );
}
