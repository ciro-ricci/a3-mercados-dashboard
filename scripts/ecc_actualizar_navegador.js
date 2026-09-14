// Port a JavaScript de scripts/ecc_actualizar.py, para correr en el navegador.
//
// Por qué existe: el sitio de la Bolsa está detrás de Cloudflare, así que los
// datos solo se pueden bajar desde un navegador de verdad. Hasta septiembre de
// 2026 el navegador traía las filas y el resto lo hacía Python en la máquina de
// CIRO; una actualización de Windows rompió el workspace y Python dejó de estar
// disponible. Ahora todo el flujo vive en el navegador.
//
// Uso: window.eccActualizar(html, filas) -> {html, resumen}
//   html  = index.html completo, tal como viene de la API de GitHub
//   filas = [{cultivo, campania, semana, condicion, siembra, cosecha}, ...]
//           tal como las devuelve scripts/ecc_navegador.js

(function () {
  var INICIO = '// ECC_DATA:START';
  var FIN = '// ECC_DATA:END';
  var MESES = ['enero','febrero','marzo','abril','mayo','junio','julio',
               'agosto','septiembre','octubre','noviembre','diciembre'];
  var ANIOS_PROM = 5;
  var SIEMBRA_MINIMA = 50;   // por debajo, el % de condición es ruido
  var COSECHA_MAXIMA = 90;   // por encima, la Bolsa arrastra el último valor

  function anioDe(c) { return parseInt(String(c).split('/')[0], 10); }

  // La campaña cruza el fin de año, así que el número de semana no da el orden.
  // La cosecha sí: dentro de una campaña solo puede subir, y donde cae de golpe
  // está el corte. El hueco entre semanas queda de respaldo para cuando todavía
  // no hay cosecha informada.
  function orden(semanas, valores) {
    var s = semanas.map(Number).sort(function (a, b) { return a - b; });
    if (s.length < 2) return s;
    if (valores) {
      var cos = s.map(function (w) { var f = valores[String(w)]; return f ? f[2] : null; });
      if (cos.some(function (v) { return v; })) {
        var peor = 0, ci = -1;
        for (var k = 0; k < s.length; k++) {
          var a = cos[k], b = cos[(k + 1) % s.length];
          if (a !== null && b !== null && a - b > peor) { peor = a - b; ci = k; }
        }
        if (peor > 20) return s.slice(ci + 1).concat(s.slice(0, ci + 1));
      }
    }
    var mejor = 0, corte = s.length - 1;
    for (var i = 0; i < s.length - 1; i++) {
      if (s[i + 1] - s[i] > mejor) { mejor = s[i + 1] - s[i]; corte = i; }
    }
    if (s[0] + 53 - s[s.length - 1] > mejor) corte = s.length - 1;
    return s.slice(corte + 1).concat(s.slice(0, corte + 1));
  }

  // Ante empate se toma la campaña nueva: en la transición de septiembre el
  // maíz viejo termina de cosecharse mientras el nuevo empieza a sembrarse, y
  // el que mira el mercado es el nuevo.
  function vigente(camps) {
    var mejor = null, clave = null;
    Object.keys(camps).forEach(function (c) {
      var s = orden(Object.keys(camps[c]), camps[c]);
      var k = s.length ? [anioDe(c) + (s[s.length - 1] < s[0] ? 1 : 0), s[s.length - 1], anioDe(c)] : [0, 0, 0];
      if (!clave || k[0] > clave[0] || (k[0] === clave[0] && (k[1] > clave[1] ||
          (k[1] === clave[1] && k[2] > clave[2])))) { clave = k; mejor = c; }
    });
    return mejor;
  }

  // La Bolsa informa el avance de siembra solo mientras se siembra: al llegar a
  // 100% la celda vuelve a cero. Y deja de relevar la condición cuando el
  // cultivo ya no está en pie.
  function acumular(semanas) {
    [1, 2].forEach(function (idx) {
      var tope = 0;
      orden(Object.keys(semanas), semanas).forEach(function (s) {
        var v = semanas[String(s)][idx];
        if (v !== null && v !== undefined && v > tope) tope = v;
        if (tope > 0) semanas[String(s)][idx] = tope;
      });
    });
    Object.keys(semanas).forEach(function (s) {
      var sie = semanas[s][1], cos = semanas[s][2];
      if ((sie === null || sie === undefined || sie <= SIEMBRA_MINIMA) ||
          (cos !== null && cos !== undefined && cos >= COSECHA_MAXIMA)) {
        semanas[s][0] = null;
      }
    });
    return semanas;
  }

  function comparar(camps, vig, idx) {
    var sems = orden(Object.keys(camps[vig]), camps[vig]);
    if (!sems.length) return null;
    var w = sems[sems.length - 1];
    var prevs = Object.keys(camps).filter(function (c) { return anioDe(c) < anioDe(vig); })
      .sort(function (a, b) { return anioDe(b) - anioDe(a); }).slice(0, ANIOS_PROM);
    function val(c, s) { var f = camps[c] && camps[c][String(s)]; return f ? f[idx] : null; }
    var vals = prevs.map(function (c) { return val(c, w); })
      .filter(function (v) { return v !== null && v !== undefined; });
    return {
      semana: w,
      actual: val(vig, w),
      semana_previa: sems.length > 1 ? val(vig, sems[sems.length - 2]) : null,
      anio_previo: prevs.length ? val(prevs[0], w) : null,
      prom5: vals.length ? Math.round(vals.reduce(function (a, b) { return a + b; }, 0) / vals.length * 10) / 10 : null,
      prom5_n: vals.length
    };
  }

  // El PAS sale los jueves, así que la semana del ECC es la semana ISO de ese jueves.
  function jueves(anio, semana) {
    var d = new Date(Date.UTC(anio, 0, 4));
    var dow = (d.getUTCDay() + 6) % 7;
    d.setUTCDate(d.getUTCDate() - dow + (semana - 1) * 7 + 3);
    return d;
  }
  function semanaISO(f) {
    var d = new Date(Date.UTC(f.getUTCFullYear(), f.getUTCMonth(), f.getUTCDate()));
    d.setUTCDate(d.getUTCDate() + 4 - ((d.getUTCDay() + 6) % 7 + 1));
    var a = new Date(Date.UTC(d.getUTCFullYear(), 0, 1));
    return Math.ceil(((d - a) / 86400000 + 1) / 7);
  }

  window.eccActualizar = function (html, filas) {
    var re = new RegExp(INICIO.replace(/[/]/g, '\\/') + '[\\s\\S]*?' + FIN.replace(/[/]/g, '\\/'));
    var m = html.match(re);
    if (!m) throw new Error('No están los marcadores ECC_DATA en index.html');
    var mo = m[0].match(/const ECC_DATA\s*=\s*(\{[\s\S]*\});/);
    var D = JSON.parse(mo[1]);

    var agregadas = 0;
    (filas || []).forEach(function (f) {
      var camp = String(f.campania || '');
      if (!camp || camp === 'null') return;
      var fila = [f.condicion, f.siembra, f.cosecha];
      if (fila.every(function (v) { return v === null || v === undefined; })) return;
      D.hist = D.hist || {};
      D.hist[f.cultivo] = D.hist[f.cultivo] || {};
      D.hist[f.cultivo][camp] = D.hist[f.cultivo][camp] || {};
      var sem = String(parseInt(f.semana, 10));
      var antes = D.hist[f.cultivo][camp][sem];
      if (JSON.stringify(antes) !== JSON.stringify(fila)) agregadas++;
      D.hist[f.cultivo][camp][sem] = fila;
    });

    var semMax = 0, resumen = [];
    Object.keys(D.hist).forEach(function (cr) {
      var camps = D.hist[cr];
      Object.keys(camps).forEach(function (c) { acumular(camps[c]); });
      var vig = vigente(camps);
      if (!vig) return;
      var fila = { campania: vig };
      [['condicion', 0], ['siembra', 1], ['cosecha', 2]].forEach(function (p) {
        fila[p[0]] = comparar(camps, vig, p[1]);
      });
      fila.semana = fila.condicion.semana;
      var prevs = Object.keys(camps).filter(function (c) { return anioDe(c) < anioDe(vig); })
        .sort(function (a, b) { return anioDe(b) - anioDe(a); });
      fila.campania_previa = prevs.length ? prevs[0] : null;
      D.cultivos[cr] = fila;
      if (fila.semana > semMax) semMax = fila.semana;
      resumen.push(cr + ': ' + vig + ' semana ' + fila.semana +
                   ' — condición ' + (fila.condicion.actual === null ? 'sin relevar' : fila.condicion.actual + '%'));
    });

    var hoy = new Date();
    var anio = semMax <= semanaISO(hoy) + 1 ? hoy.getUTCFullYear() : hoy.getUTCFullYear() - 1;
    var f = jueves(anio, semMax);
    D.semana = semMax;
    D.fecha = f.getUTCDate() + ' de ' + MESES[f.getUTCMonth()] + ' de ' + f.getUTCFullYear();
    D.fecha_iso = f.toISOString().slice(0, 10);

    var bloque = INICIO + ' — bloque autogenerado por la tarea semanal, no editar a mano\n' +
                 'const ECC_DATA = ' + JSON.stringify(D) + ';\n' + FIN;
    // función de reemplazo: los "$" del JSON no deben tomarse como grupos
    html = html.replace(re, function () { return bloque; });

    return { html: html, resumen: resumen, agregadas: agregadas, semana: semMax, fecha: D.fecha };
  };
})();
