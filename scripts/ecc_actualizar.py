#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Actualiza el bloque ECC_DATA de index.html con las filas nuevas del ECC.

Por qué existe: el sitio de la Bolsa de Cereales está detrás de Cloudflare, así
que ni GitHub Actions ni este sandbox pueden bajar los datos. El navegador sí,
porque resuelve el desafío. La tarea semanal "ecc-bolsa-cereales" hace el fetch
y el parseo del xlsx dentro del navegador y deja acá las filas crudas; este
script se encarga de la parte que conviene tener versionada: fusionar con el
histórico, recalcular las comparaciones y reescribir el bloque.

El histórico vive dentro de index.html (clave "hist"), así que no hay que
volver a bajar campañas viejas: solo llegan las semanas nuevas.

Uso:
    python3 ecc_actualizar.py --index index.html --filas filas.json
donde filas.json es [{"cultivo","campania","semana","condicion","siembra","cosecha"}, ...]
"""

import argparse
import datetime as dt
import io
import json
import re
import sys

INICIO = "// ECC_DATA:START"
FIN = "// ECC_DATA:END"
MESES = ["enero", "febrero", "marzo", "abril", "mayo", "junio", "julio",
         "agosto", "septiembre", "octubre", "noviembre", "diciembre"]
ANIOS_PROM = 5
SIEMBRA_MINIMA = 50
COSECHA_MAXIMA = 90


def log(m):
    print(f"[ecc] {m}", file=sys.stderr)


def orden_cronologico(semanas, valores=None):
    """
    Ordena las semanas de una campaña respetando el cruce de año.

    "Semana" es la semana ISO del calendario, no la de la campaña. Como una
    campaña cruza dos años (maíz 2025/26 va de la semana 48 de 2025 a la 35 de
    2026), ordenar por número da un orden equivocado.

    El criterio bueno es la cosecha, que dentro de una campaña solo puede subir:
    el punto donde cae de golpe es el corte entre un año y el siguiente. Sirve
    incluso cuando la campaña cubre las 52 semanas y no hay hueco que delate el
    arranque, que es el caso del maíz y la soja.

    Si no hay cosecha informada todavía (trigo en pleno crecimiento), se cae al
    hueco más grande del círculo de semanas.
    """
    s = sorted(int(x) for x in semanas)
    if len(s) < 2:
        return s

    if valores:
        cos = [valores.get(str(w), [None, None, None])[2] for w in s]
        if any(v for v in cos if v):
            caidas = []
            for i in range(len(s)):
                a, b = cos[i], cos[(i + 1) % len(s)]
                if a is not None and b is not None:
                    caidas.append((a - b, i))
            if caidas:
                caida, corte = max(caidas)
                if caida > 20:                      # una caída real, no ruido
                    return s[corte + 1:] + s[:corte + 1]

    huecos = [(s[i + 1] - s[i], i) for i in range(len(s) - 1)]
    huecos.append((s[0] + 53 - s[-1], len(s) - 1))
    _, corte = max(huecos)
    return s[corte + 1:] + s[:corte + 1]


def anio_de(camp):
    return int(str(camp).split("/")[0])


def vigente(camps):
    """
    La campaña con el dato más reciente, no la de número más alto.

    En la transición conviven dos: en septiembre el maíz 2025/26 termina de
    cosecharse mientras el 2026/27 empieza a sembrarse, y ambos reportan la
    misma semana. Ante empate se toma la nueva, que es la que mira el mercado
    de acá en adelante; la vieja ya no aporta decisión.
    """
    def clave(c):
        sems = orden_cronologico(camps[c].keys(), camps[c])
        if not sems:
            return (0, 0, 0)
        return (anio_de(c) + (1 if sems[-1] < sems[0] else 0), sems[-1], anio_de(c))
    return max(camps, key=clave) if camps else None


def acumular(semanas):
    """
    Arrastra el máximo de siembra y cosecha.

    La Bolsa informa el avance de siembra solo mientras se siembra: al llegar a
    100% deja de publicarlo y la celda vuelve a cero. Sin esto el maíz
    aparecería con 0% sembrado en pleno agosto.
    """
    for idx in (1, 2):
        tope = 0.0
        for s in orden_cronologico(semanas.keys(), semanas):
            v = semanas[str(s)][idx]
            if v is not None and v > tope:
                tope = v
            if tope > 0:
                semanas[str(s)][idx] = tope

    # Con la cosecha prácticamente terminada la condición deja de relevarse:
    # la Bolsa arrastra el último valor y a veces publica filas erróneas. Y
    # comparar la condición de un lote ya cosechado no aporta nada.
    for s in semanas:
        sie, cos = semanas[s][1], semanas[s][2]
        if (sie is None or sie <= SIEMBRA_MINIMA) or (cos is not None and cos >= COSECHA_MAXIMA):
            semanas[s][0] = None
    return semanas


def comparar(camps, vig, idx):
    sems = orden_cronologico(camps[vig].keys(), camps[vig])
    if not sems:
        return None
    w = sems[-1]
    prevs = sorted((c for c in camps if anio_de(c) < anio_de(vig)),
                   key=anio_de, reverse=True)[:ANIOS_PROM]

    def val(c, s):
        return (camps[c].get(str(s)) or [None, None, None])[idx]

    vals = [val(c, w) for c in prevs if val(c, w) is not None]
    return {
        "semana": w,
        "actual": val(vig, w),
        "semana_previa": val(vig, sems[-2]) if len(sems) > 1 else None,
        "anio_previo": val(prevs[0], w) if prevs else None,
        "prom5": round(sum(vals) / len(vals), 1) if vals else None,
        "prom5_n": len(vals),
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--index", default="index.html")
    ap.add_argument("--filas", required=True)
    args = ap.parse_args()

    html = io.open(args.index, encoding="utf-8").read()
    m = re.search(re.escape(INICIO) + r".*?" + re.escape(FIN), html, re.S)
    if not m:
        sys.exit("No están los marcadores ECC_DATA en " + args.index)
    datos = json.loads(re.search(r"const ECC_DATA\s*=\s*(\{.*\});", m.group(0), re.S).group(1))

    nuevas = json.load(io.open(args.filas, encoding="utf-8"))
    agregadas = 0
    for f in nuevas:
        cr, camp, sem = f["cultivo"], str(f["campania"]), str(int(f["semana"]))
        if not camp or camp == "None":
            continue
        fila = [f.get("condicion"), f.get("siembra"), f.get("cosecha")]
        if all(v is None for v in fila):
            continue
        h = datos.setdefault("hist", {}).setdefault(cr, {}).setdefault(camp, {})
        if h.get(sem) != fila:
            agregadas += 1
        h[sem] = fila

    log(f"filas nuevas o corregidas: {agregadas}")

    sem_max = 0
    for cr, camps in datos["hist"].items():
        for camp in camps:
            acumular(camps[camp])
        vig = vigente(camps)
        if not vig:
            continue
        fila = {"campania": vig}
        for nombre, idx in (("condicion", 0), ("siembra", 1), ("cosecha", 2)):
            fila[nombre] = comparar(camps, vig, idx)
        fila["semana"] = fila["condicion"]["semana"]
        prevs = sorted((c for c in camps if anio_de(c) < anio_de(vig)),
                       key=anio_de, reverse=True)
        fila["campania_previa"] = prevs[0] if prevs else None
        datos["cultivos"][cr] = fila
        sem_max = max(sem_max, fila["semana"])
        log(f"{cr}: {vig} semana {fila['semana']} — condición "
            f"{fila['condicion']['actual']}%")

    hoy = dt.date.today()
    anio = hoy.year if sem_max <= hoy.isocalendar()[1] + 1 else hoy.year - 1
    try:
        f = dt.date.fromisocalendar(anio, sem_max, 4)      # el PAS sale los jueves
    except ValueError:
        f = hoy
    datos["semana"] = sem_max
    datos["fecha"] = f"{f.day} de {MESES[f.month - 1]} de {f.year}"
    datos["fecha_iso"] = f.isoformat()

    bloque = (INICIO + " — bloque autogenerado por la tarea semanal, no editar a mano\n"
              "const ECC_DATA = "
              + json.dumps(datos, ensure_ascii=False, separators=(",", ":"))
              + ";\n" + FIN)
    html = html[:m.start()] + bloque + html[m.end():]
    io.open(args.index, "w", encoding="utf-8").write(html)
    log(f"index.html actualizado — semana {sem_max} ({datos['fecha']})")


if __name__ == "__main__":
    main()
