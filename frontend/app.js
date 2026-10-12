const $ = selector => document.querySelector(selector);
const $$ = selector => [...document.querySelectorAll(selector)];
const trNumber = new Intl.NumberFormat('tr-TR');
const trDecimal = new Intl.NumberFormat('tr-TR', {minimumFractionDigits: 1, maximumFractionDigits: 1});

let planResult = null;
let planRun = null;
let netsuiteConfigured = false;

const statusLabels = {draft: 'Taslak', review: 'İncelemede', approved: 'Onaylandı', published: 'Yayımlandı'};
const actionLabels = {submit: 'İncelemeye gönder', approve: 'Onayla', reject: 'Taslağa iade et', publish: 'Yayımla'};

const escapeHtml = value => String(value ?? '')
  .replaceAll('&', '&amp;').replaceAll('<', '&lt;').replaceAll('>', '&gt;')
  .replaceAll('"', '&quot;').replaceAll("'", '&#039;');

function checkedValues(name) {
  return $$(`input[name="${name}"]:checked`).map(input => input.value);
}

function previousYear(isoDate) {
  const [year, month, day] = isoDate.split('-').map(Number);
  const candidate = new Date(Date.UTC(year - 1, month - 1, day));
  if (candidate.getUTCMonth() !== month - 1) candidate.setUTCDate(0);
  return candidate.toISOString().slice(0, 10);
}

function displayDate(isoDate) {
  if (!isoDate) return '—';
  const [year, month, day] = isoDate.split('-');
  return `${day}.${month}.${year}`;
}

function displayFactor(value) {
  return Number(value).toLocaleString('tr-TR', {minimumFractionDigits: 1, maximumFractionDigits: 2});
}

function updateReferencePreview() {
  const start = $('#targetStart').value;
  const end = $('#targetEnd').value;
  if (start && end) {
    $('#referencePeriod').textContent = `${displayDate(previousYear(start))} — ${displayDate(previousYear(end))}`;
  }
  const growth = Number($('#growthFactor').value || 0);
  $('#growthMeaning').textContent = `Geçmiş net satışın %${trDecimal.format(growth * 100)}'i`;
}

function payload() {
  return {
    plan_name: '2026/27 Kış üretim planı',
    target_start: $('#targetStart').value,
    target_end: $('#targetEnd').value,
    growth_factor: Number($('#growthFactor').value),
    product_types: checkedValues('productType'),
    statuses: checkedValues('status'),
    brands: checkedValues('brand'),
  };
}

function netsuitePayload() {
  const values = payload();
  return {
    plan_name: values.plan_name,
    filters: {
      product_types: values.product_types,
      statuses: values.statuses,
      brands: values.brands,
    },
    policy: {
      target_start: values.target_start,
      target_end: values.target_end,
      growth_factor: values.growth_factor,
    },
  };
}

function toast(message) {
  const box = $('#toast');
  box.textContent = message;
  box.classList.add('show');
  clearTimeout(toast.timer);
  toast.timer = setTimeout(() => box.classList.remove('show'), 2800);
}

function identityHeaders() {
  return {
    'content-type': 'application/json',
    'x-user': encodeURIComponent($('#actorName').value.trim() || 'Demo Kullanıcı'),
    'x-role': $('#actorRole').value,
  };
}

async function api(url, options = {}) {
  const response = await fetch(url, {...options, headers: {...identityHeaders(), ...(options.headers || {})}});
  if (!response.ok) {
    const body = await response.json().catch(() => ({}));
    throw new Error(body.detail || `Plan servisi ${response.status} döndürdü.`);
  }
  return response.json();
}

function priorityClass(level) {
  return `p${Math.min(4, Math.max(1, level))}`;
}

function coverageLabel(line) {
  if (!line.forecast_demand) return '—';
  return `%${trDecimal.format((line.stock_coverage || 0) * 100)}`;
}

function renderRows(lines) {
  const query = $('#tableSearch').value.trim().toLocaleLowerCase('tr-TR');
  const visible = lines.filter(line =>
    `${line.product} ${line.sku} ${line.size} ${line.color || ''}`.toLocaleLowerCase('tr-TR').includes(query)
  );
  $('#resultsBody').innerHTML = visible.map(line => `
    <tr>
      <td><span class="priority ${priorityClass(line.priority_level)}"><i></i>${line.priority_rank} · ${escapeHtml(line.priority_label)}</span></td>
      <td><strong>${escapeHtml(line.product)} · ${escapeHtml(line.size)}</strong><small>${escapeHtml(line.sku)} · ${escapeHtml(line.color || 'Renk yok')}</small></td>
      <td>${trNumber.format(line.net_sales)}</td>
      <td>${trNumber.format(line.forecast_demand)}</td>
      <td>${trNumber.format(line.sellable_stock)}</td>
      <td>${coverageLabel(line)}</td>
      <td class="production-cell">${trNumber.format(line.production_need)}</td>
      <td><div class="row-actions"><button class="detail-button" data-sku="${escapeHtml(line.sku)}">Neden?</button><button class="override-button" data-sku="${escapeHtml(line.sku)}" ${!planRun || planRun.status !== 'draft' ? 'disabled' : ''}>Revize</button></div></td>
    </tr>`).join('');
  $('#rowCount').textContent = `${visible.length} ürün/beden`;
  $$('.detail-button').forEach(button => {
    button.addEventListener('click', () => showDetail(lines.find(line => line.sku === button.dataset.sku)));
  });
  $$('.override-button').forEach(button => {
    button.addEventListener('click', () => openOverride(lines.find(line => line.sku === button.dataset.sku)));
  });
}

function renderAllocations(depots, production) {
  const max = Math.max(1, ...depots.map(row => row.allocated_production));
  $('.allocation-panel h3').textContent = `${trNumber.format(production)} adedi nereye?`;
  $('#allocationList').innerHTML = depots.map(row => {
    const percent = production ? row.allocated_production / production * 100 : 0;
    return `<div class="allocation-row">
      <div><span>${escapeHtml(row.depot)}</span><b>${trNumber.format(row.allocated_production)}</b></div>
      <div class="allocation-track"><i style="width:${row.allocated_production / max * 100}%"></i></div>
      <small>%${trDecimal.format(percent)} · stok ${trNumber.format(row.sellable_stock)} · hedef ${trNumber.format(row.target_demand)}</small>
    </div>`;
  }).join('');
}

function renderPlan(result) {
  planResult = result;
  const summary = result.summary;
  const filters = result.filters;
  $('#scopeText').textContent = [
    filters.brands.join(' + '), filters.statuses.join(' + '), filters.product_types.join(' + ')
  ].filter(Boolean).join(' · ') || 'Tüm kapsam';
  $('#referenceLabel').textContent = `${displayDate(result.reference_start)} — ${displayDate(result.reference_end)}`;
  $('#targetLabel').textContent = `${displayDate(result.target_start)} — ${displayDate(result.target_end)}`;
  $('#growthLabel').textContent = displayFactor(result.growth_factor);
  $('#kpiNet').textContent = trNumber.format(summary.net_sales);
  $('#kpiForecast').textContent = trNumber.format(summary.forecast_demand);
  $('#kpiStock').textContent = trNumber.format(summary.sellable_stock);
  $('#kpiProduction').textContent = trNumber.format(summary.production_need);
  $('#wfGross').textContent = trNumber.format(summary.gross_sales);
  $('#wfReturns').textContent = trNumber.format(summary.returns);
  $('#wfNet').textContent = trNumber.format(summary.net_sales);
  $('#wfGrowth').textContent = displayFactor(result.growth_factor);
  $('#wfStock').textContent = trNumber.format(summary.sellable_stock);
  $('#wfProduction').textContent = trNumber.format(summary.production_need);
  const warningBox = $('#warningBox');
  warningBox.hidden = !result.warnings.length;
  warningBox.innerHTML = result.warnings.map(item => `<div>⚠ ${escapeHtml(item)}</div>`).join('');
  renderRows(result.lines);
  renderAllocations(summary.depot_totals, summary.production_need);
}

function localDateTime(value) {
  if (!value) return '—';
  return new Intl.DateTimeFormat('tr-TR', {dateStyle: 'short', timeStyle: 'short'}).format(new Date(value));
}

function renderQuality(checks) {
  const passed = checks.filter(check => check.status === 'pass').length;
  const blocking = checks.filter(check => check.status === 'fail' && check.blocking).length;
  $('#qualityScore').textContent = blocking ? `${blocking} blokaj` : `${passed}/${checks.length} temiz`;
  $('#qualityScore').className = blocking ? 'quality-score fail' : 'quality-score pass';
  $('#qualityChecks').innerHTML = checks.map(check => `
    <article class="quality-check ${check.status}">
      <i>${check.status === 'pass' ? '✓' : check.status === 'warn' ? '!' : '×'}</i>
      <div><b>${escapeHtml(check.title)}</b><p>${escapeHtml(check.detail)}</p></div>
      ${check.blocking ? '<span>BLOKLAYICI</span>' : ''}
    </article>`).join('');
}

function availableActions() {
  if (!planRun) return [];
  const role = $('#actorRole').value;
  const byStatus = {
    draft: role === 'planner' || role === 'admin' ? ['submit'] : [],
    review: role === 'approver' || role === 'admin' ? ['approve', 'reject'] : [],
    approved: role === 'publisher' || role === 'admin' ? ['publish'] : [],
    published: [],
  };
  return byStatus[planRun.status] || [];
}

function renderWorkflow() {
  const actions = availableActions();
  $('#workflowActions').innerHTML = `
    <button class="audit-button" type="button">Karar günlüğü</button>
    ${actions.map(action => `<button class="transition-button ${action}" type="button" data-action="${action}">${actionLabels[action]}</button>`).join('')}`;
  $('.audit-button')?.addEventListener('click', showAudit);
  $$('.transition-button').forEach(button => button.addEventListener('click', () => transitionPlan(button.dataset.action)));
}

function renderManagedPlan(run, scroll = false) {
  planRun = run;
  renderPlan(run.result);
  $('#planId').textContent = run.id;
  $('#planVersion').textContent = `v${run.version}`;
  $('#sourceTimestamp').textContent = localDateTime(run.source_timestamp);
  $('#planStatus').textContent = statusLabels[run.status] || run.status;
  $('#planStatus').className = `status-pill ${run.status}`;
  renderQuality(run.quality_checks);
  renderWorkflow();
  $('#sourceState').textContent = run.source === 'netsuite_suiteql' || run.source === 'netsuite_payload'
    ? 'NetSuite canlı' : netsuiteConfigured ? 'Demo snapshot' : 'Demo · NetSuite ayarı bekliyor';
  if (scroll) $('#results').scrollIntoView({behavior: 'smooth', block: 'start'});
}

function showDetail(line) {
  if (!line) return;
  $('#detailTitle').textContent = `${line.product} · ${line.size} beden`;
  $('#detailContent').innerHTML = `
    <div class="detail-summary">
      <div><span>EŞ DÖNEM NET</span><b>${trNumber.format(line.net_sales)}</b></div>
      <div><span>HEDEF TAHMİN</span><b>${trNumber.format(line.forecast_demand)}</b></div>
      <div><span>SATILABİLİR STOK</span><b>${trNumber.format(line.sellable_stock)}</b></div>
      <div><span>ÜRETİM İHTİYACI</span><b>${trNumber.format(line.production_need)}</b></div>
    </div>
    <div class="detail-depots">
      ${line.depot_allocations.map(row => `<div class="detail-depot">
        <b>${escapeHtml(row.depot)}</b>
        <span>Net ${trNumber.format(row.historical_net_sales)}</span>
        <span>Pay %${trDecimal.format(row.demand_share * 100)}</span>
        <span>Hedef ${trNumber.format(row.target_demand)}</span>
        <span>Stok ${trNumber.format(row.sellable_stock)}</span>
        <span><b>Üretim ${trNumber.format(row.allocated_production)}</b></span>
      </div>`).join('')}
    </div>
    <div class="trace-copy"><p><b>${escapeHtml(line.priority_label)}:</b> ${escapeHtml(line.priority_reason)}</p>${line.explanation.map(text => `<p>${escapeHtml(text)}</p>`).join('')}</div>`;
  $('#detailDialog').showModal();
}

async function runPlan(event) {
  event?.preventDefault();
  const button = $('#runPlan');
  if (!checkedValues('productType').length || !checkedValues('status').length || !checkedValues('brand').length) {
    toast('Tür, statü ve marka seçimlerinden en az biri işaretli olmalı.');
    return;
  }
  button.disabled = true;
  button.querySelector('span').textContent = 'Hesaplanıyor…';
  try {
    const endpoint = netsuiteConfigured
      ? '/api/v1/integrations/netsuite/sync-plan'
      : '/api/v1/managed-plans/demo';
    const body = netsuiteConfigured ? netsuitePayload() : payload();
    const run = await api(endpoint, {method: 'POST', body: JSON.stringify(body)});
    renderManagedPlan(run, Boolean(event));
    await loadHistory();
    toast(`${run.result.summary.product_size_count} ürün/beden için ${run.id} oluşturuldu.`);
  } catch (error) {
    toast(error.message || 'Plan hesaplanamadı.');
  } finally {
    button.disabled = false;
    button.querySelector('span').textContent = 'Planı çalıştır';
  }
}

function openOverride(line) {
  if (!line || !planRun || planRun.status !== 'draft') return toast('Yalnızca taslak plan revize edilebilir.');
  $('#overrideSku').value = line.sku;
  $('#overrideQuantity').value = line.production_need;
  $('#overrideReason').value = '';
  $('#overrideTitle').textContent = `${line.product} · ${line.size} beden`;
  $('#overrideDialog').showModal();
}

async function saveOverride(event) {
  event.preventDefault();
  const submit = $('#overrideForm button[type="submit"]');
  submit.disabled = true;
  try {
    const run = await api(`/api/v1/managed-plans/${planRun.id}/override`, {
      method: 'POST',
      body: JSON.stringify({
        sku: $('#overrideSku').value,
        production_need: Number($('#overrideQuantity').value),
        reason: $('#overrideReason').value.trim(),
      }),
    });
    $('#overrideDialog').close();
    renderManagedPlan(run);
    await loadHistory();
    toast('Revizyon gerekçesiyle birlikte kaydedildi.');
  } catch (error) {
    toast(error.message);
  } finally {
    submit.disabled = false;
  }
}

async function transitionPlan(action) {
  const note = action === 'reject' ? 'Plan, gözden geçirilmek üzere planlamaya iade edildi.' : `${actionLabels[action]} işlemi tamamlandı.`;
  try {
    const run = await api(`/api/v1/managed-plans/${planRun.id}/transition`, {
      method: 'POST', body: JSON.stringify({action, note}),
    });
    renderManagedPlan(run);
    await loadHistory();
    toast(`${run.id}: ${statusLabels[run.status]}.`);
  } catch (error) {
    toast(error.message);
  }
}

function showAudit() {
  if (!planRun) return;
  $('#auditTimeline').innerHTML = [...planRun.audit].reverse().map(event => `
    <article><i></i><div><span>${localDateTime(event.timestamp)} · ${escapeHtml(event.actor)} / ${escapeHtml(event.role)}</span><b>${escapeHtml(event.action)}</b><p>${escapeHtml(event.detail)}</p></div></article>`).join('');
  $('#auditDialog').showModal();
}

async function loadHistory() {
  try {
    const plans = await api('/api/v1/managed-plans?limit=12');
    $('#historyList').innerHTML = plans.length ? plans.map(item => `
      <button type="button" class="history-row ${planRun?.id === item.id ? 'active' : ''}" data-id="${item.id}">
        <span><b>${escapeHtml(item.plan_name)}</b><small>${item.id} · v${item.version} · ${escapeHtml(item.created_by)}</small></span>
        <em class="status-pill ${item.status}">${statusLabels[item.status]}</em>
        <strong>${trNumber.format(item.production_need)} <small>adet</small></strong>
        <time>${localDateTime(item.updated_at)}</time>
        <i>${item.blocking_check_count ? `${item.blocking_check_count} blokaj` : item.warning_count ? `${item.warning_count} uyarı` : 'Kontroller temiz'}</i>
      </button>`).join('') : '<p class="empty-history">Henüz kayıtlı plan yok.</p>';
    $$('.history-row').forEach(row => row.addEventListener('click', async () => {
      try {
        const run = await api(`/api/v1/managed-plans/${row.dataset.id}`);
        renderManagedPlan(run, true);
        await loadHistory();
      } catch (error) { toast(error.message); }
    }));
    return plans;
  } catch (error) {
    $('#historyList').innerHTML = `<p class="empty-history">${escapeHtml(error.message)}</p>`;
    return [];
  }
}

function exportCsv() {
  if (!planResult) return toast('Önce planı çalıştırın.');
  const header = [
    'oncelik', 'sku', 'urun', 'renk', 'beden', 'net_satis', 'buyume', 'tahmin', 'satilabilir_stok',
    'uretim_ihtiyaci', 'zalando_uretim', 'ekol_uretim', 'paris_uretim'
  ];
  const rows = planResult.lines.map(line => {
    const allocation = Object.fromEntries(line.depot_allocations.map(item => [item.depot, item.allocated_production]));
    return [
      line.priority_rank, line.sku, line.product, line.color || '', line.size, line.net_sales,
      line.growth_factor, line.forecast_demand, line.sellable_stock, line.production_need,
      allocation['Zalando Offerable'] || 0, allocation.Ekol || 0, allocation['Paris Shop'] || 0
    ];
  });
  const quote = value => `"${String(value).replaceAll('"', '""')}"`;
  const csv = '\ufeff' + [header, ...rows].map(row => row.map(quote).join(';')).join('\r\n');
  const link = document.createElement('a');
  link.href = URL.createObjectURL(new Blob([csv], {type: 'text/csv;charset=utf-8'}));
  link.download = `vizyon-uretim-plani-${planResult.target_start}.csv`;
  link.click();
  URL.revokeObjectURL(link.href);
}

async function initialise() {
  updateReferencePreview();
  try {
    const response = await fetch('/api/v1/meta');
    if (response.ok) {
      const meta = await response.json();
      $('#sourceState').textContent = meta.source === 'demo' ? 'Demo snapshot' : 'NetSuite canlı';
    }
  } catch (_) {
    $('#sourceState').textContent = 'Servis bekleniyor';
  }
  try {
    const status = await api('/api/v1/integrations/netsuite/status');
    netsuiteConfigured = Boolean(status.configured);
    $('#sourceState').textContent = netsuiteConfigured ? 'NetSuite hazır' : 'Demo · ayar bekliyor';
  } catch (_) {
    netsuiteConfigured = false;
  }
  const plans = await loadHistory();
  if (plans.length) {
    try {
      renderManagedPlan(await api(`/api/v1/managed-plans/${plans[0].id}`));
      await loadHistory();
    } catch (error) { toast(error.message); }
  } else {
    await runPlan();
  }
}

$('#planForm').addEventListener('submit', runPlan);
$('#targetStart').addEventListener('change', updateReferencePreview);
$('#targetEnd').addEventListener('change', updateReferencePreview);
$('#growthFactor').addEventListener('input', updateReferencePreview);
$('#tableSearch').addEventListener('input', () => planResult && renderRows(planResult.lines));
$('#exportCsv').addEventListener('click', exportCsv);
$('#actorRole').addEventListener('change', renderWorkflow);
$('#refreshHistory').addEventListener('click', loadHistory);
$('#overrideForm').addEventListener('submit', saveOverride);
$('.dialog-close').addEventListener('click', () => $('#detailDialog').close());
$('#detailDialog').addEventListener('click', event => {
  if (event.target === $('#detailDialog')) $('#detailDialog').close();
});
$('.override-close').addEventListener('click', () => $('#overrideDialog').close());
$('.audit-close').addEventListener('click', () => $('#auditDialog').close());

initialise();
