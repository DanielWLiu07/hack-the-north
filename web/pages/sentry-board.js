// sentry-board.js — a Sentry issue on /telemetry has three states, and the middle one is real.
//
//   open       what Sentry's unresolved feed reports (telemetry.js draws the row; this adds the buttons)
//   resolved   [mark fixed] PUTs status=resolved to Sentry through our own server, which READS THE
//              ISSUE BACK. The card stays on the board, struck through, wearing the status Sentry
//              answered with. Nothing on this card is a local guess: if the read-back fails the row
//              says so and stays open.
//   removed    [remove] only. The server re-checks with Sentry that the issue really is resolved,
//              Seer fires (pages/laser.js), the card is destroyed with the line that says it is
//              fixed, and THEN it leaves this board. It is not deleted in Sentry — it cannot be:
//              there is no delete anywhere in this feature.
//
// The resolved-not-yet-removed ids are remembered in localStorage so an accidental reload mid-demo
// does not lose them; on the way back their status is re-read from Sentry (GET …/issues/status)
// before anything is drawn, so a reloaded page still shows Sentry's word and not ours.
//
// telemetry.js owns the row markup and passes its own `h`/`cssId`/`ago` in, so the extra controls
// are built with the page's idiom and inherit .fail / .fbtn exactly. Styles: sentry-board.css.

import { fireLaser, firing, warmLaser } from '/pages/laser.js';

const STORE = 'gitirl.sentry.board.v1';
const MAX_KEPT = 10;                       // the status re-read is capped at 10 ids server-side too
const COLLAPSE_MS = 1250;                  // the words hold this long after impact, then fade as the embers die
const ARM_MS = 4000;                       // how long [mark fixed] stays armed before it forgets

// A press here changes a SHARED, LIVE Sentry project, so it has to come from a person.
//
// This is not paranoia: web/landing/tools/dev/framewatch.mjs --exercise walks the page and calls
// b.click() on every visible button three times over, and on 2026-09-19 that quietly resolved NINE
// real issues in the gitspace project before anyone noticed. A synthetic click carries
// isTrusted === false; a mouse press, a tap, Enter and Space on a focused button, and a real
// CDP-driven click in an end-to-end test all carry true. So the rule costs a human nothing and
// stops a crawler, an exerciser or a stray script from writing to Sentry on our behalf.
const byHand = (e) => !!(e && e.isTrusted);

const iso = (t) => { try { return new Date(t).toLocaleTimeString(); } catch { return t; } };

async function ask(url, options) {
  const r = await fetch(url, { headers: { accept: 'application/json' }, ...options });
  let body = null;
  try { body = await r.json(); } catch { /* a proxy page, or nothing at all */ }
  if (!r.ok) throw Object.assign(new Error((body && body.detail) || r.statusText || `HTTP ${r.status}`), { body, status: r.status });
  return body;
}

function load() {
  try {
    const raw = JSON.parse(localStorage.getItem(STORE) || '{}');
    return raw && typeof raw === 'object' ? raw : {};
  } catch { return {}; }
}
function save(kept) {
  try { localStorage.setItem(STORE, JSON.stringify(kept)); } catch { /* private mode: it just will not survive a reload */ }
}

export function createBoard({ h, cssId, ago, tellSeer = () => {}, seer = () => null, onChange = () => {}, reduced = false }) {
  // id -> { issue, status, substatus, read_back, read_back_at, note }  · only ones resolved and NOT yet removed
  let kept = load();
  let can = { can_write: true, why_not: null, sentry: null };   // replaced by GET /api/sentry/actions
  const busy = new Set();
  // Destroyed this visit. The live array telemetry.js holds was fetched while the issue was still
  // unresolved, so without this the next redraw would put the card straight back on the board.
  // Not persisted: after a reload Sentry's own `is:unresolved` feed leaves a resolved issue out,
  // and if it ever recurs Sentry regresses it and it SHOULD come back (see sawOpen).
  const removed = new Set();

  const remember = (id, row) => {
    kept[id] = { at: Date.now(), ...row };
    const ids = Object.keys(kept).sort((a, b) => (kept[b].at || 0) - (kept[a].at || 0)).slice(0, MAX_KEPT);
    kept = Object.fromEntries(ids.map((k) => [k, kept[k]]));
    save(kept);
  };
  const forget = (id) => { delete kept[id]; save(kept); };

  // ---- where the shot comes from: Seer's pupil, or the band if Seer never mounted ---------------
  function origin() {
    const s = seer();
    const eye = s && typeof s.eye === 'function' ? s.eye() : null;
    if (eye && Number.isFinite(eye.x) && Number.isFinite(eye.y)) return eye;
    const canvas = document.getElementById('seer');
    const r = canvas && canvas.getClientRects().length ? canvas.getBoundingClientRect() : null;
    if (r) return { x: r.left + r.width * 0.5, y: r.top + r.height * 0.42 };
    return { x: innerWidth * 0.5, y: -60 };            // above the fold: the beam still comes down the page
  }

  /** Put the card low enough that Seer's band is on screen above it, so the beam has somewhere to come from. */
  function frame(el) {
    const r = el.getBoundingClientRect();
    const want = scrollY + r.top - innerHeight * 0.64;
    if (Math.abs(want - scrollY) < 40) return Promise.resolve();
    scrollTo({ top: Math.max(0, want), behavior: reduced ? 'auto' : 'smooth' });
    return new Promise((done) => setTimeout(done, reduced ? 0 : 420));
  }

  // ---- the row's own status line, straight from Sentry ------------------------------------------
  function statusLine(id) {
    const k = kept[id];
    if (!k) return null;
    if (k.read_back === false) {
      return h('p', { class: 'fline sbstate warned' },
        h('b', {}, 'written to Sentry, not confirmed'), ' · ',
        k.read_back_failed_because || 'the read-back failed', ' · this card stays until Sentry answers');
    }
    return h('p', { class: 'fline sbstate' },
      h('b', {}, `Sentry says: ${k.status || 'unknown'}`),
      k.substatus ? ` (${k.substatus})` : null,
      k.read_back_at ? ` · read back at ${iso(k.read_back_at)}` : null,
      ' · still on the board until you remove it');
  }

  // ---- the presses ------------------------------------------------------------------------------
  function fail(el, message) {
    const box = el.querySelector('.sberr') || el.insertBefore(h('p', { class: 'fline sberr' }), el.querySelector('.fbtns'));
    box.replaceChildren(h('b', {}, 'that did not work'), ` · ${message}`);
  }

  async function markFixed(id, issue, el) {
    if (busy.has(id)) return;
    busy.add(id);
    el.classList.add('working');
    tellSeer('thinking', el);
    try {
      const r = await ask(`/api/sentry/issues/${encodeURIComponent(id)}/resolve`, {
        method: 'POST', headers: { 'content-type': 'application/json', accept: 'application/json' },
        body: JSON.stringify({ status: 'resolved' }),
      });
      remember(id, { issue: { ...issue, ...(r.title ? { title: r.title } : {}) }, status: r.status, substatus: r.substatus,
        read_back: r.read_back, read_back_at: r.read_back_at, read_back_failed_because: r.read_back_failed_because });
      tellSeer(r.resolved ? 'verdict' : 'stumped', el);
      onChange();
    } catch (e) {
      tellSeer('stumped', el);
      fail(el, e.message);
    } finally {
      busy.delete(id);
      el.classList.remove('working');
    }
  }

  async function undo(id, el) {
    if (busy.has(id)) return;
    busy.add(id);
    try {
      await ask(`/api/sentry/issues/${encodeURIComponent(id)}/resolve`, {
        method: 'POST', headers: { 'content-type': 'application/json', accept: 'application/json' },
        body: JSON.stringify({ status: 'unresolved' }),
      });
      forget(id);
      onChange();
    } catch (e) {
      fail(el, e.message);
    } finally { busy.delete(id); }
  }

  async function remove(id, el) {
    if (busy.has(id) || firing()) return;
    warmLaser();                  // belt and braces: the server round trip below is ~400 ms of cover
    busy.add(id);

    // The press starts the wind-up AT ONCE and asks Sentry at the same time; the charge holds until
    // the answer lands (laser.js · `ready`). That is the whole reason the press feels instant: the
    // round trip is ~400 ms and it happens underneath the brace instead of in front of it. The BEAM
    // still fires only on Sentry's word — `ready` resolves false and the charge fizzles otherwise.
    let verdict = null, problem = null;
    const ready = ask(`/api/sentry/issues/${encodeURIComponent(id)}/remove`, { method: 'POST' })
      .then((v) => { verdict = v; if (!v.may_remove) problem = v.what_we_did || 'Sentry does not report this issue as resolved'; return !!v.may_remove; })
      .catch((e) => { problem = e.message; return false; });
    frame(el);                                   // scrolls while the charge builds; not awaited
    tellSeer('thinking', el);                    // Seer braces: the lens brightens and the arcs tighten
    el.classList.add('bracing');
    const label = () => (verdict && verdict.short_id) || id;
    let struck = 0;
    const fired = await fireLaser({
      origin, target: el, reduced, ready,
      onImpact: () => {
        struck = performance.now();
        removed.add(id);                 // from this instant the board is rid of it, redraw or no redraw
        el.classList.remove('bracing');
        el.classList.add('zapped');
        // the page's own voice: what happened, where it now lives, and what is leaving
        el.replaceChildren(
          h('p', { class: 'fline zapline' }, h('b', {}, '✓ FIXED'), ' · ', h('span', { class: 'mono' }, label())),
          h('p', { class: 'fline zapsub' }, 'Sentry has it resolved · the issue stays there, this card does not.'));
        tellSeer('verdict', el);
        // the list closes the gap while the embers are still falling, not after
        setTimeout(() => el.classList.add('gone'), COLLAPSE_MS);
      },
    });
    el.classList.remove('bracing');
    if (!fired) {
      // the charge fizzled: Sentry would not confirm, so nothing was destroyed and nothing changed
      tellSeer('stumped', el);
      const why = problem || 'Sentry did not answer';
      fail(el, /card stays/.test(why) ? why : `${why} · the card stays`);
      busy.delete(id);
      return;
    }
    // The words outlive the beam, and they have to in BOTH modes — the reduced-motion shot is over
    // in half a second, and without this wait the re-render below took the card away before anyone
    // could read what it said. Nothing animates during it but the single opacity fade.
    await new Promise((r) => setTimeout(r, Math.max(0, struck + COLLAPSE_MS + 220 - performance.now())));
    forget(id);
    busy.delete(id);
    onChange();
  }

  // ---- what telemetry.js calls -------------------------------------------------------------------
  return {
    /** The ids this board is holding resolved (so telemetry.js can keep them in the list). */
    keptIds: () => Object.keys(kept),

    /** The watcher polls `is:unresolved`, so an issue arriving on the live feed is one SENTRY now
     *  calls open — it recurred and Sentry regressed it. The board stops holding it as fixed. */
    sawOpen(id) {
      const k = String(id || '');
      const had = !!kept[k] || removed.delete(k);     // a destroyed card comes BACK if Sentry reopens the issue
      if (kept[k]) forget(k);
      return had;
    },

    /** The live (unresolved) issues, plus the ones this board resolved and is still holding. */
    merge(live) {
      const open = live.filter((i) => !removed.has(String(i.id)));
      const seen = new Set(open.map((i) => String(i.id)));
      const held = Object.entries(kept)
        .filter(([id]) => !seen.has(id) && !removed.has(id))
        .sort((a, b) => (b[1].at || 0) - (a[1].at || 0))
        .map(([id, k]) => ({ ...(k.issue || {}), id }));
      return [...open, ...held];        // held ones sit after the open ones: they no longer need anybody
    },

    /** Add the state badge, the strike-through and the buttons to a row telemetry.js has just built. */
    decorate(el, issue) {
      const id = String(issue && issue.id || '');
      if (!id) return el;
      const k = kept[id];
      el.classList.toggle('resolved', !!k);
      const head = el.querySelector('.fline');
      if (k && head) {
        head.prepend(h('span', { class: 'sbbadge' }, k.read_back === false ? 'unconfirmed' : 'fixed'), ' ');
      }
      const line = statusLine(id);
      const btns = el.querySelector('.fbtns');
      if (line && btns) el.insertBefore(line, btns);
      if (!btns) return el;
      const blocked = !can.can_write;
      const dead = (label, why) => h('span', { class: 'fbtn off', 'aria-disabled': 'true', title: why || '' }, label);
      // one deliberate press, by hand (see byHand): fires straight away
      const press = (label, cls, run, warm) => {
        const b = h('button', { type: 'button', class: `fbtn ${cls}` }, label);
        b.addEventListener('click', (e) => { if (byHand(e)) run(); });
        if (warm) {
          // the beam's canvas and sprites are built the moment a hand comes near [remove], so the
          // press itself has nothing left to allocate (laser.js · warmLaser)
          const once = { once: true };
          b.addEventListener('pointerenter', warmLaser, once);
          b.addEventListener('focus', warmLaser, once);
        }
        return b;
      };
      // two deliberate presses: the first arms and says so, the second writes to Sentry. The write
      // is the one thing on this page that changes a service outside it — it asks twice on purpose.
      const arm = (label, cls, run) => {
        const b = h('button', { type: 'button', class: `fbtn ${cls}` }, label);
        let timer = 0;
        const disarm = () => { clearTimeout(timer); b.classList.remove('armed'); b.textContent = label; b.dataset.armed = ''; };
        b.addEventListener('click', (e) => {
          if (!byHand(e)) return;
          if (b.dataset.armed !== '1') {
            b.dataset.armed = '1';
            b.classList.add('armed');
            b.textContent = 'confirm fix';
            b.title = 'presses again to write status=resolved to Sentry';
            timer = setTimeout(disarm, ARM_MS);
            return;
          }
          disarm();
          run();
        });
        b.addEventListener('blur', disarm);
        return b;
      };
      if (!k) {
        btns.prepend(blocked
          ? dead('mark fixed', can.why_not || (can.sentry && can.sentry.reason))
          : arm('mark fixed', 'fixbtn', () => markFixed(id, issue, el)));
      } else {
        btns.prepend(
          blocked ? dead('remove', can.why_not) : press('remove', 'zapbtn', () => remove(id, el), true),
          blocked ? null : press('reopen', 'undobtn', () => undo(id, el)));
      }
      return el;
    },

    /** One line under the panel heading saying what the buttons do and whether they can. */
    note() {
      if (!can.can_write) {
        return h('p', { class: 'slot sbnote' }, can.why_not
          || (can.sentry && can.sentry.reason)
          || 'resolving an issue is not available on this server.');
      }
      return h('p', { class: 'slot sbnote' },
        h('b', {}, 'mark fixed'), ' writes status=resolved to Sentry and prints the status Sentry answers with. ',
        h('b', {}, 'remove'), ' re-checks Sentry, then Seer destroys the card. Nothing is ever deleted in Sentry.');
    },

    /** Read /api/sentry/actions, then re-read every held issue's status FROM SENTRY. */
    async restore() {
      try { can = await ask('/api/sentry/actions'); } catch { /* keep the optimistic default; a press will say why */ }
      const ids = Object.keys(kept).slice(0, MAX_KEPT);
      if (!ids.length) return;
      try {
        const r = await ask(`/api/sentry/issues/status?ids=${encodeURIComponent(ids.join(','))}`);
        if (!r || r.available !== true) return;                 // Sentry paused: keep what we had, say nothing new
        const fresh = new Map((r.issues || []).map((i) => [i.id, i]));
        for (const id of ids) {
          const got = fresh.get(id);
          if (!got) { forget(id); continue; }                   // Sentry has no such issue any more
          if (got.status !== 'resolved' && got.status !== 'ignored') { forget(id); continue; }  // it regressed: it is open again
          remember(id, { issue: { ...(kept[id].issue || {}), id, title: got.title, short_id: got.short_id, permalink: got.permalink, count: got.count, last_seen: got.last_seen },
            status: got.status, substatus: got.substatus, read_back: true, read_back_at: got.read_back_at });
        }
      } catch { /* the re-read failed: the cards stay as they were, and the next press will report it */ }
      onChange();
    },
  };
}
