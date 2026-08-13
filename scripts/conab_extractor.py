#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Extractor del Boletim da Safra de Grãos de CONAB (Brasil).

Fuente: serie histórica oficial de CONAB, en un único archivo que la propia
CONAB regenera cada vez que publica un boletín nuevo (mensual, normalmente
entre el 8 y el 12).
    https://portaldeinformacoes.conab.gov.br/downloads/arquivos/SerieHistoricaGraos.txt

Formato: CSV con ";" y codificación latin-1. Columnas:
    ano_agricola | dsc_safra_previsao | uf | produto | id_produto
    area_plantada_mil_ha | producao_mil_t | produtividade_mil_ha_mil_t

Particularidades que hay que respetar:
  - Los datos vienen POR ESTADO (uf): hay que sumar para tener el total Brasil.
  - El maíz se publica separado en 1ª, 2ª y 3ª safra. La 2ª safra (safrinha)
    es la que domina: ~77% de la producción. Se publican las tres y el total.
  - La soja es "UNICA".
  - El trigo, al ser cultivo de invierno, se identifica por año calendario
    ("2025") y no por campaña partida ("2025/26").
  - El rinde NO se puede sumar entre estados: se recalcula producción/área.

Salida: bloque CONAB_DATA para la pestaña USDA.

Uso:
    python3 conab_extractor.py --out /tmp/conab_data.json
"""

import argparse
import csv
import io
import json
import re
import sys
import urllib.request
from collections import defaultdict

URL = "https://portaldeinformacoes.conab.gov.br/downloads/arquivos/SerieHistoricaGraos.txt"
BOLETIN = ("https://www.gov.br/conab/pt-br/atuacao/informacoes-agropecuarias/"
           "safras/safra-de-graos/boletim-da-safra-de-graos")

# cultivo interno -> (producto CONAB, tipos de safra a incluir)
CULTIVOS = {
    "soja":  ("SOJA",  ["UNICA"]),
    "maiz":  ("MILHO", ["1ª SAFRA", "2ª SAFRA", "3ª SAFRA"]),
    "trigo": ("TRIGO", ["UNICA"]),
}


def log(m):
    print(f"[conab] {m}", file=sys.stderr)


def descargar():
    req = urllib.request.Request(URL, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=180) as r:
        return r.read().decode("latin-1", errors="replace")


def clave_orden(campania):
    """Ordena tanto '2025/26' como '2025'."""
    m = re.match(r"(\d{4})", campania)
    return int(m.group(1)) if m else 0


def agregar(texto):
    """Suma los estados y devuelve {cultivo: {campania: {safra: {...}}}}."""
    acum = defaultdict(lambda: defaultdict(lambda: defaultdict(
        lambda: {"area": 0.0, "prod": 0.0})))
    quiero = {v[0]: k for k, v in CULTIVOS.items()}

    for row in csv.DictReader(io.StringIO(texto), delimiter=";"):
        prod = (row.get("produto") or "").strip()
        if prod not in quiero:
            continue
        crop = quiero[prod]
        camp = (row.get("ano_agricola") or "").strip()
        safra = (row.get("dsc_safra_previsao") or "").strip()
        if safra not in CULTIVOS[crop][1]:
            continue
        d = acum[crop][camp][safra]
        for col, tag in (("area_plantada_mil_ha", "area"), ("producao_mil_t", "prod")):
            try:
                d[tag] += float(row.get(col) or 0)
            except ValueError:
                pass
    return acum


def armar(acum, n_campanias=2):
    """Arma la comparación entre la campaña vigente y la anterior."""
    salida = {}
    for crop, camps in acum.items():
        orden = sorted(camps, key=clave_orden)
        # se descartan campañas vacías (CONAB a veces publica filas en cero)
        orden = [c for c in orden
                 if sum(v["prod"] for v in camps[c].values()) > 0]
        if len(orden) < 2:
            continue
        ult, prev = orden[-1], orden[-2]

        filas = []
        for safra in CULTIVOS[crop][1]:
            a = camps[ult].get(safra)
            p = camps[prev].get(safra)
            if not a or a["prod"] <= 0:
                continue
            filas.append(_fila(_nombre_safra(safra), a, p))

        # total del cultivo (para maíz es la suma de las tres safras)
        ta = {"area": sum(v["area"] for v in camps[ult].values()),
              "prod": sum(v["prod"] for v in camps[ult].values())}
        tp = {"area": sum(v["area"] for v in camps[prev].values()),
              "prod": sum(v["prod"] for v in camps[prev].values())}

        salida[crop] = {
            "campania": ult,
            "campania_previa": prev,
            "filas": filas if len(filas) > 1 else [],
            "total": _fila("Total", ta, tp),
        }
        log(f"{crop}: {ult} vs {prev} — {ta['prod']/1000:.1f} Mt")
    return salida


def _nombre_safra(s):
    return {"1ª SAFRA": "1ª safra", "2ª SAFRA": "2ª safra (safrinha)",
            "3ª SAFRA": "3ª safra", "UNICA": "Única"}.get(s, s)


def _fila(nombre, act, prev):
    """Convierte mil ha -> M ha y mil t -> Mt, y calcula variaciones."""
    def var(a, b):
        return round((a - b) / b * 100, 1) if b else None

    area = act["area"] / 1000
    prod = act["prod"] / 1000
    rinde = (act["prod"] / act["area"]) if act["area"] else None
    p_area = (prev or {}).get("area", 0) / 1000
    p_prod = (prev or {}).get("prod", 0) / 1000
    p_rinde = ((prev["prod"] / prev["area"])
               if prev and prev.get("area") else None)

    return {
        "n": nombre,
        "area": round(area, 2), "area_var": var(area, p_area),
        "prod": round(prod, 2), "prod_var": var(prod, p_prod),
        "rinde": round(rinde, 2) if rinde else None,
        "rinde_var": var(rinde, p_rinde) if (rinde and p_rinde) else None,
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="/tmp/conab_data.json")
    args = ap.parse_args()

    txt = descargar()
    datos = armar(agregar(txt))
    out = {"fuente": "CONAB — Boletim da Safra de Grãos",
           "url": BOLETIN, "url_datos": URL, "cultivos": datos}
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, separators=(",", ":"))
    log(f"escrito {args.out} — cultivos: {sorted(datos)}")


if __name__ == "__main__":
    main()
