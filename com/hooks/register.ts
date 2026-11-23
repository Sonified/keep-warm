import type { EngineInterface, Register } from 'claude-code'

// "com" -> the built-in /compact, run by the engine itself (not the model summarizing).
//   com                      -> /compact this thread
//   com keep the tax plan    -> /compact keep the tax plan
//   com 1 3                  -> /compact the threads numbered 1 and 3 in the last "warm list" (any window)
// Only the person's own typed message counts, and only when it starts with the bare word "com".
//
// Across windows: "com 1 3" leaves a request file, ~/.claude/keep-warm/compact.<session>, for each of those threads
// (scripts/comlist.py). Every window running com looks for its own every 15 seconds; finding it, it removes it and
// compacts itself. The asking window checks back after 90 seconds and reports any request nobody picked up.
const COM = /^com(?:\s+(.+))?$/i
const NUMBERS = /^\d+(?:(?: |, ?| and )\d+)*$/ // "1", "1 3", "1, 3", "1 and 3"
const LOOK_EVERY_MS = 15_000
const PICKUP_MS = 90_000
const NOT_SENT = '(this message was not sent to Claude)'

// Runs /compact in this window, outside any hook the turn waits on; the engine queues it until the session is idle.
const compactHere = ($: EngineInterface, args: string): void => {
  $.clock.after(0, () => {
    void $.command.run({ command: 'compact', args }).catch(async () => {
      try {
        await $.session.compact(args ? { instructions: args } : undefined)
      } catch (err) {
        $.ui.toast(`com: compact did not run (${String(err)})`)
      }
    })
  })
}

const home = async ($: EngineInterface): Promise<string> => (await $.env.get('HOME')) ?? ''
const requestFile = async ($: EngineInterface, sid: string): Promise<string> =>
  `${await home($)}/.claude/keep-warm/compact.${sid}`
const remove = async ($: EngineInterface, path: string): Promise<void> => {
  await $.process.run(['rm', '-f', path], { timeoutMs: 5_000 }).catch(() => undefined)
}

type Asked = { self: boolean; asked: { n: number; sid: string; label: string }[]; skipped: string[] }

// Leaves the request files for the numbered threads (scripts/comlist.py) and says which threads they were.
const ask = async ($: EngineInterface, numbers: string[]): Promise<Asked> => {
  const script = `${$.plugin.root}/scripts/comlist.py`
  try {
    const ran = await $.process.run(['python3', script, 'ask', await $.session.id(), ...numbers], {
      timeoutMs: 20_000,
    })
    return JSON.parse(ran.stdout) as Asked
  } catch {
    return { self: false, asked: [], skipped: ['the com list script did not run'] }
  }
}

// This window's own mailbox: a request from another window to compact this thread.
let isLooking = false
const lookForRequest = async ($: EngineInterface): Promise<void> => {
  if (isLooking) return
  isLooking = true
  try {
    const path = await requestFile($, await $.session.id())
    if (!(await $.fs.exists(path))) return
    await remove($, path)
    $.ui.toast('com: compacting this thread (asked from another window)')
    compactHere($, '')
  } catch {
    // a missed look is retried on the next tick
  } finally {
    isLooking = false
  }
}

// After PICKUP_MS, any request file still there was never picked up: remove it and say which.
const checkPickup = async ($: EngineInterface, asked: Asked['asked']): Promise<void> => {
  const missed: number[] = []
  for (const a of asked) {
    const path = await requestFile($, a.sid)
    if (await $.fs.exists(path)) {
      await remove($, path)
      missed.push(a.n)
    }
  }
  $.ui.toast(
    missed.length === 0
      ? `com: ${asked.map(a => a.n).join(', ')} picked up, compacting`
      : `com: ${missed.join(', ')} never picked it up (window closed, or opened before com was installed)`,
  )
}

export const register: Register = on => {
  on('session.start', async ($, e, next) => {
    $.clock.every(LOOK_EVERY_MS, () => void lookForRequest($))

    return next(e)
  })

  on('prompt.submit', async ($, e, next) => {
    const from = e.origin?.kind
    const isPerson = from === undefined || from === 'composer' || from === 'bridge' || from === 'sdk'
    if (!isPerson || e.command !== undefined) return next(e)

    const m = COM.exec(e.text.trim().replace(/[.!]+$/, ''))
    if (m === null) return next(e)
    const args = (m[1] ?? '').trim()

    if (!NUMBERS.test(args)) {
      compactHere($, args)
      return { drop: `com → /compact${args ? ` ${args}` : ''} ${NOT_SENT}` }
    }

    // "com 1 3": the threads from the last warm list, this one included if it is among them.
    const result = await ask($, args.match(/\d+/g) ?? [])
    const own = await $.session.id()
    const elsewhere = result.asked.filter(a => a.sid !== own)
    for (const a of result.asked) $.ui.log(`➡️ ${a.n}. ${a.label}${a.sid === own ? '  ← 👋 this thread' : ''}`)
    if (result.skipped.length > 0) $.ui.log(`➡️ skipped: ${result.skipped.join(', ')}`)
    if (result.self) compactHere($, '')
    if (elsewhere.length > 0) $.clock.after(PICKUP_MS, () => void checkPickup($, elsewhere).catch(() => undefined))

    const count = result.asked.length
    return {
      drop:
        count === 0
          ? `com: nothing to compact ${NOT_SENT}`
          : `com → compacting ${count} thread${count === 1 ? '' : 's'} (each window picks it up within 15s) ${NOT_SENT}`,
    }
  })
}
