import type { Language } from '../types/streaming';
import { Icon } from './Icon';

export function LanguageSelector({ value, disabled, onChange }: { value: Language; disabled: boolean; onChange: (language: Language) => void }) {
  return <div className="source-language">
    <label htmlFor="language" className="text-xs text-slate-600 flex items-center gap-2 mb-2"><Icon name="globe" className="text-slate-400 size-3.5" />Recognition language</label>
    <select id="language" value={value} disabled={disabled} onChange={event => onChange(event.target.value as Language)} className="language-select">
      <option value="English">English</option><option value="Chinese">Mandarin Chinese</option><option value="Indonesian">Bahasa Indonesia</option>
    </select>
  </div>;
}
