#!/usr/bin/env python3
"""
Actualiza COT_DATA en index.html con el posicionamiento neto de los fondos
especulativos (Managed Money) en los futuros de soja, maiz y trigo de Chicago,
segun el reporte semanal "Commitments of Traders" (disaggregated) de la CFTC.

Fuente: API publica Socrata de la CFTC (sin auth / sin API key).
  https://publicreporting.cftc.gov/resource/72hh-3qpy.json
Codigos de contrato (cftc_contract_market_code):
  soja  -> 005602  (SOYBEANS - CHICAGO BOARD OF TRADE)
  maiz  -> 002602  (CORN - CHICAGO BOARD OF TRADE)
  trigo -> 001602  (WHEAT-SRW - CHICAGO BOARD OF TRADE)

La CFTC publica el reporte todos los viernes a la tarde (hora US) con datos
a los martes de esa misma semana. El workflow de GitHub Actions que corre
este script esta programado para el sabado a la madrugada (hora ARG), para
darle margen a que la CFTC ya haya publicado.

Uso:
    python scripts/update_cot.py [--index PATH] [--backfill-years N]

Por defecto busca index.html en la raiz del repo (relativo a este script)
y, si COT_DATA no existe todavia en el archivo, hace un backfill de 3 anios
de historia antes de insertarlo. En corridas posteriores solo trae las
semanas nuevas que todavia no esten guardadas.
"""

import argparse
import json
import re
import sys
import urllib.parse
import urllib.request
from pathlib import Path

CFTC_BASE = "https://publicreporting.cftc.gov/resource/72hh-3qpy.json"

CODES = {
    "soja": {"code": "005602", "label": "Soja"},
    "maiz": {"code": "002602", "label": "Ma\u00edz"},
    "trigo": {"code": "001602", "label": "Trigo"},
}

FIELDS = [
    "report_date_as_yyyy_mm_dd",
    "m_money_positions_long_all",
    "m_money_positions_short_all",
    "open_interest_all",
]


def _fetch_json(url, timeout=30):
    req = urllib.request.Request(url, headers={"User-Agent": "estrategiasalgrano-bot/1.0"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def fetch_series(code, since_date=None, limit=5000):
    """Trae filas del reporte disaggregated para un codigo de contrato.
    Si since_date se pasa (YYYY-MM-DD), filtra desde esa fecha inclusive.
    """
    params = {
        "cftc_contract_market_code": code,
        "$select": ",".join(FIELDS),
        "$order": "report_date_as_yyyy_mm_dd ASC",
        "$limit": str(limit),
    }
    if since_date:
        params["$where"] = f"report_date_as_yyyy_mm_dd >= '{since_date}T00:00:00.000'"
    url = CFTC_BASE + "?" + urllib.parse.urlencode(params, quote_via=urllib.parse.quote)
    rows = _fetch_json(url)
    out = []
    seen = set()
    for r in rows:
        fecha = r["report_date_as_yyyy_mm_dd"][:10]
        if fecha in seen:
            continue
        seen.add(fecha)
        long_ = int(r["m_money_positions_long_all"])
        short_ = int(r["m_money_positions_short_all"])
        oi = int(r["open_interest_all"])
        out.append({
            "fecha": fecha,
            "long": long_,
            "short": short_,
            "net": long_ - short_,
            "oi": oi,
        })
    out.sort(key=lambda p: p["fecha"])
    return out


def find_cot_data_block(html):
    """Devuelve (start, end) de la sentencia 'const COT_DATA = {...};' si existe."""
    m = re.search(r"const COT_DATA\s*=\s*", html)
    if not m:
        return None
    j = m.end()
    while html[j] in " \n\t":
        j += 1
    if html[j] != "{":
        return None
    depth = 0
    in_str = False
    esc = False
    k = j
    while True:
        c = html[k]
        if in_str:
            if esc:
                esc = False
            elif c == "\\":
                esc = True
            elif c == '"':
                in_str = False
        else:
            if c == '"':
                in_str = True
            elif c == "{":
                depth += 1
            elif c == "}":
                depth -= 1
                if depth == 0:
                    break
        k += 1
    end = k + 1
    if html[end] == ";":
        end += 1
    return (m.start(), end)


def merge_series(existing, fresh):
    by_fecha = {p["fecha"]: p for p in existing}
    for p in fresh:
        by_fecha[p["fecha"]] = p
    merged = list(by_fecha.values())
    merged.sort(key=lambda p: p["fecha"])
    return merged


def build_cot_data(index_html, backfill_years):
    block = find_cot_data_block(index_html)
    existing = {}
    if block:
        start, end = block
        eq = index_html.find("=", start)
        blob = index_html[eq + 1:end]
        blob = blob.rstrip()
        if blob.endswith(";"):
            blob = blob[:-1]
        existing = json.loads(blob)
        print("COT_DATA existente encontrado, trayendo solo semanas nuevas...")
    else:
        print(f"COT_DATA no existe todavia. Haciendo backfill de {backfill_years} a\u00f1os...")

    data = {}
    for key, meta in CODES.items():
        prev_series = existing.get(key, {}).get("series", [])
        if prev_series:
            since = None
            fresh = fetch_series(meta["code"], limit=20)
        else:
            import datetime
            since = (datetime.date.today() - datetime.timedelta(days=365 * backfill_years)).isoformat()
            fresh = fetch_series(meta["code"], since_date=since)
        merged = merge_series(prev_series, fresh)
        data[key] = {"label": meta["label"], "code": meta["code"], "series": merged}
        print(f"  {key}: {len(merged)} semanas guardadas (ultima: {merged[-1]['fecha'] if merged else 'n/a'})")

    return data, block


def inject(index_html, data, block):
    new_stmt = "const COT_DATA = " + json.dumps(data, ensure_ascii=False, separators=(",", ":")) + ";"
    if block:
        start, end = block
        return index_html[:start] + new_stmt + index_html[end:]
    anchor = "const IP_NAME_MAP"
    idx = index_html.find(anchor)
    if idx == -1:
        raise RuntimeError(
            "No encontre el ancla 'const IP_NAME_MAP' en index.html para insertar COT_DATA. "
            "Insertalo a mano una vez cerca del bloque de Insumo-Producto."
        )
    comment = "\n// \u2500\u2500 Posici\u00f3n fondos (CFTC Commitment of Traders) \u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\n"
    return index_html[:idx] + new_stmt + comment + index_html[idx:]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--index", default=str(Path(__file__).resolve().parent.parent / "index.html"))
    ap.add_argument("--backfill-years", type=int, default=3)
    args = ap.parse_args()

    index_path = Path(args.index)
    html = index_path.read_text(encoding="utf-8")

    data, block = build_cot_data(html, args.backfill_years)
    new_html = inject(html, data, block)

    if new_html == html:
        print("Sin cambios.")
        return

    index_path.write_text(new_html, encoding="utf-8")
    print(f"index.html actualizado ({index_path}).")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(1)
