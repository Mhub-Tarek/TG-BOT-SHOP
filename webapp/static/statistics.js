/* Statistics tab — Chart.js init. Reads the JSON emitted by statistics.html
   and themes every chart from the admin.css design tokens (the "Vault" dark
   palette), so charts stay in sync with the rest of the dashboard. Vanilla,
   self-contained, no build step. */
(function () {
  var Chart = window.Chart;
  var el = document.getElementById('stats-data');
  if (!Chart || !el) return;

  var D;
  try { D = JSON.parse(el.textContent); } catch (e) { return; }

  function css(n, fallback) {
    var v = getComputedStyle(document.documentElement).getPropertyValue(n).trim();
    return v || fallback;
  }
  var BRAND  = css('--brand-500', '#6366f1');
  var VIOLET = css('--violet-500', '#a855f7');
  var MONEY  = css('--money-500', '#10b981');
  var SKY    = css('--sky', '#38bdf8');
  var YELLOW = css('--yellow', '#fbbf24');
  var RED    = css('--red', '#fb7185');
  var GRID   = 'rgba(148,163,184,.12)';
  var TICK   = css('--ink-400', '#6f7b91');
  var TEXT   = css('--ink-100', '#e6eaf2');
  var PANEL  = css('--ink-850', '#11161f');

  function rgba(hex, a) {
    hex = (hex || '').replace('#', '');
    if (hex.length === 3) hex = hex.split('').map(function (c) { return c + c; }).join('');
    var n = parseInt(hex, 16);
    if (isNaN(n) || hex.length < 6) return 'rgba(99,102,241,' + a + ')';
    return 'rgba(' + ((n >> 16) & 255) + ',' + ((n >> 8) & 255) + ',' + (n & 255) + ',' + a + ')';
  }

  Chart.defaults.color = TICK;
  Chart.defaults.font.family = "Inter, system-ui, -apple-system, sans-serif";
  Chart.defaults.font.size = 11;
  Chart.defaults.plugins.legend.labels.color = TEXT;
  Chart.defaults.plugins.legend.labels.boxWidth = 12;
  Chart.defaults.plugins.legend.labels.boxHeight = 12;
  Chart.defaults.plugins.legend.labels.usePointStyle = true;

  function scales(stacked) {
    return {
      x: { stacked: !!stacked, grid: { color: GRID, drawBorder: false }, border: { display: false },
           ticks: { color: TICK, maxRotation: 0, autoSkip: true, maxTicksLimit: 12 } },
      y: { stacked: !!stacked, beginAtZero: true, grid: { color: GRID, drawBorder: false },
           border: { display: false }, ticks: { color: TICK, precision: 0 } }
    };
  }
  function baseOpts(stacked, legend) {
    return {
      responsive: true, maintainAspectRatio: false,
      interaction: { mode: 'index', intersect: false },
      plugins: { legend: { display: legend !== false, position: 'bottom' } },
      scales: scales(stacked)
    };
  }
  function line(label, data, color, fill) {
    return {
      label: label, data: data, borderColor: color,
      backgroundColor: fill ? rgba(color, 0.18) : color,
      borderWidth: 2, tension: 0.35, fill: !!fill, pointRadius: 0, pointHoverRadius: 4
    };
  }
  function bar(label, data, color) {
    return { label: label, data: data, backgroundColor: color, borderWidth: 0, borderRadius: 4, stack: 's' };
  }
  function mk(id, cfg) {
    var c = document.getElementById(id);
    if (c) new Chart(c.getContext('2d'), cfg);
  }

  mk('chartNewUsers', {
    type: 'line',
    data: { labels: D.labels, datasets: [line('New users', D.new_users, BRAND, true)] },
    options: baseOpts(false, false)
  });

  mk('chartMoney', {
    type: 'line',
    data: { labels: D.labels, datasets: [line('Revenue', D.revenue, MONEY, true), line('Deposits', D.deposits, SKY, false)] },
    options: baseOpts(false, true)
  });

  mk('chartEngagement', {
    type: 'line',
    data: { labels: D.labels, datasets: [
      line('/start', D.starts, BRAND, false),
      line('Shop views', D.shop_views, VIOLET, false),
      line('Product views', D.product_views, YELLOW, false)
    ] },
    options: baseOpts(false, true)
  });

  var o = D.orders || { delivered: [], paid: [], awaiting: [] };
  mk('chartOrders', {
    type: 'bar',
    data: { labels: D.labels, datasets: [
      bar('Delivered', o.delivered, MONEY), bar('Paid', o.paid, BRAND), bar('Awaiting', o.awaiting, YELLOW)
    ] },
    options: baseOpts(true, true)
  });

  var mix = D.mix || [];
  if (mix.length) {
    var palette = [BRAND, MONEY, SKY, YELLOW, VIOLET, RED];
    mk('chartMix', {
      type: 'doughnut',
      data: {
        labels: mix.map(function (m) { return m.method; }),
        datasets: [{
          data: mix.map(function (m) { return m.n; }),
          backgroundColor: mix.map(function (_, i) { return palette[i % palette.length]; }),
          borderColor: PANEL, borderWidth: 2
        }]
      },
      options: { responsive: true, maintainAspectRatio: false, cutout: '62%',
                 plugins: { legend: { position: 'bottom', labels: { color: TEXT, usePointStyle: true } } } }
    });
  }
})();
