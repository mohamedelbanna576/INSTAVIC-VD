/**
 * INSTAVIC VD — Frontend Logic
 * Handles URL detection, API calls, SSE progress, and DOM rendering.
 */

(function () {
    'use strict';

    // ─── DOM Elements ──────────────────────────────────────────────
    const modeSingleBtn  = document.getElementById('mode-single-btn');
    const modeBulkBtn    = document.getElementById('mode-bulk-btn');
    const urlInput       = document.getElementById('url-input');
    const maxPostsRow    = document.getElementById('max-posts-row');
    const maxPostsInput  = document.getElementById('max-posts-input');
    const submitBtn      = document.getElementById('submit-btn');
    const submitBtnText  = document.getElementById('submit-btn-text');
    const submitSpinner  = document.getElementById('submit-spinner');
    const statusMessage  = document.getElementById('status-message');
    const statusIcon     = document.getElementById('status-icon');
    const statusText     = document.getElementById('status-text');
    const loadingOverlay = document.getElementById('loading-overlay');
    const loadingText    = document.getElementById('loading-text');
    const progressSection = document.getElementById('progress-section');
    const progressTitle  = document.getElementById('progress-title');
    const progressCount  = document.getElementById('progress-count');
    const progressBar    = document.getElementById('progress-bar');
    const progressStatus = document.getElementById('progress-status');
    const resultsSection = document.getElementById('results-section');
    const resultsTitle   = document.getElementById('results-title');
    const videoGrid      = document.getElementById('video-grid');
    const zipBtn         = document.getElementById('zip-btn');

    // ─── State ─────────────────────────────────────────────────────
    let currentMode = 'single'; // 'single' | 'bulk'
    let isLoading   = false;
    let currentTaskId = null;
    let eventSource  = null;

    // ─── Settings Modal ────────────────────────────────────────────
    const settingsOpen = document.getElementById('settings-open');
    const settingsClose = document.getElementById('settings-close');
    const settingsModal = document.getElementById('settings-modal');
    const saveSessionBtn = document.getElementById('save-session-btn');
    const sessionIdInput = document.getElementById('session-id-input');
    const sessionStatus = document.getElementById('session-status-text');

    if (settingsOpen) {
        settingsOpen.addEventListener('click', () => {
            settingsModal.classList.add('visible');
            sessionStatus.textContent = '';
            sessionStatus.className = 'session-status-text';
        });

        const closeModal = () => settingsModal.classList.remove('visible');
        settingsClose.addEventListener('click', closeModal);
        settingsModal.addEventListener('click', (e) => {
            if (e.target === settingsModal) closeModal();
        });

        saveSessionBtn.addEventListener('click', async () => {
            const sid = sessionIdInput.value.trim();
            saveSessionBtn.disabled = true;
            sessionStatus.textContent = 'Saving...';
            sessionStatus.className = 'session-status-text';

            try {
                const response = await fetch('/api/login', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ session_id: sid })
                });
                const data = await response.json();
                
                if (data.success) {
                    sessionStatus.textContent = data.message;
                    sessionStatus.classList.add('success');
                    loadConfig();
                    setTimeout(closeModal, 1500);
                } else {
                    sessionStatus.textContent = 'Failed to save session.';
                    sessionStatus.classList.add('error');
                }
            } catch (err) {
                sessionStatus.textContent = 'Error connecting to server.';
                sessionStatus.classList.add('error');
            } finally {
                saveSessionBtn.disabled = false;
            }
        });
    }

    // ─── Regex ─────────────────────────────────────────────────────
    const POST_RE = /(?:https?:\/\/)?(?:www\.)?instagram\.com\/(?:p|reel|reels|tv)\/([A-Za-z0-9_-]+)/;
    const PROFILE_RE = /(?:https?:\/\/)?(?:www\.)?instagram\.com\/([A-Za-z0-9_.]+)\/?(?:\?.*)?$/;
    const RESERVED = new Set([
        'p', 'reel', 'reels', 'tv', 'explore', 'stories',
        'accounts', 'directory', 'developer', 'about', 'legal',
    ]);

    // ─── Icons (Phosphor Classes) ──────────────────────────────────
    const ICONS = {
        error: 'ph-fill ph-x-circle text-red-500',
        success: 'ph-fill ph-check-circle text-green-500',
        info: 'ph-fill ph-info text-blue-500',
    };

    // ─── Mode Toggle ───────────────────────────────────────────────
    function setMode(mode) {
        currentMode = mode;
        modeSingleBtn.classList.toggle('active', mode === 'single');
        modeBulkBtn.classList.toggle('active', mode === 'bulk');

        if (mode === 'single') {
            urlInput.placeholder = 'Paste Instagram video or reel URL...';
            submitBtnText.textContent = 'Download Video';
            maxPostsRow.classList.remove('visible');
        } else {
            urlInput.placeholder = 'Paste Instagram profile URL...';
            submitBtnText.textContent = 'Download All Videos';
            maxPostsRow.classList.add('visible');
        }

        hideStatus();
        hideResults();
        hideProgress();
    }

    modeSingleBtn.addEventListener('click', () => setMode('single'));
    modeBulkBtn.addEventListener('click', () => setMode('bulk'));

    // Auto-detect on paste
    urlInput.addEventListener('input', () => {
        const val = urlInput.value.trim();
        if (POST_RE.test(val)) {
            setMode('single');
        } else if (PROFILE_RE.test(val)) {
            const match = val.match(PROFILE_RE);
            if (match && !RESERVED.has(match[1].toLowerCase())) {
                setMode('bulk');
            }
        }
    });

    // ─── Status Helpers ────────────────────────────────────────────
    function showStatus(type, message) {
        statusMessage.className = 'status-message visible ' + type;
        statusIcon.className = ICONS[type] || ICONS.info;
        statusText.textContent = message;
    }

    function hideStatus() {
        statusMessage.className = 'status-message';
    }

    function showLoading(text) {
        isLoading = true;
        submitBtn.disabled = true;
        submitSpinner.classList.add('visible');
        submitBtnText.style.display = 'none';
        loadingOverlay.classList.add('visible');
        loadingText.textContent = text || 'Processing...';
    }

    function hideLoading() {
        isLoading = false;
        submitBtn.disabled = false;
        submitSpinner.classList.remove('visible');
        submitBtnText.style.display = '';
        loadingOverlay.classList.remove('visible');
    }

    function hideResults() {
        resultsSection.classList.remove('visible');
        videoGrid.innerHTML = '';
        zipBtn.style.display = 'none';
    }

    function hideProgress() {
        progressSection.classList.remove('visible');
        progressBar.style.width = '0%';
    }

    // ─── Render Video Card ─────────────────────────────────────────
    function createVideoCard(video) {
        const card = document.createElement('div');
        card.className = 'video-card';

        // Meta parts
        let metaHTML = '';
        if (video.date) {
            const d = new Date(video.date);
            metaHTML += `<span class="meta-item">
                <i class="ph ph-calendar-blank"></i>
                ${d.toLocaleDateString()}
            </span>`;
        }
        if (video.views) {
            metaHTML += `<span class="meta-item">
                <i class="ph ph-eye"></i>
                ${formatNumber(video.views)} views
            </span>`;
        }
        if (video.likes) {
            metaHTML += `<span class="meta-item">
                <i class="ph-fill ph-heart text-accent-pink text-xs"></i>
                ${formatNumber(video.likes)}
            </span>`;
        }

        const captionHTML = video.caption
            ? `<p class="video-caption" title="${escapeHTML(video.caption)}">${escapeHTML(video.caption)}</p>`
            : '';

        card.innerHTML = `
            <div class="meta-icon">
                <i class="ph-fill ph-video-camera"></i>
            </div>
            <div class="video-info">
                <p class="video-name" title="${escapeHTML(video.filename)}">${escapeHTML(video.filename)}</p>
                <div class="video-metadata">${metaHTML}</div>
                ${captionHTML}
            </div>
            <a href="${video.download_url}" class="glass-btn btn-sm action-btn" download>
                <i class="ph-bold ph-download-simple"></i>
            </a>
        `;

        return card;
    }

    function formatNumber(n) {
        if (!n) return '0';
        if (n >= 1_000_000) return (n / 1_000_000).toFixed(1) + 'M';
        if (n >= 1_000) return (n / 1_000).toFixed(1) + 'K';
        return n.toString();
    }

    function escapeHTML(str) {
        const div = document.createElement('div');
        div.textContent = str;
        return div.innerHTML;
    }

    // ─── API: Single Download ──────────────────────────────────────
    async function downloadSingle(url) {
        showLoading('Fetching video...');
        hideStatus();
        hideResults();

        try {
            const res = await fetch('/api/single', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ url }),
            });

            const data = await res.json();

            if (!res.ok) {
                throw new Error(data.detail || 'Download failed');
            }

            hideLoading();
            showStatus('success', 'Video ready for download!');

            // Show result
            resultsTitle.textContent = 'Your Video';
            videoGrid.innerHTML = '';
            videoGrid.appendChild(createVideoCard(data.video));
            resultsSection.classList.add('visible');

        } catch (err) {
            hideLoading();
            showStatus('error', err.message || 'An error occurred');
        }
    }

    // ─── API: Bulk Download ────────────────────────────────────────
    async function downloadBulk(url) {
        showLoading('Starting bulk download...');
        hideStatus();
        hideResults();
        hideProgress();

        const maxPosts = parseInt(maxPostsInput.value) || 50;

        try {
            const res = await fetch('/api/bulk', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ url, max_posts: maxPosts }),
            });

            const data = await res.json();

            if (!res.ok) {
                throw new Error(data.detail || 'Bulk download failed');
            }

            hideLoading();
            currentTaskId = data.task_id;

            // Show progress
            progressTitle.textContent = `Downloading from @${data.username}`;
            progressSection.classList.add('visible');
            progressStatus.textContent = 'Scanning profile for videos...';

            // Start SSE
            startProgressSSE(data.task_id);

        } catch (err) {
            hideLoading();
            showStatus('error', err.message || 'An error occurred');
        }
    }

    // ─── SSE Progress ──────────────────────────────────────────────
    function startProgressSSE(taskId) {
        if (eventSource) {
            eventSource.close();
        }

        eventSource = new EventSource(`/api/bulk/status/${taskId}`);

        eventSource.onmessage = function (event) {
            const data = JSON.parse(event.data);

            // Update progress bar
            const pct = data.total > 0 ? Math.round((data.downloaded / data.total) * 100) : 0;
            progressBar.style.width = pct + '%';
            progressCount.textContent = `${data.downloaded} / ${data.total}`;

            // Update status text
            if (data.status === 'scanning') {
                progressStatus.textContent = 'Scanning profile for videos...';
            } else if (data.status === 'downloading') {
                progressStatus.textContent = `Downloading video ${data.downloaded + 1} of ${data.total}...`;
            }

            // Render videos as they come in
            if (data.videos.length > 0) {
                resultsTitle.textContent = `Videos from Profile (${data.videos.length})`;
                videoGrid.innerHTML = '';
                data.videos.forEach(v => {
                    videoGrid.appendChild(createVideoCard(v));
                });
                resultsSection.classList.add('visible');
            }

            // Done
            if (data.done) {
                eventSource.close();
                eventSource = null;

                if (data.status === 'error') {
                    hideProgress();
                    const errorMsg = data.errors.length > 0 ? data.errors[0] : 'An error occurred';
                    showStatus('error', errorMsg);
                } else {
                    progressBar.style.width = '100%';
                    progressStatus.textContent = 'Download complete!';

                    if (data.videos.length === 0) {
                        showStatus('info', 'No videos found on this profile.');
                    } else {
                        showStatus('success', `Successfully downloaded ${data.videos.length} video(s)!`);

                        // Show ZIP button
                        zipBtn.style.display = 'flex';
                        zipBtn.onclick = () => downloadZip(taskId);
                    }

                    // Show any errors
                    if (data.errors.length > 0 && data.videos.length > 0) {
                        showStatus('info', `Downloaded ${data.videos.length} video(s). Some errors occurred: ${data.errors[0]}`);
                    }
                }
            }
        };

        eventSource.onerror = function () {
            eventSource.close();
            eventSource = null;
            // Don't show error — SSE may close normally when done
        };
    }

    // ─── Download ZIP ──────────────────────────────────────────────
    async function downloadZip(taskId) {
        zipBtn.disabled = true;
        zipBtn.textContent = 'Creating ZIP...';

        try {
            const res = await fetch(`/api/bulk/zip/${taskId}`, { method: 'POST' });
            const data = await res.json();

            if (!res.ok) {
                throw new Error(data.detail || 'Failed to create ZIP');
            }

            // Trigger download
            const a = document.createElement('a');
            a.href = data.download_url;
            a.download = data.filename;
            document.body.appendChild(a);
            a.click();
            document.body.removeChild(a);

            zipBtn.disabled = false;
            zipBtn.innerHTML = `
                <i class="ph-bold ph-file-zip"></i>
                <span>Download ZIP</span>
            `;

        } catch (err) {
            zipBtn.disabled = false;
            zipBtn.innerHTML = `
                <i class="ph-bold ph-file-zip"></i>
                <span>Download ZIP</span>
            `;
            showStatus('error', err.message);
        }
    }

    // ─── Submit Handler ────────────────────────────────────────────
    submitBtn.addEventListener('click', () => {
        if (isLoading) return;

        const url = urlInput.value.trim();
        if (!url) {
            showStatus('error', 'Please paste an Instagram URL.');
            urlInput.focus();
            return;
        }

        // Basic validation
        if (!url.includes('instagram.com')) {
            showStatus('error', 'Please enter a valid Instagram URL.');
            urlInput.focus();
            return;
        }

        if (currentMode === 'single') {
            if (!POST_RE.test(url)) {
                showStatus('error', 'Invalid post URL. Please paste a link to an Instagram Reel or Video post.');
                return;
            }
            downloadSingle(url);
        } else {
            downloadBulk(url);
        }
    });

    // Enter key support
    urlInput.addEventListener('keydown', (e) => {
        if (e.key === 'Enter') {
            submitBtn.click();
        }
    });

    // ─── App Initialization (Config Load) ──────────────────────────
    async function loadConfig() {
        try {
            const res = await fetch('/api/config');
            const data = await res.json();
            
            if (data.connected && settingsOpen) {
                settingsOpen.innerHTML = `
                    <i class="ph-fill ph-check-circle text-green-400"></i>
                    <span class="text-green-400">Connected</span>
                `;
            } else if (settingsOpen) {
                settingsOpen.innerHTML = `
                    <i class="ph ph-plug-charging"></i>
                    <span>Connect Account</span>
                `;
            }
        } catch (e) {
            console.error('Failed to load config:', e);
        }
    }

    // ─── Init ──────────────────────────────────────────────────────
    setMode('single');
    urlInput.focus();
    loadConfig();

})();
