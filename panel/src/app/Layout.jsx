import { Suspense, useEffect, useState } from "react";
import { Outlet, useLocation, useNavigate } from "react-router-dom";
import { logout, getMe, getRole } from "../api/client";
import { useTheme } from "../lib/theme.jsx";
import { useClock, uzDate } from "../components/ui.jsx";
import ChangePassword from "./ChangePassword";
import {
  IcActivity, IcBell, IcCamera, IcCloud, IcDashboard, IcBranch, IcZone, IcGrid, IcKey,
  IcList, IcLock, IcLogo, IcLogout, IcMoon, IcPlug, IcSearch, IcServer, IcSun,
} from "../components/icons.jsx";

// Navigatsiya guruhlarga bo'lingan: "Kuzatuv" (kundalik ish) va "Sozlash" (kamdan-kam).
// Kompyuterda yuqori gorizontal panel, telefonda pastki tab-bar + chiqib keladigan menyu.
const GROUPS = [
  {
    title: "Kuzatuv",
    items: [
      { to: "/", title: "Umumiy", Icon: IcDashboard, end: true },
      { to: "/monitoring", title: "Monitoring", Icon: IcActivity },
      { to: "/cameras", title: "Kameralar", Icon: IcCamera },
      { to: "/wall", title: "Jonli devor", Icon: IcGrid },
    ],
  },
  {
    title: "Tuzilma",
    items: [
      { to: "/branches", title: "Filiallar", Icon: IcBranch },
      { to: "/zones", title: "Hududlar", Icon: IcZone },
    ],
  },
  {
    title: "Ulanishlar",
    items: [
      { to: "/ezviz-accounts", title: "Bulut hisoblar", Icon: IcCloud },
      { to: "/isup", title: "ISUP", Icon: IcServer },
      { to: "/clients", title: "API mijozlar", Icon: IcKey },
      { to: "/integration", title: "Integratsiya", Icon: IcPlug },
      { to: "/logs", title: "Jurnal", Icon: IcList },
    ],
  },
];
const NAV = GROUPS.flatMap((g) => g.items);

// Telefonda pastki tab-bar'dagi asosiy sahifalar (qolganlari "Menyu" orqali)
const TABS = ["/", "/monitoring", "/cameras", "/wall"];

export default function Layout() {
  const nav = useNavigate();
  const { pathname } = useLocation();
  const { theme, toggleTheme } = useTheme();
  const [me, setMe] = useState(null);
  const [pwOpen, setPwOpen] = useState(false);
  const [menuOpen, setMenuOpen] = useState(false);   // telefon/planshet: chiqib keladigan menyu
  const now = useClock();
  const d = uzDate(now);

  useEffect(() => { getMe().then(setMe).catch(() => {}); }, []);
  useEffect(() => { setMenuOpen(false); }, [pathname]);
  useEffect(() => {
    if (!menuOpen) return;
    const onKey = (e) => e.key === "Escape" && setMenuOpen(false);
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [menuOpen]);

  const username = me?.username || "admin";
  const isActive = (item) => (item.end ? pathname === "/" : pathname.startsWith(item.to));
  const current = NAV.find(isActive);

  return (
    <div className="app-layout">
      {/* ===== Yuqori navigatsiya paneli (masthead) ===== */}
      <header className="masthead">
        <button className="mh-brand" onClick={() => nav("/")} title="Vizora — bosh sahifa">
          <span className="brand-mark"><IcLogo size={18} /></span>
          <span className="brand-name">Vizora</span>
        </button>

        <nav className="mh-nav" aria-label="Bo'limlar">
          {GROUPS.map((g, gi) => (
            <div className={`mh-group g${gi}`} key={g.title}>
              {gi > 0 && <span className="mh-sep" aria-hidden />}
              {g.items.map((item) => (
                <button key={item.to} title={item.title} className={`mh-link${isActive(item) ? " active" : ""}`} onClick={() => nav(item.to)}>
                  <item.Icon size={16} />
                  <span>{item.title}</span>
                </button>
              ))}
            </div>
          ))}
        </nav>

        <div className="mh-actions">
          <button className="mh-icon" title={theme === "dark" ? "Yorug' mavzu" : "Qorong'i mavzu"} onClick={toggleTheme}>
            {theme === "dark" ? <IcSun size={17} /> : <IcMoon size={17} />}
          </button>
          <button className="mh-icon menu-btn" title="Menyu" aria-label="Menyu" onClick={() => setMenuOpen(true)}>
            <IcList size={18} />
          </button>
          <div className="mh-user">
            <span className="avatar">{username.slice(0, 2).toUpperCase()}</span>
            <span className="mh-user-txt">
              <b>{username}</b>
              <small>{me?.role || getRole() || "Administrator"}</small>
            </span>
            <button className="mh-icon" title="Parolni o'zgartirish" onClick={() => setPwOpen(true)}><IcLock size={15} /></button>
            <button className="mh-icon" title="Chiqish" onClick={logout}><IcLogout size={15} /></button>
          </div>
        </div>
      </header>

      {/* ===== Ikkinchi qator: joriy bo'lim, qidiruv, sana ===== */}
      <div className="subbar">
        <div className="sb-crumb">
          <span className="sb-org">Kompaniya</span>
          <span className="sb-slash">/</span>
          <b>{current?.title || "Umumiy"}</b>
        </div>
        <div className="topbar-search">
          <IcSearch size={16} />
          <input placeholder="Kamera, filial yoki hudud qidirish…" />
          <span className="kbd">Ctrl K</span>
        </div>
        <div className="spacer" />
        <button className="icon-btn bell" style={{ width: 36, height: 36 }} title="Bildirishnomalar">
          <IcBell size={17} /><span className="dot" />
        </button>
        <div className="topbar-date">
          <span className="d2">{d.weekday}</span>
          <span className="d1">{d.date}</span>
          <span className="d3 mono">{d.time}</span>
        </div>
      </div>

      <div className="content">
        <main className="main">
          <Suspense fallback={null}><Outlet /></Suspense>
        </main>
      </div>

      {/* ===== Telefon: chiqib keladigan to'liq menyu ===== */}
      {menuOpen && <div className="nav-backdrop" onClick={() => setMenuOpen(false)} />}
      <aside className={`drawer${menuOpen ? " open" : ""}`} aria-hidden={!menuOpen}>
        <div className="drawer-head">
          <span className="brand-mark"><IcLogo size={18} /></span>
          <span className="brand-name">Vizora</span>
        </div>
        {GROUPS.map((g) => (
          <div className="drawer-group" key={g.title}>
            <div className="drawer-title">{g.title}</div>
            {g.items.map((item) => (
              <button key={item.to} className={`drawer-link${isActive(item) ? " active" : ""}`} onClick={() => nav(item.to)}>
                <item.Icon size={18} />{item.title}
              </button>
            ))}
          </div>
        ))}
        <div className="spacer" />
        <div className="drawer-foot">
          <span className="avatar">{username.slice(0, 2).toUpperCase()}</span>
          <b style={{ flex: 1 }}>{username}</b>
          <button className="mh-icon" title="Parolni o'zgartirish" onClick={() => setPwOpen(true)}><IcLock size={15} /></button>
          <button className="mh-icon" title="Chiqish" onClick={logout}><IcLogout size={15} /></button>
        </div>
      </aside>

      <nav className="tabbar" aria-label="Asosiy bo'limlar">
        {NAV.filter((n) => TABS.includes(n.to)).map((item) => (
          <button key={item.to} className={isActive(item) ? "on" : ""} onClick={() => nav(item.to)}>
            <item.Icon size={20} />
            <span>{item.title}</span>
          </button>
        ))}
        <button className={menuOpen ? "on" : ""} onClick={() => setMenuOpen(true)}>
          <IcList size={20} />
          <span>Menyu</span>
        </button>
      </nav>

      {pwOpen && <ChangePassword onClose={() => setPwOpen(false)} />}
    </div>
  );
}
