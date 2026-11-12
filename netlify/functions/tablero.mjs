// Datos del tablero comercial para estrategiasalgrano.com/tablero
//
// Solo responde a usuarios logueados con Netlify Identity (el mismo login del
// sitio). Netlify valida el token y nos pasa el usuario en context.clientContext.
//  - Administradores (TABLERO_ADMINS o rol "admin"): ven todos los clientes.
//  - Clientes: solo la empresa asociada a su mail en la Base de clientes.
// Las planillas son privadas: las lee un script de Google de la cuenta de DATA
// (TABLERO_GAS_URL) que exige una clave (TABLERO_GAS_KEY). Ningun dato de
// clientes vive en este repositorio.

import { usuarioAcceso } from '../acceso-node.mjs';

const json = (status, body) => ({
  statusCode: status,
  headers: { 'Content-Type': 'application/json; charset=utf-8', 'Cache-Control': 'private, no-store' },
  body: JSON.stringify(body),
});

async function gas(params) {
  const url = new URL(process.env.TABLERO_GAS_URL);
  url.searchParams.set('key', process.env.TABLERO_GAS_KEY || '');
  for (const [k, v] of Object.entries(params)) url.searchParams.set(k, v);
  const res = await fetch(url, { redirect: 'follow' });
  const text = await res.text();
  let data;
  try {
    data = JSON.parse(text);
  } catch {
    throw new Error('El lector de planillas no devolvio JSON (revisar la implementacion del script).');
  }
  if (data.error) throw new Error(data.error);
  return data;
}

// Tabla [[encabezados], ...filas] -> { headers, rows: [{encabezado: valor}] }
function aObjetos(tabla) {
  if (!Array.isArray(tabla) || !tabla.length) return { headers: [], rows: [] };
  const [head, ...body] = tabla;
  const headers = head.map((h) => String(h ?? '').trim());
  return { headers, rows: body.map((r) => Object.fromEntries(headers.map((h, i) => [h, r[i] ?? '']))) };
}

const col = (headers, inicio) => headers.find((h) => h.toLowerCase().startsWith(inicio.toLowerCase()));

// La planilla de cliente trae columnas de carga y columnas calculadas. Para el
// tablero se usan las calculadas (rinde y costos con las referencias aplicadas).
function lotesCanonicos(t, empresa) {
  const H = t.headers;
  const c = {
    campana: col(H, 'Campaña'),
    campo: col(H, 'Campo'),
    cultivo: col(H, 'Cultivo'),
    tenencia: col(H, 'Tenencia'),
    sup: col(H, 'Superficie'),
    estructura: col(H, 'Estructura (USD/ha)'),
    rinde: col(H, 'Rinde usado'),
    costo: col(H, 'Costo directo (USD/ha) · calculado'),
    alq: col(H, 'Alquiler usado'),
    grano: col(H, 'Grano del alquiler · calculado'),
  };
  const headers = ['Empresa', 'Campaña', 'Campo', 'Cultivo', 'Tenencia', 'Superficie (ha)', 'Rinde esperado (qq/ha)',
    'Costo directo total (USD/ha)', 'Estructura (USD/ha)', 'Alquiler (qq/ha)', 'Grano del alquiler', 'Gastos comercialización (USD/t)'];
  const rows = t.rows
    .filter((r) => r[c.cultivo] && r[c.sup] !== '' && r[c.sup] != null)
    .map((r) => ({
      Empresa: empresa,
      'Campaña': r[c.campana],
      Campo: r[c.campo],
      Cultivo: r[c.cultivo],
      Tenencia: r[c.tenencia],
      'Superficie (ha)': r[c.sup],
      'Rinde esperado (qq/ha)': r[c.rinde],
      'Costo directo total (USD/ha)': r[c.costo],
      'Estructura (USD/ha)': r[c.estructura],
      'Alquiler (qq/ha)': r[c.alq],
      'Grano del alquiler': r[c.grano],
      // flete y gastos comerciales ya estan dentro del costo directo por hectarea
      'Gastos comercialización (USD/t)': 0,
    }));
  return { headers, rows };
}

function conEmpresa(t, empresa) {
  const headers = ['Empresa', ...t.headers.filter((h) => h && !/calculado/i.test(h))];
  return { headers, rows: t.rows.map((r) => ({ Empresa: empresa, ...r })) };
}

function unir(tablas) {
  const headers = [...new Set(tablas.flatMap((t) => t.headers))];
  return { headers, rows: tablas.flatMap((t) => t.rows) };
}

export const handler = async (event, context) => {
  // Acceso único DATA Miazzo (cookie dm_ses); Identity queda como respaldo.
  const user = usuarioAcceso(event) || (context.clientContext && context.clientContext.user);
  if (!user || !user.email) return json(401, { error: 'Iniciá sesión en estrategiasalgrano.com para ver el tablero.' });
  if (!process.env.TABLERO_GAS_URL) return json(500, { error: 'Falta configurar TABLERO_GAS_URL.' });

  const email = String(user.email).trim().toLowerCase();
  const admins = String(process.env.TABLERO_ADMINS || '').toLowerCase().split(',').map((s) => s.trim()).filter(Boolean);
  const roles = (user.app_metadata && user.app_metadata.roles) || [];
  const admin = admins.includes(email) || roles.includes('admin');

  try {
    const dir = await gas({ accion: 'directorio' });
    let clientes;
    if (admin) {
      clientes = dir.clientes;
    } else {
      const propios = new Set(dir.contactos.filter((c) => c.email === email).map((c) => c.cliente));
      clientes = dir.clientes.filter((c) => !c.soloAdmin && propios.has(c.nombre));
    }
    if (!clientes.length) {
      return json(200, { usuario: email, admin, empresas: [], planillas: [], mensaje: 'Tu usuario todavía no tiene un tablero asignado. Escribinos y lo habilitamos.' });
    }

    // Una llamada por planilla, en paralelo: el script tarda ~2-3 s por planilla
    // sin caché y la función tiene 10 s de límite.
    const lecturas = await Promise.allSettled(clientes.map((c) => gas({ accion: 'planillas', ids: c.sheetId })));
    const planillas = lecturas.flatMap((r) => (r.status === 'fulfilled' ? r.value.planillas : []));
    const fallidas = clientes.filter((c, i) => lecturas[i].status !== 'fulfilled').map((c) => c.nombre);
    if (!planillas.length && fallidas.length) throw new Error(lecturas.find((r) => r.status === 'rejected').reason.message);
    const lotes = [];
    const comercial = [];
    const operaciones = [];
    const meta = [];
    for (const p of planillas) {
      lotes.push(lotesCanonicos(aObjetos(p.lotes), p.nombre));
      comercial.push(conEmpresa(aObjetos(p.comercial), p.nombre));
      operaciones.push(conEmpresa(aObjetos(p.operaciones), p.nombre));
      meta.push({
        empresa: p.nombre,
        modificado: p.modificado,
        actualizado: p.inicio && p.inicio.actualizado,
        responsable: p.inicio && p.inicio.responsable,
        // el link a la planilla solo para administradores
        ...(admin ? { sheetId: p.sheetId } : {}),
      });
    }
    return json(200, {
      usuario: email,
      admin,
      empresas: meta.map((m) => m.empresa),
      fallidas,
      planillas: meta,
      tablas: { lotes: unir(lotes), comercial: unir(comercial), operaciones: unir(operaciones) },
    });
  } catch (err) {
    return json(502, { error: `No se pudieron leer las planillas: ${err.message}` });
  }
};
