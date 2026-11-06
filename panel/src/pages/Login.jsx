import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { getMe, isAuthed, login } from "../api/client";
import { useTheme } from "../lib/theme.jsx";
import { useClock, uzDate } from "../components/ui.jsx";
import { IcArrowRight, IcEye, IcEyeOff, IcLock, IcLogo, IcMoon, IcSun, IcUsers, IcWarn } from "../components/icons.jsx";
import pkg from "../../package.json";

// "Meni esda saqlash" — faqat FOYDALANUVCHI NOMINI eslab qoladi (parol emas; sessiya
// HttpOnly cookie'da). localStorage yopiq bo'lishi mumkin (private rejim) — try/catch.
const REMEMBER_KEY = "vz_login_user";
const readRemembered = () => { try { return localStorage.getItem(REMEMBER_KEY) || ""; } catch { return ""; } };
const writeRemembered = (u) => { try { u ? localStorage.setItem(REMEMBER_KEY, u) : localStorage.removeItem(REMEMBER_KEY); } catch { /* yopiq */ } };

const POINTS = [
  ["Bitta oyna", "Barcha filial va hududlardagi kameralar bir joyda"],
  ["Jonli ko'rish", "Brauzerda kechikishsiz, qo'shimcha dastur o'rnatmasdan"],
  ["Holat nazorati", "Oflayn yoki oqim bermayotgan kamera darhol ko'rinadi"],
];

// Chap paneldagi bezak: kamera plitkalari mozaikasi (rasm o'rniga sof CSS)
const TILES = Array.from({ length: 12 }, (_, i) => i);

export default function Login() {
  const [username, setUsername] = useState(readRemembered);
  const [password, setPassword] = useState("");
  const [show, setShow] = useState(false);
  const [remember, setRemember] = useState(() => !!readRemembered());
  const [forgot, setForgot] = useState(false);
  const [error, setError] = useState(null);     // { msg, kind: auth|locked|server|network }
  const [busy, setBusy] = useState(false);
  const [caps, setCaps] = useState(false);
  const [shake, setShake] = useState(0);
  const [me, setMe] = useState(null);
  const nav = useNavigate();
  const { theme, toggleTheme } = useTheme();
  const d = uzDate(useClock());

  useEffect(() => { if (isAuthed()) getMe().then(setMe).catch(() => {}); }, []);

  async function submit(e) {
    e.preventDefault();
    setBusy(true); setError(null);
    try {
      await login(username.trim(), password);
      writeRemembered(remember ? username.trim() : "");
      nav("/");
    }
    catch (err) {
      setError({ msg: err.message, kind: err.kind || "auth" });
      setShake((n) => n + 1);
      if (err.kind === "auth") setPassword("");
    }
    finally { setBusy(false); }
  }
  const capsCheck = (e) => setCaps(!!e.getModifierState?.("CapsLock"));

  return (
    <div className="lg">
      {/* ===== Chap: brend paneli ===== */}
      <section className="lg-side" aria-hidden>
        <div className="lg-brand">
          <span className="brand-mark"><IcLogo size={20} /></span>
          <span className="brand-name">Vizora</span>
        </div>

        <div className="lg-mosaic">
          {TILES.map((i) => (
            <div className={`lg-tile t${i % 5}`} key={i}>
              <span className="lg-rec" />
              <span className="lg-cam">CAM-{String(i + 1).padStart(2, "0")}</span>
            </div>
          ))}
        </div>

        <div className="lg-copy">
          <h1>Kompaniyangiz<br /><em>har bir burchagi</em> — nazoratda.</h1>
          <ul>
            {POINTS.map(([t, s]) => (
              <li key={t}><b>{t}</b><span>{s}</span></li>
            ))}
          </ul>
        </div>

        <div className="lg-side-foot">
          <span>v{pkg.version}</span>
          <span>{d.weekday}, {d.date} · <b className="mono">{d.time}</b></span>
        </div>
      </section>

      {/* ===== O'ng: kirish formasi ===== */}
      <section className="lg-main">
        <button className="icon-btn lg-theme" onClick={toggleTheme} title="Mavzu">
          {theme === "dark" ? <IcSun size={17} /> : <IcMoon size={17} />}
        </button>

        <form className={`lg-form${shake ? " shake" : ""}`} key={shake} onSubmit={submit}>
          <div className="lg-form-brand">
            <span className="brand-mark"><IcLogo size={18} /></span>
            <span>Vizora</span>
          </div>
          <h2>Xush kelibsiz</h2>
          <p className="lg-intro">Davom etish uchun hisobingizga kiring.</p>

          {me && (
            <div className="lg-already">
              <span><b>{me.username}</b> sifatida kirgansiz</span>
              <button type="button" className="btn sm" onClick={() => nav("/")}>Panelga o'tish <IcArrowRight size={14} /></button>
            </div>
          )}
          {error && (
            <div className={`lg-err ${error.kind}`} role="alert">
              <IcWarn size={16} /><span>{error.msg}</span>
            </div>
          )}

          <label className="lg-label" htmlFor="login-user">Foydalanuvchi nomi</label>
          <div className="lg-input">
            <IcUsers size={18} />
            <input id="login-user" name="username" value={username} onChange={(e) => setUsername(e.target.value)}
              autoComplete="username" autoCapitalize="none" spellCheck={false} placeholder="masalan, operator"
              autoFocus={!username} required />
          </div>

          <label className="lg-label" htmlFor="login-pass">Parol</label>
          <div className="lg-input">
            <IcLock size={18} />
            <input id="login-pass" name="password" type={show ? "text" : "password"} value={password}
              onChange={(e) => setPassword(e.target.value)} autoComplete="current-password" placeholder="••••••••"
              onKeyDown={capsCheck} onKeyUp={capsCheck} onBlur={() => setCaps(false)}
              autoFocus={!!username} required />
            <button type="button" className="eye" onClick={() => setShow((v) => !v)}
              aria-label={show ? "Parolni yashirish" : "Parolni ko'rsatish"} title={show ? "Yashirish" : "Ko'rsatish"}>
              {show ? <IcEyeOff size={18} /> : <IcEye size={18} />}
            </button>
          </div>
          {caps && <div className="lg-caps"><IcWarn size={13} />Caps Lock yoqilgan</div>}

          <div className="lg-row">
            <label className="lg-remember">
              <input type="checkbox" checked={remember} onChange={(e) => setRemember(e.target.checked)} />
              Meni esda saqlash
            </label>
            <button type="button" className="link-btn" aria-expanded={forgot} onClick={() => setForgot((v) => !v)}>
              Parolni unutdingizmi?
            </button>
          </div>
          {/* O'z-o'zini tiklash yo'q (email/SMS kanali yo'q) — parolni administrator panelda o'zgartiradi */}
          {forgot && (
            <div className="lg-hint" role="status">
              Parolni tizim administratori tiklaydi — unga murojaat qiling.
            </div>
          )}

          <button className="lg-submit" disabled={busy || error?.kind === "locked"}>
            {busy ? <><span className="lg-spin" />Kirilmoqda…</> : <>Kirish <IcArrowRight size={17} /></>}
          </button>
          {error?.kind === "locked" && (
            <button type="button" className="link-btn lg-retry" onClick={() => setError(null)}>Qayta urinib ko'rish</button>
          )}
        </form>

        <p className="lg-legal">Faqat vakolatli xodimlar uchun. Barcha kirishlar qayd etiladi.</p>
      </section>
    </div>
  );
}
