#!/usr/bin/env python3
"""
Leer y publicar archivos de los repos del dashboard, sin pasar por el navegador.

Por que existe:
  - index.html paso 1 MB, y arriba de ese tamano la API de "contents" de GitHub
    devuelve el contenido VACIO sin dar error. Hay que usar la API de objetos de
    Git, que no tiene ese tope.
  - La credencial se lee del archivo en el momento de usarla y viaja en un header.
    Nunca se pega en el JavaScript de una pagina ni aparece en la linea de comando.

Uso:
  python3 publicar.py leer     <repo> <ruta> <destino>
  python3 publicar.py publicar <repo> <ruta> <origen> "<mensaje de commit>"

<repo> es "ciro-ricci/a3-mercados-dashboard" o "datamiazzo-2026/data-miazzo-web".
La credencial correcta se elige sola segun el duenio del repo.
"""
import base64, glob, json, os, sys, urllib.request, urllib.error

API = "https://api.github.com"

# Cada credencial fine-grained pertenece a UN solo duenio: por eso son dos.
CREDENCIAL = {
    "ciro-ricci":      "gh_token.txt",
    "datamiazzo-2026": "gh_token_datamiazzo.txt",
}


def carpeta_credenciales():
    for patron in ("/sessions/*/mnt/Claude-tokens", "/mnt/Claude-tokens"):
        encontrados = glob.glob(patron)
        if encontrados:
            return encontrados[0]
    sys.exit("No encuentro la carpeta Claude-tokens montada. "
             "Hay que conectarla con request_cowork_directory antes de correr esto.")


def token(repo):
    duenio = repo.split("/")[0]
    nombre = CREDENCIAL.get(duenio)
    if not nombre:
        sys.exit(f"No se que credencial usar para el duenio '{duenio}'.")
    ruta = os.path.join(carpeta_credenciales(), nombre)
    if not os.path.exists(ruta):
        sys.exit(f"Falta el archivo de credencial {nombre} en la carpeta conectada.")
    with open(ruta) as f:
        return f.read().strip()


def pedir(url, tok, metodo="GET", cuerpo=None, accept="application/vnd.github+json"):
    datos = json.dumps(cuerpo).encode() if cuerpo is not None else None
    req = urllib.request.Request(url, data=datos, method=metodo)
    req.add_header("Authorization", "Bearer " + tok)
    req.add_header("Accept", accept)
    if datos:
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            crudo = r.read()
    except urllib.error.HTTPError as e:
        detalle = e.read().decode("utf-8", "replace")[:400]
        sys.exit(f"GitHub respondio {e.code} en {metodo} {url.split(API)[-1]}\n{detalle}")
    return crudo if accept.endswith("raw") else json.loads(crudo)


def leer(repo, ruta, destino):
    tok = token(repo)
    meta = pedir(f"{API}/repos/{repo}/contents/{ruta}?ref=main", tok)
    crudo = pedir(f"{API}/repos/{repo}/git/blobs/{meta['sha']}", tok,
                  accept="application/vnd.github.raw")
    # Guarda de tamanio: si el archivo llega vacio o cortado, mejor fallar aca
    # que publicar un index.html mutilado.
    if len(crudo) < 1000 or len(crudo) < meta.get("size", 0) * 0.9:
        sys.exit(f"El archivo llego incompleto: {len(crudo)} bytes contra "
                 f"{meta.get('size')} esperados. No sigas.")
    with open(destino, "wb") as f:
        f.write(crudo)
    print(f"leido {ruta} de {repo}: {len(crudo)} bytes -> {destino}")


def publicar(repo, ruta, origen, mensaje):
    tok = token(repo)
    with open(origen, "rb") as f:
        contenido = f.read()
    if len(contenido) < 1000:
        sys.exit(f"El archivo a publicar tiene {len(contenido)} bytes. Parece un error: no publico.")

    ref = pedir(f"{API}/repos/{repo}/git/ref/heads/main", tok)
    padre = ref["object"]["sha"]
    commit_padre = pedir(f"{API}/repos/{repo}/git/commits/{padre}", tok)

    blob = pedir(f"{API}/repos/{repo}/git/blobs", tok, "POST",
                 {"content": base64.b64encode(contenido).decode(), "encoding": "base64"})
    arbol = pedir(f"{API}/repos/{repo}/git/trees", tok, "POST",
                  {"base_tree": commit_padre["tree"]["sha"],
                   "tree": [{"path": ruta, "mode": "100644", "type": "blob", "sha": blob["sha"]}]})
    nuevo = pedir(f"{API}/repos/{repo}/git/commits", tok, "POST",
                  {"message": mensaje, "tree": arbol["sha"], "parents": [padre]})
    movida = pedir(f"{API}/repos/{repo}/git/refs/heads/main", tok, "PATCH",
                   {"sha": nuevo["sha"]})
    print(f"publicado en {repo}: {movida['object']['sha'][:8]} ({len(contenido)} bytes)")


if __name__ == "__main__":
    if len(sys.argv) < 5:
        sys.exit(__doc__)
    accion = sys.argv[1]
    if accion == "leer":
        leer(sys.argv[2], sys.argv[3], sys.argv[4])
    elif accion == "publicar":
        publicar(sys.argv[2], sys.argv[3], sys.argv[4],
                 sys.argv[5] if len(sys.argv) > 5 else "Actualizacion automatica")
    else:
        sys.exit(__doc__)
