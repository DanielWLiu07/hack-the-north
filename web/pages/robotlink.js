// robotlink.js — what this COPY of the site is, and whether a robot is feeding it.
//
// The same code and the same room history run on the laptop and on a public box. The difference is that no
// robot can reach the public one, so its live panels have nothing arriving. Left alone that reads as broken
// rather than as a boundary, which is the thing this module exists to fix.
//
// TWO RULES, and they are the whole design:
//   LEAD WITH WHAT WORKS. A visitor should meet a page that works, not a setup form. The history, the search,
//   every past capture and its replay and the Sentry board are all live here with no robot at all — say that
//   first, and put "connect your own robot" behind a disclosure for the one person in a hundred who has one.
//   NEVER CLAIM A ROBOT IS CONNECTED. `connected` comes from GET /api/link and is true because data ARRIVED,
//   never because a token is configured. A page that says "connected" on the strength of an environment
//   variable is lying, and on a public copy it is lying to a stranger about someone else's room.
//
// The publishing banner is deliberately drawn on BOTH ends from the same state. An operator must not be able
// to forget their camera is being published, and a person walking into the room is entitled to see it too.
const POLL_MS = 4000, PUBLISH_POLL_MS = 2000;
const $ = (tag, attrs = {}, ...kids) => {
  const n = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (v === false || v == null) continue;
    if (k === 'text') n.textContent = v; else if (k === 'onclick') n.addEventListener('click', v); else n.setAttribute(k, v === true ? '' : v);
  }
  n.append(...kids.flat().filter((k) => k != null && k !== false));
  return n;
};
const ask = async (url) => {
  try { const r = await fetch(url, { headers: { accept: 'application/json' }, cache: 'no-store' });
    return r.ok ? await r.json() : null; } catch { return null; }
};
const clock = (s) => { const t = Math.max(0, Math.round(s)); return `${Math.floor(t / 60)}:${String(t % 60).padStart(2, '0')}`; };

let host = null, banner = null, timer = 0;

function mount() {
  if (host) return host;
  const band = document.querySelector('#stage') || document.querySelector('main') || document.body;
  if (!band) return null;
  banner = $('div', { class: 'rl-publishing', hidden: true, role: 'status' });
  host = $('section', { class: 'rl-copy', hidden: true, 'aria-label': 'What this copy of the site is' });
  band.prepend(banner);
  band.parentNode ? band.after(host) : band.append(host);
  return host;
}

// ── the banner: on while a window is open, on BOTH ends, and it says when it stops ──────────────
function paintBanner(live) {
  if (!banner) return;
  if (!live || !live.publishing) { banner.hidden = true; banner.replaceChildren(); return; }
  banner.hidden = false;
  banner.dataset.live = live.live ? '1' : '';
  banner.replaceChildren(
    $('span', { class: 'rl-dot', 'aria-hidden': 'true' }),
    $('strong', { text: live.live ? 'PUBLISHING THE LIVE CAMERA' : 'PUBLISHING — no frame right now' }),
    $('span', { class: 'rl-until', text: `stops in ${clock(live.seconds_left)}` }),
    // the honest half: a window can be open while nothing is arriving, and both facts are shown
    live.publishing && !live.live
      ? $('span', { class: 'rl-why', text: `nothing has arrived for ${live.stale_after_s}s — the picture is not live` })
      : $('span', { class: 'rl-why', text: `frame ${live.frame_age_s ?? '?'}s old` }));
}

// ── what this copy is ───────────────────────────────────────────────────────────────────────────
function paintCopy(link, live) {
  if (!host || !link) return;
  const kids = [];
  if (link.connected) {
    const l = link.last || {};
    kids.push($('p', { class: 'rl-line' },
      $('span', { class: 'rl-dot', 'data-on': '' , 'aria-hidden': 'true' }),
      $('strong', { text: 'A robot is feeding this copy' }),
      $('span', { class: 'rl-dim', text: `${l.event ? `${l.event} ${l.age_s}s ago` : ''}${link.since ? ` · since ${new Date(link.since).toLocaleTimeString()}` : ''}` })));
  } else {
    // LEAD with what works. The absence comes second and is stated as a boundary, not a failure.
    kids.push($('p', { class: 'rl-lead' }, $('strong', { text: 'This is a public copy of a real room.' }),
      ' Everything below is real and reads from the same history as the room itself:'));
    const what = $('ul', { class: 'rl-have' });
    for (const item of link.without_a_robot || []) what.append($('li', { text: item }));
    kids.push(what);
    kids.push($('p', { class: 'rl-dim', text: link.why_not || 'No robot is connected to this copy.' }));
    if (live && !live.publishing && live.detail) kids.push($('p', { class: 'rl-dim', text: live.detail }));
    // the offer, quietly, for the one visitor who has a robot of their own
    if (link.accepts_remote && link.how) {
      const how = link.how;
      kids.push($('details', { class: 'rl-connect' },
        $('summary', { text: 'Connect your own robot' }),
        $('p', { text: 'If the room on this page is yours, point your hub at this copy and it comes alive. The token is your room’s, not ours.' }),
        $('pre', { class: 'rl-env', text: (how.env || []).join('\n') }),
        $('p', { class: 'rl-dim', text: `it accepts: ${(how.accepts || []).join(', ')}` }),
        $('p', { class: 'rl-dim', text: how.note || '' })));
    }
  }
  host.replaceChildren(...kids);
  host.hidden = !kids.length;
}

async function tick() {
  clearTimeout(timer);
  if (!mount()) { timer = setTimeout(tick, POLL_MS); return; }
  const [link, live] = await Promise.all([ask('/api/link'), ask('/api/live/state')]);
  paintBanner(live);
  paintCopy(link, live);
  // while something is being published, look more often: the countdown and the stop must not lag
  timer = setTimeout(tick, live && live.publishing ? PUBLISH_POLL_MS : POLL_MS);
}

tick();
document.addEventListener('visibilitychange', () => { if (!document.hidden) tick(); });
