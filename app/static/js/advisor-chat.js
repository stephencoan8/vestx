/**
 * VestX Advisor — fully async vs the multi-page app shell.
 *
 * Chat must never block navigation. Full page loads abort fetch(), so we:
 *  - persist conversation + in-flight request in sessionStorage
 *  - re-issue the pending POST after any page load
 *  - keep sidebar/main free; panel is fire-and-forget UI
 */
(function () {
    'use strict';

    var STORAGE_HIST = 'vestx_advisor_history';
    var STORAGE_OPEN = 'vestx_advisor_open';
    var STORAGE_PENDING = 'vestx_advisor_pending';
    var STORAGE_PLAN = 'vestx_last_plan';

    var fab = document.getElementById('advisorFab');
    var panel = document.getElementById('advisorPanel');
    var closeBtn = document.getElementById('advisorClose');
    var newChatBtn = document.getElementById('advisorNewChat');
    var navAdvisor = document.getElementById('navAdvisorOpen');
    var sendBtn = document.getElementById('advisorSend');
    var input = document.getElementById('advisorInput');
    var log = document.getElementById('advisorMessages');
    var statusEl = document.getElementById('advisorStatus');

    if (!panel || !log) return;

    var history = [];
    /** In-memory request id for this document; pending survives via sessionStorage */
    var activeRequestId = null;
    var phaseTimer = null;
    var loadEl = null;
    /** Set true on pagehide so abort-like fetch errors keep pending for resume */
    var pageUnloading = false;

    // —— session helpers ——
    function saveHistory() {
        try {
            sessionStorage.setItem(STORAGE_HIST, JSON.stringify(history.slice(-40)));
        } catch (e) {}
    }
    function loadHistory() {
        try {
            var raw = sessionStorage.getItem(STORAGE_HIST);
            if (!raw) return [];
            var arr = JSON.parse(raw);
            return Array.isArray(arr) ? arr : [];
        } catch (e) {
            return [];
        }
    }
    function setOpenFlag(open) {
        try {
            sessionStorage.setItem(STORAGE_OPEN, open ? '1' : '0');
        } catch (e) {}
    }
    function readPending() {
        try {
            var raw = sessionStorage.getItem(STORAGE_PENDING);
            if (!raw) return null;
            var p = JSON.parse(raw);
            return p && p.id && Array.isArray(p.messages) ? p : null;
        } catch (e) {
            return null;
        }
    }
    function writePending(p) {
        try {
            if (p) sessionStorage.setItem(STORAGE_PENDING, JSON.stringify(p));
            else sessionStorage.removeItem(STORAGE_PENDING);
        } catch (e) {}
    }
    function clearPending() {
        writePending(null);
        activeRequestId = null;
        setBusyUi(false);
    }

    function setBusyUi(busy, label) {
        if (fab) {
            fab.classList.toggle('advisor-busy', !!busy);
            fab.setAttribute('aria-busy', busy ? 'true' : 'false');
            if (busy) {
                fab.title = 'Advisor is working in the background — you can navigate freely';
            } else {
                fab.title = 'VestX Advisor — available on every page';
            }
        }
        if (panel) panel.classList.toggle('advisor-request-inflight', !!busy);
        if (statusEl && busy) {
            statusEl.textContent = label || 'Working in background — navigate freely…';
        }
        // Never lock the page: only soft-disable double-send, not the shell
        if (sendBtn) {
            sendBtn.disabled = !!busy;
            sendBtn.textContent = busy ? 'Working…' : 'Send';
        }
        if (input) {
            // Keep input usable for notes; Enter while busy is ignored in sendAdvisor
            input.placeholder = busy
                ? 'Reply in progress — browse other pages anytime…'
                : 'Ask anything about your equity… works on every page';
        }
    }

    function isAdvisorOpen() {
        return panel && !panel.hasAttribute('hidden') && !panel.classList.contains('is-closed');
    }
    function openAdvisor() {
        panel.removeAttribute('hidden');
        panel.classList.remove('is-closed');
        panel.style.display = '';
        document.body.classList.add('advisor-open');
        if (fab) fab.setAttribute('aria-expanded', 'true');
        setOpenFlag(true);
        if (input && !readPending()) {
            try { input.focus({ preventScroll: true }); } catch (e) { input.focus(); }
        }
    }
    function closeAdvisor(ev) {
        if (ev) {
            ev.preventDefault();
            ev.stopPropagation();
        }
        panel.setAttribute('hidden', '');
        panel.classList.add('is-closed');
        panel.style.display = 'none';
        document.body.classList.remove('advisor-open');
        if (fab) fab.setAttribute('aria-expanded', 'false');
        setOpenFlag(false);
        // Closing does NOT cancel background work
    }
    window.vestxOpenAdvisor = openAdvisor;
    window.vestxCloseAdvisor = closeAdvisor;

    function appendMsg(role, text, opts) {
        opts = opts || {};
        var div = document.createElement('div');
        div.className = 'advisor-msg ' + (role === 'user' ? 'user' : 'bot');
        if (role === 'bot' || role === 'assistant') {
            div.className = 'advisor-msg bot';
            if (opts.html) {
                div.innerHTML = text;
            } else if (window.vestxMarkdown && window.vestxMarkdown.render) {
                div.innerHTML = window.vestxMarkdown.render(text);
            } else {
                div.textContent = text;
            }
        } else if (opts.html) {
            div.innerHTML = text;
        } else {
            div.textContent = text;
        }
        if (opts.id) div.id = opts.id;
        if (opts.dataAttrs) {
            Object.keys(opts.dataAttrs).forEach(function (k) {
                div.setAttribute(k, opts.dataAttrs[k]);
            });
        }
        log.appendChild(div);
        log.scrollTop = log.scrollHeight;
        return div;
    }

    function removeLoadingUi() {
        if (phaseTimer) {
            clearTimeout(phaseTimer);
            phaseTimer = null;
        }
        var existing = log.querySelectorAll('.advisor-loading-msg, [data-advisor-loading="1"]');
        for (var i = 0; i < existing.length; i++) {
            if (existing[i].parentNode) existing[i].parentNode.removeChild(existing[i]);
        }
        loadEl = null;
    }

    function showLoadingUi(startedAt) {
        removeLoadingUi();
        loadEl = document.createElement('div');
        loadEl.className = 'advisor-msg bot advisor-loading-msg';
        loadEl.setAttribute('data-advisor-loading', '1');
        loadEl.innerHTML =
            '<div class="advisor-loading">' +
            '<span class="advisor-spinner" aria-hidden="true"></span>' +
            '<div class="advisor-loading-text">' +
            '<strong>Working in the background…</strong>' +
            '<span class="advisor-loading-phase">Tax engines / Grok — navigate freely</span>' +
            '<span class="advisor-loading-hint">This tab keeps the request; switch pages anytime</span>' +
            '</div></div>';
        log.appendChild(loadEl);
        log.scrollTop = log.scrollHeight;

        var t0 = startedAt || Date.now();
        phaseTimer = setInterval(function () {
            var ph = loadEl && loadEl.querySelector('.advisor-loading-phase');
            var sec = Math.round((Date.now() - t0) / 1000);
            if (ph) {
                ph.textContent = 'Still working… ' + sec + 's · navigate freely';
            }
            if (statusEl) {
                statusEl.textContent = 'Background request · ' + sec + 's · pages stay usable';
            }
        }, 1000);
    }

    function clearChatDomKeepWelcome() {
        var keep = log.querySelector('[data-welcome="1"]');
        log.innerHTML = '';
        if (keep) {
            log.appendChild(keep);
        } else {
            var w = document.createElement('div');
            w.className = 'advisor-msg bot md-ready';
            w.setAttribute('data-welcome', '1');
            w.innerHTML = '<p class="md-p"><strong>App-wide advisor</strong> — conversation cleared. Available on every page.</p>';
            log.appendChild(w);
        }
    }

    function restoreChatFromStorage() {
        history = loadHistory();
        if (!history.length) return;
        Array.prototype.slice.call(log.querySelectorAll('.advisor-msg:not([data-welcome])')).forEach(function (n) {
            n.parentNode.removeChild(n);
        });
        history.forEach(function (m) {
            if (!m || !m.content) return;
            appendMsg(m.role === 'user' ? 'user' : 'assistant', m.content);
        });
        if (statusEl) {
            statusEl.textContent = 'Restored chat · ' + history.length + ' messages · async across pages';
        }
    }

    function applyPlanFromChat(data) {
        var plan = data.engine_plan;
        if (!plan) return;
        var applied = window.vestxApplyEnginePlan
            ? window.vestxApplyEnginePlan(plan, data.context_meta || {})
            : false;
        var picks = plan.picks || [];
        var n = picks.length;
        var net = plan.achieved_net_cash;
        var tip = document.createElement('div');
        tip.className = 'advisor-msg bot advisor-ui-sync';
        var taxUrl = window.VESTX_TAX_HUB_URL || '/tax/';
        if (applied) {
            tip.innerHTML = '<strong>Screen updated</strong> from engine' +
                (n ? ' · ' + n + ' SpecID pick(s)' : '') +
                (net != null ? ' · net ~$' + Number(net).toLocaleString('en-US', { maximumFractionDigits: 0 }) : '') +
                ' · goal/lots refreshed on this page.';
        } else {
            tip.innerHTML = '<strong>Plan ready</strong> (' + (n || 0) + ' picks)' +
                (net != null ? ', net ~$' + Number(net).toLocaleString('en-US', { maximumFractionDigits: 0 }) : '') +
                '. Open <a href="' + taxUrl + '">Sales &amp; Tax</a> anytime — plan is saved for this tab.';
        }
        log.appendChild(tip);
        log.scrollTop = log.scrollHeight;
    }

    function pingApi() {
        if (!window.VESTX_ADVISOR_PING_URL || !window.vestxFetchJson || !statusEl) return;
        if (readPending()) return; // don't clobber busy status
        statusEl.textContent = 'Checking API…';
        window.vestxFetchJson(window.VESTX_ADVISOR_PING_URL, { method: 'GET' })
            .then(function (d) {
                if (readPending()) return;
                statusEl.textContent = 'API OK · async · ' + (d.api_version || 'ping') +
                    (d.grok_enabled ? ' · Grok key ready' : ' · no Grok key (engine Qs still work)');
            })
            .catch(function (err) {
                if (readPending()) return;
                statusEl.textContent = 'API check failed: ' + (err.message || err);
            });
    }

    /**
     * Fire advisor request. Survives navigation via sessionStorage pending + resume.
     * @param {object} opts
     * @param {Array} opts.messages
     * @param {string} opts.requestId
     * @param {number} [opts.startedAt]
     * @param {boolean} [opts.isResume]
     */
    function runAdvisorRequest(opts) {
        var requestId = opts.requestId;
        var messages = opts.messages;
        var startedAt = opts.startedAt || Date.now();
        var isResume = !!opts.isResume;

        activeRequestId = requestId;
        writePending({
            id: requestId,
            messages: messages,
            plan: window.VESTX_LAST_PLAN || null,
            startedAt: startedAt,
            url: window.VESTX_ADVISOR_URL
        });
        setBusyUi(true, isResume
            ? 'Resuming background reply… navigate freely'
            : 'Background request… navigate freely');
        showLoadingUi(startedAt);

        if (!window.vestxFetchJson || !window.VESTX_ADVISOR_URL) {
            finishError(requestId, new Error('Advisor API not configured on this page'));
            return;
        }

        // Detached promise — intentionally not awaited by callers; page can unload
        window.vestxFetchJson(window.VESTX_ADVISOR_URL, {
            method: 'POST',
            body: JSON.stringify({
                messages: messages,
                plan: window.VESTX_LAST_PLAN || null,
                client_request_id: requestId
            })
        })
            .then(function (data) {
                // Ignore stale responses if user started a newer request
                var pending = readPending();
                if (pending && pending.id !== requestId) return;
                if (activeRequestId && activeRequestId !== requestId) return;
                finishSuccess(requestId, data, startedAt);
            })
            .catch(function (e) {
                var pending = readPending();
                // Navigating away aborts fetch — keep pending so the next page re-issues the request
                if (pageUnloading) {
                    return;
                }
                if (pending && pending.id !== requestId) return;
                if (activeRequestId && activeRequestId !== requestId) return;
                finishError(requestId, e);
            });
    }

    function finishSuccess(requestId, data, startedAt) {
        var pending = readPending();
        if (pending && pending.id !== requestId) return;

        removeLoadingUi();
        clearPending();

        var ms = Date.now() - (startedAt || Date.now());
        var reply = data.reply || '(empty reply)';
        history.push({ role: 'assistant', content: reply });
        saveHistory();
        appendMsg('assistant', reply);
        if (data.engine_plan) {
            applyPlanFromChat(data);
        }

        var ok = document.createElement('div');
        ok.className = 'advisor-msg bot advisor-api-ok';
        var usedGrok = data.used_grok === true;
        var apiOk = data.api_ok !== false;
        var cm = data.context_meta || {};
        ok.innerHTML = '<strong>' + (apiOk ? '✓ API OK' : '⚠ Partial') + '</strong> · ' +
            (usedGrok ? 'Grok called' : 'Engines only (0 Grok tokens)') +
            ' · ' + (ms / 1000).toFixed(1) + 's' +
            (cm.est_context_tokens ? ' · ~' + cm.est_context_tokens + ' ctx tok' : '') +
            (cm.intent || cm.mode ? ' · ' + (cm.intent || cm.mode) : '') +
            (data.phase ? ' · phase=' + data.phase : '') +
            (data.api_version ? ' · ' + data.api_version : '');
        log.appendChild(ok);
        log.scrollTop = log.scrollHeight;

        if (statusEl) {
            if (usedGrok) {
                statusEl.textContent = 'Grok API responded · ' + (ms / 1000).toFixed(1) + 's · ready';
            } else {
                statusEl.textContent = 'Engine only · API OK · ' + (ms / 1000).toFixed(1) + 's · ready';
            }
        }
    }

    function finishError(requestId, e) {
        var pending = readPending();
        if (pending && pending.id !== requestId) return;

        removeLoadingUi();
        clearPending();

        var msg = (e && e.message) || String(e);
        if (e && e.data && e.data.error) msg = e.data.error;
        if (e && e.data && e.data.phase) msg += ' (phase: ' + e.data.phase + ')';
        if (e && (e.status === 503 || /API key/i.test(msg))) {
            msg += ' → Settings to add your encrypted xAI key.';
        }
        if (/non-JSON|Internal Server Error/i.test(msg)) {
            msg += ' Server crashed before JSON response — check Railway logs after redeploy.';
        }
        appendMsg('bot', '**Error**\n\n' + msg);
        var fail = document.createElement('div');
        fail.className = 'advisor-msg bot advisor-api-fail';
        fail.innerHTML = '<strong>✗ API failed</strong> · HTTP ' +
            (e && e.status != null ? e.status : '?') +
            ' · request did not complete cleanly';
        log.appendChild(fail);
        log.scrollTop = log.scrollHeight;
        if (statusEl) {
            statusEl.textContent = 'API error HTTP ' + (e && e.status != null ? e.status : '?');
        }
    }

    function newRequestId() {
        return 'req_' + Date.now().toString(36) + '_' + Math.random().toString(36).slice(2, 9);
    }

    function sendAdvisor() {
        if (readPending() || activeRequestId) {
            if (statusEl) {
                statusEl.textContent = 'Still working on the last question — navigate freely; wait for reply';
            }
            return;
        }
        var text = (input && input.value || '').trim();
        if (!text) return;
        if (input) input.value = '';
        appendMsg('user', text);
        history.push({ role: 'user', content: text });
        saveHistory();
        setOpenFlag(true);

        var requestId = newRequestId();
        // Snapshot messages for resume after navigation
        var messagesSnapshot = history.map(function (m) {
            return { role: m.role, content: m.content };
        });
        runAdvisorRequest({
            requestId: requestId,
            messages: messagesSnapshot,
            startedAt: Date.now(),
            isResume: false
        });
    }

    /**
     * After navigation: if a pending request exists and history still ends with a
     * user turn without a matching assistant reply, re-fire the POST.
     */
    function resumePendingIfNeeded() {
        var pending = readPending();
        if (!pending) return;

        // Stale pending > 3 min: drop
        if (pending.startedAt && (Date.now() - pending.startedAt) > 180000) {
            clearPending();
            if (statusEl) statusEl.textContent = 'Previous request timed out — send again if needed';
            return;
        }

        // Align history with pending messages (pending is authoritative for in-flight)
        if (pending.messages && pending.messages.length) {
            history = pending.messages.map(function (m) {
                return { role: m.role, content: m.content };
            });
            saveHistory();
            // Rebuild DOM from history so loading attaches cleanly
            Array.prototype.slice.call(log.querySelectorAll('.advisor-msg:not([data-welcome])')).forEach(function (n) {
                n.parentNode.removeChild(n);
            });
            history.forEach(function (m) {
                if (!m || !m.content) return;
                appendMsg(m.role === 'user' ? 'user' : 'assistant', m.content);
            });
        }

        // Ensure panel can show progress (optional — keep open flag)
        try {
            if (sessionStorage.getItem(STORAGE_OPEN) === '1') openAdvisor();
        } catch (e) {}

        runAdvisorRequest({
            requestId: pending.id,
            messages: pending.messages,
            startedAt: pending.startedAt || Date.now(),
            isResume: true
        });
    }

    function toggleOrOpenAdvisor(forceOpen) {
        if (forceOpen || !isAdvisorOpen()) {
            openAdvisor();
            pingApi();
        } else {
            closeAdvisor();
        }
    }

    // —— events: never block shell ——
    if (fab) {
        fab.addEventListener('click', function (e) {
            e.preventDefault();
            e.stopPropagation();
            toggleOrOpenAdvisor(false);
        });
    }
    if (navAdvisor) {
        navAdvisor.addEventListener('click', function (e) {
            e.preventDefault();
            e.stopPropagation();
            document.body.classList.remove('sidebar-open');
            openAdvisor();
            pingApi();
        });
    }
    if (closeBtn) {
        closeBtn.addEventListener('click', closeAdvisor);
        closeBtn.addEventListener('click', closeAdvisor, true);
    }
    if (newChatBtn) {
        newChatBtn.addEventListener('click', function (e) {
            e.preventDefault();
            // Cancel pending logically (server may still finish; we ignore result)
            clearPending();
            removeLoadingUi();
            history = [];
            saveHistory();
            clearChatDomKeepWelcome();
            if (statusEl) statusEl.textContent = 'New chat · async across pages';
        });
    }
    document.addEventListener('keydown', function (e) {
        if (e.key === 'Escape' && isAdvisorOpen()) closeAdvisor(e);
    });
    if (sendBtn) sendBtn.addEventListener('click', sendAdvisor);
    if (input) {
        input.addEventListener('keydown', function (e) {
            if (e.key === 'Enter' && !e.shiftKey) {
                e.preventDefault();
                sendAdvisor();
            }
        });
    }

    // pagehide / beforeunload: keep pending; do not cancel — next page resumes
    function markUnloading() {
        pageUnloading = true;
        if (activeRequestId) {
            var p = readPending();
            if (p && p.id === activeRequestId) {
                p.interruptedAt = Date.now();
                writePending(p);
            }
        }
    }
    window.addEventListener('pagehide', markUnloading);
    window.addEventListener('beforeunload', markUnloading);

    // —— boot ——
    restoreChatFromStorage();
    try {
        if (sessionStorage.getItem(STORAGE_OPEN) === '1') {
            openAdvisor();
        }
    } catch (e) {}

    // Resume after navigation (microtask so DOM is ready)
    setTimeout(function () {
        resumePendingIfNeeded();
        if (!readPending()) {
            // light status only when idle
        }
    }, 0);
})();
