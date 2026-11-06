import { useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";
import Modal from "../components/Modal";
import { apiFetch } from "../api/client";
import { useList } from "../lib/useList";
import { relTime } from "../lib/format";
import { PageHeader, Donut } from "../components/ui.jsx";
import { IcCamera, IcCloud, IcEdit, IcGlobe, IcPlus, IcRefresh, IcSearch, IcTrash, IcWarn } from "../components/icons.jsx";

const empty = { label: "", account: "", password: "", provider: "ezviz", region: "" };
const PROVIDER_LABEL = { ezviz: "EZVIZ", hik: "Hik-Connect" };
const REGION_PLACEHOLDER = { ezviz: "apiisgp.ezvizlife.com", hik: "api.hik-connect.com" };
const SORTS = [["name", "Nom (A → Z)"], ["cameras", "Kameralar soni"], ["provider", "Bulut turi"]];

export default function EzvizAccounts() {
  const nav = useNavigate();
  const { data, loading, error, reload } = useList("/ezviz-accounts");
  const [q, setQ] = useState("");
  const [pf, setPf] = useState("");
  const [sort, setSort] = useState("name");
  const [form, setForm] = useState(null);
  const [busy, setBusy] = useState(false);
  const [formError, setFormError] = useState(null);
  // Ulanishni tekshirish natijalari: id -> { busy } | { ok, devices, online, channels } | { ok:false, error }
  const [checks, setChecks] = useState({});

  const rows = useMemo(() => {
    const n = q.trim().toLowerCase();
    const r = data.filter((a) => {
      if (pf && a.provider !== pf) return false;
      if (n && ![a.label, a.account, a.region].some((x) => (x || "").toLowerCase().includes(n))) return false;
      return true;
    });
    return [...r].sort((a, b) =>
      sort === "cameras" ? b.cameras - a.cameras || a.label.localeCompare(b.label)
        : sort === "provider" ? a.provider.localeCompare(b.provider) || a.label.localeCompare(b.label)
          : a.label.localeCompare(b.label));
  }, [data, q, pf, sort]);

  const tot = useMemo(() => data.reduce((t, a) => ({
    cameras: t.cameras + a.cameras, online: t.online + a.online, noStream: t.noStream + a.no_stream,
  }), { cameras: 0, online: 0, noStream: 0 }), [data]);
  const ezCount = data.filter((a) => a.provider === "ezviz").length;
  const hikCount = data.length - ezCount;

  async function check(a) {
    setChecks((c) => ({ ...c, [a.id]: { busy: true } }));
    try {
      const r = await apiFetch(`/cameras/ezviz/devices?account_id=${a.id}`);
      setChecks((c) => ({
        ...c, [a.id]: r.ok
          ? { ok: true, devices: r.devices.length, online: r.devices.filter((d) => d.online).length, channels: r.channels.length, notAdded: r.devices.filter((d) => !d.already_added).length }
          : { ok: false, error: r.error || "Ulanib bo'lmadi" },
      }));
      reload();                                   // sessiya vaqti yangilanadi
    } catch (e) {
      setChecks((c) => ({ ...c, [a.id]: { ok: false, error: e.message } }));
    }
  }

  async function save() {
    if (!form.label.trim() || !form.account.trim()) { setFormError("Nom va login majburiy"); return; }
    if (!form.id && !form.password) { setFormError("Parol majburiy"); return; }
    setBusy(true); setFormError(null);
    try {
      const payload = { label: form.label.trim(), account: form.account.trim(), provider: form.provider, region: form.region.trim() || null };
      if (form.password) payload.password = form.password;
      if (form.id) await apiFetch(`/ezviz-accounts/${form.id}`, { method: "PATCH", json: payload });
      else await apiFetch("/ezviz-accounts", { method: "POST", json: { ...payload, password: form.password } });
      setForm(null); reload();
    } catch (e) { setFormError(e.message); } finally { setBusy(false); }
  }

  async function remove(a) {
    if (a.cameras) { alert(`"${a.label}" hisobi ${a.cameras} ta kamerada ishlatilmoqda — avval ularni o'chiring yoki boshqa hisobga o'tkazing.`); return; }
    if (!confirm(`"${a.label}" hisobi o'chiriladi. Davom etamizmi?`)) return;
    try { await apiFetch(`/ezviz-accounts/${a.id}`, { method: "DELETE" }); reload(); }
    catch (e) { alert(e.message); }
  }

  return (
    <div>
      <PageHeader
        eyebrow="Bulut ulanishlari"
        title="EZVIZ hisoblar"
        subtitle="EZVIZ / Hik-Connect bulut kameralari shu hisoblar orqali oqim oladi. Bitta hisobda ko'p kamera bo'lishi mumkin; parol shifrlangan holda saqlanadi."
        tag={null}
        rightSlot={<button className="btn" onClick={() => { setForm({ ...empty }); setFormError(null); }}><IcPlus size={16} />Yangi hisob</button>}
      />

      <div className="ent-summary card">
        <div className="es"><span className="v">{data.length}</span><span className="l">hisob</span><em>{ezCount} EZVIZ · {hikCount} Hik</em></div>
        <div className="es"><span className="v">{tot.cameras}</span><span className="l">bulut kamerasi</span></div>
        <div className="es"><span className="v" style={{ color: "var(--online)" }}>{tot.online}</span><span className="l">onlayn</span>{tot.cameras > 0 && <em>{Math.round((tot.online / tot.cameras) * 100)}%</em>}</div>
        <div className="es"><span className="v" style={tot.noStream ? { color: "var(--error)" } : undefined}>{tot.noStream}</span><span className="l">oqim yo'q</span></div>
      </div>

      {error && <div className="error-banner">{error}</div>}

      <div className="ent-tools">
        <div className="search-inline">
          <IcSearch size={15} />
          <input className="input" placeholder="Hisob nomi, login yoki region…" value={q} onChange={(e) => setQ(e.target.value)} />
        </div>
        <select className="select" value={pf} onChange={(e) => setPf(e.target.value)}>
          <option value="">Barcha bulutlar</option>
          <option value="ezviz">EZVIZ</option>
          <option value="hik">Hik-Connect</option>
        </select>
        <select className="select" value={sort} onChange={(e) => setSort(e.target.value)} title="Saralash">
          {SORTS.map(([k, l]) => <option key={k} value={k}>{l}</option>)}
        </select>
      </div>

      <div className="fac-grid">
        {rows.map((a) => {
          const pct = a.cameras ? Math.round((a.online / a.cameras) * 100) : 0;
          const ck = checks[a.id];
          const hik = a.provider === "hik";
          return (
            <div className={`card acc-card ${hik ? "hik" : "ez"}`} key={a.id}>
              <div className="ac-band">
                <div className="ac-logo"><IcCloud size={22} /></div>
                <div className="ac-ttl">
                  <div className="nm" title={a.label}>{a.label}</div>
                  <div className="lg" title={a.account}>{a.account}</div>
                </div>
                <span className="ac-prov">{PROVIDER_LABEL[a.provider] || a.provider}</span>
              </div>

              <div className="fc-body">
                <div className="fc-stats">
                  <Donut value={pct} size={58} stroke={6} color={a.cameras ? "var(--online)" : "var(--faint)"} label={a.cameras ? `${pct}%` : "—"} />
                  <div className="fc-nums">
                    <div><b>{a.cameras}</b><span>kamera</span></div>
                    <div><b className="on">{a.online}</b><span>onlayn</span></div>
                    <div><b className={a.no_stream ? "warn" : ""}>{a.no_stream}</b><span>oqim yo'q</span></div>
                  </div>
                </div>

                <div className="ac-meta">
                  <span><IcGlobe size={13} />{a.region || <i>standart region</i>}</span>
                  <span className={`ac-sess ${a.session_at ? "on" : ""}`} title={a.session_at ? new Date(a.session_at * 1000).toLocaleString("ru-RU") : "Hali login qilinmagan — birinchi oqim yoki tekshiruvda qilinadi"}>
                    <i />{a.session_at ? `sessiya · ${relTime(new Date(a.session_at * 1000).toISOString())}` : "sessiya yo'q"}
                  </span>
                </div>

                {ck && !ck.busy && (
                  <div className={`ac-check ${ck.ok ? "ok" : "bad"}`}>
                    {ck.ok
                      ? <>✓ Bulut javob berdi: <b>{ck.devices}</b> qurilma ({ck.online} onlayn){ck.channels ? `, ${ck.channels} kanal` : ""}{ck.notAdded ? ` · ${ck.notAdded} tasi hali qo'shilmagan` : ""}</>
                      : <><IcWarn size={14} />{ck.error}</>}
                  </div>
                )}

                <div className="fc-foot">
                  <button className="btn soft sm" onClick={() => check(a)} disabled={ck?.busy} title="Bulutga ulanib qurilmalar ro'yxatini olish">
                    <IcRefresh size={14} />{ck?.busy ? "Tekshirilmoqda…" : "Tekshirish"}
                  </button>
                  <button className="btn soft sm" onClick={() => nav(`/cameras?ezviz_account_id=${a.id}`)} disabled={!a.cameras}><IcCamera size={14} />Kameralar</button>
                  <div className="ec-act">
                    <button className="btn-sq" title="Tahrirlash" onClick={() => { setForm({ id: a.id, label: a.label, account: a.account, provider: a.provider || "ezviz", region: a.region || "", password: "" }); setFormError(null); }}><IcEdit size={14} /></button>
                    <button className="btn-sq danger" title={a.cameras ? `${a.cameras} ta kamerada ishlatilmoqda` : "O'chirish"} onClick={() => remove(a)}><IcTrash size={14} /></button>
                  </div>
                </div>
              </div>
            </div>
          );
        })}
      </div>

      {!loading && data.length === 0 && <div className="empty">Hisob yo'q. EZVIZ yoki Hik-Connect bulut kameralarini ulash uchun «Yangi hisob» qo'shing.</div>}
      {!loading && data.length > 0 && rows.length === 0 && <div className="empty">Hisob topilmadi.</div>}

      {form && (
        <Modal
          title={form.id ? "Hisobni tahrirlash" : "Yangi bulut hisobi"}
          onClose={() => setForm(null)}
          footer={<>
            <button className="btn ghost" onClick={() => setForm(null)}>Bekor</button>
            <button className="btn" onClick={save} disabled={busy}>{busy ? "Saqlanmoqda…" : "Saqlash"}</button>
          </>}
        >
          {formError && <div className="error-banner">{formError}</div>}
          <div className="field">
            <label className="label">Tur (bulut)</label>
            <div className="seg" style={{ width: "100%" }}>
              <button type="button" className={form.provider === "ezviz" ? "on" : ""} style={{ flex: 1 }} onClick={() => setForm({ ...form, provider: "ezviz" })}>EZVIZ</button>
              <button type="button" className={form.provider === "hik" ? "on" : ""} style={{ flex: 1 }} onClick={() => setForm({ ...form, provider: "hik" })}>Hik-Connect</button>
            </div>
            <div style={{ marginTop: 6, fontSize: 11.5, color: "var(--faint)" }}>EZVIZ — EZVIZ ilovasidagi hisob. Hik-Connect — Hikvision NVR/kameralar.</div>
          </div>
          <div className="field">
            <label className="label">Nom (masalan "Nurafshon EZVIZ")</label>
            <input className="input" value={form.label} onChange={(e) => setForm({ ...form, label: e.target.value })} autoFocus />
          </div>
          <div className="field">
            <label className="label">Login (email/telefon)</label>
            <input className="input" value={form.account} placeholder="egasi@example.com" autoComplete="off" onChange={(e) => setForm({ ...form, account: e.target.value })} />
          </div>
          <div className="field">
            <label className="label">Parol{form.id && <span style={{ color: "var(--online)", fontWeight: 400 }}> · saqlangan (bo'sh = o'zgarmaydi)</span>}</label>
            <input className="input" type="password" autoComplete="new-password" value={form.password} placeholder={form.id ? "(o'zgartirmaslik uchun bo'sh qoldiring)" : ""} onChange={(e) => setForm({ ...form, password: e.target.value })} />
          </div>
          <div className="field">
            <label className="label">Region (api domeni) — bo'sh = standart</label>
            <input className="input" value={form.region} placeholder={REGION_PLACEHOLDER[form.provider] || ""} onChange={(e) => setForm({ ...form, region: e.target.value })} />
          </div>
        </Modal>
      )}
    </div>
  );
}
