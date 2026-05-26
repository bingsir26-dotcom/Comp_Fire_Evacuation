/**
 * 慧焰導行 – Service Worker (PWA 離線支援)
 * 快取主要靜態資源，確保基本頁面可在離線時載入
 */

const CACHE_NAME = 'fire-escape-v1';
const STATIC_ASSETS = [
    '/',
    '/static/index.html',
    '/static/style.css',
    '/static/app.js',
    '/static/manifest.json',
    '/static/icons/icon-192.png',
];

// 安裝事件：預快取靜態資源
self.addEventListener('install', (event) => {
    event.waitUntil(
        caches.open(CACHE_NAME).then((cache) => {
            console.log('[SW] 快取靜態資源');
            return cache.addAll(STATIC_ASSETS).catch((err) => {
                console.warn('[SW] 部分資源快取失敗:', err);
            });
        })
    );
    self.skipWaiting();
});

// 啟動事件：清理舊快取
self.addEventListener('activate', (event) => {
    event.waitUntil(
        caches.keys().then((keys) => {
            return Promise.all(
                keys.filter((key) => key !== CACHE_NAME)
                    .map((key) => caches.delete(key))
            );
        })
    );
    self.clients.claim();
});

// 攔截請求：快取優先策略
self.addEventListener('fetch', (event) => {
    // 只處理 GET 請求
    if (event.request.method !== 'GET') return;

    // 跳過 WebSocket 和 API 請求（不緩存動態數據）
    const url = new URL(event.request.url);
    if (url.pathname.startsWith('/ws') || url.pathname.startsWith('/api/') || url.pathname.startsWith('/outputs/')) {
        return;
    }

    event.respondWith(
        caches.match(event.request).then((cached) => {
            // 快取命中：返回快取，同時背景更新
            const fetchPromise = fetch(event.request).then((response) => {
                if (response && response.status === 200) {
                    const clone = response.clone();
                    caches.open(CACHE_NAME).then((cache) => {
                        cache.put(event.request, clone);
                    });
                }
                return response;
            }).catch(() => cached);

            return cached || fetchPromise;
        })
    );
});