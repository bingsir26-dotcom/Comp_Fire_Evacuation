/**
 * 基於生成式人工智能輔助的室內火災逃生最優路徑判斷與指示系統 – PWA 前端應用
 * - WebSocket 即時接收感測器數據
 * - Canvas 繪製火焰燈動畫 + 20×20 熱力圖
 * - 本地風險感知路線自動計算與 Canvas 疊加顯示
 * - 無法逃生時觸發避難模式 + 警報 + SOS
 */

// ============================================================
// 全域狀態
// ============================================================
const STATE = {
    ws: null,
    connected: false,
    serialConnected: false,
    serialPort: '',
    brightness: [0, 0, 0, 0, 0],
    knob: [0, 0, 0, 0, 0],
    matrix4x4: [],
    matrix20x20: [],
    routeActive: false,
    routeBusy: false,
    lastRoutePlanAt: 0,
    routeData: null,
    latestRouteResult: null,
    routeMonitoring: false,
    blockedAlertActive: false,
    guidanceBusy: false,
    guidanceSignature: '',
    guidanceUnavailableSignature: '',
    guidanceRequestId: 0,
    guidanceText: '',
    guidanceAudioUrl: '',
    guidanceVersion: 0,
    guidanceAutoplayAttemptVersion: 0,
    autoSpeechEnabled: false,
    shareRefreshTimer: null,
};

// ============================================================
// DOM 元素快取
// ============================================================
const $ = (id) => document.getElementById(id);
const statusEl = $('connection-status');
const serialStatusEl = $('serial-status');
const sensorValuesEl = $('sensor-values');
const flameGridEl = $('flame-grid');
const heatmapCanvas = $('heatmap-canvas');
const heatmapCtx = heatmapCanvas.getContext('2d');
const escapeImg = $('escape-image');
const escapeCanvas = $('escape-canvas');
const escapeCtx = escapeCanvas.getContext('2d');
const escapePlaceholder = $('escape-placeholder');
const escapeInfo = $('escape-info');
const escapeSteps = $('escape-steps');
const genaiStatus = $('genai-status');
const genaiGuidanceText = $('genai-guidance-text');
const autoSpeakToggle = $('auto-speak-toggle');
const autoSpeakNote = $('auto-speak-note');
const guidanceAudio = $('guidance-audio');
const alertBanner = $('alert-banner');
const sosMessage = $('sos-message');
const shareQrImage = $('share-qr-image');

const floorplanImage = new Image();
floorplanImage.src = '/api/floorplan';
floorplanImage.onload = () => {
    if (STATE.routeData) renderEscapeRoute(STATE.routeData);
};

// ============================================================
// WebSocket 連線
// ============================================================
function connectWebSocket() {
    const protocol = location.protocol === 'https:' ? 'wss:' : 'ws:';
    const wsUrl = `${protocol}//${location.host}/ws`;

    STATE.ws = new WebSocket(wsUrl);

    STATE.ws.onopen = () => {
        STATE.connected = true;
        statusEl.textContent = '● 已連線';
        statusEl.className = 'status-connected';
        console.log('[WS] 已連線');

        setInterval(() => {
            if (STATE.ws && STATE.ws.readyState === WebSocket.OPEN) {
                STATE.ws.send('ping');
            }
        }, 30000);
    };

    STATE.ws.onmessage = (event) => {
        try {
            const data = JSON.parse(event.data);
            if (data.type === 'sensor_update') {
                handleSensorUpdate(data);
            } else if (data.type === 'guidance_update') {
                applyGuidance(data);
            }
        } catch (e) {
            console.error('[WS] 解析錯誤:', e);
        }
    };

    STATE.ws.onclose = () => {
        STATE.connected = false;
        statusEl.textContent = '● 離線';
        statusEl.className = 'status-disconnected';
        console.log('[WS] 已斷線，5 秒後重連...');
        setTimeout(connectWebSocket, 5000);
    };

    STATE.ws.onerror = (err) => {
        console.error('[WS] 錯誤:', err);
    };
}

function handleSensorUpdate(data) {
    STATE.brightness = data.brightness || STATE.brightness;
    STATE.knob = data.knob || STATE.knob;
    STATE.matrix4x4 = data.matrix_4x4 || STATE.matrix4x4;
    STATE.matrix20x20 = data.matrix_20x20 || STATE.matrix20x20;
    STATE.serialConnected = data.serial_connected || false;
    STATE.serialPort = data.port || STATE.serialPort;

    updateSerialStatus();
    updateSensorDisplay();
    renderHeatmap();
    maybeRefreshSafeRoute();
}

// ============================================================
// 序列埠狀態顯示
// ============================================================
function updateSerialStatus() {
    if (STATE.serialConnected) {
        const portText = STATE.serialPort ? ` ${STATE.serialPort}` : '';
        serialStatusEl.textContent = `Arduino: ✅ 已連線${portText}`;
        serialStatusEl.style.color = '#4CAF50';
    } else {
        serialStatusEl.textContent = 'Arduino: ❌ 未連線';
        serialStatusEl.style.color = '#FF6B6B';
    }

    const vals = STATE.brightness.map(v => Math.round(v)).join(', ');
    sensorValuesEl.textContent = `感測器: [${vals}]`;
}

// ============================================================
// 感測器值顯示
// ============================================================
function updateSensorDisplay() {
    const lights = flameGridEl.querySelectorAll('.flame-light');
    lights.forEach((light, i) => {
        const b = STATE.brightness[i] || 0;
        const normalized = b / 255;
        const fillEl = light.querySelector('.flame-fill');
        const valEl = light.querySelector('.flame-value');

        if (fillEl) {
            fillEl.style.height = (normalized * 100) + '%';
            const r = Math.round(255 * normalized);
            const g = Math.round(180 - normalized * 80);
            const bl = Math.round(50 - normalized * 30);
            fillEl.style.background = `rgb(${r}, ${g}, ${bl})`;
            fillEl.style.boxShadow = normalized > 0.1
                ? `0 0 ${10 + normalized * 20}px rgba(${r}, ${g}, ${bl}, 0.6)`
                : 'none';
        }
        if (valEl) {
            valEl.textContent = Math.round(b);
        }
    });
}

// ============================================================
// 火焰燈初始化
// ============================================================
function createFlameLights() {
    const colors = ['#FF4500', '#FF6347', '#FFD700', '#FF8C00', '#FF1493'];
    const labels = ['左上', '左下', '中間', '右上', '右下'];

    for (let i = 0; i < 5; i++) {
        const light = document.createElement('div');
        light.className = 'flame-light';
        light.innerHTML = `
            <div class="flame-bar">
                <div class="flame-fill" style="height:0%; background:${colors[i]}"></div>
            </div>
            <div class="flame-value">0</div>
            <div class="flame-label">${labels[i]}</div>
        `;
        flameGridEl.appendChild(light);
    }
}

// ============================================================
// 20×20 熱力圖渲染 (Canvas)
// ============================================================
function renderHeatmap() {
    const matrix = STATE.matrix20x20;
    if (!matrix || matrix.length === 0) return;

    const canvas = heatmapCanvas;
    const ctx = heatmapCtx;
    const size = 20;
    const cellW = canvas.width / size;
    const cellH = canvas.height / size;

    ctx.clearRect(0, 0, canvas.width, canvas.height);

    for (let r = 0; r < size; r++) {
        for (let c = 0; c < size; c++) {
            let val = 0;
            if (r < matrix.length && c < (matrix[0] || []).length) {
                val = matrix[r][c];
            }
            const normalized = Math.min(1, Math.max(0, val / 10));
            const color = dangerColor(normalized);
            ctx.fillStyle = color;
            ctx.fillRect(c * cellW, r * cellH, cellW + 0.5, cellH + 0.5);
        }
    }
}

function dangerColor(t) {
    let r, g, b;
    if (t < 0.25) {
        const s = t / 0.25;
        r = Math.round(255 * s);
        g = 220 + Math.round(35 * s);
        b = Math.round(50 * (1 - s));
    } else if (t < 0.5) {
        const s = (t - 0.25) / 0.25;
        r = 255;
        g = Math.round(255 * (1 - s * 0.5));
        b = Math.round(50 * (1 - s));
    } else if (t < 0.75) {
        const s = (t - 0.5) / 0.25;
        r = 255;
        g = Math.round(127 * (1 - s));
        b = Math.round(25 * (1 - s));
    } else {
        const s = (t - 0.75) / 0.25;
        r = Math.round(200 - 60 * s);
        g = Math.round(30 * (1 - s));
        b = Math.round(10 * (1 - s));
    }
    return `rgb(${r}, ${g}, ${b})`;
}

// ============================================================
// 警報函數（震動 + 音效）
// ============================================================
function triggerEmergencyAlarm() {
    // 震動 (PWA / mobile)
    if (navigator.vibrate) {
        navigator.vibrate([500, 200, 500, 200, 500]);
    }

    // 播放警報音效 (Web Audio API)
    try {
        const ctx = new (window.AudioContext || window.webkitAudioContext)();
        const playBeep = (freq, duration, startTime) => {
            const osc = ctx.createOscillator();
            const gain = ctx.createGain();
            osc.type = 'square';
            osc.frequency.value = freq;
            gain.gain.value = 0.3;
            gain.gain.exponentialRampToValueAtTime(0.001, startTime + duration);
            osc.connect(gain);
            gain.connect(ctx.destination);
            osc.start(startTime);
            osc.stop(startTime + duration);
        };

        const now = ctx.currentTime;
        playBeep(800, 0.2, now);
        playBeep(600, 0.2, now + 0.25);
        playBeep(800, 0.2, now + 0.5);
        playBeep(600, 0.2, now + 0.75);
        playBeep(800, 0.4, now + 1.0);
    } catch (e) {
        // 音效失敗也不影響主要功能
    }
}

function showBlockedAlert(data) {
    // 顯示紅色警報橫幅
    alertBanner.style.display = 'block';
    alertBanner.innerHTML = '🚨 暫時無法建立安全逃生路線，系統正持續監測環境';

    // 顯示求救通知
    sosMessage.style.display = 'block';
    sosMessage.innerHTML = `📢 請先就地避難並求救；系統會每秒重新判斷，通道恢復安全後將自動重新導引。<br>
    <span style="font-size:13px;color:#ffaaaa;">
    最安全避難區指數：${data.safest_value} / 10 &nbsp;|&nbsp; 可到達範圍：${data.reachable_cells} 格
    </span>`;

    // 顯示避難圖
    if (data.shelter_image) {
        escapeCanvas.style.display = 'none';
        escapeImg.src = data.shelter_image + '?t=' + Date.now();
        escapeImg.style.display = 'block';
        escapePlaceholder.style.display = 'none';
        escapeInfo.style.display = 'block';
        escapeSteps.textContent = data.message;
        escapeSteps.style.color = '#ff4444';
    }

    // 僅在首次進入避難狀態時播放警報，避免每秒監測時重複鳴響。
    if (!STATE.blockedAlertActive) triggerEmergencyAlarm();
    STATE.blockedAlertActive = true;
}

function hideBlockedAlert() {
    alertBanner.style.display = 'none';
    sosMessage.style.display = 'none';
    STATE.blockedAlertActive = false;
}

// ============================================================
// 本地安全路線：Canvas 視覺化 + 自動重規劃
// ============================================================
function rgbaDanger(value, alpha) {
    const normalized = Math.min(1, Math.max(0, value / 10));
    const color = dangerColor(normalized).match(/\d+/g).map(Number);
    return `rgba(${color[0]}, ${color[1]}, ${color[2]}, ${alpha})`;
}

function cellCenter(cell, cellW, cellH) {
    return [(cell[1] + 0.5) * cellW, (cell[0] + 0.5) * cellH];
}

function drawRouteMarker(ctx, x, y, color, label, symbol) {
    ctx.save();
    ctx.shadowColor = color;
    ctx.shadowBlur = 16;
    ctx.fillStyle = color;
    ctx.beginPath();
    ctx.arc(x, y, 15, 0, Math.PI * 2);
    ctx.fill();
    ctx.shadowBlur = 0;
    ctx.fillStyle = '#fff';
    ctx.font = 'bold 16px sans-serif';
    ctx.textAlign = 'center';
    ctx.textBaseline = 'middle';
    ctx.fillText(symbol, x, y + 0.5);
    ctx.font = 'bold 13px sans-serif';
    const labelWidth = ctx.measureText(label).width + 14;
    const labelX = Math.max(5, Math.min(escapeCanvas.width - labelWidth - 5, x + 18));
    const labelY = Math.max(5, y - 28);
    ctx.fillStyle = 'rgba(5, 14, 25, 0.88)';
    ctx.fillRect(labelX, labelY, labelWidth, 22);
    ctx.fillStyle = '#fff';
    ctx.textAlign = 'left';
    ctx.fillText(label, labelX + 7, labelY + 12);
    ctx.restore();
}

function drawDirectionArrow(ctx, from, to) {
    const angle = Math.atan2(to[1] - from[1], to[0] - from[0]);
    const x = (from[0] + to[0]) / 2;
    const y = (from[1] + to[1]) / 2;
    ctx.save();
    ctx.translate(x, y);
    ctx.rotate(angle);
    ctx.fillStyle = '#f7fff9';
    ctx.beginPath();
    ctx.moveTo(8, 0);
    ctx.lineTo(-6, -5);
    ctx.lineTo(-6, 5);
    ctx.closePath();
    ctx.fill();
    ctx.restore();
}

function renderEscapeRoute(data) {
    if (!data || !data.risk_grid) return;
    const ctx = escapeCtx;
    const width = escapeCanvas.width;
    const height = escapeCanvas.height;
    const size = data.risk_grid.length || 20;
    const cellW = width / size;
    const cellH = height / size;
    const obstacleSet = new Set((data.structural_obstacles || []).map(cell => cell.join(',')));
    const fireSet = new Set((data.fire_cells || []).map(cell => cell.join(',')));

    ctx.clearRect(0, 0, width, height);
    ctx.fillStyle = '#08111d';
    ctx.fillRect(0, 0, width, height);
    if (floorplanImage.complete && floorplanImage.naturalWidth) {
        ctx.drawImage(floorplanImage, 0, 0, width, height);
        ctx.fillStyle = 'rgba(3, 10, 18, 0.18)';
        ctx.fillRect(0, 0, width, height);
    }

    // 熱風險透明覆蓋層：火源愈強，顏色愈明顯。
    for (let row = 0; row < size; row++) {
        for (let col = 0; col < size; col++) {
            const danger = data.risk_grid[row][col] || 0;
            if (danger > 0.15) {
                ctx.fillStyle = rgbaDanger(danger, 0.10 + (danger / 10) * 0.32);
                ctx.fillRect(col * cellW, row * cellH, cellW + 1, cellH + 1);
            }
        }
    }

    // 桌子／固定障礙物。
    for (const cell of data.structural_obstacles || []) {
        ctx.fillStyle = 'rgba(8, 21, 35, 0.68)';
        ctx.fillRect(cell[1] * cellW + 1, cell[0] * cellH + 1, cellW - 2, cellH - 2);
    }

    // 火源及一格安全緩衝區，清楚展示「為何要繞路」。
    for (const cell of data.blocked_cells || []) {
        const key = cell.join(',');
        if (!obstacleSet.has(key) && !fireSet.has(key)) {
            ctx.strokeStyle = 'rgba(255, 180, 0, 0.85)';
            ctx.lineWidth = 2;
            ctx.strokeRect(cell[1] * cellW + 2, cell[0] * cellH + 2, cellW - 4, cellH - 4);
        }
    }
    for (const cell of data.fire_cells || []) {
        const [x, y] = cellCenter(cell, cellW, cellH);
        const glow = ctx.createRadialGradient(x, y, 2, x, y, Math.max(cellW, cellH));
        glow.addColorStop(0, 'rgba(255, 45, 0, 0.96)');
        glow.addColorStop(0.55, 'rgba(255, 120, 0, 0.68)');
        glow.addColorStop(1, 'rgba(255, 60, 0, 0)');
        ctx.fillStyle = glow;
        ctx.fillRect(x - cellW, y - cellH, cellW * 2, cellH * 2);
    }

    const path = data.path || [];
    if (path.length) {
        const points = path.map(cell => cellCenter(cell, cellW, cellH));
        ctx.lineJoin = 'round';
        ctx.lineCap = 'round';
        ctx.beginPath();
        points.forEach(([x, y], index) => index ? ctx.lineTo(x, y) : ctx.moveTo(x, y));
        ctx.strokeStyle = 'rgba(0, 20, 8, 0.8)';
        ctx.lineWidth = 16;
        ctx.stroke();
        ctx.strokeStyle = '#00c853';
        ctx.lineWidth = 10;
        ctx.stroke();
        ctx.strokeStyle = '#effff4';
        ctx.lineWidth = 2;
        ctx.stroke();

        const turns = (data.route?.turn_points || []).map(cell => cellCenter(cell, cellW, cellH));
        for (let index = 0; index < turns.length - 1; index++) drawDirectionArrow(ctx, turns[index], turns[index + 1]);
    }

    if (data.start) {
        const [x, y] = cellCenter(data.start, cellW, cellH);
        drawRouteMarker(ctx, x, y, '#1769ff', '起點', '●');
    }
    const selectedExit = data.route?.exit;
    if (selectedExit?.cell) {
        const [x, y] = cellCenter(selectedExit.cell, cellW, cellH);
        drawRouteMarker(ctx, x, y, '#00b84d', selectedExit.name, '★');
    }

    ctx.save();
    ctx.fillStyle = 'rgba(5, 14, 25, 0.82)';
    ctx.fillRect(12, height - 43, 285, 30);
    ctx.fillStyle = '#fff';
    ctx.font = 'bold 14px sans-serif';
    ctx.textAlign = 'left';
    ctx.textBaseline = 'middle';
    ctx.fillText('綠線：安全路線　橙框：安全緩衝　紅光：火源', 22, height - 28);
    ctx.restore();

    escapeImg.style.display = 'none';
    escapeCanvas.style.display = 'block';
    escapePlaceholder.style.display = 'none';
}

function routeMessage(data, autoRefresh) {
    const route = data.route || {};
    const refresh = autoRefresh ? '↻ 已因感測器變化重規劃　' : '✅ ';
    return `${refresh}${data.message}<br><small>步數 ${route.distance_steps} ｜平均風險 ${route.mean_risk}/10 ｜最近火源 ${route.min_clearance_cells} 格 ｜本地計算 ${route.compute_ms} ms</small>`;
}

function guidanceRouteSignature(data) {
    const route = data?.route || {};
    const status = data?.status || route.status || 'unknown';
    const exit = status === 'escaped' ? (route.exit?.id || route.exit?.name || 'safe-exit') : 'shelter';
    // 路線可每秒更新；只有可否逃生或安全出口改變才會請求新的文字／語音。
    return [status, exit].join('|');
}
function setGenaiStatus(text, state) {
    genaiStatus.textContent = text;
    genaiStatus.className = `genai-status ${state}`;
}

function updateAutoSpeechControl(message = '') {
    if (!autoSpeakToggle || !autoSpeakNote) return;
    const enabled = STATE.autoSpeechEnabled;
    // 啟用後隱藏按鈕，避免展示畫面留下多餘控制；瀏覽器阻擋時才重新顯示供再按一次。
    autoSpeakToggle.hidden = enabled && !message;
    autoSpeakToggle.setAttribute('aria-pressed', String(enabled));
    autoSpeakToggle.textContent = '🔈 啟用語音提醒';
    autoSpeakNote.className = `genai-auto-note${enabled ? ' is-enabled' : ''}${message ? ' is-blocked' : ''}`;
    autoSpeakNote.textContent = message || (enabled
        ? '✅ 語音提醒已開啟：只在可逃生、就地避難或安全出口改變時自動播放普通話指引。'
        : '首次展示請按一下啟用語音提醒；之後只在可逃生、就地避難或安全出口改變時自動以普通話播報。');
}
function loadAutoSpeechPreference() {
    // 每次重新開頁都要求主控端按一次，避免把舊設定誤當成新的瀏覽器播放授權。
    STATE.autoSpeechEnabled = false;
    updateAutoSpeechControl();
}

function saveAutoSpeechPreference(enabled) {
    STATE.autoSpeechEnabled = enabled;
    updateAutoSpeechControl();
}
async function playGuidanceAudio({ allowBrowserFallback = true } = {}) {
    if (STATE.guidanceAudioUrl) {
        guidanceAudio.currentTime = 0;
        try {
            await guidanceAudio.play();
            return true;
        } catch (error) {
            console.warn('同步語音播放失敗:', error);
            if (allowBrowserFallback) speakWithBrowserFallback();
            throw error;
        }
    }
    if (allowBrowserFallback) {
        speakWithBrowserFallback();
        return true;
    }
    return false;
}

function autoPlayUpdatedGuidance(data) {
    const version = Number(data.version || STATE.guidanceVersion || 0);
    if (
        !STATE.autoSpeechEnabled ||
        !data.announce ||
        !data.updated ||
        !version ||
        version <= STATE.guidanceAutoplayAttemptVersion
    ) return;

    STATE.guidanceAutoplayAttemptVersion = version;
    playGuidanceAudio({ allowBrowserFallback: false }).catch(() => {
        updateAutoSpeechControl('瀏覽器暫時阻止播放；請在主控端再按一次「啟用語音提醒」。');
    });
}
function applyGuidance(data) {
    const isDeepSeek = data.source === 'deepseek';
    STATE.guidanceText = data.guidance || '';
    STATE.guidanceAudioUrl = data.audio_url || '';
    STATE.guidanceVersion = Number(data.version || STATE.guidanceVersion);
    genaiGuidanceText.textContent = STATE.guidanceText || 'AI 指引暫未更新，請依綠色已驗證路線及現場安全程序行動。';

    if (STATE.guidanceAudioUrl) {
        guidanceAudio.src = `${STATE.guidanceAudioUrl}?v=${STATE.guidanceVersion}`;
    }

    if (isDeepSeek) {
        const action = data.update_policy === 'cooldown' || data.update_policy === 'unchanged'
            ? '已沿用同一指引（僅重大變化更新）'
            : (data.tts_mode === 'edge-tts' ? 'DeepSeek 指引與普通話語音已同步' : 'DeepSeek 指引已同步');
        setGenaiStatus(action, 'is-ready');
    } else {
        const detail = String(data.error || '');
        if (detail.includes('尚未設定 DEEPSEEK_API_KEY')) {
            setGenaiStatus('未讀取到 DeepSeek API Key，請檢查 .env 後重啟伺服器', 'is-unavailable');
        } else if (detail.includes('HTTP 401')) {
            setGenaiStatus('DeepSeek API Key 無效或已失效，正使用安全固定指引', 'is-unavailable');
        } else if (detail.includes('HTTP 402') || detail.includes('HTTP 429')) {
            setGenaiStatus('DeepSeek 額度或連線繁忙，正使用安全固定指引', 'is-unavailable');
        } else {
            setGenaiStatus('AI 暫不可用，正使用安全固定指引', 'is-unavailable');
        }
    }

    autoPlayUpdatedGuidance(data);
}

async function requestGenerativeGuidance(routeData) {
    if (!routeData || STATE.guidanceBusy) return;
    const signature = guidanceRouteSignature(routeData);
    const significantChange = signature !== STATE.guidanceSignature;
    if (!significantChange && STATE.guidanceSignature) return;

    STATE.guidanceBusy = true;
    setGenaiStatus('正在建立情境指引', 'is-generating');

    try {
        const resp = await fetch('/api/generative-guidance', { method: 'POST' });
        const data = await resp.json();
        if (!resp.ok) throw new Error(data.detail?.message || data.detail || '伺服器未能更新指引');
        STATE.guidanceSignature = signature;
        applyGuidance(data);
    } catch (error) {
        console.warn('生成式 AI 指引暫不可用:', error);
        genaiGuidanceText.textContent = '請依綠色已驗證路線及現場安全程序行動。';
        setGenaiStatus('指引同步失敗', 'is-error');
    } finally {
        STATE.guidanceBusy = false;
    }
}
function speakWithBrowserFallback() {
    if (!STATE.guidanceText || !('speechSynthesis' in window)) return;
    window.speechSynthesis.cancel();
    const utterance = new SpeechSynthesisUtterance(STATE.guidanceText);
    utterance.lang = 'zh-CN';
    utterance.rate = 0.93;
    utterance.pitch = 1;
    window.speechSynthesis.speak(utterance);
}

autoSpeakToggle.addEventListener('click', () => {
    // 這一次使用者操作是瀏覽器允許後續自動語音的必要解鎖動作。
    saveAutoSpeechPreference(true);
});

async function requestSafeRoute(autoRefresh = false) {
    if (STATE.routeBusy) return;
    STATE.routeBusy = true;
    STATE.lastRoutePlanAt = Date.now();
    if (!autoRefresh) {
        hideBlockedAlert();
        escapePlaceholder.innerHTML = '<span>正在分析火場風險、安全緩衝與可行走道...</span>';
        escapePlaceholder.style.display = 'flex';
        escapeSteps.style.color = '#00cc44';
    }

    try {
        const resp = await fetch(`/api/generate-escape${autoRefresh ? '?monitor=1' : ''}`, { method: 'POST' });
        const data = await resp.json();
        if (!resp.ok) throw new Error(data.detail || '伺服器未能計算路線');

        if (data.status === 'escaped') {
            const recoveredFromBlocked = STATE.blockedAlertActive;
            STATE.routeActive = true;
            STATE.routeMonitoring = true;
            STATE.routeData = data;
            STATE.latestRouteResult = data;
            hideBlockedAlert();
            renderEscapeRoute(data);
            escapeInfo.style.display = 'block';
            escapeSteps.innerHTML = `${recoveredFromBlocked ? '✅ 通道恢復安全，已自動重新規劃。<br>' : ''}${routeMessage(data, autoRefresh)}`;
            requestGenerativeGuidance(data);
        } else if (data.status === 'blocked') {
            STATE.routeActive = false;
            STATE.routeMonitoring = true;
            STATE.routeData = null;
            STATE.latestRouteResult = data;
            escapeCanvas.style.display = 'none';
            showBlockedAlert(data);
            requestGenerativeGuidance(data);
        } else {
            throw new Error('未知的路線計算結果');
        }
    } catch (error) {
        console.error('本地安全路線計算失敗:', error);
        if (!autoRefresh) {
            escapePlaceholder.innerHTML = '<span style="color:#ff6b6b">❌ 無法計算路線，請確認後端是否已啟動</span>';
        }
    } finally {
        STATE.routeBusy = false;
    }
}

function maybeRefreshSafeRoute() {
    if (!STATE.routeMonitoring || STATE.routeBusy || Date.now() - STATE.lastRoutePlanAt < 1000) return;
    requestSafeRoute(true);
}

// ============================================================
// QR Code 即時展示
// ============================================================
function scheduleShareRefresh(data) {
    clearTimeout(STATE.shareRefreshTimer);
    const tunnel = data.tunnel || {};
    if (tunnel.enabled && !tunnel.active && ['starting', 'idle'].includes(tunnel.status)) {
        STATE.shareRefreshTimer = setTimeout(loadShareCode, 1800);
    }
}

async function loadShareCode() {
    try {
        const resp = await fetch('/api/share', { cache: 'no-store' });
        const data = await resp.json();
        scheduleShareRefresh(data);
        if (!data.available || !data.url) throw new Error(data.message || '找不到分享網址');
        shareQrImage.src = `${data.qr_image}?t=${Date.now()}`;
        shareQrImage.style.display = 'block';
    } catch (error) {
        console.warn('QR Code 分享功能暫不可用:', error);
        shareQrImage.style.display = 'none';
    }
}

// ============================================================
// 初始化
// ============================================================
function init() {
    loadAutoSpeechPreference();
    createFlameLights();
    connectWebSocket();
    STATE.routeMonitoring = true;
    requestSafeRoute(false);
    loadShareCode();
    console.log('🔥 基於生成式人工智能輔助的室內火災逃生最優路徑判斷與指示系統 PWA 已初始化：本地安全路線已自動啟動');
}

if ('serviceWorker' in navigator) {
    navigator.serviceWorker.register('/static/sw.js')
        .then(reg => console.log('[SW] 已註冊:', reg.scope))
        .catch(err => console.error('[SW] 註冊失敗:', err));
}

document.addEventListener('DOMContentLoaded', init);
