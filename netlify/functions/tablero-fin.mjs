// Datos del tablero financiero para estrategiasalgrano.com/tablero/financiero
//
// Mismo login y mismos permisos que el tablero comercial (tablero.mjs):
//  - Administradores (TABLERO_ADMINS o rol "admin"): ven y editan cualquier empresa.
//  - Clientes: solo ven la empresa asociada a su mail en la Base de clientes (sin editar).
// Los datos viven en un JSON por empresa en el Drive de DATA (carpeta privada) y
// los lee y escribe el mismo script de Google (TABLERO_GAS_URL + TABLERO_GAS_KEY).
// Ningun dato de clientes vive en este repositorio.
//
// GET  ?empresa=<nombre>[&pagina=1] -> { empresa, editable, modificado, colecciones[, html] }
// POST { accion: "set"|"del", empresa, col, id, data }  (solo administradores)

const COLS = ['campanas', 'operaciones', 'pases', 'deudas', 'cortes', 'config', 'cronograma', 'lineas', 'presupuesto'];

const json = (status, body) => ({
  statusCode: status,
  headers: { 'Content-Type': 'application/json; charset=utf-8', 'Cache-Control': 'private, no-store' },
  body: JSON.stringify(body),
});

async function leerRespuesta(res) {
  const text = await res.text();
  let data;
  try {
    data = JSON.parse(text);
  } catch {
    throw new Error('El script de datos no devolvio JSON (revisar la implementacion del script).');
  }
  if (data.error) throw new Error(data.error);
  return data;
}

async function gasGet(params) {
  const url = new URL(process.env.TABLERO_GAS_URL);
  url.searchParams.set('key', process.env.TABLERO_GAS_KEY || '');
  for (const [k, v] of Object.entries(params)) url.searchParams.set(k, v);
  return leerRespuesta(await fetch(url, { redirect: 'follow' }));
}

async function gasPost(body) {
  // Apps Script responde 302 a doPost; fetch sigue la redireccion con GET y trae el resultado.
  const res = await fetch(process.env.TABLERO_GAS_URL, {
    method: 'POST',
    headers: { 'Content-Type': 'text/plain;charset=utf-8' },
    body: JSON.stringify({ ...body, key: process.env.TABLERO_GAS_KEY || '' }),
    redirect: 'follow',
  });
  return leerRespuesta(res);
}

export const handler = async (event, context) => {
  const user = context.clientContext && context.clientContext.user;
  if (!user || !user.email) return json(401, { error: 'Iniciá sesión en estrategiasalgrano.com para ver el tablero.' });
  if (!process.env.TABLERO_GAS_URL) return json(500, { error: 'Falta configurar TABLERO_GAS_URL.' });
  const email = String(user.email).trim().toLowerCase();
  const admins = String(process.env.TABLERO_ADMINS || '').toLowerCase().split(',').map((s) => s.trim()).filter(Boolean);
  const roles = (user.app_metadata && user.app_metadata.roles) || [];
  const admin = admins.includes(email) || roles.includes('admin');

  let body = {};
  if (event.httpMethod === 'POST') {
    try {
      body = JSON.parse(event.body || '{}');
    } catch {
      return json(400, { error: 'Pedido inválido.' });
    }
  }
  const empresa = String((event.httpMethod === 'POST' ? body.empresa : (event.queryStringParameters || {}).empresa) || '').trim();
  if (!empresa) return json(400, { error: 'Falta indicar la empresa.' });

  try {
    if (!admin) {
      const dir = await gasGet({ accion: 'directorio' });
      const propia = dir.contactos.some((c) => c.email === email && c.cliente === empresa)
        && dir.clientes.some((c) => c.nombre === empresa && !c.soloAdmin);
      if (!propia) return json(403, { error: 'Tu usuario no tiene acceso al tablero de esta empresa.' });
    }

    if (event.httpMethod === 'GET') {
      const pagina = (event.queryStringParameters || {}).pagina === '1';
      const d = await gasGet({ accion: 'fin_leer', empresa, ...(pagina ? { pagina: '1' } : {}) });
      const colecciones = {};
      for (const c of COLS) colecciones[c] = Array.isArray(d.colecciones && d.colecciones[c]) ? d.colecciones[c] : [];
      const out = { usuario: email, admin, editable: admin, empresa, modificado: d.modificado || null, colecciones };
      // La pagina de la empresa (con sus textos) tambien vive en el Drive privado.
      if (pagina) out.html = d.html || '';
      return json(200, out);
    }

    if (event.httpMethod === 'POST') {
      if (!admin) return json(403, { error: 'Tu acceso es de solo lectura.' });
      const { accion, col, id } = body;
      if (!['set', 'del'].includes(accion) || !COLS.includes(col) || !id || typeof id !== 'string' || id.length > 80) {
        return json(400, { error: 'Pedido inválido.' });
      }
      if (accion === 'set' && (!body.data || typeof body.data !== 'object' || Array.isArray(body.data))) {
        return json(400, { error: 'Faltan los datos a guardar.' });
      }
      if (event.body.length > 400000) return json(413, { error: 'El registro es demasiado grande.' });
      const r = await gasPost({ accion: accion === 'set' ? 'fin_set' : 'fin_del', empresa, col, id, data: body.data, usuario: email });
      return json(200, { ok: true, modificado: r.modificado || null });
    }

    return json(405, { error: 'Método no permitido.' });
  } catch (err) {
    return json(502, { error: `No se pudieron leer o guardar los datos: ${err.message}` });
  }
};
