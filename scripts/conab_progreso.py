#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Progreso de safra de CONAB (Brasil): avance de plantio y colheita semanal.

CONAB publica cada semana una planilla con el avance por estado y, lo mejor,
ya trae calculadas las tres comparaciones que necesitamos: misma semana del
año anterior, semana previa y promedio de 5 años. No hay que reconstruir nada.

    https://www.gov.br/conab/pt-br/atuacao/informacoes-agropecuarias/safras/progresso-de-safra

Estructura de la planilla (una sola hoja):
    <Cultivo> - Safra <campaña>          <- encabezado de bloque
    (Esos N estados corresponden al X%)  <- cobertura
    Plantio / Colheita                   <- de qué avance se trata
    Estado | año previo | semana previa | semana actual | Média 5 anos
    ...una fila por estado...
    N estados | ...                      <- total nacional ponderado

Los valores vienen como fracción (0 a 1), no como porcentaje.

Uso:
    python3 conab_progreso.py --out /tmp/conab_prog.json
"""

import argparse
import datetime as dt
import io
import json
import re
import sys
import urllib.request

INDICE = ("https://www.gov.br/conab/pt-br/atuacao/informacoes-agropecuarias/"
          "safras/progresso-de-safra")

# Nombre en la planilla -> clave interna. CONAB separa el maíz por safra.
CULTIVOS = {
    "soja": "soja",
    "milho 1ª": "maiz1", "milho 1a": "maiz1",
    "milho 2ª": "maiz2", "milho 2a": "maiz2",
    "milho 3ª": "maiz3", "milho 3a": "maiz3",
    "milho": "maiz",
    "trigo": "trigo",
}


def log(m):
    print(f"[conab-prog] {m}", file=sys.stderr)


def fetch(url, timeout=90, intentos=3):
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    ultimo = None
    for i in range(1, intentos + 1):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return r.read()
        except Exception as e:                      # noqa: BLE001
            ultimo = e
            if i < intentos:
                log(f"fallo {i}/{intentos} en {url} ({e})")
    raise ultimo


def ultima_planilla():
    """
    Encuentra la planilla más reciente.

    El índice lista una página por semana con el patrón
    'acompanhamento-das-lavouras-DD-MM-a-DD-MM-YY'. Dentro de cada una hay un
    adjunto 'plantio-e-colheita-...' que se baja con /@@download/file.
    Se recorre el índice de más nueva a más vieja en vez de adivinar la fecha,
    porque CONAB a veces se saltea semanas.
    """
    html = fetch(INDICE).decode("utf-8", errors="replace")
    semanas = re.findall(
        r'href="([^"]*acompanhamento-das-lavouras-(\d{2})-(\d{2})-a-(\d{2})-(\d{2})-(\d{2}))"',
        html)
    vistas, orden = set(), []
    for url, d1, m1, d2, m2, yy in semanas:
        url = url.rstrip("/")
        if url in vistas:
            continue
        vistas.add(url)
        orden.append((dt.date(2000 + int(yy), int(m2), int(d2)), url))
    orden.sort(reverse=True)

    for fecha, url in orden[:4]:
        pagina = fetch(url).decode("utf-8", errors="replace")
        m = re.search(r'href="([^"]*plantio-e-colheita[^"]*?)(?:/view)?"', pagina)
        if not m:
            continue
        return m.group(1).rstrip("/") + "/@@download/file", fecha
    return None, None


def _pct(v):
    if v is None or isinstance(v, str):
        return None
    try:
        return round(float(v) * 100, 1)
    except (TypeError, ValueError):
        return None


def parsear(xlsx_bytes):
    import openpyxl
    ws = openpyxl.load_workbook(io.BytesIO(xlsx_bytes), data_only=True).active
    filas = [list(r) for r in ws.iter_rows(values_only=True)]

    out, actual = {}, None
    for r in filas:
        celdas = [c for c in r if c is not None]
        if not celdas:
            continue
        primera = str(celdas[0]).strip()

        m = re.match(r"^(.+?)\s*-\s*Safra\s+(.+)$", primera, re.I)
        if m:
            nombre = m.group(1).strip().lower()
            clave = None
            for k in sorted(CULTIVOS, key=len, reverse=True):
                if nombre.startswith(k):
                    clave = CULTIVOS[k]
                    break
            actual = {"clave": clave, "safra": m.group(2).strip(),
                      "cultivo": m.group(1).strip()} if clave else None
            continue

        if actual is None:
            continue

        if primera.lower().startswith(("plantio", "colheita", "semeadura")):
            actual["operacion"] = ("plantio" if primera.lower().startswith(("plantio", "semeadura"))
                                   else "colheita")
            continue

        # fila del total nacional: "N estados"
        if re.match(r"^\d+\s+estados?$", primera, re.I) and actual.get("operacion"):
            nums = [_pct(c) for c in r[2:6]]
            if len([n for n in nums if n is not None]) >= 3:
                out.setdefault(actual["clave"], {})[actual["operacion"]] = {
                    "safra": actual["safra"],
                    "cobertura": primera,
                    "anio_previo": nums[0],
                    "semana_previa": nums[1],
                    "actual": nums[2],
                    "prom5": nums[3],
                }
                log(f"{actual['clave']} {actual['operacion']}: "
                    f"{nums[2]}% (prom5 {nums[3]}%)")
            actual["operacion"] = None
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="/tmp/conab_prog.json")
    ap.add_argument("--merge-into", default=None,
                    help="JSON del extractor USDA: agrega la clave conab_prog "
                         "para que usda_update_index.py lo publique en el mismo paso")
    args = ap.parse_args()

    url, fecha = ultima_planilla()
    if not url:
        sys.exit("No se encontró la planilla de plantio y colheita.")
    log(f"planilla: {url}")
    datos = parsear(fetch(url))
    if not datos:
        sys.exit("La planilla no trajo ningún cultivo reconocible.")

    meses = ["enero", "febrero", "marzo", "abril", "mayo", "junio", "julio",
             "agosto", "septiembre", "octubre", "noviembre", "diciembre"]
    out = {
        "fuente": "CONAB — Progresso de Safra",
        "url": INDICE,
        "fecha_iso": fecha.isoformat(),
        "fecha": f"{fecha.day} de {meses[fecha.month - 1]} de {fecha.year}",
        "cultivos": datos,
    }
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, separators=(",", ":"))
    log(f"escrito {args.out} — cultivos: {sorted(datos)}")

    if args.merge_into:
        try:
            with open(args.merge_into, encoding="utf-8") as f:
                base = json.load(f)
        except (OSError, ValueError):
            base = {}
        base["conab_prog"] = out
        with open(args.merge_into, "w", encoding="utf-8") as f:
            json.dump(base, f, ensure_ascii=False, separators=(",", ":"))
        log(f"agregado a {args.merge_into}")


if __name__ == "__main__":
    main()
