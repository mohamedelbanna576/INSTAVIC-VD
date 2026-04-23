/**
 * INSTAVIC VD — Frontend Logic
 * Handles URL detection, API calls, SSE progress, DOM rendering,
 * and YouTube video download with quality selection.
 */

(function () {
    'use strict';

    // ─── DOM Elements (Instagram) ──────────────────────────────────
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
    const searchCard     = document.getElementById('search-card');

    // ─── DOM Elements (YouTube) ────────────────────────────────────
    const ytSection        = document.getElementById('yt-section');
    const ytUrlInput       = document.getElementById('yt-url-input');
    const ytFetchBtn       = document.getElementById('yt-fetch-btn');
    const ytFetchText      = document.getElementById('yt-fetch-text');
    const ytFetchSpinner   = document.getElementById('yt-fetch-spinner');
    const ytStatusMessage  = document.getElementById('yt-status-message');
    const ytStatusIcon     = document.getElementById('yt-status-icon');
    const ytStatusText     = document.getElementById('yt-status-text');
    const ytPreview        = document.getElementById('yt-preview');
    const ytThumb          = document.getElementById('yt-thumb');
    const ytDuration       = document.getElementById('yt-duration');
    const ytVideoTitle     = document.getElementById('yt-video-title');
    const ytUploader       = document.getElementById('yt-uploader');
    const ytViews          = document.getElementById('yt-views');
    const ytQualityGrid    = document.getElementById('yt-quality-grid');
    const ytDownloadBtn    = document.getElementById('yt-download-btn');
    const ytDlText         = document.getElementById('yt-dl-text');
    const ytDlSpinner      = document.getElementById('yt-dl-spinner');
    const ytLoadingOverlay = document.getElementById('yt-loading-overlay');
    const ytLoadingText    = document.getElementById('yt-loading-text');
    const ytResultsSection = document.getElementById('yt-results-section');
    const ytResultsTitle   = document.getElementById('yt-results-title');
    const ytVideoGrid      = document.getElementById('yt-video-grid');

    // ─── DOM Elements (Platform Switcher) ──────────────────────────
    const tabInstagram = document.getElementById('tab-instagram');
    const tabYoutube   = document.getElementById('tab-youtube');

    // ─── State ─────────────────────────────────────────────────────
    let currentPlatform = 'instagram'; // 'instagram' | 'youtube'
    let currentMode = 'single'; // 'single' | 'bulk'
    let isLoading   = false;
    let currentTaskId = null;
    let eventSource  = null;

    // YouTube state
    let ytIsLoading = false;
    let ytSelectedFormat = null;
    let ytSelectedLabel  = '';
    let ytCurrentUrl     = '';

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
    const YOUTUBE_RE = /(?:https?:\/\/)?(?:www\.|m\.)?(?:youtube\.com\/(?:watch\?v=|shorts\/|embed\/)|youtu\.be\/)([A-Za-z0-9_-]{11})/;
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

    // ═══════════════════════════════════════════════════════════════
    //  PLATFORM SWITCHING
    // ═══════════════════════════════════════════════════════════════

    function setPlatform(platform) {
        currentPlatform = platform;

        tabInstagram.classList.toggle('active', platform === 'instagram');
        tabYoutube.classList.toggle('active', platform === 'youtube');

        // Instagram elements
        const igElements = [searchCard, loadingOverlay, progressSection, resultsSection];
        igElements.forEach(el => {
            if (el) el.classList.toggle('ig-hidden', platform !== 'instagram');
        });

        // YouTube section
        if (ytSection) {
            ytSection.classList.toggle('visible', platform === 'youtube');
        }
    }

    tabInstagram.addEventListener('click', () => setPlatform('instagram'));
    tabYoutube.addEventListener('click', () => setPlatform('youtube'));

    // ═══════════════════════════════════════════════════════════════
    //  INSTAGRAM LOGIC (unchanged)
    // ═══════════════════════════════════════════════════════════════

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

    function formatDuration(seconds) {
        if (!seconds) return '0:00';
        const h = Math.floor(seconds / 3600);
        const m = Math.floor((seconds % 3600) / 60);
        const s = Math.floor(seconds % 60);
        if (h > 0) {
            return `${h}:${m.toString().padStart(2, '0')}:${s.toString().padStart(2, '0')}`;
        }
        return `${m}:${s.toString().padStart(2, '0')}`;
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

    // ═══════════════════════════════════════════════════════════════
    //  YOUTUBE LOGIC
    // ═══════════════════════════════════════════════════════════════

    // ─── YouTube Status Helpers ────────────────────────────────────
    function ytShowStatus(type, message) {
        ytStatusMessage.className = 'status-message visible ' + type;
        ytStatusIcon.className = ICONS[type] || ICONS.info;
        ytStatusText.textContent = message;
    }

    function ytHideStatus() {
        ytStatusMessage.className = 'status-message';
    }

    function ytHidePreview() {
        ytPreview.classList.remove('visible');
        ytQualityGrid.innerHTML = '';
        ytSelectedFormat = null;
        ytSelectedLabel = '';
    }

    function ytHideResults() {
        ytResultsSection.classList.remove('visible');
        ytVideoGrid.innerHTML = '';
    }

    // ─── Fetch YouTube Video Info ──────────────────────────────────
    ytFetchBtn.addEventListener('click', async () => {
        if (ytIsLoading) return;

        const url = ytUrlInput.value.trim();
        if (!url) {
            ytShowStatus('error', 'Please paste a YouTube video URL.');
            ytUrlInput.focus();
            return;
        }

        if (!YOUTUBE_RE.test(url)) {
            ytShowStatus('error', 'Invalid URL. Supports youtube.com/watch, youtu.be, and youtube.com/shorts links.');
            ytUrlInput.focus();
            return;
        }

        ytCurrentUrl = url;
        ytIsLoading = true;
        ytFetchBtn.disabled = true;
        ytFetchSpinner.classList.add('visible');
        ytFetchText.style.display = 'none';
        ytHideStatus();
        ytHidePreview();
        ytHideResults();

        try {
            const res = await fetch('/api/youtube/info', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ url }),
            });

            const data = await res.json();

            if (!res.ok) {
                throw new Error(data.detail || 'Failed to fetch video info');
            }

            // Populate preview
            ytThumb.src = data.thumbnail || '';
            ytDuration.textContent = formatDuration(data.duration);
            ytVideoTitle.textContent = data.title || 'Untitled Video';
            ytUploader.innerHTML = `<i class="ph-fill ph-user"></i><span>${escapeHTML(data.uploader || 'Unknown')}</span>`;
            ytViews.innerHTML = `<i class="ph ph-eye"></i><span>${formatNumber(data.view_count)} views</span>`;

            // Populate quality options
            ytQualityGrid.innerHTML = '';
            if (data.qualities && data.qualities.length > 0) {
                data.qualities.forEach((q, idx) => {
                    const btn = document.createElement('button');
                    btn.className = 'yt-quality-option';
                    btn.type = 'button';
                    btn.dataset.formatId = q.format_id;
                    btn.dataset.label = q.label;

                    const isAudio = q.height === 0;
                    const is4K = q.label.includes('4K');
                    const isHD = q.label.includes('HD') || q.label.includes('1080') || q.label.includes('720');

                    let labelClass = '';
                    if (is4K) labelClass = 'q-badge-4k';
                    else if (isHD) labelClass = 'q-badge-hd';

                    let iconHTML = '';
                    if (isAudio) {
                        iconHTML = `<i class="ph-fill ph-music-note q-audio-icon"></i>`;
                    }

                    btn.innerHTML = `
                        ${iconHTML}
                        <span class="q-label ${labelClass}">${escapeHTML(q.label)}</span>
                        ${q.size_label ? `<span class="q-size">${q.size_label}</span>` : ''}
                        <span class="q-ext">${q.ext}</span>
                        <div class="q-check"><i class="ph-bold ph-check"></i></div>
                    `;

                    btn.addEventListener('click', () => {
                        // Deselect all
                        ytQualityGrid.querySelectorAll('.yt-quality-option').forEach(el => el.classList.remove('selected'));
                        // Select this
                        btn.classList.add('selected');
                        ytSelectedFormat = q.format_id;
                        ytSelectedLabel = q.label;
                    });

                    ytQualityGrid.appendChild(btn);

                    // Auto-select the first (highest) quality
                    if (idx === 0) {
                        btn.classList.add('selected');
                        ytSelectedFormat = q.format_id;
                        ytSelectedLabel = q.label;
                    }
                });
            }

            ytPreview.classList.add('visible');
            ytShowStatus('success', `Found ${data.qualities.length} quality option(s)`);

        } catch (err) {
            ytShowStatus('error', err.message || 'Failed to fetch video info');
        } finally {
            ytIsLoading = false;
            ytFetchBtn.disabled = false;
            ytFetchSpinner.classList.remove('visible');
            ytFetchText.style.display = '';
        }
    });

    // Enter key support for YouTube
    ytUrlInput.addEventListener('keydown', (e) => {
        if (e.key === 'Enter') {
            ytFetchBtn.click();
        }
    });

    // ─── Download YouTube Video ────────────────────────────────────
    ytDownloadBtn.addEventListener('click', async () => {
        if (ytIsLoading) return;

        if (!ytSelectedFormat) {
            ytShowStatus('error', 'Please select a quality option first.');
            return;
        }

        if (!ytCurrentUrl) {
            ytShowStatus('error', 'No video URL. Please fetch video info first.');
            return;
        }

        ytIsLoading = true;
        ytDownloadBtn.disabled = true;
        ytDlSpinner.classList.add('visible');
        ytDlText.style.display = 'none';
        ytLoadingOverlay.classList.add('visible');
        ytLoadingText.textContent = `Downloading ${ytSelectedLabel}...`;
        ytHideStatus();
        ytHideResults();

        try {
            const res = await fetch('/api/youtube/download', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    url: ytCurrentUrl,
                    format_id: ytSelectedFormat,
                    quality_label: ytSelectedLabel,
                }),
            });

            const data = await res.json();

            if (!res.ok) {
                throw new Error(data.detail || 'Download failed');
            }

            // Show result
            ytLoadingOverlay.classList.remove('visible');
            ytShowStatus('success', 'Video ready for download!');

            ytResultsTitle.textContent = 'Your Video';
            ytVideoGrid.innerHTML = '';

            const card = document.createElement('div');
            card.className = 'video-card';

            let metaHTML = '';
            if (data.video.quality) {
                metaHTML += `<span class="meta-item"><i class="ph ph-monitor"></i>${escapeHTML(data.video.quality)}</span>`;
            }
            if (data.video.duration) {
                metaHTML += `<span class="meta-item"><i class="ph ph-clock"></i>${formatDuration(data.video.duration)}</span>`;
            }
            if (data.video.uploader) {
                metaHTML += `<span class="meta-item"><i class="ph ph-user"></i>${escapeHTML(data.video.uploader)}</span>`;
            }

            card.innerHTML = `
                <div class="meta-icon" style="background:rgba(255,0,0,0.1);border-color:rgba(255,0,0,0.2);color:#ff4444;">
                    <i class="ph-fill ph-youtube-logo"></i>
                </div>
                <div class="video-info">
                    <p class="video-name" title="${escapeHTML(data.video.title || data.video.filename)}">${escapeHTML(data.video.title || data.video.filename)}</p>
                    <div class="video-metadata">${metaHTML}</div>
                </div>
                <a href="${data.video.download_url}" class="glass-btn btn-sm action-btn" download>
                    <i class="ph-bold ph-download-simple"></i>
                </a>
            `;

            ytVideoGrid.appendChild(card);
            ytResultsSection.classList.add('visible');

        } catch (err) {
            ytLoadingOverlay.classList.remove('visible');
            ytShowStatus('error', err.message || 'Download failed');
        } finally {
            ytIsLoading = false;
            ytDownloadBtn.disabled = false;
            ytDlSpinner.classList.remove('visible');
            ytDlText.style.display = '';
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
    setPlatform('instagram');
    setMode('single');
    urlInput.focus();
    loadConfig();

})();
