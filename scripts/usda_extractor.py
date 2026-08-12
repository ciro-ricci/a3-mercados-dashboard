#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Extractor de datos USDA para la pestaña "USDA" de estrategiasalgrano.com

Genera el bloque USDA_DATA (JS) con:
  - Series históricas de 20 campañas (producción, stocks finales, stocks/uso)
    para Soja, Maíz y Trigo — Mundo, EEUU, Argentina y Brasil.  [Fuente: PSD Online]
  - Condición de cultivos actual + histórico de % bueno/excelente.  [Fuente: NASS QuickStats]
  - Crop Progress semanal (avance por estado).                     [Fuente: NASS release files]

Uso:
    python3 usda_extractor.py --out usda_data.json [--nass-key CLAVE]

IMPORTANTE: la NASS API key NO debe quedar embebida en el HTML publicado
(el repo es público). Este script la usa solo en tiempo de generación.
"""

import argparse
import csv
import io
import json
import os
import re
import sys
import urllib.request
import zipfile
from collections import defaultdict

PSD_URLS = {
    "grains": "https://apps.fas.usda.gov/psdonline/downloads/psd_grains_pulses_csv.zip",
    "oilseeds": "https://apps.fas.usda.gov/psdonline/downloads/psd_oilseeds_csv.zip",
}

# commodity PSD -> (clave interna, archivo)
COMMODITIES = {
    "soja":  ("Oilseed, Soybean", "oilseeds"),
    "maiz":  ("Corn",             "grains"),
    "trigo": ("Wheat",            "grains"),
}

# Países de interés (además del agregado mundial)
COUNTRIES = {
    "eeuu":      "United States",
    "argentina": "Argentina",
    "brasil":    "Brazil",
}

# Todo en unidades métricas: PSD entrega producción/stocks en 1000 MT,
# área en 1000 HA y rinde en MT/HA — no hay que convertir desde bushels/acres.
ATTRS = ("Production", "Ending Stocks", "Domestic Consumption", "Exports",
         "Area Harvested", "Yield")

N_CAMPAIGNS = 20


def log(msg):
    print(f"[usda] {msg}", file=sys.stderr)


def fetch(url, timeout=180):
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def load_psd(cache_dir):
    """Descarga (o reutiliza) los CSV de PSD Online y los deja en cache_dir."""
    os.makedirs(cache_dir, exist_ok=True)
    paths = {}
    for key, url in PSD_URLS.items():
        target = os.path.join(cache_dir, f"psd_{key}.csv")
        if os.path.exists(target) and os.path.getsize(target) > 1_000_000:
            log(f"PSD {key}: usando cache")
            paths[key] = target
            continue
        log(f"PSD {key}: descargando…")
        raw = fetch(url)
        with zipfile.ZipFile(io.BytesIO(raw)) as z:
            name = [n for n in z.namelist() if n.endswith(".csv")][0]
            with z.open(name) as src, open(target, "wb") as dst:
                dst.write(src.read())
        paths[key] = target
    return paths


def build_psd_series(paths):
    """
    Devuelve: {cultivo: {ambito: {year: {attr: valor}}}}
    ambito ∈ mundo | eeuu | argentina | brasil
    Nota: PSD no trae fila "World"; el total mundial se obtiene sumando países.
    Los países de la UE solo tienen datos hasta 1998 (luego se usa el agregado
    "European Union"), por lo que la suma no produce doble conteo.
    """
    want_countries = {v: k for k, v in COUNTRIES.items()}
    out = {}

    for crop, (psd_name, filekey) in COMMODITIES.items():
        world = defaultdict(lambda: defaultdict(float))
        byc = {k: defaultdict(dict) for k in COUNTRIES}

        with open(paths[filekey], newline="", encoding="utf-8", errors="replace") as f:
            for row in csv.DictReader(f):
                if row["Commodity_Description"] != psd_name:
                    continue
                attr = row["Attribute_Description"]
                if attr not in ATTRS:
                    continue
                try:
                    year = int(row["Market_Year"])
                    val = float(row["Value"])
                except (ValueError, TypeError):
                    continue

                world[year][attr] += val

                cname = row["Country_Name"]
                if cname in want_countries:
                    byc[want_countries[cname]][year][attr] = val

        scope = {"mundo": {y: dict(a) for y, a in world.items()}}
        for k in COUNTRIES:
            scope[k] = {y: dict(a) for y, a in byc[k].items()}
        out[crop] = scope
        log(f"PSD {crop}: {len(world)} campañas")

    return out


def shape_series(psd):
    """Recorta a las últimas N campañas y calcula stocks/uso."""
    result = {}
    for crop, scopes in psd.items():
        years = sorted(scopes["mundo"].keys())[-N_CAMPAIGNS:]
        crop_out = {"campanias": [f"{y}/{str(y + 1)[2:]}" for y in years], "years": years}
        for scope, data in scopes.items():
            prod, stocks, ratio, exports, area, rinde = [], [], [], [], [], []
            for y in years:
                d = data.get(y, {})
                p = d.get("Production")
                es = d.get("Ending Stocks")
                dc = d.get("Domestic Consumption")
                ex = d.get("Exports")
                ar = d.get("Area Harvested")
                yi = d.get("Yield")
                # El rinde no se puede sumar entre países: para el agregado
                # mundial se recalcula como producción / área cosechada.
                if scope == "mundo":
                    yi = (p / ar) if (p and ar) else None
                # 1000 MT -> millones de toneladas ; 1000 HA -> millones de ha
                prod.append(round(p / 1000, 2) if p is not None else None)
                stocks.append(round(es / 1000, 2) if es is not None else None)
                exports.append(round(ex / 1000, 2) if ex is not None else None)
                area.append(round(ar / 1000, 2) if ar is not None else None)
                rinde.append(round(yi, 2) if yi is not None else None)  # t/ha
                ratio.append(round(es / dc * 100, 1) if es and dc else None)
            crop_out[scope] = {
                "produccion": prod,       # Mt
                "stocks": stocks,         # Mt
                "exportaciones": exports, # Mt
                "area": area,             # M ha
                "rinde": rinde,           # t/ha
                "stocks_uso": ratio,      # %
            }
        result[crop] = crop_out
    return result


# ── NASS QuickStats ────────────────────────────────────────────────────
NASS_CROPS = {"maiz": "CORN", "soja": "SOYBEANS", "trigo": "WHEAT"}


def nass_get(key, **params):
    params["key"] = key
    params["format"] = "JSON"
    qs = urllib.parse.urlencode(params)
    url = f"https://quickstats.nass.usda.gov/api/api_GET/?{qs}"
    try:
        data = json.loads(fetch(url, timeout=90))
    except Exception as e:
        log(f"NASS error: {e}")
        return []
    if "error" in data:
        log(f"NASS error: {data['error']}")
        return []
    return data.get("data", [])


def build_condition(key, current_year):
    """
    Condición de cultivos (% bueno + excelente), alineada por semana del año:
      - campaña en curso
      - las dos campañas anteriores
      - promedio de las últimas 20 campañas
    Permite comparar la evolución intra-campaña, que es lo que importa
    para leer si el cultivo mejora o se deteriora respecto de la historia.
    """
    import datetime as _dt

    out = {}
    for crop, nass_name in NASS_CROPS.items():
        recs = []
        for unit in ("PCT GOOD", "PCT EXCELLENT"):
            recs += nass_get(
                key,
                source_desc="SURVEY",
                commodity_desc=nass_name,
                statisticcat_desc="CONDITION",
                agg_level_desc="NATIONAL",
                unit_desc=unit,
            )
        if not recs:
            out[crop] = None
            continue

        # (año, semana_iso) -> G+E   /  guardamos también la fecha para rotular
        ge = defaultdict(int)
        for r in recs:
            try:
                y = int(r["year"])
                we = _dt.date.fromisoformat(r["week_ending"])
                ge[(y, we.isocalendar()[1])] += int(float(r["Value"]))
            except (ValueError, TypeError, KeyError):
                continue

        years_avail = sorted({y for y, _ in ge})
        if not years_avail:
            out[crop] = None
            continue

        prev1 = current_year - 1
        prev2 = current_year - 2
        hist_years = [y for y in years_avail if y >= current_year - 20 and y < current_year]

        # Eje de semanas: las que tiene la campaña en curso, extendido con las
        # semanas típicas de la campaña previa (para ver hacia dónde va)
        weeks_cur = sorted({w for (y, w) in ge if y == current_year})
        weeks_ref = sorted({w for (y, w) in ge if y == prev1})
        weeks = sorted(set(weeks_cur) | set(weeks_ref)) or weeks_cur
        if not weeks:
            out[crop] = None
            continue

        def serie(year):
            return [ge.get((year, w)) for w in weeks]

        promedio = []
        for w in weeks:
            vals = [ge[(y, w)] for y in hist_years if (y, w) in ge]
            promedio.append(round(sum(vals) / len(vals), 1) if vals else None)

        out[crop] = {
            "semanas": weeks,
            "actual": {"anio": current_year, "ge": serie(current_year)},
            "previo1": {"anio": prev1, "ge": serie(prev1)},
            "previo2": {"anio": prev2, "ge": serie(prev2)},
            "promedio20": {"n_anios": len(hist_years), "ge": promedio},
        }
        log(f"NASS condición {crop}: {len(weeks)} semanas, promedio sobre {len(hist_years)} campañas")
    return out


# ── ENSO / El Niño (NOAA CPC — Oceanic Niño Index) ─────────────────────
ONI_URL = "https://www.cpc.ncep.noaa.gov/data/indices/oni.ascii.txt"


def build_enso(n_trimestres=48):
    """
    Serie del ONI (Oceanic Niño Index), el índice estándar para clasificar
    El Niño / La Niña. Umbrales: >= +0,5 El Niño ; <= -0,5 La Niña.
    """
    try:
        txt = fetch(ONI_URL, timeout=60).decode("utf-8", errors="replace")
    except Exception as e:
        log(f"ONI error: {e}")
        return None

    filas = []
    for line in txt.splitlines()[1:]:
        p = line.split()
        if len(p) != 4:
            continue
        try:
            filas.append({"seas": p[0], "anio": int(p[1]), "anom": float(p[3])})
        except ValueError:
            continue

    if not filas:
        return None

    recorte = filas[-n_trimestres:]
    ultimo = filas[-1]
    a = ultimo["anom"]
    if a >= 1.5:
        fase, desc = "El Niño", "fuerte"
    elif a >= 1.0:
        fase, desc = "El Niño", "moderado"
    elif a >= 0.5:
        fase, desc = "El Niño", "débil"
    elif a <= -1.5:
        fase, desc = "La Niña", "fuerte"
    elif a <= -1.0:
        fase, desc = "La Niña", "moderada"
    elif a <= -0.5:
        fase, desc = "La Niña", "débil"
    else:
        fase, desc = "Neutral", ""

    # tendencia sobre los últimos 3 trimestres móviles
    delta = filas[-1]["anom"] - filas[-4]["anom"] if len(filas) >= 4 else 0.0

    log(f"ONI: {ultimo['seas']} {ultimo['anio']} = {a:+.2f} ({fase} {desc})")
    return {
        "labels": [f"{f['seas']} {str(f['anio'])[2:]}" for f in recorte],
        "anom": [f["anom"] for f in recorte],
        "ultimo": {"periodo": f"{ultimo['seas']} {ultimo['anio']}", "valor": a,
                   "fase": fase, "intensidad": desc, "delta_3t": round(delta, 2)},
        "url": ONI_URL,
    }


# ── Mapas de sequía (hotlink, se actualizan solos cada jueves) ─────────
def build_drought():
    """
    URLs estables del U.S. Drought Monitor. Las imágenes 'current' se
    actualizan solas cada jueves, así que no hay que subir nada al repo.
    """
    fecha = None
    try:
        html = fetch("https://droughtmonitor.unl.edu/CurrentMap.aspx", timeout=45).decode(
            "utf-8", errors="replace")
        m = re.search(r"Data valid:\s*([A-Za-z]+ \d+, \d{4})", html)
        if m:
            fecha = m.group(1)
    except Exception as e:
        log(f"drought error: {e}")

    return {
        "fecha": fecha,
        "url": "https://droughtmonitor.unl.edu/CurrentMap.aspx",
        "mapas": [
            {"t": "Sequía en Estados Unidos",
             "d": "Categorías D0 (anormalmente seco) a D4 (sequía excepcional). Se actualiza cada jueves.",
             "img": "https://droughtmonitor.unl.edu/data/png/current/current_usdm.png"},
            {"t": "Cambio respecto de la semana previa",
             "d": "Zonas donde la sequía se agravó (rojo) o mejoró (verde) en la última semana.",
             "img": "https://droughtmonitor.unl.edu/data/png/current/current_conus_trd.png"},
        ],
    }


# ── Crop Progress (archivo semanal de texto) ───────────────────────────
def find_latest_progress(year):
    """Busca el archivo prog{semana}{yy}.txt más reciente disponible."""
    yy = str(year)[2:]
    for week in range(52, 10, -1):
        url = f"https://release.nass.usda.gov/reports/prog{week:02d}{yy}.txt"
        try:
            req = urllib.request.Request(url, method="HEAD",
                                         headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=20) as r:
                if r.status == 200:
                    log(f"Crop Progress: semana {week}")
                    return url, week
        except Exception:
            continue
    return None, None


def _row_numbers(chunk, label_pattern):
    """Extrae los números de una fila cuyo rótulo matchea label_pattern.
    '-' representa cero en estos reportes."""
    m = re.search(label_pattern + r"\s*\.*:\s*(.+)$", chunk, re.MULTILINE)
    if not m:
        return None
    toks = re.findall(r"-|\d+", m.group(1))
    return [0 if t == "-" else int(t) for t in toks]


def parse_progress(text):
    """
    Extrae las filas nacionales del reporte Crop Progress.

    Dos formatos de tabla:
      * Avance  (ej. "Corn Silking")   -> 4 columnas:
            año previo | semana previa | actual | promedio 5 años
      * Condición (ej. "Corn Condition") -> 5 columnas:
            very poor | poor | fair | good | excellent
        y filas extra "Previous week" / "Previous year".

    La fila nacional se rotula "18 States", "15 States", etc. (no "United States").
    """
    tables = {}
    chunks = re.split(r"\n(?=[A-Z][A-Za-z ,/&']+ - Selected States)", text)

    for ch in chunks:
        m = re.match(r"([A-Z][A-Za-z ,/&']+?) - Selected States(:.*)?$",
                     ch.split("\n")[0])
        if not m:
            continue
        title = m.group(1).strip()

        nums = _row_numbers(ch, r"^\s*\d+\s+States")
        if not nums:
            continue

        is_condition = "Condition" in title

        if is_condition and len(nums) >= 5:
            vp, poor, fair, good, exc = nums[:5]
            entry = {
                "tipo": "condicion",
                "muy_pobre": vp, "pobre": poor, "regular": fair,
                "bueno": good, "excelente": exc,
                "bueno_excelente": good + exc,
            }
            prev_w = _row_numbers(ch, r"^\s*Previous week")
            prev_y = _row_numbers(ch, r"^\s*Previous year")
            if prev_w and len(prev_w) >= 5:
                entry["ge_semana_previa"] = prev_w[3] + prev_w[4]
            if prev_y and len(prev_y) >= 5:
                entry["ge_anio_previo"] = prev_y[3] + prev_y[4]
            tables[title] = entry

        elif not is_condition and len(nums) >= 4:
            tables[title] = {
                "tipo": "avance",
                "anio_previo": nums[0],
                "semana_previa": nums[1],
                "actual": nums[2],
                "promedio_5a": nums[3],
            }

    return tables


def build_progress(year):
    url, week = find_latest_progress(year)
    if not url:
        return None
    txt = fetch(url).decode("utf-8", errors="replace")
    released = re.search(r"Released\s+(\w+ \d+, \d{4})", txt)
    return {
        "semana": week,
        "publicado": released.group(1) if released else None,
        "url": url,
        "tablas": parse_progress(txt),
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="usda_data.json")
    ap.add_argument("--nass-key", default=os.environ.get("NASS_API_KEY", ""))
    ap.add_argument("--cache", default="/tmp/psd")
    ap.add_argument("--year", type=int, default=2026)
    args = ap.parse_args()

    paths = load_psd(args.cache)
    series = shape_series(build_psd_series(paths))

    condition = build_condition(args.nass_key, args.year) if args.nass_key else {}
    progress = build_progress(args.year)
    enso = build_enso()
    drought = build_drought()

    out = {"series": series, "condicion": condition, "crop_progress": progress,
           "enso": enso, "sequia": drought}
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, separators=(",", ":"))
    log(f"escrito {args.out} ({os.path.getsize(args.out)/1024:.0f} KB)")


if __name__ == "__main__":
    import urllib.parse  # noqa: E402  (usado en nass_get)
    main()
