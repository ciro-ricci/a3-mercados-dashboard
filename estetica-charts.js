/* Estética DATA Miazzo — SOLO valores por defecto de Chart.js (tipografía y colores neutros).
   No modifica datasets, colores de series, escalas ni datos. */
(function () {
  if (typeof Chart === 'undefined' || !Chart.defaults) return;
  Chart.defaults.font.family = "'Rajdhani', system-ui, sans-serif";
  Chart.defaults.color = '#4B5563';
  Chart.defaults.borderColor = '#EEF0F2';
})();
