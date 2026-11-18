import { useRef, useState } from 'react';
import { AudioUploader } from './components/AudioUploader';
import { Header } from './components/Header';
import { LanguageSelector } from './components/LanguageSelector';
import { MetricsPanel } from './components/MetricsPanel';
import { StreamingControls } from './components/StreamingControls';
import { TranscriptPanel } from './components/TranscriptPanel';
import { Icon } from './components/Icon';
import { joinSegments, useAudioStreaming } from './hooks/useAudioStreaming';
import { useBackendStatus } from './hooks/useBackendStatus';
import { parseWav } from './services/audioService';
import type { Language, WavAudio } from './types/streaming';

const languageLabels: Record<Language, string> = { English: 'English', Chinese: 'Mandarin Chinese', Indonesian: 'Bahasa Indonesia' };

export default function App() {
  const [audio, setAudio] = useState<WavAudio | null>(null);
  const [language, setLanguage] = useState<Language>('English');
  const [sessionLanguage, setSessionLanguage] = useState<Language>('English');
  const [validating, setValidating] = useState(false);
  const [fileError, setFileError] = useState<string | null>(null);
  const [selectionKey, setSelectionKey] = useState(0);
  const selectionVersion = useRef(0);
  const backend = useBackendStatus();
  const stream = useAudioStreaming();
  async function chooseFile(file: File) {
    const version = ++selectionVersion.current;
    setValidating(true); setAudio(null); setFileError(null);
    try { const parsed = await parseWav(file); if (version === selectionVersion.current) setAudio(parsed); }
    catch (error) { if (version === selectionVersion.current) setFileError(error instanceof Error ? error.message : 'Unable to read WAV file.'); }
    finally { if (version === selectionVersion.current) setValidating(false); }
  }
  function reset() {
    selectionVersion.current++;
    stream.reset(); setAudio(null); setFileError(null); setValidating(false); setSelectionKey(key => key + 1);
  }
  const transcript = stream.finalTranscript ?? joinSegments(stream.segments, sessionLanguage);
  const progress = audio ? Math.min(1, stream.sentBytes / audio.dataBytes) : 0;
  return <div className="min-h-screen">
    <aside className="studio-rail" aria-hidden="true"><span className="rail-logo"><Icon name="wave" /></span><span className="rail-line" /><span className="rail-active"><Icon name="file" /></span><span className="rail-bottom">CPU</span></aside>
    <main className="workspace dashboard-workspace mx-auto max-w-[1480px]">
      <Header status={backend.status} health={backend.health} onRefresh={backend.refresh} />
      {backend.error && <div className="notice mb-5" role="alert">{backend.error}</div>}
      {backend.health && !backend.health.model_loaded && <div className="notice mb-5" role="alert">The backend is connected, but the ASR model is unavailable. Check the backend startup log before streaming.</div>}
      {stream.error && <div className="error-message mb-5" role="alert">{stream.error}</div>}
      <div className="dashboard-grid">
        <div className="dashboard-controls"><AudioUploader audio={audio} busy={stream.busy} validating={validating} error={fileError} onFile={file => void chooseFile(file)} selectionKey={selectionKey}>
          <LanguageSelector value={language} disabled={stream.busy} onChange={setLanguage} />
          </AudioUploader>
          <StreamingControls status={stream.status} connected={stream.connected} busy={stream.busy} hasAudio={!!audio} canStart={!!audio && !validating && backend.status === 'online' && !!backend.health?.model_loaded} progress={progress} duration={audio?.durationSeconds ?? 0} elapsed={stream.transmissionElapsed} onStart={() => { if (audio) { setSessionLanguage(language); stream.start(audio, language); } }} onStop={stream.stop} onReset={reset} />
        </div>
        <div className="dashboard-results"><TranscriptPanel text={transcript} segments={stream.segments} status={stream.status} languageLabel={languageLabels[sessionLanguage]} />
          <MetricsPanel metrics={stream.metrics} segments={stream.segments} system={backend.system} refreshedAt={backend.refreshedAt} />
        </div>
      </div>
    </main>
  </div>;
}
