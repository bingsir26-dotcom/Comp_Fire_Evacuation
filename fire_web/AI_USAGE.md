# 🔥 慧焰導行 – AI 使用技術說明

> 本文檔記錄系統中 AI 的使用方式，方便日後重現。

---

## 1. AI 服務商：APIYi

| 項目 | 值 |
|------|-----|
| 服務名稱 | **APIYi** (apiyi.com) |
| Base URL | `https://api.apiyi.com/v1` |
| 相容性 | **OpenAI SDK 相容** (Chat Completions API) |
| SDK | `pip install openai` (使用標準 `openai` Python 套件，非 `google-genai`) |

---

## 2. 使用模型

| 模型 | 用途 |
|------|------|
| `gemini-3.1-flash-image-preview` | **圖像生成**：接收平面圖 + 熱力圖，疊加生成視覺化逃生指引圖 |

此模型是 **Gemini 3.1 Flash** 的多模態圖像預覽版，支援文字+多張圖片輸入、輸出一張合成圖片。

---

## 3. API 金鑰設定

在專案**根目錄**（`fire_web/../`）建立 `.env` 檔案：

```
APIYI_API_KEY=你的APIYi金鑰
```

程式碼載入方式 (`server.py` 第 34-35 行)：

```python
import os
from dotenv import load_dotenv
load_dotenv(dotenv_path=os.path.join(os.path.dirname(__file__), '..', '.env'))
api_key = os.getenv("APIYI_API_KEY")
```

---

## 4. API 呼叫方式（完整程式碼片段）

### 4-1. 初始化 Client

```python
from openai import OpenAI

client = OpenAI(
    api_key=api_key,              # 從 .env 讀取
    base_url="https://api.apiyi.com/v1",  # APIYi 端點
)
```

### 4-2. 構建多模態訊息（文字 + 兩張圖片）

```python
import base64
from pathlib import Path

# 將圖片轉為 base64
def to_b64(path):
    with open(path, "rb") as f:
        return base64.b64encode(f.read()).decode("utf-8")

floorplan_b64 = to_b64("課室平面圖/MyClassRoom.png")
heatmap_b64  = to_b64("outputs/heatmap_20x20.png")

# 讀取 prompt 文字
with open("prompt_nanobanana.txt", "r", encoding="utf-8") as f:
    prompt_text = f.read()

# 構建 messages：一個 user message 包含 text + 兩張 image
content_parts = [
    {"type": "text", "text": prompt_text},
    {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{floorplan_b64}"}},
    {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{heatmap_b64}"}},
]
```

### 4-3. 呼叫 API

```python
response = client.chat.completions.create(
    model="gemini-3.1-flash-image-preview",
    messages=[
        {"role": "user", "content": content_parts},
    ],
    extra_body={
        "image_config": {
            "aspect_ratio": "1:1",    # 正方形輸出
            "image_size": "1K",       # 1K 解析度
        }
    },
)
```

### 4-4. 解析回應（提取圖片）

回應可能是兩種格式之一，程式碼同時處理：

**格式 A：純文字內嵌 base64 圖片**
```python
for choice in response.choices:
    content = choice.message.content
    if isinstance(content, str) and "data:image" in content:
        match = re.search(r'data:image/[^;]+;base64,([A-Za-z0-9+/=]+)', content)
        if match:
            img_data = base64.b64decode(match.group(1))
            with open("output.png", "wb") as f:
                f.write(img_data)
```

**格式 B：結構化 content list**
```python
    elif isinstance(content, list):
        for part in content:
            if part.get("type") == "image_url":
                url = part["image_url"]["url"]
                # 可能是 data: URI 或 HTTP URL
                if url.startswith("data:image"):
                    b64_data = url.split(",", 1)[1]
                    img_data = base64.b64decode(b64_data)
                else:
                    img_data = requests.get(url).content
                with open("output.png", "wb") as f:
                    f.write(img_data)
```

---

## 5. Prompt 設計

Prompt 儲存在 `fire_submit/prompt_nanobanana.txt`，完整內容：

```
Use the given classroom floor plan as the base image.
Do not redraw or replace the classroom. Keep all walls, doors, windows,
desks and layout exactly the same, only add visual overlays on top.

Now, using the separate 20×20 fire heatmap image as the danger
distribution, project this heatmap onto the classroom floor plan:
- Map hotter cells (red/orange) → semi-transparent heat layer
- Mark "Start" at bottom-left, "Exit" at top-right
- Draw a bright path line with arrows avoiding red zones
- Add labels: "High-risk hot zone", "Safer zone", "Start", "Exit",
  "AI-Generated Escape Route"
- Final image must look like the original classroom with overlays,
  not a new drawing
```

**Prompt 設計原則**：
1. 明確指定「不要重新繪製，只在原圖上疊加」
2. 提供參考圖片作為視覺指引（兩張輸入圖）
3. 指定顏色語意（紅=危險、綠=安全）
4. 指定文字標籤內容和位置
5. 指定起點/終點位置
6. 約束風格（保留透視、比例、結構）

---

## 6. AI 的定位（不做決策，只做視覺化）

```
感測器 → 4×4 矩陣 → 插值 20×20 → A* 演算法 → 路線座標
                                                  │
                  ┌───────────────────────────────┘
                  ▼
        Nano Banana (Gemini)
        輸入：平面圖 + 熱力圖 + prompt
        輸出：一張直觀的逃生指引圖
```

- **A* 演算法** 負責路線計算（保證正確性）
- **AI** 只負責將結果「畫得讓人看得懂」

---

## 7. 觸發時機

AI 生成不持續呼叫（節省費用），只在以下時機觸發：

| 觸發方式 | API 端點 | 說明 |
|----------|----------|------|
| 前端按鈕 | `POST /api/generate-ai-escape` | 手動觸發 |
| 逃生成功時 | 呼叫 `generate_with_nanobanana()` | 生成視覺化圖 |
| 逃生失敗時 | 不呼叫 AI | 直接生成避難圖（省 API 費用） |

---

## 8. 完整依賴清單

```
# requirements.txt 中 AI 相關
openai>=1.0.0           # OpenAI SDK（透過 APIYi 呼叫 Gemini）
python-dotenv>=1.0.0    # 讀取 .env 中的 APIYI_API_KEY
Pillow>=9.0.0           # 圖片處理
numpy>=1.24.0           # 矩陣運算
matplotlib>=3.7.0       # 熱力圖生成（供 AI 參考）

# 非 Python 依賴
# - 需要註冊 APIYi 帳號並取得 API Key
# - 需要網路連線
```

---

## 9. 如何重現

### 步驟 1：取得 API Key
1. 前往 [APIYi](https://apiyi.com) 註冊帳號
2. 在後台取得 API Key
3. 建立 `.env` 檔案：`APIYI_API_KEY=sk-xxxxx`

### 步驟 2：安裝依賴
```bash
pip install openai python-dotenv Pillow numpy matplotlib
```

### 步驟 3：準備輸入素材
- 一張課室平面圖 PNG（`MyClassRoom.png`）
- 一張 20×20 熱力圖 PNG（由 `matplotlib` 的 `imshow` 生成）

### 步驟 4：呼叫 API（使用上面第 4 節的程式碼）

---

*最後更新: 2026-05-16*