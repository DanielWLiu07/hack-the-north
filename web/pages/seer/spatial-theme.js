export const SPATIAL_THEME = `
.spatial-telemetry { min-width:0; }
.spatial-tabs { display:flex;flex-wrap:wrap;gap:6px;margin:0 0 18px;padding:6px;background:#090b12;border:1px solid rgba(235,231,255,.12);border-radius:14px; }
.spatial-tabs button { border:0;border-radius:9px;background:transparent;color:#aaa6bb;padding:10px 14px;font:11px var(--mono);cursor:pointer; }
.spatial-tabs button[aria-selected=true] { background:#292439;color:#f0ddff;box-shadow:inset 0 0 0 1px #675378; }
.spatial-tabs button:focus-visible,.spatial-toolbar :focus-visible { outline:2px solid #dcbdff;outline-offset:3px; }
.spatial-panel { min-width:0; }
.spatial-toolbar { display:flex;align-items:center;flex-wrap:wrap;gap:8px;margin-bottom:12px;font:12px var(--mono); }
.spatial-toolbar select { color:#efeaf5;background:#191724;border:1px solid #554460;border-radius:7px;max-width:100%;padding:8px; }
.spatial-toolbar label { margin-right:auto; }
.spatial-status { font:11px/1.7 var(--mono);color:#aeb7c6;overflow-wrap:anywhere;margin:14px 0; }
.spatial-warning { color:#efbb78; }
.spatial-stage { width:100%;height:clamp(280px,48vw,460px);border-radius:16px;overflow:hidden;background:#080b12;border:1px solid #253143; }
.spatial-stage canvas { display:block;width:100%;height:100%;touch-action:none; }
.spatial-content { display:grid;gap:16px;margin-top:16px;min-width:0; }
.spatial-content p { margin:0;font:12px/1.6 var(--sans); }
.spatial-facts { display:grid;grid-template-columns:minmax(100px,.5fr) minmax(0,1fr);gap:8px 16px;font:11px/1.5 var(--mono);margin:0; }
.spatial-facts dt { color:#a09aae; }.spatial-facts dd { margin:0;overflow-wrap:anywhere; }
.spatial-raw { min-width:0;font:11px var(--mono); }.spatial-raw summary { cursor:pointer;color:#c9bfd7; }
.spatial-raw pre { white-space:pre-wrap;overflow-wrap:anywhere;max-height:320px;overflow:auto;font:11px/1.5 var(--mono); }
.spatial-images { display:grid;grid-template-columns:repeat(auto-fit,minmax(min(260px,100%),1fr));gap:16px; }
.spatial-images article { padding:14px;border:1px solid #343040;border-radius:12px;min-width:0; }
.spatial-images h3 { font:12px var(--mono);margin:0 0 12px; }.spatial-images img { display:block;width:100%;height:auto;border-radius:8px;margin-bottom:14px; }.spatial-images button { margin-top:14px; }
.spatial-table-wrap { overflow:auto;max-height:420px; }.spatial-table-wrap table { width:100%;border-collapse:collapse;font:11px/1.5 var(--mono); }.spatial-table-wrap th { text-align:left;color:#b5abc2; }.spatial-table-wrap td,.spatial-table-wrap th { padding:10px 12px;border-bottom:1px solid #302a3a;white-space:nowrap; }.spatial-table-wrap small { display:block;color:#92899e; }
.spatial-telemetry [hidden] { display:none!important; }
.rl-lenses { display:flex;gap:4px; }.rl-lenses button { padding:6px 9px;font-size:10px; }.rl-lenses button[aria-pressed=true] { color:#fff;background:#443652; }
.rl-stage.right-eye img { transform:translateX(-50%); }
@media(max-width:760px) { .spatial-tabs { gap:3px;padding:4px; }.spatial-tabs button { flex:1 1 40%;padding:9px 5px;font-size:10px; }.spatial-toolbar .navbtn { padding:7px 9px;font-size:10px; }.spatial-toolbar label { width:100%; }.rl-lenses button { padding:5px 6px;font-size:9px; } }
`;
