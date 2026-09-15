#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Extractor del CASDE (China Agricultural Supply and Demand Estimates).

Es el equivalente chino del WASDE: lo publica el comité de alerta temprana del
Ministerio de Agricultura y Asuntos Rurales, y sale los MISMOS días que el
WASDE (12-ene, 10-feb, 10-mar, 9-abr, 12-may, 11-jun, 10-jul, 12-ago, 11-sep,
9-oct, 10-nov y 10-dic en 2026).

Por qué importa: el USDA estima cuánto va a importar China; el CASDE dice
cuánto dice China que va a importar. La brecha entre las dos es la señal. Con
la soja 2026/27 clavada en 95,5 Mt contra 108 del ciclo anterior, hay 12,5 Mt
de demanda que Beijing no está pidiendo y que el balance del USDA sí asume.

Fuente: https://www.agri.cn/sj/gxxs/ — las tablas vienen en HTML, no en PDF.
Cubre maíz, soja, aceites vegetales, algodón y azúcar. NO cubre trigo.

Particularidades:
  - Las cantidades vienen en 万吨 (diez mil toneladas): se dividen por 100 para
    pasar a millones de toneladas.
  - El área viene en 千公顷 (mil hectáreas): se divide por 1000 para M ha.
  - El rinde viene en 公斤/公顷 (kg por hectárea): se divide por 1000 para t/ha.
  - Cada tabla trae cuatro columnas: dos campañas cerradas, la previsión del mes
    anterior y la del mes actual. Las dos últimas llevan la misma etiqueta de
    campaña y solo se distinguen por el mes entre paréntesis, así que la
    etiqueta se conserva entera: sin eso no se sabe cuál es cuál.
  - Algunas celdas traen rangos de precios ("2250-2400"): se ignoran, no son
    cantidades.

Uso:
    python3 casde_extractor.py --out /tmp/casde.json
    python3 casde_extractor.py --out /tmp/casde.json --index index.html
"""

import argparse
import io
import json
import re
import sys
import urllib.parse
import urllib.request

INDICE = "https://www.agri.cn/sj/gxxs/"
INICIO = "// CASDE_DATA:START"
FIN = "// CASDE_DATA:END"

TABLAS = {
    "\u4e2d\u56fd\u7389\u7c73\u4f9b\u9700\u5e73\u8861\u8868": "maiz",
    "\u4e2d\u56fd\u5927\u8c46\u4f9b\u9700\u5e73\u8861\u8868": "soja",
}

# fila en chino -> (clave interna, divisor para llegar a nuestra unidad)
FILAS = {
    "\u64ad\u79cd\u9762\u79ef": ("area_sembrada", 1000.0),
    "\u6536\u83b7\u9762\u79ef": ("area", 1000.0),
    "\u5355\u4ea7": ("rinde", 1000.0),
    "\u4ea7\u91cf": ("produccion", 100.0),
    "\u8fdb\u53e3": ("importaciones", 100.0),
    "\u6d88\u8d39": ("consumo", 100.0),
    "\u9972\u7528\u6d88\u8d39": ("consumo_forrajero", 100.0),
    "\u538b\u69a8\u6d88\u8d39": ("molienda", 100.0),
    "\u51fa\u53e3": ("exportaciones", 100.0),
    "\u7ed3\u4f59\u53d8\u5316": ("cambio_stocks", 100.0),
}


def log(m):
    print("[casde] " + m, file=sys.stderr)


def fetch(url, timeout=120, intentos=3):
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    ultimo = None
    for i in range(1, intentos + 1):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return r.read().decode("utf-8", errors="replace")
        except Exception as e:                      # noqa: BLE001
            ultimo = e
            log("fallo %d/%d en %s (%s)" % (i, intentos, url, e))
    raise ultimo


def ultimo_informe():
    """El índice lista los informes del más nuevo al más viejo."""
    html = fetch(INDICE)
    m = re.search(r'href="([^"]*?/\d{6}/t(\d{8})_\d+\.htm)"[^>]*>\s*([^<]*CASDE[^<]*)', html)
    if not m:
        return None, None, None
    # los href vienen relativos al directorio del índice ("./202609/t....htm"),
    # así que hay que resolverlos contra INDICE y no pegarlos al dominio: si no,
    # se pierde el /sj/gxxs/ del medio y la URL resultante da 404.
    url = urllib.parse.urljoin(INDICE, m.group(1))
    return url, m.group(2), m.group(3).strip()


def _num(txt):
    t = (txt or "").strip().replace(",", "")
    if not re.match(r"^-?\d+(\.\d+)?$", t):
        return None                                  # rangos de precios y celdas vacías
    return float(t)


def parsear(html):
    """Devuelve {cultivo: {campanias: [...], filas: {clave: [4 valores]}}}."""
    # los títulos aparecen repetidos; el orden de aparición es el de las tablas
    secuencia, vistos = [], set()
    for t in re.findall("|".join(TABLAS), html):
        c = TABLAS[t]
        if c not in vistos:
            vistos.add(c)
            secuencia.append(c)

    tablas = re.findall(r"<table[^>]*>(.*?)</table>", html, re.S)
    out = {}
    for crop, tabla in zip(secuencia, tablas):
        campanias, datos = [], {}
        for fila in re.findall(r"<tr[^>]*>(.*?)</tr>", tabla, re.S):
            celdas = [re.sub(r"<[^>]+>", "", c) for c in
                      re.findall(r"<t[dh][^>]*>(.*?)</t[dh]>", fila, re.S)]
            celdas = [re.sub(r"\s+", " ", c.replace("&nbsp;", " ")).strip() for c in celdas]
            if not celdas:
                continue
            etiqueta = celdas[0]
            if not campanias and len(celdas) >= 5 and re.search(r"\d{4}/\d{2}", " ".join(celdas)):
                campanias = celdas[1:5]              # con el mes entre paréntesis incluido
                continue
            if etiqueta in FILAS:
                clave, div = FILAS[etiqueta]
                vals = [_num(c) for c in celdas[1:5]]
                if any(v is not None for v in vals):
                    datos[clave] = [round(v / div, 2) if v is not None else None for v in vals]
        if campanias and datos:
            out[crop] = {"campanias": campanias, "filas": datos}
            log("%s: %d variables" % (crop, len(datos)))
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="/tmp/casde.json")
    ap.add_argument("--index", default=None,
                    help="si se pasa, escribe el bloque CASDE_DATA en ese index.html")
    args = ap.parse_args()

    url, fecha, titulo = ultimo_informe()
    if not url:
        sys.exit("No se encontró ningún informe CASDE en el índice.")
    log("informe: " + titulo)
    datos = parsear(fetch(url))
    if not datos:
        sys.exit("No se pudo parsear ninguna tabla del informe.")

    numero = re.search(r"CASDE[-\u2014\s]*No\.?\s*(\d+)", titulo)
    out = {
        "fuente": "CASDE — Ministerio de Agricultura y Asuntos Rurales de China",
        "url": url,
        "titulo": titulo,
        "numero": numero.group(1) if numero else None,
        "fecha_iso": "%s-%s-%s" % (fecha[:4], fecha[4:6], fecha[6:8]),
        "cultivos": datos,
    }
    with io.open(args.out, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, separators=(",", ":"))
    log("escrito " + args.out)

    if not args.index:
        return

    html = io.open(args.index, encoding="utf-8").read()
    m = re.search(re.escape(INICIO) + r".*?" + re.escape(FIN), html, re.S)
    if not m:
        sys.exit("No están los marcadores CASDE_DATA en " + args.index)
    anterior = re.search(r"const CASDE_DATA\s*=\s*(\{.*\});", m.group(0), re.S)
    if anterior:
        try:
            if json.loads(anterior.group(1)) == out:
                log("CASDE sin cambios; no se reescribe el bloque.")
                return
        except json.JSONDecodeError:
            pass
    bloque = (INICIO + " \u2014 lo escribe el workflow, no editar a mano\n"
              "const CASDE_DATA = "
              + json.dumps(out, ensure_ascii=False, separators=(",", ":"))
              + ";\n" + FIN)
    html = html[:m.start()] + bloque + html[m.end():]
    io.open(args.index, "w", encoding="utf-8").write(html)
    log("CASDE_DATA actualizado en " + args.index)


if __name__ == "__main__":
    main()
