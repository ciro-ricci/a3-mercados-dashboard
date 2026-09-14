#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Escribe el bloque CONAB_DATA dentro de index.html.

Por qué existe: hasta septiembre de 2026 este paso lo hacía una tarea
programada de Cowork que corría en la máquina de CIRO. Una actualización de
Windows rompió el montaje del workspace y la tarea dejó de poder ejecutar
Python. Como bajar el Boletim de CONAB y volcarlo al índice no requiere
criterio —es puro procesamiento— se mudó al GitHub Action, que corre en los
servidores de GitHub y no depende de que la máquina de CIRO esté encendida ni
de que el workspace monte bien.

Uso:
    python3 conab_extractor.py --out /tmp/conab_data.json
    python3 conab_update_index.py --data /tmp/conab_data.json --index index.html
"""

import argparse
import io
import json
import re
import sys

INICIO = "// CONAB_DATA:START"
FIN = "// CONAB_DATA:END"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", required=True)
    ap.add_argument("--index", default="index.html")
    args = ap.parse_args()

    nuevos = json.load(io.open(args.data, encoding="utf-8"))
    if not nuevos.get("cultivos"):
        sys.exit("El JSON de CONAB no trae cultivos; no se toca el índice.")

    html = io.open(args.index, encoding="utf-8").read()
    m = re.search(re.escape(INICIO) + r".*?" + re.escape(FIN), html, re.S)
    if not m:
        sys.exit("No están los marcadores CONAB_DATA en " + args.index)

    # Si los números no cambiaron, no se reescribe: así el paso de commit del
    # workflow no genera ruido en el historial mes a mes.
    anterior = re.search(r"const CONAB_DATA\s*=\s*(\{.*\});", m.group(0), re.S)
    if anterior:
        try:
            if json.loads(anterior.group(1)) == nuevos:
                print("CONAB sin cambios; no se reescribe el bloque.")
                return
        except json.JSONDecodeError:
            pass

    bloque = (INICIO + " — Boletim da Safra de Graos, lo escribe el workflow, no editar a mano\n"
              "const CONAB_DATA = "
              + json.dumps(nuevos, ensure_ascii=False, separators=(",", ":"))
              + ";\n" + FIN)

    # str.replace y no re.sub: las barras invertidas del JSON no deben
    # interpretarse como grupos de reemplazo.
    html = html[:m.start()] + bloque + html[m.end():]
    io.open(args.index, "w", encoding="utf-8").write(html)

    resumen = ", ".join(sorted(nuevos["cultivos"]))
    print("CONAB_DATA actualizado — cultivos: " + resumen)


if __name__ == "__main__":
    main()
