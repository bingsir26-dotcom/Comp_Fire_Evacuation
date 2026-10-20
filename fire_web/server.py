"""
基於生成式人工智能輔助的室內火災逃生最優路徑判斷與指示系統 – 室內火災智慧逃生系統 Web Server
- 從 Arduino USB 序列埠讀取 5 個感測器數值
- WebSocket 即時推送火焰數據 + 20×20 熱力圖
- 本地風險感知 A* 即時逃生路線 + DeepSeek 生成式文字／共享語音指引（選用 legacy Nano Banana AI 概念圖）
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
import hashlib
import importlib.util
import io
import ipaddress
import socket
import shutil
import subprocess
import urllib.error
import urllib.request
from collections import deque
from pathlib import Path

import serial
import serial.tools.list_ports
import numpy as np
from scipy.ndimage import zoom, distance_transform_edt
from PIL import Image
from dotenv import load_dotenv
try:
    from openai import OpenAI
except ImportError:  # 本地即時計算不需要 openai；僅 AI 概念圖功能需要。
    OpenAI = None

try:
    import qrcode
except ImportError:  # QR 分享功能可提示安裝；不影響本地安全路線。
    qrcode = None

from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect, HTTPException
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.middleware.cors import CORSMiddleware
import uvicorn

# ─── 載入環境變數 ───────────────────────────────────────
load_dotenv(dotenv_path=os.path.join(os.path.dirname(__file__), '..', '.env'))

# ─── 路徑設定 ───────────────────────────────────────────
BASE_DIR = Path(__file__).parent
STATIC_DIR = BASE_DIR / "static"
OUTPUT_DIR = BASE_DIR / "outputs"
TTS_OUTPUT_DIR = OUTPUT_DIR / "tts"
ASSETS_DIR = BASE_DIR / "assets"
FLOORPLAN_PATH = ASSETS_DIR / "MyClassRoom.png"
PROMPT_PATH = ASSETS_DIR / "prompt_nanobanana.txt"

OUTPUT_DIR.mkdir(exist_ok=True)
TTS_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

# ─── 伺服器設定 ───────────────────────────────────────────
HOST = os.getenv("HOST", "0.0.0.0")
try:
    PORT = int(os.getenv("PORT", "8080"))
except ValueError:
    PORT = 8080
FALLBACK_PORTS = [PORT, 8000, 3000]
ACTIVE_PORT = PORT

# ─── 生成式 AI 與語音設定 ────────────────────────────────
# 本地 A* 仍是唯一的安全決策來源；DeepSeek 只把已驗證結果寫成人類可讀指引。
DEEPSEEK_BASE_URL = os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com").rstrip("/")
DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY", "").strip()
DEEPSEEK_MODEL = os.getenv("DEEPSEEK_MODEL", "deepseek-v4-flash").strip()
try:
    DEEPSEEK_TIMEOUT_SECONDS = max(3.0, float(os.getenv("DEEPSEEK_TIMEOUT_SECONDS", "12")))
except ValueError:
    DEEPSEEK_TIMEOUT_SECONDS = 12.0
try:
    TTS_TIMEOUT_SECONDS = max(4.0, float(os.getenv("TTS_TIMEOUT_SECONDS", "25")))
except ValueError:
    TTS_TIMEOUT_SECONDS = 25.0
EDGE_TTS_VOICE = os.getenv("EDGE_TTS_VOICE", "zh-CN-XiaoxiaoNeural").strip()
EDGE_TTS_RATE = os.getenv("EDGE_TTS_RATE", "+5%").strip()
EDGE_TTS_VOLUME = os.getenv("EDGE_TTS_VOLUME", "+0%").strip()
GUIDANCE_LOCK = threading.RLock()
GUIDANCE_TTS_LOCK = threading.Lock()
GUIDANCE_IN_FLIGHT: dict[str, threading.Event] = {}
GUIDANCE_STATE = {
    "signature": "",
    "guidance": "安全路線正由本地演算法驗證。",
    "audio_url": "",
    "tts_mode": "pending",
    "source": "pending",
    "model": "",
    "status": "",
    "exit_id": "",
    "risk_level": "",
    "updated_at": 0.0,
    "version": 0,
    "announcement_type": "none",
    "announce": False,
}


class GuidanceUnavailable(RuntimeError):
    """DeepSeek API 暫時不可用。"""


SENSOR_ZONE_LABELS = ("左上方", "左下方", "中央", "右上方", "右下方")


def _guidance_risk_level(mean_risk: float) -> str:
    """保留風險分級供畫面顯示；它不再觸發語音或 DeepSeek 更新。"""
    value = float(mean_risk)
    if value < 2.5:
        return "低"
    if value < 5.0:
        return "中"
    return "高"


def _hazard_summary(sensor_values: list[int]) -> str:
    """把感測器最高熱點轉成已量測的相對位置，不推測未量測的火勢。"""
    values = [max(0, min(255, int(value))) for value in sensor_values[:NUM_SENSORS]]
    if not values or max(values) < 60:
        return ""

    peak = max(values)
    threshold = max(90, int(round(peak * 0.72)))
    zones = [
        SENSOR_ZONE_LABELS[index]
        for index, value in enumerate(values)
        if value >= threshold
    ]
    if not zones:
        return ""
    strength = "強烈" if peak >= 190 else "較強"
    shown_zones = "、".join(zones[:2])
    if len(zones) > 2:
        shown_zones += "等多處"
    return f"{shown_zones}偵測到{strength}火勢"


def _first_route_instruction(turn_points: list) -> str:
    """依已驗證路線的第一段，提供可由受災者跟隨的起步方向。"""
    if len(turn_points) < 2:
        return ""
    try:
        start_row, start_col = (int(value) for value in turn_points[0])
        next_row, next_col = (int(value) for value in turn_points[1])
    except (TypeError, ValueError):
        return ""

    delta_row = next_row - start_row
    delta_col = next_col - start_col
    if abs(delta_row) >= abs(delta_col) and delta_row < 0:
        corridor = "左側" if start_col <= 6 else "右側" if start_col >= 13 else "中央"
        return f"起步先沿{corridor}通道向上前進"
    if abs(delta_row) >= abs(delta_col) and delta_row > 0:
        corridor = "左側" if start_col <= 6 else "右側" if start_col >= 13 else "中央"
        return f"起步先沿{corridor}通道向下前進"
    if delta_col > 0:
        return "起步先沿下方通道向右前進"
    if delta_col < 0:
        return "起步先沿下方通道向左前進"
    return ""


def _operational_core_guidance(context: dict) -> str:
    """安全關鍵句由本地規則固定產生，確保播報不依賴模型措辭。"""
    if context["status"] == "escaped":
        return (
            f"目前可逃生，請依畫面綠色已驗證路線前往{context['exit_name']}，"
            "避開紅色區域。"
        )
    return (
        "目前所有安全出口皆位於火源或高危險範圍，請勿穿越紅色危險區。"
        "請先就地避難並對外求救，系統將持續重新判斷，"
        "待畫面出現新的已驗證綠色路線再撤離。"
    )


def build_guidance_context(route_meta: dict, brightness: list[int]) -> dict:
    """只整理本地安全演算法及感測器已驗證的資料，不讓模型決定路線。"""
    status = route_meta.get("status", "blocked")
    sensors = [int(round(float(value))) for value in brightness[:NUM_SENSORS]]
    hazard_summary = _hazard_summary(sensors)
    if status == "escaped":
        selected_exit = route_meta.get("exit") or {}
        first_move = _first_route_instruction(route_meta.get("turn_points", []))
        spatial_detail_parts = []
        if hazard_summary:
            spatial_detail_parts.append(f"{hazard_summary}，綠線已避開該區域")
        if first_move:
            spatial_detail_parts.append(first_move)
        context = {
            "status": "escaped",
            "exit_id": str(selected_exit.get("id", "safe-exit")),
            "exit_name": str(selected_exit.get("name", "已選定安全出口")),
            "distance_steps": int(route_meta.get("distance_steps", 0)),
            "mean_risk": float(route_meta.get("mean_risk", 0)),
            "risk_level": _guidance_risk_level(float(route_meta.get("mean_risk", 0))),
            "min_clearance_cells": float(route_meta.get("min_clearance_cells", 0)),
            "turn_points": route_meta.get("turn_points", []),
            "sensor_values": sensors,
            "hazard_summary": hazard_summary,
            "first_move": first_move,
            "spatial_detail": "。".join(spatial_detail_parts) + ("。" if spatial_detail_parts else ""),
        }
    else:
        context = {
            "status": "blocked",
            "reason": str(route_meta.get("reason", "未能建立符合安全緩衝的逃生路線")),
            "sensor_values": sensors,
            "hazard_summary": hazard_summary,
            "first_move": "",
            "spatial_detail": f"{hazard_summary}，請遠離該區域。" if hazard_summary else "",
        }
    context["core_guidance"] = _operational_core_guidance(context)
    return context


def build_guidance_messages(context: dict) -> list[dict]:
    """要求 DeepSeek 只生成一條具體的空間補充句；安全核心由本地規則保證。"""
    system_message = (
        "你是室內火災逃生系統的生成式情境指引輔助員。"
        "安全決策、出口和撤離／避難指令已由本地系統固定，不能修改。"
        "你只能根據【已驗證空間資料】寫一句繁體中文補充，最多 42 個中文字。"
        "補充必須提及提供的火勢位置或第一段通道方向其中至少一項；"
        "不可自行選擇出口、不可猜測火源、不可加入未提供的轉彎、"
        "不可重複固定安全核心、不可加入風險等級、不要標題或 Markdown。"
    )
    facts = (
        "【本地系統固定安全核心，不可改寫】\n"
        f"{context['core_guidance']}\n\n"
        "【已驗證空間資料】\n"
        f"火勢位置：{context.get('hazard_summary') or '目前沒有足夠感測資料描述明顯火勢位置'}\n"
        f"路線起步：{context.get('first_move') or '未提供'}\n"
        "請只輸出一條有用的空間補充句。"
    )
    return [{"role": "system", "content": system_message}, {"role": "user", "content": facts}]

def _clean_generated_guidance(text: str) -> str:
    """限制模型補充句，避免冗長文字干擾緊急畫面。"""
    clean = re.sub(r"\s+", " ", str(text or "")).strip()
    clean = re.sub(r"[`*_#]", "", clean)
    if len(clean) > 70:
        clean = clean[:70].rstrip("，、；。 ") + "。"
    return clean


def _is_verified_spatial_supplement(text: str, context: dict) -> bool:
    """只接受可從本地量測或已驗證綠線回查的空間補充。"""
    if not text or "目前可逃生" in text or "目前所有安全出口" in text:
        return False

    hazard_summary = str(context.get("hazard_summary", ""))
    valid_zones = [zone for zone in SENSOR_ZONE_LABELS if zone in hazard_summary]
    if any(zone in text for zone in valid_zones):
        return True

    first_move = str(context.get("first_move", ""))
    if first_move:
        direction = next((item for item in ("向上", "向下", "向左", "向右") if item in first_move), "")
        corridor = next((item for item in ("左側", "右側", "中央", "下方") if item in first_move), "")
        return bool(direction and corridor and direction in text and corridor in text)
    return False


def _compose_guidance(context: dict, ai_supplement: str = "") -> str:
    """合併固定安全核心與可選的 AI 空間補充；不足時使用確定性資料。"""
    supplement = _clean_generated_guidance(ai_supplement)
    if not _is_verified_spatial_supplement(supplement, context):
        supplement = str(context.get("spatial_detail", "")).strip()
    if supplement and supplement[-1] not in "。！？":
        supplement += "。"
    core = str(context.get("core_guidance") or _operational_core_guidance(context))
    return f"{core}{supplement}"

def _fallback_guidance(context: dict) -> str:
    return _compose_guidance(context)


def _guidance_signature(context: dict) -> str:
    """只把撤離／避難狀態與安全出口變化視為需要新語音的事件。"""
    data = {"status": context["status"]}
    if context["status"] == "escaped":
        data["exit"] = context["exit_id"]
    raw = json.dumps(data, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]


def _announcement_type(context: dict) -> str:
    """初始載入只顯示文字；真正狀態轉換才要求主控端自動播報。"""
    if not GUIDANCE_STATE.get("signature"):
        return "initial"
    if GUIDANCE_STATE.get("status") != context["status"]:
        return "evacuation_available" if context["status"] == "escaped" else "shelter_required"
    if context["status"] == "escaped" and GUIDANCE_STATE.get("exit_id") != context["exit_id"]:
        return "exit_changed"
    return "none"


def _generate_deepseek_guidance(context: dict) -> str:
    if not DEEPSEEK_API_KEY:
        raise GuidanceUnavailable("尚未設定 DEEPSEEK_API_KEY。")
    payload = {
        "model": DEEPSEEK_MODEL,
        "messages": build_guidance_messages(context),
        "temperature": 0.2,
        "thinking": {"type": "disabled"},
        "max_tokens": 160,
        "stream": False,
    }
    request = urllib.request.Request(
        f"{DEEPSEEK_BASE_URL}/chat/completions",
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {DEEPSEEK_API_KEY}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=DEEPSEEK_TIMEOUT_SECONDS) as response:
            data = json.loads(response.read().decode("utf-8"))
        guidance = _clean_generated_guidance(data["choices"][0]["message"]["content"])
    except urllib.error.HTTPError as exc:
        raise GuidanceUnavailable(f"DeepSeek API 回應失敗（HTTP {exc.code}）。") from exc
    except (urllib.error.URLError, TimeoutError, OSError, KeyError, IndexError, json.JSONDecodeError) as exc:
        raise GuidanceUnavailable("DeepSeek 暫時無法回應。") from exc
    if not guidance:
        raise GuidanceUnavailable("DeepSeek 未產生可用指引。")
    return guidance


def _tts_output_path(text: str) -> Path:
    cache_key = f"{EDGE_TTS_VOICE}|{EDGE_TTS_RATE}|{EDGE_TTS_VOLUME}|{text}"
    digest = hashlib.sha1(cache_key.encode("utf-8")).hexdigest()[:20]
    return TTS_OUTPUT_DIR / f"guidance_{digest}.mp3"


def _synthesize_tts(text: str) -> dict:
    """產生可由主畫面與 QR 觀眾共用的 Edge TTS 音檔。"""
    if not importlib.util.find_spec("edge_tts"):
        return {"audio_url": "", "tts_mode": "unavailable", "tts_error": "edge-tts 尚未安裝"}
    output_path = _tts_output_path(text)
    if output_path.exists() and output_path.stat().st_size >= 1000:
        return {"audio_url": f"/outputs/tts/{output_path.name}", "tts_mode": "edge-tts"}

    async def save_audio(temp_path: Path) -> None:
        import edge_tts

        communicate = edge_tts.Communicate(
            text,
            voice=EDGE_TTS_VOICE,
            rate=EDGE_TTS_RATE,
            volume=EDGE_TTS_VOLUME,
        )
        await asyncio.wait_for(communicate.save(str(temp_path)), timeout=TTS_TIMEOUT_SECONDS)

    with GUIDANCE_TTS_LOCK:
        if output_path.exists() and output_path.stat().st_size >= 1000:
            return {"audio_url": f"/outputs/tts/{output_path.name}", "tts_mode": "edge-tts"}
        temp_path = output_path.with_suffix(".tmp.mp3")
        try:
            asyncio.run(save_audio(temp_path))
            if not temp_path.exists() or temp_path.stat().st_size < 1000:
                raise RuntimeError("語音檔案無效")
            temp_path.replace(output_path)
            return {"audio_url": f"/outputs/tts/{output_path.name}", "tts_mode": "edge-tts"}
        except Exception as exc:
            temp_path.unlink(missing_ok=True)
            return {"audio_url": "", "tts_mode": "failed", "tts_error": type(exc).__name__}


def get_or_create_guidance(context: dict) -> dict:
    """只在首次或重大火場事件生成一次指引；細微感測波動仍由本地 A* 即時處理。"""
    signature = _guidance_signature(context)
    with GUIDANCE_LOCK:
        if GUIDANCE_STATE["signature"] == signature:
            return {
                **GUIDANCE_STATE,
                "reused": True,
                "updated": False,
                "update_policy": "unchanged",
                "announcement_type": "none",
                "announce": False,
            }

        wait_event = GUIDANCE_IN_FLIGHT.get(signature)
        if wait_event is None:
            wait_event = threading.Event()
            GUIDANCE_IN_FLIGHT[signature] = wait_event
            owner = True
        else:
            owner = False

    if not owner:
        wait_event.wait(DEEPSEEK_TIMEOUT_SECONDS + TTS_TIMEOUT_SECONDS + 3)
        with GUIDANCE_LOCK:
            if GUIDANCE_STATE["signature"] == signature:
                return {
                    **GUIDANCE_STATE,
                    "reused": True,
                    "updated": False,
                    "update_policy": "unchanged",
                    "announcement_type": "none",
                    "announce": False,
                }
        return {
            "signature": signature,
            "guidance": _fallback_guidance(context),
            "audio_url": "",
            "tts_mode": "pending",
            "source": "local_safety_fallback",
            "model": "",
            "updated_at": time.time(),
            "version": GUIDANCE_STATE["version"],
            "reused": False,
            "updated": False,
            "update_policy": "pending",
            "announcement_type": "none",
            "announce": False,
        }

    try:
        announcement_type = _announcement_type(context)
        try:
            ai_supplement = _generate_deepseek_guidance(context)
            guidance = _compose_guidance(context, ai_supplement)
            source = "deepseek"
            model = DEEPSEEK_MODEL
            error = ""
        except GuidanceUnavailable as exc:
            guidance = _fallback_guidance(context)
            source = "local_safety_fallback"
            model = ""
            error = str(exc)
        tts = _synthesize_tts(guidance)
        with GUIDANCE_LOCK:
            GUIDANCE_STATE.update({
                "signature": signature,
                "guidance": guidance,
                "audio_url": tts.get("audio_url", ""),
                "tts_mode": tts.get("tts_mode", "unavailable"),
                "tts_error": tts.get("tts_error", ""),
                "source": source,
                "model": model,
                "error": error,
                "status": context["status"],
                "exit_id": context.get("exit_id", ""),
                "risk_level": context.get("risk_level", "避難"),
                "updated_at": time.time(),
                "version": int(GUIDANCE_STATE["version"]) + 1,
                "update_policy": "generated",
                "announcement_type": announcement_type,
                "announce": announcement_type in {"evacuation_available", "shelter_required", "exit_changed"},
            })
            return {**GUIDANCE_STATE, "reused": False, "updated": True}
    finally:
        with GUIDANCE_LOCK:
            event = GUIDANCE_IN_FLIGHT.pop(signature, None)
            if event:
                event.set()

# ─── 序列埠設定 ─────────────────────────────────────────
# 可用環境變數 SERIAL_PORT=COM4 強制指定；未指定時自動偵測 USB Arduino/CH340。
SERIAL_PORT = os.getenv("SERIAL_PORT", "").strip()
BAUD_RATE = 115200
NUM_SENSORS = 5


def detect_serial_port():
    """自動尋找常見 Arduino / USB Serial 裝置，避開藍牙 COM 埠。"""
    ports = list(serial.tools.list_ports.comports())
    if not ports:
        print("[Serial] 找不到任何序列埠")
        return None

    print("[Serial] 可用序列埠:")
    for p in ports:
        print(f"  - {p.device}: {p.description}")

    preferred_keywords = (
        "arduino",
        "ch340",
        "usb-serial",
        "usb serial",
        "wch",
        "cp210",
        "silicon labs",
    )
    for p in ports:
        text = f"{p.description} {p.hwid}".lower()
        if any(keyword in text for keyword in preferred_keywords):
            return p.device

    return None


def is_bindable(host, port):
    """檢查主機與埠是否能成功綁定。"""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            sock.bind((host, port))
        return True
    except OSError:
        return False

class QuickTunnelManager:
    """管理 Cloudflare Quick Tunnel；只在 ENABLE_QUICK_TUNNEL=1 時啟動。"""

    URL_PATTERN = re.compile(r"https://[a-z0-9-]+\.trycloudflare\.com\b", re.IGNORECASE)

    def __init__(self, enabled: bool):
        self.enabled = enabled
        self.lock = threading.RLock()
        self.process = None
        self.url = None
        self.status = "disabled" if not enabled else "idle"
        self.message = (
            "跨網絡公開展示未啟用。請以 run_public.bat 啟動。"
            if not enabled
            else "等待啟動 Cloudflare Quick Tunnel。"
        )
        self.executable = None
        self.last_log = ""

    @staticmethod
    def _is_executable(path) -> bool:
        return bool(path and Path(path).is_file())

    def find_executable(self):
        """依序尋找環境變數、專案 tools 目錄與 PATH 中的 cloudflared。"""
        explicit_path = os.getenv("CLOUDFLARED_PATH", "").strip()
        candidates = [
            explicit_path,
            str(BASE_DIR / "tools" / "cloudflared.exe"),
            shutil.which("cloudflared"),
        ]
        for candidate in candidates:
            if self._is_executable(candidate):
                return str(Path(candidate).resolve())
        return None

    def _read_output(self, process):
        try:
            assert process.stdout is not None
            for raw_line in process.stdout:
                line = raw_line.strip()
                if not line:
                    continue
                with self.lock:
                    self.last_log = line[-500:]
                    match = self.URL_PATTERN.search(line)
                    if match:
                        self.url = match.group(0)
                        self.status = "active"
                        self.message = "跨網絡公開展示已啟用；任何可上網的裝置均可掃碼觀看。"
                        print(f"[Tunnel] 公開網址: {self.url}")
        finally:
            return_code = process.poll()
            with self.lock:
                if self.process is process:
                    self.process = None
                    if self.status == "stopping":
                        self.status = "stopped"
                        self.message = "跨網絡公開展示已關閉。"
                    elif return_code not in (None, 0):
                        self.status = "error"
                        self.message = "Cloudflare Quick Tunnel 已中斷，請檢查網絡或重新啟動公開展示。"
                    elif self.status != "active":
                        self.status = "error"
                        self.message = "Cloudflare Quick Tunnel 未能建立公開網址。"

    def start(self, port: int):
        if not self.enabled:
            return self.get_status()

        with self.lock:
            if self.process and self.process.poll() is None:
                return self.get_status()

            executable = self.find_executable()
            if not executable:
                self.status = "unavailable"
                self.message = (
                    "找不到 cloudflared。請先執行 fire_web\\install_cloudflared.ps1，"
                    "再使用 run_public.bat 啟動。"
                )
                return self.get_status()

            self.executable = executable
            self.url = None
            self.last_log = ""
            self.status = "starting"
            self.message = "正在建立 Cloudflare Quick Tunnel，通常需要數秒…"
            command = [
                executable,
                "tunnel",
                "--no-autoupdate",
                "--url",
                f"http://127.0.0.1:{port}",
            ]
            popen_kwargs = {
                "stdout": subprocess.PIPE,
                "stderr": subprocess.STDOUT,
                "stdin": subprocess.DEVNULL,
                "text": True,
                "encoding": "utf-8",
                "errors": "replace",
                "bufsize": 1,
            }
            if os.name == "nt":
                popen_kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
            try:
                self.process = subprocess.Popen(command, **popen_kwargs)
            except OSError as error:
                self.process = None
                self.status = "error"
                self.message = f"無法啟動 cloudflared：{error}"
                return self.get_status()

            threading.Thread(
                target=self._read_output,
                args=(self.process,),
                daemon=True,
                name="cloudflare-quick-tunnel",
            ).start()
            print(f"[Tunnel] 正在建立公開通道 -> http://127.0.0.1:{port}")
            return self.get_status()

    def stop(self):
        with self.lock:
            process = self.process
            if not process or process.poll() is not None:
                self.process = None
                if self.enabled and self.status not in {"unavailable", "error"}:
                    self.status = "stopped"
                    self.message = "跨網絡公開展示已關閉。"
                return
            self.status = "stopping"
            self.message = "正在關閉跨網絡公開展示…"

        process.terminate()
        try:
            process.wait(timeout=4)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=2)

        with self.lock:
            if self.process is process:
                self.process = None
                self.status = "stopped"
                self.message = "跨網絡公開展示已關閉。"

    def get_status(self):
        with self.lock:
            running = bool(self.process and self.process.poll() is None)
            return {
                "enabled": self.enabled,
                "status": self.status,
                "active": self.status == "active" and bool(self.url) and running,
                "url": self.url if self.status == "active" and running else None,
                "message": self.message,
                "executable": bool(self.executable or self.find_executable()),
            }


QUICK_TUNNEL_ENABLED = os.getenv("ENABLE_QUICK_TUNNEL", "0").strip().lower() in {"1", "true", "yes", "on"}
tunnel_mgr = QuickTunnelManager(enabled=QUICK_TUNNEL_ENABLED)


def get_lan_ipv4():
    """取得可讓同一 Wi-Fi 裝置連入的私有 IPv4；不依賴第三方網頁服務。"""
    candidates = []
    try:
        # UDP connect 不會傳送資料，但可取得 Windows 選擇的本機網卡位址。
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.connect(("8.8.8.8", 80))
            candidates.append(sock.getsockname()[0])
    except OSError:
        pass

    try:
        candidates.extend(socket.gethostbyname_ex(socket.gethostname())[2])
    except OSError:
        pass

    for address in candidates:
        try:
            parsed = ipaddress.ip_address(address)
            if parsed.version == 4 and parsed.is_private and not parsed.is_loopback:
                return str(parsed)
        except ValueError:
            continue
    return None


def get_share_info(port=None):
    """建立 QR 分享資料；公開 Tunnel 成功時優先使用 HTTPS 公開網址。"""
    lan_ip = get_lan_ipv4()
    resolved_port = int(port or ACTIVE_PORT)
    lan_url = f"http://{lan_ip}:{resolved_port}/" if lan_ip else None
    tunnel = tunnel_mgr.get_status()
    public_url = tunnel["url"] if tunnel["active"] else None

    if public_url:
        url = public_url
        mode = "public"
        message = "🌐 跨網絡公開展示已啟用：手機不必連接同一個 Wi-Fi。"
    elif lan_url:
        url = lan_url
        mode = "lan"
        message = (
            "正在建立跨網絡公開通道；完成前此 QR Code 僅限同一個 Wi-Fi。"
            if tunnel["enabled"] and tunnel["status"] == "starting"
            else "📶 同一個 Wi-Fi 的手機可掃描此 QR Code 同步觀看。"
        )
    else:
        url = None
        mode = "unavailable"
        message = "找不到區域網路 IPv4，請確認 Wi-Fi；或使用跨網絡公開展示模式。"

    return {
        "available": bool(url),
        "mode": mode,
        "lan_ip": lan_ip,
        "port": resolved_port,
        "lan_url": lan_url,
        "public_url": public_url,
        "url": url,
        "message": message,
        "tunnel": tunnel,
    }

# ─── A* / 熱力圖設定 ────────────────────────────────────
GRID_4X4 = 4
GRID_20X20 = 20
START_4X4 = (3, 0)    # 左下角 (row=3, col=0)
EXIT_4X4 = (0, 3)     # 右上角 (row=0, col=3) — 出口在右上
START_20X20 = (19, 0)
# 現場平面圖目前標示一個右上安全出口；日後只要在此新增已確認的出口即可。
EXIT_CANDIDATES_20X20 = (
    {"id": "exit_top_right", "name": "右上安全出口", "cell": (0, 19)},
)
EXIT_20X20 = EXIT_CANDIDATES_20X20[0]["cell"]  # 保留給舊有繪圖程式使用

# 本地安全路線參數（單位：20×20 格）
# 高於 HARD_DANGER_THRESHOLD 的位置視為火源／不可通過區；周邊一格也封鎖，
# 其餘近火區則以較高成本處理，讓演算法主動保留安全距離。
HARD_DANGER_THRESHOLD = 7.5
HARD_SAFETY_BUFFER_CELLS = 1.0
PREFERRED_CLEARANCE_CELLS = 4.0
HEAT_RISK_COST_SCALE = 18.0
CLEARANCE_COST_SCALE = 4.5

# 單一旋鈕不應瞬間封鎖整個課室：先平滑，再將單一感測器上限壓到 8 / 10。
# 穩定維持在最大值時，仍可能跨過硬性火源門檻以保留安全保護；安全出口另採較低門檻。
SENSOR_SMOOTHING_ALPHA = 0.35
SENSOR_RISK_CEILING = 8.0
EXIT_DANGER_THRESHOLD = 6.8

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
    def __init__(self, port=None, baudrate=BAUD_RATE):
        self.configured_port = port
        self.port = port or "AUTO"
        self.baudrate = baudrate
        self.ser = None
        self.running = False
        self.lock = threading.Lock()
        self.brightness = [0] * NUM_SENSORS
        self.knob_value = [0] * NUM_SENSORS
        self.history = [deque(maxlen=100) for _ in range(NUM_SENSORS)]
        self.connected = False

    def connect(self):
        selected_port = self.configured_port or detect_serial_port()
        if not selected_port:
            print("[Serial] 未找到 Arduino USB 序列埠，略過硬體連線")
            self.connected = False
            return False

        try:
            self.port = selected_port
            self.ser = serial.Serial(self.port, self.baudrate, timeout=1)
            self.connected = True
            print(f"[Serial] 已連接到 {self.port} (baud: {self.baudrate})")
            return True
        except serial.SerialException as e:
            print(f"[Serial] 無法連接到 {self.port}: {e}")
            self.connected = False
            return False

    def close_serial(self):
        if self.ser and self.ser.is_open:
            self.ser.close()
        self.ser = None
        self.connected = False

    def disconnect(self):
        self.running = False
        self.close_serial()
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
                    self.connect()
                    time.sleep(2)
            except serial.SerialException:
                print("[Serial] 讀取錯誤，嘗試重連...")
                self.close_serial()
                time.sleep(2)
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
        self.running = True
        self.connect()
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
        self.route_meta = {}
        self._last_safety_model = None
        self.smoothed_brightness = np.zeros(NUM_SENSORS, dtype=float)
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
        # 對旋鈕值作低通平滑，並限制單一感測器的最高危險影響。
        # 此設計避免某一個旋鈕突然轉到底，就把整張地圖瞬間變成不可通行。
        raw = np.asarray(list(brightness_5)[:NUM_SENSORS], dtype=float)
        if raw.size < NUM_SENSORS:
            raw = np.pad(raw, (0, NUM_SENSORS - raw.size))
        raw = np.clip(raw, 0, 255)
        self.smoothed_brightness = (
            SENSOR_SMOOTHING_ALPHA * raw
            + (1.0 - SENSOR_SMOOTHING_ALPHA) * self.smoothed_brightness
        )
        vals = [
            min(SENSOR_RISK_CEILING * 10, value / 255.0 * SENSOR_RISK_CEILING * 10)
            for value in self.smoothed_brightness
        ]

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

    def _build_safety_model(self):
        """建立本地風險地圖：桌子、火源禁區與安全緩衝均會納入規劃。"""
        risk_grid = np.clip(self.matrix_20x20.astype(float), 0, 10)
        structural_obstacles = ~OBSTACLE_MASK_20X20.copy()
        direct_fire = risk_grid >= HARD_DANGER_THRESHOLD

        if np.any(direct_fire):
            # 對每一格計算與最近火源的距離；距離愈小，通行成本愈高。
            clearance = distance_transform_edt(~direct_fire)
        else:
            clearance = np.full(risk_grid.shape, np.hypot(*risk_grid.shape), dtype=float)

        fire_buffer = direct_fire | (clearance <= HARD_SAFETY_BUFFER_CELLS)
        blocked = structural_obstacles | fire_buffer

        normalized_risk = risk_grid / 10.0
        heat_cost = HEAT_RISK_COST_SCALE * np.square(normalized_risk)
        clearance_gap = np.maximum(0.0, PREFERRED_CLEARANCE_CELLS - clearance)
        clearance_cost = CLEARANCE_COST_SCALE * np.square(clearance_gap)
        travel_cost = 1.0 + heat_cost + clearance_cost
        travel_cost[blocked] = np.inf

        model = {
            "risk_grid": risk_grid,
            "structural_obstacles": structural_obstacles,
            "direct_fire": direct_fire,
            "clearance": clearance,
            "blocked": blocked,
            "travel_cost": travel_cost,
        }
        self._last_safety_model = model
        return model

    @staticmethod
    def _path_to_waypoints(path):
        """把逐格路徑壓縮為轉彎點，方便在網頁及簡報中解釋導引方向。"""
        if len(path) <= 2:
            return path[:]

        waypoints = [path[0]]
        previous_direction = (path[1][0] - path[0][0], path[1][1] - path[0][1])
        for index in range(1, len(path) - 1):
            current_direction = (
                path[index + 1][0] - path[index][0],
                path[index + 1][1] - path[index][1],
            )
            if current_direction != previous_direction:
                waypoints.append(path[index])
                previous_direction = current_direction
        waypoints.append(path[-1])
        return waypoints

    @staticmethod
    def _as_cell_list(mask):
        return [[int(row), int(col)] for row, col in np.argwhere(mask)]

    def get_route_visual_data(self):
        """回傳前端 Canvas 所需資料，不生成 PNG，因此不依賴 API 且反應更快。"""
        model = self._last_safety_model or self._build_safety_model()
        return {
            "risk_grid": model["risk_grid"].round(3).tolist(),
            "structural_obstacles": self._as_cell_list(model["structural_obstacles"]),
            "fire_cells": self._as_cell_list(model["direct_fire"]),
            "blocked_cells": self._as_cell_list(model["blocked"]),
            "start": [int(START_20X20[0]), int(START_20X20[1])],
            "exits": [
                {"id": item["id"], "name": item["name"], "cell": list(item["cell"])}
                for item in EXIT_CANDIDATES_20X20
            ],
        }

    def run_astar(self):
        """執行本地風險感知 A*，在最短距離、熱風險與安全緩衝之間取平衡。"""
        started_at = time.perf_counter()
        model = self._build_safety_model()
        blocked = model["blocked"]
        travel_cost = model["travel_cost"]
        risk_grid = model["risk_grid"]
        clearance = model["clearance"]
        start = START_20X20
        rows, cols = risk_grid.shape

        viable_exits = [
            exit_item for exit_item in EXIT_CANDIDATES_20X20
            if not blocked[exit_item["cell"]]
            and risk_grid[exit_item["cell"]] < EXIT_DANGER_THRESHOLD
        ]
        if not viable_exits:
            self.path_20x20 = []
            self.route_meta = {
                "status": "blocked",
                "reason": "所有已設定安全出口均位於火源、安全緩衝區或高危險範圍內",
                "compute_ms": round((time.perf_counter() - started_at) * 1000, 2),
            }
            return []

        goal_cells = {item["cell"]: item for item in viable_exits}

        def heuristic(cell):
            # 每一步的最低成本為 1，因此曼哈頓距離可作為安全且快速的 A* 啟發式。
            return min(abs(cell[0] - goal[0]) + abs(cell[1] - goal[1]) for goal in goal_cells)

        open_set = []
        push_order = 0
        heapq.heappush(open_set, (heuristic(start), 0.0, push_order, start))
        came_from = {}
        g_score = {start: 0.0}
        visited = set()
        selected_exit = None

        while open_set:
            _, current_cost, _, current = heapq.heappop(open_set)
            if current in visited:
                continue
            visited.add(current)

            if current in goal_cells:
                selected_exit = goal_cells[current]
                break

            row, col = current
            for delta_row, delta_col in ((-1, 0), (1, 0), (0, -1), (0, 1)):
                next_row, next_col = row + delta_row, col + delta_col
                next_cell = (next_row, next_col)
                if not (0 <= next_row < rows and 0 <= next_col < cols):
                    continue
                if blocked[next_cell]:
                    continue

                new_cost = current_cost + float(travel_cost[next_cell])
                if new_cost < g_score.get(next_cell, float("inf")):
                    came_from[next_cell] = current
                    g_score[next_cell] = new_cost
                    push_order += 1
                    heapq.heappush(
                        open_set,
                        (new_cost + heuristic(next_cell), new_cost, push_order, next_cell),
                    )

        if not selected_exit:
            self.path_20x20 = []
            self.route_meta = {
                "status": "blocked",
                "reason": "火勢、安全緩衝區或桌子阻塞了所有通往安全出口的路徑",
                "searched_cells": len(visited),
                "compute_ms": round((time.perf_counter() - started_at) * 1000, 2),
            }
            return []

        current = selected_exit["cell"]
        path = [current]
        while current in came_from:
            current = came_from[current]
            path.append(current)
        path.reverse()
        self.path_20x20 = path

        path_risks = [float(risk_grid[cell]) for cell in path]
        path_clearances = [float(clearance[cell]) for cell in path]
        self.route_meta = {
            "status": "escaped",
            "exit": {
                "id": selected_exit["id"],
                "name": selected_exit["name"],
                "cell": [int(selected_exit["cell"][0]), int(selected_exit["cell"][1])],
            },
            "distance_steps": len(path) - 1,
            "path_cells": len(path),
            "turn_points": [[int(row), int(col)] for row, col in self._path_to_waypoints(path)],
            "total_cost": round(float(g_score[selected_exit["cell"]]), 2),
            "mean_risk": round(float(np.mean(path_risks)), 2),
            "max_risk": round(float(np.max(path_risks)), 2),
            "min_clearance_cells": round(float(np.min(path_clearances)), 2),
            "safety_buffer_cells": HARD_SAFETY_BUFFER_CELLS,
            "preferred_clearance_cells": PREFERRED_CLEARANCE_CELLS,
            "searched_cells": len(visited),
            "compute_ms": round((time.perf_counter() - started_at) * 1000, 2),
        }
        return self.path_20x20

    def find_safest_reachable(self):
        """
        當無法逃生時，從起點出發，找出所有可到達且非障礙物的格子中
        危險指數最低的作為避難點。
        使用 BFS 遍歷可通行區域（避開障礙物）。
        """
        start = START_20X20
        model = self._build_safety_model()
        blocked = model["blocked"]
        rows, cols = self.matrix_20x20.shape

        # BFS 找所有可到達的格子（避開桌子、火源與安全緩衝區）
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
                if blocked[nr, nc]:
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
        ax.set_title('無法建立安全逃生路線：請就地避難', fontsize=14, color='red',
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

        if OpenAI is None:
            print("[NanoBanana] 未安裝 openai；本地安全路線功能不受影響")
            return None

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
app = FastAPI(title="基於生成式人工智能輔助的室內火災逃生最優路徑判斷與指示系統 Web Server", version="1.0")

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
                "port": serial_mgr.port,
                "timestamp": time.time(),
            })

        await asyncio.sleep(0.2)  # 200ms


@app.on_event("startup")
async def startup_event():
    # 啟動序列埠
    serial_mgr.start()
    # 啟動背景推送
    asyncio.create_task(sensor_push_loop())
    if QUICK_TUNNEL_ENABLED:
        tunnel_status = tunnel_mgr.start(ACTIVE_PORT)
        print(f"[Tunnel] {tunnel_status['message']}")
    print("[Server] 基於生成式人工智能輔助的室內火災逃生最優路徑判斷與指示系統 Web Server 已啟動")


@app.on_event("shutdown")
async def shutdown_event():
    tunnel_mgr.stop()
    serial_mgr.stop()


# ─── REST API ────────────────────────────────────────────

@app.get("/api/status")
async def get_status():
    """取得目前連線狀態"""
    return {
        "serial_connected": serial_mgr.connected,
        "port": serial_mgr.port,
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
async def generate_escape(monitor: bool = False):
    """本地即時計算安全路線；避難期間仍持續監測，恢復通行時會回到規劃模式。"""
    previous_status = engine.route_meta.get("status")
    engine.update_from_brightness(serial_mgr.get_brightness())
    path = engine.run_astar()
    visual_data = engine.get_route_visual_data()

    if path:
        meta = engine.route_meta
        return {
            "status": "escaped",
            "success": True,
            "path_length": len(path),
            "path": [[int(row), int(col)] for row, col in path],
            "route": meta,
            "message": f"已選擇 {meta['exit']['name']}；路線避開火源及安全緩衝區",
            **visual_data,
        }

    # 無法逃生時顯示避難圖；持續監測期間若狀態未變，沿用上一張圖以免每秒重繪 PNG。
    safest_cell, safest_val, reachable = engine.find_safest_reachable()
    shelter_path = OUTPUT_DIR / "shelter_plot.png"
    if not monitor or previous_status != "blocked" or not shelter_path.exists():
        engine.generate_shelter_plot()
    return {
        "status": "blocked",
        "success": False,
        "shelter_image": "/outputs/shelter_plot.png",
        "safest_cell": list(safest_cell),
        "safest_value": round(float(safest_val), 2),
        "reachable_cells": reachable,
        "route": engine.route_meta,
        "message": "🚨 無法建立符合安全緩衝的逃生路線，請就地避難等待救援",
        **visual_data,
    }


@app.post("/api/generative-guidance")
async def generate_generative_guidance():
    """把本地 A* 已驗證結果交給 DeepSeek 生成指引並快取共享語音。"""
    engine.update_from_brightness(serial_mgr.get_brightness())
    engine.run_astar()
    context = build_guidance_context(engine.route_meta, serial_mgr.get_brightness())
    generated = await asyncio.to_thread(get_or_create_guidance, context)
    response = {"status": context["status"], "context": context, **generated}
    if generated.get("updated") and connected_clients:
        await broadcast_ws({"type": "guidance_update", **response})
    return response


@app.get("/api/generative-guidance-status")
async def get_generative_guidance_status():
    """回傳不含金鑰的 DeepSeek 與 TTS 狀態。"""
    return {
        "mode": "deepseek",
        "model": DEEPSEEK_MODEL,
        "configured": bool(DEEPSEEK_API_KEY),
        "tts_available": bool(importlib.util.find_spec("edge_tts")),
        "update_policy": "只在首次、可否逃生狀態改變或安全出口改變時生成；路線微調與風險級別變化不會呼叫 API。",
        "safety_note": "安全路線由本地 A* 演算法驗證；生成式 AI 只負責文字與語音輔助。",
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


@app.get("/api/tunnel-status")
async def get_tunnel_status():
    """回傳 Cloudflare Quick Tunnel 狀態，供前端更新公開 QR Code。"""
    return tunnel_mgr.get_status()


@app.get("/api/share")
async def get_share_url(request: Request):
    """回傳供同一 Wi-Fi 手機開啟本系統的本機網址與 QR 圖片端點。"""
    request_port = request.url.port or ACTIVE_PORT
    info = get_share_info(request_port)
    info["qr_image"] = "/api/share-qr" if info["available"] else None
    return info


@app.get("/api/share-qr")
async def get_share_qr(request: Request):
    """在本機產生 QR Code PNG；不需要 CDN 或外部 QR API。"""
    if qrcode is None:
        raise HTTPException(status_code=503, detail="未安裝 qrcode 套件")
    info = get_share_info(request.url.port or ACTIVE_PORT)
    if not info["url"]:
        raise HTTPException(status_code=503, detail=info["message"])

    image = qrcode.make(info["url"])
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return Response(content=buffer.getvalue(), media_type="image/png")


@app.get("/api/floorplan")
async def get_floorplan():
    """提供本機課室平面圖給 Canvas 疊加即時安全路線。"""
    if not FLOORPLAN_PATH.exists():
        raise HTTPException(status_code=404, detail="找不到課室平面圖")
    return FileResponse(str(FLOORPLAN_PATH), media_type="image/png")


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
    print("  🔥 基於生成式人工智能輔助的室內火災逃生最優路徑判斷與指示系統 – Web Server")
    print("=" * 50)
    print(f"  序列埠: {SERIAL_PORT}")
    print("=" * 50)

    hosts_to_try = [HOST]
    if HOST == "0.0.0.0":
        hosts_to_try.append("127.0.0.1")

    chosen_host = None
    chosen_port = None
    for host in hosts_to_try:
        for port in dict.fromkeys(FALLBACK_PORTS):
            if is_bindable(host, port):
                chosen_host = host
                chosen_port = port
                break
            print(f"[Warning] {host}:{port} 綁定失敗，嘗試下一個埠")
        if chosen_host:
            break

    if not chosen_host or not chosen_port:
        raise OSError("找不到可用的 host/port 組合")

    ACTIVE_PORT = chosen_port
    local_display_host = "localhost" if chosen_host in {"0.0.0.0", "127.0.0.1"} else chosen_host
    print(f"  Local:   http://{local_display_host}:{chosen_port}")
    if chosen_host == "0.0.0.0":
        lan_ip = get_lan_ipv4()
        if lan_ip:
            print(f"  LAN:     http://{lan_ip}:{chosen_port}")
    print(f"  WebSocket: ws://{local_display_host}:{chosen_port}/ws")
    print("=" * 50)

    uvicorn.run(
        app,
        host=chosen_host,
        port=chosen_port,
        reload=False,
        log_level="info",
    )
