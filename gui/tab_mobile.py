"""
tab_mobile.py — 모바일 앱 탭
  · 최신 APK 다운로드 QR 코드 (EAS API로 자동 조회)
  · PC 서버 IP 표시
  · 폰 서버 (uvicorn) 시작 / 중지
  · 앱 사용 설명서
"""

import os, sys, socket, json, subprocess, threading
from io import BytesIO

from PyQt5.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QFrame, QLineEdit, QSizePolicy, QScrollArea, QTextEdit,
)
from PyQt5.QtCore import Qt, QThread, pyqtSignal
from PyQt5.QtGui import QPixmap, QFont

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

C_BG     = "#F8F9FA"; C_WHITE  = "#FFFFFF"; C_DARK   = "#2C3E50"
C_SUB    = "#7F8C8D"; C_BLUE   = "#3498DB"; C_GREEN  = "#27AE60"
C_RED    = "#E74C3C"; C_BORDER = "#E0E4E8"; C_CARD   = "#FFFFFF"

EAS_PROJECT_ID = "7874aedd-42a7-4517-8f82-04ea104a0386"
PHONE_SERVER_PORT = 8765
TOKEN_FILE = os.path.join(_ROOT, "data", "eas_token.txt")


def _get_local_ip() -> str:
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "알 수 없음"


def _load_token() -> str:
    if os.path.isfile(TOKEN_FILE):
        try:
            return open(TOKEN_FILE, encoding="utf-8").read().strip()
        except Exception:
            pass
    return ""


def _save_token(token: str):
    os.makedirs(os.path.dirname(TOKEN_FILE), exist_ok=True)
    with open(TOKEN_FILE, "w", encoding="utf-8") as f:
        f.write(token.strip())


def _fetch_latest_apk_url(token: str) -> str | None:
    """EAS GraphQL API로 최신 완료된 Android APK 다운로드 URL을 가져온다."""
    try:
        import requests as req
        import warnings
        warnings.filterwarnings("ignore")

        query = """
        {
          app {
            byId(appId: "%s") {
              builds(platform: ANDROID, limit: 5, offset: 0) {
                status
                artifacts { buildUrl }
              }
            }
          }
        }
        """ % EAS_PROJECT_ID

        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        }
        resp = req.post(
            "https://api.expo.dev/graphql",
            headers=headers,
            json={"query": query},
            timeout=15,
        )
        data = resp.json()
        builds = (
            data.get("data", {})
                .get("app", {})
                .get("byId", {})
                .get("builds", [])
        )
        for build in builds:
            if build.get("status") == "FINISHED":
                url = build.get("artifacts", {}).get("buildUrl")
                if url:
                    return url
    except Exception:
        pass
    return None


def _make_qr_pixmap(url: str, size: int = 240) -> QPixmap | None:
    try:
        import qrcode
        qr = qrcode.QRCode(version=1, box_size=6, border=3)
        qr.add_data(url)
        qr.make(fit=True)
        img = qr.make_image(fill_color="#2C3E50", back_color="white")
        buf = BytesIO()
        img.save(buf, format="PNG")
        buf.seek(0)
        pm = QPixmap()
        pm.loadFromData(buf.read())
        return pm.scaled(size, size, Qt.KeepAspectRatio, Qt.SmoothTransformation)
    except Exception:
        return None


class FetchThread(QThread):
    done = pyqtSignal(str)   # url or ""

    def __init__(self, token: str):
        super().__init__()
        self.token = token

    def run(self):
        url = _fetch_latest_apk_url(self.token) or ""
        self.done.emit(url)


class MobileTab(QWidget):
    def __init__(self):
        super().__init__()
        self._server_proc: subprocess.Popen | None = None
        self._fetch_thread: FetchThread | None = None
        self._apk_url = ""
        self._build_ui()

    # ──────────────────────────────────────── UI 구성 ────
    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(20, 20, 20, 20)
        root.setSpacing(16)

        # ── 상단 2열 ──────────────────────────────────────
        top = QHBoxLayout()
        top.setSpacing(16)
        top.addWidget(self._build_qr_card(), stretch=0)
        top.addWidget(self._build_server_card(), stretch=1)
        root.addLayout(top)

        # ── 설명서 ────────────────────────────────────────
        root.addWidget(self._build_guide_card(), stretch=1)

    # ── QR 카드 ──────────────────────────────────────────
    def _build_qr_card(self) -> QFrame:
        card = self._card()
        lay = QVBoxLayout(card)
        lay.setSpacing(10)

        title = QLabel("📱  APK 다운로드")
        title.setFont(QFont("Malgun Gothic", 12, QFont.Bold))
        title.setStyleSheet(f"color:{C_DARK};")
        lay.addWidget(title)

        # QR 이미지 영역
        self.qr_label = QLabel()
        self.qr_label.setFixedSize(240, 240)
        self.qr_label.setAlignment(Qt.AlignCenter)
        self.qr_label.setStyleSheet(
            f"background:{C_BG}; border:1px solid {C_BORDER}; border-radius:8px;"
        )
        self.qr_label.setText("QR 코드 없음\n아래 버튼을 눌러\n최신 빌드를 가져오세요")
        self.qr_label.setWordWrap(True)
        lay.addWidget(self.qr_label, alignment=Qt.AlignHCenter)

        # URL 표시
        self.url_label = QLabel("")
        self.url_label.setStyleSheet(f"color:{C_SUB}; font-size:10px;")
        self.url_label.setWordWrap(True)
        self.url_label.setAlignment(Qt.AlignCenter)
        lay.addWidget(self.url_label)

        # EAS 토큰 입력
        token_row = QHBoxLayout()
        token_label = QLabel("EXPO_TOKEN")
        token_label.setStyleSheet(f"color:{C_SUB}; font-size:11px;")
        self.token_input = QLineEdit(_load_token())
        self.token_input.setEchoMode(QLineEdit.Password)
        self.token_input.setPlaceholderText("expo.dev 토큰 붙여넣기")
        self.token_input.setStyleSheet(
            f"background:{C_BG}; border:1px solid {C_BORDER}; "
            f"border-radius:5px; padding:4px 8px; font-size:11px;"
        )
        token_row.addWidget(token_label)
        token_row.addWidget(self.token_input)
        lay.addLayout(token_row)

        # 버튼
        self.fetch_btn = QPushButton("🔄  최신 빌드 QR 가져오기")
        self.fetch_btn.setStyleSheet(
            f"background:{C_BLUE}; color:white; border:none; "
            f"border-radius:8px; padding:10px; font-weight:bold;"
        )
        self.fetch_btn.clicked.connect(self._on_fetch)
        lay.addWidget(self.fetch_btn)

        return card

    # ── 서버 카드 ─────────────────────────────────────────
    def _build_server_card(self) -> QFrame:
        card = self._card()
        lay = QVBoxLayout(card)
        lay.setSpacing(10)

        title = QLabel("🖥  PC 서버 정보")
        title.setFont(QFont("Malgun Gothic", 12, QFont.Bold))
        title.setStyleSheet(f"color:{C_DARK};")
        lay.addWidget(title)

        local_ip = _get_local_ip()

        for label_text, value in [
            ("PC IP 주소", local_ip),
            ("WebSocket 포트", str(PHONE_SERVER_PORT)),
            ("접속 주소", f"http://{local_ip}:{PHONE_SERVER_PORT}"),
        ]:
            row = QHBoxLayout()
            lbl = QLabel(label_text)
            lbl.setStyleSheet(f"color:{C_SUB}; font-size:12px; min-width:90px;")
            val = QLabel(value)
            val.setStyleSheet(
                f"background:{C_BG}; color:{C_DARK}; font-weight:bold; "
                f"font-size:13px; padding:5px 10px; border-radius:5px; "
                f"font-family:Consolas,monospace;"
            )
            val.setTextInteractionFlags(Qt.TextSelectableByMouse)
            row.addWidget(lbl)
            row.addWidget(val, stretch=1)
            lay.addLayout(row)

        lay.addSpacing(8)

        # 서버 상태
        self.server_status = QLabel("● 서버 중지됨")
        self.server_status.setStyleSheet(f"color:{C_RED}; font-weight:bold;")
        lay.addWidget(self.server_status)

        # 서버 시작/중지 버튼
        btn_row = QHBoxLayout()
        self.start_btn = QPushButton("▶  서버 시작")
        self.start_btn.setStyleSheet(
            f"background:{C_GREEN}; color:white; border:none; "
            f"border-radius:8px; padding:10px; font-weight:bold;"
        )
        self.start_btn.clicked.connect(self._start_server)

        self.stop_btn = QPushButton("■  서버 중지")
        self.stop_btn.setStyleSheet(
            f"background:{C_RED}; color:white; border:none; "
            f"border-radius:8px; padding:10px; font-weight:bold;"
        )
        self.stop_btn.clicked.connect(self._stop_server)
        self.stop_btn.setEnabled(False)

        btn_row.addWidget(self.start_btn)
        btn_row.addWidget(self.stop_btn)
        lay.addLayout(btn_row)

        lay.addStretch()

        # 서버 로그
        log_title = QLabel("서버 로그")
        log_title.setStyleSheet(f"color:{C_SUB}; font-size:11px;")
        lay.addWidget(log_title)

        self.log_box = QTextEdit()
        self.log_box.setReadOnly(True)
        self.log_box.setMaximumHeight(120)
        self.log_box.setStyleSheet(
            f"background:#1e1e2e; color:#a0f0a0; font-family:Consolas,monospace; "
            f"font-size:11px; border-radius:6px; padding:6px;"
        )
        lay.addWidget(self.log_box)

        return card

    # ── 설명서 카드 ───────────────────────────────────────
    def _build_guide_card(self) -> QFrame:
        card = self._card()
        lay = QVBoxLayout(card)

        title = QLabel("📋  앱 사용 설명서")
        title.setFont(QFont("Malgun Gothic", 12, QFont.Bold))
        title.setStyleSheet(f"color:{C_DARK};")
        lay.addWidget(title)

        guide = QLabel(
            "<h3 style='color:#3498DB;'>📲 앱 설치</h3>"
            "<ol>"
            "<li>위 <b>QR 코드</b>를 폰 카메라로 스캔합니다.</li>"
            "<li>APK 파일을 다운로드합니다.</li>"
            "<li>설치 전 <b>폰 설정 → 보안 → 알 수 없는 출처 허용</b>을 켭니다.</li>"
            "<li>다운로드한 APK를 열어 설치합니다.</li>"
            "</ol>"
            "<h3 style='color:#27AE60;'>🚀 사용 방법</h3>"
            "<ol>"
            "<li>PC와 폰을 <b>같은 WiFi</b>에 연결합니다.</li>"
            "<li>이 탭에서 <b>▶ 서버 시작</b> 버튼을 누릅니다.</li>"
            "<li>폰에서 <b>Canon Monitor 앱</b>을 실행합니다.</li>"
            "<li>앱이 자동으로 서버를 찾아 연결합니다. (약 2~3초)</li>"
            "<li>연결되면 PC 화면을 폰 카메라로 비추면 실시간 분석이 시작됩니다.</li>"
            "</ol>"
            "<h3 style='color:#E67E22;'>⚠️ 주의사항</h3>"
            "<ul>"
            "<li>PC와 폰이 반드시 <b>같은 WiFi 네트워크</b>에 있어야 합니다.</li>"
            "<li>서버가 중지된 상태면 앱에서 <b>연결 끊김</b>으로 표시됩니다.</li>"
            "<li>Windows 방화벽이 포트 8765를 차단하면 연결이 안 될 수 있습니다.</li>"
            "</ul>"
        )
        guide.setTextFormat(Qt.RichText)
        guide.setWordWrap(True)
        guide.setStyleSheet(f"color:{C_DARK}; font-size:12px; line-height:1.6;")

        scroll = QScrollArea()
        scroll.setWidget(guide)
        scroll.setWidgetResizable(True)
        scroll.setStyleSheet("border:none;")
        lay.addWidget(scroll)

        return card

    # ──────────────────────────────────────── 액션 ─────
    def _on_fetch(self):
        token = self.token_input.text().strip()
        if not token:
            self.url_label.setText("EXPO_TOKEN을 입력해주세요")
            return
        _save_token(token)
        self.fetch_btn.setEnabled(False)
        self.fetch_btn.setText("가져오는 중...")
        self.qr_label.setText("조회 중...")

        self._fetch_thread = FetchThread(token)
        self._fetch_thread.done.connect(self._on_fetch_done)
        self._fetch_thread.start()

    def _on_fetch_done(self, url: str):
        self.fetch_btn.setEnabled(True)
        self.fetch_btn.setText("🔄  최신 빌드 QR 가져오기")

        if not url:
            self.qr_label.setText("빌드를 찾을 수 없습니다.\n토큰을 확인하거나\n빌드가 완료됐는지 확인하세요.")
            self.url_label.setText("")
            return

        self._apk_url = url
        pm = _make_qr_pixmap(url, 240)
        if pm:
            self.qr_label.setPixmap(pm)
        self.url_label.setText(url[:60] + ("..." if len(url) > 60 else ""))

    def _start_server(self):
        if self._server_proc and self._server_proc.poll() is None:
            return
        server_path = os.path.join(_ROOT, "connect_phone", "server", "app.py")
        if not os.path.isfile(server_path):
            self._log("서버 파일을 찾을 수 없습니다: " + server_path)
            return

        self._log("서버 시작 중...")
        try:
            self._server_proc = subprocess.Popen(
                [sys.executable, "-m", "uvicorn",
                 "connect_phone.server.app:app",
                 "--host", "0.0.0.0",
                 "--port", str(PHONE_SERVER_PORT),
                 "--reload"],
                cwd=_ROOT,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
            )
            threading.Thread(target=self._read_log, daemon=True).start()
            self.server_status.setText("● 서버 실행 중")
            self.server_status.setStyleSheet(f"color:{C_GREEN}; font-weight:bold;")
            self.start_btn.setEnabled(False)
            self.stop_btn.setEnabled(True)
        except Exception as e:
            self._log(f"서버 시작 실패: {e}")

    def _stop_server(self):
        if self._server_proc:
            self._server_proc.terminate()
            self._server_proc = None
        self.server_status.setText("● 서버 중지됨")
        self.server_status.setStyleSheet(f"color:{C_RED}; font-weight:bold;")
        self.start_btn.setEnabled(True)
        self.stop_btn.setEnabled(False)
        self._log("서버 중지됨")

    def _read_log(self):
        try:
            for line in self._server_proc.stdout:
                self._log(line.rstrip())
        except Exception:
            pass

    def _log(self, msg: str):
        # 백그라운드 스레드에서도 안전하게 호출
        from PyQt5.QtCore import QMetaObject, Q_ARG
        QMetaObject.invokeMethod(
            self.log_box, "append",
            Qt.QueuedConnection,
            Q_ARG(str, msg),
        )

    def closeEvent(self, event):
        self._stop_server()
        super().closeEvent(event)

    # ──────────────────────────────────────── 유틸 ─────
    @staticmethod
    def _card() -> QFrame:
        f = QFrame()
        f.setStyleSheet(
            f"QFrame {{ background:{C_WHITE}; border:1px solid {C_BORDER}; "
            f"border-radius:10px; }}"
        )
        f.setContentsMargins(16, 16, 16, 16)
        return f
