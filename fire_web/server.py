"""
慧焰導行 – 室內火災智慧逃生系統 Web Server
- 從 Arduino COM3 讀取 5 個感測器數值
- WebSocket 即時推送火焰數據 + 20×20 熱力圖
- API 手動觸發 A* 逃生路線 + Nano Banana AI 生圖
- PWA 靜態檔案服務
"""

import os
import sys
import json
import re
import time
import threading
import asyncio
import heapq
from collections import deque
from pathlib import Path

import serial
import serial.tools.list_ports
import numpy as np
from scipy.ndimage import zoom
from PIL import Image
from dotenv import load_dotenv
from openai import OpenAI

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware
import uvicorn

# ─── 載入環境變數 ───────────────────────────────────────
load_dotenv(dotenv_path=os.path.join(os.path.dirname(__file__), '..', '.env'))

# ─── 路徑設定 ───────────────────────────────────────────
BASE_DIR = Path(__file__).parent
STATIC_DIR = BASE_DIR / "static"
OUTPUT_DIR = BASE_DIR / "outputs"
FLOORPLAN_PATH = BASE_DIR.parent / "fire_submit" / "課室平面圖" / "MyClassRoom.png"
PROMPT_PATH = BASE_DIR.parent / "fire_submit" / "prompt_nanobanana.txt"

OUTPUT_DIR.mkdir(exist_ok=True)

# ─── 序列埠設定 ─────────────────────────────────────────
SERIAL_PORT = "COM3"
BAUD_RATE = 115200
NUM_SENSORS = 5

# ─── A* / 熱力圖設定 ────────────────────────────────────
GRID_4X4 = 4
GRID_20X20 = 20
START_4X4 = (3, 0)    # 左下角 (row=3, col=0)
EXIT_4X4 = (0, 3)     # 右上角 (row=0, col=3) — 出口在右上
START_20X20 = (19, 0)
EXIT_20X20 = (0, 19)
DANGER_THRESHOLD = 6.0

# ─── 障礙物設定 (20×20 網格) ─────────────────────────────
# 課室平面圖中綠色桌子區域不可通行
# 以下定義 20×20 網格障礙物 (True = 可通行, False = 障礙物/桌子)
# 典型教室：中間有 3-4 排桌子，留出前後走道和兩側通道
def _build_obstacle_mask():
    """根據課室桌子分布建立 20×20 通行遮罩"""
    mask = np.ones((20, 20), dtype=bool)  # 預設全部可通行

    # 桌子區域：row 4~15，col 2~17 之間畫 4 排桌子，每排之間留走道
    # 第 1 排桌子: row 5~7, col 2~8
    mask[5:8, 2:9] = False
    # 第 2 排桌子: row 5~7, col 11~17
    mask[5:8, 11:18] = False
    # 第 3 排桌子: row 9~11, col 2~8
    mask[9:12, 2:9] = False
    # 第 4 排桌子: row 9~11, col 11~17
    mask[9:12, 11:18] = False
    # 第 5 排桌子: row 13~15, col 2~8
    mask[13:16, 2:9] = False
    # 第 6 排桌子: row 13~15, col 11~17
    mask[13:16, 11:18] = False

    # 確保起點和出口不受阻擋
    mask[19, 0] = True   # 起點 (左下)
    mask[0, 19] = True   # 出口 (右上)

    return mask

OBSTACLE_MASK_20X20 = _build_obstacle_mask()

# ============================================================
# 序列埠管理（從 fire_display.py 移植）
# ============================================================
class SerialManager:
    def __init__(self, port=SERIAL_PORT, baudrate=BAUD_RATE):
        self.port = port
        self.baudrate = baudrate
        self.ser = None
        self.running = False
        self.lock = threading.Lock()
        self.brightness = [0] * NUM_SENSORS
        self.knob_value = [0] * NUM_SENSORS
        self.history = [deque(maxlen=100) for _ in range(NUM_SENSORS)]
        self.connected = False

    def connect(self):
        try:
            self.ser = serial.Serial(self.port, self.baudrate, timeout=1)
            self.running = True
            self.connected = True
            print(f"[Serial] 已連接到 {self.port} (baud: {self.baudrate})")
            return True
        except serial.SerialException as e:
            print(f"[Serial] 無法連接到 {self.port}: {e}")
            self.connected = False
            return False

    def disconnect(self):
        self.running = False
        self.connected = False
        if self.ser and self.ser.is_open:
            self.ser.close()
            print(f"[Serial] 已斷開 {self.port}")

    def parse_line(self, line):
        line = line.strip()
        if not line:
            return

        with self.lock:
            # 格式 1: 「燈%d: 旋鈕=%d, 亮度=%d」(Fire_sim.ino)
            match = re.match(r'燈(\d+):\s*旋鈕=(\d+),\s*亮度=(\d+)', line)
            if match:
                idx = int(match.group(1)) - 1
                raw = int(match.group(2))
                bri = int(match.group(3))
                if 0 <= idx < NUM_SENSORS:
                    self.knob_value[idx] = raw
                    self.brightness[idx] = bri
                    self.history[idx].append(bri)
                return

            # 格式 2: pin_reader 「Sensor N (A0): YYYY」
            match = re.match(r'Sensor\s+(\d+)\s+\((?:GPIO\s+\d+|A\d+)\):\s*(\d+)', line)
            if match:
                idx = int(match.group(1)) - 1
                raw = int(match.group(2))
                max_val = 1023 if raw <= 1023 else 4095
                bri = max(0, min(255, int((1 - raw / max_val) * 255)))
                if 0 <= idx < NUM_SENSORS:
                    self.knob_value[idx] = raw
                    self.brightness[idx] = bri
                    self.history[idx].append(bri)
                return

            # 格式 3: 逗號分隔 5 個值
            parts = line.split(',')
            if len(parts) == NUM_SENSORS:
                try:
                    vals = [int(p.strip()) for p in parts]
                    for i, v in enumerate(vals):
                        self.brightness[i] = max(0, min(255, v))
                        self.knob_value[i] = v
                        self.history[i].append(v)
                except ValueError:
                    pass

    def read_thread(self):
        while self.running:
            try:
                if self.ser and self.ser.is_open:
                    line = self.ser.readline().decode('utf-8', errors='ignore')
                    if line:
                        self.parse_line(line)
                else:
                    time.sleep(0.5)
            except serial.SerialException:
                print("[Serial] 讀取錯誤，嘗試重連...")
                time.sleep(1)
            except Exception as e:
                print(f"[Serial] 錯誤: {e}")
                time.sleep(0.1)

    def get_brightness(self):
        with self.lock:
            return list(self.brightness)

    def get_knob(self):
        with self.lock:
            return list(self.knob_value)

    def start(self):
        if not self.connect():
            return False
        self.thread = threading.Thread(target=self.read_thread, daemon=True)
        self.thread.start()
        return True

    def stop(self):
        self.disconnect()


# ============================================================
# 應用層：感測器數據 → 熱力圖矩陣
# ============================================================
class FireDataEngine:
    """將 5 個感測器值轉換為 4×4 危險矩陣，再做插值 + A*"""

    def __init__(self):
        self.matrix_4x4 = np.zeros((4, 4), dtype=float)
        self.matrix_20x20 = np.zeros((20, 20), dtype=float)
        self.path_20x20 = []
        self.last_update = time.time()

    def update_from_brightness(self, brightness_5):
        """
        5 個值 (0-255) 映射到 4×4 矩陣。
        感測器位置（教室內 5 個點）：
          sensor[0] → 左上角 (row=0, col=0)
          sensor[1] → 左下角 (row=3, col=0)
          sensor[2] → 中間   (row=2, col=2)
          sensor[3] → 右上角 (row=0, col=3)
          sensor[4] → 右下角 (row=3, col=3)
        其餘格用鄰近加權平均插值補齊。
        """
        # 將 0-255 亮度映射到 0-100 危險指數
        vals = [min(100, v / 255.0 * 100) for v in brightness_5]

        mat = np.zeros((4, 4), dtype=float)

        # 直接賦值 5 個感測器位置
        mat[0, 0] = vals[0]  # 左上  — sensor 1
        mat[3, 0] = vals[1]  # 左下  — sensor 2
        mat[2, 2] = vals[2]  # 中間  — sensor 3
        mat[0, 3] = vals[3]  # 右上  — sensor 4
        mat[3, 3] = vals[4]  # 右下  — sensor 5

        # 用鄰近加權平均補其餘格子
        # row 0
        mat[0, 1] = (vals[0] * 0.4 + vals[2] * 0.3 + vals[3] * 0.3)
        mat[0, 2] = (vals[3] * 0.5 + vals[2] * 0.3 + vals[0] * 0.2)
        # row 1
        mat[1, 0] = (vals[0] * 0.6 + vals[1] * 0.4)
        mat[1, 1] = (vals[0] * 0.15 + vals[1] * 0.15 + vals[2] * 0.5 + vals[3] * 0.1 + vals[4] * 0.1)
        mat[1, 2] = (vals[2] * 0.5 + vals[3] * 0.3 + vals[4] * 0.2)
        mat[1, 3] = (vals[3] * 0.6 + vals[4] * 0.2 + vals[2] * 0.2)
        # row 2
        mat[2, 0] = (vals[1] * 0.6 + vals[0] * 0.2 + vals[2] * 0.2)
        mat[2, 1] = (vals[1] * 0.3 + vals[2] * 0.5 + vals[4] * 0.2)
        mat[2, 3] = (vals[4] * 0.55 + vals[3] * 0.25 + vals[2] * 0.2)
        # row 3
        mat[3, 1] = (vals[1] * 0.5 + vals[2] * 0.3 + vals[4] * 0.2)
        mat[3, 2] = (vals[4] * 0.4 + vals[2] * 0.4 + vals[1] * 0.2)

        self.matrix_4x4 = np.clip(mat, 0, 100)

        # 雙線性插值 → 20×20
        scale = GRID_20X20 / GRID_4X4
        self.matrix_20x20 = zoom(self.matrix_4x4 / 10.0, scale, order=1)  # 縮放到 0~10

        self.last_update = time.time()
        return self.matrix_4x4, self.matrix_20x20

    def run_astar(self):
        """執行 A* 路線規劃，使用 20×20 矩陣"""
        grid = self.matrix_20x20
        start = START_20X20
        goal = EXIT_20X20

        def heuristic(a, b):
            return abs(a[0] - b[0]) + abs(a[1] - b[1])

        rows, cols = grid.shape
        open_set = []
        heapq.heappush(open_set, (0, start))
        came_from = {}
        g_score = {start: 0}

        while open_set:
            _, current = heapq.heappop(open_set)
            if current == goal:
                path = []
                while current in came_from:
                    path.append(current)
                    current = came_from[current]
                path.append(start)
                self.path_20x20 = path[::-1]
                return self.path_20x20

            r, c = current
            for dr, dc in [(-1, 0), (1, 0), (0, -1), (0, 1)]:
                nr, nc = r + dr, c + dc
                if not (0 <= nr < rows and 0 <= nc < cols):
                    continue
                # 跳過障礙物（桌子），但起點和出口除外
                if not OBSTACLE_MASK_20X20[nr, nc] and (nr, nc) != goal:
                    continue
                danger = grid[nr, nc]
                cost = 1 + (danger ** 2) * 10 if danger > DANGER_THRESHOLD else 1 + danger
                new_g = g_score[current] + cost
                if new_g < g_score.get((nr, nc), float('inf')):
                    came_from[(nr, nc)] = current
                    g_score[(nr, nc)] = new_g
                    heapq.heappush(open_set, (new_g + heuristic((nr, nc), goal), (nr, nc)))

        self.path_20x20 = []
        return []

    def find_safest_reachable(self):
        """
        當無法逃生時，從起點出發，找出所有可到達且非障礙物的格子中
        危險指數最低的作為避難點。
        使用 BFS 遍歷可通行區域（避開障礙物）。
        """
        start = START_20X20
        rows, cols = self.matrix_20x20.shape

        # BFS 找所有可到達的格子
        visited = set()
        queue = deque([start])
        visited.add(start)

        while queue:
            r, c = queue.popleft()
            for dr, dc in [(-1, 0), (1, 0), (0, -1), (0, 1)]:
                nr, nc = r + dr, c + dc
                if not (0 <= nr < rows and 0 <= nc < cols):
                    continue
                if (nr, nc) in visited:
                    continue
                # 只經過非障礙物
                if not OBSTACLE_MASK_20X20[nr, nc]:
                    continue
                visited.add((nr, nc))
                queue.append((nr, nc))

        # 從所有可到達格中找危險指數最低的
        best_val = 999.0
        best_cell = start  # 預設起點
        for (r, c) in visited:
            val = self.matrix_20x20[r, c]
            if val < best_val:
                best_val = val
                best_cell = (r, c)

        return best_cell, best_val, len(visited)

    def generate_shelter_plot(self):
        """生成避難圖：標示最安全可到達區域"""
        import matplotlib
        matplotlib.use('Agg')
        import matplotlib.pyplot as plt
        import matplotlib.patches as mpatches
        import matplotlib.font_manager as fm

        candidates = [
            "C:/Windows/Fonts/msjh.ttc",
            "C:/Windows/Fonts/msyh.ttc",
            "C:/Windows/Fonts/simhei.ttf",
        ]
        for p in candidates:
            if os.path.exists(p):
                fm.fontManager.addfont(p)
                plt.rcParams['font.family'] = fm.FontProperties(fname=p).get_name()
                plt.rcParams['axes.unicode_minus'] = False
                break

        safest_cell, safest_val, reachable_count = self.find_safest_reachable()

        fig, ax = plt.subplots(figsize=(8, 8))

        if os.path.exists(FLOORPLAN_PATH):
            img = Image.open(FLOORPLAN_PATH).convert("RGB")
            ax.imshow(img, extent=[0, 4, 4, 0], aspect='auto', zorder=0)

        # 畫 4×4 熱力色塊（紅色調為主，強調危險）
        def danger_to_color(val):
            if val >= 80: return (0.9, 0.0, 0.0, 0.50)
            elif val >= 60: return (1.0, 0.3, 0.0, 0.45)
            elif val >= 40: return (1.0, 0.7, 0.0, 0.40)
            else: return (0.0, 0.7, 0.2, 0.35)

        for row in range(4):
            for col in range(4):
                val = self.matrix_4x4[row, col]
                rect = mpatches.Rectangle((col, row), 1, 1,
                    linewidth=0, facecolor=danger_to_color(val), zorder=1)
                ax.add_patch(rect)

        # 格線
        for i in range(5):
            ax.axhline(i, color='#aaaaaa', linewidth=0.8, alpha=0.6, zorder=2)
            ax.axvline(i, color='#aaaaaa', linewidth=0.8, alpha=0.6, zorder=2)

        # 繪製最安全避難區域（轉換 20×20 → 4×4 座標）
        sr, sc = safest_cell
        sx = sc / 5.0 + 0.1
        sy = sr / 5.0 + 0.1
        ax.plot(sx, sy, 'o', color='#00ff88', markersize=22,
                markeredgecolor='white', markeredgewidth=3, zorder=7)
        # 大圈標示
        circle = plt.Circle((sx, sy), 0.35, color='#00ff88', fill=False,
                           linewidth=3, linestyle='--', zorder=6)
        ax.add_patch(circle)

        ax.text(sx + 0.4, sy - 0.1, '就地避難', fontsize=12, color='#00ff88',
                fontweight='bold', zorder=8,
                bbox=dict(boxstyle='round,pad=0.3', facecolor='#003322', edgecolor='#00ff88', alpha=0.9))

        # 起點標記
        ax.plot(0.1, 3.9, 'o', color='#1155ff', markersize=14,
                markeredgecolor='white', markeredgewidth=2, zorder=6)
        ax.text(0.35, 3.85, '你在此', fontsize=9, color='white', fontweight='bold',
                zorder=7, bbox=dict(boxstyle='round,pad=0.2', facecolor='#1155ff', alpha=0.8))

        # 出口標記（打叉表示無法到達）
        ax.plot(3.9, 0.1, 'X', color='#ff4444', markersize=18,
                markeredgecolor='white', markeredgewidth=2, zorder=6)
        ax.text(2.7, 0.3, '出口被封鎖', fontsize=9, color='white', fontweight='bold',
                zorder=7, bbox=dict(boxstyle='round,pad=0.2', facecolor='#cc0000', alpha=0.8))

        # 警告標題
        ax.set_title('🚨 無法逃生！請就地尋找避難處', fontsize=14, color='red',
                    fontweight='bold', pad=15)

        # 狀態文字
        status_text = (
            f"可到達範圍: {reachable_count} 格\n"
            f"最安全區危險指數: {safest_val:.1f} / 10"
        )
        ax.text(2.0, 3.85, status_text, fontsize=9, color='#ffaaaa',
                ha='center', zorder=8,
                bbox=dict(boxstyle='round,pad=0.3', facecolor='#330000', alpha=0.8))

        ax.set_xlim(0, 4)
        ax.set_ylim(0, 4)
        ax.invert_yaxis()
        ax.axis('off')
        plt.tight_layout(pad=0)

        output_path = OUTPUT_DIR / "shelter_plot.png"
        plt.savefig(str(output_path), dpi=150, bbox_inches='tight')
        plt.close()
        return output_path

    def generate_escape_plot(self):
        """生成逃生路線圖（Python matplotlib 版，無 AI）"""
        import matplotlib
        matplotlib.use('Agg')
        import matplotlib.pyplot as plt
        import matplotlib.patches as mpatches
        import matplotlib.font_manager as fm

        # 設定中文字型
        candidates = [
            "C:/Windows/Fonts/msjh.ttc",
            "C:/Windows/Fonts/msyh.ttc",
            "C:/Windows/Fonts/simhei.ttf",
        ]
        for p in candidates:
            if os.path.exists(p):
                fm.fontManager.addfont(p)
                plt.rcParams['font.family'] = fm.FontProperties(fname=p).get_name()
                plt.rcParams['axes.unicode_minus'] = False
                break

        fig, ax = plt.subplots(figsize=(8, 8))

        # 載入課室平面圖
        if os.path.exists(FLOORPLAN_PATH):
            img = Image.open(FLOORPLAN_PATH).convert("RGB")
            ax.imshow(img, extent=[0, 4, 4, 0], aspect='auto', zorder=0)

        # 畫 4×4 熱力色塊
        def danger_to_color(val):
            if val >= 80:
                return (0.8, 0.0, 0.0, 0.45)
            elif val >= 60:
                return (1.0, 0.45, 0.0, 0.40)
            elif val >= 40:
                return (1.0, 0.85, 0.0, 0.35)
            else:
                return (0.0, 0.75, 0.2, 0.30)

        for row in range(4):
            for col in range(4):
                val = self.matrix_4x4[row, col]
                color = danger_to_color(val)
                rect = mpatches.Rectangle(
                    (col, row), 1, 1,
                    linewidth=0, facecolor=color, zorder=1
                )
                ax.add_patch(rect)

        # 格線
        for i in range(5):
            ax.axhline(i, color='#aaaaaa', linewidth=0.8, alpha=0.6, zorder=2)
            ax.axvline(i, color='#aaaaaa', linewidth=0.8, alpha=0.6, zorder=2)

        # 逃生路線
        if self.path_20x20:
            xs = [c / 5.0 for r, c in self.path_20x20]
            ys = [r / 5.0 for r, c in self.path_20x20]
            ax.plot(xs, ys, color='#00ee55', linewidth=4, zorder=4,
                    solid_capstyle='round', solid_joinstyle='round')
            step = max(1, len(self.path_20x20) // 5)
            for i in range(0, len(self.path_20x20) - 1, step):
                r1, c1 = self.path_20x20[i]
                r2, c2 = self.path_20x20[i + 1]
                ax.annotate('',
                    xy=(c2 / 5.0, r2 / 5.0),
                    xytext=(c1 / 5.0, r1 / 5.0),
                    arrowprops=dict(arrowstyle='->', color='#00ee55', lw=2.5),
                    zorder=5)

        # 起點（左下角）
        ax.plot(0.1, 3.9, 'o', color='#1155ff', markersize=16,
                markeredgecolor='white', markeredgewidth=2, zorder=6)
        ax.text(0.35, 3.85, '起點', fontsize=10, color='white', fontweight='bold',
                zorder=7, bbox=dict(boxstyle='round,pad=0.2', facecolor='#1155ff', alpha=0.8))

        # 出口（右上角）
        ax.plot(3.9, 0.1, '*', color='#00cc44', markersize=20,
                markeredgecolor='white', markeredgewidth=1.5, zorder=6)
        ax.text(2.9, 0.3, '安全出口', fontsize=10, color='white', fontweight='bold',
                zorder=7, bbox=dict(boxstyle='round,pad=0.2', facecolor='#00aa33', alpha=0.8))

        ax.set_xlim(0, 4)
        ax.set_ylim(0, 4)
        ax.invert_yaxis()
        ax.axis('off')
        plt.tight_layout(pad=0)

        output_path = OUTPUT_DIR / "escape_python.png"
        plt.savefig(str(output_path), dpi=150, bbox_inches='tight')
        plt.close()
        return output_path

    def generate_heatmap_image(self):
        """生成純 20×20 熱力圖 PNG"""
        import matplotlib
        matplotlib.use('Agg')
        import matplotlib.pyplot as plt

        fig, ax = plt.subplots(figsize=(6, 6))
        ax.imshow(self.matrix_20x20, cmap=plt.cm.RdYlGn_r, vmin=0, vmax=10,
                  interpolation='nearest')
        ax.axis('off')
        plt.tight_layout(pad=0)

        output_path = OUTPUT_DIR / "heatmap_20x20.png"
        plt.savefig(str(output_path), dpi=150, bbox_inches='tight')
        plt.close()
        return output_path

    def generate_with_nanobanana(self):
        """使用 APIYi (Nano Banana 2 / Gemini) 生成 AI 逃生圖"""
        import base64
        import requests

        api_key = os.getenv("APIYI_API_KEY")
        if not api_key:
            print("[NanoBanana] 錯誤：未設定 APIYI_API_KEY")
            return None

        # 先生成熱力圖
        heatmap_path = self.generate_heatmap_image()

        # 讀取 prompt
        if not PROMPT_PATH.exists():
            print(f"[NanoBanana] prompt 檔案不存在: {PROMPT_PATH}")
            return None
        with open(PROMPT_PATH, "r", encoding="utf-8") as f:
            prompt_text = f.read()

        # 把平面圖和熱力圖轉 base64
        def to_b64(path):
            if not path or not Path(path).exists():
                return ""
            with open(path, "rb") as f:
                return base64.b64encode(f.read()).decode("utf-8")

        fp_b64 = to_b64(str(FLOORPLAN_PATH))
        hm_b64 = to_b64(str(heatmap_path))

        client = OpenAI(
            api_key=api_key,
            base_url="https://api.apiyi.com/v1",
        )

        print("[NanoBanana] 提交任務 (APIYi / Gemini)...")

        # 構建 messages
        content_parts = [{"type": "text", "text": prompt_text}]
        if fp_b64:
            content_parts.append({"type": "image_url", "image_url": {"url": f"data:image/png;base64,{fp_b64}"}})
        if hm_b64:
            content_parts.append({"type": "image_url", "image_url": {"url": f"data:image/png;base64,{hm_b64}"}})

        try:
            response = client.chat.completions.create(
                model="gemini-3.1-flash-image-preview",
                messages=[
                    {"role": "user", "content": content_parts},
                ],
                extra_body={"image_config": {"aspect_ratio": "1:1", "image_size": "1K"}},
            )
        except Exception as e:
            print(f"[NanoBanana] API 呼叫失敗: {e}")
            return None

        # 解析回應
        output_path = OUTPUT_DIR / "nanobanana_result.png"
        for choice in response.choices:
            msg = choice.message
            content = msg.content if hasattr(msg, 'content') else ""

            if isinstance(content, str) and "data:image" in content:
                match = re.search(r'data:image/[^;]+;base64,([A-Za-z0-9+/=]+)', content)
                if match:
                    img_data = base64.b64decode(match.group(1))
                    with open(output_path, "wb") as f:
                        f.write(img_data)
                    print(f"[NanoBanana] ✅ 已儲存: {output_path} ({len(img_data) / 1024:.1f} KB)")
                    return output_path

            elif isinstance(content, list):
                for part in content:
                    if isinstance(part, dict) and part.get("type") == "image_url":
                        url = part["image_url"]["url"]
                        if url.startswith("data:image"):
                            b64_data = url.split(",", 1)[1]
                            img_data = base64.b64decode(b64_data)
                        else:
                            img_data = requests.get(url).content
                        with open(output_path, "wb") as f:
                            f.write(img_data)
                        print(f"[NanoBanana] ✅ 已儲存: {output_path}")
                        return output_path

        print(f"[NanoBanana] ❌ 未收到圖片 (content preview: {str(content)[:200]})")
        return None


# ============================================================
# 全域實例
# ============================================================
serial_mgr = SerialManager()
engine = FireDataEngine()
connected_clients: list[WebSocket] = []

# ============================================================
# FastAPI 應用
# ============================================================
app = FastAPI(title="慧焰導行 Web Server", version="1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ─── WebSocket 即時推送 ──────────────────────────────────
@app.websocket("/ws")
async def websocket_endpoint(ws: WebSocket):
    await ws.accept()
    connected_clients.append(ws)
    print(f"[WS] 客戶端連線 (共 {len(connected_clients)} 個)")
    try:
        # 立即發送初始狀態
        while True:
            # 等待客戶端訊息（也可用作心跳）
            data = await ws.receive_text()
            if data == "ping":
                await ws.send_text(json.dumps({"type": "pong"}))
    except WebSocketDisconnect:
        pass
    except Exception as e:
        print(f"[WS] 錯誤: {e}")
    finally:
        connected_clients.remove(ws)
        print(f"[WS] 客戶端斷線 (剩 {len(connected_clients)} 個)")


async def broadcast_ws(message: dict):
    """向所有 WebSocket 客戶端廣播"""
    disconnected = []
    for ws in connected_clients:
        try:
            await ws.send_text(json.dumps(message))
        except Exception:
            disconnected.append(ws)
    for ws in disconnected:
        if ws in connected_clients:
            connected_clients.remove(ws)


# ─── 背景任務：定時推送感測器數據 ────────────────────────
async def sensor_push_loop():
    """每 200ms 讀取序列埠數據並透過 WebSocket 推送"""
    while True:
        brightness = serial_mgr.get_brightness()
        knob = serial_mgr.get_knob()

        # 更新 4×4 和 20×20 矩陣
        engine.update_from_brightness(brightness)

        # 推送給所有客戶端
        if connected_clients:
            await broadcast_ws({
                "type": "sensor_update",
                "brightness": brightness,            # 5 個 0-255 值
                "knob": knob,                        # 5 個原始值
                "matrix_4x4": engine.matrix_4x4.tolist(),
                "matrix_20x20": engine.matrix_20x20.tolist(),
                "serial_connected": serial_mgr.connected,
                "timestamp": time.time(),
            })

        await asyncio.sleep(0.2)  # 200ms


@app.on_event("startup")
async def startup_event():
    # 啟動序列埠
    serial_mgr.start()
    # 啟動背景推送
    asyncio.create_task(sensor_push_loop())
    print("[Server] 慧焰導行 Web Server 已啟動")


@app.on_event("shutdown")
async def shutdown_event():
    serial_mgr.stop()


# ─── REST API ────────────────────────────────────────────

@app.get("/api/status")
async def get_status():
    """取得目前連線狀態"""
    return {
        "serial_connected": serial_mgr.connected,
        "port": SERIAL_PORT,
        "brightness": serial_mgr.get_brightness(),
        "knob": serial_mgr.get_knob(),
        "clients_connected": len(connected_clients),
    }


@app.get("/api/matrix")
async def get_matrix():
    """取得目前的 4×4 和 20×20 矩陣"""
    return {
        "matrix_4x4": engine.matrix_4x4.tolist(),
        "matrix_20x20": engine.matrix_20x20.tolist(),
    }


@app.post("/api/generate-escape")
async def generate_escape():
    """手動觸發 A* 逃生路線計算 + 生成圖像；若無法逃生則生成避難圖"""
    path = engine.run_astar()

    if path:
        engine.generate_escape_plot()
        return {
            "status": "escaped",
            "success": True,
            "path_length": len(path),
            "path": [[int(r), int(c)] for r, c in path],
            "escape_image": f"/outputs/escape_python.png",
            "message": f"找到逃生路線，共 {len(path)} 步",
        }
    else:
        # 無法逃生：生成避難圖
        safest_cell, safest_val, reachable = engine.find_safest_reachable()
        engine.generate_shelter_plot()
        return {
            "status": "blocked",
            "success": False,
            "shelter_image": "/outputs/shelter_plot.png",
            "safest_cell": list(safest_cell),
            "safest_value": round(float(safest_val), 2),
            "reachable_cells": reachable,
            "message": "🚨 無法逃生！所有安全路徑已被火勢封鎖，請就地避難等待救援",
        }


@app.post("/api/generate-ai-escape")
async def generate_ai_escape():
    """呼叫 AI 生成最終逃生圖；若無法逃生則跳過 AI，直接生成避難圖"""
    path = engine.run_astar()

    if not path:
        # 無法逃生：不浪費 AI API，直接回避難資訊
        safest_cell, safest_val, reachable = engine.find_safest_reachable()
        engine.generate_shelter_plot()
        return {
            "status": "blocked",
            "success": False,
            "shelter_image": "/outputs/shelter_plot.png",
            "safest_cell": list(safest_cell),
            "safest_value": round(float(safest_val), 2),
            "reachable_cells": reachable,
            "message": "🚨 無法逃生！所有安全路徑已被火勢封鎖，請就地避難等待救援。已發送求救通知至應急中心。",
            "sos_sent": True,
        }

    result = engine.generate_with_nanobanana()

    if result:
        return {
            "status": "escaped",
            "success": True,
            "ai_image": "/outputs/nanobanana_result.png",
            "path_length": len(path),
        }
    else:
        raise HTTPException(status_code=500, detail="AI 生成失敗，請檢查 API Key 或網路")


@app.get("/api/heatmap-image")
async def get_heatmap_image():
    """取得即時熱力圖 PNG"""
    engine.update_from_brightness(serial_mgr.get_brightness())
    img_path = engine.generate_heatmap_image()
    return FileResponse(str(img_path), media_type="image/png")


@app.get("/api/escape-image")
async def get_escape_image():
    """取得即時逃生路線圖 PNG"""
    engine.update_from_brightness(serial_mgr.get_brightness())
    engine.run_astar()
    img_path = engine.generate_escape_plot()
    return FileResponse(str(img_path), media_type="image/png")


# ─── 靜態檔案服務 ────────────────────────────────────────
@app.get("/")
async def serve_index():
    return FileResponse(str(STATIC_DIR / "index.html"))


# 掛載 static 和 outputs 目錄
if STATIC_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

if OUTPUT_DIR.exists():
    app.mount("/outputs", StaticFiles(directory=str(OUTPUT_DIR)), name="outputs")


# ════════════════════════════════════════════════════════
# 程式入口
# ════════════════════════════════════════════════════════
if __name__ == "__main__":
    import sys
    sys.stdout.reconfigure(encoding='utf-8')
    print("=" * 50)
    print("  🔥 慧焰導行 – Web Server")
    print("=" * 50)
    print(f"  序列埠: {SERIAL_PORT}")
    print(f"  HTTP:    http://0.0.0.0:8080")
    print(f"  WebSocket: ws://0.0.0.0:8080/ws")
    print("=" * 50)

    uvicorn.run(
        "server:app",
        host="0.0.0.0",
        port=8080,
        reload=False,
        log_level="info",
    )