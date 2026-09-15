// Precios en vivo para estrategiasalgrano.com
//
// Corre en el servidor porque ni A3 ni Yahoo autorizan que el navegador les
// pegue directo (CORS). Solo se ejecuta cuando alguien aprieta el boton de
// actualizar: no hay nada consultando en loop.
//
// A3 publica un canal SSE abierto con las posiciones liquidas de soja, maiz y
// trigo, en tiempo real. Chicago sale de Yahoo, que va 10 minutos atras del
// mercado; devolvemos la hora del dato para poder decirlo en pantalla.

const A3_SSE = 'https://a3mercados.com.ar/api/market-data/stream?streams=Dashboard';
const YAHOO = 'https://query1.finance.yahoo.com/v8/finance/chart/';

// bushels y libras a toneladas metricas
const FACTOR = {
  ZC: v => v / 100 * 39.36825,
  ZW: v => v / 100 * 36.7437,
  ZS: v => v / 100 * 36.7437,
  ZL: v => v / 100 * 2204.62262,
  ZM: v => v * 1.1023113
};

const MES = {ENE:'F', FEB:'G', MAR:'H', ABR:'J', MAY:'K', JUN:'M',
             JUL:'N', AGO:'Q', SEP:'U', OCT:'V', NOV:'X', DIC:'Z'};
const ORDEN = ['F','G','H','J','K','M','N','Q','U','V','X','Z'];

// Chicago no lista todos los meses. Si el vencimiento de A3 no existe alla,
// tomamos el primero que si cotiza despues de esa fecha, y lo decimos.
const LISTADOS = {
  ZC: ['H','K','N','U','Z'],
  ZW: ['H','K','N','U','Z'],
  ZS: ['F','H','K','N','Q','U','X'],
  ZL: ['F','H','K','N','Q','U','V','Z'],
  ZM: ['F','H','K','N','Q','U','V','Z']
};

const GRANO = {SOJ: {ch: 'ZS', nombre: 'Soja'},
               MAI: {ch: 'ZC', nombre: 'Maiz'},
               TRI: {ch: 'ZW', nombre: 'Trigo'}};

function equivalente(raiz, mes3, anio2) {
  const base = MES[mes3];
  if (!base) return null;
  const listados = LISTADOS[raiz];
  const i = ORDEN.indexOf(base), anio = parseInt(anio2, 10);
  for (let salto = 0; salto < 24; salto++) {
    const c = ORDEN[(i + salto) % 12];
    const a = anio + Math.floor((i + salto) / 12);
    if (listados.indexOf(c) >= 0) {
      return {simbolo: raiz + c + String(a).padStart(2, '0') + '.CBT', exacto: salto === 0};
    }
  }
  return null;
}

async function leerA3() {
  const r = await fetch(A3_SSE, {signal: AbortSignal.timeout(9000),
                                 headers: {Accept: 'text/event-stream'}});
  if (!r.ok) throw new Error('A3 respondio ' + r.status);
  const lector = r.body.getReader();
  const dec = new TextDecoder();
  let buffer = '';
  try {
    for (let i = 0; i < 40; i++) {
      const paso = await lector.read();
      if (paso.done) break;
      buffer += dec.decode(paso.value, {stream: true});
      const m = buffer.match(/event: snapshot\ndata: (\{[\s\S]*?)\n/);
      if (m) return JSON.parse(m[1]);
    }
  } finally {
    try { await lector.cancel(); } catch (e) {}
  }
  throw new Error('A3 no mando el snapshot');
}

async function leerChicago(simbolo) {
  const r = await fetch(YAHOO + encodeURIComponent(simbolo) + '?range=1d&interval=1d',
                        {signal: AbortSignal.timeout(8000)});
  if (!r.ok) throw new Error(simbolo + ' respondio ' + r.status);
  const j = await r.json();
  const m = j.chart && j.chart.result && j.chart.result[0] && j.chart.result[0].meta;
  if (!m || m.regularMarketPrice == null) throw new Error(simbolo + ' sin precio');
  return {precio: m.regularMarketPrice, cierreAnterior: m.chartPreviousClose,
          variacion: m.regularMarketChangePercent, minDia: m.regularMarketDayLow,
          maxDia: m.regularMarketDayHigh, min52: m.fiftyTwoWeekLow,
          max52: m.fiftyTwoWeekHigh, volumen: m.regularMarketVolume,
          hora: m.regularMarketTime, descripcion: m.shortName || null};
}

// El canal abierto de A3 solo trae las posiciones mas liquidas. El resto sale
// del resumen diario oficial, que Ciro ya publica como CSV: ahi esta el ajuste
// del cierre anterior de todos los contratos que operaron.
const HOJA_AJUSTES = 'https://docs.google.com/spreadsheets/d/' +
  '1j-ZrWBO-fCkGUPqWtWRsGgGswMRCm2mnMhsPmX6osLI/export?format=csv&gid=527444289';

async function leerAjustes() {
  const r = await fetch(HOJA_AJUSTES, {signal: AbortSignal.timeout(9000)});
  if (!r.ok) throw new Error('la hoja de ajustes respondio ' + r.status);
  const txt = await r.text();
  const salida = {};
  for (const linea of txt.split('\n')) {
    // Contrato,Vencimiento,Producto,...,Ajuste,Volumen,IntAbierto,VarIA,FechaDatos,...
    // Ojo: las opciones empiezan igual que el futuro pero siguen con el strike
    // (SOY.CME/ABR27 438 P). Si no se exige que el ticker sea TODO el primer
    // campo, la prima de la opcion pisa el ajuste del futuro.
    const c = linea.match(/^([A-Z]{3}\.[A-Z]{3}(?:\.P)?\/[A-Z0-9]+),/);
    if (!c) continue;
    if (linea.indexOf(',Opcion,') >= 0 || linea.indexOf(',Opción,') >= 0) continue;
    // el ajuste viene entrecomillado con coma decimal
    const m = linea.match(/,"(-?[\d.]+,\d+)",/);
    if (!m) continue;
    const valor = parseFloat(m[1].replace(/\./g, '').replace(',', '.'));
    const fecha = (linea.match(/,(\d{2}-\d{2}-\d{4}),/) || [])[1] || null;
    if (isFinite(valor)) salida[c[1]] = {ajuste: valor, fecha: fecha};
  }
  return salida;
}

function redondear(v, d) {
  if (v == null || !isFinite(v)) return null;
  const f = Math.pow(10, d == null ? 2 : d);
  return Math.round(v * f) / f;
}

export default async (req) => {
  const salida = {generado: new Date().toISOString(), a3: null, chicago: null, errores: []};

  let snap = null;
  try { snap = await leerA3(); } catch (e) { salida.errores.push('A3: ' + e.message); }

  const posiciones = [];
  if (snap && snap.data && Array.isArray(snap.data.agro)) {
    for (const x of snap.data.agro) {
      const p = String(x.ticker || '').match(/^([A-Z]{3})\.ROS\/([A-Z]{3})(\d{2})$/);
      if (!p) continue;
      const g = GRANO[p[1]];
      if (!g) continue;
      posiciones.push({ticker: x.ticker, grano: g.nombre, raizCh: g.ch, mes: p[2], anio: p[3],
        compra: x.bi || null, venta: x.of || null, ultimo: x.la || null,
        ajusteAnterior: x.pse || null,
        variacion: x.variation == null ? null : redondear(x.variation, 2),
        volumen: x.nv || 0, operaciones: x.tv || 0});
    }
  }

  const pedidos = new Map();
  for (const p of posiciones) {
    const eq = equivalente(p.raizCh, p.mes, p.anio);
    if (eq) { p.chicago = eq.simbolo; p.chicagoExacto = eq.exacto; pedidos.set(eq.simbolo, p.raizCh); }
    if (p.raizCh === 'ZS') {
      for (const sub of ['ZL', 'ZM']) {
        const e2 = equivalente(sub, p.mes, p.anio);
        if (e2) pedidos.set(e2.simbolo, sub);
      }
    }
  }

  const simbolos = Array.from(pedidos.keys());
  const res = await Promise.allSettled(simbolos.map(leerChicago));
  const chicago = {};
  res.forEach((r, i) => {
    const s = simbolos[i], raiz = pedidos.get(s);
    if (r.status !== 'fulfilled') { salida.errores.push('Chicago ' + s + ': ' + r.reason.message); return; }
    const d = r.value, conv = FACTOR[raiz];
    chicago[s] = {simbolo: s, raiz: raiz, descripcion: d.descripcion, nativo: d.precio,
      usdTn: redondear(conv(d.precio), 2),
      cierreAnteriorUsdTn: redondear(conv(d.cierreAnterior), 2),
      variacion: redondear(d.variacion, 2),
      minDiaUsdTn: redondear(conv(d.minDia), 2), maxDiaUsdTn: redondear(conv(d.maxDia), 2),
      min52UsdTn: redondear(conv(d.min52), 2), max52UsdTn: redondear(conv(d.max52), 2),
      volumen: d.volumen, hora: d.hora};
  });

  let ajustes = {};
  try { ajustes = await leerAjustes(); }
  catch (e) { salida.errores.push('Ajustes: ' + e.message); }

  salida.a3 = {posiciones: posiciones, enVivo: true, ajustes: ajustes};
  salida.chicago = {contratos: chicago, demoraMin: 10,
    nota: 'Yahoo Finance, aproximadamente 10 minutos de retraso sobre el CBOT'};

  // Salida para planillas: una fila por posicion, punto y coma como separador y
  // coma decimal, que es como lee Excel en configuracion argentina.
  const url = new URL(req.url);
  if ((url.searchParams.get('formato') || '').toLowerCase() === 'csv') {
    const ahora = new Date(salida.generado).toLocaleString('es-AR',
      {timeZone: 'America/Argentina/Buenos_Aires', hour12: false});
    const num = v => (v == null || !isFinite(v)) ? '' :
      v.toLocaleString('es-AR', {minimumFractionDigits: 2, maximumFractionDigits: 2,
                                 useGrouping: false});

    // Dos fechas distintas, porque son dos cosas distintas: cuando se genero el
    // dato en el mercado y cuando lo fuimos a buscar. Mezclarlas hacia parecer
    // fresco un precio viejo con el mercado cerrado.
    const filas = [['Posicion', 'Precio', 'Estado', 'FechaDato', 'Consultado'].join(';')];
    const yaEsta = new Set();
    const ahoraMs = Date.parse(salida.generado);

    for (const p of posiciones) {
      yaEsta.add(p.ticker);
      const operado = p.ultimo != null && p.ultimo !== 0;
      // A3 no publica la hora de cada operacion, asi que la fecha del dato
      // queda vacia a proposito en vez de inventar la hora de la consulta.
      filas.push([p.ticker, num(operado ? p.ultimo : p.ajusteAnterior),
        operado ? 'A3, ultimo operado' : 'A3, sin operar hoy, ajuste anterior',
        '', ahora].join(';'));
    }

    for (const t of Object.keys(ajustes)) {
      if (yaEsta.has(t)) continue;
      filas.push([t, num(ajustes[t].ajuste), 'A3, cierre anterior',
                  (ajustes[t].fecha || ''), ahora].join(';'));
    }

    for (const k of Object.keys(chicago)) {
      const c = chicago[k];
      const minutos = c.hora ? (ahoraMs - c.hora * 1000) / 60000 : null;
      // el retraso normal es de unos 10 minutos: bastante mas que eso significa
      // que la rueda cerro y el precio dejo de moverse
      const estado = (minutos != null && minutos > 25)
        ? 'Chicago, rueda cerrada, ultimo de la sesion'
        : 'Chicago, unos 10 min de retraso';
      filas.push([c.simbolo, num(c.usdTn), estado,
        c.hora ? new Date(c.hora * 1000).toLocaleString('es-AR',
          {timeZone: 'America/Argentina/Buenos_Aires', hour12: false}) : '',
        ahora].join(';'));
    }

    return new Response(filas.join('\r\n'), {
      headers: {'content-type': 'text/csv; charset=utf-8',
                'cache-control': 'no-store'}});
  }

  return new Response(JSON.stringify(salida), {
    headers: {'content-type': 'application/json; charset=utf-8',
              'cache-control': 'no-store'}});
};