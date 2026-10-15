#!/usr/bin/env python3
"""
Actualiza HIST_CRUCES_DATA en index.html (series diarias del disponible en
USD de soja, maíz y trigo, usadas para los gráficos de evolución de los
cruces de precios: Soja/Maíz, Soja/Trigo, Maíz/Trigo).

Fuente: la misma planilla pública que ya alimenta el disponible (SPOT) de
las pestañas de Futuros -- DATA_CSV_URL en index.html, actualizada a diario
por el equipo. Trae columnas "Soja USD", "Maiz USD", "Trigo USD" por fecha.

A diferencia de otros scripts de este repo, este no solo agrega el punto
del día: sincroniza TODAS las fechas presentes en la planilla (la planilla
solo tiene el historial desde abril-2026 en adelante). Los puntos previos a
esa fecha vienen de un backfill manual cargado una sola vez y se conservan
tal cual -- este script nunca los toca ni los borra.

Corre a diario. Si la planilla no trae filas nuevas, no cambia nada.
"""
import json
import re
import sys

import requests

INDEX_PATH = "index.html"
# Hasta esta fecha la serie es la base historica (CSV pizarra Rosario, depurada).
# El script solo agrega/actualiza fechas POSTERIORES a BASE_END.
BASE_END = "2026-10-14"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
}
DATA_FILE_ID = "11VQysuTx_JT8gw4bf0q89ulbSE12kLZpNfDuPwktBoQ"
DATA_CSV_URL = (
    f"https://docs.google.com/spreadsheets/d/{DATA_FILE_ID}/export?format=csv&gid=0"
)


def to_float_ar(s):
    if s is None:
        return None
    s = s.strip().replace("$", "").strip()
    if not s or s.upper() in ("N/A", "-", ""):
        return None
    # Formato AR: punto de miles, coma decimal -> si hay coma, es decimal.
    if "," in s:
        s = s.replace(".", "").replace(",", ".")
    try:
        return float(s)
    except ValueError:
        return None


def parse_fecha(s, prev_iso=None):
    """La planilla es mayormente D/M/AAAA, pero algunas filas (fechas
    ingresadas sin cero adelante) quedaron guardadas como M/D/AAAA por
    Google Sheets. Como las filas vienen en orden cronologico ascendente,
    se usa la fecha anterior ya parseada para desambiguar: se elige la
    interpretacion que resulte igual o posterior a la anterior."""
    s = s.strip()
    m = re.match(r"^(\d{1,2})/(\d{1,2})/(\d{4})$", s)
    if not m:
        return None
    p1, p2, y = int(m.group(1)), int(m.group(2)), int(m.group(3))

    def mk(d, mo):
        if not (1 <= mo <= 12 and 1 <= d <= 31):
            return None
        try:
            from datetime import date

            date(y, mo, d)
        except ValueError:
            return None
        return f"{y:04d}-{mo:02d}-{d:02d}"

    cand_dmy = mk(p1, p2)  # dia/mes/anio (formato dominante de la planilla)
    cand_mdy = mk(p2, p1)  # mes/dia/anio (formato alternativo, filas sueltas)

    if prev_iso is None:
        return cand_dmy or cand_mdy
    # De las interpretaciones validas que no retroceden respecto de la fecha
    # anterior, elegir la mas cercana (la serie es diaria/habil, no deberia
    # haber saltos grandes de una fila a la siguiente).
    candidatos = [c for c in (cand_dmy, cand_mdy) if c and c >= prev_iso]
    if candidatos:
        return min(candidatos)
    return cand_dmy or cand_mdy


def fetch_csv():
    r = requests.get(DATA_CSV_URL, headers=HEADERS, timeout=25)
    r.raise_for_status()
    return r.text


def parse_spot_csv(text):
    """Devuelve {fecha: {soja, maiz, trigo}} en USD."""
    lines = [l for l in text.splitlines() if l.strip()]
    if len(lines) < 2:
        return {}
    header = next(csv_row(lines[0]))
    hdrs = [h.strip().lower() for h in header]

    def find_idx(*needles):
        for i, h in enumerate(hdrs):
            if all(n in h for n in needles):
                return i
        return -1

    i_fecha = find_idx("fecha")
    i_soja = find_idx("soja", "usd")
    i_maiz = find_idx("maiz", "usd")
    i_trigo = find_idx("trigo", "usd")
    # Columnas en pesos y tipo de cambio: se usan como respaldo cuando las
    # columnas "... USD" de la planilla quedan vacias (paso desde el 30/09/2026).
    i_soja_ars = next((i for i, h in enumerate(hdrs) if h == "soja"), -1)
    i_maiz_ars = next((i for i, h in enumerate(hdrs) if h in ("maiz", "maíz")), -1)
    i_trigo_ars = next((i for i, h in enumerate(hdrs) if h == "trigo"), -1)
    i_tc = find_idx("mayorista")
    if i_fecha < 0 or i_soja < 0:
        raise RuntimeError("No se encontraron las columnas esperadas en la planilla.")

    out = {}
    prev_iso = None
    for line in lines[1:]:
        row = next(csv_row(line))
        if len(row) <= max(i_fecha, i_soja, i_maiz, i_trigo):
            continue
        fecha = parse_fecha(row[i_fecha], prev_iso)
        if not fecha:
            continue
        prev_iso = fecha
        soja = to_float_ar(row[i_soja])
        maiz = to_float_ar(row[i_maiz]) if i_maiz >= 0 else None
        trigo = to_float_ar(row[i_trigo]) if i_trigo >= 0 else None
        # Respaldo: si la planilla no trae el USD, se calcula ARS / USD Mayorista
        # (verificado contra filas con ambos datos: coincide al centavo).
        tc = to_float_ar(row[i_tc]) if 0 <= i_tc < len(row) else None

        def _usd(ars_idx, usd_val):
            if usd_val is not None or tc in (None, 0) or ars_idx < 0 or ars_idx >= len(row):
                return usd_val
            ars = to_float_ar(row[ars_idx])
            return round(ars / tc, 2) if ars else None

        soja = _usd(i_soja_ars, soja)
        maiz = _usd(i_maiz_ars, maiz)
        trigo = _usd(i_trigo_ars, trigo)
        if soja is None:
            continue
        out[fecha] = {"soja": soja, "maiz": maiz, "trigo": trigo}
    return out


def csv_row(line):
    """Generador simple de un solo row CSV (con comillas)."""
    cells = []
    cur = ""
    in_q = False
    for ch in line + ",":
        if ch == '"':
            in_q = not in_q
        elif ch == "," and not in_q:
            cells.append(cur.strip())
            cur = ""
        else:
            cur += ch
    yield cells


def extract_current_block(html_text):
    m = re.search(r"const HIST_CRUCES_DATA = (\{.*?\n\};)", html_text, re.S)
    if not m:
        raise RuntimeError("No se encontro HIST_CRUCES_DATA en index.html")
    return m.group(0), m.group(1)


def js_obj_to_py(js_text):
    t = js_text.rstrip(";")
    t = re.sub(r"(?<=[{,\[])\s*([a-zA-Z_][a-zA-Z0-9_]*)\s*:", r'"\1":', t)
    t = re.sub(
        r":\s*'([^']*)'",
        lambda m: ':"' + m.group(1).replace('"', '\\"') + '"',
        t,
    )
    t = re.sub(r",\s*([}\]])", r"\1", t)
    return json.loads(t)


def py_to_js_obj(obj):
    """Serializa compacto (4 puntos por linea) para no infrar el diff de git
    con reformateos gigantes en cada corrida."""
    lines = ['{']
    keys = list(obj.keys())
    for ki, key in enumerate(keys):
        arr = obj[key]
        lines.append(f'  "{key}": [')
        per_line = 4
        for i in range(0, len(arr), per_line):
            chunk = arr[i : i + per_line]
            row = ",".join(
                '{"fecha":"%s","valor":%s}' % (p["fecha"], json.dumps(p["valor"]))
                for p in chunk
            )
            suffix = "," if i + per_line < len(arr) else ""
            lines.append("    " + row + suffix)
        suffix = "," if ki < len(keys) - 1 else ""
        lines.append(f"  ]{suffix}")
    lines.append("}")
    return "\n".join(lines)


def main():
    with open(INDEX_PATH, "r", encoding="utf-8") as f:
        html_text = f.read()

    full_match, block_text = extract_current_block(html_text)
    try:
        data = js_obj_to_py(block_text)
    except Exception as e:
        print(f"[ERROR] No se pudo parsear HIST_CRUCES_DATA existente: {e}", file=sys.stderr)
        sys.exit(1)

    try:
        csv_text = fetch_csv()
        sheet = parse_spot_csv(csv_text)
    except Exception as e:
        print(f"[ERROR] No se pudo leer la planilla de disponible: {e}", file=sys.stderr)
        sys.exit(1)

    if not sheet:
        print("La planilla no trajo filas validas, no se hace nada.")
        return False

    changed = False
    for cult, key in (("soja", "soja"), ("maiz", "maiz"), ("trigo", "trigo")):
        existing = data.get(cult, [])
        by_fecha = {p["fecha"]: p["valor"] for p in existing}
        for fecha, vals in sorted(sheet.items()):
            if fecha <= BASE_END:
                continue
            v = vals.get(key)
            if v is None:
                continue
            # Guarda anti-errores de carga: si el valor se desvia >10% del dia
            # anterior y es ese mismo valor x10 / x100 / /10 / /100 (decimal
            # corrido), se descarta y se usa el dato del dia anterior.
            previos = [f for f in by_fecha if f < fecha]
            if previos:
                pv = by_fecha[max(previos)]
                if pv and abs(v / pv - 1) > 0.10 and any(
                    abs(v * f / pv - 1) <= 0.10 for f in (10, 100, 0.1, 0.01)
                ):
                    print(f"[AVISO] {cult} {fecha}: {v} parece error de carga, se usa {pv}")
                    v = pv
            if by_fecha.get(fecha) != v:
                by_fecha[fecha] = v
                changed = True
        nuevo = [{"fecha": f, "valor": by_fecha[f]} for f in sorted(by_fecha.keys())]
        data[cult] = nuevo

    if not changed:
        print("Sin cambios en HIST_CRUCES_DATA.")
        return False

    new_block_text = "const HIST_CRUCES_DATA = " + py_to_js_obj(data) + ";"
    html_text = html_text.replace(full_match, new_block_text, 1)

    with open(INDEX_PATH, "w", encoding="utf-8") as f:
        f.write(html_text)

    print("index.html actualizado: HIST_CRUCES_DATA")
    return True


if __name__ == "__main__":
    main()
    raise SystemExit(0)
