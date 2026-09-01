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
import time
import urllib.request
import zipfile
from collections import defaultdict

PSD_URLS = {
    "grains": "https://apps.fas.usda.gov/psdonline/downloads/psd_grains_pulses_csv.zip",
    "oilseeds": "https://apps.fas.usda.gov/psdonline/downloads/psd_oilseeds_csv.zip",
}

# commodity PSD -> (clave interna, archivo)
COMMODITIES = {
    "soja":    ("Oilseed, Soybean",       "oilseeds"),
    "maiz":    ("Corn",                   "grains"),
    "trigo":   ("Wheat",                  "grains"),
    "girasol": ("Oilseed, Sunflowerseed", "oilseeds"),
}

# Países de interés (además del agregado mundial)
COUNTRIES = {
    "eeuu":      "United States",
    "argentina": "Argentina",
    "brasil":    "Brazil",
    "rusia":     "Russia",
    "ucrania":   "Ukraine",
    "ue":        "European Union",
}

# Qué ámbitos guarda cada cultivo. No tiene sentido cargar Brasil en girasol
# ni Rusia en soja: infla el bloque USDA_DATA sin aportar nada al dashboard.
SCOPES_POR_CULTIVO = {
    "soja":    {"mundo", "eeuu", "argentina", "brasil"},
    "maiz":    {"mundo", "eeuu", "argentina", "brasil"},
    "trigo":   {"mundo", "eeuu", "argentina", "brasil"},
    "girasol": {"mundo", "argentina", "rusia", "ucrania", "ue"},
}

# Todo en unidades métricas: PSD entrega producción/stocks en 1000 MT,
# área en 1000 HA y rinde en MT/HA — no hay que convertir desde bushels/acres.
ATTRS = ("Production", "Ending Stocks", "Domestic Consumption", "Exports",
         "Area Harvested", "Yield")

N_CAMPAIGNS = 20


def log(msg):
    print(f"[usda] {msg}", file=sys.stderr)


def fetch(url, timeout=120, intentos=3):
    """
    Descarga con reintentos. Los servidores del USDA (sobre todo
    apps.fas.usda.gov) cortan la conexion cada tanto sin motivo; un solo
    timeout no deberia tumbar toda la actualizacion.
    """
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    ultimo = None
    for i in range(1, intentos + 1):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return r.read()
        except Exception as e:                      # noqa: BLE001
            ultimo = e
            if i < intentos:
                espera = 15 * i
                log(f"fallo {i}/{intentos} en {url} ({e}); reintento en {espera}s")
                time.sleep(espera)
    raise ultimo


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
        permitidos = SCOPES_POR_CULTIVO.get(crop, set(COUNTRIES) | {"mundo"})
        scope = {k: v for k, v in scope.items() if k in permitidos}
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
# El trigo se pide por clase. Sumar "WHEAT" sin filtrar mezcla el de invierno
# con el de primavera en las semanas 22-27, cuando NASS informa los dos, y da
# porcentajes imposibles (73-85% de bueno+excelente).
NASS_CROPS = {
    "maiz":            {"commodity_desc": "CORN"},
    "soja":            {"commodity_desc": "SOYBEANS"},
    "trigo":           {"commodity_desc": "WHEAT", "class_desc": "WINTER"},
    "trigo_primavera": {"commodity_desc": "WHEAT", "class_desc": "SPRING, (EXCL DURUM)"},
}

# Título de la tabla del Crop Progress -> cultivo interno, para poder calcular
# el promedio de 5 años de las tablas de condición (NASS no lo publica).
CP_CONDICION = {
    "Corn Condition":         "maiz",
    "Soybean Condition":      "soja",
    "Winter Wheat Condition": "trigo",
    "Spring Wheat Condition": "trigo_primavera",
}

# Serie cruda {(año, semana ISO): % bueno+excelente} por cultivo. La llena
# build_condition y la reusa el promedio de 5 años, para no repetir llamadas.
_GE_CACHE = {}


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
    for crop, filtro in NASS_CROPS.items():
        recs = []
        for unit in ("PCT GOOD", "PCT EXCELLENT"):
            recs += nass_get(
                key,
                source_desc="SURVEY",
                statisticcat_desc="CONDITION",
                agg_level_desc="NATIONAL",
                unit_desc=unit,
                **filtro,
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

        _GE_CACHE[crop] = dict(ge)
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


# ── ENSO / El Niño (NOAA CPC — Oceanic Niño Index + RONI) ───────────────
ONI_URL = "https://www.cpc.ncep.noaa.gov/data/indices/oni.ascii.txt"
# RONI (Relative Oceanic Niño Index): reemplazo oficial del ONI adoptado por
# NOAA/CPC en feb-2026 (resta al Niño 3.4 el calentamiento medio de los
# trópicos 20S-20N, más representativo del efecto atmosférico en clima
# cambiante). CPC todavía no publica un ascii de la serie completa
# actualizado en tiempo real, así que se usa la tabla de Brian McNoldy
# (Univ. of Miami), que replica la metodología oficial de CPC sobre
# ERSSTv6 y se actualiza mensualmente: https://cpc.ncep.noaa.gov/products/analysis_monitoring/enso/roni/
RONI_URL = "https://bmcnoldy.earth.miami.edu/tropics/roni/RONI_NINO34_v6.txt"
_RONI_SEAS = {1: "DJF", 2: "JFM", 3: "FMA", 4: "MAM", 5: "AMJ", 6: "MJJ",
              7: "JJA", 8: "JAS", 9: "ASO", 10: "SON", 11: "OND", 12: "NDJ"}


def build_roni(n_trimestres=48):
    """
    Serie del RONI a partir de la tabla pública de Brian McNoldy (Univ. of
    Miami), que reproduce la metodología oficial de NOAA/CPC (ERSSTv6,
    Niño 3.4 menos el promedio tropical 20S-20N, escalado a la varianza del
    Niño 3.4). Se descarta el último valor si viene marcado como -99.99
    (falta ERSSTv6 del mes en curso, columna PHASE='M').
    """
    try:
        txt = fetch(RONI_URL, timeout=60).decode("utf-8", errors="replace")
    except Exception as e:
        log(f"RONI error: {e}")
        return None

    filas = []
    for line in txt.splitlines():
        p = line.split()
        if len(p) != 8:
            continue
        try:
            anio, mes, roni = int(p[0]), int(p[1]), float(p[6])
        except ValueError:
            continue
        if roni <= -99.0 or mes not in _RONI_SEAS:
            continue
        filas.append({"seas": _RONI_SEAS[mes], "anio": anio, "anom": roni})

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

    delta = filas[-1]["anom"] - filas[-4]["anom"] if len(filas) >= 4 else 0.0

    log(f"RONI: {ultimo['seas']} {ultimo['anio']} = {a:+.2f} ({fase} {desc})")
    return {
        "labels": [f"{f['seas']} {str(f['anio'])[2:]}" for f in recorte],
        "anom": [f["anom"] for f in recorte],
        "ultimo": {"periodo": f"{ultimo['seas']} {ultimo['anio']}", "valor": a,
                   "fase": fase, "intensidad": desc, "delta_3t": round(delta, 2)},
        "url": RONI_URL,
    }


def build_enso(n_trimestres=48):
    """
    Serie del ONI (Oceanic Niño Index) + RONI (Relative ONI, el índice que
    NOAA/CPC usa desde feb-2026 para clasificar El Niño / La Niña).
    Umbrales en ambos: >= +0,5 El Niño ; <= -0,5 La Niña.
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
    out = {
        "labels": [f"{f['seas']} {str(f['anio'])[2:]}" for f in recorte],
        "anom": [f["anom"] for f in recorte],
        "ultimo": {"periodo": f"{ultimo['seas']} {ultimo['anio']}", "valor": a,
                   "fase": fase, "intensidad": desc, "delta_3t": round(delta, 2)},
        "url": ONI_URL,
    }

    roni = build_roni(n_trimestres)
    if roni:
        out["roni"] = {
            "labels": roni["labels"],
            "anom": roni["anom"],
            "periodo": roni["ultimo"]["periodo"],
            "valor": roni["ultimo"]["valor"],
            "fase": roni["ultimo"]["fase"],
            "intensidad": roni["ultimo"]["intensidad"],
            "delta_3t": roni["ultimo"]["delta_3t"],
            "url": RONI_URL,
        }
    else:
        log("RONI: sin datos, se conserva el valor estático de ENSO_MATRIZ.actual.roni")

    return out


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


def _normalizar(texto):
    """
    NASS sirve los reportes con saltos de línea de Windows (CRLF).

    Como el extractor decodifica bytes, el CR queda pegado al final de cada
    línea. Eso rompía el título de las tablas de AVANCE ("Corn Dough - Selected
    States<CR>"), que no matcheaba el patrón y se descartaba. Las de condición
    zafaban de casualidad, porque su título sigue con ": Week Ending ..." y el
    punto de la expresión regular se tragaba el CR. Resultado: se perdían las
    15 tablas de avance y quedaban solo las 7 de condición.
    """
    return texto.replace("\r\n", "\n").replace("\r", "\n")


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
    text = _normalizar(text)
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
    txt = _normalizar(fetch(url).decode("utf-8", errors="replace"))
    released = re.search(r"Released\s+(\w+ \d+, \d{4})", txt)
    # La fecha de cierre de la semana es la que alinea el promedio de 5 anios
    # con la misma semana de campanias anteriores.
    fin = re.search(r"Week Ending\s+(\w+ \d+, \d{4})", txt)
    return {
        "semana": week,
        "publicado": released.group(1) if released else None,
        "semana_termina": fin.group(1) if fin else None,
        "url": url,
        "tablas": parse_progress(txt),
    }


ANIOS_PROM5 = 5


def build_condicion_hist(anio):
    """
    Histórico de condición de las 5 campañas de referencia, semana por semana.

    Esto no cambia nunca: 2021-2025 hoy ya está cerrado. Se guarda en el
    index.html para que el promedio de 5 años sea una consulta a una tabla fija
    y no dependa de que QuickStats conteste todas las semanas. Lo único que
    cambia semana a semana es qué columna de esa tabla se lee.

    Formato: {cultivo: {"semana": {"anio": ge}}}, con las claves en texto
    porque el bloque viaja como JSON dentro del HTML.
    """
    if not _GE_CACHE:
        return None
    ventana = list(range(anio - ANIOS_PROM5, anio))
    hist = {}
    for crop, ge in _GE_CACHE.items():
        por_semana = {}
        for (y, w), v in ge.items():
            if y in ventana:
                por_semana.setdefault(str(w), {})[str(y)] = v
        if por_semana:
            hist[crop] = por_semana
    if not hist:
        return None
    log(f"histórico de condición: {len(hist)} cultivos, campañas "
        f"{ventana[0]}-{ventana[-1]}")
    return {"ventana": ventana, "cultivos": hist}


def completar_prom5(progress, anio):
    """
    Agrega el promedio de 5 años a las tablas de CONDICIÓN.

    NASS lo publica para las tablas de avance ("2021-2025 Average") pero no
    para las de condición, que traen solo las cinco categorías de la semana.
    Se reconstruye con el histórico de QuickStats: bueno+excelente en la
    MISMA semana ISO de cada una de las 5 campañas anteriores.

    Se informa n: si alguna campaña no tiene dato para esa semana (pasa
    cuando la campaña arrancó o terminó corrida), el promedio va sobre menos
    de 5 años y conviene que se note.
    """
    import datetime as _dt

    if not progress or not progress.get("tablas") or not _GE_CACHE:
        return progress

    fecha = progress.get("semana_termina")
    semana = progress.get("semana")
    if fecha:
        try:
            semana = _dt.datetime.strptime(fecha, "%B %d, %Y").date().isocalendar()[1]
        except ValueError:
            pass
    if not semana:
        return progress

    for titulo, datos in progress["tablas"].items():
        if datos.get("tipo") != "condicion":
            continue
        crop = CP_CONDICION.get(titulo)
        ge = _GE_CACHE.get(crop) if crop else None
        if not ge:
            continue
        vals = [ge[(y, semana)] for y in range(anio - 5, anio) if (y, semana) in ge]
        if not vals:
            continue
        datos["promedio_5a"] = round(sum(vals) / len(vals), 1)
        datos["promedio_5a_n"] = len(vals)
        log(f"prom 5a {titulo} (semana {semana}): "
            f"{datos['promedio_5a']}% sobre {len(vals)} campanias")

    return progress


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="usda_data.json")
    ap.add_argument("--nass-key", default=os.environ.get("NASS_API_KEY", ""))
    ap.add_argument("--cache", default="/tmp/psd")
    ap.add_argument("--year", type=int, default=2026)
    args = ap.parse_args()

    # Cada fuente es independiente: PSD, NASS, Crop Progress, ONI y el monitor
    # de sequia viven en servidores distintos. Si una se cae, las demas tienen
    # que actualizarse igual. Las claves que no se generan se omiten del JSON,
    # y usda_update_index.py conserva el valor anterior del index.html.
    out = {}
    fallas = []

    def intentar(clave, fn):
        try:
            v = fn()
        except Exception as e:                      # noqa: BLE001
            log(f"ERROR en '{clave}': {e} - se conserva el dato anterior")
            fallas.append(clave)
            return
        if v:
            out[clave] = v
        else:
            log(f"'{clave}' vino vacio - se conserva el dato anterior")
            fallas.append(clave)

    intentar("series", lambda: shape_series(build_psd_series(load_psd(args.cache))))
    if args.nass_key:
        intentar("condicion", lambda: build_condition(args.nass_key, args.year))
    else:
        log("sin NASS_API_KEY: se omite la condicion de cultivos")
    intentar("condicion_hist", lambda: build_condicion_hist(args.year))
    intentar("crop_progress", lambda: build_progress(args.year))
    if "crop_progress" in out:
        try:
            completar_prom5(out["crop_progress"], args.year)
        except Exception as e:                      # noqa: BLE001
            log(f"ERROR calculando el promedio de 5 anios: {e}")
    intentar("enso", build_enso)
    intentar("sequia", build_drought)

    if not out:
        sys.exit("Fallaron todas las fuentes; no se escribe nada.")

    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, separators=(",", ":"))
    log(f"escrito {args.out} ({os.path.getsize(args.out)/1024:.0f} KB) - "
        f"actualizadas: {', '.join(sorted(out))}"
        + (f" · sin novedad: {', '.join(fallas)}" if fallas else ""))


if __name__ == "__main__":
    import urllib.parse  # noqa: E402  (usado en nass_get)
    main()
