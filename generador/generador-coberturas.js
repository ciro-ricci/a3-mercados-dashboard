/* ═══════════════════════════════════════════════════════════════════════════
   GENERADOR DE HTML PARA CLIENTES — Estrategias de cobertura (soja y maíz)
   Pestaña ⚙ Estrategia → "Generar HTML para cliente".
   1) Descarga la hoja "Resumen - Agropecuarios" de A3 (misma fuente que el dashboard).
   2) Arma futuros Rosario, opciones Rosario y futuros Chicago de soja y maíz.
   3) Completa generador/plantilla-coberturas.html y descarga el archivo.
   Márgenes de futuros: se editan en la plantilla (MARGEN_TN y MARGEN_CHI)
   cuando Argentina Clearing publique una nueva comunicación.
   ═══════════════════════════════════════════════════════════════════════════ */
(function(){
  const HOJA_A3 = 'https://docs.google.com/spreadsheets/d/1j-ZrWBO-fCkGUPqWtWRsGgGswMRCm2mnMhsPmX6osLI/export?format=csv&gid=527444289';
  const PLANTILLA = '/generador/plantilla-coberturas.html';
  const MESES = ['ENE','FEB','MAR','ABR','MAY','JUN','JUL','AGO','SEP','OCT','NOV','DIC'];

  // CSV con comillas y comas decimales
  function parseCSV(texto){
    const filas=[]; let fila=[], campo='', comillas=false;
    for(let i=0;i<texto.length;i++){
      const c=texto[i];
      if(comillas){
        if(c==='"'){ if(texto[i+1]==='"'){ campo+='"'; i++; } else comillas=false; }
        else campo+=c;
      } else if(c==='"') comillas=true;
      else if(c===','){ fila.push(campo); campo=''; }
      else if(c==='\n'){ fila.push(campo.replace(/\r$/,'')); filas.push(fila); fila=[]; campo=''; }
      else campo+=c;
    }
    if(campo||fila.length){ fila.push(campo); filas.push(fila); }
    return filas;
  }
  const num = s => { if(!s||s==='N/A') return null; const n=parseFloat(String(s).replace(/\./g,'').replace(',','.')); return isNaN(n)?null:n; };
  // 'YYYY-MM-DD' o 'DD-MM-YYYY' → 'YYYY-MM-DD'
  const isoFecha = s => { if(!s||s==='N/A') return null; const m=s.match(/^(\d{2})-(\d{2})-(\d{4})/); return m?`${m[3]}-${m[2]}-${m[1]}`:(/^\d{4}-\d{2}-\d{2}/.test(s)?s.slice(0,10):null); };
  const idxMes = pos => { const m=MESES.indexOf(pos.slice(0,3)); return m<0?null:(+pos.slice(3))*12+m; };

  function armarDatos(texto){
    const filas=parseCSV(texto);
    const hdr=filas.find(f=>f[0]==='Contrato');
    if(!hdr) throw new Error('No se encontró el encabezado de la hoja de A3.');
    const col=n=>hdr.indexOf(n);
    const C={c:col('Contrato'),v:col('Fecha de Vencimiento'),t:col('Tipo de Instrumento'),p:col('Ajuste/Valor teórico'),f:col('Fecha de Datos')};

    let fechaISO=null;
    const out={soja:{futuros:[],opciones:{},chicago:[],pares:[]}, maiz:{futuros:[],opciones:{},chicago:[],pares:[]}};
    const opts={};
    filas.forEach(f=>{
      const c=f[C.c]||''; const g=/^SOJ\.ROS\/|^SOY\.CME\//.test(c)?'soja':/^MAI\.ROS\/|^CRN\.CME\//.test(c)?'maiz':null;
      if(!g) return;
      const fd=isoFecha(f[C.f]); if(fd && (!fechaISO||fd>fechaISO)) fechaISO=fd;
      const venc=isoFecha(f[C.v]), precio=num(f[C.p]), tipo=f[C.t];
      if(!venc||precio==null||precio<=0) return;
      let m;
      if(tipo==='Futuro' && (m=c.match(/^(?:SOJ|MAI)\.ROS\/([A-Z]{3}\d\d)$/))) out[g].futuros.push({pos:m[1],venc,precio});
      else if(tipo==='Futuro' && (m=c.match(/^(?:SOY|CRN)\.CME\/([A-Z]{3}\d\d)$/))){
        const i=idxMes(m[1]); if(i==null) return;
        const ci=i+1;   // cada posición Chicago de A3 replica el contrato CBOT del mes siguiente
        out[g].chicago.push({pos:m[1],venc,precio,cbot:MESES[ci%12]+String(Math.floor(ci/12)).padStart(2,'0'),cbotIdx:ci});
      }
      else if(tipo==='Opción' && (m=c.match(/^(?:SOJ|MAI)\.ROS\/([A-Z]{3}\d\d) ([\d.]+) ([PC])$/))){
        const k=g+'|'+m[1]; opts[k]=opts[k]||{g,pos:m[1],venc,calls:[],puts:[]};
        opts[k][m[3]==='C'?'calls':'puts'].push([parseFloat(m[2]),precio]);
      }
    });
    if(!fechaISO) fechaISO=new Date().toISOString().slice(0,10);
    // margen mínimo de días al vencimiento para mostrar una posición
    const limite=new Date(fechaISO+'T12:00:00'); limite.setDate(limite.getDate()+7);
    const vigente=v=>new Date(v+'T12:00:00')>limite;

    ['soja','maiz'].forEach(g=>{
      const d=out[g];
      d.futuros=d.futuros.filter(f=>vigente(f.venc)).sort((a,b)=>a.venc.localeCompare(b.venc));
      d.chicago=d.chicago.filter(c=>vigente(c.venc)).sort((a,b)=>a.venc.localeCompare(b.venc));
      Object.values(opts).filter(o=>o.g===g && vigente(o.venc) && o.calls.length+o.puts.length>=3)
        .sort((a,b)=>a.venc.localeCompare(b.venc))
        .forEach(o=>{
          const fut=d.futuros.find(f=>f.pos===o.pos); if(!fut) return;
          o.calls.sort((a,b)=>a[0]-b[0]); o.puts.sort((a,b)=>a[0]-b[0]);
          d.opciones[o.pos]={venc:o.venc,calls:o.calls,puts:o.puts,fut:fut.precio};
        });
      d.futuros.forEach(f=>{
        const i=idxMes(f.pos); if(i==null) return;
        const cand=d.chicago.filter(c=>c.cbotIdx-i>=0 && c.cbotIdx-i<=1);
        if(!cand.length) return;
        const c=cand.reduce((a,b)=>(b.cbotIdx-i)<(a.cbotIdx-i)?b:a);
        d.pares.push({ros:f.pos,rosVenc:f.venc,rosPrecio:f.precio,chi:c.pos,chiVenc:c.venc,chiPrecio:c.precio,cbot:c.cbot});
      });
    });
    const [y,mo,da]=fechaISO.split('-');
    out.fechaISO=fechaISO; out.fecha=`${da}/${mo}/${y}`;
    return out;
  }

  const escHTML = s => s.replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const nombreArchivo = (cli,f) => 'Coberturas_'+(cli.normalize('NFD').replace(/[̀-ͯ]/g,'').replace(/[^A-Za-z0-9]+/g,'_').replace(/^_|_$/g,'')||'Cliente')+'_'+f+'.html';

  async function generar(){
    const inp=document.getElementById('gencob-cliente'), btn=document.getElementById('gencob-btn'), est=document.getElementById('gencob-estado');
    const cliente=(inp.value||'').trim();
    if(!cliente){ est.textContent='Ingresá el nombre del cliente.'; est.style.color='#cb2431'; inp.focus(); return; }
    btn.disabled=true; est.style.color='#666'; est.textContent='Descargando precios de A3…';
    try{
      const [rA3,rPl]=await Promise.all([fetch(HOJA_A3,{cache:'no-store'}),fetch(PLANTILLA,{cache:'no-store'})]);
      if(!rA3.ok) throw new Error('No se pudo leer la hoja de A3 ('+rA3.status+').');
      if(!rPl.ok) throw new Error('No se encontró la plantilla ('+rPl.status+').');
      const datos=armarDatos(await rA3.text());
      if(!datos.soja.futuros.length && !datos.maiz.futuros.length) throw new Error('La hoja de A3 no trae futuros de soja ni maíz.');
      let html=await rPl.text();
      if(!html.includes('/*DATA*/')) throw new Error('La plantilla no tiene el marcador de datos.');
      html=html.replace('/*DATA*/',()=>JSON.stringify(datos)).split('{{CLIENTE}}').join(escHTML(cliente));
      const blob=new Blob([html],{type:'text/html;charset=utf-8'});
      const a=document.createElement('a'); a.href=URL.createObjectURL(blob); a.download=nombreArchivo(cliente,datos.fechaISO);
      document.body.appendChild(a); a.click(); a.remove(); setTimeout(()=>URL.revokeObjectURL(a.href),4000);
      est.style.color='#1a7f37'; est.textContent=`✓ Listo: ${a.download} · precios A3 al ${datos.fecha}`;
    }catch(e){
      est.style.color='#cb2431'; est.textContent='Error: '+e.message;
    }finally{ btn.disabled=false; }
  }

  // Tarjeta para la pestaña Estrategia
  window.genCobCardHTML = () => `
    <div class="full-card" id="gencob-card" style="border-left:4px solid #024627">
      <div style="display:flex;justify-content:space-between;gap:16px;flex-wrap:wrap;align-items:center">
        <div>
          <h3 style="margin:0 0 4px">📄 Generar HTML para cliente</h3>
          <div style="color:#666;font-size:0.85rem">Futuros, lanzamiento cubierto, compra de put, bull put spread, spread Argentina–Chicago y calculadora de márgenes, con los precios de A3 de hoy.</div>
        </div>
        <div style="display:flex;gap:10px;align-items:flex-end;flex-wrap:wrap">
          <div class="strat-field"><label>Cliente</label><input id="gencob-cliente" class="strat-select" type="text" placeholder="Ej.: Luga Agronegocios" style="min-width:220px" onkeydown="if(event.key==='Enter')generarHTMLCliente()"></div>
          <button id="gencob-btn" class="btn-add" style="background:#024627" onclick="generarHTMLCliente()">⬇ Generar HTML</button>
        </div>
      </div>
      <div id="gencob-estado" style="margin-top:8px;font-size:0.82rem;min-height:1em"></div>
    </div>`;
  window.generarHTMLCliente = generar;
  window._genCobArmarDatos = armarDatos;   // para pruebas
})();
