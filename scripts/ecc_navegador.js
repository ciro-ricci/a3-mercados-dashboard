// Se ejecuta DENTRO del navegador, en una pestaña de bolsadecereales.com.
//
// El sitio de la Bolsa está detrás de Cloudflare: ni GitHub Actions ni el
// sandbox pueden bajar nada. El navegador sí, porque ya resolvió el desafío.
// Este script pide la base de datos del ECC al mismo endpoint que usa el botón
// "Descargar Base de Datos", parsea el xlsx acá mismo (el archivo no se puede
// devolver crudo) y entrega solo las filas, que después procesa
// scripts/ecc_actualizar.py.
//
// Devuelve: {filas:[{cultivo,campania,semana,condicion,siembra,cosecha}], ...}

(async () => {
  // id del formulario -> qué filas del archivo interesan.
  // La base distingue soja de primera y de segunda (Soja1/Soja2) y maíz
  // temprano y tardío (Maiz1/Maiz2). De soja se toma el agregado; el maíz se
  // separa, porque temprano y tardío transitan el llenado en momentos
  // distintos y promediarlos tapa la señal.
  const PEDIDOS = [
    { id: 3, filas: { trigo: 'trigo' } },
    { id: 1, filas: { maiz1: 'maiz_temprano', maiz2: 'maiz_tardio' } },
    { id: 2, filas: { soja: 'soja' } }
  ];
  const ZONA_TOTAL = 16;

  // ── lector mínimo de xlsx (zip + XML), sin librerías externas
  async function leerXlsx(buf) {
    const dv = new DataView(buf), td = new TextDecoder('utf-8');
    const files = {};
    let off = 0;
    while (off + 4 <= buf.byteLength && dv.getUint32(off, true) === 0x04034b50) {
      const metodo = dv.getUint16(off + 8, true);
      const comp = dv.getUint32(off + 18, true);
      const nLen = dv.getUint16(off + 26, true), eLen = dv.getUint16(off + 28, true);
      const nombre = td.decode(new Uint8Array(buf, off + 30, nLen));
      const datos = off + 30 + nLen + eLen;
      if (comp === 0 && dv.getUint32(off + 22, true) === 0) break;
      files[nombre] = { metodo, buf: buf.slice(datos, datos + comp) };
      off = datos + comp;
    }
    async function texto(n) {
      const f = files[n];
      if (!f) return '';
      if (f.metodo === 0) return td.decode(f.buf);
      const ds = new DecompressionStream('deflate-raw');
      return td.decode(await new Response(
        new Blob([f.buf]).stream().pipeThrough(ds)).arrayBuffer());
    }
    const ss = [...(await texto('xl/sharedStrings.xml')).matchAll(/<si>(.*?)<\/si>/gs)]
      .map(m => [...m[1].matchAll(/<t[^>]*>(.*?)<\/t>/gs)].map(x => x[1]).join('')
        .replace(/&amp;/g, '&').replace(/&lt;/g, '<').replace(/&gt;/g, '>'));
    const hoja = await texto('xl/worksheets/sheet1.xml');
    const filas = [];
    for (const mr of hoja.matchAll(/<row[^>]*>(.*?)<\/row>/gs)) {
      const fila = [];
      for (const mc of mr[1].matchAll(
          /<c r="([A-Z]+)\d+"([^>]*)>(?:<v>(.*?)<\/v>|<is>.*?<t[^>]*>(.*?)<\/t>.*?<\/is>)?<\/c>/gs)) {
        let col = 0;
        for (const ch of mc[1]) col = col * 26 + (ch.charCodeAt(0) - 64);
        let v = mc[3] !== undefined ? mc[3] : (mc[4] !== undefined ? mc[4] : null);
        if (/t="s"/.test(mc[2]) && v !== null) v = ss[+v];
        fila[col - 1] = v;
      }
      filas.push(fila);
    }
    return filas;
  }

  const num = v => (v === null || v === undefined || v === '' ? null : Number(v));

  // Campañas a pedir: la del ciclo en curso y la siguiente, para que la
  // transición de campaña (septiembre en maíz y soja) se tome sola.
  const hoy = new Date();
  const y = hoy.getUTCFullYear();
  const yy = n => String(n).slice(2);
  const campanias = [
    `${y - 1}/${yy(y)}`, `${y}/${yy(y + 1)}`
  ];

  const filas = [], errores = [];
  for (const pedido of PEDIDOS) {
    for (const camp of campanias) {
      const url = '/admin/phpexcel/Examples/reporte_bd.php?cultivo=' + pedido.id +
        '&campania=' + encodeURIComponent(camp) + '&zona=' + ZONA_TOTAL;
      try {
        const r = await fetch(url, { credentials: 'include' });
        if (!r.ok) { errores.push(pedido.id + ' ' + camp + ' http ' + r.status); continue; }
        const datos = await leerXlsx(await r.arrayBuffer());
        for (const f of datos) {
          if (!f[0] || f[1] !== 'TOTAL' || !f[3]) continue;
          const clave = pedido.filas[String(f[0]).toLowerCase()];
          if (!clave) continue;
          const cats = [5, 6, 7, 8, 9].map(i => num(f[i]) || 0);
          const suma = cats.reduce((a, b) => a + b, 0);
          filas.push({
            cultivo: clave,
            campania: String(f[2]).trim(),
            semana: Number(f[3]),
            // si las cinco categorías vienen en cero, esa semana no se relevó
            condicion: suma > 0 ? Math.round((cats[3] + cats[4]) * 10) / 10 : null,
            siembra: num(f[4]),
            cosecha: num(f[20])
          });
        }
      } catch (e) { errores.push(pedido.id + ' ' + camp + ': ' + String(e).slice(0, 80)); }
    }
  }

  const porCultivo = {};
  filas.forEach(f => {
    const k = f.cultivo + ' ' + f.campania;
    porCultivo[k] = Math.max(porCultivo[k] || 0, f.semana);
  });
  return { filas, total: filas.length, ultimaSemana: porCultivo, errores };
})()
