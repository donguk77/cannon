import sys
import os
from datetime import datetime

# ─── DLL 충돌(WinError 1114) 방지 핵심 ─────────────────────────────────────────────
# 운영체제가 PyQt5의 DLL(Qt5GUI.dll)을 메모리에 올려버리기 전에, 
# PyTorch와 YOLO 엔진(c10.dll 등)을 애플리케이션 최상단에서 우선하여 로딩시킵니다.
try:
    import torch
    from ultralytics import YOLO
except ImportError:
    pass
# ──────────────────────────────────────────────────────────────────────────────

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from PyQt5.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout,
    QHBoxLayout, QLabel, QTabWidget, QFrame, QPushButton, QMessageBox
)
from PyQt5.QtCore import Qt, QTimer
from PyQt5.QtGui import QFont, QColor

# ─── 전역 색상 (Clean White 테마) ─────────────────────────────────────────────
C_BG         = "#F8F9FA"   # 전체 배경 (오프 화이트)
C_WHITE      = "#FFFFFF"   # 카드 배경
C_TEXT_DARK  = "#2C3E50"   # 기본 텍스트 (딥 그레이)
C_TEXT_SUB   = "#7F8C8D"   # 보조 텍스트
C_BLUE       = "#3498DB"   # 포인트 블루 (정상/긍정)
C_GREEN      = "#27AE60"   # 성공/승인
C_RED        = "#E74C3C"   # 에러/위험
C_ORANGE     = "#E67E22"   # 경고/Hard Negative
C_BORDER     = "#E0E4E8"   # 구분선
C_SHADOW     = "rgba(0,0,0,0.06)"

# ─── 글로벌 스타일시트 ──────────────────────────────────────────────────────────
GLOBAL_STYLE = f"""
    QMainWindow, QWidget {{
        background-color: {C_BG};
        color: {C_TEXT_DARK};
        font-family: 'Malgun Gothic', 'Segoe UI', sans-serif;
        font-size: 13px;
    }}
    QTabWidget::pane {{
        border: 1px solid {C_BORDER};
        background: {C_WHITE};
        border-radius: 8px;
    }}
    QTabBar::tab {{
        background: {C_BG};
        color: {C_TEXT_SUB};
        padding: 11px 28px;
        font-size: 13px;
        font-weight: bold;
        border: 1px solid {C_BORDER};
        border-bottom: none;
        border-top-left-radius: 6px;
        border-top-right-radius: 6px;
        margin-right: 3px;
    }}
    QTabBar::tab:selected {{
        background: {C_WHITE};
        color: {C_BLUE};
        border-bottom: 3px solid {C_BLUE};
    }}
    QTabBar::tab:hover {{ color: {C_TEXT_DARK}; }}
    QPushButton {{
        background: {C_WHITE};
        color: {C_TEXT_DARK};
        border: 1px solid {C_BORDER};
        border-radius: 6px;
        padding: 8px 18px;
        font-size: 12px;
        font-weight: bold;
    }}
    QPushButton:hover {{
        background: {C_BLUE};
        color: white;
        border-color: {C_BLUE};
    }}
    QScrollBar:vertical {{
        border: none; background: {C_BG}; width: 8px; border-radius: 4px;
    }}
    QScrollBar::handle:vertical {{
        background: {C_BORDER}; border-radius: 4px; min-height: 30px;
    }}
"""


class HeaderBar(QWidget):
    """상단 고정 헤더: 로고 + 시스템 상태 표시 + 실시간 시각"""
    def __init__(self):
        super().__init__()
        self.setFixedHeight(58)
        self.setStyleSheet(f"""
            background: {C_WHITE};
            border-bottom: 1px solid {C_BORDER};
        """)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(24, 0, 24, 0)

        # 로고 타이틀
        title = QLabel("🏭  Canon AI Vision")
        title.setStyleSheet(f"font-size: 18px; font-weight: bold; color: {C_TEXT_DARK};")
        layout.addWidget(title)

        ver = QLabel("v5.0")
        ver.setStyleSheet(f"font-size: 11px; color: {C_TEXT_SUB}; padding-top: 4px;")
        layout.addWidget(ver)
        layout.addStretch()

        # 시스템 상태 표시
        self.lbl_status = QLabel("● 시스템 정상 가동 중")
        self.lbl_status.setStyleSheet(f"font-size: 13px; font-weight: bold; color: {C_GREEN}; padding-right: 16px;")
        layout.addWidget(self.lbl_status)

        # 구분선
        sep = QFrame()
        sep.setFrameShape(QFrame.VLine)
        sep.setStyleSheet(f"color: {C_BORDER};")
        layout.addWidget(sep)

        # 실시간 시각
        self.lbl_time = QLabel()
        self.lbl_time.setStyleSheet(f"font-size: 13px; color: {C_TEXT_SUB}; padding-left: 16px;")
        layout.addWidget(self.lbl_time)

        timer = QTimer(self)
        timer.timeout.connect(self._tick)
        timer.start(1000)
        self._tick()

    def _tick(self):
        from datetime import datetime
        time_str = datetime.now().strftime("%Y-%m-%d  %H:%M:%S")
        self.lbl_time.setText(f"⏰  {time_str}")

    def set_status(self, ok: bool, msg: str = ""):
        if ok:
            self.lbl_status.setText(f"● {msg or '시스템 정상 가동 중'}")
            self.lbl_status.setStyleSheet(f"font-size: 13px; font-weight: bold; color: {C_GREEN}; padding-right: 16px;")
        else:
            self.lbl_status.setText(f"● {msg or '에러 감지!'}")
            self.lbl_status.setStyleSheet(f"font-size: 13px; font-weight: bold; color: {C_RED}; padding-right: 16px;")


class EStopBar(QWidget):
    """하단 고정 긴급 정지 바"""
    def __init__(self):
        super().__init__()
        self.setFixedHeight(56)
        self.setStyleSheet(f"background: #FFF5F5; border-top: 2px solid {C_RED};")
        layout = QHBoxLayout(self)
        layout.setContentsMargins(24, 0, 24, 0)

        note = QLabel("⚠️  AI 지연 500ms 이상 또는 이상 감지 시 즉시 누르세요")
        note.setStyleSheet(f"color: {C_ORANGE}; font-size: 12px;")
        layout.addWidget(note)
        layout.addStretch()

        btn = QPushButton("🚨  E-STOP  —  전체 즉시 정지")
        btn.setFixedHeight(38)
        btn.setMinimumWidth(280)
        btn.setStyleSheet(f"""
            QPushButton {{
                background: {C_RED}; color: white; font-size: 14px;
                font-weight: bold; border-radius: 6px; border: none;
            }}
            QPushButton:hover {{ background: #C0392B; }}
            QPushButton:pressed {{ background: #922B21; }}
        """)
        btn.clicked.connect(self._trigger)
        layout.addWidget(btn)

    def _trigger(self):
        QMessageBox.critical(None, "🚨 긴급 정지",
            "모든 AI 분석 및 스트리밍이 즉각 중단됩니다!\n\n안전 확인 후 시스템을 재가동 하십시오.")


class AppStudioMainWindow(QMainWindow):
    """Canon AI Vision v5.0 — Clean White 메인 윈도우"""
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Canon AI Vision  v5.0  —  AI 비전 관제 대시보드")
        self.resize(1400, 900)
        self.setMinimumSize(1100, 700)
        self.setStyleSheet(GLOBAL_STYLE)

        # 각 탭 임포트 (실패 시 Dummy로 대체)
        try:
            from gui.tab_monitor   import MonitorTab
            from gui.tab_training  import TrainingTab
            from gui.tab_report    import ReportTab
            from gui.tab_guide     import GuideTab
            from gui.tab_labeling  import LabelingTab
            from gui.tab_mobile    import MobileTab
        except ImportError as e:
            print(f"[경고] 탭 임포트 실패: {e}")
            class _Dummy(QWidget):
                def __init__(self, msg="준비 중"):
                    super().__init__()
                    l = QVBoxLayout(); lb = QLabel(msg)
                    lb.setAlignment(Qt.AlignCenter)
                    lb.setStyleSheet(f"font-size:18px; color:{C_TEXT_SUB};")
                    l.addWidget(lb); self.setLayout(l)
            MonitorTab   = lambda: _Dummy("🎥  실시간 관제 탭 준비 중...")
            TrainingTab  = lambda: _Dummy("🌙  야간 학습 탭 준비 중...")
            ReportTab    = lambda: _Dummy("📊  결재 대시보드 탭 준비 중...")
            GuideTab     = lambda: _Dummy("📖  파라미터 가이드 준비 중...")
            LabelingTab  = lambda: _Dummy("🏷  정답 라벨링 준비 중...")
            MobileTab    = lambda: _Dummy("📱  모바일 앱 탭 준비 중...")

        root = QWidget()
        self.setCentralWidget(root)
        root_layout = QVBoxLayout(root)
        root_layout.setContentsMargins(0, 0, 0, 0)
        root_layout.setSpacing(0)

        # 1. 상단 헤더
        self.header = HeaderBar()
        root_layout.addWidget(self.header)

        # 2. 탭 영역
        self.tabs = QTabWidget()
        self.tabs.setContentsMargins(10, 10, 10, 10)
        self.monitor_tab   = MonitorTab()
        self.training_tab  = TrainingTab()
        self.report_tab    = ReportTab()
        self.guide_tab     = GuideTab()
        self.labeling_tab  = LabelingTab()
        self.mobile_tab    = MobileTab()
        self.tabs.addTab(self.monitor_tab,   "  🎥  실시간 관제 (Track A)  ")
        self.tabs.addTab(self.training_tab,  "  🌙  야간 학습 지시 (Track B)  ")
        self.tabs.addTab(self.report_tab,    "  📊  아침 결재 대시보드  ")
        self.tabs.addTab(self.labeling_tab,  "  🏷  정답 라벨링  ")
        self.tabs.addTab(self.guide_tab,     "  📖  파라미터 가이드  ")
        self.tabs.addTab(self.mobile_tab,    "  📱  모바일 앱  ")
        root_layout.addWidget(self.tabs, stretch=1)

        # 파라미터 저장 시 실시간 관제 스레드에 즉시 반영
        if hasattr(self.guide_tab, 'params_saved') and hasattr(self.monitor_tab, 'on_params_reloaded'):
            self.guide_tab.params_saved.connect(self.monitor_tab.on_params_reloaded)

        # 3. 하단 E-STOP
        root_layout.addWidget(EStopBar())


if __name__ == "__main__":
    app = QApplication(sys.argv)
    app.setFont(QFont("Malgun Gothic", 11))
    win = AppStudioMainWindow()
    win.show()
    sys.exit(app.exec_())
