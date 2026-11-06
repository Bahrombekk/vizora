import { useState } from "react";
import Modal from "../components/Modal";
import { changePassword } from "../api/client";

const MIN_LEN = 8;  // backend'dagi PasswordChange bilan bir xil chegara

export default function ChangePassword({ onClose }) {
  const [cur, setCur] = useState("");
  const [next, setNext] = useState("");
  const [repeat, setRepeat] = useState("");
  const [error, setError] = useState(null);
  const [done, setDone] = useState(false);
  const [busy, setBusy] = useState(false);

  async function save() {
    setError(null);
    if (next.length < MIN_LEN) return setError(`Yangi parol kamida ${MIN_LEN} belgi bo'lishi kerak`);
    if (next !== repeat) return setError("Yangi parol takrori mos kelmadi");
    setBusy(true);
    try {
      await changePassword(cur, next);
      setDone(true);
    } catch (e) {
      setError(e.message);
    } finally {
      setBusy(false);
    }
  }

  if (done) {
    return (
      <Modal title="Parol o'zgartirildi" onClose={onClose}
        footer={<button className="btn" onClick={onClose}>Yopish</button>}>
        <p style={{ fontSize: 13.5, color: "var(--dim)", margin: 0, lineHeight: 1.6 }}>
          Parol yangilandi. Sessiya uzilmadi — qayta kirish shart emas.
          Keyingi safar <b style={{ color: "var(--text)" }}>yangi parol</b> bilan kiring.
        </p>
      </Modal>
    );
  }

  return (
    <Modal title="Parolni o'zgartirish" onClose={onClose}
      footer={<>
        <button className="btn ghost" onClick={onClose}>Bekor</button>
        <button className="btn" onClick={save} disabled={busy || !cur || !next}>
          {busy ? "…" : "Saqlash"}
        </button>
      </>}>
      {error && <div className="error-banner">{error}</div>}

      <div className="field">
        <label className="label">Joriy parol</label>
        <input className="input" type="password" value={cur} autoFocus
          autoComplete="current-password"
          onChange={(e) => setCur(e.target.value)} />
      </div>
      <div className="field">
        <label className="label">Yangi parol (kamida {MIN_LEN} belgi)</label>
        <input className="input" type="password" value={next}
          autoComplete="new-password"
          onChange={(e) => setNext(e.target.value)} />
      </div>
      <div className="field">
        <label className="label">Yangi parolni takrorlang</label>
        <input className="input" type="password" value={repeat}
          autoComplete="new-password"
          onChange={(e) => setRepeat(e.target.value)}
          onKeyDown={(e) => e.key === "Enter" && save()} />
      </div>

      <div style={{ fontSize: 12.5, color: "var(--faint)", lineHeight: 1.55 }}>
        Parol shu yerda o'zgaradi — <span style={{ fontFamily: "var(--mono)" }}>.env</span> dagi{" "}
        <span style={{ fontFamily: "var(--mono)" }}>ADMIN_PASSWORD</span> faqat birinchi ishga
        tushishda (baza bo'sh bo'lganda) ishlatiladi.
      </div>
    </Modal>
  );
}
