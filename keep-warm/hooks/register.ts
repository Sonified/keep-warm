import type { EngineInterface, Register } from 'claude-code'

// The keep-warm plugin's part that runs inside Claude Code. It does three things, none of which costs a request
// except the ping itself:
//
//   1. The words. When the person's whole message is "warm" or "keep warm" (or "cold" / "cool"), it
//      switches keep-warm on (or off) and stops the message there, so it never reaches the model.
//   2. The watching. Every 30 seconds it asks the plugin's control script whether this session's last request
//      is 50 minutes old. When it is, it sends the ping itself: a one-word turn, a cheap cache read that
//      restarts the cache's hour. A timer here starts again after a window reload, so keep-warm carries on.
//   3. The line. While keep-warm is on, a status line by the prompt reads "active 🔥" and, in brackets, the
//      thread's size in thousands of tokens (as of the last request). While it is off (every new thread starts
//      there) the line reads "back burner" with the same size: no pings, just the count. The size off is
//      measured when the window opens and after each turn, not on every look. /warm-line hides the line and
//      shows it again; hidden, it stays hidden until keep-warm is next switched on or off.
//   4. The list. "warm list" numbers every thread keep-warm is on for, in any window; "cool 2 4" switches those
//      off (scripts/warmlist.py). Their own windows notice within 30 seconds and turn to back burner.
//
// All state is in ~/.claude/keep-warm/ and is read and written by one script, scripts/ctl.sh (arm, off, tick,
// peek): the decision of when to ping lives there, next to the settings in scripts/common.sh.

const LINE = '𝗮𝗰𝘁𝗶𝘃𝗲 🔥' // "active" in Unicode bold letters
const BACK = '𝗯𝗮𝗰𝗸 𝗯𝘂𝗿𝗻𝗲𝗿' // "back burner": keep-warm off, size still shown
const BACK_KEY = 'back' // what closedFor holds when the back-burner line is hidden
const COMMAND = 'warm-line'
const LOOK_EVERY_MS = 30_000
const SIZE_EVERY_MS = 3_000 // while a turn runs: each request in it reports the thread's new size as its reply starts
const PING = 'keep-warm ping: reply with the single word "ok" and do nothing else. No tool calls.'
const ON_WORDS = ['warm', 'keep warm', 'keepwarm', 'keep-warm', 'warm on', 'keep warm on']
const OFF_WORDS = ['cold', 'cool', 'stop warm', 'warm off', 'keep warm off', 'stop keeping warm']
const LIST_WORDS = ['warm list', 'warmlist', 'list warm', 'warm ls']
const COOL = /^(?:cool|cold) (\d+(?:(?: |, ?| and )\d+)*)$/ // "cool 2", "cool 2 4", "cold 2, 4", "cool 2 and 4"
const NOT_SENT = '(this message was not sent to Claude)'
const SAID_ON = `keep-warm: on 🔥 · one ping after each 50 idle minutes · ends after 24 hours without a message from you, or type "cold" or "cool" · "warm list" shows every warm thread ${NOT_SENT}`
const SAID_OFF = `keep-warm: off · back burner ${NOT_SENT}`

let shownText = '' // the line now on screen, '' when none
let size = '' // the thread's size as shown after the word, for example ' (167k)'
let isBusy = false // a turn is running, so the cache is in use and a ping would only queue up behind it
let closedFor = '' // the switch-on time the person hid the line for, or BACK_KEY for the back-burner line
let warmSince = '' // the switch-on time of the keep-warm now running, '' when it is not

// Runs the control script for this session and returns its one line of output ('' when it could not run).
const ctl = async ($: EngineInterface, verb: string): Promise<string> => {
  try {
    const script = `${$.plugin.root}/scripts/ctl.sh`
    const ran = await $.process.run(['bash', script, verb, await $.session.id()], { timeoutMs: 15_000 })

    return ran.exitCode === 0 ? ran.stdout.trim() : ran.stdout.trim() || ran.stderr.trim()
  } catch {
    return ''
  }
}

// Runs scripts/warmlist.py ("list", or "cool" with numbers) and returns what it printed.
const warmlist = async ($: EngineInterface, args: string[]): Promise<string> => {
  try {
    const script = `${$.plugin.root}/scripts/warmlist.py`
    const [verb = 'list', ...numbers] = args
    const ran = await $.process.run(['python3', script, verb, await $.session.id(), ...numbers], {
      timeoutMs: 20_000,
    })

    return ran.stdout.trim() || ran.stderr.trim() || 'keep-warm: the list script printed nothing'
  } catch {
    return 'keep-warm: the list script did not run'
  }
}

// Shows a many-line answer: the drop banner joins lines into one, so each line goes to the transcript as a row of
// its own and the banner carries only the first.
const rows = ($: EngineInterface, text: string): { drop: string } => {
  const [first = '', ...rest] = text.split('\n')
  for (const line of rest) $.ui.log(`➡️ ${line}`) // after the "keep-warm plugin:" label, the eye finds the start

  return { drop: `${first} ${NOT_SENT}` }
}

const lineKey = (): string => (warmSince !== '' ? warmSince : BACK_KEY)
// The size's colour, the same as the "tokens" line: green to 300k, yellow to 400k, orange to 500k, red above.
const dot = (n: number): string => (n <= 300_000 ? '🟢' : n <= 400_000 ? '🟡' : n <= 500_000 ? '🟠' : '🔴')
const sized = (tokens: string): string =>
  Number(tokens) >= 1000 ? ` (${dot(Number(tokens))} ${Math.round(Number(tokens) / 1000)}k)` : ''

const show = ($: EngineInterface): void => {
  const wanted = lineKey() === closedFor ? '' : `${warmSince !== '' ? LINE : BACK}${size}`
  if (wanted !== shownText) {
    shownText = wanted
    $.ui.status(wanted === '' ? undefined : wanted)
  }
}

// One look: is keep-warm on, has it run out, is it time to ping. `mayPing` is false while a turn is running.
const look = async ($: EngineInterface, mayPing: boolean): Promise<void> => {
  const [word = '', since = '', tokens = ''] = (await ctl($, mayPing ? 'tick' : 'peek')).split(' ')
  const isWarm = word === 'wait' || word === 'ping'
  warmSince = isWarm ? since : ''
  if (isWarm) size = sized(tokens) // off, the size is kept from the last measure()
  show($)
  if (word === 'expired') $.ui.toast('keep-warm ended: 24 hours without a message from you')
  if (word === 'ping') void $.prompt.submit({ text: PING }).catch(() => undefined)
}

// The thread's size for the line. Only a request changes it, so this runs at the start, every few seconds while a
// turn runs, and after each turn.
const measure = async ($: EngineInterface): Promise<void> => {
  const tokens = await ctl($, 'size')
  if (/^\d+$/.test(tokens)) size = sized(tokens)
  show($)
}

// A look, then, if keep-warm turns out to be off, a measure for the back-burner line.
const lookAndMeasure = async ($: EngineInterface): Promise<void> => {
  await look($, false)
  if (warmSince === '') await measure($)
}

export const register: Register = on => {
  on('session.start', async ($, e, next) => {
    await $.command.register({
      name: COMMAND,
      description: 'Hide or show the keep-warm line ("active" or "back burner")',
    })
    void lookAndMeasure($)
    $.clock.every(LOOK_EVERY_MS, () => void look($, !isBusy))
    // While a turn runs the size is read every few seconds, so the line follows the thread as it grows (a turn of
    // many tool calls is many requests) instead of jumping once at the end. Reading it sends nothing.
    $.clock.every(SIZE_EVERY_MS, () => {
      if (isBusy) void measure($)
    })

    return next(e)
  })

  on('turn.start', async ($, e, next) => {
    isBusy = true

    return next(e)
  })

  on('turn.complete', async ($, e, next) => {
    isBusy = false
    void lookAndMeasure($)

    return next(e)
  })

  // The words. Only the person's own typed message counts, and only when it is nothing but the words.
  on('prompt.submit', async ($, e, next) => {
    const from = e.origin?.kind
    const isPerson = from === undefined || from === 'composer' || from === 'bridge' || from === 'sdk'
    if (!isPerson || e.command !== undefined) return next(e)
    const said = e.text.trim().toLowerCase().replace(/[.!?]+$/, '').replace(/\s+/g, ' ')

    if (ON_WORDS.includes(said)) {
      const answer = await ctl($, 'arm')
      closedFor = ''
      await look($, false)

      return {
        drop: answer === 'on' ? SAID_ON : `keep-warm could not switch on: ${answer || 'its control script did not run'}`,
      }
    }
    if (OFF_WORDS.includes(said)) {
      await ctl($, 'off')
      closedFor = ''
      await lookAndMeasure($)

      return { drop: SAID_OFF }
    }
    if (LIST_WORDS.includes(said)) return rows($, await warmlist($, ['list']))
    const cool = COOL.exec(said)
    if (cool !== null) {
      const answer = await warmlist($, ['cool', ...(cool[1].match(/\d+/g) ?? [])])
      await lookAndMeasure($) // this thread may be one of them

      return rows($, answer)
    }

    return next(e)
  })

  on('command.run', { command: COMMAND }, async $ => {
    await look($, false)
    const isHiding = closedFor !== lineKey()
    closedFor = isHiding ? lineKey() : ''
    show($)
    const state = warmSince !== '' ? 'Keep-warm is still on' : 'Keep-warm is off (back burner)'

    return {
      text: isHiding ? `Line hidden. ${state}; /warm-line shows it again.` : 'Line shown.',
    }
  })
}
