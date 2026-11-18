import type { StreamStatus } from '../types/streaming';
import { Icon } from './Icon';

export const statusLabels: Record<StreamStatus, string> = {
  idle: 'Ready when you are', connecting: 'Connecting to the backend…', ready: 'Connected. Preparing audio…',
  streaming: 'Sending audio', processing: 'Waiting for remaining transcription', completed: 'Transcription completed',
  cancelled: 'Streaming cancelled', error: 'Session interrupted',
};

export function StreamingControls({ status, connected, busy, canStart, hasAudio, progress, duration, elapsed, onStart, onStop, onReset }: {
  status: StreamStatus; connected: boolean; busy: boolean; canStart: boolean; hasAudio: boolean;
  progress: number; duration: number; elapsed: number; onStart: () => void; onStop: () => void; onReset: () => void;
}) {
  return <section className="panel streaming-controls-panel">
    <div className="section-heading"><span className="step-number">02</span><h2>Stream audio</h2></div>
    <button className="primary-button w-full" disabled={!canStart || busy} onClick={onStart}><Icon name="play" className="size-4" />Start Streaming<Icon name="arrow" className="ml-auto size-4" /></button>
    <div className="grid grid-cols-2 gap-2 mt-2"><button className="secondary-button" disabled={!busy} onClick={onStop}><Icon name="stop" className="size-3.5" />Stop Streaming</button><button className="secondary-button" disabled={busy || (!hasAudio && status === 'idle')} onClick={onReset}><Icon name="reset" className="size-3.5" />Reset</button></div>
    <div className="stream-progress border-t border-slate-100 mt-3 pt-3">
      <div className="flex items-center justify-between text-xs gap-2"><span className="text-slate-600" role="status" data-testid="stream-status">{statusLabels[status]}</span><span className="font-mono text-slate-500">{Math.round(progress * 100)}%</span></div>
      <div className="progress-track mt-3" role="progressbar" aria-label="Audio transmission progress" aria-valuenow={Math.round(progress * 100)} aria-valuemin={0} aria-valuemax={100}><div style={{ width: `${progress * 100}%` }} /></div>
      <div className="flex justify-between text-[11px] text-slate-400 mt-2"><span>Audio transmission</span><span>{(duration * progress).toFixed(1)} / {duration.toFixed(1)} s</span></div>
      <div className="flex justify-between text-xs mt-2 text-slate-500"><span>Transmission elapsed</span><span className="font-mono text-slate-700">{elapsed.toFixed(1)} s</span></div>
      <div className="flex justify-between text-xs mt-2 text-slate-500"><span>WebSocket</span><span className={connected ? 'text-teal-700' : ''}>{connected ? 'Connected' : status === 'connecting' ? 'Connecting' : 'Disconnected'}</span></div>
    </div>
  </section>;
}
