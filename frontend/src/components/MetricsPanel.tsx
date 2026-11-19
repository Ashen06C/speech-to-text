import type { MetricsEvent, SegmentEvent, SystemInfo } from '../types/streaming';

const seconds = (value: number | null | undefined) => value == null ? '—' : `${value.toFixed(2)} s`;
const cpuPercent = (value: number | null | undefined) => typeof value === 'number' && Number.isFinite(value) && value >= 0 ? `${value.toFixed(1)}%` : 'Unavailable';
export function MetricsPanel({ metrics, segments, system, refreshedAt }: {
  metrics: MetricsEvent | null; segments: SegmentEvent[]; system: SystemInfo | null; refreshedAt: Date | null;
}) {
  const inference = metrics?.total_inference_time_seconds ?? (segments.length ? segments.reduce((sum, segment) => sum + segment.inference_time_seconds, 0) : null);
  return <section className="panel metrics-panel">
    <div className="flex flex-wrap items-center gap-2 mb-3"><h2 className="text-sm font-semibold text-slate-800">Session performance</h2><span className="ml-auto text-[10px] text-slate-400 uppercase tracking-wider">Measured by backend</span></div>
    <div className="grid grid-cols-2 lg:grid-cols-4 gap-3">
      {[['First transcript', seconds(metrics?.time_to_first_transcript_seconds), metrics ? 'From streaming start' : 'Available on completion'],
        ['Inference time', seconds(inference), 'Sum of processed windows'],
        ['Audio duration', seconds(metrics?.total_audio_duration_seconds), 'Received by the server'],
        ['Total processing time', seconds(metrics?.stream_duration_seconds), 'Includes audio transmission']].map(([label, value, hint]) => <div className="metric" key={label}><p>{label}</p><strong>{value}</strong><span>{hint}</span></div>)}
    </div>
    <div className="system-row">
      <span title="Overall CPU utilization across the machine, including other applications">System CPU <strong>{cpuPercent(system?.system_cpu_percent)}</strong></span>
      <span title="Backend process only; 100% is approximately one logical CPU, so this can exceed 100%. Child processes are excluded.">Backend CPU <strong>{cpuPercent(system?.process_cpu_percent)}</strong></span>
      <span>Process memory <strong>{system ? `${(system.process_ram_bytes / 1024 ** 2).toFixed(0)} MiB` : 'Unavailable'}</strong></span>
      <span className="ml-auto text-slate-400">{system && refreshedAt ? `Sampled ${refreshedAt.toLocaleTimeString()}` : 'System metrics unavailable'}</span>
    </div>
    {metrics && <details className="text-xs text-slate-500 mt-4"><summary className="cursor-pointer">Queue and finalization details</summary><div className="grid grid-cols-2 gap-3 mt-3"><p>Queue wait: {seconds(metrics.total_queue_wait_time_seconds)}</p><p>Last audio to final: {seconds(metrics.last_audio_to_final_seconds)}</p></div></details>}
  </section>;
}
