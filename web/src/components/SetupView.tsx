import { useEffect, useMemo, useRef, useState } from 'react'
import { ArrowUpRight, Check, Copy, Database, Download, KeyRound, LoaderCircle, Mail, RefreshCw, Server, Upload } from 'lucide-react'
import { IMPORT_TEMPLATE, importRecords, parseImport, readBootstrap, readSetup, saveModel } from '../data/setup'
import type { ModelSettings, SetupStatus } from '../data/setup'
import { isLocalSession, type ReviewAccess } from '../data/auth'
import './setup.css'

interface Props {
  connected: boolean
  token: ReviewAccess
  onConnect: (token: string) => Promise<void> | void
  onDisconnect: () => Promise<void> | void
  onImported?: () => void
  onOpenOutbox: () => void
}

const TOKEN_COMMAND = `export RESPAWNED_REVIEW_TOKEN="$(python -c 'import secrets; print(secrets.token_urlsafe(32))')"
printf '%s\\n' "$RESPAWNED_REVIEW_TOKEN"
respawned serve`

function message(error: unknown, fallback: string) { return error instanceof Error ? error.message : fallback }

function downloadTemplate() {
  const url = URL.createObjectURL(new Blob([IMPORT_TEMPLATE], { type: 'application/json' }))
  const link = document.createElement('a')
  link.href = url
  link.download = 'respawned-import-template.json'
  link.click()
  setTimeout(() => URL.revokeObjectURL(url), 1000)
}

export function SetupView({ connected, token, onConnect, onDisconnect, onImported, onOpenOutbox }: Props) {
  const [accessToken, setAccessToken] = useState('')
  const [connecting, setConnecting] = useState(false)
  const [accessError, setAccessError] = useState<string | null>(null)
  const [reviewEnabled, setReviewEnabled] = useState<boolean | null>(null)
  const [status, setStatus] = useState<SetupStatus | null>(null)
  const [statusError, setStatusError] = useState<string | null>(null)
  const [loading, setLoading] = useState(false)
  const [refresh, setRefresh] = useState(0)
  const [model, setModel] = useState<ModelSettings | null>(null)
  const [savingModel, setSavingModel] = useState(false)
  const [modelError, setModelError] = useState<string | null>(null)
  const [modelNotice, setModelNotice] = useState<string | null>(null)
  const [importText, setImportText] = useState('')
  const [importing, setImporting] = useState(false)
  const [importError, setImportError] = useState<string | null>(null)
  const [importNotice, setImportNotice] = useState<string | null>(null)
  const [copied, setCopied] = useState(false)
  const currentAccess = useRef(token)
  currentAccess.current = token

  useEffect(() => {
    setAccessToken('')
    setAccessError(null)
    setImportText('')
  }, [token])

  useEffect(() => {
    const abort = new AbortController()
    setLoading(true)
    setStatusError(null)
    setStatus(null)
    setModel(null)
    setModelError(null)
    setModelNotice(null)
    setImportError(null)
    setImportNotice(null)
    setSavingModel(false)
    setImporting(false)
    setReviewEnabled(null)
    if (connected) {
      void readSetup(token, abort.signal).then(result => {
        if (abort.signal.aborted) return
        setStatus(result)
        setModel(result.model)
        setReviewEnabled(true)
      }).catch(error => {
        if (!abort.signal.aborted) setStatusError(message(error, 'Could not load the environment.'))
      }).finally(() => { if (!abort.signal.aborted) setLoading(false) })
    } else {
      void readBootstrap(abort.signal).then(result => {
        if (!abort.signal.aborted) setReviewEnabled(result.review_enabled)
      }).catch(error => {
        if (!abort.signal.aborted) setStatusError(message(error, 'Could not reach the server.'))
      }).finally(() => { if (!abort.signal.aborted) setLoading(false) })
    }
    return () => abort.abort()
  }, [connected, token, refresh])

  const preview = useMemo(() => {
    if (!importText.trim()) return { payload: null, error: null }
    try { return { payload: parseImport(importText), error: null } }
    catch (error) { return { payload: null, error: message(error, 'Check the import JSON.') } }
  }, [importText])
  const databaseReady = status?.database.status === 'ready'

  return <div className="setup-view">
    <div className="setup-intro"><p>Connect this environment, choose a model, and bring in the records you want to follow up.</p>
      <button className="button small" disabled={loading || savingModel || importing} onClick={() => setRefresh(value => value + 1)}><RefreshCw size={14} className={loading ? 'spin' : ''} />Refresh status</button></div>
    {statusError && <p className="inline-error" role="alert">{statusError}</p>}

    <section className="setup-section" aria-labelledby="setup-access-title">
      <div className="setup-section-heading"><KeyRound size={23} /><div><h2 id="setup-access-title">Review access</h2><p>Your connection to this environment</p></div></div>
      <div className="setup-section-body">
        <div className="setup-heading-row"><p>{isLocalSession(token) ? 'Connected securely through your local CLI.' : 'Launch with respawned ui to connect automatically, or use a server access token below.'}</p><span className={`setup-state ${connected ? 'is-ready' : ''}`}>{connected ? 'Unlocked' : 'Locked'}</span></div>
        {connected ? <div className="setup-access-active"><Check size={18} /><p>{isLocalSession(token) ? 'This local connection survives page reloads and expires after 12 hours or when the server stops. Lock access ends it immediately.' : 'Access is active for this browser tab. The manually entered token stays in memory and clears when the page reloads.'}</p><button className="button small" onClick={onDisconnect}>Lock access</button></div> : <><div className="setup-note"><p>On the machine running Respawned, run:</p><pre><code>respawned ui</code></pre><p>It opens this interface with access already connected. In a terminal without a browser, use <code>respawned ui --no-open</code> and open the one-use link it prints on this machine. If port 8000 is occupied, stop the existing server or choose <code>--port 8001</code>.</p></div><form className="setup-access-form" onSubmit={async event => {
          event.preventDefault()
          setConnecting(true)
          setAccessError(null)
          try { await onConnect(accessToken.trim()); setAccessToken('') }
          catch (error) { setAccessError(message(error, 'Could not unlock review access.')) }
          finally { setConnecting(false) }
        }}>
          <label htmlFor="setup-review-token">Review access token</label>
          <div className="setup-input-action"><input id="setup-review-token" type="password" autoComplete="off" spellCheck={false} value={accessToken} onChange={event => setAccessToken(event.target.value)} placeholder="Enter the token from your server" required disabled={connecting} />
            <button type="submit" className="button primary" disabled={connecting || !accessToken.trim()}>{connecting && <LoaderCircle size={16} className="spin" />}Connect local engine</button></div>
          {accessError && <p className="inline-error" role="alert">{accessError}</p>}
        </form></>}
        {!isLocalSession(token) && <details className="setup-details">
          <summary>{reviewEnabled === false ? 'Optional: configure remote or API access' : 'Using a remote server or API client?'}</summary>
          <p>Set <code>RESPAWNED_REVIEW_TOKEN</code> on the machine running Respawned, then start or restart the server. In a Bash terminal, this generates a random token and prints it for you to enter above:</p>
          <pre><code>{TOKEN_COMMAND}</code></pre>
          <p>Keep the same value in your server environment for future starts. Docker users can set it in the app service environment. Anyone with this token can review records and change environment settings.</p>
          <p>The <code>review_token</code> attached to an individual draft is different: Respawned manages that version check automatically to prevent approving stale text.</p>
        </details>}
      </div>
    </section>

    <section className="setup-section" aria-labelledby="setup-model-title">
      <div className="setup-section-heading"><Server size={23} /><div><h2 id="setup-model-title">Model backend</h2><p>Generate drafts on demand</p></div></div>
      <div className="setup-section-body">
        <div className="setup-heading-row"><p>Use your Codex ChatGPT login or connect an OpenAI-compatible API.</p><span className={`setup-state ${status?.model.ready ? 'is-ready' : ''}`}>{!connected ? 'Unlock to configure' : status?.model.ready ? 'Configured' : loading ? 'Checking' : 'Setup needed'}</span></div>
        {!connected && <p className="setup-note">Unlock review access to view and save the server’s model settings.</p>}
        {status?.model.error && <p className="inline-error">{status.model.error}. Enter valid settings below to replace it.</p>}
        {connected && model && <form onSubmit={async event => {
          event.preventDefault()
          const startedWith = token
          setSavingModel(true)
          setModelError(null)
          setModelNotice(null)
          try {
            const saved = await saveModel(token, { backend: model.backend ?? 'openai_compatible', base_url: model.backend === 'codex_cli' ? '' : model.base_url.trim(), model_alias: model.model_alias.trim(), api_key_env: model.api_key_env, timeout_seconds: model.timeout_seconds })
            if (currentAccess.current !== startedWith) return
            setModel(saved)
            setStatus(previous => previous ? { ...previous, model: saved } : previous)
            setModelNotice('Model settings saved. No model request was made.')
          } catch (error) { if (currentAccess.current === startedWith) setModelError(message(error, 'Could not save model settings.')) }
          finally { if (currentAccess.current === startedWith) setSavingModel(false) }
        }}>
          <fieldset className="setup-form-grid" disabled={savingModel || !databaseReady}>
            <label className="setup-field-wide">Backend<select value={model.backend ?? 'openai_compatible'} onChange={event => setModel({ ...model, backend: event.target.value as ModelSettings['backend'], model_alias: '', timeout_seconds: event.target.value === 'codex_cli' ? 120 : 60 })}><option value="openai_compatible">OpenAI-compatible API / LiteLLM</option><option value="codex_cli">Codex CLI · ChatGPT login</option></select></label>
            {model.backend !== 'codex_cli' && <label className="setup-field-wide">API base URL<input type="url" value={model.base_url} onChange={event => setModel({ ...model, base_url: event.target.value })} placeholder="http://localhost:11434/v1" required /><span>Use the exact API root reachable from the Respawned server. Include /v1 if your backend requires it.</span></label>}
            <label>{model.backend === 'codex_cli' ? 'Codex model (optional)' : 'Model or proxy alias'}<input value={model.model_alias} onChange={event => setModel({ ...model, model_alias: event.target.value })} placeholder={model.backend === 'codex_cli' ? 'Use the Codex CLI default' : 'Model name accepted by your backend'} required={model.backend !== 'codex_cli'} /></label>
            <label>Timeout (seconds)<input type="number" min="0.1" max="300" step="0.1" value={model.timeout_seconds} onChange={event => setModel({ ...model, timeout_seconds: Number(event.target.value) })} required /></label>
            {model.backend !== 'codex_cli' && <label className="setup-field-wide">Server credential variable<select value={model.api_key_env} onChange={event => setModel({ ...model, api_key_env: event.target.value as ModelSettings['api_key_env'] })}><option value="LITELLM_MASTER_KEY">LITELLM_MASTER_KEY</option><option value="RESPAWNED_MODEL_API_KEY">RESPAWNED_MODEL_API_KEY</option></select><span>Set the key in this environment variable on the server and restart it. The key is never entered or returned here.</span></label>}
          </fieldset>
          <p className="setup-note">{model.backend === 'codex_cli' ? (status?.model.backend === 'codex_cli' && status.model.login_ready ? 'Codex CLI and its ChatGPT login are available on the server.' : 'Run codex login with ChatGPT on the server. Save to check the CLI and login; no API key is needed.') : model.api_key_env !== status?.model.api_key_env ? 'Save to check this credential variable.' : status?.model.key_configured ? 'The selected credential is set on the server.' : 'The selected credential is missing on the server.'} {status?.model.source === 'saved' ? 'Using saved model settings.' : 'Using server environment defaults.'}</p>
          {modelError && <p className="inline-error" role="alert">{modelError}</p>}
          {modelNotice && <p className="setup-success" role="status"><Check size={15} />{modelNotice}</p>}
          <div className="setup-form-actions"><p>Saving makes no model request. Generating a draft sends its drafting context to this backend.</p><button type="submit" className="button primary" disabled={savingModel || !databaseReady}>{savingModel && <LoaderCircle size={16} className="spin" />}Save model settings</button></div>
        </form>}
        <details className="setup-details"><summary>Which backend settings should I use?</summary><p>Codex uses the CLI installed on the server and its existing ChatGPT login. It runs a bounded text-only task with structured output; the engine still validates the draft and requires approval. If Codex is not on PATH, set <code>RESPAWNED_CODEX_BIN</code> in the server environment. When calling Windows Codex from WSL, also set <code>RESPAWNED_CODEX_SCRATCH_DIR</code> to an existing directory on a mounted Windows drive.</p><p>For a local model server, use its API base URL and the exact model name it serves. For LiteLLM, use the proxy URL, a model alias from its configuration, and <code>LITELLM_MASTER_KEY</code>. A direct provider can use <code>RESPAWNED_MODEL_API_KEY</code>.</p><p>If your local backend needs no authentication, set a local-only value in the selected server credential variable. Respawned’s API client requires a value, even when the backend ignores it.</p></details>
      </div>
    </section>

    <section className="setup-section" aria-labelledby="setup-source-title">
      <div className="setup-section-heading"><Database size={23} /><div><h2 id="setup-source-title">Records & sources</h2><p>Bring your own workflow</p></div></div>
      <div className="setup-section-body">
        <div className="setup-heading-row"><p>Import records and activity from your existing tools. Workspaces organize records by their kind.</p><span className={`setup-state ${databaseReady ? 'is-ready' : ''}`}>{!connected ? 'Unlock to import' : databaseReady ? 'Database ready' : loading ? 'Checking' : 'Database unavailable'}</span></div>
        {status && <p className="setup-note">{status.database.message}</p>}
        <form onSubmit={async event => {
          event.preventDefault()
          if (!preview.payload) return
          const startedWith = token
          setImporting(true)
          setImportError(null)
          setImportNotice(null)
          try {
            const result = await importRecords(token, preview.payload)
            if (currentAccess.current !== startedWith) return
            setImportText('')
            setImportNotice(`Imported ${result.opportunities_upserted} record${result.opportunities_upserted === 1 ? '' : 's'} and ${result.activities_inserted} new activit${result.activities_inserted === 1 ? 'y' : 'ies'}. Refresh the review queue to evaluate next actions.`)
            onImported?.()
          } catch (error) { if (currentAccess.current === startedWith) setImportError(message(error, 'Could not import the records.')) }
          finally { if (currentAccess.current === startedWith) setImporting(false) }
        }}>
          <label className="setup-file-label">Choose a JSON file<input type="file" accept=".json,application/json" disabled={!connected || !databaseReady || importing} onChange={async event => {
            const file = event.target.files?.[0]
            if (!file) return
            const startedWith = token
            setImportError(null)
            setImportNotice(null)
            try {
              if (file.size > 2_000_000) throw new Error('Choose a JSON file smaller than 2 MB. Split larger imports into batches.')
              const text = await file.text()
              if (currentAccess.current === startedWith) setImportText(text)
            } catch (error) { if (currentAccess.current === startedWith) setImportError(message(error, 'Could not read this file.')) }
            event.target.value = ''
          }} /></label>
          <label className="setup-json-label" htmlFor="setup-import-json">Or paste your import JSON</label>
          <textarea id="setup-import-json" className="setup-json-input" value={importText} onChange={event => { setImportText(event.target.value); setImportError(null); setImportNotice(null) }} placeholder={'{ "opportunities": [...], "activities": [...] }'} disabled={!connected || !databaseReady || importing} spellCheck={false} rows={5} />
          {preview.payload && <p className="setup-preview" role="status">Ready to validate: {preview.payload.opportunities.length} record{preview.payload.opportunities.length === 1 ? '' : 's'} · {preview.payload.activities.length} {preview.payload.activities.length === 1 ? 'activity' : 'activities'}</p>}
          {preview.error && <p className="inline-error">{preview.error}</p>}
          {importError && <p className="inline-error" role="alert">{importError}</p>}
          {importNotice && <p className="setup-success" role="status"><Check size={15} />{importNotice}</p>}
          <p className="setup-note">Matching record IDs replace the full saved snapshot, including clearing omitted optional fields. Activity IDs are immutable: resend the same fact, or use a new ID for a new event.</p>
          <div className="setup-form-actions"><p>Only your submitted data is imported. Importing does not generate drafts or send messages.</p><button className="button primary" type="submit" disabled={!connected || !databaseReady || importing || !preview.payload}>{importing ? <LoaderCircle size={16} className="spin" /> : <Upload size={16} />}Import records</button></div>
        </form>
        <details className="setup-details"><summary>Import format & API integration</summary><p>The API calls tracked records <code>opportunities</code>, regardless of your workflow. Give each record a stable ID and a <code>kind</code> such as <code>generic</code> or your own lowercase identifier. Use real source timestamps. Only add a contact route when you know the recipient.</p>
          <div className="setup-template-actions"><button type="button" className="button small" onClick={downloadTemplate}><Download size={14} />Download template</button><button type="button" className="button small" onClick={async () => {
            try { await navigator.clipboard.writeText(IMPORT_TEMPLATE); setCopied(true) }
            catch { setCopied(false); setImportError('Copy was unavailable. Download the template or select its text below.') }
          }}>{copied ? <Check size={14} /> : <Copy size={14} />}{copied ? 'Copied' : 'Copy template'}</button></div>
          <pre><code>{IMPORT_TEMPLATE}</code></pre>
          <p>Replace the template values before importing. To integrate a source tool, send the same JSON to <code>POST /v1/ui/import</code> with <code>Authorization: Bearer &lt;review access token&gt;</code>. Import confirmed human replies as activities with <code>direction: "inbound"</code> and <code>classification: "human"</code>. Automated acknowledgments should use <code>classification: "automated"</code>.</p>
          <p>There is no automatic mailbox or CRM sync in this version. “Refresh queue” evaluates the records already imported; it does not fetch from external tools.</p>
        </details>
      </div>
    </section>

    <section className="setup-section" aria-labelledby="setup-outbox-title">
      <div className="setup-section-heading"><Mail size={23} /><div><h2 id="setup-outbox-title">Outbox & delivery</h2><p>Review, export, then send</p></div></div>
      <div className="setup-section-body"><div className="setup-heading-row"><p>Approving a draft reserves an unsent message in the outbox. Delivery is manual in this version.</p><span className="setup-state">Export only</span></div>
        <ol className="setup-delivery-steps"><li>Review and approve the exact message text.</li><li>Open the outbox and export the approved messages.</li><li>Send through your own mail or messaging tool, then import the actual outbound event.</li></ol>
        <p className="setup-note">No email account, SMS provider, or sending worker is connected. Exporting does not mark a message sent; the UI keeps its recorded outbox status.</p>
        <button className="button" disabled={!connected} onClick={onOpenOutbox}>Open outbox<ArrowUpRight size={17} /></button>
      </div>
    </section>
  </div>
}
