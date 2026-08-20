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
import datetime
import io
import json
import re
import sys

# Mismo mapeo que usa el extractor, para poder reconstruir el promedio
# de 5 años sin volver a consultar QuickStats.
CP_CONDICION = {
    "Corn Condition":         "maiz",
    "Soybean Condition":      "soja",
    "Winter Wheat Condition": "trigo",
    "Spring Wheat Condition": "trigo_primavera",
}

INICIO = "// USDA_DATA:START"
FIN = "// USDA_DATA:END"
CLAVES_DUROS = ("series", "condicion", "crop_progress", "enso", "sequia",
                "condicion_hist")


def _completar_prom5_desde_hist(datos):
    """
    Rellena el promedio de 5 años de las tablas de condición usando el bloque
    histórico guardado.

    El extractor ya lo calcula cuando QuickStats responde. Esto cubre el caso
    contrario: Crop Progress se actualizó (servidor de NASS releases) pero
    QuickStats no contestó (otro servidor). Sin esto, la columna quedaría
    vacía justo la semana que falla, aunque el dato histórico no cambie nunca.
    """
    hist = (datos.get("condicion_hist") or {}).get("cultivos") or {}
    cp = datos.get("crop_progress") or {}
    tablas = cp.get("tablas") or {}
    if not hist or not tablas:
        return

    semana = cp.get("semana")
    fecha = cp.get("semana_termina")
    if fecha:
        try:
            semana = datetime.datetime.strptime(
                fecha, "%B %d, %Y").date().isocalendar()[1]
        except ValueError:
            pass
    if not semana:
        return

    for titulo, d in tablas.items():
        if d.get("tipo") != "condicion" or d.get("promedio_5a") is not None:
            continue
        crop = CP_CONDICION.get(titulo)
        vals = list(((hist.get(crop) or {}).get(str(semana)) or {}).values())
        if not vals:
            continue
        d["promedio_5a"] = round(sum(vals) / len(vals), 1)
        d["promedio_5a_n"] = len(vals)
        print(f"Prom. 5 anios de {titulo} reconstruido desde el historico: "
              f"{d['promedio_5a']}% (n={len(vals)})")


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

    # El histórico de condición es una tabla fija (las 5 campañas de
    # referencia ya cerradas). Si la corrida no lo trajo porque QuickStats no
    # contestó, se conserva el que ya estaba: no cambia de una semana a otra.
    if not combinado.get("condicion_hist") and (actual.get("condicion_hist")):
        combinado["condicion_hist"] = actual["condicion_hist"]
        print("Aviso: se conserva el histórico de condición anterior.")

    _completar_prom5_desde_hist(combinado)

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
