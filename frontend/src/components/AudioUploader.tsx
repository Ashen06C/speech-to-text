import { useRef, useState } from 'react';
import type { ReactNode } from 'react';
import { Icon } from './Icon';
import type { WavAudio } from '../types/streaming';

export function AudioUploader({ audio, busy, validating, error, onFile, selectionKey, children }: {
  audio: WavAudio | null; busy: boolean; validating: boolean; error: string | null;
  onFile: (file: File) => void; selectionKey: number; children?: ReactNode;
}) {
  const input = useRef<HTMLInputElement>(null);
  const [dragging, setDragging] = useState(false);
  const disabled = busy || validating;
  return <section className="panel audio-source-panel" aria-labelledby="audio-source-heading">
    <div className="section-heading"><span className="step-number">01</span><h2 id="audio-source-heading">Audio source</h2><span className="ml-auto subtle-tag">WAV</span></div>
    <input key={selectionKey} ref={input} id="audio-file" data-testid="audio-file" className="sr-only" type="file" accept=".wav,audio/wav,audio/x-wav" disabled={disabled} onChange={event => { const file = event.target.files?.[0]; if (file) onFile(file); event.target.value = ''; }} />
    <button type="button" className={`upload-zone ${dragging ? 'dragging' : ''}`} disabled={disabled}
      onClick={() => input.current?.click()} aria-label="Choose WAV file"
      onDragOver={event => { event.preventDefault(); if (!disabled) setDragging(true); }}
      onDragLeave={() => setDragging(false)} onDrop={event => { event.preventDefault(); setDragging(false); const file = event.dataTransfer.files[0]; if (!disabled && file) onFile(file); }}>
      <span className="upload-icon"><Icon name="upload" className="size-6" /></span>
      <strong>{validating ? 'Checking WAV format…' : audio ? 'Choose another recording' : 'Drop your audio file here'}</strong>
      <span>{validating ? 'Reading the audio header' : <>or <span className="text-teal-700 underline underline-offset-4">browse files</span> to get started</>}</span>
      <span className="text-[11px] mt-2">16 kHz · Mono · 16-bit PCM</span>
    </button>
    {error && <p className="error-message mt-4" role="alert">{error}</p>}
    {audio && <div className="audio-metadata" data-testid="audio-metadata">
      <div className="flex items-center gap-3"><span className="file-icon"><Icon name="file" /></span><div className="min-w-0"><p className="text-sm font-medium text-slate-800 truncate" title={audio.file.name}>{audio.file.name}</p><p className="text-xs text-slate-500 mt-1">{(audio.file.size / 1024).toFixed(1)} KB <span className="mx-1">·</span> {audio.durationSeconds.toFixed(2)} seconds</p></div><Icon name="check" className="ml-auto shrink-0 text-teal-600 size-4" /></div>
      <p className="mt-2 text-xs text-teal-700 flex items-center gap-1.5"><Icon name="check" className="size-3.5" />Validated and ready to stream</p>
    </div>}
    {children}
  </section>;
}
