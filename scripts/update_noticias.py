#!/usr/bin/env python3
"""
Actualiza NEWS_DATA en index.html (la pestaña "Noticias" real del dashboard)
y en noticias.html (archivo standalone), a partir de feeds RSS públicos
(Infocampo, Bichos de Campo), sin usar ningún modelo de IA ni API key.

- Filtra entradas de las últimas 48hs que mencionen trigo, maíz o soja.
- Publica título + resumen (tal cual vienen del feed) + link + fuente.
- Fusiona con las noticias existentes de CADA archivo (dedup por link,
  cada uno mantiene su propio historial) y purga >10 días.
"""
import re
import html
import feedparser
from datetime import datetime, timedelta, timezone

TARGET_FILES = ["index.html", "noticias.html"]
MAX_AGE_DAYS = 10
WINDOW_HOURS = 48

FEEDS = [
    {"url": "https://www.infocampo.com.ar/feed/", "fuente": "Infocampo"},
    {"url": "https://bichosdecampo.com/feed/", "fuente": "Bichos de Campo"},
]

KEYWORDS = {
    "Trigo": [r"\btrigo\b"],
    "Maíz": [r"\bma[ií]z\b"],
    "Soja": [r"\bsoja\b"],
}

ITEM_RE = re.compile(
    r"\{fecha:'(?P<fecha>[^']*)',fuente:'(?P<fuente>(?:[^'\\]|\\.)*)',"
    r"cultivo:'(?P<cultivo>(?:[^'\\]|\\.)*)',titulo:'(?P<titulo>(?:[^'\\]|\\.)*)',"
    r"resumen:'(?P<resumen>(?:[^'\\]|\\.)*)',link:'(?P<link>(?:[^'\\]|\\.)*)'\}"
)

FECHA_RE = re.compile(r"Última actualización: \d{4}-\d{2}-\d{2}")


def strip_html(text):
    text = html.unescape(text or "")
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def detect_cultivos(text):
    low = text.lower()
    hits = [c for c, pats in KEYWORDS.items() if any(re.search(p, low) for p in pats)]
    return hits


def js_escape(s):
    return (s or "").replace("\\", "\\\\").replace("'", "\\'")


def fetch_new_items():
    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(hours=WINDOW_HOURS)
    items = []
    for feed in FEEDS:
        try:
            parsed = feedparser.parse(feed["url"])
        except Exception as e:
            print(f"[WARN] No se pudo leer {feed['fuente']}: {e}")
            continue
        for entry in parsed.entries:
            pub = entry.get("published_parsed") or entry.get("updated_parsed")
            if not pub:
                continue
            pub_dt = datetime(*pub[:6], tzinfo=timezone.utc)
            if pub_dt < cutoff:
                continue
            titulo = strip_html(entry.get("title", ""))
            resumen_raw = strip_html(entry.get("summary", "") or entry.get("description", ""))
            texto_busqueda = f"{titulo} {resumen_raw}"
            cultivos = detect_cultivos(texto_busqueda)
            if not cultivos:
                continue
            resumen = resumen_raw[:400].rstrip()
            items.append({
                "fecha": pub_dt.strftime("%Y-%m-%d"),
                "fuente": feed["fuente"],
                "cultivo": " / ".join(cultivos),
                "titulo": titulo,
                "resumen": resumen,
                "link": entry.get("link", ""),
            })
    return items


def load_existing(html_text):
    match = re.search(r"const NEWS_DATA = \[(?P<body>.*?)\];", html_text, re.S)
    if not match:
        raise RuntimeError("No se encontró el array NEWS_DATA")
    body = match.group("body")
    existing = []
    for m in ITEM_RE.finditer(body):
        d = m.groupdict()
        for k in d:
            d[k] = d[k].replace("\\'", "'").replace('\\\\', '\\')
        existing.append(d)
    return existing, match.span()


def merge(existing, new_items):
    by_link = {it["link"]: it for it in existing if it.get("link")}
    for it in new_items:
        by_link[it["link"]] = it  # nuevo pisa viejo si el link se repite

    cutoff_date = (datetime.now(timezone.utc) - timedelta(days=MAX_AGE_DAYS)).strftime("%Y-%m-%d")
    merged = [it for it in by_link.values() if it["fecha"] >= cutoff_date]
    merged.sort(key=lambda x: x["fecha"], reverse=True)
    return merged


def render_array(items):
    parts = []
    for it in items:
        parts.append(
            "  {fecha:'%s',fuente:'%s',cultivo:'%s',titulo:'%s',resumen:'%s',link:'%s'}"
            % (
                js_escape(it["fecha"]),
                js_escape(it["fuente"]),
                js_escape(it["cultivo"]),
                js_escape(it["titulo"]),
                js_escape(it["resumen"]),
                js_escape(it["link"]),
            )
        )
    return "const NEWS_DATA = [\n" + ",\n".join(parts) + "\n];"


def update_date_before(html_text, span_start):
    """Reemplaza SOLO la última fecha 'Última actualización' que aparece
    antes de la posición span_start (evita tocar otras fechas del archivo,
    como la del BCRA_TC_OVERRIDE en index.html)."""
    prefix = html_text[:span_start]
    last_match = None
    for m in FECHA_RE.finditer(prefix):
        last_match = m
    if not last_match:
        return html_text
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    new_text = f"Última actualización: {today}"
    return html_text[:last_match.start()] + new_text + html_text[last_match.end():]


def process_file(path, new_items):
    with open(path, "r", encoding="utf-8") as f:
        html_text = f.read()

    existing, span = load_existing(html_text)
    merged = merge(existing, new_items)

    before_count = len(existing)
    if len(merged) == before_count and not new_items:
        print(f"[{path}] Sin cambios, no se reescribe.")
        return False

    new_array_text = render_array(merged)
    new_html = html_text[:span[0]] + new_array_text + html_text[span[1]:]
    new_html = update_date_before(new_html, span[0])

    with open(path, "w", encoding="utf-8") as f:
        f.write(new_html)

    print(f"[{path}] actualizado: {before_count} -> {len(merged)} noticias.")
    return True


def main():
    new_items = fetch_new_items()
    print(f"Noticias nuevas encontradas (últimas {WINDOW_HOURS}hs, con keyword): {len(new_items)}")

    any_changed = False
    for path in TARGET_FILES:
        try:
            changed = process_file(path, new_items)
            any_changed = any_changed or changed
        except RuntimeError as e:
            print(f"[ERROR] {path}: {e}")

    return any_changed


if __name__ == "__main__":
    changed = main()
    raise SystemExit(0)
