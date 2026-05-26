# 慧焰導行 — 火勢與熱力圖畫面說明（中英對照）

---

## 1. 五點火勢模擬旋鈕

### Fire level simulator (5 knobs)

**English**

At the top of the screen, you can see five vertical knobs. Each knob represents one position in the classroom: top-left, bottom-left, center, top-right, and bottom-right.

When you move a knob, you are setting the fire level at that point. The value goes from 0 (no fire) to 255 (very strong fire).

Right now, we are not using real fire. We use the knobs to simulate different fire situations for testing and learning.

**中文**

在畫面最上方，你會看到五個直立的旋鈕。每一個旋鈕代表教室裡的一個位置：左上、左下、中間、右上、右下。

當你轉動旋鈕，就是在設定那個位置的「火勢強度」。數值範圍是 0（沒有火）到 255（火勢非常強）。

目前我們沒有真的放火，而是用旋鈕來模擬不同的火災情境，方便測試和學習。

---

## 2. 從 5 個旋鈕到一張熱力圖

### From 5 knobs to a heatmap

**English**

The system reads the 5 knob values and thinks: "I know the fire level at these 5 points. But what about the whole room?"

To answer this, we first build a small 4×4 danger grid. We place the 5 known values on the grid and estimate the other cells using nearby values.

Then, we use interpolation to scale this 4×4 grid up to a smooth 20×20 heatmap. This heatmap shows how dangerous each small area of the classroom is.

**中文**

系統讀取這 5 個旋鈕的數值後會思考：「我知道這 5 個點的火勢，但整個教室其他地方呢？」

為了回答這個問題，我們先做出一張小小的 4×4 危險矩陣。把 5 個已知的數值放在對應的位置，再用附近的數值去估算其他格子的危險程度。

接著，我們用「插值法」把 4×4 放大成平滑的 20×20 熱力圖。這張熱力圖就代表教室中每一小塊區域的危險程度。

---

## 3. 插值法的簡單想法

### Interpolation – simple idea

**English**

Interpolation means "filling in the gaps between known points". If we know the fire level at several key points, we can estimate the fire level in the middle.

In 慧焰導行, we know the fire at 5 positions. We use interpolation so the computer can guess the fire level at many more positions, not just 5.

This is why the heatmap looks smooth and continuous, not just a few blocks.

**中文**

插值法的意思就是「用已知的點，推算中間沒量到的點」。如果我們知道幾個關鍵位置的火勢，就可以估算中間區域大概有多熱、多危險。

在慧焰導行裡，我們只真正量到 5 個位置的火勢。透過插值法，電腦可以幫我們推算出更多位置的火勢，而不是只有 5 個點。

因此你看到的熱力圖是連續、平滑的顏色漸變，而不是只有幾塊零散的方格。

---

## 4. 即時危險熱力圖（20×20）

### Real-time danger heatmap (20×20)

**English**

In the middle of the screen, you see a colorful square. This is the 20×20 danger heatmap of the classroom.

Each small square (cell) has a color:

- **Red** means high risk.
- **Yellow or orange** means medium risk.
- **Green** means safe or low risk.

When you move the knobs, the heatmap updates in real time. You can see how the danger zone changes when the fire moves.

**中文**

畫面中間的那個彩色正方形，就是教室的 20×20 危險熱力圖。

每一個小格子都有一個顏色：

- **紅色**代表高危險區。
- **黃色或橘色**代表中度危險。
- **綠色**代表安全或低危險區。

當你轉動上面的旋鈕時，熱力圖會即時更新。你可以清楚看到火勢改變時，危險區域是如何移動和擴散的。

---

## 5. 逃生路線圖與 AI 視覺化

### Escape route map and AI view

**English**

Under the heatmap, there is an escape route picture. The route is calculated by the A* algorithm using the 20×20 danger map.

The algorithm chooses a path that avoids red zones and does not cross the desks. This gives us a safe and short way from the start point to the exit.

Sometimes, we also ask an AI model to draw a nicer version of this map. The AI overlays the heatmap on the real classroom floor plan and adds arrows to show the route.

**中文**

在熱力圖下面，你會看到一張逃生路線圖。這條路線是 A* 演算法根據 20×20 危險地圖計算出來的。

演算法會盡量避開紅色高危險區，也不會穿越桌子所在的格子。這樣就可以找出一條又安全又不太繞路的逃生路線，從起點走到出口。

有時候，我們還會請 AI 模型幫忙畫出更漂亮的版本。AI 會把熱力圖疊在真實的教室平面圖上，再加上箭頭，讓路線更直觀。

---

## 6. 為什麼這個系統要用插值法？

### Why do we use interpolation in this system?

**English**

In the real world, it is impossible to put a sensor in every tiny spot of the classroom. Sensors cost money, and too many wires would be messy and unsafe.

By using interpolation, we only need a few sensors (or knobs) at key positions. The computer can then estimate the danger in the whole room from these few points.

This design keeps the hardware cost low but still gives us a detailed danger map. It is a smart balance between "accuracy" and "cost".

**中文**

在真實世界裡，不可能在教室的每一個小地方都放一個感測器。感測器要錢、線材又會變得很亂，也可能影響安全。

透過插值法，我們只需要在幾個關鍵位置放感測器（或用旋鈕來模擬）。電腦就可以根據這幾個點，推算出整個教室裡其他位置的大致危險程度。

這樣的設計可以用很少的硬體成本，換來一張細緻的危險地圖。在「準確度」和「成本」之間取得一個很聰明的平衡。