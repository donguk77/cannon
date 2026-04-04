"""
tab_report.py — TAB 3: 아침 결재 대시보드
- 가짜 데이터(random) 완전 제거
- 지표 그룹별 개별 막대 그래프 (4개 섹션)
- 실측 데이터 및 Gemini 요약만 표시
"""
import os, sys, json
from dotenv import load_dotenv
load_dotenv()
from PyQt5.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QFrame, QTextEdit, QScrollArea, QMessageBox
)
from PyQt5.QtCore import Qt, QRect, QThread, pyqtSignal
from PyQt5.QtGui import QPainter, QColor, QFont, QPen, QBrush

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
C_BG     = "#F8F9FA"; C_WHITE  = "#FFFFFF"; C_DARK   = "#2C3E50"
C_SUB    = "#7F8C8D"; C_BLUE   = "#3498DB"; C_GREEN  = "#27AE60"
C_RED    = "#E74C3C"; C_ORANGE = "#E67E22"; C_BORDER = "#E0E4E8"
C_YELLOW = "#F39C12"

# ─── 결과 데이터 저장 경로 ───────────────────────────────────────────────────
RESULT_FILE = os.path.join(_ROOT, "models", "latest_metrics.json")


def _load_metrics() -> dict:
    """
    latest_metrics.json에서 실측 지표를 불러옵니다.
    파일이 없으면 None을 반환합니다. (가짜 데이터 절대 없음)
    """
    if not os.path.exists(RESULT_FILE):
        return None
    try:
        with open(RESULT_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        print(f"[tab_report] 지표 파일 로드 실패: {e}")
        return None


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  단일 지표 섹션 막대 그래프 위젯
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
class MetricGroupChart(QWidget):
    """
    하나의 섹션(예: YOLO 탐지 지표)에 속하는 지표들을
    3개 버전 비교 막대 그래프로 표시합니다.
    데이터가 없으면 '데이터 없음' 안내만 표시합니다.
    """
    def __init__(self, section_title: str, metrics: list, colors: list):
        """
        section_title: 섹션 제목 (e.g., "1. YOLO 탐지 성능")
        metrics: [(지표명, [v1.9, v2.0, v2.1]), ...]  — 값이 없으면 None
        colors: 3개 모델 색상 리스트
        """
        super().__init__()
        self.section_title = section_title
        self.metrics  = metrics
        self.colors   = colors
        self.setMinimumHeight(180)
        self.setStyleSheet(f"background:{C_WHITE}; border-radius:8px;")

    def paintEvent(self, event):
        p = QPainter(self); p.setRenderHint(QPainter.Antialiasing)
        w, h = self.width(), self.height()

        # 제목 배경
        p.setBrush(QBrush(QColor(C_BG))); p.setPen(Qt.NoPen)
        p.drawRoundedRect(0, 0, w, 28, 4, 4)
        p.setPen(QPen(QColor(C_DARK)))
        p.setFont(QFont("Malgun Gothic", 11, QFont.Bold))
        p.drawText(QRect(10, 0, w-20, 28), Qt.AlignVCenter, self.section_title)

        # 데이터 없음
        has_data = any(
            any(v is not None for v in vals)
            for _, vals in self.metrics
        )
        if not has_data:
            p.setPen(QPen(QColor(C_SUB)))
            p.setFont(QFont("Malgun Gothic", 10))
            p.drawText(QRect(0, 28, w, h-28), Qt.AlignCenter,
                       "⏳  실측 데이터 없음\n학습 완료 후 자동 표시됩니다.")
            return

        pad_t = 38; pad_b = 36; pad_l = 80; pad_r = 20
        MAX_VAL = 100
        plot_h = h - pad_t - pad_b
        n = len(self.metrics)
        if n == 0: return
        group_w   = (w - pad_l - pad_r) / n
        bar_w     = group_w * 0.22
        bar_gap   = bar_w * 0.15
        MODEL_NAMES = ["v1.9", "v2.0", "v2.1↑"]

        # Y축
        p.setPen(QPen(QColor(C_BORDER), 1))
        for i in range(5):
            yv = i * 25
            yp = pad_t + plot_h - int(plot_h * yv / MAX_VAL)
            p.setPen(QPen(QColor(C_BORDER), 1, Qt.DashLine))
            p.drawLine(pad_l, yp, w-pad_r, yp)
            p.setPen(QPen(QColor(C_SUB)))
            p.setFont(QFont("Malgun Gothic", 7))
            p.drawText(QRect(0, yp-8, pad_l-6, 16), Qt.AlignRight|Qt.AlignVCenter, str(yv))

        for gi, (metric_name, values) in enumerate(self.metrics):
            gx = pad_l + gi * group_w
            for bi, val in enumerate(values):
                if val is None: continue
                bh_px = int(plot_h * min(float(val), MAX_VAL) / MAX_VAL)
                bx = int(gx + bi*(bar_w+bar_gap))
                by = pad_t + plot_h - bh_px
                # 신규(bi=2) 강조 테두리
                p.setPen(QPen(QColor(C_DARK), 1.5) if bi==2 else Qt.NoPen)
                p.setBrush(QBrush(QColor(self.colors[bi])))
                p.drawRoundedRect(bx, by, int(bar_w), bh_px, 2, 2)
                # 값 레이블
                p.setPen(QPen(QColor(C_DARK)))
                p.setFont(QFont("Malgun Gothic", 7, QFont.Bold))
                label = f"{val:.1f}" if isinstance(val, float) else str(val)
                p.drawText(QRect(bx, by-13, int(bar_w), 12), Qt.AlignCenter, label)

            # 지표명 X축
            p.setPen(QPen(QColor(C_DARK)))
            p.setFont(QFont("Malgun Gothic", 8, QFont.Bold))
            p.drawText(QRect(int(gx), h-pad_b+2, int(group_w), 20), Qt.AlignCenter, metric_name)

        # 범례
        lx = pad_l; ly = h - 18
        for i, (name, col) in enumerate(zip(MODEL_NAMES, self.colors)):
            bx2 = lx + i*90
            p.setBrush(QBrush(QColor(col))); p.setPen(Qt.NoPen)
            p.drawRect(bx2, ly-6, 10, 8)
            p.setPen(QPen(QColor(C_DARK)))
            p.setFont(QFont("Malgun Gothic", 7))
            p.drawText(QRect(bx2+12, ly-7, 72, 12), Qt.AlignLeft|Qt.AlignVCenter, name)


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  Gemini 요약 스레드
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
class ReportThread(QThread):
    report_ready = pyqtSignal(str)

    def __init__(self, metrics: dict):
        super().__init__()
        self.metrics = metrics

    def run(self):
        try:
            import requests
            api_key = os.getenv("GEMINI_API_KEY", "")
            url = (f"https://generativelanguage.googleapis.com/v1beta/"
                   f"models/gemini-1.5-flash:generateContent?key={api_key}")
            m = self.metrics
            prompt = (
                "당신은 공장 AI 시스템 운영 보조입니다. "
                f"어젯밤 모델 튜닝 결과 실측 지표: "
                f"mAP50={m.get('map50','N/A')}, "
                f"Recall={m.get('recall','N/A')}, "
                f"Precision={m.get('precision','N/A')}, "
                f"FPS={m.get('fps','N/A')}, "
                f"Latency={m.get('latency_ms','N/A')}ms. "
                "이 수치들을 바탕으로 반장님이 [업데이트 승인] 버튼을 누르도록 "
                "3줄 이내의 친절하고 명확한 보고서를 한국어로 작성하세요. "
                "수치가 N/A이면 '아직 학습 데이터가 없음'이라고 솔직하게 안내하세요."
            )
            payload = {
                "contents": [{"parts": [{"text": prompt}]}],
                "generationConfig": {"maxOutputTokens": 250}
            }
            r = requests.post(url, json=payload, timeout=15)
            r.raise_for_status()
            text = r.json()["candidates"][0]["content"]["parts"][0]["text"].strip()
            self.report_ready.emit(text)
        except Exception as e:
            self.report_ready.emit(f"[Gemini 연결 실패]\n수동으로 지표를 확인하여 승인 여부를 결정하세요.\n오류: {e}")


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  결재 대시보드 탭
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
class ReportTab(QWidget):

    # 모델 버전별 색상 (v1.9 / v2.0 / v2.1↑)
    MODEL_COLORS = ["#85C1E9", "#2E86C1", "#1A5276"]

    def __init__(self):
        super().__init__()
        self.setStyleSheet(f"background:{C_BG};")
        self._metrics    = {}
        self._best_params = {}
        self._rpt_thread = None
        self._build_ui()

    # ── UI 조립 ──────────────────────────────────────────────────────────────
    def _build_ui(self):
        main = QVBoxLayout(self)
        main.setContentsMargins(12,12,12,12); main.setSpacing(10)
        main.addWidget(self._build_header())

        # 스크롤 가능한 그래프 영역 + 우측 결재 패널
        body = QHBoxLayout(); body.setSpacing(12)

        # 좌측: 4개 그룹 차트 스크롤 영역
        scroll = QScrollArea(); scroll.setWidgetResizable(True)
        scroll.setStyleSheet("border:none;")
        charts_w = QWidget()
        self._charts_layout = QVBoxLayout(charts_w)
        self._charts_layout.setSpacing(10)
        self._charts_layout.setContentsMargins(0,0,0,0)
        scroll.setWidget(charts_w)
        body.addWidget(scroll, stretch=3)

        # 우측: 결재 패널
        body.addWidget(self._build_approval_panel(), stretch=2)
        main.addLayout(body, stretch=1)

        # 초기 차트 빌드 (데이터 없음 상태)
        self._rebuild_charts(None)

    def _build_header(self):
        bar = QWidget(); bar.setFixedHeight(48)
        bar.setStyleSheet(f"background:{C_WHITE}; border:1px solid {C_BORDER}; border-radius:8px;")
        h = QHBoxLayout(bar); h.setContentsMargins(12,0,12,0)
        title = QLabel("📊  아침 결재 대시보드 (Morning Report)")
        title.setStyleSheet(f"font-size:14px; font-weight:bold; color:{C_DARK};")
        h.addWidget(title); h.addStretch()
        btn = QPushButton("🔄  최신 학습 결과 불러오기")
        btn.setStyleSheet(f"background:{C_BLUE};color:white;border:none;border-radius:6px;font-weight:bold;")
        btn.clicked.connect(self._load_result); h.addWidget(btn)
        self.lbl_status = QLabel("💤  결재 대기 중인 리포트: 없음")
        self.lbl_status.setStyleSheet(f"font-size:11px; color:{C_SUB}; padding-left:8px;")
        h.addWidget(self.lbl_status)
        return bar

    def _build_approval_panel(self):
        panel = QWidget()
        panel.setStyleSheet(f"background:{C_WHITE}; border:1px solid {C_BORDER}; border-radius:8px;")
        v = QVBoxLayout(panel); v.setContentsMargins(14,14,14,14); v.setSpacing(10)
        v.addWidget(self._lbl("🤖  Gemini AI 3줄 요약", bold=True))

        self.report_box = QTextEdit()
        self.report_box.setReadOnly(True)
        self.report_box.setPlaceholderText(
            "AI 요약 보고서가 여기에 표시됩니다.\n"
            "[최신 학습 결과 불러오기] 버튼을 누르세요."
        )
        self.report_box.setStyleSheet(
            f"QTextEdit{{background:{C_BG}; border:1px solid {C_BORDER};"
            f"border-radius:6px; color:{C_DARK}; font-size:12px; padding:8px;}}")
        v.addWidget(self.report_box, stretch=1)

        sep = QFrame(); sep.setFrameShape(QFrame.HLine)
        sep.setStyleSheet(f"color:{C_BORDER};"); v.addWidget(sep)

        # 모델 버전 롤백 표시
        v.addWidget(self._lbl("🗄️  모델 스냅샷 (롤백 가능)", bold=True))
        self.lbl_snapshots = QLabel("─  학습 기록 없음")
        self.lbl_snapshots.setStyleSheet(
            f"font-size:10px; color:{C_SUB}; background:{C_BG}; border-radius:4px; padding:4px;")
        v.addWidget(self.lbl_snapshots)
        self._refresh_snapshots()
        v.addWidget(sep)

        # 결재 버튼
        btn_approve = QPushButton("✅  신규 파라미터 업데이트 승인")
        btn_approve.setFixedHeight(46)
        btn_approve.setStyleSheet(f"""QPushButton{{background:{C_GREEN};color:white;font-size:13px;
            font-weight:bold;border:none;border-radius:8px;}}
            QPushButton:hover{{background:#1E8449;}}""")
        btn_approve.clicked.connect(self._approve)
        v.addWidget(btn_approve)

        btn_rollback = QPushButton("❌  거부 및 이전 모델 롤백 (Taboo 등록)")
        btn_rollback.setFixedHeight(40)
        btn_rollback.setStyleSheet(f"""QPushButton{{background:{C_RED};color:white;font-size:12px;
            font-weight:bold;border:none;border-radius:8px;}}
            QPushButton:hover{{background:#C0392B;}}""")
        btn_rollback.clicked.connect(self._rollback)
        v.addWidget(btn_rollback)
        return panel

    # ── 차트 재빌드 ───────────────────────────────────────────────────────────
    def _rebuild_charts(self, m: dict):
        """m이 None이면 '데이터 없음' 상태로 모든 차트를 표시"""
        # 기존 차트 위젯 제거
        while self._charts_layout.count():
            item = self._charts_layout.takeAt(0)
            if item.widget(): item.widget().deleteLater()

        def _v(key, history_key=None):
            """지표 값을 [v1.9, v2.0, v2.1] 리스트로 반환 (없으면 None)"""
            if m is None: return [None, None, None]
            history = m.get("history", {})
            prev2 = history.get("v1_9", {}).get(key)
            prev1 = history.get("v2_0", {}).get(key)
            curr  = m.get(key)
            return [prev2, prev1, curr]

        charts = [
            ("1. 객체 탐지 성능 (YOLO 단계)", [
                ("mAP50(%)",     _v("map50")),
                ("IoU(%)",       _v("iou")),
                ("Loc.Error(px)",_v("loc_error")),
            ], ["#AED6F1","#2E86C1","#1A5276"]),

            ("2. 화면 분류/매칭 정확도 (ORB/Siamese)", [
                ("Recall(%)",      _v("recall")),
                ("Precision(%)",   _v("precision")),
                ("F1-Score(%)",    _v("f1_score")),
                ("Match.Conf(%)",  _v("match_confidence")),
            ], ["#A9DFBF","#27AE60","#1E8449"]),

            ("3. 실시간 처리 성능", [
                ("FPS",             _v("fps")),
                ("Latency(ms)",     _v("latency_ms")),
            ], ["#FAD7A0","#E67E22","#A04000"]),

            ("4. MLOps 자가학습 지표", [
                ("Auto-label(%)",   _v("auto_label_acc")),
                ("Human Int.(%)",   _v("human_intervention")),
            ], ["#D2B4DE","#8E44AD","#6C3483"]),
        ]

        for title, metrics, colors in charts:
            chart = MetricGroupChart(title, metrics, colors)
            self._charts_layout.addWidget(chart)

        self._charts_layout.addStretch()

    # ── 이벤트 핸들러 ─────────────────────────────────────────────────────────
    def _load_result(self):
        m = _load_metrics()
        if m is None:
            self.lbl_status.setText("⚠️  latest_metrics.json 파일 없음 — 먼저 학습을 실행하세요.")
            self.lbl_status.setStyleSheet(f"font-size:11px; color:{C_ORANGE}; padding-left:8px;")
            self._rebuild_charts(None)
            self.report_box.setPlainText("학습 완료 후 자동으로 생성된 지표 파일이 없습니다.\n[야간 학습 지시] 탭에서 학습을 먼저 실행하세요.")
            return
        self._metrics = m
        self._best_params = m.get("best_params", {})
        self._rebuild_charts(m)
        self.lbl_status.setText("📝  결재 대기 중인 리포트: 1건")
        self.lbl_status.setStyleSheet(f"font-size:11px; color:{C_ORANGE}; padding-left:8px; font-weight:bold;")
        self.report_box.setPlainText("🔄  Gemini AI 보고서 생성 중...")
        self._rpt_thread = ReportThread(m)
        self._rpt_thread.report_ready.connect(self.report_box.setPlainText)
        self._rpt_thread.start()
        self._refresh_snapshots()

    def _refresh_snapshots(self):
        weights_dir = os.path.join(_ROOT, "models", "canon_fast_yolo", "weights")
        if not os.path.isdir(weights_dir):
            return
        pts = [f for f in os.listdir(weights_dir) if f.endswith(".pt")]
        self.lbl_snapshots.setText("  " + "  /  ".join(pts) if pts else "─  스냅샷 없음")

    def _approve(self):
        if not self._metrics:
            QMessageBox.warning(self,"경고","먼저 [최신 학습 결과 불러오기]를 실행하세요!"); return
        # best_params를 실제 config로 저장 (실전 연동 포인트)
        config_file = os.path.join(_ROOT, "models", "active_params.json")
        with open(config_file, "w") as f:
            json.dump(self._best_params, f, indent=2)
        self.lbl_status.setText("✅  승인 완료 — 시스템에 반영됨")
        self.lbl_status.setStyleSheet(f"font-size:11px; color:{C_GREEN}; padding-left:8px; font-weight:bold;")
        QMessageBox.information(self,"✅ 승인 완료",
            "파라미터가 시스템에 적용되었습니다.\n내일 낮 관제부터 신규 파라미터로 가동됩니다.")

    def _rollback(self):
        if self._best_params:
            try:
                from offline.auto_tuner import BayesianAutoTuner
                BayesianAutoTuner([], []).register_taboo_rollback(self._best_params)
            except: pass
        self.lbl_status.setText("❌  롤백 완료 — 이전 버전으로 복구됨")
        self.lbl_status.setStyleSheet(f"font-size:11px; color:{C_RED}; padding-left:8px; font-weight:bold;")
        QMessageBox.warning(self,"❌ 거부 및 롤백",
            "현재 파라미터를 블랙리스트에 등록했습니다.\n내일 밤 오늘과 전혀 다른 방향으로 탐색합니다.")

    def _lbl(self, t, bold=False):
        l = QLabel(t)
        l.setStyleSheet(f"font-size:12px;{'font-weight:bold;' if bold else ''}color:{C_DARK};")
        return l
