'use strict';
const state = { user: null, products: [], proposals: [], history: {runs: [],events: [],operations: []}, view: 'catalog', selected: new Set(), config: null };
const $ = (selector) => document.querySelector(selector);
const esc = (value) => String(value ?? '').replace(/[&<>"']/g, (c) => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const money = (value, currency = 'USD') => new Intl.NumberFormat('en', {style:'currency',currency}).format(value);
const date = (value) => new Date(value).toLocaleString([], {dateStyle:'medium',timeStyle:'short'});
const status = (value) => `<span class="status ${esc(value)}">${esc(value.replaceAll('_',' '))}</span>`;
let toastTimer;
function toast(message, error = false) { $('#toast').textContent = message; $('#toast').className = error ? 'error' : ''; $('#toast').hidden = false; clearTimeout(toastTimer); toastTimer = setTimeout(() => $('#toast').hidden = true, 6500); }
async function api(path, options = {}) {
  const response = await fetch('/api' + path, {...options, headers: {'Content-Type':'application/json', ...options.headers}});
  let data;
  try { data = await response.json(); } catch { throw new Error('The server returned an unreadable response.'); }
  if (!response.ok) {
    const detail = Array.isArray(data.detail) ? data.detail.map((e) => e.msg).join('. ') : data.detail;
    throw new Error(detail || 'The request could not be completed.');
  }
  return data;
}
const post = (path, data = {}) => api(path, {method:'POST',body:JSON.stringify(data)});
async function action(button, fn) {
  const previous = button.textContent; button.disabled = true; button.textContent = 'Working…';
  try { await fn(); } catch (error) { toast(error.message, true); }
  finally { if (button.isConnected) { button.disabled = false; button.textContent = previous; } }
}
function dialog(html) { $('#modal-content').innerHTML = html; if (!$('#modal').open) $('#modal').showModal(); }
function close() { $('#modal').close(); }
function auth(register = false) {
  dialog(`<p class="eyebrow">YOUR OWN WORKSPACE</p><h2>${register ? 'Start with a clean slate.' : 'Welcome back.'}</h2><p>${register ? 'Create an account to explore four sample products. Your catalog and decisions stay in your own workspace.' : 'Sign in to your catalog, proposals and change history.'}</p><form id="auth-form">${register ? '<label class="field">Your name<input name="name" autocomplete="name" required maxlength="60"></label>' : ''}<label class="field">Email<input name="email" type="email" autocomplete="email" required maxlength="200"></label><label class="field">Password<input name="password" type="password" autocomplete="${register ? 'new-password' : 'current-password'}" minlength="10" maxlength="128" required placeholder="At least 10 characters"></label><div class="form-error" id="form-error" role="alert"></div><button class="button primary full">${register ? 'Create workspace ↗' : 'Sign in ↗'}</button></form><button class="auth-switch" id="switch-auth">${register ? 'Already have an account? Sign in' : 'New here? Create a workspace'}</button><p class="auth-legal">Local accounts use hashed passwords and an HttpOnly session cookie. Sample data is illustrative; no live store changes happen in demo mode.</p>`);
  $('#switch-auth').onclick = () => auth(!register);
  $('#auth-form').onsubmit = async (event) => {
    event.preventDefault(); const form = event.currentTarget; const button = form.querySelector('button'); button.disabled = true;
    try { state.user = await post(register ? '/auth/register' : '/auth/login', Object.fromEntries(new FormData(form))); close(); await refresh(); toast(register ? 'Your workspace is ready.' : 'Signed in.'); }
    catch (error) { $('#form-error').textContent = error.message; }
    finally { button.disabled = false; }
  };
}
function account() {
  if (!state.user) return auth(true);
  dialog(`<p class="eyebrow">WORKSPACE ACCOUNT</p><h2>${esc(state.user.name)}</h2><p>${esc(state.user.email)}</p><p>Your products, proposals and audit history belong to this account.</p><div class="actions"><a class="button" href="/api/export" download>Export workspace JSON ↓</a><button class="button primary" id="logout">Sign out</button></div>`);
  $('#logout').onclick = async (event) => action(event.currentTarget, async () => { await post('/auth/logout'); state.user = null; state.products=[]; state.proposals=[]; state.selected.clear(); close(); render(); });
}
function settings() {
  const mode = state.config?.mode;
  dialog(`<p class="eyebrow">CONNECTIONS & MODE</p><h2>Know what is connected.</h2><p>Descriptions: <strong>${esc(mode?.llm || 'demo')}</strong><br>Live Shopify writes: <strong>${mode?.shopify_writes ? 'enabled' : 'disabled'}</strong></p><div class="notice">Sample observations are labeled and never presented as live market prices. Demo changes update your local database only.</div><p>Optional providers are configured on the server. See the README and .env.example for OpenAI and Shopify setup. Credentials never enter this page.</p><p>Shopify imports are limited to 50 products per request. Every write requires approval, an unchanged product version and an explicit execution action.</p><button class="button" id="import-shopify" ${state.user ? '' : 'disabled'}>Import Shopify catalog ↙</button>`);
  $('#import-shopify').onclick = async (event) => action(event.currentTarget, async () => { const result = await post('/shopify/import'); close(); await refresh(); toast(`${result.imported} Shopify products imported.`); });
}
function illustration(title) {
  const style = /mug/i.test(title) ? '<path d="M33 40h61v63c0 17-61 17-61 0z" fill="#d1c3a8"/><ellipse cx="63" cy="40" rx="30" ry="9" fill="#e5ddc9"/><ellipse cx="63" cy="41" rx="23" ry="5" fill="#b5a488"/><path d="M94 51h12c24 0 21 43-12 43" stroke="#c7b99d" stroke-width="12" fill="none"/>' : /notebook/i.test(title) ? '<rect x="31" y="24" width="75" height="105" rx="4" fill="#414d44"/><path d="M38 24v105" stroke="#929582" stroke-width="2"/><path d="M48 47h42M48 55h29" stroke="#b5b9a4" stroke-width="1"/><rect x="94" y="24" width="3" height="105" fill="#303a34"/>' : /light/i.test(title) ? '<ellipse cx="70" cy="124" rx="35" ry="6" fill="#3a433c"/><path d="M70 122V72l25-34" stroke="#465045" stroke-width="7" fill="none"/><path d="M74 28q29-10 42 16L80 63q-13-13-6-35" fill="#465045"/><ellipse cx="97" cy="52" rx="20" ry="6" transform="rotate(-28 97 52)" fill="#e0cb91"/>' : '<path d="M30 58h80l-8 70H38z" fill="#c4b490"/><path d="M48 62V42q0-36 20-36t20 36v20" stroke="#baa985" stroke-width="8" fill="none"/><path d="M39 69l4 47M101 69l-4 47" stroke="#ab9b76" stroke-width="1"/><rect x="59" y="83" width="22" height="15" fill="#d5c8a8"/><path d="M64 89h12M64 94h7" stroke="#948560" stroke-width="1"/>';
  return `<svg viewBox="0 0 140 150" role="img" aria-label="Illustration of ${esc(title)}">${style}</svg>`;
}
function stats() {
  const pending = state.proposals.filter((p) => p.status === 'proposed').length;
  const applied = state.proposals.filter((p) => p.status === 'applied').length;
  const low = state.products.filter((p) => p.inventory < 10).length;
  return `<section class="stats" aria-label="Workspace summary"><div class="stat"><div class="stat-label">Products in your catalog</div><div class="stat-number">${state.products.length.toString().padStart(2,'0')} <small>IN VIEW</small></div></div><div class="stat"><div class="stat-label">Ready for your review</div><div class="stat-number">${pending.toString().padStart(2,'0')} <small>PROPOSALS</small></div></div><div class="stat"><div class="stat-label">Low-stock products</div><div class="stat-number">${low.toString().padStart(2,'0')}</div></div><div class="stat"><div class="stat-label">Approved changes applied</div><div class="stat-number">${applied.toString().padStart(2,'0')}</div></div></section>`;
}
function catalogView() {
  return `${stats()}<div class="toolbar"><div><h2>The product shelf <span class="muted">(${state.products.length})</span></h2><p>Select products to analyze, or start with the whole catalog.</p></div><div class="actions"><button class="button" id="add-product">＋ Add product</button><button class="button primary" id="analyze">✳ Analyze ${state.selected.size ? 'selected (' + state.selected.size + ')' : 'catalog'} ↗</button></div></div><div class="notice"><strong>Thoughtful changes, with your approval.</strong> Analysis creates proposals only. Sample prices are illustrative. Live changes always need a separate approval and execution.</div><section class="catalog-grid" aria-label="Product catalog">${state.products.map((p) => `<article class="product-card"><div class="product-image"><input type="checkbox" class="product-select" data-select="${p.id}" ${state.selected.has(p.id) ? 'checked' : ''} aria-label="Select ${esc(p.title)}"><span class="product-origin">${esc(p.origin)}</span>${illustration(p.title)}</div><div class="product-body"><h3>${esc(p.title)}</h3><div class="product-meta"><strong>${money(p.price,p.currency)}</strong><span class="${p.inventory < 10 ? 'stock-low' : ''}">${p.inventory} in stock${p.inventory < 10 ? ' ↘' : ''}</span></div><p class="product-desc">${esc(p.description.slice(0,125))}${p.description.length > 125 ? '…' : ''}</p><span class="evidence-tag">${p.evidence.length} price observation${p.evidence.length === 1 ? '' : 's'} · v${p.version}</span></div><div class="product-card-bottom"><button class="text-button" data-edit="${p.id}">Edit details ↗</button><button class="text-button" data-evidence="${p.id}">Price evidence ＋</button></div></article>`).join('')}</section>${!state.products.length ? '<div class="empty"><h2>Your first product belongs here.</h2><p>Add a product to start creating proposals.</p></div>' : ''}<div class="wide-callout"><div><h3>Small improvements. A clear paper trail.</h3><p>Descriptions, product-only offers and restock notes.<br>Every proposal keeps its evidence and original product version.</p></div><span class="callout-icon">↗</span><button class="button" data-view="review">Open review queue →</button></div>`;
}
function reviewView() {
  const pending = state.proposals.filter((p) => ['proposed','approved'].includes(p.status));
  const completed = state.proposals.filter((p) => !['proposed','approved'].includes(p.status));
  const cards = (rows) => rows.map((p) => `<article class="proposal-card"><div class="proposal-header"><div><span class="kind">${esc(p.kind === 'restock' ? 'Restock recommendation' : p.kind === 'promotion' ? 'Product-only promotion' : 'Description refinement')}</span><h3>${esc(p.title)}</h3></div>${status(p.status)}</div><p class="proposal-reason">${esc(p.reason)}</p><div class="diff"><div><span class="diff-label">Current / before</span><pre>${esc(p.before_text)}</pre></div><div><span class="diff-label">Proposed / after</span><pre>${esc(p.after_text)}</pre></div></div>${p.evidence.length ? `<div class="evidence-list">${p.evidence.map((e) => `<div>${e.is_sample ? 'SAMPLE' : 'OBSERVED'} · ${esc(e.source)} · ${money(e.price,e.currency)} · ${date(e.observed_at)}</div>`).join('')}</div>` : ''}<div class="proposal-footer"><small>Product v${p.version} · ${date(p.created_at)}</small><div class="actions"><button class="button small" data-preview="${p.id}">Dry run</button>${p.status === 'proposed' ? `<button class="button small" data-decision="rejected" data-id="${p.id}">Reject</button><button class="button primary small" data-decision="approved" data-id="${p.id}">Approve proposal</button>` : p.status === 'approved' ? `<button class="button orange small" data-execute="${p.id}">Apply approved change ↗</button>` : ''}</div></div></article>`).join('');
  return `<div class="toolbar"><div><h2>A second pair of eyes. Yours.</h2><p>${pending.length} proposals awaiting a decision or execution. Approval never applies a change automatically.</p></div><button class="button" data-view="catalog">← Back to catalog</button></div><div class="notice">A product change invalidates other proposals based on its old version. Run a fresh analysis if an earlier proposal becomes stale.</div>${pending.length ? cards(pending) : '<div class="empty"><h2>Nothing waiting on your desk.</h2><p>Analyze products in the catalog to create your first set of proposals.</p></div>'}${completed.length ? `<div class="toolbar"><h2>Past decisions</h2></div>${cards(completed)}` : ''}`;
}
function activityView() {
  const operations = state.history.operations;
  return `<div class="toolbar"><div><h2>Every change, accounted for.</h2><p>Persistent run records, operation results and workspace events.</p></div><a class="button" href="/api/export" download>Export JSON ↓</a></div><div class="table-wrap"><table><thead><tr><th>Analysis run</th><th>Time</th><th>Status</th><th>Usage / result</th></tr></thead><tbody>${state.history.runs.map((r) => { let usage={}; try{usage=JSON.parse(r.usage);}catch{} return `<tr><td><code>${esc(r.id.slice(0,12))}</code></td><td>${date(r.created_at)}</td><td>${status(r.status)}</td><td>${r.error ? esc(r.error) : `${usage.input_tokens || 0} input / ${usage.output_tokens || 0} output tokens · ${esc(usage.provider || 'demo')}`}</td></tr>`; }).join('') || '<tr><td colspan="4">No analysis runs yet.</td></tr>'}</tbody></table></div><div class="toolbar"><h2>Execution ledger</h2></div><div class="table-wrap"><table><thead><tr><th>Operation</th><th>Status</th><th>Recorded result</th></tr></thead><tbody>${operations.map((op) => {let result={};try{result=JSON.parse(op.result);}catch{} return `<tr><td><code>${esc(op.id.slice(0,12))}</code><br>${date(op.created_at)}</td><td>${status(op.status)}</td><td>${esc(result.message || 'Execution may still be running. If interrupted, verify the outcome and reconcile after one minute.')}${['unknown','executing'].includes(op.status) ? `<br><button class="button small" data-reconcile="${op.id}">Resolve after verification</button>` : ''}${result.code ? '<br><code>'+esc(result.code)+'</code>' : ''}</td></tr>`;}).join('') || '<tr><td colspan="3">No executions recorded yet.</td></tr>'}</tbody></table></div><div class="toolbar"><h2>Workspace events</h2></div><div class="table-wrap"><table><thead><tr><th>Event</th><th>Time</th><th>Details</th></tr></thead><tbody>${state.history.events.map((e) => `<tr><td>${esc(e.event.replaceAll('_',' '))}</td><td>${date(e.created_at)}</td><td><details><summary>View record</summary><pre class="history-detail">${esc(e.detail)}</pre></details></td></tr>`).join('')}</tbody></table></div>`;
}
function render() {
  document.querySelectorAll('.nav-item[data-view]').forEach((b) => b.classList.toggle('active', b.dataset.view === state.view));
  $('#breadcrumb').textContent = {catalog:'Product catalog',review:'Review queue',activity:'Run history'}[state.view];
  $('#catalog-count').textContent = state.products.length;
  $('#review-count').textContent = state.proposals.filter((p) => p.status === 'proposed').length;
  $('#auth-button').textContent = state.user ? state.user.name.split(' ')[0] : 'Sign in';
  $('#account-name').textContent = state.user?.name || 'Your workspace';
  $('#avatar').textContent = state.user ? state.user.name.slice(0,2).toUpperCase() : 'CS';
  $('#mode-badge').textContent = state.config?.mode.llm === 'openai' ? 'OpenAI connected' : 'Demo workspace';
  if (!state.user) {
    $('#content').innerHTML = `<div class="welcome-grid"><div class="welcome-card"><p class="eyebrow">A WORKSPACE OF YOUR OWN</p><h2>Meet your catalog's<br>thoughtful assistant.</h2><p>Start with a sample collection, add your own products, and see what could be better. Nothing changes until you say so.</p><button class="button primary" id="welcome-start">Create your workspace ↗</button></div><div class="welcome-points"><p><span>01</span> Analyze details and observed prices</p><p><span>02</span> Compare exact before-and-after proposals</p><p><span>03</span> Approve, preview, then apply a change</p><p><span>04</span> Keep a lasting record of every decision</p></div></div>`;
    $('#welcome-start').onclick = () => auth(true); return;
  }
  $('#content').innerHTML = state.view === 'catalog' ? catalogView() : state.view === 'review' ? reviewView() : activityView();
  if ($('#add-product')) $('#add-product').onclick = () => productForm();
  if ($('#analyze')) $('#analyze').onclick = async (event) => action(event.currentTarget, async () => {
    const ids = state.selected.size ? [...state.selected] : state.products.map((p) => p.id);
    if (!ids.length) throw new Error('Add a product before analysis.');
    const run = await post('/runs', {product_ids:ids}); state.view='review'; await refresh();
    toast(run.status === 'completed' ? 'Analysis complete. Your proposals are ready for review.' : run.error, run.status !== 'completed');
  });
}
async function refresh() {
  if (!state.user) state.user = await api('/me');
  [state.products,state.proposals,state.history] = await Promise.all([api('/products'),api('/proposals'),api('/history')]);
  render();
}
function productForm(product = null) {
  dialog(`<p class="eyebrow">PRODUCT DETAILS</p><h2>${product ? 'A little more detail.' : 'Add to the shelf.'}</h2><form id="product-form"><label class="field">Product name<input name="title" required maxlength="160" value="${esc(product?.title || '')}"></label><label class="field">Description<textarea name="description" required maxlength="6000">${esc(product?.description || '')}</textarea></label><div class="form-row"><label class="field">Price<input name="price" type="number" min="0.01" step="0.01" max="100000" required value="${product?.price || ''}"></label><label class="field">Currency<input name="currency" pattern="[A-Z]{3}" maxlength="3" required value="${esc(product?.currency || 'USD')}"></label></div><label class="field">Recorded units in stock<input name="inventory" type="number" min="0" max="1000000" step="1" required value="${product?.inventory ?? 0}"></label>${product?.origin === 'shopify' ? '<p>Editing these details updates the local workspace only. Live description changes require a new approved proposal.</p>' : ''}<div class="form-error" id="form-error" role="alert"></div><button class="button primary full">Save product</button></form>`);
  $('#product-form').onsubmit = async (event) => {
    event.preventDefault(); const form=event.currentTarget; const button=form.querySelector('button');button.disabled=true;
    const data=Object.fromEntries(new FormData(form));data.price=Number(data.price);data.inventory=Number(data.inventory);
    if(product) data.expected_version=product.version;
    try {await api('/products'+(product ? '/'+product.id : ''), {method:product?'PUT':'POST',body:JSON.stringify(data)});close();await refresh();toast('Product saved.');}
    catch(error){$('#form-error').textContent=error.message;}finally{button.disabled=false;}
  };
}
function evidenceForm(product) {
  dialog(`<p class="eyebrow">PRICE EVIDENCE</p><h2>${esc(product.title)}</h2><p>Record a matching product's observed price. Include a source and check the currency. Only observations from the past seven days inform promotions.</p>${product.evidence.length ? `<div class="evidence-list">${product.evidence.map((e) => `${e.is_sample ? 'SAMPLE' : 'OBSERVED'} · ${esc(e.source)} · ${money(e.price,e.currency)}<br>${date(e.observed_at)}`).join('<hr>')}</div>` : ''}<form id="evidence-form"><label class="field">Source URL or reference<input name="source" required minlength="3" maxlength="500" placeholder="Store and exact product URL"></label><div class="form-row"><label class="field">Observed price<input name="price" type="number" min="0.01" step="0.01" max="100000" required></label><label class="field">Currency<input name="currency" pattern="[A-Z]{3}" maxlength="3" value="${esc(product.currency)}" required></label></div><label class="field">When observed, in your local time<input name="observed" type="datetime-local" required></label><div class="form-error" id="form-error" role="alert"></div><button class="button primary full">Record observation</button></form>`);
  const local = new Date(Date.now() - new Date().getTimezoneOffset()*60000).toISOString().slice(0,16); $('input[name="observed"]').value=local;
  $('#evidence-form').onsubmit=async(event)=>{event.preventDefault();const form=event.currentTarget;const button=form.querySelector('button');button.disabled=true;const data=Object.fromEntries(new FormData(form));data.price=Number(data.price);data.observed_at=new Date(data.observed).toISOString();delete data.observed;
    try{await post('/products/'+product.id+'/evidence',data);close();await refresh();toast('Observation recorded. Earlier proposals now require fresh analysis.');}catch(error){$('#form-error').textContent=error.message;}finally{button.disabled=false;}};
}
function confirmExecution(id) {
  const proposal=state.proposals.find((p)=>p.id===id); const product=state.products.find((p)=>p.id===proposal.product);
  dialog(`<p class="eyebrow">EXPLICIT EXECUTION</p><h2>Apply this approved change?</h2><p><strong>${esc(proposal.title)}</strong><br>${esc(proposal.after_text)}</p><div class="notice">${product.origin==='shopify' ? 'This targets your live Shopify store. Server configuration must allow writes.' : 'This updates your local demo workspace.'} A changed product version will block execution.</div><div class="actions"><button class="button" id="cancel-execution">Go back</button><button class="button orange" id="confirm-execution">Apply approved change</button></div>`);
  $('#cancel-execution').onclick=close;
  $('#confirm-execution').onclick=async(event)=>action(event.currentTarget,async()=>{const result=await post('/proposals/'+id+'/execute',{dry_run:false,idempotency_key:'apply_'+id});close();await refresh();toast(result.message,['failed','unknown'].includes(result.status));});
}
function reconcile(id) {
  dialog(`<p class="eyebrow">MANUAL RECONCILIATION</p><h2>Verify before resolving.</h2><p>Inspect the local catalog or the live Shopify store. Confirm whether this exact change happened. This records your finding and releases the product; it does not retry a write.</p><form id="reconcile-form"><label class="field">Verified outcome<select name="outcome"><option value="no">Change was not applied</option><option value="yes">Change was applied</option></select></label><label class="field">What did you verify?<textarea name="note" minlength="10" maxlength="1000" required></textarea></label><div class="form-error" id="form-error" role="alert"></div><button class="button primary full">Record verified outcome</button></form>`);
  $('#reconcile-form').onsubmit=async(event)=>{event.preventDefault();const data=Object.fromEntries(new FormData(event.currentTarget));try{await post('/operations/'+id+'/reconcile',{confirmed_applied:data.outcome==='yes',note:data.note});close();await refresh();toast('Verified outcome recorded.');}catch(error){$('#form-error').textContent=error.message;}};
}
document.addEventListener('click', async (event) => {
  const button=event.target.closest('button');if(!button)return;
  if(button.dataset.view){state.view=button.dataset.view;render();}
  if(button.dataset.edit)productForm(state.products.find((p)=>p.id===button.dataset.edit));
  if(button.dataset.evidence)evidenceForm(state.products.find((p)=>p.id===button.dataset.evidence));
  if(button.dataset.decision)await action(button,async()=>{await post('/proposals/'+button.dataset.id+'/decision',{decision:button.dataset.decision});await refresh();toast(button.dataset.decision==='approved'?'Proposal approved. Apply it separately when ready.':'Proposal rejected.');});
  if(button.dataset.preview)await action(button,async()=>{const result=await post('/proposals/'+button.dataset.preview+'/execute',{dry_run:true,idempotency_key:'preview_'+button.dataset.preview});toast(`Dry run: ${result.writes} writes. ${result.stale?'Product changed; create a fresh analysis.':result.approved?'Approved and ready for explicit execution.':'Approval required before execution.'}`);});
  if(button.dataset.execute)confirmExecution(button.dataset.execute);
  if(button.dataset.reconcile)reconcile(button.dataset.reconcile);
});
document.addEventListener('change',(event)=>{if(event.target.dataset.select){const id=event.target.dataset.select;if(event.target.checked)state.selected.add(id);else state.selected.delete(id);render();}});
$('#close-modal').onclick=close;$('#auth-button').onclick=()=>state.user?account():auth();$('#account-button').onclick=account;$('#settings-button').onclick=settings;
(async()=>{try{state.config=await api('/config');try{await refresh();}catch(error){if(error.message!=='Sign in to continue.'&&!error.message.includes('session expired'))throw error;render();}}catch(error){render();toast(error.message,true);}})();
