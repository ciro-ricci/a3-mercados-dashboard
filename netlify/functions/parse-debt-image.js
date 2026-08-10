// Netlify Function: parse-debt-image
// Recibe una imagen (base64) con una tabla/captura de deuda o cuentas por cobrar
// y usa la API de Claude (Anthropic) con visión para extraer los datos estructurados
// que después se cargan en la pestaña Deuda/Stock del dashboard.
//
// Requiere la variable de entorno ANTHROPIC_API_KEY configurada en Netlify
// (Site settings -> Environment variables).

const ANTHROPIC_URL = 'https://api.anthropic.com/v1/messages';
const MODEL = 'claude-3-5-sonnet-latest';
const MAX_BASE64_CHARS = 7_000_000; // ~5MB de imagen aprox.

const SYSTEM_PROMPT = `Sos un asistente que lee capturas de pantalla o fotos de planillas de deuda agropecuaria argentina (tipo Excel) y extrae datos estructurados.

La imagen puede mostrar:
- Un cronograma de vencimientos de deuda (compromisos a pagar), a veces en varias columnas de moneda (Pesos, Pesos en USD, USD).
- Cuentas por cobrar (créditos a favor del productor), si las hay, a veces en una tabla separada o marcadas de otra forma.
- Stock de granos (soja, maíz, trigo) en toneladas, si aparece mencionado.

Reglas importantes:
- Si hay columna en USD, usá esa como el monto. Si no hay columna en USD pero sí en Pesos, dejá el monto en null y aclaralo en "notas" (no inventes un tipo de cambio).
- Los meses suelen aparecer como "26. Julio", "Jul-26", "Julio 2026", etc. Convertí siempre a formato "YYYY-MM". Si el año no es explícito, asumí que corresponde al año en curso o al más cercano razonable, y decilo en "notas".
- Ignorá filas de totales/subtotales (ej. la fila final con la suma de todo).
- Si no podés distinguir con certeza qué filas son deuda (a pagar) vs. cuentas por cobrar, asumí que son deuda (vencimientos) y decilo en "notas".
- No inventes datos que no estén en la imagen.

Respondé ÚNICA Y EXCLUSIVAMENTE con un JSON válido (sin markdown, sin texto extra, sin \`\`\`), con esta forma exacta:
{
  "vencimientos": [{"fecha":"YYYY-MM","monto":NUMBER,"label":"texto original de la fila"}],
  "cxc": [{"fecha":"YYYY-MM","monto":NUMBER,"label":"texto original de la fila"}],
  "stock": {"soja": NUMBER_OR_NULL, "maiz": NUMBER_OR_NULL, "trigo": NUMBER_OR_NULL},
  "notas": "aclaraciones breves sobre supuestos o datos ambiguos, o string vacío si no hay nada que aclarar"
}`;

function jsonResponse(statusCode, body) {
  return {
    statusCode,
    headers: { 'Content-Type': 'application/json', 'Access-Control-Allow-Origin': '*' },
    body: JSON.stringify(body)
  };
}

function extractJson(text) {
  if (!text) return null;
  let cleaned = text.trim();
  cleaned = cleaned.replace(/^```(json)?/i, '').replace(/```$/, '').trim();
  const start = cleaned.indexOf('{');
  const end = cleaned.lastIndexOf('}');
  if (start === -1 || end === -1) return null;
  try {
    return JSON.parse(cleaned.slice(start, end + 1));
  } catch (e) {
    return null;
  }
}

exports.handler = async function (event) {
  if (event.httpMethod === 'OPTIONS') {
    return jsonResponse(200, {});
  }
  if (event.httpMethod !== 'POST') {
    return jsonResponse(405, { error: 'Método no permitido.' });
  }

  const apiKey = process.env.ANTHROPIC_API_KEY;
  if (!apiKey) {
    return jsonResponse(500, {
      error: 'Falta configurar ANTHROPIC_API_KEY en las variables de entorno de Netlify.'
    });
  }

  let payload;
  try {
    payload = JSON.parse(event.body || '{}');
  } catch (e) {
    return jsonResponse(400, { error: 'Body inválido, se esperaba JSON.' });
  }

  const { image, mediaType } = payload;
  if (!image || typeof image !== 'string') {
    return jsonResponse(400, { error: 'Falta la imagen (campo "image" en base64).' });
  }
  if (image.length > MAX_BASE64_CHARS) {
    return jsonResponse(400, { error: 'La imagen es muy pesada. Probá con una más liviana (menos de ~5MB).' });
  }
  const allowedTypes = ['image/png', 'image/jpeg', 'image/webp', 'image/gif'];
  const finalMediaType = allowedTypes.includes(mediaType) ? mediaType : 'image/png';

  try {
    const anthropicRes = await fetch(ANTHROPIC_URL, {
      method: 'POST',
      headers: {
        'content-type': 'application/json',
        'x-api-key': apiKey,
        'anthropic-version': '2023-06-01'
      },
      body: JSON.stringify({
        model: MODEL,
        max_tokens: 1500,
        system: SYSTEM_PROMPT,
        messages: [
          {
            role: 'user',
            content: [
              { type: 'image', source: { type: 'base64', media_type: finalMediaType, data: image } },
              { type: 'text', text: 'Extraé los datos de esta imagen siguiendo las instrucciones del sistema. Respondé solo con el JSON.' }
            ]
          }
        ]
      })
    });

    if (!anthropicRes.ok) {
      const errText = await anthropicRes.text();
      return jsonResponse(anthropicRes.status, {
        error: 'La API de Claude devolvió un error.',
        detail: errText.slice(0, 500)
      });
    }

    const data = await anthropicRes.json();
    const textBlock = (data.content || []).find((c) => c.type === 'text');
    const parsed = extractJson(textBlock ? textBlock.text : '');

    if (!parsed) {
      return jsonResponse(502, {
        error: 'No se pudo interpretar la respuesta de la IA como JSON.',
        raw: textBlock ? textBlock.text.slice(0, 800) : ''
      });
    }

    parsed.vencimientos = Array.isArray(parsed.vencimientos) ? parsed.vencimientos : [];
    parsed.cxc = Array.isArray(parsed.cxc) ? parsed.cxc : [];
    parsed.stock = parsed.stock && typeof parsed.stock === 'object' ? parsed.stock : {};
    parsed.notas = typeof parsed.notas === 'string' ? parsed.notas : '';

    return jsonResponse(200, parsed);
  } catch (e) {
    return jsonResponse(500, { error: 'Error inesperado procesando la imagen.', detail: String(e).slice(0, 300) });
  }
};
