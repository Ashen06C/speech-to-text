import { useEffect, useRef, useState } from 'react';
import { Icon } from './Icon';
import type { SegmentEvent, StreamStatus } from '../types/streaming';

export function TranscriptPanel({ text, segments, status, languageLabel }: {
  text: string; segments: SegmentEvent[]; status: StreamStatus; languageLabel: string;
}) {
  const [copyLabel, setCopyLabel] = useState('Copy transcript');
  const timer = useRef<ReturnType<typeof setTimeout> | undefined>(undefined);
  useEffect(() => () => clearTimeout(timer.current), []);
  useEffect(() => { setCopyLabel('Copy transcript'); }, [text]);
  async function copy() {
    try { await navigator.clipboard.writeText(text); setCopyLabel('Copied'); }
    catch { setCopyLabel('Unable to copy — select the text'); }
    clearTimeout(timer.current);
    timer.current = setTimeout(() => setCopyLabel('Copy transcript'), 2500);
  }
  const active = ['ready', 'streaming', 'processing'].includes(status);
  return <section className="panel transcript-panel flex flex-col overflow-hidden">
    <div className="transcript-header flex flex-wrap items-center justify-between gap-3 border-b border-slate-100">
      <div className="flex items-center gap-3"><span className="transcript-icon"><Icon name="wave" /></span><div><h2 className="font-semibold text-sm text-slate-800">Live transcript</h2><p className="text-[11px] text-slate-400 mt-1">Your words, as they arrive</p></div></div>
      <button className="text-button" onClick={copy} disabled={!text}><Icon name={copyLabel === 'Copied' ? 'check' : 'copy'} className="size-3.5" />{copyLabel}</button>
    </div>
    <div className="transcript-tags flex gap-2 flex-wrap"><span className="subtle-tag">{languageLabel}</span><span className={`subtle-tag ${active ? 'text-teal-700 bg-teal-50' : ''}`}>{status === 'completed' ? 'Final transcript' : active ? 'Live session' : status === 'cancelled' ? 'Partial transcript · cancelled' : status === 'error' ? 'Partial transcript' : 'Waiting for audio'}</span></div>
    <div className="transcript-content" tabIndex={0} aria-label="Transcript text" aria-live="polite" aria-atomic="true" data-testid="transcript">
      {text ? <p className="transcript-text" lang={languageLabel === 'Mandarin Chinese' ? 'zh' : languageLabel === 'Bahasa Indonesia' ? 'id' : 'en'}>{text}{active && <span className="typing-cursor" />}</p> : <div className="transcript-empty"><div className="empty-wave"><Icon name="wave" className="size-10" /></div><h3>{active ? 'Listening to your recording' : 'A clear space for your words'}</h3><p>{active ? 'The first transcript will appear after an audio window is processed.' : 'Choose a recording and start streaming. Recognized speech will appear here.'}</p></div>}
    </div>
    <div className="transcript-footer border-t border-slate-100 flex justify-between text-[11px] text-slate-400"><span>{segments.length} {segments.length === 1 ? 'segment' : 'segments'} received</span><span>{text.length} characters</span></div>
    {segments.length > 0 && <details className="segment-details"><summary>View transcript segments</summary><ol>{segments.map(segment => <li key={segment.segment_index}><span className="font-mono text-[11px] text-slate-400">{segment.audio_start_seconds.toFixed(2)}–{segment.audio_end_seconds.toFixed(2)} s</span><p className="text-sm text-slate-600 mt-1">{segment.text || '(No speech recognized)'}</p></li>)}</ol></details>}
  </section>;
}
