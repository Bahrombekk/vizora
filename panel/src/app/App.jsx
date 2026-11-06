import { Suspense, lazy } from "react";
import { BrowserRouter, Navigate, Outlet, Route, Routes } from "react-router-dom";
import { isAuthed } from "../api/client";
import Layout from "./Layout";
import Login from "../pages/Login";

// Sahifalar alohida bo'laklarda (code-splitting): login sahifasi va birinchi ochilish
// pleyer/hls.js kabi og'ir kodni yuklamaydi — har sahifa birinchi kirilganda yuklanadi.
const Dashboard = lazy(() => import("../pages/Dashboard"));
const Monitoring = lazy(() => import("../pages/Monitoring"));
const Branches = lazy(() => import("../pages/Branches"));
const Zones = lazy(() => import("../pages/Zones"));
const EzvizAccounts = lazy(() => import("../pages/EzvizAccounts"));
const Isup = lazy(() => import("../pages/Isup"));
const Cameras = lazy(() => import("../pages/Cameras"));
const Wall = lazy(() => import("../pages/Wall"));
const Clients = lazy(() => import("../pages/Clients"));
const Integration = lazy(() => import("../pages/Integration"));
const Logs = lazy(() => import("../pages/Logs"));

function Protected() {
  return isAuthed() ? <Outlet /> : <Navigate to="/login" replace />;
}

// Marshrutlar xaritasi — yangi sahifa shu yerga (va Layout navigatsiyasiga) qo'shiladi.
export default function App() {
  return (
    <BrowserRouter>
      <Suspense fallback={null}>
        <Routes>
          <Route path="/login" element={<Login />} />
          <Route element={<Protected />}>
            <Route element={<Layout />}>
              <Route index element={<Dashboard />} />
              <Route path="monitoring" element={<Monitoring />} />
              <Route path="branches" element={<Branches />} />
              <Route path="zones" element={<Zones />} />
              <Route path="ezviz-accounts" element={<EzvizAccounts />} />
              <Route path="isup" element={<Isup />} />
              <Route path="cameras" element={<Cameras />} />
              <Route path="wall" element={<Wall />} />
              <Route path="clients" element={<Clients />} />
              <Route path="integration" element={<Integration />} />
              <Route path="logs" element={<Logs />} />
            </Route>
          </Route>
          <Route path="*" element={<Navigate to="/" replace />} />
        </Routes>
      </Suspense>
    </BrowserRouter>
  );
}
