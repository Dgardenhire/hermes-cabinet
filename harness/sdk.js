// A stand-in for '@hermes/plugin-sdk', for rendering desktop/plugin.js in a plain browser.
// Adapted from the Factory harness (holdout-factory).
//
// The exports the plugin imports, with the real names and area ids. Components and the
// data layer come from kit-sdk-stub.js (hermes-ui-kit/harness); host is Cabinet's own.
// The data layer is the real library at the app's versions (apps/desktop/package.json:
// @tanstack/react-query 5.101.2, nanostores 1.4.2), loaded through the import map in
// index.html. Button, Codicon and RowButton render the same elements and data attributes
// as apps/desktop/src/components/ui/{button,codicon,row-button}.tsx; their look is
// approximated in index.html. host records every call in window.__hostLog.
//
// This file is for the harness only. The app provides the real SDK.

import { useStore } from '@nanostores/react'
import { atom as nanoAtom } from 'nanostores'

// Every component, area id and data hook the kit and the plugin use: hermes-ui-kit's harness
// stand-in, copied here by the kit's build. Cabinet's own host follows.
export * from './kit-sdk-stub.js'

export const atom = nanoAtom
export const useValue = useStore

function harness() {
  return window.__harness || {}
}

function log(...args) {
  ;(window.__hostLog = window.__hostLog || []).push(args)
  console.log('[host]', ...args.map(a => (typeof a === 'string' ? a : JSON.stringify(a))))
}

function toast(input) {
  const box = document.getElementById('hb-toasts')
  if (!box) return
  const el = document.createElement('div')
  el.className = `hb-toast hb-toast-${input.kind || 'info'}`
  el.textContent = input.message
  box.append(el)
  setTimeout(() => el.remove(), 20000) // long enough to be in a screenshot
}

// Hermes's own Bots catalog, as its gateway answers bots.catalog (shape: BotsCatalogResult in
// NousResearch/hermes-agent#119260, apps/shared/src/gateway-contract.generated.ts), trimmed to the fields Cabinet reads.
// Hermes main has no such method yet, so the harness answers only when a scene asks for it (handover=1); otherwise
// the call fails like an unknown method and Cabinet behaves as it does without the catalog. Two of these names are
// Cabinet slugs on purpose (scout, planner), so the hand-over has something to hand over.
export const HERMES_BOTS_CATALOG = {
  entries: [
    { name: 'scout', title: 'Opportunity Scout', summary: 'Finds timely openings in the areas you choose', tier: 'community' },
    { name: 'planner', title: 'Weekly Planner', summary: 'Keeps your week in order', tier: 'official' },
    { name: 'research-analyst', title: 'Research Analyst', summary: 'Sourced research briefs', tier: 'official' },
    { name: 'inbox-triage', title: 'Inbox Triage', summary: 'Sorts and drafts replies', tier: 'official' },
  ],
  removed: [],
}

export const host = {
  state: {
    profile: nanoAtom('default'),
    activeSessionId: nanoAtom(null),
    focusedSessionId: nanoAtom(null),
    focusedStoredSessionId: nanoAtom(harness().lastSession || null),
  },
  navigate(path) {
    log('navigate', path)
    if (path !== '/cabinet') toast({ kind: 'info', message: `The app would open ${path}` })
  },
  newChat(profile) {
    log('newChat', profile || null)
    if (profile) toast({ kind: 'info', message: `A new chat opens with the ${profile} bot` })
  },
  async openSession(id) {
    log('openSession', id)
  },
  async request(method, params, timeoutMs) {
    log('request', method, params, timeoutMs)
    const answers = harness().rpc || {}
    if (method in answers) return answers[method]
    if (method === 'bots.catalog' && harness().hermesBots) return HERMES_BOTS_CATALOG
    if (method === 'profiles.create') {
      // The harness's backend makes the profile folder in its sandbox home, the way Hermes's gateway would.
      const res = await fetch('/__h/gateway', { method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify({ method, params }) })
      const text = await res.text()
      if (!res.ok) throw new Error(`${res.status}: ${text}`)
      return JSON.parse(text)
    }
    throw new Error(`Hermes gateway unavailable (${method})`)
  },
  notify(input) {
    log('notify', input)
    toast(input)
    return 'toast'
  },
  composer: {
    // Like the app: true when a mounted composer took the text and sent it.
    submit(address, text) {
      log('submit', address, text)
      toast({ kind: 'info', message: 'A new chat opens and sends: ' + text.slice(0, 90) + (text.length > 90 ? '…' : '') })
      return true
    },
    async setDraft(address, text) {
      log('setDraft', address, text)
      toast({ kind: 'info', message: 'A new chat opens with this in the message box: ' + text.slice(0, 90) + (text.length > 90 ? '…' : '') })
      return true
    },
  },
}
