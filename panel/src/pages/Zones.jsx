import { useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";
import Modal from "../components/Modal";
import { apiFetch, getRole } from "../api/client";
import ZoneIsupModal from "../components/ZoneIsupModal";
import { useList } from "../lib/useList";
import { PageHeader, Donut } from "../components/ui.jsx";
import CamSnapshot from "../player/CamSnapshot";
import { IcCamera, IcCopy, IcEdit, IcBranch, IcZone, IcKey, IcMapPin, IcPlus, IcSearch, IcTrash } from "../components/icons.jsx";

const empty = { branch_id: "", name: "", code: "", location: "" };
const SORTS = [["name", "Nom (A → Z)"], ["cameras", "Kameralar soni"], ["branch", "Filial bo'yicha"], ["new", "Yangi qo'shilgan"]];
const fmtDate = (d) => { try { return new Date(d).toLocaleDateString("ru-RU"); } catch { return "—"; } };

export default function Zones() {
  const nav = useNavigate();
  const ents = useList("/branches");
  const { data, loading, error, reload } = useList("/zones");
  // Karta muqovasi uchun: har hududning onlayn kameralari (oxirgi kadrlar keshdan)
  const onlineCams = useList("/cameras?status=online&limit=500");
  const camsByFac = useMemo(() => {
    const m = {};
    for (const c of onlineCams.data) if (c.zone_id) (m[c.zone_id] ||= []).push(c);
    return m;
  }, [onlineCams.data]);
  // Filiallar sahifasidan kelish: /zones?branch_id=…
  const [filter, setFilter] = useState(() => new URLSearchParams(location.search).get("branch_id") || "");
  const [q, setQ] = useState("");
  const [sort, setSort] = useState("name");
  const [form, setForm] = useState(null);
  const [busy, setBusy] = useState(false);
  const admin = getRole() === "admin";
  const [isupFor, setIsupFor] = useState(null);   // hudud id — ISUP sozlamasi oynasi
  const [formError, setFormError] = useState(null);

  const entName = useMemo(() => Object.fromEntries(ents.data.map((e) => [e.id, e.name])), [ents.data]);
  const rows = useMemo(() => {
    const n = q.trim().toLowerCase();
    const r = data.filter((f) => {
      if (filter && f.branch_id !== filter) return false;
      if (n && ![f.name, f.code, entName[f.branch_id], f.location].some((x) => (x || "").toLowerCase().includes(n))) return false;
      return true;
    });
    return [...r].sort((a, b) =>
      sort === "cameras" ? b.cameras - a.cameras || a.name.localeCompare(b.name)
        : sort === "branch" ? (entName[a.branch_id] || "").localeCompare(entName[b.branch_id] || "") || a.name.localeCompare(b.name)
          : sort === "new" ? new Date(b.created_at) - new Date(a.created_at)
            : a.name.localeCompare(b.name));
  }, [data, filter, q, sort, entName]);

  const tot = useMemo(() => rows.reduce((t, f) => ({ cameras: t.cameras + f.cameras, online: t.online + f.online }), { cameras: 0, online: 0 }), [rows]);
  const locCount = useMemo(() => new Set(rows.map((f) => (f.location || "").trim()).filter(Boolean)).size, [rows]);

  async function save() {
    setBusy(true); setFormError(null);
    try {
      if (!form.branch_id) throw new Error("Filialni tanlang");
      if (!form.name.trim() || !form.code.trim()) throw new Error("Nom va kod majburiy");
      const payload = { name: form.name.trim(), code: form.code.trim(), location: form.location.trim() || null, branch_id: form.branch_id };
      if (form.id) await apiFetch(`/zones/${form.id}`, { method: "PATCH", json: payload });
      else await apiFetch("/zones", { method: "POST", json: payload });
      setForm(null); reload();
    } catch (e) { setFormError(e.message); } finally { setBusy(false); }
  }

  async function remove(f) {
    const extra = f.cameras ? `\n${f.cameras} ta kamera o'chmaydi — "Biriktirilmagan" bo'lib qoladi.` : "";
    if (!confirm(`"${f.name}" hududi o'chiriladi.${extra}\n\nDavom etamizmi?`)) return;
    await apiFetch(`/zones/${f.id}`, { method: "DELETE" });
    reload();
  }

  const openNew = () => { setForm({ ...empty, branch_id: filter || "" }); setFormError(null); };
  const filtered = filter ? entName[filter] : null;

  return (
    <div>
      <PageHeader
        eyebrow="Tashkiliy tuzilma"
        title={filtered ? `Hududlar — ${filtered}` : "Hududlar"}
        subtitle="Hudud filialga tegishli; kamera hududga biriktiriladi (ixtiyoriy). Hudud o'chirilsa kameralari o'chmaydi."
        tag={null}
        rightSlot={<button className="btn" onClick={openNew} disabled={!ents.data.length} title={ents.data.length ? "" : "Avval filial qo'shing"}><IcPlus size={16} />Yangi hudud</button>}
      />

      <div className="ent-summary card">
        <div className="es"><span className="v">{rows.length}</span><span className="l">hudud{filtered ? "" : ` · ${ents.data.length} filialda`}</span></div>
        <div className="es"><span className="v">{tot.cameras}</span><span className="l">kamera</span></div>
        <div className="es"><span className="v" style={{ color: "var(--online)" }}>{tot.online}</span><span className="l">onlayn</span>{tot.cameras > 0 && <em>{Math.round((tot.online / tot.cameras) * 100)}%</em>}</div>
        <div className="es"><span className="v">{locCount}</span><span className="l">manzil ko'rsatilgan</span></div>
      </div>

      {error && <div className="error-banner">{error}</div>}

      <div className="ent-tools">
        <div className="search-inline">
          <IcSearch size={15} />
          <input className="input" placeholder="Hudud nomi, kod, filial yoki manzil…" value={q} onChange={(e) => setQ(e.target.value)} />
        </div>
        <select className="select" value={filter} onChange={(e) => setFilter(e.target.value)}>
          <option value="">Barcha filiallar</option>
          {ents.data.map((e) => <option key={e.id} value={e.id}>{e.name}</option>)}
        </select>
        <select className="select" value={sort} onChange={(e) => setSort(e.target.value)} title="Saralash">
          {SORTS.map(([k, l]) => <option key={k} value={k}>{l}</option>)}
        </select>
      </div>

      <div className="fac-grid">
        {rows.map((f) => {
          const pct = f.cameras ? Math.round((f.online / f.cameras) * 100) : 0;
          return (
            <div className="card fac-card" key={f.id}>
              {/* Muqova: hudud kameralarining oxirgi kadrlari (1 katta + 2 kichik) */}
              <div className={`fc-cover n${Math.min(3, (camsByFac[f.id] || []).length)}`}
                onClick={() => f.cameras && nav(`/cameras?zone_id=${f.id}`)} role={f.cameras ? "link" : undefined}>
                {(camsByFac[f.id] || []).slice(0, 3).map((c) => (
                  <div className="fc-shot" key={c.id}><CamSnapshot id={c.id} active /></div>
                ))}
                {!(camsByFac[f.id] || []).length && (
                  <div className="fc-empty"><IcZone size={30} /><span>{f.cameras ? "Onlayn kamera yo'q" : "Kamera biriktirilmagan"}</span></div>
                )}
                <div className="fc-shade" />
                <div className="fc-top">
                  <span className="fc-code">{f.code}</span>
                  {f.cameras > 0 && (
                    <span className={`fc-live${f.online ? "" : " off"}`}><i />{f.online}/{f.cameras} onlayn</span>
                  )}
                </div>
                <div className="fc-name">
                  <div className="nm" title={f.name}>{f.name}</div>
                  <button className="fc-ent" onClick={(e) => { e.stopPropagation(); setFilter(f.branch_id); }} title="Shu filial hududlari">
                    <IcBranch size={12} />{entName[f.branch_id] || "—"}
                  </button>
                </div>
              </div>

              <div className="fc-body">
                <div className="fc-stats">
                  <Donut value={pct} size={58} stroke={6} color={f.cameras ? "var(--online)" : "var(--faint)"} label={f.cameras ? `${pct}%` : "—"} />
                  <div className="fc-nums">
                    <div><b>{f.cameras}</b><span>kamera</span></div>
                    <div><b className="on">{f.online}</b><span>onlayn</span></div>
                    <div><b className={f.cameras - f.online ? "off" : ""}>{f.cameras - f.online}</b><span>onlayn emas</span></div>
                  </div>
                </div>
                <div className="ec-loc"><IcMapPin size={13} />{f.location || <span className="faint">Manzil ko'rsatilmagan</span>}</div>
                <button type="button" className={`fc-isup${f.isup_key_id ? " on" : ""}`} onClick={() => setIsupFor(f.id)} disabled={!admin}
                  title={admin ? "ISUP sozlamasi: kalit, prefiks, qurilmalar" : "Faqat admin sozlaydi"}>
                  <span className="ic"><IcKey size={14} /></span>
                  <span className="tx"><b>ISUP</b>{f.isup_key_id ? <>kalit bor{f.isup_prefix ? <> · <span className="mono">{f.isup_prefix}</span></> : ""}</> : "kalit sozlanmagan"}</span>
                  {admin && <span className="go">Sozlash →</span>}
                </button>
                <div className="fc-foot">
                  <button className="btn soft sm" onClick={() => nav(`/cameras?zone_id=${f.id}`)} disabled={!f.cameras}><IcCamera size={14} />Kameralar</button>
                  <div className="ec-act">
                    <button className="btn-sq" title="Tahrirlash" onClick={() => { setForm({ id: f.id, name: f.name, code: f.code, location: f.location || "", branch_id: f.branch_id }); setFormError(null); }}><IcEdit size={14} /></button>
                    <button className="btn-sq" title="Nusxa (shu filialda yangi hudud)" onClick={() => { setForm({ name: `${f.name} (nusxa)`, code: "", location: f.location || "", branch_id: f.branch_id }); setFormError(null); }}><IcCopy size={14} /></button>
                    <button className="btn-sq danger" title="O'chirish" onClick={() => remove(f)}><IcTrash size={14} /></button>
                  </div>
                </div>
              </div>
            </div>
          );
        })}
      </div>

      {!loading && data.length === 0 && (
        <div className="empty">Hududlar yo'q.{!ents.data.length && " Avval filial qo'shing — hudud filialga tegishli bo'ladi."}</div>
      )}
      {!loading && data.length > 0 && rows.length === 0 && (
        <div className="empty">Hudud topilmadi. {(filter || q) && <button className="link-btn" onClick={() => { setFilter(""); setQ(""); }}>Filtrni tozalash</button>}</div>
      )}

      {form && (
        <Modal
          title={form.id ? "Hududni tahrirlash" : "Yangi hudud"}
          onClose={() => setForm(null)}
          footer={<>
            <button className="btn ghost" onClick={() => setForm(null)}>Bekor</button>
            <button className="btn" onClick={save} disabled={busy}>{busy ? "Saqlanmoqda…" : "Saqlash"}</button>
          </>}
        >
          {formError && <div className="error-banner">{formError}</div>}
          <div className="field">
            <label className="label">Filial</label>
            <select className="select" value={form.branch_id} onChange={(e) => setForm({ ...form, branch_id: e.target.value })}>
              <option value="">— tanlang —</option>
              {ents.data.map((e) => <option key={e.id} value={e.id}>{e.name}</option>)}
            </select>
          </div>
          <div className="field">
            <label className="label">Nom</label>
            <input className="input" value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} autoFocus />
          </div>
          <div className="field">
            <label className="label">Kod (noyob)</label>
            <input className="input" value={form.code} style={{ fontFamily: "var(--mono)" }} onChange={(e) => setForm({ ...form, code: e.target.value })} />
          </div>
          <div className="field">
            <label className="label">Manzil (ixtiyoriy)</label>
            <input className="input" value={form.location} placeholder="masalan: Toshkent sh., Chilonzor t." onChange={(e) => setForm({ ...form, location: e.target.value })}
              onKeyDown={(e) => e.key === "Enter" && save()} />
          </div>
        </Modal>
      )}
      {isupFor && <ZoneIsupModal zoneId={isupFor} onClose={() => setIsupFor(null)} onSaved={reload} />}
    </div>
  );
}
