import { Icon } from './Icon';
import type { Health } from '../types/streaming';

export function Header({ status, health, onRefresh }: {
  status: 'checking' | 'online' | 'offline'; health: Health | null; onRefresh: () => void;
}) {
  return <header className="dashboard-header">
    <div className="header-status-row flex flex-wrap items-center justify-between gap-3">
      <div className="brand"><span className="brand-mark"><Icon name="wave" /></span><span className="brand-divider" /> <span className="text-slate-500 text-lg sm:text-xl font-semibold tracking-normal">Real-Time Multilingual Speech-to-Text</span></div>
      <div className="flex flex-wrap items-center gap-2 text-xs">
        <span className={`status-pill ${status === 'online' ? 'positive' : ''}`}><span className={`status-dot ${status === 'checking' ? 'animate-pulse' : ''}`} />{status === 'online' ? 'Backend connected' : status === 'checking' ? 'Checking backend' : 'Backend offline'}</span>
        <span className={`status-pill ${health?.model_loaded ? 'positive' : ''}`}><Icon name="cpu" className="size-3.5" />{health?.model_loaded ? 'Model ready' : health ? 'Model unavailable' : 'Model unchecked'}</span>
        <button className="icon-button" onClick={onRefresh} aria-label="Refresh backend status" title="Refresh backend status"><Icon name="reset" className="size-4" /></button>
      </div>
    </div>
  </header>;
}
