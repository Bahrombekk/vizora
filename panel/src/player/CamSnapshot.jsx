import { useEffect, useState } from "react";

// Kameraning oxirgi kadri (JPEG, keshdan) — grid va Dashboard uchun arzon poster.
// 200 → rasm (10 daq'dan keyin yangilanadi), 202 → server hozir olmoqda (5s'da qayta),
// 204 → manba yo'q (boshqa so'ralmaydi).
export default function CamSnapshot({ id, active }) {
  const [url, setUrl] = useState(null);
  useEffect(() => {
    if (!active) { setUrl(null); return; }
    let alive = true, timer, obj;
    const load = async () => {
      let status = 0;
      try {
        const r = await fetch(`/api/cameras/${id}/snapshot`, { credentials: "same-origin" });
        status = r.status;
        if (alive && status === 200) {
          const blob = await r.blob();
          if (obj) URL.revokeObjectURL(obj);
          obj = URL.createObjectURL(blob);
          setUrl(obj);
        }
      } catch { /* tarmoq xatosi */ }
      if (alive && status !== 204) timer = setTimeout(load, status === 200 ? 600000 : 5000);
    };
    load();
    return () => { alive = false; clearTimeout(timer); if (obj) URL.revokeObjectURL(obj); };
  }, [id, active]);
  if (!url) return null;
  return <img src={url} alt="" style={{ position: "absolute", inset: 0, width: "100%", height: "100%", objectFit: "cover" }} />;
}
