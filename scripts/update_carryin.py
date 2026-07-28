#!/usr/bin/env python3
"""
Actualiza CARRYIN_DATA en index.html: Produccion, Carry In (stock inicial) y
Oferta total de la campania, para Trigo, Maiz y Soja.

Fuente: Monitor del Comercio Granario (MAGyP / SIO Granos), una app
GeneXus que arma estos valores via ajax al cambiar el selector "Producto"
y el selector "Cosecha" (Actual / Proxima).

IMPORTANTE: el monitor tiene DOS campanias visibles por cultivo:
- "Actual" (radio #vSCOSECHA1): la campania YA cosechada/en curso, con datos
  reales de produccion (ej. hoy equivale a la campania 25/26).
- "Proxima" (radio #vSCOSECHA2): la campania que recien arranca, sin
  estimaciones oficiales todavia (suele estar en 0 hasta que avanza la
  siembra, ej. hoy equivale a la campania 26/27).
Guardamos AMBAS, indexadas por la etiqueta de campania que la propia pagina
muestra (texto de #TXTCOSECHA, ej. "25/26", "26/27"), para poder cruzarlas
correctamente con la campania de venta correspondiente en COMERCIALIZACION_DATA
(que usa la misma nomenclatura de campania).

No requiere login ni token. Pensado para correr con poca frecuencia (mensual):
estos valores son de campania y casi no cambian semana a semana.
"""
import re
from playwright.sync_api import sync_playwright

URL = "https://monitorssma.magyp.gob.ar/siogranos.dashboardgranos.aspx"

# valor del <option> del selector #vSIIA_GRANOID -> clave que usamos en el JSON
CULTIVOS = {
    "1": "trigo",
    "2": "maiz",
    "18": "soja",
}

# id del radio de Cosecha -> no se usa como clave final (usamos la etiqueta
# real que muestra la pagina), solo para saber que radio clickear.
COSECHAS = ["vSCOSECHA1", "vSCOSECHA2"]  # 1=Actual, 2=Proxima

TARGET_FILES = ["index.html"]


def to_float(txt):
    """'49,50 MT' -> 49.50 (en millones de toneladas)"""
    txt = txt.replace("MT", "").strip()
    txt = txt.replace(".", "").replace(",", ".")
    txt = re.sub(r"[^0-9.\-]", "", txt)
    return float(txt) if txt not in ("", "-") else 0.0


def leer_valores(page):
    produccion = page.text_content("#span_vVALUECARD4") or ""
    carry_in = page.text_content("#span_vCARRYIN") or ""
    oferta = page.text_content("#span_vOFERTA") or ""
    campaña = (page.text_content("#TXTCOSECHA") or "").strip()
    return campaña, {
        "produccion": to_float(produccion),
        "carry_in": to_float(carry_in),
        "oferta": to_float(oferta),
    }


def esperar_cambio(page, valor_anterior):
    try:
        page.wait_for_function(
            """(prev) => {
                const el = document.querySelector('#span_vVALUECARD4');
                return el && el.textContent.trim() !== prev.trim();
            }""",
            arg=valor_anterior,
            timeout=15000,
        )
    except Exception:
        # Puede pasar que el valor nuevo coincida con el anterior (ej. dos
        # cultivos con el mismo numero por casualidad, o cosecha "Proxima"
        # en 0 dos veces seguidas); seguimos igual, ya vamos a leer el DOM.
        page.wait_for_timeout(3000)
    page.wait_for_load_state("networkidle", timeout=15000)


def fetch_carryin():
    resultados = {}
    fecha_hoy = None

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        page.goto(URL, wait_until="networkidle", timeout=60000)
        page.wait_for_selector("#span_vVALUECARD4", timeout=30000)

        try:
            fecha_hoy = page.text_content("#span_vTODAY") or None
        except Exception:
            fecha_hoy = None

        for valor_option, clave in CULTIVOS.items():
            valor_anterior = page.text_content("#span_vVALUECARD4")
            page.select_option("#vSIIA_GRANOID", value=valor_option)
            esperar_cambio(page, valor_anterior)

            resultados[clave] = {}
            for cosecha_id in COSECHAS:
                valor_anterior = page.text_content("#span_vVALUECARD4")
                page.click(f"#{cosecha_id}")
                esperar_cambio(page, valor_anterior)

                campaña, valores = leer_valores(page)
                if not campaña:
                    print(f"[WARN] No se pudo leer la etiqueta de campania para {clave}/{cosecha_id}")
                    continue
                resultados[clave][campaña] = valores

        browser.close()

    return fecha_hoy, resultados


def render_js_object(fecha_hoy, resultados):
    partes = []
    for clave, campañas in resultados.items():
        campañas_js = ",".join(
            f"'{camp}':{{produccion:{v['produccion']:g},"
            f"carry_in:{v['carry_in']:g},oferta:{v['oferta']:g}}}"
            for camp, v in campañas.items()
        )
        partes.append(f"    {clave}:{{{campañas_js}}}")
    body = ",\n".join(partes)
    fecha_js = fecha_hoy.strip() if fecha_hoy else ""
    return (
        "const CARRYIN_DATA = {\n"
        f"  fecha_consulta:'{fecha_js}',\n"
        "  cultivos:{\n"
        f"{body}\n"
        "  }\n"
        "};"
    )


def update_file(path, new_js_text):
    with open(path, "r", encoding="utf-8") as f:
        html_text = f.read()

    pattern = re.compile(r"const CARRYIN_DATA = \{.*?\};", re.S)
    m = pattern.search(html_text)

    if m:
        if m.group(0).strip() == new_js_text.strip():
            print(f"[{path}] Sin cambios en CARRYIN_DATA.")
            return False
        new_html = html_text[: m.start()] + new_js_text + html_text[m.end() :]
    else:
        marker = "const COMERCIALIZACION_DATA"
        idx = html_text.find(marker)
        if idx == -1:
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
    print(f"[{path}] CARRYIN_DATA actualizado.")
    return True


def main():
    fecha_hoy, resultados = fetch_carryin()
    if not resultados or not any(resultados.values()):
        raise RuntimeError("No se pudo extraer ningun cultivo (trigo/maiz/soja)")

    js_text = render_js_object(fecha_hoy, resultados)

    any_changed = False
    for path in TARGET_FILES:
        changed = update_file(path, js_text)
        any_changed = any_changed or changed

    return any_changed


if __name__ == "__main__":
    main()
    raise SystemExit(0)
