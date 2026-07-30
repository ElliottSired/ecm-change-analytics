"""
Persists a DataFrame to a medallion-layer folder (bronze/silver/gold) as Parquet,
alongside a small JSON manifest recording lineage: what built it, from what, and when.

Usage, right after a table is built in a notebook cell:

    write_layer(fact_cost_enriched, "fact_cost_enriched", ROOT / "data/silver",
                sources=["fact_cost", "system_dim", "subsystem_dim"])

This has no knowledge of any specific project, table name, or notebook structure —
it just saves what you hand it, where you tell it, with whatever lineage you declare.
"""
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd


def write_layer(df: pd.DataFrame, name: str, folder: Path, sources: list[str] | None = None) -> Path:
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)

    parquet_path = folder / f"{name}.parquet"
    df.to_parquet(parquet_path, index=False)

    manifest = {
        "table": name,
        "layer": folder.name,
        "built_from": sources or [],
        "built_at": datetime.now(timezone.utc).isoformat(),
        "rows": len(df),
        "columns": list(df.columns),
    }
    manifest_path = folder / f"{name}.json"
    manifest_path.write_text(json.dumps(manifest, indent=2))

    print(f"wrote {name:22s} {len(df):>6,} rows -> {parquet_path.relative_to(folder.parent.parent)}"
          f"  (from: {', '.join(sources) if sources else '—'})")
    return parquet_path


def read_lineage(data_root: Path) -> list[dict]:
    """Reads every *.json manifest under data_root/{bronze,silver,gold}/ and
    returns them as a flat list of dicts, sorted bronze -> silver -> gold.
    The manifest's own "layer" field is the source of truth, not the folder name."""
    data_root = Path(data_root)
    layer_order = {"bronze": 0, "silver": 1, "gold": 2}
    manifests = []
    seen = set()
    for folder_name in ("bronze", "silver", "gold"):
        folder = data_root / folder_name
        if not folder.exists():
            continue
        for manifest_path in sorted(folder.glob("*.json")):
            m = json.loads(manifest_path.read_text())
            key = (m["layer"], m["table"])
            if key not in seen:
                seen.add(key)
                manifests.append(m)
    manifests.sort(key=lambda m: (layer_order.get(m["layer"], 99), m["table"]))
    return manifests


def render_mermaid(manifests: list[dict]) -> str:
    """Renders a lineage graph (Mermaid syntax) from manifests, e.g. from read_lineage()."""
    layer_class = {"bronze": "bronze", "silver": "silver", "gold": "gold"}
    lines = ["graph LR"]
    for m in manifests:
        node_id = m["table"]
        label = f'{m["table"]}<br/>{m["rows"]:,} rows'
        lines.append(f'  {node_id}["{label}"]:::{layer_class.get(m["layer"], "")}')
        for src in m["built_from"]:
            lines.append(f"  {src} --> {node_id}")
    lines.append("  classDef bronze fill:#cd7f32,color:#fff")
    lines.append("  classDef silver fill:#adb5bd,color:#000")
    lines.append("  classDef gold fill:#d4af37,color:#000")
    return "\n".join(lines)


def print_lineage(manifests: list[dict]) -> None:
    """Prints a readable text summary of lineage — table, layer, rows, sources."""
    for m in manifests:
        sources = ", ".join(m["built_from"]) if m["built_from"] else "(source data)"
        print(f'[{m["layer"]:6s}] {m["table"]:32s} {m["rows"]:>7,} rows  <- {sources}')


def render_html(manifests: list[dict], project_name: str) -> str:
    """Renders a standalone HTML lineage catalog page — a dependency graph plus a
    table of every bronze/silver/gold table, read from the manifests. Self-contained,
    no external requests; open the resulting file directly in a browser."""
    counts = {"bronze": 0, "silver": 0, "gold": 0}
    for m in manifests:
        counts[m["layer"]] = counts.get(m["layer"], 0) + 1
    total = len(manifests)
    graph = render_mermaid(manifests)
    data_json = json.dumps(manifests)

    return f"""<!doctype html>
<html><head><meta charset="utf-8"/><title>{project_name} — Lineage Catalog</title>
<script src="https://cdn.jsdelivr.net/npm/mermaid@10/dist/mermaid.min.js"></script>
<style>
  :root {{
    --bg: #f6f4ef; --surface: #ffffff; --ink: #1c2024; --ink-dim: #6b6f76;
    --border: #e2ddd1; --accent: #3d6b7d; --bronze: #a8622f; --bronze-tint: #f3e6da;
    --silver: #5f6a76; --silver-tint: #e9ebed; --gold: #a37f27; --gold-tint: #f5edd8;
    --mono: ui-monospace, "SF Mono", "Cascadia Code", "Roboto Mono", Menlo, Consolas, monospace;
    --sans: -apple-system, BlinkMacSystemFont, "Segoe UI", Helvetica, Arial, sans-serif;
  }}
  @media (prefers-color-scheme: dark) {{
    :root {{
      --bg: #10141a; --surface: #171c24; --ink: #e9e6df; --ink-dim: #9aa0aa;
      --border: #262d38; --accent: #6fa9be; --bronze: #c97d44; --bronze-tint: #2e2119;
      --silver: #97a2ae; --silver-tint: #1f242c; --gold: #d1ab52; --gold-tint: #2c2617;
    }}
  }}
  * {{ box-sizing: border-box; }}
  body {{ background: var(--bg); color: var(--ink); font-family: var(--sans); margin: 0; padding: 48px 24px 80px; }}
  .page {{ max-width: 1100px; margin: 0 auto; }}
  .eyebrow {{ font-family: var(--mono); font-size: 12px; letter-spacing: 0.14em; text-transform: uppercase; color: var(--accent); margin: 0 0 10px; }}
  h1 {{ font-family: var(--mono); font-size: clamp(26px, 4vw, 36px); font-weight: 600; letter-spacing: -0.01em; margin: 0 0 12px; text-wrap: balance; }}
  .lede {{ color: var(--ink-dim); font-size: 15px; line-height: 1.6; max-width: 62ch; margin: 0 0 32px; }}
  .lede code {{ font-family: var(--mono); font-size: 0.92em; background: var(--surface); border: 1px solid var(--border); border-radius: 4px; padding: 1px 5px; }}
  .stats {{ display: grid; grid-template-columns: repeat(4, 1fr); gap: 1px; background: var(--border); border: 1px solid var(--border); border-radius: 10px; overflow: hidden; margin-bottom: 40px; }}
  .stat {{ background: var(--surface); padding: 16px 18px; }}
  .stat .n {{ font-family: var(--mono); font-size: 24px; font-weight: 600; font-variant-numeric: tabular-nums; }}
  .stat .l {{ font-size: 12px; color: var(--ink-dim); margin-top: 2px; }}
  .stat.bronze .n {{ color: var(--bronze); }} .stat.silver .n {{ color: var(--silver); }} .stat.gold .n {{ color: var(--gold); }}
  section {{ margin-bottom: 40px; }}
  h2 {{ font-family: var(--mono); font-size: 13px; letter-spacing: 0.08em; text-transform: uppercase; color: var(--ink-dim); font-weight: 600; margin: 0 0 16px; display: flex; align-items: center; gap: 10px; }}
  h2::after {{ content: ""; flex: 1; height: 1px; background: var(--border); }}
  .graph-wrap {{ background: var(--surface); border: 1px solid var(--border); border-radius: 12px; padding: 20px; overflow-x: auto; }}
  .legend {{ display: flex; gap: 20px; margin-top: 16px; padding-top: 16px; border-top: 1px solid var(--border); font-family: var(--mono); font-size: 12px; color: var(--ink-dim); }}
  .legend .chip {{ display: inline-flex; align-items: center; gap: 6px; }}
  .legend .dot {{ width: 9px; height: 9px; border-radius: 2px; display: inline-block; }}
  table {{ width: 100%; border-collapse: collapse; background: var(--surface); border: 1px solid var(--border); border-radius: 12px; overflow: hidden; font-size: 13.5px; }}
  .table-wrap {{ overflow-x: auto; border-radius: 12px; }}
  thead th {{ text-align: left; font-family: var(--mono); font-size: 11px; letter-spacing: 0.06em; text-transform: uppercase; color: var(--ink-dim); font-weight: 600; padding: 12px 16px; border-bottom: 1px solid var(--border); white-space: nowrap; }}
  tbody td {{ padding: 11px 16px; border-bottom: 1px solid var(--border); vertical-align: top; }}
  tbody tr:last-child td {{ border-bottom: none; }}
  td.name {{ font-family: var(--mono); font-weight: 500; }}
  td.rows {{ font-family: var(--mono); font-variant-numeric: tabular-nums; text-align: right; white-space: nowrap; }}
  td.sources {{ color: var(--ink-dim); font-size: 12.5px; }}
  td.sources .src {{ font-family: var(--mono); background: var(--bg); border: 1px solid var(--border); border-radius: 4px; padding: 1px 5px; margin: 1px 3px 1px 0; display: inline-block; }}
  .pill {{ font-family: var(--mono); font-size: 10.5px; font-weight: 600; letter-spacing: 0.04em; text-transform: uppercase; padding: 3px 8px; border-radius: 20px; display: inline-block; }}
  .pill.bronze {{ background: var(--bronze-tint); color: var(--bronze); }}
  .pill.silver {{ background: var(--silver-tint); color: var(--silver); }}
  .pill.gold {{ background: var(--gold-tint); color: var(--gold); }}
  .note {{ font-size: 13px; color: var(--ink-dim); line-height: 1.6; max-width: 68ch; }}
  .note code {{ font-family: var(--mono); font-size: 0.94em; background: var(--surface); border: 1px solid var(--border); border-radius: 4px; padding: 1px 5px; }}
</style></head>
<body>
<div class="page">
  <p class="eyebrow">Lineage catalog — read-only</p>
  <h1>{project_name}</h1>
  <p class="lede">Every table below was written by <code>write_layer()</code>, which pairs each
  <code>.parquet</code> file with a <code>.json</code> manifest declaring what it was built from.
  This page is that manifest data, read back and laid out.</p>
  <div class="stats">
    <div class="stat bronze"><div class="n">{counts.get('bronze', 0)}</div><div class="l">bronze tables</div></div>
    <div class="stat silver"><div class="n">{counts.get('silver', 0)}</div><div class="l">silver tables</div></div>
    <div class="stat gold"><div class="n">{counts.get('gold', 0)}</div><div class="l">gold tables</div></div>
    <div class="stat"><div class="n">{total}</div><div class="l">total, one pipeline</div></div>
  </div>
  <section>
    <h2>Dependency graph</h2>
    <div class="graph-wrap">
      <pre class="mermaid">{graph}</pre>
      <div class="legend">
        <span class="chip"><span class="dot" style="background:#a8622f"></span>bronze</span>
        <span class="chip"><span class="dot" style="background:#5f6a76"></span>silver</span>
        <span class="chip"><span class="dot" style="background:#a37f27"></span>gold</span>
      </div>
    </div>
  </section>
  <section>
    <h2>Every table</h2>
    <div class="table-wrap">
      <table><thead><tr><th>Layer</th><th>Table</th><th>Rows</th><th>Built from</th></tr></thead>
      <tbody id="table-body"></tbody></table>
    </div>
  </section>
  <p class="note">Regenerate this page any time by re-running your data generator and notebook,
  then this script — nothing here is hand-maintained.</p>
</div>
<script>
  mermaid.initialize({{ startOnLoad: true, theme: 'base' }});
  const data = {data_json};
  const tbody = document.getElementById('table-body');
  for (const m of data) {{
    const tr = document.createElement('tr');
    const sources = m.built_from.length
      ? m.built_from.map(s => `<span class="src">${{s}}</span>`).join('')
      : '<span style="color:var(--ink-dim)">— origin —</span>';
    tr.innerHTML = `<td><span class="pill ${{m.layer}}">${{m.layer}}</span></td>
      <td class="name">${{m.table}}</td>
      <td class="rows">${{m.rows.toLocaleString()}}</td>
      <td class="sources">${{sources}}</td>`;
    tbody.appendChild(tr);
  }}
</script>
</body></html>
"""


def main():
    """CLI: python3 src/lineage.py — regenerates docs/lineage.html from data/ manifests."""
    root = Path.cwd()
    while not (root / "data").exists() and root != root.parent:
        root = root.parent

    manifests = read_lineage(root / "data")
    if not manifests:
        print(f"No lineage manifests found under {root / 'data'} — nothing to render.")
        return

    print_lineage(manifests)

    html = render_html(manifests, root.name)
    out_path = root / "docs" / "lineage.html"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(html)
    print(f"\nwrote {out_path}")
    print(f"open it: open {out_path}")


if __name__ == "__main__":
    main()
