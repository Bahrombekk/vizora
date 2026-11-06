import { useEffect, useMemo, useState } from "react";
import Modal from "./Modal";
import KeyBox from "./KeyBox";
import { apiFetch } from "../api/client";
import { csvCell, relTime } from "../lib/format";
import { IcCheck, IcCopy, IcDownload, IcEye, IcEyeOff, IcKey, IcPlus, IcRefresh, IcServer, IcShield, IcTrash } from "./icons.jsx";

// Hududning ISUP sozlamasi:
//   1) HUDUD KALITI — bitta. Qurilma o'rnatuvchi uchun "umumiy" yo'l.
//   2) ULANISH MA'LUMOTLARI — "nechta kerak?" deb N ta tayyor juftlik (noyob Device ID +
//      alohida kalit) yaratiladi va o'rnatuvchiga beriladi. Kamera nomi so'ralmaydi:
//      qurilma ulanganda kamera o'zi qo'shiladi (vaqtinchalik nom, keyin o'zgartiriladi).
//      Har juftlik bitta qurilmaga — Device ID noyob, shuning uchun ikki marta ishlatilmaydi.
const STATUS = {
  waiting: ["Kutilmoqda", "", "hali ulanmagan"],
  used: ["Ulangan edi", "", "hozir uzilgan"],
  online: ["Ulangan", "on", "kamera qo'shilmoqda"],
  camera: ["Kamera qo'shildi", "on", ""],
  blocked: ["Bloklangan", "warn", ""],
};

function Sec({ icon, title, sub, right, children }) {
  return (
    <div className="cf-sec">
      <div className="cf-sec-h">
        <span className="n">{icon}</span>
        <div style={{ flex: 1, minWidth: 0 }}><b>{title}</b>{sub && <span>{sub}</span>}</div>
        {right}
      </div>
      {children}
    </div>
  );
}

function slotText(rows, host, port) {
  return [
    `ISUP 5.0 ulanish ma'lumotlari`,
    `Server Address: ${host}`, `Port: ${port}`, `Protocol Version: v5.0`, "",
    ...rows.map((r) => `Device ID: ${r.device_id}    Encryption Key: ${r.key}`),
  ].join("\n");
}

export default function ZoneIsupModal({ zoneId, onClose, onSaved }) {
  const [f, setF] = useState(null);
  const [ov, setOv] = useState(null);
  const [slots, setSlots] = useState(null);
  const [facKey, setFacKey] = useState(null);     // {label, key} — hudud kaliti (ochilganda)
  const [showFac, setShowFac] = useState(false);
  const [count, setCount] = useState(1);
  const [fresh, setFresh] = useState(new Set());  // endigina yaratilgan Device ID'lar
  const [shown, setShown] = useState({});         // Device ID -> kalit ko'rinadimi
  const [copied, setCopied] = useState(false);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState(null);

  async function load() {
    try {
      const [facs, o, s] = await Promise.all([
        apiFetch("/isup/zones"), apiFetch("/isup/devices"), apiFetch(`/isup/zones/${zoneId}/slots`),
      ]);
      const ff = facs.find((x) => x.id === zoneId);
      if (!ff) throw new Error("Hudud topilmadi");
      setF(ff); setOv(o); setSlots(s);
      if (ff.isup_key_id) {
        const r = await apiFetch(`/isup/zones/${zoneId}/key`);
        setFacKey({ label: r.label, key: r.key });
      } else setFacKey(null);
    } catch (e) { setErr(e.message); }
  }
  useEffect(() => { load(); const t = setInterval(() => apiFetch(`/isup/zones/${zoneId}/slots`).then(setSlots).catch(() => {}), 8000); return () => clearInterval(t); }, [zoneId]); // eslint-disable-line react-hooks/exhaustive-deps

  const host = ov?.server_host || "<server IP>";
  const port = ov?.server_port || 17660;
  const list = slots || [];
  const freshRows = useMemo(() => list.filter((s) => fresh.has(s.device_id)), [list, fresh]);
  const counts = useMemo(() => ({
    waiting: list.filter((s) => s.status === "waiting").length,
    camera: list.filter((s) => s.status === "camera").length,
  }), [list]);

  async function makeZoneKey() {
    if (f.isup_key_id && !confirm("Hudud kaliti qayta generatsiya qilinadi.\n\nShu kalit bilan ulangan qurilmalar DARHOL uziladi va yangi kalit yozilmaguncha ulanmaydi. (Ulanish ma'lumotlari bilan ulanganlarga ta'sir qilmaydi.)")) return;
    setBusy(true); setErr(null);
    try {
      if (f.isup_key_id && facKey) {
        const r = await apiFetch(`/isup/keys/${f.isup_key_id}/rotate`, { method: "POST", json: {} });
        setFacKey({ ...facKey, key: r.key });
      } else {
        const r = await apiFetch(`/isup/zones/${zoneId}/key`, { method: "POST" });
        setFacKey({ label: r.label, key: r.key });
      }
      setShowFac(true);
      await load(); onSaved?.();
    } catch (e) { setErr(e.message); } finally { setBusy(false); }
  }
  async function createSlots() {
    const n = Math.max(1, Math.min(50, Number(count) || 1));
    setBusy(true); setErr(null);
    try {
      const made = await apiFetch(`/isup/zones/${zoneId}/slots`, { method: "POST", json: { count: n } });
      setFresh(new Set(made.map((m) => m.device_id)));
      setShown((s) => ({ ...s, ...Object.fromEntries(made.map((m) => [m.device_id, true])) }));
      setSlots(await apiFetch(`/isup/zones/${zoneId}/slots`));
      onSaved?.();
    } catch (e) { setErr(e.message); } finally { setBusy(false); }
  }
  async function removeSlot(s) {
    if (s.cameras.length) { alert(`${s.device_id} da kamera bor («${s.cameras[0].name}») — avval kamerani o'chiring.`); return; }
    if (!confirm(`${s.device_id} ulanish ma'lumoti o'chiriladi — bu Device ID/kalit bilan qurilma ulana olmaydi.`)) return;
    try { await apiFetch(`/isup/devices/${s.id}`, { method: "DELETE" }); setSlots(await apiFetch(`/isup/zones/${zoneId}/slots`)); onSaved?.(); }
    catch (e) { setErr(e.message); }
  }
  async function copyRows(rows) {
    try { await navigator.clipboard.writeText(slotText(rows, host, port)); setCopied(true); setTimeout(() => setCopied(false), 1500); }
    catch { /* clipboard yo'q */ }
  }
  function download(rows) {
    const csv = ["Device ID;Encryption Key;Server;Port;Holat",
      ...rows.map((r) => [r.device_id, r.key, host, port, STATUS[r.status]?.[0] || r.status].map(csvCell).join(";"))].join("\r\n");
    const url = URL.createObjectURL(new Blob(["﻿" + csv], { type: "text/csv;charset=utf-8" }));
    const a = document.createElement("a");
    a.href = url; a.download = `isup-${(f?.code || "hudud").replace(/[^A-Za-z0-9_-]/g, "")}-${new Date().toISOString().slice(0, 10)}.csv`;
    document.body.appendChild(a); a.click(); a.remove(); URL.revokeObjectURL(url);
  }
  const giveable = list.filter((s) => s.key && s.status === "waiting");

  return (
    <Modal wide title={<><IcServer size={17} />ISUP — {f ? f.name : "…"}</>} onClose={onClose}
      footer={<button className="btn" onClick={onClose}>Yopish</button>}>
      {err && <div className="error-banner">{err}</div>}
      {!f || !slots ? <div className="empty">Yuklanmoqda…</div> : (
        <>
          <div className="isup-stat">
            <div><span>Hudud kaliti</span><b className={f.isup_key_id ? "on" : "off"}>{f.isup_key_id ? "Bor" : "Yo'q"}</b><em>{f.isup_key_id ? (facKey?.label || "") : "yaratish kerak"}</em></div>
            <div><span>Ulanish ma'lumotlari</span><b>{list.length}</b><em>{counts.waiting} kutilmoqda</em></div>
            <div><span>Kamera qo'shilgan</span><b className={counts.camera ? "on" : ""}>{counts.camera}</b><em>{list.filter((s) => s.online).length} hozir ulangan</em></div>
          </div>

          <Sec icon={<IcKey size={13} />} title="Hudud kaliti" sub="Bitta — hududning hamma qurilmasi uchun umumiy">
            {f.isup_key_id && facKey ? (
              <div className="isup-keyrow">
                {showFac ? <KeyBox value={facKey.key} /> : <button className="btn soft" onClick={() => setShowFac(true)}><IcEye size={14} />Kalitni ko'rsatish</button>}
                <button className="btn ghost" onClick={makeZoneKey} disabled={busy}><IcRefresh size={14} />Qayta generatsiya</button>
              </div>
            ) : (
              <div className="isup-keyrow">
                <span className="cf-hint" style={{ margin: 0, flex: 1 }}>Hududda kalit yo'q.</span>
                <button className="btn" onClick={makeZoneKey} disabled={busy}><IcKey size={14} />Hudud kalitini yaratish</button>
              </div>
            )}
          </Sec>

          <Sec icon={<IcShield size={13} />} title="Ulanish ma'lumotlari"
            sub="O'rnatuvchiga beriladi: har biri — noyob Device ID + alohida kalit, bitta qurilma uchun. Kamera nomi kerak emas: qurilma ulanganda kamera o'zi qo'shiladi."
            right={(
              <div className="isup-gen">
                <input className="input" type="number" min={1} max={50} value={count} onChange={(e) => setCount(e.target.value)} title="Nechta kerak" />
                <button className="btn sm" onClick={createSlots} disabled={busy}><IcPlus size={13} />Yaratish</button>
              </div>
            )}>
            {freshRows.length > 0 && (
              <div className="isup-done" style={{ marginBottom: 10 }}>
                <IcCheck size={18} />
                <div><b>{freshRows.length} ta yangi ulanish ma'lumoti tayyor</b><span>Nusxalab yoki faylga yuklab o'rnatuvchiga bering.</span></div>
                <div style={{ marginLeft: "auto", display: "flex", gap: 6 }}>
                  <button className="btn soft sm" onClick={() => copyRows(freshRows)}>{copied ? <IcCheck size={13} /> : <IcCopy size={13} />}Nusxalash</button>
                  <button className="btn soft sm" onClick={() => download(freshRows)}><IcDownload size={13} />Yuklab olish</button>
                </div>
              </div>
            )}
            {list.length === 0 ? (
              <div className="empty" style={{ padding: 14 }}>Hali yo'q. Nechta qurilma ulanishi kerak bo'lsa, sonini yozib «Yaratish» bosing.</div>
            ) : (
              <div className="isup-slots">
                <div className="h"><span>Device ID</span><span>Kalit</span><span>Holat</span><span /></div>
                {list.map((s) => {
                  const [sl, sc, sh] = STATUS[s.status] || [s.status, "", ""];
                  return (
                    <div key={s.id} className={`r${fresh.has(s.device_id) ? " new" : ""}`}>
                      <span className="mono">{s.device_id}</span>
                      <span className="k">
                        {s.key ? (
                          <>
                            <code>{shown[s.device_id] ? s.key : "••••••••••••"}</code>
                            <button className="btn-sq" title={shown[s.device_id] ? "Yashirish" : "Ko'rsatish"} onClick={() => setShown((m) => ({ ...m, [s.device_id]: !m[s.device_id] }))}>{shown[s.device_id] ? <IcEyeOff size={13} /> : <IcEye size={13} />}</button>
                          </>
                        ) : <span className="muted">hudud kaliti</span>}
                      </span>
                      <span className="st">
                        <span className={`badge ${sc === "on" ? "on" : sc === "warn" ? "err" : "off"}`}><span className="dot" />{sl}</span>
                        <em title={s.cameras.map((c) => c.name).join(", ")}>{s.cameras.length > 1 ? `NVR · ${s.cameras.length} ta kamera` : s.cameras.length ? s.cameras[0].name : s.online ? (s.ip || sh) : s.last_online_at ? relTime(s.last_online_at) : sh}</em>
                      </span>
                      <span className="a">
                        {s.key && <button className="btn-sq" title="Shu bittasini nusxalash" onClick={() => copyRows([s])}><IcCopy size={13} /></button>}
                        <button className="btn-sq danger" title="O'chirish" onClick={() => removeSlot(s)} disabled={s.cameras.length > 0}><IcTrash size={13} /></button>
                      </span>
                    </div>
                  );
                })}
              </div>
            )}
            {giveable.length > 0 && freshRows.length === 0 && (
              <div className="isup-keyrow" style={{ marginTop: 8 }}>
                <span className="cf-hint" style={{ margin: 0, flex: 1 }}>{giveable.length} ta ishlatilmagan ulanish ma'lumoti bor.</span>
                <button className="btn ghost sm" onClick={() => copyRows(giveable)}>{copied ? <IcCheck size={13} /> : <IcCopy size={13} />}Ishlatilmaganlarni nusxalash</button>
                <button className="btn ghost sm" onClick={() => download(giveable)}><IcDownload size={13} />Yuklab olish</button>
              </div>
            )}
          </Sec>

          <Sec icon={<IcServer size={13} />} title="Qurilmani sozlash (o'rnatuvchi uchun)" sub="Configuration → Network → Advanced → Platform Access → ISUP">
            <div className="cf-isup-steps">
              <span>Protocol Version</span><b>v5.0</b>
              <span>Server Address</span><b className="mono">{host}</b>
              <span>Port</span><b className="mono">{port}</b>
              <span>Device ID</span><b>ulanish ma'lumotidagi Device ID</b>
              <span>Encryption Key</span><b>o'sha qatordagi kalit</b>
            </div>
            <div className="cf-hint">Save → 10–20 s da «Registration Status: Online» — kamera panelda o'zi paydo bo'ladi («{f.name} — Device ID» nomi bilan), nomini keyin o'zgartirasiz. <b>NVR</b> bo'lsa bitta ulanish ma'lumoti yetadi — har kanali alohida kamera bo'lib qo'shiladi (NVR'dagi kanal nomi bilan).</div>
          </Sec>
        </>
      )}
    </Modal>
  );
}
