export default `
.tc {
  --tc-bg: var(--md-bg, #fff); --tc-fg: var(--md-fg, #242424);
  --tc-muted: var(--md-fg-muted, #616161); --tc-border: var(--md-border, #c7c7c7);
  --tc-surface: var(--md-surface, #f3f3f3); --tc-accent: var(--md-accent, #005fb8);
  --tc-good: light-dark(#13713c, #6ad9a0); --tc-bad: light-dark(#ba3028, #ff948d);
  --tc-grid: color-mix(in srgb, var(--tc-muted) 22%, transparent);
  color: var(--tc-fg); background: var(--tc-bg); font: 14px/1.55 system-ui, sans-serif;
  padding: 22px 24px 32px; min-width: 0; box-sizing: border-box; color-scheme: light dark;
}
:root[data-theme="light"] .tc { color-scheme: light; }
:root[data-theme="dark"] .tc { color-scheme: dark; }
.tc * { box-sizing: border-box; }
.tc [hidden] { display: none !important; }
.tc h1, .tc h2, .tc h3, .tc p { margin: 0; }
.tc h1 { font-size: 24px; letter-spacing: .02em; }
.tc h2 { font-size: 17px; }
.tc h3 { font-size: 14px; }
.tc .tc-evolution { margin-bottom: 24px; padding-bottom: 16px; border-bottom: 1px solid var(--tc-border); }
.tc .tc-head { display: flex; align-items: center; justify-content: space-between; gap: 12px; flex-wrap: wrap; }
.tc .tc-kicker { color: var(--tc-accent); font: 600 11px/1.4 ui-monospace, monospace; letter-spacing: .13em; }
.tc .tc-sub { color: var(--tc-muted); font-size: 12px; margin-top: 3px; }
.tc .tc-tag { border: 1px solid var(--tc-border); padding: 4px 9px; border-radius: 999px; font-size: 12px; }
.tc .tc-status { display: flex; flex-wrap: wrap; gap: 6px 18px; color: var(--tc-muted); font-size: 12px; margin: 14px 0; }
.tc .tc-toolbar { display: flex; flex-wrap: wrap; align-items: center; gap: 8px; padding: 12px 0 17px; }
.tc button, .tc select, .tc input {
  color: var(--tc-fg); background: var(--tc-bg); border: 1px solid var(--tc-border);
  font: inherit; padding: 7px 10px; border-radius: 6px; min-height: 34px; min-width: 0;
}
.tc button { cursor: pointer; }
.tc button:hover:not(:disabled) { background: var(--tc-surface); }
.tc button:disabled { color: var(--tc-muted); opacity: .6; cursor: default; }
.tc button:focus-visible, .tc input:focus-visible, .tc select:focus-visible, .tc [tabindex]:focus-visible {
  outline: 2px solid var(--md-focus, #005fb8); outline-offset: 3px;
}
.tc button.tc-primary { color: var(--tc-bg); background: var(--tc-accent); border-color: var(--tc-accent); }
.tc .tc-card { border: 1px solid var(--tc-border); border-radius: 10px; padding: 17px 18px; margin-bottom: 16px; overflow: hidden; }
.tc .tc-chart-head { display: flex; align-items: center; justify-content: space-between; gap: 12px; flex-wrap: wrap; }
.tc .tc-date { display: flex; align-items: center; gap: 7px; flex-wrap: wrap; }
.tc .tc-date input { font-variant-numeric: tabular-nums; }
.tc .tc-chart { display: block; width: 100%; height: auto; min-height: 200px; margin-top: 7px; overflow: visible; }
.tc .tc-chart text { fill: var(--tc-muted); font: 12px system-ui, sans-serif; }
.tc .tc-price { fill: none; stroke: var(--tc-accent); stroke-width: 2.4; vector-effect: non-scaling-stroke; }
.tc .tc-gridline { stroke: var(--tc-grid); stroke-width: 1; }
.tc .tc-point { cursor: help; }
.tc .tc-point circle { fill: var(--tc-muted); stroke: var(--tc-bg); stroke-width: 2; }
.tc .tc-point[data-result="correct"] circle { fill: var(--tc-good); }
.tc .tc-point[data-result="incorrect"] circle { fill: var(--tc-bad); }
.tc .tc-point text { fill: var(--tc-bg); font-size: 13px; font-weight: 700; text-anchor: middle; dominant-baseline: central; }
.tc .tc-tooltip { color: var(--tc-muted); font-size: 12px; min-height: 20px; }
.tc .tc-empty { text-align: center; padding: 68px 12px; color: var(--tc-muted); }
.tc .tc-legend { display: flex; flex-wrap: wrap; gap: 14px; color: var(--tc-muted); font-size: 12px; margin-top: 7px; }
.tc .tc-good { color: var(--tc-good); } .tc .tc-bad { color: var(--tc-bad); }
.tc .tc-summary { display: flex; flex-wrap: wrap; gap: 10px 30px; padding: 13px 0; }
.tc .tc-value { font-size: 23px; font-weight: 650; font-variant-numeric: tabular-nums; }
.tc .tc-label { color: var(--tc-muted); font-size: 12px; }
.tc .tc-conclusion { border-left: 3px solid var(--tc-accent); padding: 10px 12px; background: var(--tc-surface); margin: 10px 0 14px; }
.tc .tc-scroll { overflow-x: auto; }
.tc table { border-collapse: collapse; width: 100%; font-size: 13px; font-variant-numeric: tabular-nums; }
.tc th, .tc td { padding: 9px 10px; text-align: right; border-bottom: 1px solid var(--tc-grid); white-space: nowrap; }
.tc th:first-child, .tc td:first-child { text-align: left; }
.tc th { color: var(--tc-muted); font-size: 12px; font-weight: 500; }
.tc tr[data-method="jev"] { background: var(--tc-surface); }
.tc .tc-note { color: var(--tc-muted); font-size: 12px; margin-top: 12px; }
.tc .tc-lock { display: flex; align-items: center; flex-wrap: wrap; gap: 10px; }
.tc .tc-lock p { flex: 1 1 240px; color: var(--tc-muted); font-size: 12px; }
.tc .tc-error { color: var(--tc-bad); margin-bottom: 10px; }
.tc .tc-dialog { padding: 16px; border: 2px solid var(--tc-accent); border-radius: 8px; background: var(--tc-surface); margin-bottom: 16px; }
.tc .tc-dialog-actions { display: flex; gap: 8px; margin-top: 12px; }
.tc .tc-dialog label { display: flex; align-items: center; gap: 10px; margin-top: 10px; }
@media (max-width: 650px) {
  .tc { padding: 14px 12px; }
  .tc .tc-card { padding: 13px 10px; }
  .tc h1 { font-size: 21px; }
  .tc .tc-chart { min-height: 160px; }
  .tc .tc-toolbar { gap: 6px; }
  .tc button, .tc select { font-size: 12px; }
}
`;
