/**
 * VestX Advisor — professional async chat (job queue + poll).
 *
 * Architecture (Silicon Valley grade):
 *  1. POST /tax/api/advisor/jobs  → 202 { job_id } in milliseconds
 *  2. Background thread on server runs engines/Grok (does not hold HTTP worker)
 *  3. Client polls GET /tax/api/advisor/jobs/<id> with backoff
 *  4. Page navigation is free: only job_id is stored; next page resumes POLL only
 *     (never re-runs the heavy work)
 *
 * Never blocks the multi-page app shell.
 */
(function () {
    'use strict';

    var STORAGE_HIST = 'vestx_advisor_history';
    var STORAGE_OPEN = 'vestx_advisor_open';
    var STORAGE_PENDING = 'vestx_advisor_pending_job';

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
    var pollTimer = null;
    var phaseTimer = null;
    var loadEl = null;
    var activeJobId = null;
    var pollAttempt = 0;
    var pageUnloading = false;

    // —— storage ——
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
    function readPendingJob() {
        try {
            var raw = sessionStorage.getItem(STORAGE_PENDING);
            if (!raw) return null;
            var p = JSON.parse(raw);
            return p && p.jobId ? p : null;
        } catch (e) {
            return null;
        }
    }
    function writePendingJob(p) {
        try {
            if (p) sessionStorage.setItem(STORAGE_PENDING, JSON.stringify(p));
            else sessionStorage.removeItem(STORAGE_PENDING);
        } catch (e) {}
    }
    function clearPending() {
        writePendingJob(null);
        activeJobId = null;
        pollAttempt = 0;
        stopPolling();
        setBusyUi(false);
    }

    function jobStatusUrl(jobId) {
        var tmpl = window.VESTX_ADVISOR_JOB_URL || '/tax/api/advisor/jobs/__ID__';
        return tmpl.replace('__ID__', encodeURIComponent(jobId));
    }

    function setBusyUi(busy, label) {
        if (fab) {
            fab.classList.toggle('advisor-busy', !!busy);
            fab.setAttribute('aria-busy', busy ? 'true' : 'false');
            fab.title = busy
                ? 'Advisor working in background — navigate freely'
                : 'VestX Advisor — works in background; navigate freely';
        }
        if (panel) panel.classList.toggle('advisor-request-inflight', !!busy);
        if (statusEl && busy) {
            statusEl.textContent = label || 'Background job · navigate freely…';
        }
        if (sendBtn) {
            sendBtn.disabled = !!busy;
            sendBtn.textContent = busy ? 'Working…' : 'Send';
        }
        if (input) {
            input.placeholder = busy
                ? 'Job running in background — switch pages anytime…'
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
        if (input && !readPendingJob()) {
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
        // Closing never cancels the background job
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
        log.appendChild(div);
        log.scrollTop = log.scrollHeight;
        return div;
    }

    function removeLoadingUi() {
        if (phaseTimer) {
            clearInterval(phaseTimer);
            phaseTimer = null;
        }
        var existing = log.querySelectorAll('[data-advisor-loading="1"]');
        for (var i = 0; i < existing.length; i++) {
            if (existing[i].parentNode) existing[i].parentNode.removeChild(existing[i]);
        }
        loadEl = null;
    }

    function showLoadingUi(startedAt, jobId) {
        removeLoadingUi();
        loadEl = document.createElement('div');
        loadEl.className = 'advisor-msg bot advisor-loading-msg';
        loadEl.setAttribute('data-advisor-loading', '1');
        loadEl.innerHTML =
            '<div class="advisor-loading">' +
            '<span class="advisor-spinner" aria-hidden="true"></span>' +
            '<div class="advisor-loading-text">' +
            '<strong>Background job running…</strong>' +
            '<span class="advisor-loading-phase">Queued · servers free for page loads</span>' +
            '<span class="advisor-loading-hint">Navigate anywhere — reply attaches when ready' +
            (jobId ? ' · job ' + String(jobId).slice(0, 8) : '') +
            '</span>' +
            '</div></div>';
        log.appendChild(loadEl);
        log.scrollTop = log.scrollHeight;

        var t0 = startedAt || Date.now();
        phaseTimer = setInterval(function () {
            if (!loadEl) return;
            var ph = loadEl.querySelector('.advisor-loading-phase');
            var sec = Math.round((Date.now() - t0) / 1000);
            if (ph) ph.textContent = 'Still working · ' + sec + 's · navigate freely';
            if (statusEl) {
                statusEl.textContent = 'Background job · ' + sec + 's · pages stay instant';
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
            w.innerHTML = '<p class="md-p"><strong>App-wide advisor</strong> — conversation cleared. Async jobs never block the app.</p>';
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
            statusEl.textContent = 'Restored chat · ' + history.length + ' messages · async jobs';
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
        if (readPendingJob()) return;
        statusEl.textContent = 'Checking API…';
        window.vestxFetchJson(window.VESTX_ADVISOR_PING_URL, { method: 'GET' })
            .then(function (d) {
                if (readPendingJob()) return;
                statusEl.textContent = 'API OK · async jobs · ' + (d.api_version || 'ping') +
                    (d.grok_enabled ? ' · Grok key ready' : ' · no Grok key (engine Qs still work)');
            })
            .catch(function (err) {
                if (readPendingJob()) return;
                statusEl.textContent = 'API check failed: ' + (err.message || err);
            });
    }

    function stopPolling() {
        if (pollTimer) {
            clearTimeout(pollTimer);
            pollTimer = null;
        }
    }

    function nextPollDelay() {
        // 300ms → 2s exponential, then cap
        var base = 300 * Math.pow(1.35, Math.min(pollAttempt, 10));
        return Math.min(2000, Math.round(base));
    }

    /**
     * Poll only — never re-enqueue. Survives full page navigations via job_id in sessionStorage.
     */
    function startPolling(jobId, startedAt) {
        activeJobId = jobId;
        stopPolling();

        function tick() {
            if (pageUnloading) return;
            if (!activeJobId || activeJobId !== jobId) return;
            if (!window.vestxFetchJson) {
                pollTimer = setTimeout(tick, 1000);
                return;
            }

            window.vestxFetchJson(jobStatusUrl(jobId), { method: 'GET' })
                .then(function (data) {
                    if (activeJobId !== jobId) return;
                    var st = data.status || '';
                    if (st === 'queued' || st === 'running') {
                        pollAttempt += 1;
                        var ph = loadEl && loadEl.querySelector('.advisor-loading-phase');
                        if (ph) {
                            ph.textContent = (st === 'queued' ? 'Queued' : 'Running') +
                                ' · poll #' + pollAttempt + ' · navigate freely';
                        }
                        pollTimer = setTimeout(tick, nextPollDelay());
                        return;
                    }
                    if (st === 'done') {
                        finishSuccess(jobId, data, startedAt);
                        return;
                    }
                    if (st === 'error') {
                        finishError(jobId, data);
                        return;
                    }
                    // Unknown — keep polling a bit
                    pollAttempt += 1;
                    if (pollAttempt > 90) {
                        finishError(jobId, { error: 'Job timed out waiting for status', status: st });
                        return;
                    }
                    pollTimer = setTimeout(tick, nextPollDelay());
                })
                .catch(function (e) {
                    if (pageUnloading) return;
                    if (activeJobId !== jobId) return;
                    // Transient network blip — keep polling
                    pollAttempt += 1;
                    if (pollAttempt > 100) {
                        finishError(jobId, { error: e.message || String(e), status: e.status });
                        return;
                    }
                    pollTimer = setTimeout(tick, nextPollDelay());
                });
        }

        pollTimer = setTimeout(tick, 250);
    }

    function finishSuccess(jobId, data, startedAt) {
        if (activeJobId && activeJobId !== jobId) return;
        removeLoadingUi();
        clearPending();

        var ms = Date.now() - (startedAt || Date.now());
        // Prefer flattened fields; fall back to nested result
        var result = data.result || data;
        var reply = data.reply || result.reply || '(empty reply)';
        var success = data.result_success !== false && result.success !== false;

        if (!success && !data.reply && !result.reply) {
            finishError(jobId, data);
            return;
        }

        history.push({ role: 'assistant', content: reply });
        saveHistory();
        appendMsg('assistant', reply);

        var plan = data.engine_plan || result.engine_plan;
        if (plan) {
            applyPlanFromChat({
                engine_plan: plan,
                context_meta: data.context_meta || result.context_meta || {}
            });
        }

        var ok = document.createElement('div');
        ok.className = 'advisor-msg bot advisor-api-ok';
        var usedGrok = (data.used_grok === true) || (result.used_grok === true);
        var apiOk = data.api_ok !== false;
        var cm = data.context_meta || result.context_meta || {};
        ok.innerHTML = '<strong>' + (apiOk ? '✓ API OK' : '⚠ Partial') + '</strong> · ' +
            'async job · ' +
            (usedGrok ? 'Grok called' : 'Engines only (0 Grok tokens)') +
            ' · ' + (ms / 1000).toFixed(1) + 's' +
            (cm.intent || cm.mode ? ' · ' + (cm.intent || cm.mode) : '') +
            (data.phase || result.phase ? ' · phase=' + (data.phase || result.phase) : '') +
            (data.api_version ? ' · ' + data.api_version : '');
        log.appendChild(ok);
        log.scrollTop = log.scrollHeight;

        if (statusEl) {
            statusEl.textContent = (usedGrok ? 'Grok' : 'Engine') +
                ' · async done · ' + (ms / 1000).toFixed(1) + 's · ready';
        }
    }

    function finishError(jobId, data) {
        if (activeJobId && activeJobId !== jobId) return;
        removeLoadingUi();
        clearPending();

        var msg = (data && (data.error || (data.result && data.result.error))) || 'Job failed';
        if (data && data.phase) msg += ' (phase: ' + data.phase + ')';
        if (/API key/i.test(msg)) {
            msg += ' → Settings to add your encrypted xAI key.';
        }
        appendMsg('bot', '**Error**\n\n' + msg);

        // Engine fallback reply on error payload
        var fallback = data && (data.reply || (data.result && data.result.reply));
        if (fallback) {
            history.push({ role: 'assistant', content: fallback });
            saveHistory();
            appendMsg('assistant', fallback);
            var plan = data.engine_plan || (data.result && data.result.engine_plan);
            if (plan) {
                applyPlanFromChat({
                    engine_plan: plan,
                    context_meta: data.context_meta || {}
                });
            }
        }

        var fail = document.createElement('div');
        fail.className = 'advisor-msg bot advisor-api-fail';
        fail.innerHTML = '<strong>✗ Job failed</strong> · ' +
            (data && data.status ? String(data.status) : 'error');
        log.appendChild(fail);
        log.scrollTop = log.scrollHeight;
        if (statusEl) statusEl.textContent = 'Job error — app was never blocked';
    }

    /**
     * Enqueue job (returns in ms). Then poll. Navigation never waits on engines/Grok.
     */
    function enqueueAndWatch(messages, startedAt) {
        var jobsUrl = window.VESTX_ADVISOR_JOBS_URL;
        if (!jobsUrl || !window.vestxFetchJson) {
            finishError(null, { error: 'Async jobs API not configured on this page' });
            return;
        }

        setBusyUi(true, 'Enqueueing background job…');
        showLoadingUi(startedAt, null);

        window.vestxFetchJson(jobsUrl, {
            method: 'POST',
            body: JSON.stringify({
                messages: messages,
                plan: window.VESTX_LAST_PLAN || null
            })
        })
            .then(function (data) {
                var jobId = data.job_id || data.id;
                if (!jobId) {
                    finishError(null, { error: 'No job_id returned from server' });
                    return;
                }
                writePendingJob({
                    jobId: jobId,
                    startedAt: startedAt || Date.now(),
                    messages: messages
                });
                activeJobId = jobId;
                setBusyUi(true, 'Job ' + String(jobId).slice(0, 8) + '… navigate freely');
                showLoadingUi(startedAt, jobId);
                startPolling(jobId, startedAt);
            })
            .catch(function (e) {
                if (pageUnloading) return;
                finishError(null, {
                    error: e.message || String(e),
                    status: e.status,
                    phase: e.data && e.data.phase
                });
            });
    }

    function sendAdvisor() {
        if (readPendingJob() || activeJobId) {
            if (statusEl) {
                statusEl.textContent = 'Job still running — navigate freely; wait for reply';
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

        var messagesSnapshot = history.map(function (m) {
            return { role: m.role, content: m.content };
        });
        enqueueAndWatch(messagesSnapshot, Date.now());
    }

    /**
     * After navigation: resume POLL for existing job_id only — never re-POST work.
     */
    function resumePendingJobIfNeeded() {
        var pending = readPendingJob();
        if (!pending || !pending.jobId) return;

        // Stale after 4 minutes
        if (pending.startedAt && (Date.now() - pending.startedAt) > 240000) {
            clearPending();
            if (statusEl) statusEl.textContent = 'Previous job expired — send again if needed';
            return;
        }

        activeJobId = pending.jobId;
        setBusyUi(true, 'Resuming job poll · navigate freely');
        showLoadingUi(pending.startedAt, pending.jobId);
        startPolling(pending.jobId, pending.startedAt);
    }

    function toggleOrOpenAdvisor(forceOpen) {
        if (forceOpen || !isAdvisorOpen()) {
            openAdvisor();
            pingApi();
        } else {
            closeAdvisor();
        }
    }

    // —— events ——
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
            clearPending();
            removeLoadingUi();
            history = [];
            saveHistory();
            clearChatDomKeepWelcome();
            if (statusEl) statusEl.textContent = 'New chat · async jobs · navigate freely';
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

    function markUnloading() {
        pageUnloading = true;
        stopPolling();
        // job_id already in sessionStorage — next page polls only
    }
    window.addEventListener('pagehide', markUnloading);
    window.addEventListener('beforeunload', markUnloading);

    // —— boot (must not block paint) ——
    restoreChatFromStorage();
    try {
        if (sessionStorage.getItem(STORAGE_OPEN) === '1') {
            openAdvisor();
        }
    } catch (e) {}

    // Resume poll after paint
    if (typeof requestAnimationFrame === 'function') {
        requestAnimationFrame(function () {
            setTimeout(resumePendingJobIfNeeded, 0);
        });
    } else {
        setTimeout(resumePendingJobIfNeeded, 0);
    }
})();
