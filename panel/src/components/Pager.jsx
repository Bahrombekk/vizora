import { IcChevronLeft, IcChevronRight } from "./icons.jsx";

// Raqamli sahifalagich: "1–20 / 162 ta" + 1 … 4 5 6 … 9 (Monitoring, Kameralar).
function pageList(cur, total) {
  const out = [];
  for (let i = 0; i < total; i++) {
    if (i === 0 || i === total - 1 || Math.abs(i - cur) <= 1) out.push(i);
    else if (out[out.length - 1] !== "…") out.push("…");
  }
  return out;
}

export default function Pager({ page, size, total, onPage, className = "" }) {
  if (!total) return null;
  const pages = Math.max(1, Math.ceil(total / size));
  return (
    <div className={`pager ${className}`}>
      <span className="range">{page * size + 1}–{Math.min(total, (page + 1) * size)} / {total} ta</span>
      {pages > 1 && (
        <div className="pages">
          <button className="btn-sq" disabled={page === 0} onClick={() => onPage(page - 1)} title="Oldingi"><IcChevronLeft size={15} /></button>
          {pageList(page, pages).map((p, k) => p === "…"
            ? <span key={`e${k}`} className="gap">…</span>
            : <button key={p} className={`pn${p === page ? " on" : ""}`} onClick={() => onPage(p)}>{p + 1}</button>)}
          <button className="btn-sq" disabled={page >= pages - 1} onClick={() => onPage(page + 1)} title="Keyingi"><IcChevronRight size={15} /></button>
        </div>
      )}
    </div>
  );
}
