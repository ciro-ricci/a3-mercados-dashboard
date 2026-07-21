#!/usr/bin/env python3
"""
Actualiza BCRA_TC_OVERRIDE en index.html con el Tipo de Cambio Mayorista
de Referencia (Comunicación A 3500) publicado por el BCRA, vía su API
pública oficial (api.bcra.gob.ar), sin necesidad de ninguna API key.

idVariable=5 en la API de Estadísticas Monetarias v4.0 corresponde a
"Tipo de cambio mayorista de referencia" (el valor de la Com. A 3500).
"""
import re
import sys
import urllib3
import requests
from datetime import datetime, timezone

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

INDEX_PATH = "index.html"
BCRA_URL = "https://api.bcra.gob.ar/estadisticas/v4.0/monetarias/5?limit=1&offset=0"


def fetch_a3500():
    # La API del BCRA suele tener un certificado con cadena incompleta;
    # verify=False es el workaround estándar usado para este dominio.
    resp = requests.get(BCRA_URL, verify=False, timeout=20)
    resp.raise_for_status()
    data = resp.json()
    detalle = data["results"][0]["detalle"]
    if not detalle:
        raise RuntimeError("La API del BCRA no devolvió datos.")
    ultimo = detalle[0]
    return float(ultimo["valor"]), ultimo["fecha"]


def main():
    valor, fecha = fetch_a3500()
    print(f"BCRA A3500 (mayorista referencia): {valor} - fecha publicada: {fecha}")

    with open(INDEX_PATH, "r", encoding="utf-8") as f:
        html_text = f.read()

    match = re.search(r"const BCRA_TC_OVERRIDE = [\d.]+;", html_text)
    if not match:
        print("[ERROR] No se encontró BCRA_TC_OVERRIDE en index.html", file=sys.stderr)
        sys.exit(1)

    old_line = match.group(0)
    new_line = f"const BCRA_TC_OVERRIDE = {valor};"

    if old_line == new_line:
        print("Sin cambios en BCRA_TC_OVERRIDE.")
        return False

    html_text = html_text.replace(old_line, new_line, 1)

    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    html_text = re.sub(
        r"(BCRA_TC_OVERRIDE.*?Última actualización: )\d{4}-\d{2}-\d{2}",
        rf"\g<1>{today}",
        html_text,
        count=1,
        flags=re.S,
    )

    with open(INDEX_PATH, "w", encoding="utf-8") as f:
        f.write(html_text)

    print(f"index.html actualizado: BCRA_TC_OVERRIDE {old_line} -> {new_line}")
    return True


if __name__ == "__main__":
    changed = main()
    raise SystemExit(0)
