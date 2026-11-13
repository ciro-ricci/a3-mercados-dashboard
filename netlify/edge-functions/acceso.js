// Acceso único DATA Miazzo: protege el dashboard de este sitio.
// Sin sesión válida, el dashboard no se entrega: se manda a la persona a
// datamiazzo.com.ar/acceso, que la reconoce (o le pide el mail) y la devuelve
// con un ticket firmado. Quién tiene acceso a qué se define en la planilla
// "Base de clientes · DATA Miazzo" (Drive de datamiazzo@gmail.com).
import {
  CENTRAL, SITIOS, HORAS_SES, DIAS_ID, ahora, firmar, leerPase, leerCookie,
  cookie, redirigir, volverSeguro,
} from '../acceso-lib.js';

const SITIO = 'grano';
const DASH = SITIOS[SITIO].dash;
const SES = 'dm_ses';   // sesión de este sitio (12 h)
const CLI = 'dm_cli';   // marca "esta persona ya entró alguna vez" (60 días)

function irAlAcceso(volver) {
  return redirigir(`${CENTRAL}/acceso/ir?sitio=${SITIO}&volver=${encodeURIComponent(volver)}`);
}

export default async (req, context) => {
  const url = new URL(req.url);
  const p = url.pathname;

  // Vuelta desde datamiazzo.com.ar con el ticket firmado.
  if (p === '/acceso/entrar') {
    const t = await leerPase(url.searchParams.get('t'), 'ticket');
    if (!t || t.s !== SITIO) return redirigir(`${CENTRAL}/acceso/?sitio=${SITIO}&error=ticket`);
    const ses = await firmar({ t: 'ses', s: SITIO, e: t.e, x: ahora() + HORAS_SES * 3600 });
    return redirigir(volverSeguro(url.searchParams.get('volver'), DASH), [
      cookie(SES, ses, HORAS_SES * 3600),
      cookie(CLI, '1', DIAS_ID * 86400, { httpOnly: false }),
    ]);
  }
  // Botón "Ingresar" de la landing.
  if (p === '/acceso/ingresar') return irAlAcceso(volverSeguro(url.searchParams.get('volver'), DASH));
  // Cerrar sesión. Desde el botón "Salir": va a la central, que cierra la suya y
  // recorre los tres sitios (?cadena=...) para cerrar la de cada uno.
  if (p === '/acceso/salir') {
    const borrar = [cookie(SES, '', 0), cookie(CLI, '', 0, { httpOnly: false })];
    const cadena = url.searchParams.get('cadena');
    if (cadena === null) return redirigir(`${CENTRAL}/acceso/salir`, borrar);
    const [sig, ...resto] = cadena.split(',').filter((k) => SITIOS[k] && k !== SITIO);
    if (sig) return redirigir(`${SITIOS[sig].url}/acceso/salir?cadena=${resto.join(',')}`, borrar);
    return redirigir(`${CENTRAL}/acceso/?salio=1`, borrar);
  }
  if (p.startsWith('/acceso/')) return context.next();

  // Dashboard: con sesión válida se entrega tal cual, sin caché compartida.
  const ses = await leerPase(leerCookie(req, SES), 'ses');
  if (ses && ses.s === SITIO) {
    const r = await context.next();
    const h = new Headers(r.headers);
    h.set('cache-control', 'private, no-store');
    if (esTablero(p) && (h.get('content-type') || '').includes('text/html')) {
      h.delete('content-length');
      const html = (await r.text()).replace(/<head[^>]*>/i, (m) => m + puenteIdentity(ses.e));
      return new Response(html, { status: r.status, statusText: r.statusText, headers: h });
    }
    return new Response(r.body, { status: r.status, statusText: r.statusText, headers: h });
  }

  const volver = p + url.search;
  if (SITIO !== 'grano' || esTablero(p)) return irAlAcceso(volver);

  // Al Grano: el dashboard vive en la raíz. Quien ya entró alguna vez va directo
  // (si su acceso central sigue vigente, ni lo nota); el resto ve la landing.
  if (leerCookie(req, CLI)) return irAlAcceso('/');
  return landingGrano(context);
};

// ---------- Tableros de clientes (estrategiasalgrano.com/tablero) ----------
// Se programaron con Netlify Identity. En vez de reescribirlos, se les entrega
// un "puente" con la misma forma que el widget de Identity, que devuelve la
// persona de la sesión única. Sus funciones (tablero, tablero-fin) leen la
// cookie dm_ses para saber quién es (netlify/acceso-node.mjs).
function esTablero(p) {
  return p === '/tablero' || p.startsWith('/tablero/');
}

function puenteIdentity(email) {
  const e = JSON.stringify(String(email)).replace(/</g, '\\u003c');
  return `<script>
(function(){
  var u={email:${e},user_metadata:{},app_metadata:{roles:[]},jwt:function(){return Promise.resolve('acceso-dm');}};
  window.netlifyIdentity={
    currentUser:function(){return u;},
    on:function(ev,cb){if(ev==='init'){setTimeout(function(){cb(u);},0);}},
    off:function(){},
    open:function(){location.href='/acceso/ingresar?volver='+encodeURIComponent(location.pathname+location.search);},
    close:function(){},
    init:function(){},
    refresh:function(){return Promise.resolve('acceso-dm');},
    logout:function(){location.href='/acceso/salir';return new Promise(function(){});}
  };
})();
</script>`;
}

// ---------- Landing pública de Al Grano ----------
// Se arma con la plantilla que ya vive dentro de index.html (gate.innerHTML)
// y con window.LANDING_LINKS, que actualiza la tarea de links de informes.
// Así hay una sola fuente y el dashboard no se entrega a quien no tiene acceso.
let cacheLanding = { t: 0, html: '' };

async function landingGrano(context) {
  if (cacheLanding.html && Date.now() - cacheLanding.t < 5 * 60 * 1000) return paginaLanding(cacheLanding.html);
  let html = '';
  try {
    const r = await context.next();
    html = armarLanding(await r.text());
  } catch (e) {
    html = '';
  }
  if (!html) {
    html = `<!DOCTYPE html><html lang="es"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Estrategias al Grano · DATA Miazzo</title></head><body style="font-family:sans-serif;padding:40px;text-align:center">
<h1>Estrategias al Grano</h1><p><a href="/acceso/ingresar">Ingresar al dashboard</a></p></body></html>`;
    return paginaLanding(html);
  }
  cacheLanding = { t: Date.now(), html };
  return paginaLanding(html);
}

function paginaLanding(html) {
  return new Response(html, {
    status: 200,
    headers: { 'content-type': 'text/html; charset=utf-8', 'cache-control': 'private, max-age=0, must-revalidate' },
  });
}

function armarLanding(g) {
  const m = g.match(/window\.LANDING_LINKS\s*=\s*\{([\s\S]*?)\};/);
  if (!m) return '';
  const L = {};
  for (const x of m[1].matchAll(/(\w+):\s*'([^']*)'/g)) L[x[1]] = x[2];
  const marca = 'gate.innerHTML=`';
  const a = g.indexOf(marca);
  if (a < 0) return '';
  const b = g.indexOf('`;', a + marca.length);
  if (b < 0) return '';
  let land = g.slice(a + marca.length, b);
  land = land.replace(/\$\{L\.(\w+)\|\|'#'\}/g, (_, k) => L[k] || '#');
  land = land.split('#landing-gate .lg').join('.lg');
  if (land.includes('${')) return '';
  const desc = 'Datos de mercado de granos para decidir cuándo y cómo vender: futuros, opciones, estrategias y un informe periódico. Un servicio de DATA Miazzo.';
  return `<!DOCTYPE html>
<html lang="es-AR"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Estrategias al Grano · DATA Miazzo</title>
<meta name="description" content="${desc}">
<meta property="og:title" content="Estrategias al Grano · DATA Miazzo">
<meta property="og:description" content="${desc}">
<meta property="og:type" content="website">
<meta property="og:url" content="https://estrategiasalgrano.com/">
<link rel="icon" type="image/svg+xml" href="/favicon.svg">
<link rel="icon" type="image/png" sizes="32x32" href="/favicon-32x32.png">
<link rel="apple-touch-icon" sizes="180x180" href="/apple-touch-icon.png">
<link rel="preconnect" href="https://fonts.googleapis.com"><link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Rajdhani:wght@500;600;700&family=IBM+Plex+Sans:wght@400;500;600&display=swap">
<link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.4.2/css/all.min.css">
<link rel="stylesheet" href="/data-miazzo.css">
</head>
<body style="margin:0;background:#F7F1EA">${land}</body></html>`;
}

export const config = { path: ["/", "/index.html", "/acceso/*", "/tablero", "/tablero/*"] };
