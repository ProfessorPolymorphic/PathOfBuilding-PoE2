import { useState } from 'react'

interface Props {
  onLoad: (code: string, character: string) => Promise<void>
  onCancel?: () => void
  // Names already in the history, offered as suggestions.
  characters: string[]
  // Pre-filled: re-pasting the character you're looking at needs no typing.
  defaultCharacter?: string
}

export function BuildLoader({ onLoad, onCancel, characters, defaultCharacter }: Props) {
  const [code, setCode] = useState('')
  const [character, setCharacter] = useState(defaultCharacter ?? '')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  async function submit(e: React.FormEvent) {
    e.preventDefault()
    const trimmed = code.trim()
    if (!trimmed) return
    setBusy(true)
    setError(null)
    try {
      await onLoad(trimmed, character)
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
    } finally {
      setBusy(false)
    }
  }

  return (
    <form className="loader panel" onSubmit={submit}>
      <h2>Load a build</h2>
      <p className="muted">
        Paste a Path of Building (PoE2) share code. In PoB: Import/Export Build → Generate, then copy.
      </p>
      <textarea
        value={code}
        onChange={(e) => setCode(e.target.value)}
        placeholder="eNrtPV132zayz9Wv4PE5vS92HQIk…"
        spellCheck={false}
        rows={6}
        aria-label="Build code"
        autoFocus
      />
      <label className="field">
        <span>Character</span>
        <input
          value={character}
          onChange={(e) => setCharacter(e.target.value)}
          list="known-characters"
          maxLength={60}
          placeholder="Character name (leave empty to file it under its class)"
          spellCheck={false}
        />
        <datalist id="known-characters">
          {characters.map((c) => <option key={c} value={c} />)}
        </datalist>
        <span className="small-print muted">
          The code doesn’t include the character’s name, so this is how the build joins that character’s history.
        </span>
      </label>
      {error && <p className="error" role="alert">{error}</p>}
      <div className="loader-actions">
        {onCancel && (
          <button type="button" className="ghost" onClick={onCancel} disabled={busy}>Cancel</button>
        )}
        <button type="submit" disabled={busy || !code.trim()}>
          {busy ? 'Loading build…' : 'Load build'}
        </button>
      </div>
    </form>
  )
}
