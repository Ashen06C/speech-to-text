import { useCallback, useEffect, useState } from 'react';
import { API_BASE_URL } from '../services/websocketService';
import type { Health, SystemInfo } from '../types/streaming';

export function useBackendStatus() {
  const [health, setHealth] = useState<Health | null>(null);
  const [system, setSystem] = useState<SystemInfo | null>(null);
  const [status, setStatus] = useState<'checking' | 'online' | 'offline'>('checking');
  const [error, setError] = useState<string | null>(null);
  const [refreshedAt, setRefreshedAt] = useState<Date | null>(null);
  const [refreshKey, setRefreshKey] = useState(0);
  const refresh = useCallback(() => setRefreshKey(key => key + 1), []);

  useEffect(() => {
    let disposed = false;
    let request: AbortController | null = null;
    let timer: ReturnType<typeof setTimeout>;
    async function poll() {
      request = new AbortController();
      const timeout = setTimeout(() => request?.abort(), 5000);
      try {
        const response = await fetch(`${API_BASE_URL}/health`, { signal: request.signal });
        const body = await response.json() as Health;
        if ((response.status !== 200 && response.status !== 503) || typeof body.model_loaded !== 'boolean' ||
            typeof body.model !== 'string' || typeof body.device !== 'string') throw new Error('Unexpected health response. Check the backend URL.');
        if (!disposed) { setHealth(body); setStatus('online'); setError(null); }
        const details = await fetch(`${API_BASE_URL}/api/system`, { signal: request.signal });
        if (!details.ok) throw new Error('System metrics are unavailable.');
        const info = await details.json() as SystemInfo;
        if (!Number.isFinite(info.process_ram_bytes) || !Number.isFinite(info.total_ram_bytes)) throw new Error('Unexpected system metrics response.');
        if (!disposed) { setSystem(info); setRefreshedAt(new Date()); }
      } catch (error) {
        if (!disposed) {
          setStatus('offline'); setHealth(null); setSystem(null);
          setError(error instanceof Error && error.message.includes('response') ? error.message : 'Cannot reach the backend. Check the server and frontend URL configuration.');
        }
      } finally {
        clearTimeout(timeout);
        if (!disposed) timer = setTimeout(poll, 5000);
      }
    }
    void poll();
    return () => { disposed = true; request?.abort(); clearTimeout(timer); };
  }, [refreshKey]);

  return { health, system, status, error, refreshedAt, refresh };
}
