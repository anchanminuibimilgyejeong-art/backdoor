from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse
import html
import hmac
import json
import os
import threading
import time


PORT = int(os.environ.get("PORT", "8080"))
MIRROR_PASSWORD = os.environ.get("MIRROR_PASSWORD", "").strip()
MAX_FRAME_BYTES = 10 * 1024 * 1024
rooms = {}
rooms_lock = threading.Lock()


class Room:
    def __init__(self):
        self.viewers = set()
        self.audio_viewers = set()
        self.password = None
        self.last_frame = None
        self.last_at = 0
        self.last_audio_at = 0
        self.commands = []
        self.next_command_id = 1
        self.lock = threading.Lock()


class Viewer:
    def __init__(self, handler):
        self.handler = handler
        self.lock = threading.Lock()
        self.alive = True

    def send_frame(self, frame):
        with self.lock:
            self.handler.wfile.write(
                b"--frame\r\n"
                b"Content-Type: image/jpeg\r\n"
                + f"Content-Length: {len(frame)}\r\n\r\n".encode("ascii")
            )
            self.handler.wfile.write(frame)
            self.handler.wfile.write(b"\r\n")
            self.handler.wfile.flush()


class AudioViewer:
    def __init__(self, handler):
        self.handler = handler
        self.lock = threading.Lock()
        self.alive = True

    def send_audio(self, chunk):
        with self.lock:
            self.handler.wfile.write(chunk)
            self.handler.wfile.flush()


def get_room(name):
    key = name or "test"
    with rooms_lock:
        if key not in rooms:
            rooms[key] = Room()
        return rooms[key]


def viewer_html(room):
    room_value = html.escape(room or "test", quote=True)
    return f"""<!doctype html>
<html lang="ko">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Mobile Data Mirror</title>
  <style>
    :root {{ color-scheme: light dark; font-family: system-ui, sans-serif; }}
    body {{ margin: 0; background: #111827; color: white; }}
    header {{ display: flex; gap: 8px; align-items: center; padding: 12px; background: #020617; position: sticky; top: 0; flex-wrap: wrap; }}
    input {{ flex: 1; min-width: 130px; padding: 10px; border-radius: 6px; border: 1px solid #475569; background: #0f172a; color: white; }}
    button {{ padding: 10px 14px; border: 0; border-radius: 6px; background: #2563eb; color: white; font-weight: 700; }}
    button.secondary {{ background: #334155; }}
    main {{ display: grid; place-items: center; min-height: calc(100vh - 58px); }}
    img {{ max-width: 100vw; max-height: calc(100vh - 58px); background: #000; object-fit: contain; touch-action: none; user-select: none; }}
    .empty {{ color: #94a3b8; text-align: center; padding: 24px; }}
    .status {{ flex-basis: 100%; color: #94a3b8; font-size: 13px; }}
  </style>
</head>
<body>
  <header>
    <input id="room" value="{room_value}" autocomplete="off" aria-label="room">
    <input id="password" type="password" placeholder="비밀번호" autocomplete="current-password" aria-label="password">
    <button id="connect">연결</button>
    <button class="secondary" data-action="back">뒤로</button>
    <button class="secondary" data-action="home">홈</button>
    <button class="secondary" data-action="recents">최근</button>
    <button class="secondary" data-action="notifications">알림</button>
    <button class="secondary" id="audio">오디오 시작</button>
    <div id="status" class="status">비밀번호 입력 후 연결하세요.</div>
  </header>
  <main>
    <img id="screen" alt="phone screen">
    <div id="empty" class="empty">앱에서 같은 방 코드/비밀번호로 화면 공유를 시작하세요.</div>
  </main>
  <script>
    const roomInput = document.getElementById("room");
    const passwordInput = document.getElementById("password");
    const image = document.getElementById("screen");
    const empty = document.getElementById("empty");
    const status = document.getElementById("status");
    const audioButton = document.getElementById("audio");
    let pointerStart = null;
    let audioContext = null;
    let audioStarted = false;
    let audioClock = 0;

    function authQuery() {{
      const room = encodeURIComponent(roomInput.value.trim() || "test");
      const key = encodeURIComponent(passwordInput.value.trim());
      if (!key) {{
        status.textContent = "비밀번호를 입력하세요.";
        return null;
      }}
      return "room=" + room + "&key=" + key;
    }}
    function connect() {{
      const query = authQuery();
      if (!query) return;
      history.replaceState(null, "", "/viewer?room=" + encodeURIComponent(roomInput.value.trim() || "test"));
      image.src = "/mjpeg?" + query + "&t=" + Date.now();
      empty.style.display = "none";
      status.textContent = "연결 중...";
    }}
    document.getElementById("connect").addEventListener("click", connect);
    image.addEventListener("load", () => status.textContent = "연결됨: 클릭=탭, 드래그=스와이프");
    image.addEventListener("error", () => {{
      empty.style.display = "block";
      status.textContent = "연결 실패. 비밀번호나 앱 공유 상태를 확인하세요.";
    }});
    function imagePoint(event) {{
      const rect = image.getBoundingClientRect();
      if (!rect.width || !rect.height) return null;
      return {{
        x: Math.min(1, Math.max(0, (event.clientX - rect.left) / rect.width)),
        y: Math.min(1, Math.max(0, (event.clientY - rect.top) / rect.height)),
      }};
    }}
    async function sendCommand(command) {{
      const query = authQuery();
      if (!query) return;
      status.textContent = "명령 전송 중...";
      const response = await fetch("/control?" + query, {{
        method: "POST",
        headers: {{ "Content-Type": "application/json" }},
        body: JSON.stringify(command),
      }});
      if (response.ok) {{
        const result = await response.json();
        status.textContent = "명령 #" + result.id + " 전송됨";
      }} else {{
        status.textContent = "명령 실패: " + response.status;
      }}
    }}
    image.addEventListener("pointerdown", (event) => {{
      const point = imagePoint(event);
      if (!point) return;
      pointerStart = {{ ...point, at: Date.now(), id: event.pointerId }};
      image.setPointerCapture(event.pointerId);
      event.preventDefault();
    }});
    image.addEventListener("pointerup", (event) => {{
      if (!pointerStart) return;
      const point = imagePoint(event);
      if (!point) return;
      const dx = point.x - pointerStart.x;
      const dy = point.y - pointerStart.y;
      const distance = Math.sqrt(dx * dx + dy * dy);
      const duration = Math.max(80, Math.min(1200, Date.now() - pointerStart.at));
      if (distance < 0.015) {{
        sendCommand({{ type: "tap", x: point.x, y: point.y }});
      }} else {{
        sendCommand({{ type: "swipe", x: pointerStart.x, y: pointerStart.y, x2: point.x, y2: point.y, duration }});
      }}
      pointerStart = null;
      event.preventDefault();
    }});
    image.addEventListener("contextmenu", (event) => event.preventDefault());
    document.querySelectorAll("[data-action]").forEach((button) => {{
      button.addEventListener("click", () => sendCommand({{ type: "global", action: button.dataset.action }}));
    }});
    audioButton.addEventListener("click", async () => {{
      if (audioStarted) return;
      audioStarted = true;
      audioButton.textContent = "오디오 연결 중";
      try {{
        await startAudioStream();
      }} catch (error) {{
        audioStarted = false;
        audioButton.textContent = "오디오 다시 시작";
        status.textContent = "오디오 실패: " + error.message;
      }}
    }});
    async function startAudioStream() {{
      const query = authQuery();
      if (!query) throw new Error("비밀번호를 입력하세요.");
      audioContext = new (window.AudioContext || window.webkitAudioContext)();
      await audioContext.resume();
      audioClock = audioContext.currentTime + 0.1;
      audioButton.textContent = "오디오 재생 중";

      const response = await fetch("/audio-stream?" + query + "&t=" + Date.now());
      if (!response.ok || !response.body) throw new Error("HTTP " + response.status);

      const reader = response.body.getReader();
      let pending = new Uint8Array(0);
      while (true) {{
        const {{ value, done }} = await reader.read();
        if (done) break;
        if (!value || !value.length) continue;
        const joined = new Uint8Array(pending.length + value.length);
        joined.set(pending, 0);
        joined.set(value, pending.length);
        const usable = joined.length - (joined.length % 2);
        if (usable > 0) playPcm16(joined.subarray(0, usable));
        pending = joined.subarray(usable);
      }}
      throw new Error("stream closed");
    }}
    function playPcm16(bytes) {{
      if (!audioContext || bytes.length < 2) return;
      const samples = bytes.length / 2;
      const audioBuffer = audioContext.createBuffer(1, samples, 24000);
      const channel = audioBuffer.getChannelData(0);
      for (let index = 0; index < samples; index++) {{
        const lo = bytes[index * 2];
        const hi = bytes[index * 2 + 1];
        let value = (hi << 8) | lo;
        if (value >= 0x8000) value -= 0x10000;
        channel[index] = value / 32768;
      }}
      const source = audioContext.createBufferSource();
      source.buffer = audioBuffer;
      source.connect(audioContext.destination);
      const now = audioContext.currentTime;
      if (audioClock < now + 0.05) audioClock = now + 0.05;
      source.start(audioClock);
      audioClock += samples / 24000;
      if (audioClock > now + 0.8) audioClock = now + 0.25;
    }}
  </script>
</body>
</html>"""


class MirrorHandler(BaseHTTPRequestHandler):
    server_version = "MobileDataMirror/1.0"

    def do_GET(self):
        parsed = urlparse(self.path)
        query = parse_qs(parsed.query)
        room_name = query.get("room", ["test"])[0]
        password = request_password(query, self.headers)

        if parsed.path in ("/", "/viewer"):
            body = viewer_html(room_name).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return

        if parsed.path == "/health":
            self.send_text(200, "ok")
            return

        if parsed.path == "/mjpeg":
            if not check_room_password(room_name, password):
                self.send_text(401, "Wrong password")
                return
            self.open_mjpeg(room_name)
            return

        if parsed.path == "/commands":
            if not check_room_password(room_name, password):
                self.send_text(401, "Wrong password")
                return
            try:
                after_id = int(query.get("after", ["0"])[0])
            except ValueError:
                after_id = 0
            room = get_room(room_name)
            with room.lock:
                commands = [item for item in room.commands if item["id"] > after_id]
            body = json.dumps({"commands": commands}).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return

        if parsed.path == "/audio-stream":
            if not check_room_password(room_name, password):
                self.send_text(401, "Wrong password")
                return
            self.open_audio_stream(room_name)
            return

        self.send_text(404, "Not found")

    def do_POST(self):
        parsed = urlparse(self.path)
        query = parse_qs(parsed.query)
        room_name = query.get("room", [self.headers.get("X-Mirror-Room", "test")])[0]
        password = request_password(query, self.headers)

        if parsed.path == "/control":
            if not check_room_password(room_name, password):
                self.send_text(401, "Wrong password")
                return
            self.handle_control(room_name)
            return

        if parsed.path == "/audio":
            if not set_or_check_room_password(room_name, password):
                self.send_text(401, "Wrong password")
                return
            self.handle_audio(room_name)
            return

        if parsed.path != "/frame":
            self.send_text(404, "Not found")
            return

        if not set_or_check_room_password(room_name, password):
            self.send_text(401, "Wrong password")
            return

        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            length = 0

        if length <= 0 or length > MAX_FRAME_BYTES:
            self.send_text(413, "Invalid frame size")
            return

        frame = self.rfile.read(length)
        room = get_room(room_name)
        dead_viewers = []
        with room.lock:
            room.last_frame = frame
            room.last_at = int(time.time() * 1000)
            viewers = list(room.viewers)

        for viewer in viewers:
            try:
                viewer.send_frame(frame)
            except (BrokenPipeError, ConnectionResetError, OSError):
                viewer.alive = False
                dead_viewers.append(viewer)

        if dead_viewers:
            with room.lock:
                for viewer in dead_viewers:
                    room.viewers.discard(viewer)

        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()

    def handle_control(self, room_name):
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            length = 0

        if length <= 0 or length > 64 * 1024:
            self.send_text(413, "Invalid command size")
            return

        try:
            command = json.loads(self.rfile.read(length).decode("utf-8"))
        except json.JSONDecodeError:
            self.send_text(400, "Invalid JSON")
            return

        command_type = command.get("type")
        if command_type not in ("tap", "swipe", "global"):
            self.send_text(400, "Invalid command type")
            return

        clean = {"type": command_type, "createdAt": int(time.time() * 1000)}
        if command_type == "global":
            action = command.get("action")
            if action not in ("back", "home", "recents", "notifications"):
                self.send_text(400, "Invalid global action")
                return
            clean["action"] = action
        else:
            for key in ("x", "y"):
                clean[key] = clamp01(command.get(key))
            if command_type == "swipe":
                clean["x2"] = clamp01(command.get("x2"))
                clean["y2"] = clamp01(command.get("y2"))
                clean["duration"] = int(max(80, min(1200, command.get("duration", 250))))

        room = get_room(room_name)
        with room.lock:
            clean["id"] = room.next_command_id
            room.next_command_id += 1
            room.commands.append(clean)
            room.commands = room.commands[-200:]

        body = json.dumps({"ok": True, "id": clean["id"]}).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def handle_audio(self, room_name):
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            length = 0

        if length <= 0 or length > 512 * 1024:
            self.send_text(413, "Invalid audio size")
            return

        chunk = self.rfile.read(length)
        room = get_room(room_name)
        dead_viewers = []
        with room.lock:
            room.last_audio_at = int(time.time() * 1000)
            viewers = list(room.audio_viewers)

        for viewer in viewers:
            try:
                viewer.send_audio(chunk)
            except (BrokenPipeError, ConnectionResetError, OSError):
                viewer.alive = False
                dead_viewers.append(viewer)

        if dead_viewers:
            with room.lock:
                for viewer in dead_viewers:
                    room.audio_viewers.discard(viewer)

        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()

    def open_mjpeg(self, room_name):
        room = get_room(room_name)
        self.send_response(200)
        self.send_header("Content-Type", "multipart/x-mixed-replace; boundary=frame")
        self.send_header("Cache-Control", "no-cache, no-store, must-revalidate")
        self.send_header("Pragma", "no-cache")
        self.send_header("Connection", "keep-alive")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()

        viewer = Viewer(self)
        with room.lock:
            room.viewers.add(viewer)
            last_frame = room.last_frame

        try:
            if last_frame:
                viewer.send_frame(last_frame)
            while viewer.alive:
                time.sleep(30)
        except (BrokenPipeError, ConnectionResetError, OSError):
            viewer.alive = False
        finally:
            with room.lock:
                room.viewers.discard(viewer)

    def open_audio_stream(self, room_name):
        room = get_room(room_name)
        self.send_response(200)
        self.send_header("Content-Type", "application/octet-stream")
        self.send_header("Cache-Control", "no-cache, no-store, must-revalidate")
        self.send_header("Pragma", "no-cache")
        self.send_header("Connection", "keep-alive")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("X-Audio-Sample-Rate", "24000")
        self.send_header("X-Audio-Format", "pcm_s16le_mono")
        self.end_headers()

        viewer = AudioViewer(self)
        with room.lock:
            room.audio_viewers.add(viewer)

        try:
            while viewer.alive:
                time.sleep(30)
        except (BrokenPipeError, ConnectionResetError, OSError):
            viewer.alive = False
        finally:
            with room.lock:
                room.audio_viewers.discard(viewer)

    def send_text(self, code, message):
        body = message.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt, *args):
        print("[%s] %s" % (self.log_date_time_string(), fmt % args))


def request_password(query, headers):
    values = query.get("key", [""])
    password = values[0] if values else ""
    if not password:
        password = headers.get("X-Mirror-Key", "")
    return password or ""


def set_or_check_room_password(room_name, password):
    if not password:
        return False
    if MIRROR_PASSWORD:
        return hmac.compare_digest(MIRROR_PASSWORD, password)
    room = get_room(room_name)
    with room.lock:
        if room.password is None:
            room.password = password
            return True
        return hmac.compare_digest(room.password, password)


def check_room_password(room_name, password):
    if not password:
        return False
    if MIRROR_PASSWORD:
        return hmac.compare_digest(MIRROR_PASSWORD, password)
    room = get_room(room_name)
    with room.lock:
        if room.password is None:
            return False
        return hmac.compare_digest(room.password, password)


def clamp01(value):
    try:
        number = float(value)
    except (TypeError, ValueError):
        return 0
    return max(0, min(1, number))


def main():
    server = ThreadingHTTPServer(("0.0.0.0", PORT), MirrorHandler)
    print(f"Relay server running on 0.0.0.0:{PORT}")
    server.serve_forever()


if __name__ == "__main__":
    main()
