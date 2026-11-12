// Acceso único DATA Miazzo, versión para las funciones de Node de este sitio
// (tablero, tablero-fin). Lee la cookie dm_ses que deja la edge function de
// acceso y devuelve la persona con la misma forma que el usuario de Identity.
import { createHmac, timingSafeEqual } from 'node:crypto';

export function usuarioAcceso(event, sitio = 'grano') {
  const secreto = process.env.DM_ACCESO_SECRETO;
  if (!secreto) return null;
  const h = (event && event.headers) || {};
  const cookies = String(h.cookie || h.Cookie || '');
  const par = cookies.split(/;\s*/).find((c) => c.startsWith('dm_ses='));
  if (!par) return null;
  let pase;
  try { pase = decodeURIComponent(par.slice('dm_ses='.length)); } catch { return null; }
  const [p, firma] = pase.split('.');
  if (!p || !firma) return null;
  const esperada = createHmac('sha256', secreto).update(p).digest('base64url');
  const a = Buffer.from(firma), b = Buffer.from(esperada);
  if (a.length !== b.length || !timingSafeEqual(a, b)) return null;
  let d;
  try { d = JSON.parse(Buffer.from(p, 'base64url').toString('utf8')); } catch { return null; }
  if (d.t !== 'ses' || d.s !== sitio || !d.x || d.x < Date.now() / 1000 || !d.e) return null;
  return { email: d.e, app_metadata: { roles: [] }, user_metadata: {} };
}
