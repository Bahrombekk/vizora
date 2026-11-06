import { useCallback, useEffect, useState } from "react";
import { apiFetch } from "../api/client";

// Ro'yxatni yuklash + qayta yuklash uchun oddiy hook.
// pollMs berilsa, shu oraliqda jim qayta yuklaydi (holat yangilanishi uchun).
export function useList(path, { pollMs } = {}) {
  const [data, setData] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);

  const load = useCallback(
    (silent) => {
      if (!silent) setLoading(true);
      apiFetch(path)
        .then((d) => {
          setData(d || []);
          setError(null);
        })
        .catch((e) => setError(e.message))
        .finally(() => setLoading(false));
    },
    [path]
  );

  const reload = useCallback(() => load(false), [load]);

  useEffect(() => {
    load(false);
    if (!pollMs) return;
    const id = setInterval(() => load(true), pollMs);
    return () => clearInterval(id);
  }, [load, pollMs]);

  return { data, loading, error, reload, setError };
}
