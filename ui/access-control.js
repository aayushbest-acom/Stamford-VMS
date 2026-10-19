/* Stamford VMS local account authentication and feature permissions UI. */
(function () {
  'use strict';
  const AUTH_BASE = '/api/v1/auth';
  const ACCESS_BASE = '/api/v1/access';
  const PERMISSION_VIEW = {
    overview: ['dashboard.view'], visits: ['visits.view', 'visits.create'], 'visitor-types': ['visitor_types.view'],
    reports: ['reports.view'], attendance: ['attendance.view'], kiosk: ['visits.kiosk'], groups: ['groups.view'],
    cards: ['cards.view'], integrations: ['integrations.view'], genetec: ['genetec.view'], events: ['events.view'],
    audit: ['audit.view'], accounts: ['accounts.manage', 'permissions.manage']
  };
  let auth = null;
  let csrfToken = '';
  let started = false;
  let startCallback = null;
  let permissionCatalog = [];
  let accountCache = [];
  let groupCache = [];
  let selectedAccountId = '';
  let selectedGroupId = 'receptionist';
  let originalFetch = window.fetch.bind(window);

  function safe(value) {
    return String(value == null ? '' : value).replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  }
  function can(permission) { return !!(auth && Array.isArray(auth.permissions) && auth.permissions.includes(permission)); }
  function canAny(permissions) { return permissions.some(can); }
  function isSignedIn() { return !!auth; }
  function setCsrf(value) { csrfToken = typeof value === 'string' ? value : ''; }
  function setAuth(payload) {
    auth = payload ? { user: payload.user, groups: payload.groups || [], permissions: payload.permissions || [] } : null;
    setCsrf(payload && payload.csrfToken);
    updateHeader();
    applyPermissions();
  }
  function updateHeader() {
    const name = auth && auth.user ? auth.user.displayName : 'Signed out';
    const role = auth && auth.groups && auth.groups.length ? auth.groups.map(g => g.name).join(' · ') : 'No active session';
    const nameNode = document.querySelector('.profile-name');
    const roleNode = document.querySelector('.profile-role');
    const avatar = document.querySelector('.profile .avatar');
    if (nameNode) nameNode.textContent = name;
    if (roleNode) roleNode.textContent = role;
    if (avatar) avatar.textContent = auth && auth.user ? initials(auth.user.displayName) : '—';
    if (auth) {
      try { localStorage.removeItem('stamford-vms-demo-state-v1'); } catch (_) {}
      let logout = document.getElementById('authLogoutButton');
      if (!logout) {
        logout = document.createElement('button'); logout.id = 'authLogoutButton'; logout.className = 'icon-btn';
        logout.type = 'button'; logout.title = 'Sign out'; logout.setAttribute('aria-label', 'Sign out'); logout.textContent = '↪';
        logout.addEventListener('click', logoutNow);
        const top = document.querySelector('.top-actions'); if (top) top.appendChild(logout);
      }
    } else document.getElementById('authLogoutButton')?.remove();
  }
  function initials(name) { return String(name || '?').trim().split(/\s+/).slice(0,2).map(x => x[0] || '').join('').toUpperCase(); }

  // Attach CSRF to all state-changing requests; the server also checks permissions for every API call.
  window.fetch = function (input, init) {
    const options = Object.assign({}, init || {});
    const method = String(options.method || (input instanceof Request ? input.method : 'GET')).toUpperCase();
    const rawUrl = typeof input === 'string' ? input : (input && input.url) || '';
    let pathname = '';
    try { pathname = new URL(rawUrl, window.location.href).pathname; } catch (_) { pathname = String(rawUrl).split('?')[0]; }
    const headers = new Headers(options.headers || (input instanceof Request ? input.headers : undefined));
    if (csrfToken && ['POST','PUT','PATCH','DELETE'].includes(method) && pathname !== AUTH_BASE + '/login' && pathname !== AUTH_BASE + '/bootstrap') {
      headers.set('X-Stamford-CSRF', csrfToken);
    }
    options.headers = headers;
    options.credentials = 'same-origin';
    return originalFetch(input, options);
  };

  function injectUi() {
    if (document.getElementById('stamfordAuthGate')) return;
    const style = document.createElement('style');
    style.id = 'stamfordAccessStyle';
    style.textContent = `
      body.auth-gated .layout{visibility:hidden;pointer-events:none}#stamfordAuthGate{position:fixed;inset:0;z-index:20000;display:grid;place-items:center;background:linear-gradient(135deg,#0e283f 0%,#153f5e 70%,#0b7778 140%);padding:20px;color:#193246}#stamfordAuthGate[hidden]{display:none}.auth-card{width:min(100%,510px);background:#fff;border-radius:18px;padding:30px;box-shadow:0 30px 90px #04182770}.auth-brand{background:#fff;text-align:center;padding:8px;margin-bottom:20px}.auth-brand img{max-width:310px;width:100%;height:auto}.auth-kicker{font-size:10px;letter-spacing:1.5px;text-transform:uppercase;color:#168780;font-weight:850;margin-bottom:7px}.auth-title{font-size:25px;line-height:1.2;letter-spacing:-.5px;margin:0;color:#16364e}.auth-description{font-size:12px;color:#718595;line-height:1.6;margin:9px 0 20px}.auth-form{display:grid;gap:13px}.auth-field{display:grid;gap:5px}.auth-field label{font-size:11px;font-weight:750;color:#3f5b6e}.auth-field input{width:100%;border:1px solid #dce6ec;border-radius:9px;padding:11px 12px;color:#16364e;background:#fbfdfe}.auth-error{font-size:11px;color:#a33232;background:#fff0ef;border:1px solid #f1d4d2;border-radius:8px;padding:10px;display:none}.auth-foot{font-size:10px;color:#8495a2;line-height:1.5;margin-top:16px}.auth-user-chip{display:flex;align-items:center;gap:12px;background:#f4f8fa;border:1px solid #e1ebef;border-radius:10px;padding:11px;margin-bottom:12px}.auth-user-chip .avatar{flex:none}.access-layout{display:grid;grid-template-columns:minmax(0,1fr) minmax(0,1fr);gap:16px;align-items:start}.access-full{grid-column:1/-1}.access-form-grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:12px}.access-field{display:grid;gap:5px}.access-field label{font-size:11px;font-weight:750;color:#4a6576}.access-field input,.access-field select,.access-field textarea{width:100%;border:1px solid #dce6ec;border-radius:8px;padding:9px 10px;background:#fff;color:#1b3c52}.access-checks{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:8px;max-height:245px;overflow:auto;padding:10px;border:1px solid #e5edf1;border-radius:9px}.access-check{display:flex;gap:8px;align-items:flex-start;font-size:11px;color:#496375;line-height:1.35}.access-check input{margin-top:2px}.permission-list{display:grid;gap:0}.permission-row{display:grid;grid-template-columns:minmax(0,1fr) 125px 72px;align-items:center;gap:9px;padding:8px 0;border-bottom:1px solid #edf1f4}.permission-row:last-child{border-bottom:0}.permission-row strong{font-size:11px;color:#2b495d}.permission-row small{display:block;color:#8a9aa6;font-size:10px;margin-top:2px}.permission-row select{font-size:10px;padding:6px;border:1px solid #dae4e9;border-radius:7px;background:white}.permission-badge{font-size:9px;font-weight:800;padding:4px 6px;border-radius:999px;text-align:center}.permission-badge.allowed{background:#e6f6ef;color:#187454}.permission-badge.denied{background:#fff0ef;color:#a13c39}.permission-badge.inherited{background:#edf2f6;color:#647b8b}.access-table-wrap{overflow:auto}.access-table{width:100%;border-collapse:collapse;white-space:nowrap}.access-table th{font-size:9px;text-transform:uppercase;letter-spacing:.8px;text-align:left;background:#f8fafb;color:#8295a2;padding:10px;border-bottom:1px solid #eaf0f3}.access-table td{font-size:11px;color:#4d6576;padding:11px 10px;border-bottom:1px solid #eff3f5}.access-section-hint{font-size:11px;color:#7c909e;line-height:1.5}.auth-loading{color:#e4f4f8;font-size:12px;letter-spacing:.4px}@media(max-width:900px){.access-layout{grid-template-columns:1fr}.access-full{grid-column:auto}}@media(max-width:560px){.auth-card{padding:22px}.access-form-grid{grid-template-columns:1fr}.access-checks{grid-template-columns:1fr}.permission-row{grid-template-columns:minmax(0,1fr) 108px 65px;gap:5px}}`;
    document.head.appendChild(style);
    const gate = document.createElement('div'); gate.id = 'stamfordAuthGate'; gate.innerHTML = '<div class="auth-loading">Checking Stamford VMS account session…</div>';
    document.body.appendChild(gate);
    document.body.classList.add('auth-gated');
    const nav = document.getElementById('nav');
    if (nav && !nav.querySelector('[data-view="accounts"]')) {
      const button = document.createElement('button'); button.dataset.view = 'accounts'; button.innerHTML = '<span class="nav-icon">♙</span><span class="nav-label-text">Accounts & permissions</span>';
      button.addEventListener('click', () => window.navigate && window.navigate('accounts'));
      nav.appendChild(button);
    }
    const content = document.querySelector('.content');
    if (content && !document.getElementById('view-accounts')) {
      const section = document.createElement('section'); section.className = 'view'; section.id = 'view-accounts';
      section.innerHTML = `
        <div class="page-head"><div><div class="eyebrow">Security & governance</div><h1 class="h1">Accounts & permissions</h1><p class="subtitle">Create local user accounts, assign one or more role groups, and grant or deny individual feature permissions. Changes are enforced by the local API, not just hidden in the interface.</p></div><div class="head-actions"><button class="btn secondary" id="accessRefreshButton" type="button">↻ Refresh</button></div></div>
        <div class="banner warning"><span class="banner-icon">♙</span><div><strong>Local prototype identity store</strong><p>Passwords use PBKDF2 hashes and sessions expire after eight hours. All API features are denied unless the signed-in account has permission. This remains a loopback-only prototype, not an enterprise identity provider; use synthetic data only.</p></div></div>
        <div class="access-layout section-space">
          <div class="panel"><div class="panel-head"><div><h2 class="panel-title">Create account</h2><div class="panel-sub">Assign a preset role; fine-tune individual features below.</div></div></div><div class="panel-body"><form id="accessCreateAccountForm" class="access-form-grid"><div class="access-field"><label for="accessNewUsername">Username *</label><input required id="accessNewUsername" autocomplete="off" minlength="3" maxlength="64" pattern="[A-Za-z0-9][A-Za-z0-9._-]{2,63}"></div><div class="access-field"><label for="accessNewDisplayName">Display name *</label><input required id="accessNewDisplayName" maxlength="100"></div><div class="access-field access-full"><label for="accessNewPassword">Initial password * (12+ characters)</label><input required id="accessNewPassword" type="password" minlength="12" maxlength="256" autocomplete="new-password"></div><div class="access-field access-full"><label>Initial role group(s) *</label><div id="accessCreateGroupChecks" class="access-checks">Loading groups…</div></div><div class="access-field access-full"><button class="btn primary" type="submit">＋ Create account</button><div id="accessCreateStatus" class="access-section-hint" aria-live="polite">No passwords are displayed after the account is created.</div></div></form></div></div>
          <div class="panel"><div class="panel-head"><div><h2 class="panel-title">Create permission group</h2><div class="panel-sub">Make custom groups for school-specific job duties.</div></div></div><div class="panel-body"><form id="accessCreateGroupForm" class="access-form-grid"><div class="access-field access-full"><label for="accessNewGroupName">Group name *</label><input required id="accessNewGroupName" maxlength="80" placeholder="e.g. Admissions Team"></div><div class="access-field access-full"><label for="accessNewGroupDescription">Description</label><input id="accessNewGroupDescription" maxlength="240" placeholder="Purpose of the group"></div><div class="access-field access-full"><button class="btn primary" type="submit">＋ Create group</button><div id="accessCreateGroupStatus" class="access-section-hint" aria-live="polite">A new group starts with no permissions until configured.</div></div></form></div></div>
          <div class="panel access-full"><div class="panel-head"><div><h2 class="panel-title">Accounts</h2><div class="panel-sub" id="accessAccountStatus">Loading accounts…</div></div></div><div class="access-table-wrap"><table class="access-table"><thead><tr><th>Account</th><th>Role groups</th><th>Status</th><th>Permissions</th><th>Actions</th></tr></thead><tbody id="accessAccountsTable"></tbody></table></div></div>
          <div class="panel"><div class="panel-head"><div><h2 class="panel-title">Group permissions</h2><div class="panel-sub">Set the baseline permissions inherited by every member of a group.</div></div></div><div class="panel-body"><div class="access-field"><label for="accessSelectedGroup">Permission group</label><select id="accessSelectedGroup"></select></div><div id="accessSelectedGroupMeta" class="access-section-hint" style="margin:9px 0"></div><div id="accessGroupPermissionChecks" class="access-checks"></div><button class="btn primary" id="accessSaveGroupPermissions" style="margin-top:12px" type="button">Save group permissions</button><div id="accessGroupPermissionStatus" class="access-section-hint" aria-live="polite" style="margin-top:8px"></div></div></div>
          <div class="panel"><div class="panel-head"><div><h2 class="panel-title">Individual account access</h2><div class="panel-sub">An explicit Allow or Deny overrides group inheritance for that account.</div></div></div><div class="panel-body"><div class="access-field"><label for="accessSelectedAccount">Account</label><select id="accessSelectedAccount"></select></div><div class="access-field" style="margin-top:10px"><label>Role group memberships</label><div id="accessAccountGroupChecks" class="access-checks"></div><button class="btn secondary small" id="accessSaveAccountGroups" style="margin-top:8px" type="button">Save memberships</button></div><div id="accessOverrideRows" class="permission-list" style="margin-top:14px"></div><button class="btn primary" id="accessSaveOverrides" style="margin-top:12px" type="button">Save individual permissions</button><div id="accessOverrideStatus" class="access-section-hint" aria-live="polite" style="margin-top:8px"></div></div></div>
          <div class="panel access-full"><div class="panel-head"><div><h2 class="panel-title">Access-control audit</h2><div class="panel-sub">Successful/failed sign-in events and account/group/permission changes.</div></div></div><div class="access-table-wrap"><table class="access-table"><thead><tr><th>Timestamp (UTC)</th><th>Actor</th><th>Action</th><th>Target</th></tr></thead><tbody id="accessAuditTable"></tbody></table></div></div>
        </div>`;
      content.appendChild(section);
    }
    document.getElementById('accessRefreshButton')?.addEventListener('click', refreshAdminData);
    document.getElementById('accessCreateAccountForm')?.addEventListener('submit', createAccount);
    document.getElementById('accessCreateGroupForm')?.addEventListener('submit', createGroup);
    document.getElementById('accessSelectedGroup')?.addEventListener('change', renderSelectedGroup);
    document.getElementById('accessSelectedAccount')?.addEventListener('change', renderSelectedAccount);
    document.getElementById('accessSaveGroupPermissions')?.addEventListener('click', saveGroupPermissions);
    document.getElementById('accessSaveAccountGroups')?.addEventListener('click', saveAccountGroups);
    document.getElementById('accessSaveOverrides')?.addEventListener('click', saveAccountOverrides);
  }

  function showGate(setupRequired, message) {
    document.body.classList.add('auth-gated');
    const gate = document.getElementById('stamfordAuthGate');
    gate.hidden = false;
    const isSetup = !!setupRequired;
    gate.innerHTML = `<div class="auth-card"><div class="auth-brand"><img src="assets/stamford-school-logo.png" alt="Stamford American International School"></div><div class="auth-kicker">Visitor Management System</div><h1 class="auth-title">${isSetup ? 'Set up your administrator' : 'Sign in to Stamford VMS'}</h1><p class="auth-description">${isSetup ? 'Create the first administrator account. This one-time setup closes permanently after the first account is created.' : 'Use your school-approved prototype account. Your access to every feature is determined by administrator-assigned permissions.'}</p><form class="auth-form" id="stamfordAuthForm">${isSetup ? '<div class="auth-field"><label for="authDisplayName">Administrator display name</label><input required id="authDisplayName" maxlength="100" autocomplete="name" value="VMS Administrator"></div>' : ''}<div class="auth-field"><label for="authUsername">Username</label><input required id="authUsername" maxlength="64" autocomplete="username" autofocus></div><div class="auth-field"><label for="authPassword">Password</label><input required id="authPassword" type="password" maxlength="256" autocomplete="${isSetup ? 'new-password' : 'current-password'}" ${isSetup ? 'minlength="12"' : ''}></div><div class="auth-error" id="stamfordAuthError" role="alert">${safe(message || '')}</div><button class="btn primary" type="submit">${isSetup ? 'Create administrator' : 'Sign in'}</button></form><div class="auth-foot">Local prototype only · Service bound to loopback · Do not use real student/visitor data until security review and production identity controls are complete.</div></div>`;
    const error = document.getElementById('stamfordAuthError'); if (message && error) error.style.display = 'block';
    document.getElementById('stamfordAuthForm').addEventListener('submit', async e => {
      e.preventDefault(); const button = e.currentTarget.querySelector('button[type="submit"]'); button.disabled = true; button.textContent = 'Please wait…';
      const body = { username: document.getElementById('authUsername').value, password: document.getElementById('authPassword').value };
      let path = AUTH_BASE + '/login';
      if (isSetup) { body.displayName = document.getElementById('authDisplayName').value; path = AUTH_BASE + '/bootstrap'; }
      try {
        const response = await originalFetch(path, {method:'POST', headers:{'Content-Type':'application/json'}, credentials:'same-origin', cache:'no-store', body:JSON.stringify(body)});
        const data = await response.json();
        if (!response.ok || data.ok !== true) throw new Error(data.message || data.error || 'Authentication failed.');
        setAuth(data); gate.hidden = true; document.body.classList.remove('auth-gated');
        applyPermissions();
        if (startCallback && !started) { started = true; await startCallback(); applyPermissions(); }
        else { window.location.reload(); }
      } catch (err) {
        const ebox = document.getElementById('stamfordAuthError'); if (ebox) { ebox.textContent = String(err.message || err); ebox.style.display = 'block'; }
        button.disabled = false; button.textContent = isSetup ? 'Create administrator' : 'Sign in';
      }
    });
    document.getElementById('authUsername')?.focus();
  }

  async function logoutNow() {
    try { await originalFetch(AUTH_BASE + '/logout', {method:'POST', headers:{'Content-Type':'application/json','X-Stamford-CSRF':csrfToken}, credentials:'same-origin', body:'{}'}); } catch (_) {}
    auth = null; setCsrf('');
    try { localStorage.removeItem('stamford-vms-demo-state-v1'); } catch (_) {}
    window.location.reload();
  }

  function applyPermissions() {
    if (!auth) return;
    document.querySelectorAll('#nav button[data-view]').forEach(button => {
      const view = button.dataset.view;
      const requirements = view === 'accounts' ? ['accounts.manage','permissions.manage'] : (PERMISSION_VIEW[view] || []);
      const allowed = requirements.length > 0 && canAny(requirements);
      button.hidden = !allowed;
      button.setAttribute('aria-hidden', allowed ? 'false' : 'true');
    });
    document.querySelectorAll('.view[id^="view-"]').forEach(section => {
      const view = section.id.slice(5);
      const requirements = view === 'accounts' ? ['accounts.manage','permissions.manage'] : (PERMISSION_VIEW[view] || []);
      section.hidden = !requirements.length || !canAny(requirements);
      section.classList.toggle('rbac-hidden', section.hidden);
    });
    const layout = document.querySelector('.layout'); if (layout) layout.style.visibility = 'visible';
    const current = document.querySelector('.view.active');
    if (!current || current.hidden) {
      const first = [...document.querySelectorAll('#nav button[data-view]')].find(b => !b.hidden);
      if (first && window.navigate) window.navigate(first.dataset.view);
    }
    const reportBanner = document.querySelector('#view-reports .banner p');
    if (reportBanner) reportBanner.textContent = 'Reports are generated from local prototype databases. Sensitive QR tokens, raw credentials and biometric data are excluded. Snapshot-based reports are labelled. Report preview and export are separately permission-controlled.';
    const visitorTypeBanner = document.querySelector('#view-visitor-types .banner p');
    if (visitorTypeBanner) visitorTypeBanner.textContent = 'Only authorised accounts can create, rename or deactivate visitor types. API-side permissions are enforced in addition to this interface.';
    const globalSearch = document.getElementById('globalSearch');
    if (globalSearch && !can('visits.view')) globalSearch.disabled = true;
  }

  async function api(path, method='GET', body=undefined) {
    const options = {method, cache:'no-store', credentials:'same-origin'};
    if (body !== undefined) { options.headers = {'Content-Type':'application/json'}; options.body = JSON.stringify(body); }
    const response = await fetch(path, options);
    let data = {}; try { data = await response.json(); } catch (_) {}
    if (!response.ok || data.ok === false) throw new Error(data.message || data.error || `Request failed (${response.status}).`);
    return data;
  }
  function toastSafe(title, message, bad=false) { if (typeof window.toast === 'function') window.toast(title, message, bad ? '!' : undefined); }

  async function refreshAdminData() {
    if (!can('accounts.manage') || !can('permissions.manage')) return;
    const status = document.getElementById('accessAccountStatus'); if (status) status.textContent = 'Loading account and group lists…';
    try {
      const [accounts, groups, catalog, audit] = await Promise.all([
        api(ACCESS_BASE + '/accounts'), api(ACCESS_BASE + '/groups'), api(ACCESS_BASE + '/permission-catalog'), api(ACCESS_BASE + '/audit')
      ]);
      accountCache = accounts.accounts || []; groupCache = groups.groups || []; permissionCatalog = catalog.permissions || [];
      if (!accountCache.some(a => String(a.accountId) === String(selectedAccountId))) selectedAccountId = String(accountCache[0]?.accountId || '');
      if (!groupCache.some(g => g.groupId === selectedGroupId)) selectedGroupId = groupCache[0]?.groupId || '';
      renderAccounts(); renderGroupSelectors(); renderSelectedGroup(); renderSelectedAccount(); renderSecurityAudit(audit.events || []);
      if (status) status.textContent = `${accountCache.length} account(s) · ${groupCache.length} groups · permissions change is enforced immediately on the next request.`;
    } catch (err) {
      if (status) status.textContent = String(err.message || err);
      toastSafe('Access management failed', String(err.message || err), true);
    }
  }
  function renderAccounts() {
    const table = document.getElementById('accessAccountsTable'); if (!table) return;
    table.innerHTML = accountCache.map(account => `<tr><td><strong>${safe(account.displayName)}</strong><br><span style="color:#8b9ca7">@${safe(account.username)}</span></td><td>${safe((account.groups||[]).map(g=>g.name).join(', '))}</td><td><span class="pill ${account.isActive?'success':'danger'}">${account.isActive?'Active':'Disabled'}</span></td><td>${(account.effectivePermissions||[]).length} permission(s)</td><td><button class="btn secondary small" type="button" data-account-edit="${account.accountId}">Permissions</button> <button class="btn ${account.isActive?'danger':'secondary'} small" type="button" data-account-toggle="${account.accountId}" data-active="${account.isActive?'1':'0'}">${account.isActive?'Disable':'Enable'}</button> <button class="btn secondary small" type="button" data-account-reset="${account.accountId}">Reset password</button></td></tr>`).join('') || '<tr><td colspan="5">No accounts.</td></tr>';
    table.querySelectorAll('[data-account-edit]').forEach(b => b.addEventListener('click',()=>{selectedAccountId=b.dataset.accountEdit; const sel=document.getElementById('accessSelectedAccount'); if(sel)sel.value=selectedAccountId; renderSelectedAccount(); document.getElementById('accessSelectedAccount')?.scrollIntoView({behavior:'smooth',block:'center'});}));
    table.querySelectorAll('[data-account-toggle]').forEach(b => b.addEventListener('click',async()=>{const accountId=Number(b.dataset.accountToggle), active=b.dataset.active!=='1'; if(!active && !confirm('Enable this account?'))return; if(active && !confirm('Disable this account? Existing sessions will stop working immediately.'))return; try{await api(`${ACCESS_BASE}/accounts/${accountId}/update`,'POST',{isActive:!active});await refreshAdminData();toastSafe('Account updated',`Account ${!active?'enabled':'disabled'}.`);}catch(e){toastSafe('Account update failed',e.message,true);}}));
    table.querySelectorAll('[data-account-reset]').forEach(b => b.addEventListener('click',async()=>{const pw=prompt('Enter a new temporary password (12–256 characters). The user’s existing sessions will be revoked.');if(pw===null)return;try{await api(`${ACCESS_BASE}/accounts/${Number(b.dataset.accountReset)}/reset-password`,'POST',{password:pw});toastSafe('Password reset','Password changed and sessions revoked. Share the new password securely.');}catch(e){toastSafe('Password reset failed',e.message,true);}}));
  }
  function renderGroupSelectors() {
    const createChecks = document.getElementById('accessCreateGroupChecks');
    if (createChecks) createChecks.innerHTML = groupCache.map(g=>`<label class="access-check"><input type="checkbox" name="new-account-group" value="${safe(g.groupId)}" ${g.groupId==='receptionist'?'checked':''}><span><strong>${safe(g.name)}</strong><br>${safe(g.description||'')}</span></label>`).join('');
    const groupSelect = document.getElementById('accessSelectedGroup');
    if (groupSelect) { const previous=groupSelect.value||selectedGroupId; groupSelect.innerHTML=groupCache.map(g=>`<option value="${safe(g.groupId)}">${safe(g.name)}</option>`).join(''); groupSelect.value=groupCache.some(g=>g.groupId===previous)?previous:(groupCache[0]?.groupId||'');selectedGroupId=groupSelect.value; }
    const accountSelect = document.getElementById('accessSelectedAccount');
    if (accountSelect) { const previous=accountSelect.value||selectedAccountId;accountSelect.innerHTML=accountCache.map(a=>`<option value="${a.accountId}">${safe(a.displayName)} (@${safe(a.username)})${a.isActive?'':' — disabled'}</option>`).join('');accountSelect.value=accountCache.some(a=>String(a.accountId)===String(previous))?previous:(accountCache[0]?String(accountCache[0].accountId):'');selectedAccountId=accountSelect.value; }
  }
  function renderSelectedGroup() {
    const select=document.getElementById('accessSelectedGroup'); if(!select)return;selectedGroupId=select.value;
    const group=groupCache.find(g=>g.groupId===selectedGroupId);const meta=document.getElementById('accessSelectedGroupMeta');if(meta)meta.textContent=group?`${group.builtIn?'Built-in role':'Custom group'} · ${group.activeAccountCount} active member(s) · ${group.description}`:'';
    const container=document.getElementById('accessGroupPermissionChecks');if(!container)return;const assigned=new Set(group?.permissions||[]);
    const categories=[...new Set(permissionCatalog.map(p=>p.category))];
    container.innerHTML=categories.map(category=>`<div style="grid-column:1/-1;font-size:9px;font-weight:850;color:#13817c;text-transform:uppercase;letter-spacing:1px;padding:5px 0">${safe(category)}</div>`+permissionCatalog.filter(p=>p.category===category).map(p=>`<label class="access-check"><input type="checkbox" data-group-permission="${safe(p.key)}" ${assigned.has(p.key)?'checked':''}><span><strong>${safe(p.label)}</strong><br>${safe(p.description)}</span></label>`).join('')).join('');
  }
  function renderSelectedAccount() {
    const select=document.getElementById('accessSelectedAccount');if(!select)return;selectedAccountId=select.value;const account=accountCache.find(a=>String(a.accountId)===String(selectedAccountId));const groupBox=document.getElementById('accessAccountGroupChecks');
    if(groupBox){const memberships=new Set((account?.groups||[]).map(g=>g.groupId));groupBox.innerHTML=groupCache.map(g=>`<label class="access-check"><input type="checkbox" data-account-group="${safe(g.groupId)}" ${memberships.has(g.groupId)?'checked':''}><span><strong>${safe(g.name)}</strong><br>${safe(g.description||'')}</span></label>`).join('');}
    const overrides=new Map((account?.overrides||[]).map(o=>[o.permission,o.effect]));const effective=new Set(account?.effectivePermissions||[]);const container=document.getElementById('accessOverrideRows');if(!container)return;
    container.innerHTML=permissionCatalog.map(p=>{const value=overrides.get(p.key)||'inherit';const status=value==='allow'?'Allowed by override':value==='deny'?'Denied by override':effective.has(p.key)?'Allowed via group':'Not granted';const cls=value==='deny'||(!effective.has(p.key)&&value==='inherit')?'denied':value==='allow'||effective.has(p.key)?'allowed':'inherited';return `<div class="permission-row"><div><strong>${safe(p.label)}</strong><small>${safe(p.category)} · ${safe(p.key)}</small></div><select aria-label="${safe(p.label)} override" data-account-override="${safe(p.key)}"><option value="inherit" ${value==='inherit'?'selected':''}>Inherit</option><option value="allow" ${value==='allow'?'selected':''}>Allow</option><option value="deny" ${value==='deny'?'selected':''}>Deny</option></select><span class="permission-badge ${cls}">${safe(status)}</span></div>`;}).join('');
  }
  function renderSecurityAudit(events) {
    const table=document.getElementById('accessAuditTable');if(!table)return;
    table.innerHTML=events.slice(0,100).map(e=>`<tr><td>${safe(e.timestampUtc)}</td><td>${safe(e.actor)}</td><td>${safe(e.action)}</td><td>${safe(e.targetType)} · ${safe(e.targetId)}</td></tr>`).join('')||'<tr><td colspan="4">No access-control audit events yet.</td></tr>';
  }
  async function createAccount(e) {
    e.preventDefault();const form=e.currentTarget;const groupIds=[...form.querySelectorAll('input[name="new-account-group"]:checked')].map(x=>x.value);const status=document.getElementById('accessCreateStatus');
    if(!groupIds.length){if(status)status.textContent='Select at least one group.';return;}
    try{const result=await api(ACCESS_BASE+'/accounts','POST',{username:document.getElementById('accessNewUsername').value,displayName:document.getElementById('accessNewDisplayName').value,password:document.getElementById('accessNewPassword').value,groupIds});form.reset();const reception=form.querySelector('input[value="receptionist"]');if(reception)reception.checked=true;if(status)status.textContent=`Created account @${result.username}. Credentials are not shown again.`;await refreshAdminData();toastSafe('Account created',`Created ${result.username}.`);}catch(err){if(status)status.textContent=String(err.message||err);toastSafe('Account creation failed',String(err.message||err),true);}
  }
  async function createGroup(e) {
    e.preventDefault();const status=document.getElementById('accessCreateGroupStatus');try{const data=await api(ACCESS_BASE+'/groups','POST',{name:document.getElementById('accessNewGroupName').value,description:document.getElementById('accessNewGroupDescription').value});document.getElementById('accessCreateGroupForm').reset();if(status)status.textContent=`Created ${data.name}. It has no permissions until you configure them.`;selectedGroupId=data.groupId;await refreshAdminData();toastSafe('Group created',data.name);}catch(err){if(status)status.textContent=String(err.message||err);toastSafe('Group creation failed',String(err.message||err),true);}
  }
  async function saveGroupPermissions() {
    const status=document.getElementById('accessGroupPermissionStatus');const select=document.getElementById('accessSelectedGroup');if(!select?.value)return;const permissionKeys=[...document.querySelectorAll('[data-group-permission]:checked')].map(x=>x.dataset.groupPermission);
    try{await api(`${ACCESS_BASE}/groups/${encodeURIComponent(select.value)}/permissions`,'POST',{permissionKeys});if(status)status.textContent=`Saved ${permissionKeys.length} group permissions. Existing sessions are re-evaluated on their next API request.`;await refreshAdminData();toastSafe('Group permissions saved',select.options[select.selectedIndex]?.text||'Group');}catch(err){if(status)status.textContent=String(err.message||err);toastSafe('Permission update rejected',String(err.message||err),true);}
  }
  async function saveAccountGroups() {
    const status=document.getElementById('accessOverrideStatus');if(!selectedAccountId)return;const groupIds=[...document.querySelectorAll('[data-account-group]:checked')].map(x=>x.dataset.accountGroup);if(!groupIds.length){if(status)status.textContent='Every account must belong to at least one group.';return;}
    try{await api(`${ACCESS_BASE}/accounts/${Number(selectedAccountId)}/groups`,'POST',{groupIds});if(status)status.textContent='Group memberships saved.';await refreshAdminData();toastSafe('Membership updated','The account permissions have been recalculated.');}catch(err){if(status)status.textContent=String(err.message||err);toastSafe('Membership update rejected',String(err.message||err),true);}
  }
  async function saveAccountOverrides() {
    const status=document.getElementById('accessOverrideStatus');if(!selectedAccountId)return;const overrides=[...document.querySelectorAll('[data-account-override]')].filter(sel=>sel.value!=='inherit').map(sel=>({permission:sel.dataset.accountOverride,effect:sel.value}));
    try{await api(`${ACCESS_BASE}/accounts/${Number(selectedAccountId)}/overrides`,'POST',{overrides});if(status)status.textContent=`Saved ${overrides.length} explicit override(s). Deny overrides group grants; Inherit clears the explicit override.`;await refreshAdminData();toastSafe('Individual permissions saved','Effective permissions are updated immediately.');}catch(err){if(status)status.textContent=String(err.message||err);toastSafe('Permission update rejected',String(err.message||err),true);}
  }
  function showAccountView() { if (window.navigate) window.navigate('accounts'); }
  async function refreshCurrentSession() {
    if (!auth) return;
    try {
      const response = await originalFetch(AUTH_BASE + '/status', {cache:'no-store', credentials:'same-origin'});
      const data = await response.json();
      if (!data.authenticated) {
        auth = null; setCsrf(''); setAuth(null);
        showGate(!!data.setupRequired, 'Your session has expired or your account was disabled. Sign in again.');
        return;
      }
      const oldPermissions = JSON.stringify(auth.permissions || []);
      setAuth(data);
      document.getElementById('stamfordAuthGate').hidden = true;
      document.body.classList.remove('auth-gated');
      if (can('accounts.manage') && can('permissions.manage')) refreshAdminData();
      if (oldPermissions !== JSON.stringify(auth.permissions || [])) toastSafe('Permissions refreshed', 'Your current feature access has been updated by the administrator.');
    } catch (_) { /* A transient status failure must not create a browser-side fallback or alter authorization. */ }
  }

  async function start(startPrototype) {
    injectUi(); startCallback = startPrototype;
    const originalNavigate = window.navigate;
    if (typeof originalNavigate === 'function' && !originalNavigate.__rbacWrapped) {
      const guarded = function(view) {
        const needs = view === 'accounts' ? ['accounts.manage','permissions.manage'] : (PERMISSION_VIEW[view] || []);
        if (!auth || !needs.length || !canAny(needs)) { toastSafe('Access denied','Your account does not have permission to open this feature.',true); return; }
        const result = originalNavigate.call(this, view); if(view==='accounts'){const title=document.getElementById('topTitle');if(title)title.textContent='Accounts & permissions';const crumb=document.querySelector('.crumb');if(crumb)crumb.textContent='Stamford / Accounts & permissions';} return result;
      };
      guarded.__rbacWrapped = true; window.navigate = guarded;
    }
    try {
      const response = await originalFetch(AUTH_BASE + '/status', {cache:'no-store', credentials:'same-origin'});
      const data = await response.json();
      if (data.authenticated) {
        setAuth(data); document.getElementById('stamfordAuthGate').hidden = true; document.body.classList.remove('auth-gated');
        if (!started) { started = true; await startCallback(); } applyPermissions();
        if (can('accounts.manage') && can('permissions.manage')) refreshAdminData();
        return;
      }
      setCsrf(''); showGate(data.setupRequired, '');
    } catch (err) {
      showGate(false, 'The local service could not be reached. Start the VMS service and reload this page.');
    }
  }

  window.addEventListener('focus', () => refreshCurrentSession());
  document.addEventListener('visibilitychange', () => { if (!document.hidden) refreshCurrentSession(); });
  window.StamfordAccessControl = { start, can, canAny, isSignedIn, refreshAdminData, showAccountView, get currentUser(){return auth?.user || null;} };
})();
