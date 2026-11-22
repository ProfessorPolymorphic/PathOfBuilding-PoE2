import { useState } from 'react'

interface Props {
  onLoad: (code: string) => Promise<void>
  onCancel?: () => void
}

export function BuildLoader({ onLoad, onCancel }: Props) {
  const [code, setCode] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  async function submit(e: React.FormEvent) {
    e.preventDefault()
    const trimmed = code.trim()
    if (!trimmed) return
    setBusy(true)
    setError(null)
    try {
      await onLoad(trimmed)
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
