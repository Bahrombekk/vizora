import { useMemo, useState } from "react";
import { IcArrowRight } from "./icons.jsx";

// Filial → hudud → kamera daraxti bo'yicha ruxsat (scope) tanlagich.
// value: { all: true }  yoki  { branches:[], zones:[], cameras:[] }
// Berilgan tugun belgilansa, undagi barcha pastki tugunlar avtomatik qamraladi.
// Filial/hududga biriktirilmagan kameralar "Biriktirilmagan" ostida ko'rinadi
// va faqat kamera darajasida tanlanadi.
export default function ScopePicker({ branches, zones, cameras, value, onChange }) {
  const [openEnt, setOpenEnt] = useState(() => new Set());
  const [openFac, setOpenFac] = useState(() => new Set());

  const all = !!value?.all;
  const entSel = useMemo(() => new Set(value?.branches || []), [value]);
  const facSel = useMemo(() => new Set(value?.zones || []), [value]);
  const camSel = useMemo(() => new Set(value?.cameras || []), [value]);

  const camsByFac = useMemo(() => {
    const m = {};
    for (const c of cameras) (m[c.zone_id || "__none__"] ||= []).push(c);
    return m;
  }, [cameras]);
  const facsByEnt = useMemo(() => {
    const m = {};
    for (const f of zones) (m[f.branch_id] ||= []).push(f);
    return m;
  }, [zones]);
  const facById = useMemo(() => Object.fromEntries(zones.map((f) => [f.id, f])), [zones]);

  const facTotal = (fid) => (camsByFac[fid] || []).length;
  const entTotal = (eid) => (facsByEnt[eid] || []).reduce((n, f) => n + facTotal(f.id), 0);

  const facSelCount = (fac) => {
    if (entSel.has(fac.branch_id) || facSel.has(fac.id)) return facTotal(fac.id);
    return (camsByFac[fac.id] || []).filter((c) => camSel.has(c.id)).length;
  };
  const entSelCount = (eid) => (facsByEnt[eid] || []).reduce((n, f) => n + facSelCount(f), 0);

  function emit(next) {
    onChange({ branches: [...next.ent], zones: [...next.fac], cameras: [...next.cam] });
  }

  function toggleEnt(eid) {
    const ent = new Set(entSel), fac = new Set(facSel), cam = new Set(camSel);
    if (ent.has(eid)) ent.delete(eid);
    else {
      ent.add(eid);
      for (const f of facsByEnt[eid] || []) {
        fac.delete(f.id);
        for (const c of camsByFac[f.id] || []) cam.delete(c.id);
      }
    }
    emit({ ent, fac, cam });
  }

  function toggleFac(fid) {
    const fac = facById[fid];
    if (entSel.has(fac.branch_id)) return;
    const ent = new Set(entSel), facS = new Set(facSel), cam = new Set(camSel);
    if (facS.has(fid)) facS.delete(fid);
    else { facS.add(fid); for (const c of camsByFac[fid] || []) cam.delete(c.id); }
    emit({ ent, fac: facS, cam });
  }

  function toggleCam(c) {
    if (c.zone_id && (entSel.has(facById[c.zone_id]?.branch_id) || facSel.has(c.zone_id))) return;
    const ent = new Set(entSel), fac = new Set(facSel), cam = new Set(camSel);
    cam.has(c.id) ? cam.delete(c.id) : cam.add(c.id);
    emit({ ent, fac, cam });
  }

  const selectedTotal = branches.reduce((n, e) => n + entSelCount(e.id), 0) +
    (camsByFac["__none__"] || []).filter((c) => camSel.has(c.id)).length;
  const grandTotal = cameras.length;
  const orphans = camsByFac["__none__"] || [];

  return (
    <div>
      <label className="checkbox-field" style={{ marginBottom: 14 }}>
        <input type="checkbox" checked={all}
          onChange={(e) => onChange(e.target.checked ? { all: true } : { branches: [], zones: [], cameras: [] })} />
        <span className="label" style={{ margin: 0 }}>Barcha kameralarga ruxsat</span>
      </label>

      {!all && (
        <>
          <div style={{ display: "flex", justifyContent: "space-between", fontSize: 12.5, color: "var(--dim)", marginBottom: 8 }}>
            <span>Ruxsat etilgan kameralarni tanlang</span>
            <span className="chip accent">{selectedTotal} / {grandTotal}</span>
          </div>
          <div style={{ maxHeight: 360, overflow: "auto", border: "1px solid var(--border)", borderRadius: "var(--radiusSm)" }}>
            {branches.length === 0 && orphans.length === 0 && <div className="empty" style={{ padding: 30 }}>Kameralar yo'q.</div>}
            {branches.map((ent) => {
              const facs = facsByEnt[ent.id] || [];
              const total = entTotal(ent.id), sel = entSelCount(ent.id);
              const checked = entSel.has(ent.id);
              const isOpen = openEnt.has(ent.id);
              return (
                <div key={ent.id} style={{ borderBottom: "1px solid var(--border)" }}>
                  <Row indent={0} open={isOpen} hasChildren={facs.length > 0}
                    onToggleOpen={() => setOpenEnt((s) => toggleSet(s, ent.id))}
                    checked={checked} indeterminate={!checked && sel > 0}
                    onCheck={() => toggleEnt(ent.id)}
                    label={ent.name} count={`${sel}/${total}`} bold />
                  {isOpen && facs.map((fac) => {
                    const ct = facTotal(fac.id), cs = facSelCount(fac);
                    const fc = entSel.has(ent.id) || facSel.has(fac.id);
                    const fopen = openFac.has(fac.id);
                    const cams = camsByFac[fac.id] || [];
                    return (
                      <div key={fac.id}>
                        <Row indent={1} open={fopen} hasChildren={cams.length > 0}
                          onToggleOpen={() => setOpenFac((s) => toggleSet(s, fac.id))}
                          checked={fc} indeterminate={!fc && cs > 0} disabled={entSel.has(ent.id)}
                          onCheck={() => toggleFac(fac.id)}
                          label={fac.name} count={`${cs}/${ct}`} />
                        {fopen && cams.map((c) => (
                          <Row key={c.id} indent={2} checked={fc || camSel.has(c.id)} disabled={fc}
                            onCheck={() => toggleCam(c)} label={c.name} />
                        ))}
                        {fopen && cams.length === 0 && <div style={{ padding: "8px 16px 8px 64px", fontSize: 12.5, color: "var(--faint)" }}>Kamera yo'q</div>}
                      </div>
                    );
                  })}
                </div>
              );
            })}
            {orphans.length > 0 && (
              <div style={{ borderBottom: "1px solid var(--border)" }}>
                <Row indent={0} open={openEnt.has("__none__")} hasChildren
                  onToggleOpen={() => setOpenEnt((s) => toggleSet(s, "__none__"))}
                  checked={false} indeterminate={orphans.some((c) => camSel.has(c.id))}
                  onCheck={() => {}} label="Biriktirilmagan" count={`${orphans.filter((c) => camSel.has(c.id)).length}/${orphans.length}`} bold />
                {openEnt.has("__none__") && orphans.map((c) => (
                  <Row key={c.id} indent={1} checked={camSel.has(c.id)} onCheck={() => toggleCam(c)} label={c.name} />
                ))}
              </div>
            )}
          </div>
        </>
      )}
    </div>
  );
}

const toggleSet = (s, id) => { const n = new Set(s); n.has(id) ? n.delete(id) : n.add(id); return n; };

function Row({ indent, open, hasChildren, onToggleOpen, checked, indeterminate, disabled, onCheck, label, count, bold }) {
  return (
    <div style={{ display: "flex", alignItems: "center", gap: 8, padding: "8px 12px", paddingLeft: 12 + indent * 22, background: indent === 0 ? "var(--surface2)" : "transparent" }}>
      {hasChildren ? (
        <button onClick={onToggleOpen} style={{ background: "none", border: "none", cursor: "pointer", color: "var(--dim)", display: "flex", padding: 0, width: 16 }}>
          <IcArrowRight size={14} style={{ transform: open ? "rotate(90deg)" : "none", transition: "transform .12s" }} />
        </button>
      ) : <span style={{ width: 16 }} />}
      <input type="checkbox" checked={checked} disabled={disabled}
        ref={(el) => { if (el) el.indeterminate = !!indeterminate; }}
        onChange={onCheck} style={{ width: "auto", cursor: disabled ? "default" : "pointer" }} />
      <span style={{ flex: 1, minWidth: 0, fontSize: 13.5, fontWeight: bold ? 600 : 400, whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis", color: disabled ? "var(--dim)" : "var(--text)" }}>{label}</span>
      {count && <span style={{ fontSize: 11.5, color: "var(--faint)", fontFamily: "var(--mono)" }}>{count}</span>}
    </div>
  );
}
