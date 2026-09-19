export const GLASS_THEME = `
  body.seer-room { --seer-gutter:clamp(120px,13vw,190px); --glass-line:rgba(235,231,255,.12);
    background:radial-gradient(ellipse at 70% 0%,#191725 0,transparent 60%),#09090e; }
  .seer-room .bandtext { position:absolute;z-index:1;left:calc(var(--seer-gutter) + 18px);top:90px;right:220px; }
  .seer-room .bandtext h1 { margin:6px 0 4px;font:550 clamp(32px,3.5vw,48px)/1.1 var(--sans);letter-spacing:-.045em; }
  .seer-wordmark { display:block;width:clamp(210px,27vw,370px);max-width:100%;height:auto;color:#c49aff;filter:drop-shadow(0 0 12px #a46aef50) drop-shadow(0 2px 2px #622f8230); }
  .seer-room .bandtext .q { font-size:15px;max-width:none;color:#aca8bb;margin:10px 0 0; }
  .seer-room > .robotlive { margin-top:140px; }
  .seer-room .bandnav { top:94px;right:32px;align-items:center;gap:12px; }
  .seer-room .navbtn { border-radius:999px;background:rgba(255,255,255,.045);border-color:var(--glass-line);padding:10px 16px; }
  .seer-state { font:11px var(--mono);color:#aaa5bc; }
  .seer-room[data-seer-state="thinking"] .seer-state { color:#efaeeb; }
  .seer-room > .tboard,.seer-room > .state-msg { width:calc(100% - var(--seer-gutter) - 30px);margin:180px 30px 0 var(--seer-gutter);
    padding:0 0 64px;background:none;gap:20px;grid-template-columns:minmax(0,.8fr) minmax(0,1.2fr); }
  .seer-room .tboard > * { min-width:0; }
  .seer-room .tboard > .tags,.seer-room .tboard > section:first-of-type,.seer-room #board { grid-column:1/-1; }
  .seer-room .tboard > section { padding:22px;border:1px solid var(--glass-line);border-radius:24px;
    background:radial-gradient(ellipse at 0 0,rgba(172,140,221,.075),transparent 65%),linear-gradient(135deg,rgba(255,255,255,.04),rgba(255,255,255,.01)),#0e0e17;
    box-shadow:inset 0 1px 0 rgba(255,255,255,.09),0 16px 48px rgba(0,0,0,.18);backdrop-filter:blur(18px); }
  .seer-room .tboard > section:first-of-type { background:radial-gradient(ellipse at 85% 0,rgba(160,112,224,.13),transparent 65%),#11111b; }
  .seer-room .rulehead::before { content:'';width:5px;height:14px;border-radius:3px;background:#b39bcf;flex:none;box-shadow:0 0 14px #a77dc433; }
  .seer-room .rulehead { align-items:center; }
  .seer-room #h-live .right { margin-left:auto;max-width:55%;text-align:right; }
  .seer-room .tags .tag { border-style:solid;border-color:rgba(210,192,235,.13);background:rgba(167,136,210,.055);font-size:10px;padding:7px 12px;letter-spacing:.055em; }
  .seer-room .rulehead { border:0;font:600 11px/1.5 var(--mono);letter-spacing:.12em;color:#a9a5b8;margin-bottom:18px;padding:0;gap:6px; }
  .seer-room .rulehead .right { font-size:10px;letter-spacing:0;color:#9994aa; }
  .seer-room .tiles { grid-template-columns:repeat(4,minmax(0,1fr));gap:12px; }
  .seer-room .tile,.seer-room .tile:first-child,.seer-room .tile:nth-child(3) { padding:18px;border:1px solid rgba(255,255,255,.075);border-radius:16px;background:linear-gradient(155deg,rgba(255,255,255,.045),transparent);transition:border-color .2s,background-color .2s; }
  .seer-room .tile:hover { border-color:rgba(217,187,255,.28);background-color:rgba(181,135,239,.045); }
  .seer-room .tile.bad { background:linear-gradient(140deg,rgba(242,160,60,.07),transparent);border-color:rgba(242,160,60,.15); }
  .seer-room .tile .name { font-size:10px;letter-spacing:.07em;color:#a6a0b5; }
  .seer-room .tile .val { font:500 clamp(30px,3.5vw,50px)/1.15 var(--sans);font-variant-numeric:tabular-nums;letter-spacing:-.05em;margin:14px 0; }
  .seer-room .tile .val small { font-size:11px;letter-spacing:0; }
  .seer-room .tile .rule { font-size:11px;line-height:1.5;color:#9994a8; }
  .seer-room .stack { grid-template-columns:minmax(0,1fr);gap:18px; }
  .seer-room .stackstate { font-size:14px;line-height:1.6;color:#c8c3d4; }
  .seer-room .products { font-size:12px;gap:12px 16px;line-height:1.5; }
  .seer-room .products dt { font-size:10px;letter-spacing:.06em;color:#ded8ec; }
  .seer-room .products dd { color:#a09bad;padding-bottom:12px;border-bottom:1px solid rgba(255,255,255,.055); }
  .seer-room .slot.big { border:1px dashed var(--glass-line);border-radius:14px;font-size:13px;line-height:1.7;background:rgba(0,0,0,.08); }
  .seer-room .ledger { gap:14px; }
  .seer-room .row,.seer-room .fail { border:1px solid var(--glass-line);border-radius:16px;padding:18px;background:rgba(255,255,255,.025); }
  .seer-room .row.sel { border-color:rgba(223,197,255,.28);background:rgba(201,174,255,.035); }
  .seer-room .fail { border-left:2px solid rgba(242,160,60,.5);background:linear-gradient(120deg,rgba(242,160,60,.045),transparent 65%); }
  .seer-room .fail .fline { font-size:17px;line-height:1.5; }
  .seer-room .fail .fdetail { display:block;font:13px/1.6 var(--sans);color:#b9b2c3;margin:6px 0 12px; }
  .seer-room .fbtn { transition:background .18s,border-color .18s,box-shadow .18s; }
  .seer-room .fbtn.ask:hover { box-shadow:0 0 20px rgba(213,144,238,.12); }
  .seer-room .fbtn:focus-visible,.seer-room .navbtn:focus-visible { outline:2px solid #d5a9ed;outline-offset:4px; }
  .seer-room[data-seer-state="thinking"] .fans.thinking { border:1px solid rgba(211,162,241,.4);box-shadow:inset 3px 0 #d3a2f1; }
  @media(prefers-reduced-motion:reduce) { .seer-room .tile,.seer-room .fbtn { transition:none; } }
  .seer-room .row.bad.sel { border-color:rgba(242,160,60,.28); }
  .seer-room .rowbody { grid-template-columns:minmax(150px,.65fr) minmax(250px,1.5fr);gap:22px; }
  .seer-room .c3 { grid-column:1/-1; }
  .seer-room .capid { font-size:20px; }
  .seer-room .verd { font-size:12px;letter-spacing:.05em;padding:5px 9px;border-radius:7px;background:rgba(255,255,255,.045); }
  .seer-room .row .because,.seer-room .row .more { font-size:12px; }
  .seer-room .key { font-size:11px;gap:8px 18px;margin-bottom:18px; }
  .seer-room .sentrycol { border-color:var(--glass-line); }
  .seer-room .fbtn,.seer-room .copy,.seer-room .cfg select { border:1px solid var(--glass-line);border-radius:9px;background:rgba(255,255,255,.04); }
  .seer-room .fbtn { padding:8px 12px; }
  .seer-room .fbtn.ask { background:rgba(218,154,235,.1);border-color:rgba(218,154,235,.22);color:#efc4f0; }
  .seer-room .fbtn:hover { background:rgba(255,255,255,.09);border-color:rgba(255,255,255,.23); }
  .seer-room .fans { border-radius:10px;padding:12px;font-size:13px; }
  .seer-room .fans.thinking { background:rgba(203,128,238,.065);border-color:rgba(203,128,238,.3); }
  .seer-room .seercfg { border-top:1px solid var(--glass-line);padding-top:18px; }
  .seer-room .strip svg { max-width:100%; }
  @media(min-width:1101px) {
    .seer-room > .tboard,.seer-room > .state-msg { width:calc(100% - var(--seer-gutter) - 140px);margin-right:140px; }
  }
  @media(max-width:1100px) {
    .seer-room > .tboard { grid-template-columns:minmax(0,1fr); }
    .seer-room .tboard > * { grid-column:1; }
    .seer-room .tiles { grid-template-columns:repeat(2,minmax(0,1fr)); }
  }
  @media(max-width:760px) {
    body.seer-room { --seer-gutter:48px; }
    .seer-room .bandtext { left:64px;top:86px;right:16px; }
    .seer-room .bandtext h1 { font-size:32px; }
    .seer-room > .robotlive { margin-top:110px; }
    .seer-room .bandtext .q { font-size:12px;line-height:1.5;max-width:28ch; }
    .seer-room .bandnav { top:58px;right:16px;gap:8px; }
    .seer-room .navbtn { padding:6px 10px;font-size:10px; }
    .seer-state { font-size:9px; }
    .seer-room > .tboard,.seer-room > .state-msg { width:calc(100% - 88px);margin:152px 40px 0 48px;padding:0 0 40px;gap:12px; }
    .seer-room .tboard > section { padding:14px;border-radius:18px; }
    .seer-room .tiles { gap:8px; }
    .seer-room .tile,.seer-room .tile:first-child,.seer-room .tile:nth-child(3) { padding:12px 10px;border-radius:12px; }
    .seer-room .tile .val { font-size:29px; }
    .seer-room .tile .val small { display:block;margin-left:0; }
    .seer-room .tile .name { font-size:9px;overflow-wrap:anywhere; }
    .seer-room .products { grid-template-columns:minmax(0,1fr);gap:4px; }
    .seer-room .products dd { margin-bottom:10px; }
    .seer-room .rowbody { grid-template-columns:minmax(0,1fr); }
    .seer-room .row,.seer-room .fail { padding:12px; }
    .seer-room .c3 { grid-column:auto; }
    .seer-room .gate3 { grid-template-columns:minmax(0,1fr); }
    .seer-room .srow.col { grid-template-columns:minmax(0,1fr); }
    .seer-room .fbtns { gap:6px; }
  }
`;
