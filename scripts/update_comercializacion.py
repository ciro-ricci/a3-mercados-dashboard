#!/usr/bin/env python3
"""
Actualiza COMERCIALIZACION_DATA en index.html a partir de la tabla oficial
"Compras y DJVE de Granos" de la Secretaria de Bioeconomia (MAGyP).
Fuente: pagina HTML publica, sin login ni API key.
Se actualiza semanalmente en origen (la propia pagina lo indica como "Semanal").

Solo procesamos Trigo, Maiz y Soja (se descartan Sorgo, Cebada y Girasol).
"""
import re
import requests
from bs4 import BeautifulSoup

URL = "https://www.magyp.gob.ar/sitio/areas/ss_mercados_agropecuarios/areas/granos/_archivos/000058_Estad%C3%ADsticas/000020_Compras%20y%20DJVE%20de%20Granos.php"

# nombre visible del tab -> clave que usamos en el JSON
CULTIVOS = {
    "Trigo": "trigo",
    "Maíz": "maiz",
    "Soja": "soja",
}

COLS = ["comprado", "precio_hecho", "a_fijar", "fijado", "saldo_a_fijar", "djve_acumulado"]

TARGET_FILES = ["index.html"]


def to_float(s):
    s = s.strip().replace(".", "").replace(",", ".")
    s = re.sub(r"[^0-9.\-]", "", s)
    if s in ("", "-"):
        return 0.0
    try:
        return float(s)
    except ValueError:
        return 0.0


def fetch_html():
    resp = requests.get(URL, headers={"User-Agent": "Mozilla/5.0"}, timeout=30)
    resp.raise_for_status()
    return resp.text


def parse_fecha(html_text):
    m = re.search(r"Compras y DJVE AL (\d{2}/\d{2}/\d{4})", html_text)
    if not m:
        raise RuntimeError("No se encontro la fecha 'Compras y DJVE AL' en la pagina")
    d, mth, y = m.group(1).split("/")
    return f"{y}-{mth}-{d}"


def parse_cultivo_table(table):
    """Devuelve (campaña_actual, actual{}, comparacion_anio_anterior{}) leyendo
    la seccion 'Total' de la tabla (Compras Exportador + Industria combinadas)."""
    rows = [
        [c.get_text(" ", strip=True) for c in r.find_all(["td", "th"])]
        for r in table.find_all("tr")
    ]
    total_idx = None
    for i, r in enumerate(rows):
        if r and r[0] == "Total":
            total_idx = i
            break
    if total_idx is None:
        raise RuntimeError("No se encontro la fila 'Total' en la tabla")

    # fila_actual: ['Total', 'Cosecha', 'Semanal', 'Comprado', 'PrecioHecho',
    #               'AFijar', 'Fijado', 'SaldoAFijar', 'DJVE']  (9 celdas)
    # fila_comparacion: ['(Semanal)', '(Comprado)', '(PrecioHecho)', '(AFijar)',
    #                    '(Fijado)', '(SaldoAFijar)', '(DJVE)']  (7 celdas, sin Cosecha)
    fila_actual = rows[total_idx]
    fila_comparacion = rows[total_idx + 1]

    campaña = fila_actual[1]
    valores_actual = fila_actual[3:9]   # salta Cosecha y Semanal
    valores_comp = fila_comparacion[1:7]  # salta Semanal

    actual = {COLS[i]: to_float(valores_actual[i]) for i in range(6)}
    comparacion = {COLS[i]: to_float(valores_comp[i]) for i in range(6)}
    return campaña, actual, comparacion


def fetch_comercializacion():
    html_text = fetch_html()
    fecha = parse_fecha(html_text)
    soup = BeautifulSoup(html_text, "lxml")

    tabs = [t.get_text(strip=True) for t in soup.find_all("li", class_="TabbedPanelsTab")]
    contents = soup.find_all("div", class_="TabbedPanelsContent")
    if len(tabs) != len(contents):
        raise RuntimeError(f"Tabs ({len(tabs)}) y paneles ({len(contents)}) no coinciden")

    data = {"fecha": fecha, "cultivos": {}}
    for nombre_tab, clave in CULTIVOS.items():
        try:
            idx = tabs.index(nombre_tab)
        except ValueError:
            print(f"[WARN] No se encontro el tab '{nombre_tab}'")
            continue
        table = contents[idx].find("table", class_="tabla")
        if table is None:
            print(f"[WARN] No se encontro tabla para '{nombre_tab}'")
            continue
        campaña, actual, comparacion = parse_cultivo_table(table)
        data["cultivos"][clave] = {
            "campaña": campaña,
            "actual": actual,
            "comparacion_anio_anterior": comparacion,
        }
    return data


def render_js_object(data):
    def fmt_num(n):
        return f"{n:g}"

    def fmt_dict(d):
        parts = [f"{k}:{fmt_num(v)}" for k, v in d.items()]
        return "{" + ",".join(parts) + "}"

    cultivos_js = []
    for clave, c in data["cultivos"].items():
        cultivos_js.append(
            f"    {clave}:{{campaña:'{c['campaña']}',"
            f"actual:{fmt_dict(c['actual'])},"
            f"comparacion_anio_anterior:{fmt_dict(c['comparacion_anio_anterior'])}}}"
        )
    body = ",\n".join(cultivos_js)
    return (
        "const COMERCIALIZACION_DATA = {\n"
        f"  fecha:'{data['fecha']}',\n"
        "  cultivos:{\n"
        f"{body}\n"
        "  }\n"
        "};"
    )


def update_file(path, new_js_text, new_fecha):
    with open(path, "r", encoding="utf-8") as f:
        html_text = f.read()

    pattern = re.compile(r"const COMERCIALIZACION_DATA = \{.*?\};", re.S)
    m = pattern.search(html_text)

    if m:
        old_block = m.group(0)
        old_fecha_match = re.search(r"fecha:'(\d{4}-\d{2}-\d{2})'", old_block)
        if old_fecha_match and old_fecha_match.group(1) == new_fecha:
            print(f"[{path}] Sin cambios (misma fecha de origen: {new_fecha}).")
            return False
        new_html = html_text[: m.start()] + new_js_text + html_text[m.end() :]
    else:
        # Primera vez: insertamos el bloque justo antes de la linea de NEWS_DATA,
        # o si no existe, antes de </body>.
        marker = "const NEWS_DATA"
        idx = html_text.find(marker)
        if idx == -1:
            idx = html_text.rfind("</body>")
            insertion = new_js_text + "\n"
        else:
            line_start = html_text.rfind("\n", 0, idx) + 1
            idx = line_start
            insertion = new_js_text + "\n\n"
        new_html = html_text[:idx] + insertion + html_text[idx:]

    with open(path, "w", encoding="utf-8") as f:
        f.write(new_html)
    print(f"[{path}] actualizado con datos al {new_fecha}.")
    return True


def main():
    data = fetch_comercializacion()
    if not data["cultivos"]:
        raise RuntimeError("No se pudo extraer ningun cultivo (trigo/maiz/soja)")

    js_text = render_js_object(data)

    any_changed = False
    for path in TARGET_FILES:
        changed = update_file(path, js_text, data["fecha"])
        any_changed = any_changed or changed

    return any_changed


if __name__ == "__main__":
    main()
    raise SystemExit(0)
