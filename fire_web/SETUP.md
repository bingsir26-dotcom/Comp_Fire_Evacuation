# 🔥 慧焰導行 – 新電腦環境搭建指南

> 從零開始在新電腦上跑起整個火災智慧逃生系統。

---

## 1. 專案目錄結構（搬到新電腦後應保持）

```
Comp_Fire_Evacuation/          ← 專案根目錄
├── .env                        ← ⚠️ 需自行建立（見第 4 節）
├── .gitignore
├── 慧焰導行 – 室內火災智慧逃生系統.docx
├── Fire_sim/
│   └── Fire_sim.ino            ← Arduino 火焰模擬器
├── fire_submit/
│   ├── fire_escape_submit.py
│   ├── prompt_nanobanana.txt   ← AI prompt
│   └── 課室平面圖/
│       └── MyClassRoom.png     ← 課室平面圖（server.py 讀取）
├── fire_web/                   ← ⭐ 主系統目錄
│   ├── server.py               ← FastAPI 後端
│   ├── requirements.txt
│   ├── run.bat                 ← 一鍵啟動
│   ├── project_judging_brief.md
│   ├── AI_USAGE.md
│   ├── SETUP.md                ← 本文件
│   ├── outputs/                ← 自動產生（執行時）
│   └── static/                 ← PWA 前端
│       ├── index.html
│       ├── style.css
│       ├── app.js
│       ├── sw.js
│       ├── manifest.json
│       └── icons/
│           └── icon-192.png
└── pin_reader/
    └── pin_reader.ino          ← Arduino 火焰感測器讀取
```

> **⚠️ 重要**：`fire_web/server.py` 使用**相對路徑**讀取上層目錄的檔案：
> - `../.env`
> - `../fire_submit/課室平面圖/MyClassRoom.png`
> - `../fire_submit/prompt_nanobanana.txt`
>
> 因此整個專案目錄結構**必須保持不變**。

---

## 2. 系統需求

| 項目 | 最低版本 | 說明 |
|------|----------|------|
| **Python** | 3.9+ | 後端執行環境 |
| **pip** | 隨 Python | 套件管理 |
| **Arduino IDE** | 1.8+ 或 2.x | 上傳 `.ino` 到 Arduino Uno |
| **Arduino Uno** | — | 實體硬體（或純軟體測試可跳過） |
| **作業系統** | Windows / macOS / Linux | Windows 需注意 COM 埠名稱 |

---

## 3. Python 環境搭建

### 3-1. 建立虛擬環境（建議）

```bash
cd Comp_Fire_Evacuation/fire_web
python -m venv venv
```

啟動虛擬環境：
- **Windows**：`venv\Scripts\activate`
- **macOS / Linux**：`source venv/bin/activate`

### 3-2. 安裝依賴

```bash
pip install -r requirements.txt
```

### 3-3. 依賴清單說明

| 套件 | 版本 | 用途 |
|------|------|------|
| `fastapi` | ≥0.100.0 | Web 框架（REST API + WebSocket） |
| `uvicorn[standard]` | ≥0.23.0 | ASGI 伺服器，執行 FastAPI |
| `websockets` | ≥12.0 | WebSocket 支援（即時推送感測器數據） |
| `pyserial` | ≥3.5 | USB 序列埠通訊（讀取 Arduino 數據） |
| `numpy` | ≥1.24.0 | 數值矩陣運算（4×4 / 20×20 熱力圖） |
| `scipy` | ≥1.10.0 | `zoom()` 雙線性插值放大 |
| `Pillow` | ≥9.0.0 | 圖片讀取（課室平面圖） |
| `matplotlib` | ≥3.7.0 | 熱力圖 / 逃生圖 / 避難圖繪製 |
| `python-dotenv` | ≥1.0.0 | 讀取 `.env` 環境變數 |
| `openai` | ≥1.0.0 | 透過 APIYi 呼叫 Gemini AI 生圖 |

---

## 4. 環境變數設定（`.env`）

在專案**根目錄**（`Comp_Fire_Evacuation/.env`）建立：

```
APIYI_API_KEY=你的APIYi金鑰
```

> 如果沒有 APIYi 金鑰，AI 生圖功能會失敗，但**不影響其他功能**（感測器讀取、WebSocket 推送、A* 路線規劃、Matplotlib 生圖仍可正常運作）。

---

## 5. Arduino 設定

### 5-1. 上傳程式

1. 打開 Arduino IDE
2. 選擇 `工具 → 開發板 → Arduino Uno`
3. 選擇 `工具 → 連接埠 → COM3`（Windows）或 `/dev/ttyUSB0`（Linux）
4. 開啟 `Fire_sim/Fire_sim.ino`
5. 點擊「上傳」

### 5-2. 硬體接線（Fire_sim.ino）

| 旋鈕（類比輸入） | LED（PWM 輸出） |
|-----------------|----------------|
| A1 | Pin 3 |
| A2 | Pin 6 |
| A3 | Pin 7 |
| A4 | Pin 9 |
| A5 | Pin 11 |

### 5-3. 替代方案：pin_reader.ino

如果不使用旋鈕模擬，也可以用火焰感測器：
- 上傳 `pin_reader/pin_reader.ino`
- 5 個火焰感測器接 A0-A4
- `server.py` 同時支援兩種 Serial 輸出格式

### 5-4. 純軟體測試（無硬體）

如果沒有 Arduino，仍可測試 Web 介面：
1. 啟動 `python server.py`
2. Serial 會顯示連線失敗（不影響 Web 啟動）
3. 打開瀏覽器 `http://localhost:8080`
4. 前端會顯示「離線」但頁面仍可瀏覽

---

## 6. 啟動系統

### 方法一：run.bat（Windows 一鍵啟動）

```
雙擊 fire_web/run.bat
```

### 方法二：手動指令

```bash
cd fire_web
pip install -r requirements.txt   # 首次才需要
python server.py
```

### 方法三：uvicorn 直接啟動

```bash
cd fire_web
uvicorn server:app --host 0.0.0.0 --port 8080
```

---

## 7. 連線方式

### 本機測試
- 瀏覽器開啟：`http://localhost:8080`

### 手機 / 平板連線
- 確保手機和電腦在同一 Wi-Fi 網路
- 查詢電腦 IP：
  - **Windows**：`ipconfig` → 找 IPv4 位址
  - **macOS / Linux**：`ifconfig` 或 `ip addr`
- 手機瀏覽器開啟：`http://<電腦IP>:8080`
- 可「加入主畫面」作為 PWA 使用

---

## 8. API 端點一覽

| 方法 | 路徑 | 說明 |
|------|------|------|
| `GET` | `/` | PWA 前端頁面 |
| `WS` | `/ws` | WebSocket 即時數據推送（200ms） |
| `GET` | `/api/status` | 序列埠連線狀態 + 感測器數值 |
| `GET` | `/api/matrix` | 4×4 和 20×20 熱力圖矩陣 |
| `POST` | `/api/generate-escape` | 觸發 A* 逃生路線 + Matplotlib 生圖 |
| `POST` | `/api/generate-ai-escape` | 觸發 A* + Nano Banana AI 生圖 |
| `GET` | `/api/heatmap-image` | 取得即時熱力圖 PNG |
| `GET` | `/api/escape-image` | 取得即時逃生路線圖 PNG |
| `GET` | `/static/*` | PWA 靜態資源 |
| `GET` | `/outputs/*` | 生成的圖片輸出 |

---

## 9. 常見問題

### Q: `No module named 'serial'`
```bash
pip install pyserial
```

### Q: `could not open port 'COM3'`
- Windows：檢查裝置管理員 → 連接埠，確認 Arduino 的 COM 編號
- 修改 `fire_web/server.py` 第 47 行 `SERIAL_PORT = "COM3"` 為正確埠號
- macOS / Linux：改為 `/dev/ttyUSB0` 或 `/dev/ttyACM0`

### Q: `APIYI_API_KEY` 未設定
- 不影響基本功能，只影響 AI 生圖（`POST /api/generate-ai-escape`）
- 如需 AI 功能，請在 `.env` 中設定金鑰

### Q: matplotlib 找不到中文字型
- 系統自動搜尋 `msjh.ttc` / `msyh.ttc` / `simhei.ttf`
- 如果都沒有，圖表中文會顯示方塊，但不影響功能

### Q: 手機無法連線
- 確認防火牆允許 8080 埠
- Windows：`控制台 → Windows Defender 防火牆 → 允許應用程式通過防火牆`
- 確認手機和電腦在同一 Wi-Fi

---

## 10. 快速檢查清單

搬遷到新電腦後，依序檢查：

- [ ] Python 3.9+ 已安裝
- [ ] 專案目錄結構完整（見第 1 節）
- [ ] `pip install -r fire_web/requirements.txt` 成功
- [ ] `.env` 檔案已建立（可選）
- [ ] Arduino IDE 已安裝 + `.ino` 已上傳（可選）
- [ ] `cd fire_web && python server.py` 正常啟動
- [ ] `http://localhost:8080` 可開啟
- [ ] 手機同網段可連線

---

*最後更新: 2026-05-16*