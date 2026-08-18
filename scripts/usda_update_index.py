#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Actualiza el bloque USDA_DATA dentro de index.html con los datos frescos
del extractor, PRESERVANDO el análisis cualitativo del WASDE.

Reemplaza solo las claves de datos duros:
    series · condicion · crop_progress · enso · sequia

Y deja intactas las que escribe el análisis mensual:
    wasde · wwcb

Uso:
    python3 usda_update_index.py --data /tmp/usda_data.json --index index.html
"""

import argparse
import io
import json
import re
import sys

INICIO = "// USDA_DATA:START"
FIN = "// USDA_DATA:END"
CLAVES_DUROS = ("series", "condicion", "crop_progress", "enso", "sequia")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", required=True)
    ap.add_argument("--index", default="index.html")
    args = ap.parse_args()

    html = io.open(args.index, encoding="utf-8").read()
    nuevos = json.load(io.open(args.data, encoding="utf-8"))

    patron = re.compile(re.escape(INICIO) + r".*?" + re.escape(FIN), re.S)
    m = patron.search(html)
    if not m:
        sys.exit("No se encontraron los marcadores USDA_DATA:START/END en " + args.index)

    # Recuperar el objeto actual para conservar wasde y wwcb
    actual = {}
    mo = re.search(r"const USDA_DATA\s*=\s*(\{.*\});", m.group(0), re.S)
    if mo:
        try:
            actual = json.loads(mo.group(1))
        except json.JSONDecodeError as e:
            print(f"Aviso: no se pudo parsear el bloque existente ({e}); "
                  "se reescribe solo con los datos nuevos.")

    combinado = dict(actual)
    for k in CLAVES_DUROS:
        if k in nuevos:
            combinado[k] = nuevos[k]

    # El RONI vive en un servidor universitario que se cae seguido. Si esta
    # corrida no lo trajo, se conserva el último valor bueno en vez de dejar
    # el dashboard sin RONI hasta la próxima corrida.
    viejo_roni = (actual.get("enso") or {}).get("roni")
    if viejo_roni and not (combinado.get("enso") or {}).get("roni"):
        if isinstance(combinado.get("enso"), dict):
            combinado["enso"]["roni"] = viejo_roni
            print(f"Aviso: RONI no vino en esta corrida; se conserva "
                  f"{viejo_roni.get('periodo')} = {viejo_roni.get('valor')}")

    faltantes = [k for k in ("wasde", "wwcb") if k not in combinado]
    if faltantes:
        print(f"Aviso: el bloque no traía {', '.join(faltantes)}; "
              "el análisis del WASDE debe cargarlo la tarea programada.")

    bloque = (INICIO + " — bloque autogenerado por las tareas programadas, no editar a mano\n"
              "const USDA_DATA = "
              + json.dumps(combinado, ensure_ascii=False, separators=(",", ":"))
              + ";\n" + FIN)

    # str.replace evita que las barras invertidas del JSON se interpreten
    # como grupos de reemplazo de una expresión regular.
    html = html[:m.start()] + bloque + html[m.end():]
    io.open(args.index, "w", encoding="utf-8").write(html)

    cp = combinado.get("crop_progress") or {}
    enso = (combinado.get("enso") or {}).get("ultimo") or {}
    print(f"index.html actualizado — Crop Progress: {cp.get('publicado', 's/d')} · "
          f"ONI: {enso.get('periodo', 's/d')} {enso.get('valor', '')}")


if __name__ == "__main__":
    main()
