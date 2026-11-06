import { useEffect, useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";
import Modal from "../components/Modal";
import { apiFetch } from "../api/client";
import { useList } from "../lib/useList";
import { PageHeader, Donut } from "../components/ui.jsx";
import CamSnapshot from "../player/CamSnapshot";
import { IcCamera, IcEdit, IcBranch, IcZone, IcPlus, IcSearch, IcTrash } from "../components/icons.jsx";

const empty = { name: "", code: "" };
const SORTS = [["name", "Nom (A → Z)"], ["cameras", "Kameralar soni"], ["zones", "Hududlar soni"], ["new", "Yangi qo'shilgan"]];
const fmtDate = (d) => { try { return new Date(d).toLocaleDateString("ru-RU"); } catch { return "—"; } };

// Karta muqovasi: kameralarning oxirgi kadrlari (1 katta + 2 kichik) — Hududlar bilan bir uslub
function Cover({ cams, code, cameras, online, name, sub, onOpen, emptyText, icon }) {
  const shots = cams.slice(0, 3);
  return (
    <div className={`fc-cover n${shots.length}`} onClick={onOpen} role={onOpen ? "link" : undefined}>
      {shots.map((c) => <div className="fc-shot" key={c.id}><CamSnapshot id={c.id} active /></div>)}
      {!shots.length && <div className="fc-empty">{icon}<span>{emptyText}</span></div>}
      <div className="fc-shade" />
      <div className="fc-top">
        {code ? <span className="fc-code">{code}</span> : <span />}
        {cameras > 0 && <span className={`fc-live${online ? "" : " off"}`}><i />{online}/{cameras} onlayn</span>}
      </div>
      <div className="fc-name">
        <div className="nm" title={name}>{name}</div>
        {sub && <div className="fc-sub">{sub}</div>}
      </div>
    </div>
  );
}

export default function Branches() {
  const nav = useNavigate();
  const { data, loading, error, reload } = useList("/branches");
  const [unassigned, setUnassigned] = useState(null);
  const [q, setQ] = useState("");
  const [sort, setSort] = useState("name");
  const [form, setForm] = useState(null);
  const [busy, setBusy] = useState(false);
  const [formError, setFormError] = useState(null);

  // Muqovalar uchun: onlayn kameralar (kadrlar keshdan) filialga hudud orqali bog'lanadi
  const facs = useList("/zones");
  const onlineCams = useList("/cameras?status=online&limit=500");
  const camsByEnt = useMemo(() => {
    const facEnt = Object.fromEntries(facs.data.map((f) => [f.id, f.branch_id]));
    const m = { _none: [] };
    for (const c of onlineCams.data) {
      const eid = c.zone_id ? facEnt[c.zone_id] : "_none";
      if (eid) (m[eid] ||= []).push(c);
    }
    return m;
  }, [facs.data, onlineCams.data]);
  const loadUnassigned = () => apiFetch("/branches/unassigned").then(setUnassigned).catch(() => {});
  useEffect(() => { loadUnassigned(); }, []);

  const rows = useMemo(() => {
    const needle = q.trim().toLowerCase();
    const r = data.filter((e) => !needle || e.name.toLowerCase().includes(needle) || (e.code || "").toLowerCase().includes(needle));
    return [...r].sort((a, b) =>
      sort === "cameras" ? b.cameras - a.cameras || a.name.localeCompare(b.name)
        : sort === "zones" ? b.zones - a.zones || a.name.localeCompare(b.name)
          : sort === "new" ? new Date(b.created_at) - new Date(a.created_at)
            : a.name.localeCompare(b.name));
  }, [data, q, sort]);

  const tot = useMemo(() => data.reduce((t, e) => ({
    zones: t.zones + e.zones, cameras: t.cameras + e.cameras, online: t.online + e.online,
  }), { zones: 0, cameras: 0, online: 0 }), [data]);

  async function save() {
    if (!form.name.trim() || !form.code.trim()) { setFormError("Nom va kod majburiy"); return; }
    setBusy(true); setFormError(null);
    try {
      const payload = { name: form.name.trim(), code: form.code.trim() };
      if (form.id) await apiFetch(`/branches/${form.id}`, { method: "PATCH", json: payload });
      else await apiFetch("/branches", { method: "POST", json: payload });
      setForm(null); reload();
    } catch (e) { setFormError(e.message); } finally { setBusy(false); }
  }

  async function remove(e) {
    const parts = [
      e.zones ? `${e.zones} ta hududi ham o'chiriladi` : null,
      e.cameras ? `${e.cameras} ta kamera o'chmaydi — "Biriktirilmagan" bo'lib qoladi` : null,
    ].filter(Boolean).join(";\n");
    if (!confirm(`"${e.name}" filiali o'chiriladi.${parts ? `\n${parts}.` : ""}\n\nDavom etamizmi?`)) return;
    await apiFetch(`/branches/${e.id}`, { method: "DELETE" });
    reload(); loadUnassigned();
  }

  const camsPct = (n) => (unassigned && tot.cameras + unassigned.cameras ? Math.round((n / (tot.cameras + unassigned.cameras)) * 100) : 0);

  return (
    <div>
      <PageHeader
        eyebrow="Tashkiliy tuzilma"
        title="Filiallar"
        subtitle="Ixtiyoriy: kameralarni filial → hudud bo'yicha guruhlash. Filialga kamera hudud orqali biriktiriladi."
        tag={null}
        rightSlot={
          <button className="btn" onClick={() => { setForm({ ...empty }); setFormError(null); }}><IcPlus size={16} />Yangi filial</button>
        }
      />

      {/* Qisqa xulosa — hammasi haqiqiy sonlar */}
      <div className="ent-summary card">
        <div className="es"><span className="v">{data.length}</span><span className="l">filial</span></div>
        <div className="es"><span className="v">{tot.zones}</span><span className="l">hudud</span></div>
        <div className="es"><span className="v">{tot.cameras}</span><span className="l">biriktirilgan kamera</span><em>{camsPct(tot.cameras)}%</em></div>
        <div className="es"><span className="v">{unassigned ? unassigned.cameras : "—"}</span><span className="l">biriktirilmagan</span>{unassigned && <em>{camsPct(unassigned.cameras)}%</em>}</div>
      </div>

      {error && <div className="error-banner">{error}</div>}

      <div className="ent-tools">
        <div className="search-inline">
          <IcSearch size={15} />
          <input className="input" placeholder="Filial nomi yoki kodi…" value={q} onChange={(e) => setQ(e.target.value)} />
        </div>
        <select className="select" value={sort} onChange={(e) => setSort(e.target.value)} title="Saralash">
          {SORTS.map(([k, l]) => <option key={k} value={k}>{l}</option>)}
        </select>
      </div>

      <div className="fac-grid">
        {rows.map((e) => {
          const pct = e.cameras ? Math.round((e.online / e.cameras) * 100) : 0;
          return (
            <div className="card fac-card" key={e.id}>
              <Cover cams={camsByEnt[e.id] || []} code={e.code} cameras={e.cameras} online={e.online}
                name={e.name} sub={<><IcZone size={12} />{e.zones} hudud · {fmtDate(e.created_at)}</>}
                onOpen={e.cameras ? () => nav(`/cameras?branch_id=${e.id}`) : undefined}
                icon={<IcBranch size={30} />}
                emptyText={e.cameras ? "Onlayn kamera yo'q" : e.zones ? "Hududlarda kamera yo'q" : "Hudud qo'shilmagan"} />
              <div className="fc-body">
                <div className="fc-stats">
                  <Donut value={pct} size={58} stroke={6} color={e.cameras ? "var(--online)" : "var(--faint)"} label={e.cameras ? `${pct}%` : "—"} />
                  <div className="fc-nums">
                    <div><b>{e.zones}</b><span>hudud</span></div>
                    <div><b>{e.cameras}</b><span>kamera</span></div>
                    <div><b className="on">{e.online}</b><span>onlayn</span></div>
                  </div>
                </div>
                <div className="fc-foot">
                  <button className="btn soft sm" onClick={() => nav(`/zones?branch_id=${e.id}`)} disabled={!e.zones}><IcZone size={14} />Hududlar</button>
                  <button className="btn soft sm" onClick={() => nav(`/cameras?branch_id=${e.id}`)} disabled={!e.cameras}><IcCamera size={14} />Kameralar</button>
                  <div className="ec-act">
                    <button className="btn-sq" title="Tahrirlash" onClick={() => { setForm({ id: e.id, name: e.name, code: e.code }); setFormError(null); }}><IcEdit size={14} /></button>
                    <button className="btn-sq danger" title="O'chirish" onClick={() => remove(e)}><IcTrash size={14} /></button>
                  </div>
                </div>
              </div>
            </div>
          );
        })}

        {/* Hech qaysi hududga biriktirilmagan kameralar — filial emas, lekin ko'rinishi kerak */}
        {!q.trim() && unassigned && unassigned.cameras > 0 && (
          <div className="card fac-card ghost">
            <Cover cams={camsByEnt._none || []} cameras={unassigned.cameras} online={unassigned.online}
              name="Biriktirilmagan" sub="Hech qaysi hududga tegishli emas"
              icon={<IcCamera size={30} />} emptyText="Onlayn kamera yo'q" />
            <div className="fc-body">
              <div className="fc-stats">
                <Donut value={unassigned.cameras ? Math.round((unassigned.online / unassigned.cameras) * 100) : 0} size={58} stroke={6} color="var(--online)" />
                <div className="fc-nums">
                  <div><b>{unassigned.cameras}</b><span>kamera</span></div>
                  <div><b className="on">{unassigned.online}</b><span>onlayn</span></div>
                  <div><b className="off">{unassigned.cameras - unassigned.online}</b><span>onlayn emas</span></div>
                </div>
              </div>
              <div className="fc-foot"><span className="hint">Kamerani tahrirlab hududni tanlang — shu filialga o'tadi</span></div>
            </div>
          </div>
        )}
      </div>

      {!loading && data.length === 0 && (
        <div className="empty">Filiallar yo'q. Ixtiyoriy — kerak bo'lsa «Yangi filial» bilan qo'shing.</div>
      )}
      {!loading && data.length > 0 && rows.length === 0 && <div className="empty">"{q}" bo'yicha filial topilmadi.</div>}

      {form && (
        <Modal
          title={form.id ? "Filialni tahrirlash" : "Yangi filial"}
          onClose={() => setForm(null)}
          footer={<>
            <button className="btn ghost" onClick={() => setForm(null)}>Bekor</button>
            <button className="btn" onClick={save} disabled={busy}>{busy ? "Saqlanmoqda…" : "Saqlash"}</button>
          </>}
        >
          {formError && <div className="error-banner">{formError}</div>}
          <div className="field">
            <label className="label">Nom</label>
            <input className="input" value={form.name} placeholder="masalan: O'zbekiston temir yo'llari" onChange={(e) => setForm({ ...form, name: e.target.value })} autoFocus />
          </div>
          <div className="field">
            <label className="label">Kod (noyob)</label>
            <input className="input" value={form.code} placeholder="masalan: HQ" style={{ fontFamily: "var(--mono)" }} onChange={(e) => setForm({ ...form, code: e.target.value })}
              onKeyDown={(e) => e.key === "Enter" && save()} />
          </div>
        </Modal>
      )}
    </div>
  );
}
