# 慧焰導行 – 室內火災智慧逃生系統
# 作者：[你的名字]
# 比賽：[比賽名稱]
#
# 系統流程：
#   1. 讀取感測器的 4x4 危險矩陣
#   2. 雙線性插值放大成 20x20，生成熱力圖
#   3. A* 演算法計算最佳逃生路線
#   4. 直接在課室平面圖上疊加 4x4 熱力色塊 + 逃生路線
#   5. 合併四張圖輸出一張

import os
import heapq
import time
import numpy as np
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
import matplotlib.font_manager as fm
from matplotlib.colors import LinearSegmentedColormap
from scipy.ndimage import zoom
from PIL import Image
from dotenv import load_dotenv

load_dotenv(dotenv_path=os.path.join(os.path.dirname(__file__), '..', '.env'))

# ── 課室設定 ─────────────────────────────────────────
FLOORPLAN_PATH = "./課室平面圖/example1.png"
OUTPUT_DIR = "output"

# 4x4 危險指數，右上角最危險（對應感測器實際數據）
# 矩陣排列：matrix[row][col]，row=0 是頂部，row=3 是底部
DANGER_MATRIX = np.array([
    [10, 20, 80, 90],   # row0（頂部）右上角最危險
    [10, 30, 70, 80],   # row1
    [20, 20, 40, 50],   # row2
    [10, 10, 20, 20],   # row3（底部）
], dtype=float)

# 起點：左下角，出口：左上角
START_4x4   = (3, 0)
EXIT_4x4    = (0, 0)
START_20x20 = (19, 0)
EXIT_20x20  = (0, 0)

# 設定中文字型
def set_chinese_font():
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
            return

set_chinese_font()


# ════════════════════════════════════════════════════
# 1. 感測器數據
# ════════════════════════════════════════════════════

def get_sensor_matrix():
    # 把 0~100 的危險指數縮放到 0~10 供 A* 使用
    return DANGER_MATRIX / 10.0


# ════════════════════════════════════════════════════
# 2. 雙線性插值
# ════════════════════════════════════════════════════

def interpolate(matrix, target_size=20):
    scale = target_size / matrix.shape[0]
    return zoom(matrix, scale, order=1)


# ════════════════════════════════════════════════════
# 3. A* 路線規劃
# ════════════════════════════════════════════════════

DANGER_THRESHOLD = 6.0  # 對應原始指數 60

def heuristic(a, b):
    return abs(a[0] - b[0]) + abs(a[1] - b[1])

def astar(grid, start, goal):
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
            return path[::-1]

        r, c = current
        for dr, dc in [(-1, 0), (1, 0), (0, -1), (0, 1)]:
            nr, nc = r + dr, c + dc
            if not (0 <= nr < rows and 0 <= nc < cols):
                continue
            danger = grid[nr, nc]
            cost = 1 + (danger ** 2) * 10 if danger > DANGER_THRESHOLD else 1 + danger
            new_g = g_score[current] + cost
            if new_g < g_score.get((nr, nc), float('inf')):
                came_from[(nr, nc)] = current
                g_score[(nr, nc)] = new_g
                heapq.heappush(open_set, (new_g + heuristic((nr, nc), goal), (nr, nc)))

    return []


# ════════════════════════════════════════════════════
# 4. 在課室平面圖上直接疊加逃生圖
# ════════════════════════════════════════════════════

def danger_to_color(val):
    # 根據危險指數返回 (r,g,b,a)
    if val >= 80:
        return (0.8, 0.0, 0.0, 0.45)    # 深紅
    elif val >= 60:
        return (1.0, 0.45, 0.0, 0.40)   # 橘色
    elif val >= 40:
        return (1.0, 0.85, 0.0, 0.35)   # 黃色
    else:
        return (0.0, 0.75, 0.2, 0.30)   # 綠色


def draw_escape_on_floorplan(path_20x20, output_path):
    fig, ax = plt.subplots(figsize=(10, 10))

    # 載入課室平面圖
    img = Image.open(FLOORPLAN_PATH).convert("RGB")
    w, h = img.size
    ax.imshow(img, extent=[0, 4, 4, 0], aspect='auto', zorder=0)

    # 畫 4x4 熱力色塊（半透明）
    for row in range(4):
        for col in range(4):
            val = DANGER_MATRIX[row, col]
            color = danger_to_color(val)
            rect = mpatches.Rectangle(
                (col, row), 1, 1,
                linewidth=0, facecolor=color, zorder=1
            )
            ax.add_patch(rect)

    # 畫 4x4 格線（淡灰色細線）
    for i in range(5):
        ax.axhline(i, color='#aaaaaa', linewidth=0.8, alpha=0.6, zorder=2)
        ax.axvline(i, color='#aaaaaa', linewidth=0.8, alpha=0.6, zorder=2)

    # 把 20x20 路線轉換回 4x4 座標畫出來
    if path_20x20:
        # 縮放：20x20 → 4x4，每格 = 0.2 單位
        xs = [c / 5.0 for r, c in path_20x20]
        ys = [r / 5.0 for r, c in path_20x20]
        ax.plot(xs, ys, color='#00ee55', linewidth=4, zorder=4,
                solid_capstyle='round', solid_joinstyle='round')
        # 方向箭頭
        step = max(1, len(path_20x20) // 5)
        for i in range(0, len(path_20x20) - 1, step):
            r1, c1 = path_20x20[i]
            r2, c2 = path_20x20[i + 1]
            ax.annotate('',
                xy=(c2 / 5.0, r2 / 5.0),
                xytext=(c1 / 5.0, r1 / 5.0),
                arrowprops=dict(arrowstyle='->', color='#00ee55', lw=2.5),
                zorder=5)

    # 起點標記（左下角，4x4 座標 col=0, row=3）
    ax.plot(0.1, 3.9, 'o', color='#1155ff', markersize=18,
            markeredgecolor='white', markeredgewidth=2, zorder=6)
    ax.text(0.35, 3.85, '起點', fontsize=11, color='white', fontweight='bold',
            zorder=7, bbox=dict(boxstyle='round,pad=0.2', facecolor='#1155ff', alpha=0.8))

    # 出口標記（右上角，4x4 座標 col=3, row=0）
    ax.plot(3.9, 0.1, '*', color='#00cc44', markersize=22,
            markeredgecolor='white', markeredgewidth=1.5, zorder=6)
    ax.text(2.9, 0.3, '安全出口', fontsize=11, color='white', fontweight='bold',
            zorder=7, bbox=dict(boxstyle='round,pad=0.2', facecolor='#00aa33', alpha=0.8))

    # 危險區域標註（右上角附近）
    ax.text(0.1, 0.35, '危險區域', fontsize=10, color='white', fontweight='bold',
            zorder=7, bbox=dict(boxstyle='round,pad=0.2', facecolor='#cc0000', alpha=0.8))

    # 路線標註
    ax.text(1.2, 2.2, '建議逃生路線', fontsize=10, color='#00ee55', fontweight='bold',
            zorder=7, rotation=70,
            bbox=dict(boxstyle='round,pad=0.2', facecolor='#003311', alpha=0.7))

    ax.set_xlim(0, 4)
    ax.set_ylim(0, 4)
    ax.invert_yaxis()
    ax.axis('off')
    plt.tight_layout(pad=0)
    plt.savefig(output_path, dpi=150, bbox_inches='tight')
    plt.close()
    print(f"[逃生圖] 儲存：{output_path}")
    return output_path


# ════════════════════════════════════════════════════
# 5. 生成純熱力圖（供 Nano Banana 2 參考）
# ════════════════════════════════════════════════════

def save_heatmap_only(matrix_20x20, output_path):
    fig, ax = plt.subplots(figsize=(6, 6))
    ax.imshow(matrix_20x20, cmap=plt.cm.RdYlGn_r, vmin=0, vmax=10,
              interpolation='nearest')
    ax.axis('off')
    plt.tight_layout(pad=0)
    plt.savefig(output_path, dpi=150, bbox_inches='tight')
    plt.close()
    print(f"[熱力圖] 儲存：{output_path}")
    return output_path


def save_three_panel(matrix_4x4, matrix_20x20, output_path):
    """三圖合併：課室平面圖 | 4x4 原始熱力圖 | 20x20 插值熱力圖"""
    fig, axes = plt.subplots(1, 3, figsize=(18, 6))
    fig.patch.set_facecolor('#1a1a2e')
    cmap = plt.cm.RdYlGn_r
    title_style = dict(fontsize=13, fontweight='bold', color='white', pad=10)

    # 左：課室平面圖
    axes[0].set_title("Classroom Floor Plan", **title_style)
    if os.path.exists(FLOORPLAN_PATH):
        img = Image.open(FLOORPLAN_PATH).convert("RGB")
        axes[0].imshow(img)
    axes[0].axis('off')

    # 中：4x4 原始熱力圖
    axes[1].set_title("4x4 Sensor Danger Matrix", **title_style)
    im1 = axes[1].imshow(DANGER_MATRIX, cmap=cmap, vmin=0, vmax=100,
                          interpolation='nearest')
    axes[1].set_xticks(range(4))
    axes[1].set_yticks(range(4))
    axes[1].tick_params(colors='white')
    for i in range(4):
        for j in range(4):
            val = int(DANGER_MATRIX[i, j])
            color = 'white' if DANGER_MATRIX[i, j] > 50 else 'black'
            axes[1].text(j, i, str(val), ha='center', va='center',
                         fontsize=14, fontweight='bold', color=color)
    plt.colorbar(im1, ax=axes[1], label="Danger Index")

    # 右：20x20 插值熱力圖
    axes[2].set_title("20x20 Interpolated Heatmap", **title_style)
    im2 = axes[2].imshow(matrix_20x20, cmap=cmap, vmin=0, vmax=10)
    axes[2].tick_params(colors='white')
    plt.colorbar(im2, ax=axes[2], label="Danger Level")

    fig.suptitle("Smart Indoor Fire Escape System - Data Visualization",
                 fontsize=15, fontweight='bold', color='white', y=1.02)
    plt.tight_layout()
    plt.savefig(output_path, dpi=150, bbox_inches='tight', facecolor='#1a1a2e')
    plt.close()
    print(f"[三圖] 儲存：{output_path}")


def generate_with_nanobanana2(floorplan_path, heatmap_path, prompt_path, output_path):
    import base64
    import requests
    from openai import OpenAI

    api_key = os.getenv("APIYI_API_KEY")
    if not api_key:
        print("[NanaBanana2] 錯誤：未設定 APIYI_API_KEY")
        return None

    # 讀取 prompt
    with open(prompt_path, "r", encoding="utf-8") as f:
        prompt_text = f.read()

    # 把兩張圖轉成 base64
    def to_b64(path):
        with open(path, "rb") as f:
            return base64.b64encode(f.read()).decode("utf-8")

    fp_b64 = to_b64(floorplan_path)
    hm_b64 = to_b64(heatmap_path)

    client = OpenAI(
        api_key=api_key,
        base_url="https://api.apiyi.com/v1",
    )

    print("[NanaBanana2] 提交任務...")
    response = client.chat.completions.create(
        model="gemini-3.1-flash-image-preview",
        messages=[
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt_text},
                    {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{fp_b64}"}},
                    {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{hm_b64}"}},
                ],
            }
        ],
        extra_body={"image_config": {"aspect_ratio": "1:1", "image_size": "1K"}},
    )

    # 解析回應（API易 的 Nano Banana 2 把圖片放在 content 文字裡的 markdown 格式）
    for choice in response.choices:
        msg = choice.message
        content = msg.content if hasattr(msg, 'content') else ""

        if isinstance(content, str) and "data:image" in content:
            # 從 markdown ![image](data:image/...) 或純 base64 提取
            import re
            match = re.search(r'data:image/[^;]+;base64,([A-Za-z0-9+/=]+)', content)
            if match:
                img_data = base64.b64decode(match.group(1))
                with open(output_path, "wb") as f:
                    f.write(img_data)
                print(f"[NanaBanana2] 儲存：{output_path}")
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
                    print(f"[NanaBanana2] 儲存：{output_path}")
                    return output_path

        if content:
            print(f"[NanaBanana2] 模型回應：{str(content)[:100]}")

    print("[NanaBanana2] 未收到圖片")
    return None



# ════════════════════════════════════════════════════
# 主程式
# ════════════════════════════════════════════════════

def main():
    os.makedirs(OUTPUT_DIR, exist_ok=True)

    # 步驟 1：讀取感測器數據
    print("步驟 1：讀取感測器數據")
    matrix_4x4 = get_sensor_matrix()
    print(DANGER_MATRIX)

    # 步驟 2：插值
    print("\n步驟 2：雙線性插值 → 20x20")
    matrix_20x20 = interpolate(matrix_4x4, target_size=20)

    # 步驟 3：A* 路線規劃
    print("\n步驟 3：A* 路線規劃")
    path = astar(matrix_20x20, START_20x20, EXIT_20x20)
    if path:
        print(f"  找到路線，共 {len(path)} 步")
    else:
        print("  找不到路線！")

    # 步驟 4：在課室平面圖上畫逃生圖，直接輸出
    print("\n步驟 4：在課室平面圖上疊加逃生路線")
    escape_path = f"{OUTPUT_DIR}/escape_on_floorplan.png"
    draw_escape_on_floorplan(path, escape_path)

    # 步驟 5：生成純熱力圖
    print("\n步驟 5：生成純熱力圖")
    heatmap_path = f"{OUTPUT_DIR}/heatmap_only.png"
    save_heatmap_only(matrix_20x20, heatmap_path)

    # 步驟 6：三圖合併輸出
    print("\n步驟 6：三圖合併輸出")
    save_three_panel(matrix_4x4, matrix_20x20, f"{OUTPUT_DIR}/three_panel.png")

    # 步驟 7：Nano Banana 2 生成最終逃生圖
    print("\n步驟 7：呼叫 Nano Banana 2 生成最終逃生圖")
    prompt_path = os.path.join(os.path.dirname(__file__), 'prompt_nanobanana.txt')
    generate_with_nanobanana2(
        floorplan_path=FLOORPLAN_PATH,
        heatmap_path=heatmap_path,
        prompt_path=prompt_path,
        output_path=f"{OUTPUT_DIR}/nanobanana2_result.png",
    )

    print(f"\n完成！輸出：")
    print(f"  逃生圖（Python）：{OUTPUT_DIR}/escape_on_floorplan.png")
    print(f"  三圖合併        ：{OUTPUT_DIR}/three_panel.png")
    print(f"  逃生圖（AI）    ：{OUTPUT_DIR}/nanobanana2_result.png")


if __name__ == "__main__":
    main()
