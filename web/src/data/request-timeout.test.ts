// Keep these request-contract tests on a legacy engine; workflow.test.ts covers discovery.
vi.mock('./workflow', async importOriginal => ({ ...await importOriginal<typeof import('./workflow')>(), workflowRoot: async (baseUrl = '', signal?: AbortSignal) => { signal?.throwIfAborted(); return `${baseUrl.replace(/\/+$/, '')}/v1/ui` } }))
import { afterEach, expect, it, vi } from 'vitest'
import { importRecords, readSetup } from './setup'
import { workspaceRequest } from './workspaces'

afterEach(() => { vi.restoreAllMocks(); vi.unstubAllGlobals() })

function stalledRequest() {
  const deadline = new AbortController()
  const timeout = vi.spyOn(AbortSignal, 'timeout').mockReturnValue(deadline.signal)
  const fetch = vi.fn((_url: string, options: RequestInit) => new Promise<Response>((_resolve, reject) => {
    options.signal!.addEventListener('abort', () => reject(options.signal!.reason), { once: true })
  }))
  vi.stubGlobal('fetch', fetch)
  return { deadline, timeout, fetch }
}

it.each(['import', 'workspace'])('releases a stalled %s write without retrying an unknown outcome', async operation => {
  const { deadline, timeout, fetch } = stalledRequest()
  const pending = operation === 'import'
    ? importRecords('review-access', { opportunities: [{ id: 'record' }], activities: [] })
    : workspaceRequest('review-access', '', 'POST', { name: 'Work', description: '', kinds: [] })
  const rejected = expect(pending).rejects.toThrow('Reload to check whether it completed before retrying')
  await Promise.resolve()
  deadline.abort(new DOMException('Deadline exceeded', 'TimeoutError'))
  await rejected
  expect(timeout).toHaveBeenCalledWith(20_000)
  expect(fetch).toHaveBeenCalledOnce()
})

it('preserves caller cancellation when leaving Setup', async () => {
  const { deadline, fetch } = stalledRequest()
  const caller = new AbortController()
  const pending = readSetup('review-access', caller.signal)
  const rejected = expect(pending).rejects.toMatchObject({ name: 'AbortError' })
  await Promise.resolve()
  caller.abort()
  await rejected
  expect(deadline.signal.aborted).toBe(false)
  expect(fetch).toHaveBeenCalledOnce()
})
