import json
import logging
import os
import shutil
import subprocess
import threading
import urllib.parse
import webbrowser
from collections import deque
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
import numpy as np
from faster_whisper import WhisperModel

# ================= БАЗОВЫЕ НАСТРОЙКИ =================
CLIP_DURATION = 40           # Длительность клипа (в секундах)
SECONDS_BEFORE_PEAK = 28     # Сколько секунд брать ДО пика громкости
MIN_GAP_SECONDS = 90         # Минимальный отступ между клипами

# Координаты вебкамеры на исходном стриме (в долях от ширины и высоты экрана 16:9)
# По умолчанию: верхний левый угол (ширина 26% экрана, высота 30% экрана)
WEBCAM_X = 0.00              # Отступ слева (0.0 = самый левый край)
WEBCAM_Y = 0.00              # Отступ сверху (0.0 = самый верхний край)
WEBCAM_W = 0.26              # Ширина вебкамеры (0.26 = 26% от ширины экрана)
WEBCAM_H = 0.30              # Высота вебкамеры (0.30 = 30% от высоты экрана)

ADD_SUBTITLES = True         # True = пословные караоке-субтитры на стыке экранов
WHISPER_MODEL = "small"      # "small" или "medium"
LANGUAGE = "ru"              # Язык речи

OUTPUT_DIR = "vod_clips"          # Папка со всеми нарезанными клипами
APPROVED_DIR = "approved_clips"   # Папка для отобранных (годных) клипов
TEMP_DIR = "vod_temp"
WEB_PORT = 8765
# =====================================================

STATE = {
    "is_running": False,
    "status_text": "Готов к работе. Вставьте ссылку на VOD и нажмите «Нарезать стрим».",
    "logs": deque(maxlen=40)
}
WHISPER_INSTANCE = None


class WebLogHandler(logging.Handler):
    def emit(self, record):
        msg = self.format(record)
        STATE["logs"].append(msg)
        STATE["status_text"] = record.getMessage()


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S"
)
log = logging.getLogger("VODClipper")
web_handler = WebLogHandler()
web_handler.setFormatter(logging.Formatter("%(asctime)s — %(message)s", datefmt="%H:%M:%S"))
log.addHandler(web_handler)


def get_whisper():
    global WHISPER_INSTANCE
    if WHISPER_INSTANCE is None and ADD_SUBTITLES:
        log.info(f"🧠 Загружаю нейросеть Whisper ({WHISPER_MODEL})...")
        WHISPER_INSTANCE = WhisperModel(WHISPER_MODEL, device="cpu", compute_type="int8")
    return WHISPER_INSTANCE


def prepare_source_video(source: str) -> str:
    source = source.strip().strip('"').strip("'")
    if os.path.exists(source):
        log.info(f"📂 Использую локальный видеофайл: {source}")
        return source

    os.makedirs(TEMP_DIR, exist_ok=True)
    safe_id = "".join(c for c in source.split("/")[-1] if c.isalnum()) or "vod"
    downloaded_path = os.path.join(TEMP_DIR, f"vod_{safe_id}.mp4")

    if os.path.exists(downloaded_path) and os.path.getsize(downloaded_path) > 10_000_000:
        log.info(f"📂 Нашёл уже скачанный VOD в кэше: {downloaded_path}")
        return downloaded_path

    log.info(f"⬇️ Скачиваю запись стрима: {source} ...")
    cmd = [
        "yt-dlp",
        "-f", "bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best",
        "--merge-output-format", "mp4",
        "-o", downloaded_path,
        source
    ]
    subprocess.run(cmd, check=True)
    log.info("✅ Запись стрима скачана.")
    return downloaded_path


def find_hype_timestamps(video_path: str, top_n: int, min_gap: int) -> list[float]:
    log.info("🔊 Анализирую звуковую дорожку стрима на всплески эмоций...")
    cmd = [
        "ffmpeg", "-nostdin", "-loglevel", "error", "-i", video_path,
        "-f", "s16le", "-ac", "1", "-ar", "8000", "pipe:1"
    ]
    res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, check=True)
    samples = np.frombuffer(res.stdout, dtype=np.int16).astype(np.float32) / 32768.0

    sample_rate = 8000
    chunk_size = sample_rate * 2
    total_chunks = len(samples) // chunk_size

    rms_values = []
    for i in range(total_chunks):
        chunk = samples[i * chunk_size : (i + 1) * chunk_size]
        rms = float(np.sqrt(np.mean(chunk ** 2)))
        rms_values.append((i * 2.0, rms))

    rms_values.sort(key=lambda x: x[1], reverse=True)

    selected_peaks = []
    for timestamp, rms in rms_values:
        if timestamp < 15:
            continue
        if all(abs(timestamp - existing) >= min_gap for existing in selected_peaks):
            selected_peaks.append(timestamp)
            if len(selected_peaks) >= top_n:
                break

    selected_peaks.sort()
    log.info(f"🔥 Найдено {len(selected_peaks)} лучших моментов.")
    return selected_peaks


def generate_karaoke_subtitles(whisper: WhisperModel, video_path: str, start_sec: float, duration: float, ass_path: str) -> bool:
    """Генерирует пословные субтитры с фиолетовой подсветкой активного слова на стыке вебкамеры и игры."""
    cmd = [
        "ffmpeg", "-nostdin", "-loglevel", "error",
        "-ss", str(start_sec),
        "-t", str(duration),
        "-i", video_path,
        "-vn", "-f", "s16le", "-ac", "1", "-ar", "16000", "pipe:1"
    ]
    res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, check=True)
    if not res.stdout:
        return False

    audio_np = np.frombuffer(res.stdout, dtype=np.int16).astype(np.float32) / 32768.0
    if len(audio_np) == 0:
        return False

    segments, _ = whisper.transcribe(audio_np, language=LANGUAGE, vad_filter=True, word_timestamps=True)
    segments = list(segments)
    if not segments:
        return False

    def fmt_time(sec: float) -> str:
        h = int(sec // 3600)
        m = int((sec % 3600) // 60)
        s = sec % 60
        return f"{h}:{m:02d}:{s:05.2f}"

    # Alignment=2 (снизу по центру), MarginV=1235 ставит текст ровно на границу Y=640 (стык вебки и игры)
    header = (
        "[Script Info]\nScriptType: v4.00+\nPlayResX: 1080\nPlayResY: 1920\n\n"
        "[V4+ Styles]\n"
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, "
        "Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, "
        "Alignment, MarginL, MarginR, MarginV, Encoding\n"
        "Style: Default,Arial,68,&H00FFFFFF,&H000000FF,&H00000000,&H80000000,-1,0,0,0,100,100,0,0,1,5,2,2,40,40,1235,1\n\n"
        "[Events]\nFormat: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"
    )

    lines = [header]
    # Собираем все слова с таймкодами и группируем по 2-3 слова на экран
    all_words = []
    for seg in segments:
        if getattr(seg, "words", None):
            for w in seg.words:
                clean_w = w.word.strip()
                if clean_w:
                    all_words.append((w.start, w.end, clean_w))
        else:
            clean_t = seg.text.strip()
            if clean_t:
                all_words.append((seg.start, seg.end, clean_t))

    if not all_words:
        return False

    chunk_size = 2
    for i in range(0, len(all_words), chunk_size):
        group = all_words[i : i + chunk_size]
        for active_idx, (w_start, w_end, _) in enumerate(group):
            # Если слово очень короткое, слегка продлеваем до начала следующего
            end_t = group[active_idx + 1][0] if active_idx + 1 < len(group) else w_end
            if end_t <= w_start:
                end_t = w_start + 0.15

            styled_parts = []
            for j, (_, _, word_txt) in enumerate(group):
                if j == active_idx:
                    # Активное слово подсвечивается ярким фиолетово-сиреневым цветом (формат BGR: &H00F775A8)
                    styled_parts.append(r"{\c&H00F775A8&}" + word_txt + r"{\c&H00FFFFFF&}")
                else:
                    styled_parts.append(word_txt)

            dialogue_text = " ".join(styled_parts)
            lines.append(f"Dialogue: 0,{fmt_time(w_start)},{fmt_time(end_t)},Default,,0,0,0,,{dialogue_text}\n")

    with open(ass_path, "w", encoding="utf-8") as f:
        f.writelines(lines)
    return True


def run_clipping_job(source_url: str, max_clips: int, cam_cfg: dict):
    STATE["is_running"] = True
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    os.makedirs(APPROVED_DIR, exist_ok=True)
    os.makedirs(TEMP_DIR, exist_ok=True)

    wx = cam_cfg.get("x", WEBCAM_X)
    wy = cam_cfg.get("y", WEBCAM_Y)
    ww = cam_cfg.get("w", WEBCAM_W)
    wh = cam_cfg.get("h", WEBCAM_H)

    try:
        whisper = get_whisper()
        video_path = prepare_source_video(source_url)
        peaks = find_hype_timestamps(video_path, max_clips, MIN_GAP_SECONDS)

        vod_tag = "".join(c for c in source_url.split("/")[-1] if c.isalnum())[:12] or "vod"

        for idx, peak_sec in enumerate(peaks, start=1):
            start_sec = max(0.0, peak_sec - SECONDS_BEFORE_PEAK)
            ass_file = os.path.join(TEMP_DIR, f"subs_{idx}.ass")
            final_clip = os.path.join(OUTPUT_DIR, f"{vod_tag}_clip_{idx:02d}_at_{int(start_sec)}s.mp4")

            if os.path.exists(final_clip) and os.path.getsize(final_clip) > 100_000:
                log.info(f"⏭ Клип #{idx} уже готов, пропускаю...")
                continue

            has_subs = False
            if whisper:
                log.info(f"💬 [{idx}/{len(peaks)}] Распознаю речь для субтитров...")
                has_subs = generate_karaoke_subtitles(whisper, video_path, start_sec, CLIP_DURATION, ass_file)

            # Split-Screen 9:16 (1080x1920):
            # 1) Верхняя треть (1080x640) — вебкамера из заданного угла экрана
            # 2) Нижние две трети (1080x1280) — центральный кроп геймплея (прицел + HUD по центру)
            split_filter = (
                f"[0:v]crop=iw*{ww:.3f}:ih*{wh:.3f}:iw*{wx:.3f}:ih*{wy:.3f},"
                f"scale=1080:640:force_original_aspect_ratio=increase,crop=1080:640[cam];"
                f"[0:v]scale=-1:1280,crop=1080:1280:(iw-1080)/2:0[game];"
                f"[cam][game]vstack=inputs=2[vout]"
            )

            filters = [split_filter]
            last_v = "[vout]"

            if has_subs:
                escaped_ass = ass_file.replace("\\", "/").replace(":", "\\:")
                filters.append(f"{last_v}ass='{escaped_ass}'[vfinal]")
                last_v = "[vfinal]"

            cmd = [
                "ffmpeg", "-y", "-nostdin", "-loglevel", "error",
                "-ss", str(start_sec),
                "-t", str(CLIP_DURATION),
                "-i", video_path,
                "-filter_complex", ";".join(filters),
                "-map", last_v,
                "-map", "0:a?",
                "-c:v", "libx264", "-preset", "veryfast", "-crf", "22",
                "-pix_fmt", "yuv420p",
                "-c:a", "aac", "-b:a", "192k",
                "-movflags", "+faststart",
                final_clip
            ]

            log.info(f"🎬 [{idx}/{len(peaks)}] Рендерю Split-Screen ролик...")
            try:
                subprocess.run(cmd, check=True, timeout=180)
                log.info(f"✅ Готов клип #{idx}: {os.path.basename(final_clip)}")
            except subprocess.TimeoutExpired:
                log.warning(f"⚠️ Клип #{idx} превысил время рендера, пропускаю...")
            except subprocess.CalledProcessError as err:
                log.warning(f"⚠️ Ошибка FFmpeg на клипе #{idx}: {err}")
            finally:
                if os.path.exists(ass_file):
                    os.remove(ass_file)

        log.info("🎉 Нарезка завершена! Выбирайте лучшие клипы ниже.")

    except Exception as e:
        log.error(f"❌ Ошибка при обработке: {e}")
    finally:
        STATE["is_running"] = False


# ================= ВЕБ-ИНТЕРФЕЙС =================
HTML_PAGE = """<!DOCTYPE html>
<html lang="ru">
<head>
<meta charset="UTF-8">
<title>AutoClipper — Split-Screen Нарезка</title>
<style>
  * { box-sizing: border-box; margin: 0; padding: 0; font-family: 'Segoe UI', system-ui, sans-serif; }
  body { background: #0f0f13; color: #f1f1f5; padding: 24px; }
  .container { max-width: 1400px; margin: 0 auto; }
  header { display: flex; justify-content: space-between; align-items: center; margin-bottom: 20px; flex-wrap: wrap; gap: 12px; }
  h1 { font-size: 22px; font-weight: 700; }
  .stats { display: flex; gap: 12px; font-size: 14px; }
  .badge { background: #1c1c24; padding: 6px 14px; border-radius: 20px; border: 1px solid #2e2e3a; }
  .badge.good { border-color: #22c55e; color: #4ade80; }

  .control-panel { background: #181820; border: 1px solid #2a2a38; border-radius: 14px; padding: 18px; margin-bottom: 24px; }
  .input-row { display: flex; gap: 12px; flex-wrap: wrap; }
  input[type="text"] { flex: 1; min-width: 280px; background: #0f0f14; border: 1px solid #323242; color: #fff; padding: 12px 16px; border-radius: 10px; font-size: 15px; outline: none; }
  input[type="text"]:focus { border-color: #8b5cf6; }
  input[type="number"] { width: 95px; background: #0f0f14; border: 1px solid #323242; color: #fff; padding: 10px 12px; border-radius: 10px; font-size: 14px; outline: none; }
  .btn-start { background: #8b5cf6; color: #fff; padding: 12px 24px; border: none; border-radius: 10px; font-weight: 700; font-size: 15px; cursor: pointer; transition: background 0.15s; flex: 0 0 auto; }
  .btn-start:hover { background: #7c3aed; }
  .btn-start:disabled { background: #3f3f52; color: #8c8ca0; cursor: not-allowed; }

  .cam-settings { margin-top: 14px; display: flex; align-items: center; gap: 14px; flex-wrap: wrap; font-size: 13px; color: #a1a1b5; }
  .cam-settings label { display: flex; align-items: center; gap: 6px; }
  .cam-settings input[type="number"] { width: 72px; padding: 6px 8px; font-size: 13px; }

  .status-bar { margin-top: 14px; display: flex; align-items: center; gap: 10px; font-size: 14px; color: #c4c4d4; background: #121218; padding: 10px 14px; border-radius: 8px; border: 1px solid #23232f; }
  .spinner { width: 16px; height: 16px; border: 2px solid #8b5cf6; border-top-color: transparent; border-radius: 50%; animation: spin 0.8s linear infinite; display: none; }
  @keyframes spin { to { transform: rotate(360deg); } }

  .grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(300px, 1fr)); gap: 22px; }
  .card { background: #181820; border: 1px solid #282834; border-radius: 14px; overflow: hidden; display: flex; flex-direction: column; }
  .card.approved { border-color: #22c55e; box-shadow: 0 0 18px rgba(34, 197, 94, 0.15); }
  video { width: 100%; aspect-ratio: 9 / 16; background: #000; object-fit: contain; }
  .info { padding: 12px 14px 6px; font-size: 13px; color: #a1a1b5; display: flex; justify-content: space-between; gap: 8px; }
  .fname { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .actions { display: flex; gap: 10px; padding: 12px 14px 14px; }
  .actions button { flex: 1; padding: 10px; border: none; border-radius: 8px; font-weight: 600; font-size: 14px; cursor: pointer; }
  .btn-ok { background: #22c55e; color: #052e16; }
  .btn-del { background: #ef4444; color: #fff; }
  .empty { text-align: center; grid-column: 1 / -1; padding: 70px 20px; color: #71718a; font-size: 16px; }
</style>
</head>
<body>
<div class="container">
  <header>
    <h1>🎬 Twitch AutoClipper — Split-Screen (Вебка + Игра)</h1>
    <div class="stats">
      <div class="badge">На проверке: <b id="cnt-pending">0</b></div>
      <div class="badge good">Отобрано (approved_clips): <b id="cnt-approved">0</b></div>
    </div>
  </header>

  <div class="control-panel">
    <div class="input-row">
      <input type="text" id="vod-url" placeholder="Вставьте ссылку на стрим (https://www.twitch.tv/videos/...) или путь к файлу на ПК">
      <input type="number" id="max-clips" value="15" min="1" max="100" title="Сколько клипов нарезать">
      <button class="btn-start" id="btn-run" onclick="startJob()">🚀 Нарезать стрим</button>
    </div>
    <div class="cam-settings">
      <span>📷 Зона вебкамеры на стриме (% от экрана):</span>
      <label>Отступ слева: <input type="number" id="cam-x" value="0" min="0" max="90">%</label>
      <label>Отступ сверху: <input type="number" id="cam-y" value="0" min="0" max="90">%</label>
      <label>Ширина вебки: <input type="number" id="cam-w" value="26" min="5" max="100">%</label>
      <label>Высота вебки: <input type="number" id="cam-h" value="30" min="5" max="100">%</label>
    </div>
    <div class="status-bar">
      <div class="spinner" id="spinner"></div>
      <span id="status-text">Загрузка статуса...</span>
    </div>
  </div>

  <div class="grid" id="grid"></div>
</div>

<script>
let lastCount = -1;

async function startJob() {
  const url = document.getElementById('vod-url').value.trim();
  const maxClips = parseInt(document.getElementById('max-clips').value) || 15;
  const camCfg = {
    x: (parseFloat(document.getElementById('cam-x').value) || 0) / 100,
    y: (parseFloat(document.getElementById('cam-y').value) || 0) / 100,
    w: (parseFloat(document.getElementById('cam-w').value) || 26) / 100,
    h: (parseFloat(document.getElementById('cam-h').value) || 30) / 100
  };

  if (!url) {
    alert('Вставьте ссылку на VOD или путь к видеофайлу!');
    return;
  }

  document.getElementById('btn-run').disabled = true;
  await fetch('/api/start', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ url, max_clips: maxClips, cam: camCfg })
  });
  pollStatus();
}

async function pollStatus() {
  try {
    const res = await fetch('/api/status');
    const st = await res.json();
    document.getElementById('status-text').textContent = st.status_text;
    document.getElementById('btn-run').disabled = st.is_running;
    document.getElementById('spinner').style.display = st.is_running ? 'block' : 'none';

    if (st.total_clips !== lastCount) {
      lastCount = st.total_clips;
      loadClips();
    }
  } catch (e) {}
}

async function loadClips() {
  const res = await fetch('/api/clips');
  const data = await res.json();
  document.getElementById('cnt-pending').textContent = data.pending.length;
  document.getElementById('cnt-approved').textContent = data.approved.length;

  const grid = document.getElementById('grid');
  grid.innerHTML = '';

  const all = [
    ...data.pending.map(f => ({ name: f, status: 'pending', url: '/clips/' + encodeURIComponent(f) })),
    ...data.approved.map(f => ({ name: f, status: 'approved', url: '/approved/' + encodeURIComponent(f) }))
  ];

  if (all.length === 0) {
    grid.innerHTML = '<div class="empty">Пока нет нарезанных клипов. Вставьте ссылку на стрим сверху и нажмите «Нарезать стрим».</div>';
    return;
  }

  for (const item of all) {
    const card = document.createElement('div');
    card.className = 'card ' + (item.status === 'approved' ? 'approved' : '');
    card.innerHTML = `
      <video src="${item.url}" controls preload="metadata"></video>
      <div class="info">
        <span class="fname" title="${item.name}">${item.name}</span>
        <span>${item.status === 'approved' ? '✅ Отобран' : '⏳ Новый'}</span>
      </div>
      <div class="actions">
        ${item.status === 'pending'
          ? `<button class="btn-ok" onclick="act('approve', '${item.name}')">✅ Норм</button>`
          : ''}
        <button class="btn-del" onclick="act('delete', '${item.name}', '${item.status}')">🗑 Удалить</button>
      </div>
    `;
    grid.appendChild(card);
  }
}

async function act(action, filename, status = 'pending') {
  await fetch('/api/action', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ action, filename, status })
  });
  loadClips();
}

setInterval(pollStatus, 2000);
pollStatus();
loadClips();
</script>
</body>
</html>
"""


class ClipReviewHandler(SimpleHTTPRequestHandler):
    def log_message(self, format, *args):
        pass

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path

        if path == "/":
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(HTML_PAGE.encode("utf-8"))
            return

        if path == "/api/status":
            pending = [f for f in os.listdir(OUTPUT_DIR) if f.endswith(".mp4")]
            approved = [f for f in os.listdir(APPROVED_DIR) if f.endswith(".mp4")]
            payload = {
                "is_running": STATE["is_running"],
                "status_text": STATE["status_text"],
                "total_clips": len(pending) + len(approved)
            }
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps(payload).encode("utf-8"))
            return

        if path == "/api/clips":
            pending = sorted([f for f in os.listdir(OUTPUT_DIR) if f.endswith(".mp4")])
            approved = sorted([f for f in os.listdir(APPROVED_DIR) if f.endswith(".mp4")])
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({"pending": pending, "approved": approved}).encode("utf-8"))
            return

        if path.startswith("/clips/") or path.startswith("/approved/"):
            folder = OUTPUT_DIR if path.startswith("/clips/") else APPROVED_DIR
            fname = urllib.parse.unquote(path.split("/", 2)[2])
            fpath = os.path.join(folder, os.path.basename(fname))
            if os.path.exists(fpath):
                self._serve_video(fpath)
                return

        self.send_error(404)

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        body = json.loads(self.rfile.read(length).decode("utf-8")) if length else {}

        if self.path == "/api/start":
            if not STATE["is_running"]:
                url = body.get("url", "").strip()
                max_clips = int(body.get("max_clips", 15))
                cam_cfg = body.get("cam", {})
                if url:
                    threading.Thread(target=run_clipping_job, args=(url, max_clips, cam_cfg), daemon=True).start()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b'{"ok":true}')
            return

        if self.path == "/api/action":
            action = body.get("action")
            fname = os.path.basename(body.get("filename", ""))
            status = body.get("status", "pending")

            src_folder = OUTPUT_DIR if status == "pending" else APPROVED_DIR
            src_path = os.path.join(src_folder, fname)

            if os.path.exists(src_path):
                if action == "approve" and status == "pending":
                    shutil.move(src_path, os.path.join(APPROVED_DIR, fname))
                elif action == "delete":
                    os.remove(src_path)

            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b'{"ok":true}')

    def _serve_video(self, filepath: str):
        file_size = os.path.getsize(filepath)
        range_header = self.headers.get("Range")

        if range_header:
            byte_range = range_header.replace("bytes=", "").split("-")
            start = int(byte_range[0])
            end = int(byte_range[1]) if byte_range[1] else file_size - 1
            length = end - start + 1

            self.send_response(206)
            self.send_header("Content-Type", "video/mp4")
            self.send_header("Accept-Ranges", "bytes")
            self.send_header("Content-Range", f"bytes {start}-{end}/{file_size}")
            self.send_header("Content-Length", str(length))
            self.end_headers()

            with open(filepath, "rb") as f:
                f.seek(start)
                self.wfile.write(f.read(length))
        else:
            self.send_response(200)
            self.send_header("Content-Type", "video/mp4")
            self.send_header("Accept-Ranges", "bytes")
            self.send_header("Content-Length", str(file_size))
            self.end_headers()
            with open(filepath, "rb") as f:
                shutil.copyfileobj(f, self.wfile)


def main():
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    os.makedirs(APPROVED_DIR, exist_ok=True)
    url = f"http://127.0.0.1:{WEB_PORT}"
    server = ThreadingHTTPServer(("127.0.0.1", WEB_PORT), ClipReviewHandler)
    log.info(f"🌐 Веб-панель запущена: {url}")
    webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        server.shutdown()
        log.info("👋 Сервер остановлен.")


if __name__ == "__main__":
    main()