/**
 * 慧焰導行 – PWA 前端應用
 * - WebSocket 即時接收感測器數據
 * - Canvas 繪製火焰燈動畫 + 20×20 熱力圖
 * - 按鈕觸發逃生路線生成 (Python / AI)
 * - 無法逃生時觸發避難模式 + 警報 + SOS
 */

// ============================================================
// 全域狀態
// ============================================================
const STATE = {
    ws: null,
    connected: false,
    serialConnected: false,
    brightness: [0, 0, 0, 0, 0],
    knob: [0, 0, 0, 0, 0],
    matrix4x4: [],
    matrix20x20: [],
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
const escapePlaceholder = $('escape-placeholder');
const escapeInfo = $('escape-info');
const escapeSteps = $('escape-steps');
const btnPython = $('btn-escape-python');
const btnAI = $('btn-escape-ai');
const alertBanner = $('alert-banner');
const sosMessage = $('sos-message');

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

    updateSerialStatus();
    updateSensorDisplay();
    renderHeatmap();
}

// ============================================================
// 序列埠狀態顯示
// ============================================================
function updateSerialStatus() {
    if (STATE.serialConnected) {
        serialStatusEl.textContent = 'Arduino: ✅ 已連線 COM3';
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
    alertBanner.innerHTML = '🚨 無法逃生！所有安全路徑已被火勢封鎖';

    // 顯示求救通知
    sosMessage.style.display = 'block';
    sosMessage.innerHTML = `📢 已發送求救通知：有人被困在電腦室！請撥打 119<br>
    <span style="font-size:13px;color:#ffaaaa;">
    最安全避難區指數：${data.safest_value} / 10 &nbsp;|&nbsp; 可到達範圍：${data.reachable_cells} 格
    </span>`;

    // 顯示避難圖
    if (data.shelter_image) {
        escapeImg.src = data.shelter_image + '?t=' + Date.now();
        escapeImg.style.display = 'block';
        escapePlaceholder.style.display = 'none';
        escapeInfo.style.display = 'block';
        escapeSteps.textContent = data.message;
        escapeSteps.style.color = '#ff4444';
    }

    // 觸發警報
    triggerEmergencyAlarm();
}

function hideBlockedAlert() {
    alertBanner.style.display = 'none';
    sosMessage.style.display = 'none';
}

// ============================================================
// 逃生路線生成
// ============================================================
async function generateEscape() {
    hideBlockedAlert();
    btnPython.disabled = true;
    btnPython.textContent = '⏳ 生成中...';
    escapePlaceholder.innerHTML = '<span>計算中，請稍候...</span>';
    escapePlaceholder.style.display = 'flex';
    escapeSteps.style.color = '#00cc44';

    try {
        const resp = await fetch('/api/generate-escape', { method: 'POST' });
        const data = await resp.json();

        if (data.status === 'escaped') {
            escapeImg.src = data.escape_image + '?t=' + Date.now();
            escapeImg.style.display = 'block';
            escapePlaceholder.style.display = 'none';
            escapeInfo.style.display = 'block';
            escapeSteps.textContent = `✅ ${data.message}`;
        } else if (data.status === 'blocked') {
            showBlockedAlert(data);
        } else {
            escapePlaceholder.innerHTML = '<span style="color:#ff6b6b">❌ 生成失敗</span>';
        }
    } catch (e) {
        console.error('生成逃生路線失敗:', e);
        escapePlaceholder.innerHTML = '<span style="color:#ff6b6b">❌ 網路錯誤</span>';
    } finally {
        btnPython.disabled = false;
        btnPython.textContent = '🧭 生成逃生路線 (Python)';
    }
}

async function generateAIEscape() {
    hideBlockedAlert();
    btnAI.disabled = true;
    btnAI.textContent = '⏳ AI 生成中 (約 30 秒)...';
    escapePlaceholder.style.display = 'flex';
    escapePlaceholder.innerHTML = '<span>🤖 正在呼叫 AI 生成，請耐心等候...</span>';
    escapeSteps.style.color = '#00cc44';

    try {
        const resp = await fetch('/api/generate-ai-escape', { method: 'POST' });
        const data = await resp.json();

        if (data.status === 'escaped') {
            escapeImg.src = data.ai_image + '?t=' + Date.now();
            escapeImg.style.display = 'block';
            escapePlaceholder.style.display = 'none';
            escapeInfo.style.display = 'block';
            escapeSteps.textContent = `✅ AI 逃生路線生成完成！路線共 ${data.path_length} 步`;
        } else if (data.status === 'blocked') {
            showBlockedAlert(data);
        } else {
            escapePlaceholder.innerHTML = '<span style="color:#ff6b6b">❌ AI 生成失敗，請檢查 API Key</span>';
        }
    } catch (e) {
        console.error('AI 生成失敗:', e);
        escapePlaceholder.innerHTML = '<span style="color:#ff6b6b">❌ 網路錯誤或後端未回應</span>';
    } finally {
        btnAI.disabled = false;
        btnAI.textContent = '🤖 AI 生成逃生路線';
    }
}

// ============================================================
// 初始化
// ============================================================
function init() {
    createFlameLights();
    connectWebSocket();
    console.log('🔥 慧焰導行 PWA 已初始化');
}

if ('serviceWorker' in navigator) {
    navigator.serviceWorker.register('/static/sw.js')
        .then(reg => console.log('[SW] 已註冊:', reg.scope))
        .catch(err => console.error('[SW] 註冊失敗:', err));
}

document.addEventListener('DOMContentLoaded', init);