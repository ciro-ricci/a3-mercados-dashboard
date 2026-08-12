#!/usr/bin/env python3
"""
Actualiza GIRASOL_MANI_DATA en index.html (pestaña "Girasol y Maní").

Fuentes (todas verificadas como accesibles con requests simple, sin
navegador headless):
- Girasol disponible (Cámara Arbitral Rosario): Bolsa de Comercio de
  Rosario, Cotizaciones Locales.
- Girasol FOB oficial + FAS teórico + FOB aceite/pellets (referencia
  internacional del complejo): Bolsa de Comercio de Rosario, FOB/FAS
  Argentina.
  (Se descartó bolsadecereales.com como fuente: su sitio está detrás de
  un challenge de Cloudflare que bloquea requests automatizados sin
  navegador real.)
- Maní disponible (Industria / Runner): Bolsa de Comercio de Córdoba
  (BCCBA), portada.
- Referencia USDA (Weekly National Posted Prices for Peanuts): FSA/USDA.
- Dólar oficial (A 3500): API pública del BCRA, para construir el punto
  del mes en curso de la serie histórica de girasol en USD oficial.

Corre una vez por semana. Cada corrida agrega o actualiza el punto de la
fecha de hoy en girasol.hist_usd (disponible ÷ TC oficial A3500). El
backfill inicial (25/08/2025 a 10/08/2026) es una serie diaria real de
pizarra Rosario cargada por el usuario desde una planilla externa, ya en
USD -- no se recalcula ni se toca, solo se le van agregando puntos nuevos.

El FOB implícito de maní (valor unitario de exportación, INDEC) se descartó
como automatizable: comex.indec.gov.ar/search es un SPA sin API publica y
esta protegido con captcha. No se muestra esa tarjeta en el dashboard.

El disponible de maní (Industria y Runner) tambien se va guardando cada
corrida en mani.hist_industria_ars / mani.hist_runner_ars (mismo mecanismo
que girasol.hist_usd_oficial), para poder construir mas adelante un
grafico de evolucion propio.
"""
import json
import re
import sys
import io
from datetime import datetime, timezone

import requests
import urllib3
from bs4 import BeautifulSoup

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

INDEX_PATH = "index.html"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
}

BCR_LOCALES_URL = "https://www.bcr.com.ar/es/mercados/mercado-de-granos/cotizaciones/cotizaciones-locales-0"
BCR_FOB_FAS_URL = "https://www.bcr.com.ar/es/mercados/mercado-de-granos/cotizaciones/cotizaciones-locales-1"
BCCBA_URL = "https://www.bccba.org.ar/"
BCRA_URL = "https://api.bcra.gob.ar/estadisticas/v4.0/monetarias/5?limit=1&offset=0"
USDA_LISTING_URL = "https://www.fsa.usda.gov/resources/economic-policy-analysis/reports/peanut/weekly-national-posted-prices-peanuts"


def fetch_html(url, retries=3):
    last_err = None
    for _ in range(retries):
        try:
            r = requests.get(url, headers=HEADERS, timeout=25)
            r.raise_for_status()
            return r.text
        except Exception as e:
            last_err = e
    raise RuntimeError(f"No se pudo bajar {url}: {last_err}")


def to_float_ar(s):
    s = s.strip().replace("$", "").replace("U$S", "").strip()
    if not s or s.upper() in ("S/C", "-", "N/A"):
        return None
    s = s.replace(".", "").replace(",", ".")
    try:
        return float(s)
    except ValueError:
        return None


def parse_girasol_disponible(html):
    soup = BeautifulSoup(html, "lxml")
    for table in soup.find_all("table"):
        for tr in table.find_all("tr"):
            cells = [td.get_text(strip=True) for td in tr.find_all(["td", "th"])]
            if not cells:
                continue
            if cells[0] == "Girasol":
                for c in cells[2:]:
                    val = to_float_ar(c)
                    if val is not None:
                        return val
    return None


def parse_girasol_fob(html):
    """Del bloque 'Cálculo del FAS Teórico para la Exportación de Granos':
    el Commodity Soja/Girasol tiene a Girasol siempre en la última columna
    (Spot). Y del bloque 'Industria Aceitera': Complejo Girasol trae FOB
    aceite y pellets."""
    soup = BeautifulSoup(html, "lxml")
    result = {}
    tables = soup.find_all("table")
    if not tables:
        return result
    rows = []
    for table in tables:
        for tr in table.find_all("tr"):
            cells = [td.get_text(strip=True) for td in tr.find_all(["td", "th"])]
            rows.append(cells)

    in_soja_girasol_block = False
    complejo_girasol_idx = None
    for cells in rows:
        if not cells:
            continue
        label = cells[0]
        if label == "Commodity" and len(cells) > 1 and "Soja" in cells[1]:
            in_soja_girasol_block = True
            continue
        if label == "Commodity":
            in_soja_girasol_block = False
            continue
        if in_soja_girasol_block and label.startswith("FOB comprador"):
            last_val = None
            for c in cells[1:]:
                v = to_float_ar(c)
                if v is not None:
                    last_val = v
            if last_val is not None:
                result["fob_oficial_usd"] = last_val
        if in_soja_girasol_block and label.startswith("FAS Teórico"):
            last_val = None
            for c in cells[1:]:
                v = to_float_ar(c)
                if v is not None:
                    last_val = v
            if last_val is not None:
                result["fas_teorico_usd"] = last_val

        if label == "Complejo / Complex":
            for i, c in enumerate(cells):
                if "Girasol" in c:
                    complejo_girasol_idx = i
                    break
        if complejo_girasol_idx is not None and label.startswith("FOB ACEITE"):
            if complejo_girasol_idx < len(cells):
                v = to_float_ar(cells[complejo_girasol_idx])
                if v is not None:
                    result["internacional_usd"] = v
                    result["internacional_label"] = "Aceite de girasol FOB (BCR)"
        if complejo_girasol_idx is not None and label.startswith("FOB PELLETS"):
            if complejo_girasol_idx < len(cells):
                v = to_float_ar(cells[complejo_girasol_idx])
                if v is not None:
                    result["pellets_usd"] = v

    today = datetime.now(timezone.utc).strftime("%b-%y").lower()
    result.setdefault("fob_posicion", "spot")
    return result


def parse_bccba_mani(html):
    result = {}
    m_ind = re.search(r"Man[ií] Industria.*?\$\s*([\d.,]+)", html, re.S)
    if m_ind:
        v = to_float_ar(m_ind.group(1))
        if v is not None:
            result["disponible_industria_ars"] = v
    m_run = re.search(
        r"Man[ií] Runner.*?\$\s*([\d.,]+)(?:.*?U\$S\s*([\d.,]+))?", html, re.S
    )
    if m_run:
        v = to_float_ar(m_run.group(1))
        if v is not None:
            result["disponible_runner_ars"] = v
        if m_run.group(2):
            v2 = to_float_ar(m_run.group(2))
            if v2 is not None:
                result["disponible_runner_usd"] = v2
    return result


def fetch_a3500():
    resp = requests.get(BCRA_URL, verify=False, timeout=20)
    resp.raise_for_status()
    data = resp.json()
    detalle = data["results"][0]["detalle"]
    if not detalle:
        raise RuntimeError("La API del BCRA no devolvio datos.")
    ultimo = detalle[0]
    return float(ultimo["valor"]), ultimo["fecha"]


def fetch_usda_peanut_prices():
    listing_html = fetch_html(USDA_LISTING_URL)
    pdfs = re.findall(r'href="([^"]*peanut\d{6}\.pdf)"', listing_html, re.I)
    if not pdfs:
        raise RuntimeError("No se encontro ningun PDF de precios de mani en USDA.")
    pdf_url = pdfs[0]
    if pdf_url.startswith("/"):
        pdf_url = "https://www.fsa.usda.gov" + pdf_url

    import pdfplumber

    r = requests.get(pdf_url, headers=HEADERS, timeout=25)
    r.raise_for_status()
    with pdfplumber.open(io.BytesIO(r.content)) as pdf:
        text = "\n".join(page.extract_text() or "" for page in pdf.pages)

    def grab(tipo):
        m = re.search(r"\$([\d,]+\.\d{2}) per ton for " + tipo, text, re.I)
        if m:
            try:
                return float(m.group(1).replace(",", ""))
            except ValueError:
                return None
        return None

    runner = grab("Runner")
    spanish = grab("Spanish")
    valencia = grab("Valencia")
    virginia = grab("Virginia")

    fecha = None
    m_fecha = re.search(
        r"(January|February|March|April|May|June|July|August|September|October|November|December) (\d{1,2}), (\d{4})",
        text,
    )
    if m_fecha:
        try:
            fecha = datetime.strptime(
                f"{m_fecha.group(1)} {m_fecha.group(2)}, {m_fecha.group(3)}", "%B %d, %Y"
            ).strftime("%Y-%m-%d")
        except ValueError:
            fecha = None

    if runner is None:
        raise RuntimeError(f"No se pudo extraer el precio Runner del PDF {pdf_url}")

    return {
        "fecha": fecha or datetime.now(timezone.utc).strftime("%Y-%m-%d"),
        "runner": runner,
        "spanish": spanish,
        "valencia": valencia,
        "virginia": virginia,
    }


def _mes_add(mes, delta):
    y, m = map(int, mes.split("-"))
    m += delta
    y += (m - 1) // 12
    m = ((m - 1) % 12) + 1
    return f"{y:04d}-{m:02d}"


def _fill_gap_months(hist, mes_actual):
    """Si el script no corrio (o fallo) uno o mas meses, completa esos meses
    salteados con valor null en vez de dejarlos directamente ausentes del
    arreglo -- el grafico usa spanGaps:false y necesita el punto null para
    dibujar el corte, si no el mes salteado queda invisible y la linea
    conecta como si fuera continuo."""
    if not hist:
        return hist
    last_mes = hist[-1]["mes"]
    cursor = _mes_add(last_mes, 1)
    existentes = {h["mes"] for h in hist}
    while cursor < mes_actual:
        if cursor not in existentes:
            hist.append({"mes": cursor, "valor": None})
        cursor = _mes_add(cursor, 1)
    return hist


def extract_current_block(html_text):
    m = re.search(r"const GIRASOL_MANI_DATA = (\{.*?\n\};)", html_text, re.S)
    if not m:
        raise RuntimeError("No se encontro GIRASOL_MANI_DATA en index.html")
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


def py_to_js_obj(obj, indent=2):
    return json.dumps(obj, ensure_ascii=False, indent=indent)


def main():
    changed = False
    with open(INDEX_PATH, "r", encoding="utf-8") as f:
        html_text = f.read()

    full_match, block_text = extract_current_block(html_text)
    try:
        data = js_obj_to_py(block_text)
    except Exception as e:
        print(f"[ERROR] No se pudo parsear GIRASOL_MANI_DATA existente: {e}", file=sys.stderr)
        sys.exit(1)

    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    mes_actual = datetime.now(timezone.utc).strftime("%Y-%m")

    try:
        locales_html = fetch_html(BCR_LOCALES_URL)
        disp = parse_girasol_disponible(locales_html)
        if disp is not None:
            data["girasol"]["disponible_rosario_ars"] = disp
            data["girasol"]["fecha"] = today
            changed = True
            print(f"Girasol disponible Rosario: {disp}")
        else:
            print("[WARN] Girasol disponible: sin fijacion (S/C) esta semana, se mantiene el ultimo valor.")
    except Exception as e:
        print(f"[WARN] Girasol disponible (BCR) fallo: {e}")

    try:
        fob_html = fetch_html(BCR_FOB_FAS_URL)
        fob_vals = parse_girasol_fob(fob_html)
        if fob_vals:
            data["girasol"].update(fob_vals)
            data["girasol"]["fecha"] = today
            changed = True
            print(f"Girasol FOB/FAS/internacional: {fob_vals}")
        else:
            print("[WARN] No se pudo extraer FOB/FAS de girasol.")
    except Exception as e:
        print(f"[WARN] Girasol FOB (BCR) fallo: {e}")

    try:
        tc_valor, tc_fecha = fetch_a3500()
        rosario_ars = data["girasol"].get("disponible_rosario_ars")
        if rosario_ars:
            usd_oficial = round(rosario_ars / tc_valor, 1)
            hist = data["girasol"].get("hist_usd", [])
            found = False
            for h in hist:
                if h["fecha"] == today:
                    h["valor"] = usd_oficial
                    found = True
                    break
            if not found:
                hist.append({"fecha": today, "valor": usd_oficial})
                hist.sort(key=lambda h: h["fecha"])
            data["girasol"]["hist_usd"] = hist
            changed = True
            print(f"Girasol USD {today}: {usd_oficial} (TC {tc_valor})")
    except Exception as e:
        print(f"[WARN] Calculo de USD oficial girasol fallo: {e}")

    try:
        bccba_html = fetch_html(BCCBA_URL)
        mani_vals = parse_bccba_mani(bccba_html)
        if mani_vals:
            data["mani"].update(mani_vals)
            data["mani"]["fecha"] = today
            changed = True
            print(f"Mani (BCCBA): {mani_vals}")

            def _update_hist(campo_hist, campo_valor):
                valor = data["mani"].get(campo_valor)
                if valor is None:
                    return
                hist = data["mani"].get(campo_hist, [])
                found = False
                for h in hist:
                    if h["mes"] == mes_actual:
                        h["valor"] = valor
                        found = True
                        break
                if not found:
                    hist.append({"mes": mes_actual, "valor": valor})
                data["mani"][campo_hist] = hist

            _update_hist("hist_industria_ars", "disponible_industria_ars")
            _update_hist("hist_runner_ars", "disponible_runner_ars")
        else:
            print("[WARN] No se pudo extraer ningun valor de mani de BCCBA.")
    except Exception as e:
        print(f"[WARN] Mani/BCCBA fallo: {e}")

    try:
        usda = fetch_usda_peanut_prices()
        data["mani"]["usda"] = usda
        changed = True
        print(f"USDA peanut prices: {usda}")
    except Exception as e:
        print(f"[WARN] USDA fallo, se mantiene la referencia anterior: {e}")

    if not changed:
        print("Sin cambios en GIRASOL_MANI_DATA.")
        return False

    new_block_text = "const GIRASOL_MANI_DATA = " + py_to_js_obj(data) + ";"
    html_text = html_text.replace(full_match, new_block_text, 1)

    with open(INDEX_PATH, "w", encoding="utf-8") as f:
        f.write(html_text)

    print("index.html actualizado: GIRASOL_MANI_DATA")
    return True


if __name__ == "__main__":
    main()
    raise SystemExit(0)
