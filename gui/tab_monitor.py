"""
tab_monitor.py — TAB 1: 실시간 관제 (Track A)
서브탭 구성:
  [서브탭 A] 실시간 관제  : 영상 뷰어 / Latency 워터폴 / ORB BoxPlot
  [서브탭 B] 타겟 & ROI   : 타겟 이미지 + ORB 특징점 시각화 + 멀티 ROI 설정 및 저장
"""
import os, sys, time, json, cv2
import numpy as np
from collections import deque

from PyQt5.QtWidgets import (
    QWidget, QHBoxLayout, QVBoxLayout, QLabel, QFrame,
    QPushButton, QFileDialog, QSizePolicy, QListWidget,
    QAbstractItemView, QProgressBar, QScrollArea, QTabWidget,
    QMessageBox, QInputDialog, QRubberBand, QSlider
)
from PyQt5.QtCore import Qt, QThread, pyqtSignal, QTimer, QRect, QPoint, QSize
from PyQt5.QtGui import QImage, QPixmap, QPainter, QPen, QColor, QFont, QBrush

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

C_BG     = "#F8F9FA"; C_WHITE  = "#FFFFFF"; C_DARK   = "#2C3E50"
C_SUB    = "#7F8C8D"; C_BLUE   = "#3498DB"; C_GREEN  = "#27AE60"
C_RED    = "#E74C3C"; C_ORANGE = "#E67E22"; C_BORDER = "#E0E4E8"
C_YELLOW = "#F39C12"

MATCH_THRESHOLD    = 60
# NOTE: pending 저장은 MATCH_THRESHOLD 기준 ±margin(2~3점) 방식으로 동작합니다.
# (tab_monitor.py:1194 참고) PENDING_THRESHOLD는 더 이상 사용되지 않습니다.

# 설정 파일에서 임계값 덮어쓰기 (파일이 없으면 위 기본값 유지)
try:
    import json as _json
    _cfg_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                             "data", "params_config.json")
    if os.path.isfile(_cfg_path):
        with open(_cfg_path, "r", encoding="utf-8") as _f:
            _saved = _json.load(_f)
        MATCH_THRESHOLD = int(_saved.get("MATCH_THRESHOLD", MATCH_THRESHOLD))
except Exception:
    pass

ROI_SAVE_FILE  = os.path.join(_ROOT, "data", "roi_config.json")
MASK_SAVE_FILE = os.path.join(_ROOT, "data", "mask_config.json")
PARAMS_CONFIG_FILE = os.path.join(_ROOT, "data", "params_config.json")


def _load_params_config() -> dict:
    defaults = {
        "nfeatures": 700, "lowe_ratio": 0.75, "match_threshold": 25,
        "roi_match_threshold": 7, "clahe_clip_limit": 2.0, "clahe_tile_grid": 8,
        "MATCH_THRESHOLD": 60,
        "blur_ksize": 0, "gamma": 1.0, "sharpen_amount": 1.0,
    }
    if os.path.isfile(PARAMS_CONFIG_FILE):
        try:
            with open(PARAMS_CONFIG_FILE, "r", encoding="utf-8") as f:
                return {**defaults, **json.load(f)}
        except Exception:
            pass
    return defaults


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  공통 위젯
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
class VideoDisplayLabel(QLabel):
    def __init__(self):
        super().__init__()
        self.setAlignment(Qt.AlignCenter)
        self.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Ignored)
        self._pixmap = None
        self.setText("🎥  영상 파일을 선택하거나 카메라를 연결해주세요")
        self.setStyleSheet(f"background:#1a1a2e; color:{C_SUB}; font-size:14px; border-radius:6px;")

    def set_frame(self, px: QPixmap):
        self._pixmap = px; self.update()

    def paintEvent(self, e):
        super().paintEvent(e)
        if self._pixmap and not self._pixmap.isNull():
            p = QPainter(self)
            s = self._pixmap.scaled(self.size(), Qt.KeepAspectRatio, Qt.SmoothTransformation)
            p.drawPixmap((self.width()-s.width())//2, (self.height()-s.height())//2, s)


class LatencyBarWidget(QWidget):
    def __init__(self):
        super().__init__(); self.setFixedHeight(140)
        v = QVBoxLayout(self); v.setContentsMargins(0,0,0,0); v.setSpacing(6)
        v.addWidget(self._lbl("⏱️  구간별 분석 시간 (Latency)", bold=True))
        def _row(name, color):
            h = QHBoxLayout()
            l = QLabel(name); l.setFixedWidth(88)
            l.setStyleSheet(f"font-size:11px; color:{C_SUB};")
            b = QProgressBar(); b.setMaximum(100); b.setValue(0)
            b.setTextVisible(False); b.setFixedHeight(12)
            b.setStyleSheet(f"QProgressBar{{background:{C_BORDER};border-radius:4px;}}QProgressBar::chunk{{background:{color};border-radius:4px;}}")
            val = QLabel("0 ms"); val.setFixedWidth(52)
            val.setStyleSheet(f"font-size:11px; color:{C_DARK}; font-weight:bold;")
            h.addWidget(l); h.addWidget(b); h.addWidget(val)
            return h, b, val
        r1,self.b_yolo,self.v_yolo = _row("YOLO 추론", C_BLUE)
        r2,self.b_pre, self.v_pre  = _row("전처리 (최대5단계)", C_YELLOW)
        r3,self.b_ext, self.v_ext  = _row("ORB 고유추출", "#9B59B6")
        r4,self.b_cmp, self.v_cmp  = _row("타겟 병렬비교", C_ORANGE)
        v.addLayout(r1); v.addLayout(r2); v.addLayout(r3); v.addLayout(r4)

    def _lbl(self, t, bold=False):
        l = QLabel(t); l.setStyleSheet(f"font-size:12px; {'font-weight:bold;' if bold else ''} color:{C_DARK};")
        return l

    def update_latency(self, yolo, pre, ext, cmp):
        total = max(yolo+pre+ext+cmp, 1)
        self.b_yolo.setValue(int(yolo/total*100)); self.v_yolo.setText(f"{yolo:.1f} ms")
        self.b_pre.setValue(int(pre/total*100));   self.v_pre.setText(f"{pre:.1f} ms")
        self.b_ext.setValue(int(ext/total*100));   self.v_ext.setText(f"{ext:.1f} ms")
        self.b_cmp.setValue(int(cmp/total*100));   self.v_cmp.setText(f"{cmp:.1f} ms")

class PieChartWidget(QWidget):
    def __init__(self):
        super().__init__(); self.setFixedHeight(160)
        self._cum = [0.0, 0.0, 0.0, 0.0]
        self._vals = [0.0, 0.0, 0.0, 0.0]
        self._colors = [C_BLUE, C_YELLOW, "#9B59B6", C_ORANGE]
        self._names = ["YOLO", "Pre", "Extract", "Compare"]
        
    def reset_cumulative(self):
        self._cum = [0.0, 0.0, 0.0, 0.0]
        
    def update_pie(self, y, p, e, c):
        self._cum[0] += max(0.1, y)
        self._cum[1] += max(0.1, p)
        self._cum[2] += max(0.1, e)
        self._cum[3] += max(0.1, c)
        self._vals = self._cum.copy()
        self.update()

    def paintEvent(self, e):
        p = QPainter(self); p.setRenderHint(QPainter.Antialiasing)
        w, h = self.width(), self.height()
        p.setBrush(QBrush(QColor(C_WHITE))); p.setPen(QPen(QColor(C_BORDER)))
        p.drawRoundedRect(0, 0, w, h, 6, 6)
        tot = sum(self._vals)
        if tot <= 0: tot = 0.0001

        # 제목
        p.setPen(QPen(QColor(C_DARK))); p.setFont(QFont("Malgun Gothic", 10, QFont.Bold))
        p.drawText(QRect(0, 4, w, 20), Qt.AlignCenter, "비중 원그래프 (Pie Chart)")

        # 파이 차트 (중앙 상단)
        cx, cy, r = w // 2, 65, 38
        start = 0
        for i, val in enumerate(self._vals):
            span = int(-val / tot * 5760)
            p.setBrush(QBrush(QColor(self._colors[i]))); p.setPen(Qt.NoPen)
            p.drawPie(cx - r, cy - r, r * 2, r * 2, start, span)
            start += span

        # 범례 — 차트 아래 2열 배치 (잘림 방지)
        p.setFont(QFont("Malgun Gothic", 9))
        legend_y = cy + r + 8
        cols = [10, w // 2 + 5]
        for i, val in enumerate(self._vals):
            col = i % 2
            lx = cols[col]
            ly = legend_y + (i // 2) * 18
            p.setBrush(QBrush(QColor(self._colors[i])))
            p.setPen(Qt.NoPen)
            p.drawRoundedRect(lx, ly, 9, 9, 2, 2)
            p.setPen(QPen(QColor(C_DARK)))
            p.drawText(lx + 13, ly + 9, f"{self._names[i]}: {val/tot*100:.1f}%")


class BoxPlotWidget(QWidget):
    def __init__(self, title="ORB 점수 분포 (Box Plot)", max_val=100, is_time=False):
        super().__init__(); self.setFixedHeight(110); self._scores = deque(maxlen=200)
        self.title = title
        self.max_val = max_val
        self.is_time = is_time

    def add_score(self, s): self._scores.append(s); self.update()

    def paintEvent(self, e):
        p = QPainter(self); p.setRenderHint(QPainter.Antialiasing)
        w, h = self.width(), self.height()
        p.setBrush(QBrush(QColor(C_WHITE))); p.setPen(QPen(QColor(C_BORDER)))
        p.drawRoundedRect(0,0,w,h,6,6)
        if len(self._scores) < 5:
            p.setPen(QPen(QColor(C_SUB))); p.drawText(QRect(0,0,w,h), Qt.AlignCenter, "데이터 수집 중...")
            return
        arr = np.array(list(self._scores))
        q1,med,q3,mn,mx = (float(np.percentile(arr,v)) for v in [25,50,75,0,100])
        mx_scale = max(self.max_val, mx) if self.is_time else self.max_val
        pl,pr,pt,pb = 48,16,14,28
        pw = w-pl-pr
        def px(v): return pl+int((min(v, mx_scale)/mx_scale)*pw)
        cy = pt+(h-pt-pb)//2; bh=22
        p.setPen(QPen(QColor(C_DARK),1.5))
        for a,b in [(mn,q1),(q3,mx)]: p.drawLine(px(a),cy,px(b),cy)
        for v in [mn,mx]: p.drawLine(px(v),cy-7,px(v),cy+7)
        if self.is_time: bc = C_RED if med > 120 else C_GREEN
        else: bc = C_GREEN if med >= MATCH_THRESHOLD else C_RED
        p.setBrush(QBrush(QColor(bc).lighter(180))); p.setPen(QPen(QColor(bc),2))
        p.drawRect(px(q1),cy-bh//2,max(2,px(q3)-px(q1)),bh)
        p.setPen(QPen(QColor(bc),3)); p.drawLine(px(med),cy-bh//2,px(med),cy+bh//2)
        p.setPen(QPen(QColor(C_SUB))); p.setFont(QFont("Malgun Gothic",8))
        scales = [0, int(mx_scale*0.33), int(mx_scale*0.66), int(mx_scale)]
        for v in scales:
            p.drawText(QRect(px(v)-15,h-pb+4,30,14), Qt.AlignCenter, str(v) + ("ms" if self.is_time else ""))
        p.setFont(QFont("Malgun Gothic",10,QFont.Bold)); p.setPen(QPen(QColor(C_DARK)))
        p.drawText(QRect(0,0,w,16), Qt.AlignCenter, self.title)


class DualBoxPlotWidget(QWidget):
    """
    합격/불합격 ORB 점수 분포를 하나의 위젯 안에 두 행으로 나란히 표시합니다.
    같은 X축 스케일을 공유하므로 두 분포의 겹침/분리 정도를 직접 비교할 수 있습니다.
    """
    def __init__(self, title="ORB 매칭 점수 분포 비교", max_val=100):
        super().__init__()
        self.setFixedHeight(130)
        self.title   = title
        self.max_val = max_val
        self._ok   = deque(maxlen=300)
        self._fail = deque(maxlen=300)

    def add_score(self, s: float, is_ok: bool):
        (self._ok if is_ok else self._fail).append(s)
        self.update()

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        w, h = self.width(), self.height()

        p.setBrush(QBrush(QColor(C_WHITE)))
        p.setPen(QPen(QColor(C_BORDER)))
        p.drawRoundedRect(0, 0, w, h, 6, 6)

        p.setFont(QFont("Malgun Gothic", 10, QFont.Bold))
        p.setPen(QPen(QColor(C_DARK)))
        p.drawText(QRect(0, 2, w, 16), Qt.AlignCenter, self.title)

        pl, pr, pt, pb = 62, 16, 20, 22
        pw = w - pl - pr

        # 두 분포가 같은 X 스케일을 공유해야 비교가 의미있음
        all_mx = [float(self.max_val)]
        if self._ok:   all_mx.append(float(max(self._ok)))
        if self._fail: all_mx.append(float(max(self._fail)))
        mx_scale = max(all_mx)

        def pxs(v):
            return pl + int(min(max(v, 0), mx_scale) / mx_scale * pw)

        def draw_row(scores, cy, color, label):
            # 레이블
            p.setFont(QFont("Malgun Gothic", 9, QFont.Bold))
            p.setPen(QPen(QColor(color)))
            p.drawText(QRect(2, cy - 10, pl - 5, 20),
                       Qt.AlignRight | Qt.AlignVCenter, label)

            if len(scores) < 5:
                p.setFont(QFont("Malgun Gothic", 8))
                p.setPen(QPen(QColor(C_SUB)))
                p.drawText(QRect(pl + 4, cy - 8, pw, 16),
                           Qt.AlignLeft | Qt.AlignVCenter,
                           f"수집 중 ({len(scores)}개)")
                return

            arr = np.array(list(scores))
            mn  = float(np.min(arr))
            q1  = float(np.percentile(arr, 25))
            med = float(np.percentile(arr, 50))
            q3  = float(np.percentile(arr, 75))
            mx  = float(np.max(arr))
            bh  = 16

            # 수염
            p.setPen(QPen(QColor(C_DARK), 1.5))
            for a, b in [(mn, q1), (q3, mx)]:
                p.drawLine(pxs(a), cy, pxs(b), cy)
            for v in [mn, mx]:
                p.drawLine(pxs(v), cy - 5, pxs(v), cy + 5)

            # 박스체
            box_x = pxs(q1)
            box_w = max(2, pxs(q3) - pxs(q1))
            p.setBrush(QBrush(QColor(color).lighter(170)))
            p.setPen(QPen(QColor(color), 2))
            p.drawRect(box_x, cy - bh // 2, box_w, bh)

            # 중앙선
            p.setPen(QPen(QColor(color), 3))
            p.drawLine(pxs(med), cy - bh // 2, pxs(med), cy + bh // 2)

            # 중앙값 숫자
            p.setFont(QFont("Malgun Gothic", 7))
            p.setPen(QPen(QColor(color)))
            p.drawText(pxs(med) - 8, cy - bh // 2 - 2, f"{med:.0f}")

        row_h = (h - pt - pb) // 3
        draw_row(self._ok,   pt + row_h,       C_GREEN, "합격 ✅")
        draw_row(self._fail, pt + row_h * 2,   C_RED,   "불합격 ❌")

        # 공통 X축 눈금
        p.setFont(QFont("Malgun Gothic", 8))
        p.setPen(QPen(QColor(C_SUB)))
        for frac in [0, 0.25, 0.5, 0.75, 1.0]:
            v  = mx_scale * frac
            xp = pxs(v)
            p.drawText(QRect(xp - 12, h - pb + 2, 24, 14), Qt.AlignCenter, str(int(v)))


class CandlestickWidget(QWidget):
    """
    전체 분석 소요 시간을 주식 캔들스틱 방식으로 시각화합니다.
    - WINDOW_SIZE(100)개 샘플마다 캔들 1개 생성
    - 박스체: Q1(25%) ~ Q3(75%),  수염: min ~ max,  중앙선: 중앙값
    - 꺾은선: P10(하위 10%, 파랑) / P90(상위 10%, 빨강)
    - 새 캔들은 오른쪽에 추가되고 기존 캔들은 왼쪽으로 밀림
    - 현재 수집 중인 버퍼 진행률을 가장 오른쪽에 반투명으로 표시
    """
    MAX_CANDLES = 10
    WINDOW_SIZE = 100

    def __init__(self):
        super().__init__()
        self.setFixedHeight(190)
        self._buffer  = []
        self._candles = deque(maxlen=self.MAX_CANDLES)

    def add_value(self, v: float):
        self._buffer.append(v)
        if len(self._buffer) >= self.WINDOW_SIZE:
            arr = np.array(self._buffer)
            self._candles.append((
                float(np.min(arr)),
                float(np.percentile(arr, 10)),
                float(np.percentile(arr, 25)),
                float(np.percentile(arr, 50)),
                float(np.percentile(arr, 75)),
                float(np.percentile(arr, 90)),
                float(np.max(arr)),
            ))
            self._buffer = []
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        W, H = self.width(), self.height()

        # 배경
        painter.setBrush(QBrush(QColor(C_WHITE)))
        painter.setPen(QPen(QColor(C_BORDER)))
        painter.drawRoundedRect(0, 0, W, H, 6, 6)

        # 제목
        painter.setFont(QFont("Malgun Gothic", 10, QFont.Bold))
        painter.setPen(QPen(QColor(C_DARK)))
        painter.drawText(QRect(0, 3, W, 16), Qt.AlignCenter, "전체 분석 소요 시간 (캔들스틱)")

        candles  = list(self._candles)
        buf_len  = len(self._buffer)

        # 데이터 부족 시 진행 안내
        if not candles and buf_len < 5:
            painter.setFont(QFont("Malgun Gothic", 9))
            painter.setPen(QPen(QColor(C_SUB)))
            painter.drawText(QRect(0, 0, W, H), Qt.AlignCenter,
                             f"캔들 생성 중... ({buf_len} / {self.WINDOW_SIZE})")
            return

        # ── 차트 영역 ──────────────────────────────────────────
        PL, PR, PT, PB = 42, 10, 22, 32
        CW = W - PL - PR
        CH = H - PT - PB

        # Y축 범위 결정 — 데이터 분포 구간만 자동 확대 표시
        lows  = [c[1] for c in candles]   # P10 값들
        highs = [c[6] for c in candles]   # max 값들
        if buf_len >= 5:
            lows.append(float(np.percentile(self._buffer, 5)))
            highs.append(float(np.percentile(self._buffer, 95)))

        raw_min = min(lows)  if lows  else 0.0
        raw_max = max(highs) if highs else 150.0
        span    = max(10.0, raw_max - raw_min)

        # 여백 15% 추가 후 10ms 단위로 맞춤
        y_min = max(0.0, raw_min - span * 0.15)
        y_max = raw_max + span * 0.15
        y_min = float(int(y_min / 10) * 10)
        y_max = max(y_min + 10.0, float(int(y_max / 10 + 1) * 10))

        y_span = y_max - y_min

        def py(v):
            return PT + CH - int((min(max(v, y_min), y_max) - y_min) / y_span * CH)

        # Y축 격자 + 레이블
        painter.setFont(QFont("Malgun Gothic", 8))
        for frac in [0.0, 0.25, 0.5, 0.75, 1.0]:
            ms  = y_min + y_span * frac
            yy  = py(ms)
            painter.setPen(QPen(QColor(C_BORDER), 1, Qt.DotLine))
            painter.drawLine(PL, yy, W - PR, yy)
            painter.setPen(QPen(QColor(C_SUB)))
            painter.drawText(QRect(0, yy - 8, PL - 3, 16),
                             Qt.AlignRight | Qt.AlignVCenter, f"{int(ms)}")

        # ── 캔들 배치 ──────────────────────────────────────────
        # 슬롯 0 ~ MAX_CANDLES-1 = 완성된 캔들,  슬롯 MAX_CANDLES = 형성 중
        total_slots = self.MAX_CANDLES + 1
        slot_w  = CW / total_slots
        body_w  = max(4, int(slot_w * 0.52))

        def cx(slot):
            return PL + int((slot + 0.5) * slot_w)

        n = len(candles)
        start_slot = self.MAX_CANDLES - n   # 새 캔들이 오른쪽에 붙도록

        p10_pts, p90_pts = [], []

        for i, (mn, p10, q1, med, q3, p90, mx) in enumerate(candles):
            slot = start_slot + i
            x    = cx(slot)

            # 캔들 색상 (중앙값 기준)
            # 색상 기준: Y축 범위의 하위 1/3 = 녹, 중간 = 주황, 상위 = 빨강
            third = y_span / 3
            if   med <= y_min + third:         col = C_GREEN
            elif med <= y_min + third * 2:     col = C_ORANGE
            else:                               col = C_RED

            # 수염 (min ~ max)
            painter.setPen(QPen(QColor(C_DARK), 1))
            painter.drawLine(x, py(mn), x, py(mx))

            # 박스체 (Q1 ~ Q3)
            top = py(q3);  bot = py(q1)
            bh  = max(2, bot - top)
            painter.setBrush(QBrush(QColor(col).lighter(165)))
            painter.setPen(QPen(QColor(col), 1.5))
            painter.drawRect(x - body_w // 2, top, body_w, bh)

            # 중앙값 선
            painter.setPen(QPen(QColor(col), 2))
            painter.drawLine(x - body_w // 2, py(med), x + body_w // 2, py(med))

            p10_pts.append(QPoint(x, py(p10)))
            p90_pts.append(QPoint(x, py(p90)))

        # P10 꺾은선 (파랑 점선)
        if len(p10_pts) >= 2:
            painter.setPen(QPen(QColor(C_BLUE), 1.5, Qt.DashLine))
            for i in range(len(p10_pts) - 1):
                painter.drawLine(p10_pts[i], p10_pts[i + 1])

        # P90 꺾은선 (빨강 점선)
        if len(p90_pts) >= 2:
            painter.setPen(QPen(QColor(C_RED), 1.5, Qt.DashLine))
            for i in range(len(p90_pts) - 1):
                painter.drawLine(p90_pts[i], p90_pts[i + 1])

        # 형성 중인 캔들 (반투명 진행 바 + 현재 중앙값 점)
        if buf_len >= 5:
            x_cur    = cx(self.MAX_CANDLES)
            progress = buf_len / self.WINDOW_SIZE
            bar_h    = max(2, int(CH * progress))
            painter.setOpacity(0.22)
            painter.setBrush(QBrush(QColor(C_SUB)))
            painter.setPen(Qt.NoPen)
            painter.drawRect(x_cur - body_w // 2, PT + CH - bar_h, body_w, bar_h)
            painter.setOpacity(1.0)
            cur_med = float(np.median(self._buffer))
            painter.setPen(QPen(QColor(C_SUB), 2))
            painter.drawEllipse(QPoint(x_cur, py(cur_med)), 3, 3)
            # 진행률 텍스트
            painter.setFont(QFont("Malgun Gothic", 7))
            painter.drawText(QRect(x_cur - 14, H - PB + 2, 28, 12),
                             Qt.AlignCenter, f"{buf_len}")

        # ── 범례 ───────────────────────────────────────────────
        legend = [
            (C_GREEN,  "rect", "≤50ms"),
            (C_ORANGE, "rect", "≤100ms"),
            (C_RED,    "rect", ">100ms"),
            (C_BLUE,   "line", "P10"),
            (C_RED,    "line", "P90"),
        ]
        painter.setFont(QFont("Malgun Gothic", 7))
        lx = PL
        ly = H - PB + 16
        for color, shape, label in legend:
            qc = QColor(color)
            if shape == "rect":
                painter.setBrush(QBrush(qc.lighter(165)))
                painter.setPen(QPen(qc, 1))
                painter.drawRect(lx, ly, 8, 8)
            else:
                painter.setPen(QPen(qc, 1.5, Qt.DashLine))
                painter.drawLine(lx, ly + 4, lx + 8, ly + 4)
            painter.setPen(QPen(QColor(C_SUB)))
            painter.drawText(lx + 10, ly + 8, label)
            lx += 46


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  타겟 이미지 + ORB 시각화 + 멀티 ROI 편집기
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
class ORBViewer(QLabel):
    """
    타겟 이미지를 표시하고 그 위에 ORB 특징점(빨간 점)을 그립니다.
    마우스 드래그로 ROI 박스(파란 점선) 또는 마스크 박스(빨간 실선)를 추가할 수 있습니다.
    """
    roi_added  = pyqtSignal(QRect)
    mask_added = pyqtSignal(QRect)

    def __init__(self):
        super().__init__()
        self.setAlignment(Qt.AlignCenter)
        self.setMinimumSize(400, 300)
        self.setStyleSheet("background:#1a1a2e; border-radius:6px;")
        self._base_pixmap = None
        self._kp_list     = []   # QPointF 좌표 리스트
        self._rois        = []   # QRectF 리스트 (정규화)
        self._masks       = []   # QRectF 리스트 (정규화)
        self._mask_mode   = False
        self._origin      = QPoint()
        self._rubber      = QRubberBand(QRubberBand.Rectangle, self)

    def set_mask_mode(self, enabled: bool):
        """True=마스크 모드, False=ROI 모드"""
        self._mask_mode = enabled
        self.setStyleSheet(
            "background:#2a0a0a; border-radius:6px; border:2px solid #E74C3C;" if enabled
            else "background:#1a1a2e; border-radius:6px;"
        )

    def set_masks(self, masks: list):
        self._masks = masks
        self.update()

    def load_image(self, img_path: str):
        """이미지를 읽고 전처리 후 ORB 특징점을 계산 후 표시"""
        try:
            img_buf = np.fromfile(img_path, dtype=np.uint8)
            img_color = cv2.imdecode(img_buf, cv2.IMREAD_COLOR)
        except Exception as ex:
            print(f"[ORBViewer] 이미지 로드 실패: {img_path} ({ex})")
            return
        if img_color is None:
            return

        # ⚠️ 실제 파이프라인과 동일하게 전처리 계층 적용 (설정값 반영)
        try:
            from engine.preprocessor import ImagePreprocessor
            _cfg = _load_params_config()
            _tile = int(_cfg.get("clahe_tile_grid", 8))
            pre_ready = ImagePreprocessor(
                clahe_clip_limit=float(_cfg.get("clahe_clip_limit", 2.0)),
                clahe_tile_grid=(_tile, _tile),
                blur_ksize=int(_cfg.get("blur_ksize", 0)),
                gamma=float(_cfg.get("gamma", 1.0)),
                sharpen_amount=float(_cfg.get("sharpen_amount", 1.0)),
            ).preprocess_for_orb(img_color)
        except Exception as ex:
            print(f"전처리 모듈 로드 실패, 흑백 변환으로 대체: {ex}")
            pre_ready = cv2.cvtColor(img_color, cv2.COLOR_BGR2GRAY)

        orb   = cv2.ORB_create(nfeatures=700)
        kp, _ = orb.detectAndCompute(pre_ready, None)
        
        # BGR → RGB 변환
        img_rgb = cv2.cvtColor(img_color, cv2.COLOR_BGR2RGB)
        h, w = img_rgb.shape[:2]
        self._kp_list = [(p.pt[0]/w, p.pt[1]/h) for p in kp]
        
        qimg = QImage(img_rgb.tobytes(), w, h, 3*w, QImage.Format_RGB888)
        self._base_pixmap = QPixmap.fromImage(qimg)
        self.repaint()


    def set_rois(self, rois: list):
        """외부에서 ROI 목록(정규화 QRectF 리스트)을 받아 표시"""
        self._rois = rois; self.update()

    def paintEvent(self, e):
        super().paintEvent(e)
        # ⚠️ 핵심 수정: QPixmap 객체는 bool() 판단이 안 되므로 is None 으로 체크해야 함
        if self._base_pixmap is None or self._base_pixmap.isNull(): return
        p = QPainter(self); p.setRenderHint(QPainter.Antialiasing)
        # 이미지 표시
        scaled = self._base_pixmap.scaled(self.size(), Qt.KeepAspectRatio, Qt.SmoothTransformation)
        ox = (self.width()-scaled.width())//2
        oy = (self.height()-scaled.height())//2
        p.drawPixmap(ox, oy, scaled)
        sw, sh = scaled.width(), scaled.height()
        # ORB 특징점 (빨간 점)
        p.setPen(Qt.NoPen)
        p.setBrush(QBrush(QColor(C_RED)))
        for nx, ny in self._kp_list:
            x = int(ox + nx*sw); y = int(oy + ny*sh)
            p.drawEllipse(x-3, y-3, 6, 6)
        # ROI 박스 (파란 점선)
        p.setPen(QPen(QColor(C_BLUE), 2, Qt.DashLine))
        p.setBrush(QBrush(QColor(C_BLUE+"33")))
        for i, r in enumerate(self._rois):
            rx = int(ox + r.x()*sw); ry = int(oy + r.y()*sh)
            rw = int(r.width()*sw);  rh = int(r.height()*sh)
            p.drawRect(rx, ry, rw, rh)
            p.setPen(QPen(QColor(C_BLUE))); p.setFont(QFont("Malgun Gothic", 10, QFont.Bold))
            p.drawText(rx+4, ry+14, f"ROI-{i+1}")
            p.setPen(QPen(QColor(C_BLUE), 2, Qt.DashLine))
        # 마스크 박스 (빨간 반투명)
        p.setPen(QPen(QColor(C_RED), 2, Qt.SolidLine))
        p.setBrush(QBrush(QColor(C_RED+"55")))
        for i, r in enumerate(self._masks):
            rx = int(ox + r.x()*sw); ry = int(oy + r.y()*sh)
            rw = int(r.width()*sw);  rh = int(r.height()*sh)
            p.drawRect(rx, ry, rw, rh)
            p.setPen(QPen(QColor(C_RED))); p.setFont(QFont("Malgun Gothic", 10, QFont.Bold))
            p.drawText(rx+4, ry+14, f"MASK-{i+1}")
            p.setPen(QPen(QColor(C_RED), 2, Qt.SolidLine))

    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton:
            self._origin = e.pos()
            self._rubber.setGeometry(QRect(self._origin, QSize()))
            self._rubber.show()

    def mouseMoveEvent(self, e):
        if not self._rubber.isHidden():
            self._rubber.setGeometry(QRect(self._origin, e.pos()).normalized())

    def mouseReleaseEvent(self, e):
        if e.button() == Qt.LeftButton:
            self._rubber.hide()
            rect = QRect(self._origin, e.pos()).normalized()
            if rect.width() > 10 and rect.height() > 10:
                if self._mask_mode:
                    self.mask_added.emit(rect)
                else:
                    self.roi_added.emit(rect)


class TargetROITab(QWidget):
    """서브탭 B: 타겟 이미지 ORB 시각화 + 멀티 ROI 설정 + 마스킹"""
    def __init__(self):
        super().__init__()
        self.setStyleSheet(f"background:{C_BG};")
        self._cur_fname  = ""
        self._roi_dict   = self._load_saved_rois()   # { "1.png": [QRectF, ...], ... }
        self._rois       = []
        self._mask_dict  = self._load_saved_masks()  # { "1.png": [QRectF, ...], ... }
        self._masks      = []

        main = QHBoxLayout(self)
        main.setContentsMargins(12,12,12,12); main.setSpacing(12)

        # 좌측: 타겟 이미지 선택 목록
        left = QWidget(); left.setFixedWidth(200)
        left.setStyleSheet(f"background:{C_WHITE}; border:1px solid {C_BORDER}; border-radius:8px;")
        lv = QVBoxLayout(left); lv.setContentsMargins(10,10,10,10); lv.setSpacing(8)
        lv.addWidget(self._lbl("🖼️  타겟 이미지 목록", bold=True))
        self.img_list = QListWidget()
        self.img_list.setStyleSheet(f"""QListWidget{{background:{C_BG};border:1px solid {C_BORDER};border-radius:4px;}}
            QListWidget::item:selected{{background:#EBF5FB;color:{C_BLUE};}}""")
        lv.addWidget(self.img_list)
        btn_refresh = QPushButton("🔄 새로고침")
        btn_refresh.clicked.connect(self._load_target_list)
        lv.addWidget(btn_refresh)
        main.addWidget(left)

        # 중앙: ORB 시각화 뷰어
        center = QWidget()
        center.setStyleSheet(f"background:{C_WHITE}; border:1px solid {C_BORDER}; border-radius:8px;")
        cv2_layout = QVBoxLayout(center); cv2_layout.setContentsMargins(8,8,8,8); cv2_layout.setSpacing(6)
        cv2_layout.addWidget(self._lbl("🔴 ORB 특징점  |  파란 점선 = ROI  |  빨간 박스 = 마스크", bold=False))
        self.orb_viewer = ORBViewer()
        cv2_layout.addWidget(self.orb_viewer, stretch=1)
        main.addWidget(center, stretch=1)

        # 우측: ROI & 마스킹 관리 패널
        right = QWidget(); right.setFixedWidth(250)
        right.setStyleSheet(f"background:{C_WHITE}; border:1px solid {C_BORDER}; border-radius:8px;")
        rv = QVBoxLayout(right); rv.setContentsMargins(10,10,10,10); rv.setSpacing(6)

        # ── 모드 토글 버튼 ─────────────────────────────────────
        self._mode_btn = QPushButton("✏  ROI 모드  (클릭 → 마스크 모드)")
        self._mode_btn.setCheckable(True)
        self._mode_btn.setStyleSheet(
            f"QPushButton{{background:{C_BLUE}11;color:{C_BLUE};border:1px solid {C_BLUE}55;"
            f"border-radius:5px;font-size:11px;padding:4px;}}"
            f"QPushButton:checked{{background:{C_RED}22;color:{C_RED};border:1px solid {C_RED}88;}}")
        self._mode_btn.toggled.connect(self._on_mode_toggle)
        rv.addWidget(self._mode_btn)

        sep1 = QFrame(); sep1.setFrameShape(QFrame.HLine)
        sep1.setStyleSheet(f"color:{C_BORDER};"); rv.addWidget(sep1)

        # ── ROI 섹션 ───────────────────────────────────────────
        rv.addWidget(self._lbl("🎯  ROI 설정 관리", bold=True))
        self.roi_list = QListWidget()
        self.roi_list.setDragDropMode(QAbstractItemView.InternalMove)
        self.roi_list.setFixedHeight(88)
        self.roi_list.setStyleSheet(
            f"QListWidget{{background:{C_BG};border:1px solid {C_BORDER};border-radius:4px;font-size:10px;}}"
            f"QListWidget::item:selected{{background:#EBF5FB;color:{C_BLUE};}}")
        rv.addWidget(self.roi_list)

        btn_del = QPushButton("🗑  선택 ROI 삭제"); btn_del.clicked.connect(self._del_roi)
        btn_clr = QPushButton("🗑  전체 초기화");  btn_clr.clicked.connect(self._clear_roi)
        btn_sav = QPushButton("💾  ROI 저장 (핫리로드)")
        btn_sav.setStyleSheet(f"background:{C_GREEN};color:white;border:none;border-radius:6px;font-weight:bold;font-size:11px;")
        btn_sav.clicked.connect(self._save_roi)
        rv.addWidget(btn_del); rv.addWidget(btn_clr); rv.addWidget(btn_sav)

        sep2 = QFrame(); sep2.setFrameShape(QFrame.HLine)
        sep2.setStyleSheet(f"color:{C_BORDER};"); rv.addWidget(sep2)

        # ── 마스킹 섹션 ────────────────────────────────────────
        rv.addWidget(self._lbl("🔴  마스크 영역 (동적 콘텐츠 제외)", bold=True))
        self.mask_list = QListWidget()
        self.mask_list.setFixedHeight(72)
        self.mask_list.setStyleSheet(
            f"QListWidget{{background:{C_BG};border:1px solid {C_RED}44;border-radius:4px;font-size:10px;}}"
            f"QListWidget::item:selected{{background:#FDEDEC;color:{C_RED};}}")
        rv.addWidget(self.mask_list)

        btn_del_m = QPushButton("🗑  선택 마스크 삭제"); btn_del_m.clicked.connect(self._del_mask)
        btn_clr_m = QPushButton("🗑  마스크 전체 초기화"); btn_clr_m.clicked.connect(self._clear_mask)
        btn_sav_m = QPushButton("💾  마스크 저장")
        btn_sav_m.setStyleSheet(f"background:{C_RED};color:white;border:none;border-radius:6px;font-weight:bold;font-size:11px;")
        btn_sav_m.clicked.connect(self._save_mask)
        rv.addWidget(btn_del_m); rv.addWidget(btn_clr_m); rv.addWidget(btn_sav_m)

        rv.addWidget(self._lbl("ℹ️  드래그로 영역 추가\n(시계·카운터 등 변하는 구역)", bold=False))
        rv.addStretch()
        main.addWidget(right)

        # currentItemChanged: 클릭 + 키보드 모두 처리
        self.img_list.currentItemChanged.connect(
            lambda cur, _: self._load_image(cur.text() if cur else "")
        )
        self.orb_viewer.roi_added.connect(self._on_roi_drawn)
        self.orb_viewer.mask_added.connect(self._on_mask_drawn)
        self._load_target_list()  # 뷰어(orb_viewer) 초기화 이후에 실행해야 함

    def _lbl(self, t, bold=False):
        l = QLabel(t); l.setWordWrap(True)
        l.setStyleSheet(f"font-size:12px;{'font-weight:bold;' if bold else ''}color:{C_DARK};")
        return l


    def _load_target_list(self):
        self.img_list.clear()
        # 타겟 이미지 경로: data/targets
        td = os.path.join(_ROOT, "data", "targets")
        print(f"[TargetROITab] 타겟 경로: {td} | 존재: {os.path.isdir(td)}")
        if not os.path.isdir(td):
            print(f"[TargetROITab] ⚠️ 경로 없음: {td}")
            return
        files = [f for f in sorted(os.listdir(td))
                 if f.lower().endswith((".png", ".jpg", ".jpeg"))]
        print(f"[TargetROITab] 발견된 이미지 {len(files)}개: {files}")
        for f in files:
            self.img_list.addItem(f)
        if self.img_list.count() > 0:
            self.img_list.setCurrentRow(0)
            first = self.img_list.item(0)
            if first:
                self._load_image(first.text())

    def _load_image(self, fname):
        if not fname: return
        path = os.path.join(_ROOT, "data", "targets", fname)
        self._cur_fname = fname
        self._rois  = self._roi_dict.get(fname, []).copy()
        self._masks = self._mask_dict.get(fname, []).copy()

        # ROI 리스트 동기화
        self.roi_list.clear()
        for i, r in enumerate(self._rois):
            self.roi_list.addItem(f"ROI-{i+1}: ({r.x():.2f},{r.y():.2f}) {r.width():.2f}×{r.height():.2f}")

        # 마스크 리스트 동기화
        self.mask_list.clear()
        for i, m in enumerate(self._masks):
            self.mask_list.addItem(f"MASK-{i+1}: ({m.x():.2f},{m.y():.2f}) {m.width():.2f}×{m.height():.2f}")

        self.orb_viewer.load_image(path)
        self.orb_viewer.set_rois(self._rois)
        self.orb_viewer.set_masks(self._masks)

    def _on_roi_drawn(self, rect: QRect):
        """뷰어에서 드래그된 픽셀 좌표를 정규화 비율로 변환 후 저장"""
        vw = self.orb_viewer.width(); vh = self.orb_viewer.height()
        if not self.orb_viewer._base_pixmap: return
        scaled = self.orb_viewer._base_pixmap.scaled(
            self.orb_viewer.size(), Qt.KeepAspectRatio, Qt.SmoothTransformation)
        ox = (vw-scaled.width())//2; oy = (vh-scaled.height())//2
        sw = scaled.width(); sh = scaled.height()
        nx = max(0, (rect.x()-ox)/sw); ny = max(0, (rect.y()-oy)/sh)
        nw = min(rect.width()/sw, 1-nx); nh = min(rect.height()/sh, 1-ny)
        # 정규화 QRect 사용 (0~1 범위)
        import PyQt5.QtCore as _qc
        nr = _qc.QRectF(nx, ny, nw, nh)
        self._rois.append(nr)
        if self._cur_fname: self._roi_dict[self._cur_fname] = self._rois
        self.roi_list.addItem(f"ROI-{len(self._rois)}: ({nx:.2f},{ny:.2f}) {nw:.2f}×{nh:.2f}")
        self.orb_viewer.set_rois(self._rois)

    def _del_roi(self):
        row = self.roi_list.currentRow()
        if row >= 0:
            self._rois.pop(row); self.roi_list.takeItem(row)
            if self._cur_fname: self._roi_dict[self._cur_fname] = self._rois
            self.orb_viewer.set_rois(self._rois)

    def _clear_roi(self):
        self._rois.clear(); self.roi_list.clear()
        if self._cur_fname: self._roi_dict[self._cur_fname] = self._rois
        self.orb_viewer.set_rois(self._rois)

    def _save_roi(self):
        os.makedirs(os.path.dirname(ROI_SAVE_FILE), exist_ok=True)
        # 딕셔너리 형태로 모두 저장
        saved_dict = {}
        for fname, rois in self._roi_dict.items():
            saved_dict[fname] = [{"x": r.x(), "y": r.y(), "w": r.width(), "h": r.height()} for r in rois]
            
        with open(ROI_SAVE_FILE, "w") as f: json.dump(saved_dict, f, indent=2)
        QMessageBox.information(self, "저장 완료", f"모든 타겟 이미지의 다중 ROI가 성공적으로 저장되었습니다.\n{ROI_SAVE_FILE}")

    def _load_saved_rois(self):
        roi_dict_loaded = {}
        if not os.path.exists(ROI_SAVE_FILE): return roi_dict_loaded
        try:
            import PyQt5.QtCore as _qc
            with open(ROI_SAVE_FILE) as f: data = json.load(f)
            # 이전 버전 배열 형태 (하위 호환)
            if isinstance(data, list):
                roi_dict_loaded["1.png"] = [_qc.QRectF(d["x"],d["y"],d["w"],d["h"]) for d in data]
            elif isinstance(data, dict):
                for fname, rlist in data.items():
                    roi_dict_loaded[fname] = [_qc.QRectF(d["x"],d["y"],d["w"],d["h"]) for d in rlist]
        except: pass
        return roi_dict_loaded

    # ── 마스크 관련 메서드 ────────────────────────────────────────────────────

    def _on_mode_toggle(self, checked: bool):
        self.orb_viewer.set_mask_mode(checked)
        self._mode_btn.setText(
            "🔴  마스크 모드  (클릭 → ROI 모드)" if checked
            else "✏  ROI 모드  (클릭 → 마스크 모드)"
        )

    def _on_mask_drawn(self, rect: QRect):
        """뷰어에서 드래그된 픽셀 좌표를 정규화 비율로 변환 후 마스크에 저장"""
        vw = self.orb_viewer.width(); vh = self.orb_viewer.height()
        if not self.orb_viewer._base_pixmap: return
        scaled = self.orb_viewer._base_pixmap.scaled(
            self.orb_viewer.size(), Qt.KeepAspectRatio, Qt.SmoothTransformation)
        ox = (vw - scaled.width()) // 2; oy = (vh - scaled.height()) // 2
        sw = scaled.width(); sh = scaled.height()
        nx = max(0.0, (rect.x() - ox) / sw); ny = max(0.0, (rect.y() - oy) / sh)
        nw = min(rect.width() / sw, 1.0 - nx); nh = min(rect.height() / sh, 1.0 - ny)
        import PyQt5.QtCore as _qc
        nm = _qc.QRectF(nx, ny, nw, nh)
        self._masks.append(nm)
        if self._cur_fname: self._mask_dict[self._cur_fname] = self._masks
        self.mask_list.addItem(f"MASK-{len(self._masks)}: ({nx:.2f},{ny:.2f}) {nw:.2f}×{nh:.2f}")
        self.orb_viewer.set_masks(self._masks)

    def _del_mask(self):
        row = self.mask_list.currentRow()
        if row >= 0:
            self._masks.pop(row); self.mask_list.takeItem(row)
            if self._cur_fname: self._mask_dict[self._cur_fname] = self._masks
            self.orb_viewer.set_masks(self._masks)

    def _clear_mask(self):
        self._masks.clear(); self.mask_list.clear()
        if self._cur_fname: self._mask_dict[self._cur_fname] = self._masks
        self.orb_viewer.set_masks(self._masks)

    def _save_mask(self):
        os.makedirs(os.path.dirname(MASK_SAVE_FILE), exist_ok=True)
        saved = {}
        for fname, masks in self._mask_dict.items():
            saved[fname] = [{"x": m.x(), "y": m.y(), "w": m.width(), "h": m.height()} for m in masks]
        with open(MASK_SAVE_FILE, "w", encoding="utf-8") as f:
            json.dump(saved, f, indent=2)
        QMessageBox.information(self, "저장 완료",
            f"마스크 설정이 저장되었습니다.\n영상 재시작 시 자동 적용됩니다.\n{MASK_SAVE_FILE}")

    def _load_saved_masks(self):
        mask_dict_loaded = {}
        if not os.path.exists(MASK_SAVE_FILE): return mask_dict_loaded
        try:
            import PyQt5.QtCore as _qc
            with open(MASK_SAVE_FILE, encoding="utf-8") as f:
                data = json.load(f)
            for fname, mlist in data.items():
                mask_dict_loaded[fname] = [_qc.QRectF(d["x"], d["y"], d["w"], d["h"]) for d in mlist]
        except: pass
        return mask_dict_loaded


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  YOLO + ORB 연결된 실제 VideoThread
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
class VideoThread(QThread):
    frame_signal   = pyqtSignal(QImage)
    # fps, yolo_ms, pre_ms, orb_ext_ms, orb_cmp_ms, score, ok, roi_passed, roi_total, target_id
    status_signal  = pyqtSignal(float, float, float, float, float, int, bool, int, int, str)
    capture_signal = pyqtSignal(str)
    progress_signal = pyqtSignal(int, int)  # 현재프레임, 총프레임

    def __init__(self, source):
        super().__init__()
        self.source  = source
        self.running = True
        self._paused = False
        self._seek_frame = -1
        self.pending_dir = os.path.join(_ROOT, "data", "pending")
        self.matched_dir = os.path.join(_ROOT, "data", "matched")
        os.makedirs(self.pending_dir, exist_ok=True)
        os.makedirs(self.matched_dir, exist_ok=True)

        self.detector     = None
        self.preprocessor = None
        self.matcher      = None
        self.skipper      = None
        self.targets      = {}
        self.skip_enabled = True
        self._last_is_ok  = False                  # 합격 전환 감지용
        self._fps_ts      = deque(maxlen=7)        # FPS 계산용 최근 7프레임 타임스탬프
        self.diag_enabled = False
        self.use_clahe    = True
        self._diag_logger = None
        self._last_crop          = None   # 최근 YOLO 크롭 프레임 (BGR 640×360)
        self._last_best_target_id = ''    # 최근 매칭된 타겟 ID (스킵 프레임에서도 유지)
        self._last_display_frame  = None  # ROI 오버레이가 그려진 최근 분석 프레임
        self.show_crop            = False # True: YOLO 크롭 뷰, False: 풀프레임 뷰

    def pause_resume(self):
        self._paused = not self._paused

    def set_frame(self, frame_idx):
        self._seek_frame = frame_idx

    def run(self):
        _cfg = _load_params_config()
        try:
            from engine.preprocessor  import ImagePreprocessor
            from engine.matcher       import ScreenMatcher
            from engine.frame_skipper import FrameSkipper
            tile = int(_cfg["clahe_tile_grid"])
            self.preprocessor = ImagePreprocessor(
                clahe_clip_limit=float(_cfg["clahe_clip_limit"]),
                clahe_tile_grid=(tile, tile),
                blur_ksize=int(_cfg.get("blur_ksize", 0)),
                gamma=float(_cfg.get("gamma", 1.0)),
                sharpen_amount=float(_cfg.get("sharpen_amount", 1.0)),
            )
            self.matcher = ScreenMatcher(
                orb_nfeatures=int(_cfg["nfeatures"]),
                lowe_ratio=float(_cfg["lowe_ratio"]),
                match_threshold=int(_cfg["match_threshold"]),
            )
            self.skipper = FrameSkipper(skip_frames=2)
        except Exception as e:
            print(f"[VideoThread] 전처리/매처 초기화 실패: {e}")

        try:
            from engine.detector import BezelDetector
            active_file = os.path.join(_ROOT, "data", "active_model.json")
            model_path = None
            if os.path.exists(active_file):
                try:
                    import json as _json
                    with open(active_file) as af:
                        model_path = os.path.join(_ROOT, _json.load(af).get("path", ""))
                except Exception:
                    pass
            # 추론용 모델 폴백: best.pt(학습된 세그, 원근보정 정확) 우선
            # 사용자가 다각형 라벨링 → 세그 학습한 모델이 사다리꼴 꼭짓점을 정확히 잡음
            if not model_path or not os.path.exists(model_path):
                seg_best  = os.path.join(_ROOT, "models", "canon_fast_yolo", "weights", "best.pt")
                det_model = os.path.join(_ROOT, "yolov8n.pt")
                if os.path.exists(seg_best):
                    model_path = seg_best
                elif os.path.exists(det_model):
                    model_path = det_model
            self.detector = BezelDetector(model_path=model_path)
            print(f"[VideoThread] YOLO 로드 완료: {os.path.basename(model_path)}")
        except Exception as e:
            err_msg = f"[VideoThread] YOLO 초기화 실패 (ORB 단독 폴백 모드): {e}"
            print(err_msg)
            try:
                with open(os.path.join(_ROOT, "yolo_error.log"), "w", encoding="utf-8") as f:
                    import traceback
                    f.write(err_msg + "\n" + traceback.format_exc())
            except: pass

        # YOLO 초기화 이후 타겟 로드 — detector를 넘겨 타겟 이미지에도 YOLO 크롭 적용
        if self.matcher:
            try:
                td = os.path.join(_ROOT, "data", "targets")
                mask_cfg = os.path.join(_ROOT, "data", "mask_config.json")
                self.targets = self.matcher.load_targets_from_dir(
                    td, ROI_SAVE_FILE, detector=self.detector,
                    mask_config_path=mask_cfg)
            except Exception as e:
                print(f"[VideoThread] 타겟 로드 실패: {e}")

        from engine.matcher import RESIZE_W, RESIZE_H
        ROI_MATCH_THRESHOLD = int(_cfg["roi_match_threshold"])

        cap = cv2.VideoCapture(self.source)
        if not cap.isOpened(): return
        fps_orig   = cap.get(cv2.CAP_PROP_FPS) or 30.0
        tot_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
        if tot_frames <= 0: tot_frames = 1
        idx = 0

        while self.running:
            if self._seek_frame >= 0:
                cap.set(cv2.CAP_PROP_POS_FRAMES, self._seek_frame)
                self._seek_frame = -1

            if self._paused:
                self.msleep(50)
                continue

            ret, frame = cap.read()
            if not ret:
                cap.set(cv2.CAP_PROP_POS_FRAMES, 0); continue

            cur_frame = int(cap.get(cv2.CAP_PROP_POS_FRAMES))
            self.progress_signal.emit(cur_frame, tot_frames)

            score = 0; is_ok = False; roi_passed = 0; roi_total = 0
            yolo_ms = pre_ms = orb_ext_ms = orb_cmp_ms = 0.0

            if self.preprocessor and self.targets:
                should_proc = True
                if self.skip_enabled and self.skipper:
                    should_proc = self.skipper.should_process()

                active_bbox = None

                if should_proc:
                    # ① YOLO — 베젤 찾기 (bbox는 원본 좌표 유지, UI 박스 그리기용)
                    analysis_frame = cv2.resize(frame, (RESIZE_W, RESIZE_H))
                    if self.detector:
                        t0 = time.perf_counter()
                        cropped, bbox = self.detector.detect_and_crop(frame)
                        yolo_ms = (time.perf_counter()-t0)*1000
                        if cropped is not None and cropped.size > 0:
                            analysis_frame = cv2.resize(cropped, (RESIZE_W, RESIZE_H))
                            active_bbox = bbox

                    # YOLO 크롭 프레임 저장 (타겟 저장용)
                    self._last_crop = analysis_frame.copy()

                    # ② 전처리 (640×360 정규화된 이미지 기준)
                    t1 = time.perf_counter()
                    if self.use_clahe:
                        orb_ready = self.preprocessor.preprocess_for_orb(analysis_frame)
                    else:
                        orb_ready = cv2.cvtColor(analysis_frame, cv2.COLOR_BGR2GRAY)
                    # 마스크 합집합 적용 (동적 영역 제거)
                    _union_masks = getattr(self.matcher, 'union_masks', [])
                    if _union_masks:
                        orb_ready = self.preprocessor.apply_masks(orb_ready, _union_masks)
                    pre_ms = (time.perf_counter()-t1)*1000

                    # ③-A ORB 고유 추출: 실시간 프레임의 ROI 영역별 특징점 미리 추출
                    t2 = time.perf_counter()
                    live_roi_features = {}   # (x1,y1,x2,y2) → des (중복 좌표 재사용)
                    full_des_cache = None    # fallback용 전체 이미지 특징점 캐시
                    for _, target_data in self.targets.items():
                        if target_data.get('n_rois', 0) == 0:
                            if full_des_cache is None:
                                _, full_des_cache = self.matcher.get_features(orb_ready)
                        else:
                            for (_, rx1, ry1, rx2, ry2) in target_data['rois']:
                                key = (rx1, ry1, rx2, ry2)
                                if key not in live_roi_features:
                                    roi_crop = orb_ready[ry1:ry2, rx1:rx2]
                                    if roi_crop.size > 0:
                                        _, q_des = self.matcher.get_features(roi_crop)
                                        live_roi_features[key] = q_des
                                    else:
                                        live_roi_features[key] = None
                    orb_ext_ms = (time.perf_counter()-t2)*1000

                    # ③-B 타겟 병렬비교: 추출된 특징점 vs 타겟 ROI 디스크립터 (멀티스레딩 최적화)
                    t3 = time.perf_counter()
                    best_score = 0; best_passed = 0; best_total = 0; best_ok = False
                    best_target_id = ''
                    frame_roi_detail = []   # 진단 DB 기록용

                    if not hasattr(self, '_target_executor'):
                        import concurrent.futures
                        self._target_executor = concurrent.futures.ThreadPoolExecutor(max_workers=4)

                    def _compare_single_target(target_item):
                        tid, t_data = target_item
                        rois   = t_data.get('rois', [])
                        n_rois = t_data.get('n_rois', 0)
                        
                        l_detail = []
                        if n_rois == 0:
                            # ROI 미설정 → 전체 이미지 fallback
                            s, p = self.matcher.compare_descriptors(full_des_cache, t_data.get('full'))
                            return tid, 1, s, (1 if p else 0), p, l_detail

                        l_passed = 0; max_s = 0
                        for roi_idx, (t_des, rx1, ry1, rx2, ry2) in enumerate(rois):
                            q_des = live_roi_features.get((rx1, ry1, rx2, ry2))
                            s, p = self.matcher.compare_descriptors(
                                q_des, t_des, threshold=ROI_MATCH_THRESHOLD)
                            if s > max_s: max_s = s
                            if p: l_passed += 1
                            l_detail.append((tid, roi_idx, rx1, ry1, rx2, ry2, s, p))

                        required  = n_rois if n_rois <= 2 else n_rois - 1
                        tok = l_passed >= required
                        return tid, n_rois, max_s, l_passed, tok, l_detail

                    if self.targets:
                        import concurrent.futures
                        futures = [self._target_executor.submit(_compare_single_target, item) 
                                   for item in self.targets.items()]
                        for future in concurrent.futures.as_completed(futures):
                            tid, n_tot, cur_max_s, passed, tok, l_detail = future.result()
                            frame_roi_detail.extend(l_detail)
                            if tok or cur_max_s > best_score:
                                best_score  = cur_max_s
                                best_passed = passed
                                best_total  = n_tot
                                best_ok     = tok
                                best_target_id = tid

                    score      = best_score
                    roi_passed = best_passed
                    roi_total  = best_total
                    is_ok      = best_ok
                    orb_cmp_ms = (time.perf_counter()-t3)*1000
                    self._last_best_target_id = best_target_id

                    # ③-D ROI 오버레이 — analysis_frame에 직접 그리기
                    # 합격 ROI: 초록 박스, 불합격 ROI: 빨간 박스 + 스코어 텍스트
                    for (tid, roi_idx, rx1, ry1, rx2, ry2, s, p) in frame_roi_detail:
                        if tid != best_target_id:
                            continue
                        color = (0, 200, 0) if p else (0, 60, 220)
                        cv2.rectangle(analysis_frame, (rx1, ry1), (rx2, ry2), color, 2)
                        cv2.putText(analysis_frame, f"R{roi_idx} {s}",
                                    (rx1 + 3, ry1 + 15),
                                    cv2.FONT_HERSHEY_SIMPLEX, 0.42, color, 1, cv2.LINE_AA)
                    # 상단 배너: "Target N  Y/Z"
                    if best_target_id:
                        tag_text  = f"Target {best_target_id}  {roi_passed}/{roi_total}"
                        tag_color = (0, 200, 0) if is_ok else (0, 60, 220)
                    else:
                        tag_text  = "No Match"
                        tag_color = (100, 100, 100)
                    cv2.rectangle(analysis_frame, (0, 0), (210, 26), (0, 0, 0), -1)
                    cv2.putText(analysis_frame, tag_text, (4, 18),
                                cv2.FONT_HERSHEY_SIMPLEX, 0.58, tag_color, 1, cv2.LINE_AA)
                    self._last_display_frame = analysis_frame.copy()

                    # ③-C 진단 DB 기록
                    if self.diag_enabled and self._diag_logger:
                        self._diag_logger.log(
                            frame_idx=idx,
                            preprocessing='clahe' if self.use_clahe else 'raw',
                            yolo_detected=(active_bbox is not None),
                            yolo_w=analysis_frame.shape[1],
                            yolo_h=analysis_frame.shape[0],
                            best_target=best_target_id,
                            best_score=best_score,
                            roi_passed=roi_passed, roi_total=roi_total,
                            is_ok=is_ok,
                            roi_detail=frame_roi_detail)

                    # ④ 합격 전환 시점에만 matched 캡처
                    if is_ok and not self._last_is_ok:
                        from datetime import datetime as _dt
                        _ts = _dt.now().strftime("%Y%m%d_%H%M%S_%f")[:18]
                        mfname = f"matched_{_ts}_{roi_passed}of{roi_total}.jpg"
                        cv2.imwrite(os.path.join(self.matched_dir, mfname), frame)
                        self.capture_signal.emit(mfname)

                    # ⑤ 애매한 점수대(Hard Mining) → pending 캡처
                    # 의미 없는(너무 낮은) 점수는 버리고, 커트라인 근접 데이터만 수집
                    margin = 2 if roi_total > 0 else 3
                    target_thr = ROI_MATCH_THRESHOLD if roi_total > 0 else MATCH_THRESHOLD

                    if abs(score - target_thr) <= margin:
                        from datetime import datetime as _dt
                        _ts = _dt.now().strftime("%Y%m%d_%H%M%S_%f")[:18]
                        pfname = f"pending_{_ts}_s{score:02d}.jpg"
                        cv2.imwrite(os.path.join(self.pending_dir, pfname), frame)

                    if self.skipper:
                        self.skipper.update_zombie_memory(
                            (score, "ORB", is_ok, yolo_ms, pre_ms,
                             orb_ext_ms, orb_cmp_ms, active_bbox,
                             roi_passed, roi_total))

                else:
                    # 스킵 프레임 — 좀비 메모리 사용
                    if self.skipper:
                        z = self.skipper.get_zombie_result()
                        if z and len(z) == 10:
                            score, _, is_ok, yolo_ms, pre_ms, orb_ext_ms, orb_cmp_ms, active_bbox, roi_passed, roi_total = z
                        elif z and len(z) == 8:
                            score, _, is_ok, yolo_ms, pre_ms, orb_ext_ms, orb_cmp_ms, active_bbox = z
                            roi_passed, roi_total = 0, 0
                        elif z and len(z) == 7:
                            score, _, is_ok, yolo_ms, pre_ms, orb_ms, active_bbox = z
                            orb_ext_ms, orb_cmp_ms = orb_ms, 0.0
                            roi_passed, roi_total = 0, 0

                self._last_is_ok = is_ok

                if active_bbox:
                    x1, y1, x2, y2 = active_bbox
                    corners = self.detector.last_corners if self.detector else None
                    if corners is not None:
                        # 사다리꼴 폴리곤 오버레이 (원근 보정 성공)
                        pts = corners.astype(np.int32).reshape((-1, 1, 2))
                        cv2.polylines(frame, [pts], True, (0, 255, 0), 3)
                        lx = int(corners[0][0])
                        ly = max(0, int(corners[0][1]) - 10)
                    else:
                        # 폴백: 직사각형 (원근 보정 불가)
                        cv2.rectangle(frame, (x1, y1), (x2, y2), (0, 200, 255), 3)
                        lx, ly = x1, max(0, y1 - 10)
                    label = "Canon Monitor" + (" [원근보정]" if corners is not None else " [bbox]")
                    cv2.putText(frame, label, (lx, ly),
                                cv2.FONT_HERSHEY_SIMPLEX, 0.65, (0, 255, 0), 2)

            # 풀프레임 뷰 OR YOLO 크롭 뷰 선택 (버튼 토글)
            if self.show_crop and self._last_display_frame is not None:
                disp = self._last_display_frame  # YOLO 크롭 + ROI 오버레이 뷰
            else:
                disp = frame                     # 원본 풀프레임 뷰
            rgb      = cv2.cvtColor(disp, cv2.COLOR_BGR2RGB)
            h, w, _  = rgb.shape
            self.frame_signal.emit(QImage(rgb.data, w, h, 3*w, QImage.Format_RGB888).copy())

            curr_t = time.perf_counter()
            self._fps_ts.append(curr_t)
            if len(self._fps_ts) >= 2:
                fps_real = (len(self._fps_ts) - 1) / max(self._fps_ts[-1] - self._fps_ts[0], 1e-6)
            else:
                fps_real = 0.0

            self.status_signal.emit(
                fps_real, yolo_ms, pre_ms, orb_ext_ms, orb_cmp_ms,
                score, is_ok, roi_passed, roi_total, self._last_best_target_id)
            idx += 1
            # msleep 제거 — QThread는 UI와 독립 스레드이므로 sleep 불필요.
            # Windows에서 msleep(1)이 실제로 ~15ms를 소모해 FPS를 12로 제한했던 원인 제거.

        cap.release()

    def stop(self): self.running=False; self.wait()

    def set_skip_enabled(self, enabled: bool):
        """스킵 ON/OFF 토글 — 런닝 중에도 즉시 적용"""
        self.skip_enabled = enabled
        if self.skipper:
            self.skipper.frame_count = 0   # 카운터 리셋으로 다음 프레임이 바로 처리됨

    def set_diag(self, enabled: bool):
        """진단 모드 ON/OFF — DB 기록 시작/중단"""
        self.diag_enabled = enabled
        if enabled and self._diag_logger is None:
            try:
                from engine.diagnostic_logger import DiagnosticLogger
                self._diag_logger = DiagnosticLogger()
            except Exception as ex:
                print(f"[VideoThread] DiagnosticLogger 초기화 실패: {ex}")
        print(f"[VideoThread] 진단 모드 {'ON' if enabled else 'OFF'}")

    def set_clahe(self, enabled: bool):
        """CLAHE 전처리 ON/OFF — 런닝 중 즉시 적용"""
        self.use_clahe = enabled
        print(f"[VideoThread] CLAHE {'ON' if enabled else 'OFF'}")

    def set_show_crop(self, enabled: bool):
        """뷰 모드 전환 — True: YOLO 크롭 뷰, False: 풀프레임 뷰 (런닝 중 즉시 적용)"""
        self.show_crop = enabled
        print(f"[VideoThread] 뷰 모드: {'크롭 뷰' if enabled else '풀프레임 뷰'}")

    def reload_targets(self):
        """타겟 이미지 교체 후 ORB 특징점을 즉시 재로드"""
        if self.matcher is None:
            return
        try:
            td = os.path.join(_ROOT, "data", "targets")
            self.targets = self.matcher.load_targets_from_dir(
                td, ROI_SAVE_FILE, detector=self.detector)
            print(f"[VideoThread] 타겟 재로드 완료: {list(self.targets.keys())}")
        except Exception as ex:
            print(f"[VideoThread] 타겟 재로드 실패: {ex}")


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  우측 KPI + Latency + BoxPlot 패널
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
class StatsPanel(QWidget):
    def __init__(self):
        super().__init__()
        self.setFixedWidth(400)
        self.setStyleSheet(f"background:{C_WHITE}; border-left:1px solid {C_BORDER};")
        
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(0,0,0,0)
        
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setStyleSheet("QScrollArea { border: none; }")
        
        content = QWidget()
        v = QVBoxLayout(content); v.setContentsMargins(14,14,14,14); v.setSpacing(12)

        def sep():
            f=QFrame(); f.setFrameShape(QFrame.HLine)
            f.setStyleSheet(f"color:{C_BORDER}; max-height:1px;"); return f

        # KPI 카드 — 1행: FPS / ORB점수 / 판정 / YOLO
        row = QHBoxLayout()
        self.fps_card   = self._card("FPS", "--")
        self.score_card = self._card("ORB 점수", "--")
        self.verdict    = self._card("판정", "대기")
        self.yolo_card  = self._card("YOLO", "미연결")
        row.addWidget(self.fps_card[0]); row.addWidget(self.score_card[0])
        row.addWidget(self.verdict[0]);  row.addWidget(self.yolo_card[0])
        v.addLayout(row)

        # KPI 카드 — 2행: ROI 매칭 카운트
        row2 = QHBoxLayout()
        self.roi_card = self._card("ROI 매칭", "--")
        row2.addWidget(self.roi_card[0])
        row2.addStretch()
        v.addLayout(row2)
        v.addWidget(sep())

        # 총 지연 시간
        self.lbl_total = QLabel("Total: 0 ms")
        self.lbl_total.setStyleSheet(f"font-size:15px; font-weight:bold; color:{C_DARK}; text-align:center;")
        self.lbl_total.setAlignment(Qt.AlignCenter)
        v.addWidget(self.lbl_total)

        # 1. 막대 그래프 (Latency)
        self.latency = LatencyBarWidget()
        v.addWidget(self.latency); v.addWidget(sep())

        # 2. 파이 차트 (Pie)
        self.pie = PieChartWidget()
        v.addWidget(self.pie); v.addWidget(sep())

        # 3. 전체 시간 캔들스틱 차트
        self.candle_chart = CandlestickWidget()
        v.addWidget(self.candle_chart); v.addWidget(sep())

        # 4. ORB 점수 박스 플롯 — 합격/불합격 동일 차트 비교
        self.boxplot = DualBoxPlotWidget()
        v.addWidget(self.boxplot); v.addWidget(sep())

        self.lbl_capture = QLabel("📁  최근 캡처: 없음")
        self.lbl_capture.setWordWrap(True)
        self.lbl_capture.setStyleSheet(f"font-size:10px; color:{C_SUB};")
        v.addWidget(self.lbl_capture)
        v.addStretch()
        
        scroll.setWidget(content)
        main_layout.addWidget(scroll)

    def _card(self, title, val):
        c = QWidget(); c.setStyleSheet(f"background:{C_BG}; border-radius:8px;")
        cv = QVBoxLayout(c); cv.setContentsMargins(6,6,6,6)
        t = QLabel(title); t.setAlignment(Qt.AlignCenter)
        t.setStyleSheet(f"font-size:9px; color:{C_SUB}; font-weight:bold;")
        v = QLabel(val);   v.setAlignment(Qt.AlignCenter)
        v.setStyleSheet(f"font-size:16px; font-weight:bold; color:{C_DARK};")
        cv.addWidget(t); cv.addWidget(v)
        return c, v

    def update_stats(self, fps, yolo_ms, pre_ms, orb_ext_ms, orb_cmp_ms,
                     score, is_ok, roi_passed=0, roi_total=0, target_id=''):
        total = yolo_ms + pre_ms + orb_ext_ms + orb_cmp_ms
        col = C_GREEN if is_ok else C_RED

        self.fps_card[1].setText(f"{fps:.0f}")

        # ORB 점수 카드 (기존 단일 숫자)
        self.score_card[1].setText(str(score))
        self.score_card[1].setStyleSheet(f"font-size:16px; font-weight:bold; color:{col};")

        # 판정 카드 — 매칭된 타겟 번호 표시
        if is_ok and target_id:
            verdict_text = f"✅ 타겟 {target_id}"
        elif is_ok:
            verdict_text = "✅ 정상"
        else:
            verdict_text = "❌ 에러"
        self.verdict[1].setText(verdict_text)
        self.verdict[1].setStyleSheet(f"font-size:13px; font-weight:bold; color:{col};")

        # YOLO 카드
        yolo_state = f"{yolo_ms:.0f}ms" if yolo_ms > 0 else "폴백"
        self.yolo_card[1].setText(yolo_state)
        self.yolo_card[1].setStyleSheet(
            f"font-size:13px; font-weight:bold; color:{C_BLUE if yolo_ms>0 else C_ORANGE};")

        # ROI 매칭 카드 (신규: X/Y 합격 카운트)
        if roi_total > 0:
            roi_text = f"{roi_passed}/{roi_total}"
            roi_suffix = " ✅" if is_ok else " ❌"
            self.roi_card[1].setText(roi_text + roi_suffix)
            self.roi_card[1].setStyleSheet(f"font-size:14px; font-weight:bold; color:{col};")
        else:
            self.roi_card[1].setText("--")
            self.roi_card[1].setStyleSheet(f"font-size:14px; font-weight:bold; color:{C_SUB};")

        self.lbl_total.setText(f"Total: {total:.1f} ms")
        self.latency.update_latency(yolo_ms, pre_ms, orb_ext_ms, orb_cmp_ms)
        self.pie.update_pie(yolo_ms, pre_ms, orb_ext_ms, orb_cmp_ms)
        self.candle_chart.add_value(total)
        self.boxplot.add_score(score, is_ok)

    def update_capture(self, name):
        self.lbl_capture.setText(f"📁  최근 캡처: {name}")


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  실시간 관제 서브탭 A
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
class LiveMonitorSubTab(QWidget):
    def __init__(self):
        super().__init__()
        self.setStyleSheet(f"background:{C_BG};")
        self._thread = None
        v = QVBoxLayout(self); v.setContentsMargins(8,8,8,8); v.setSpacing(6)
        v.addWidget(self._build_ctrl())
        
        body = QHBoxLayout(); body.setSpacing(0)
        
        # 좌측 영상 + 슬라이더 수직 배치
        left_body = QVBoxLayout(); left_body.setContentsMargins(0,0,0,0); left_body.setSpacing(8)
        self.video = VideoDisplayLabel()
        left_body.addWidget(self.video, stretch=1)
        
        # 타임라인 슬라이더
        self.slider = QSlider(Qt.Horizontal)
        self.slider.setRange(0, 100)
        self.slider.setEnabled(False)
        self.slider.sliderMoved.connect(self._on_seek)
        self.slider.setStyleSheet(f"""
            QSlider::groove:horizontal {{ border:1px solid {C_BORDER}; background:{C_WHITE}; height:6px; border-radius:3px; }}
            QSlider::sub-page:horizontal {{ background:{C_BLUE}; border:1px solid #2980B9; height:6px; border-radius:3px; }}
            QSlider::handle:horizontal {{ background:{C_DARK}; border:1px solid #1A252F; width:14px; margin-top:-4px; margin-bottom:-4px; border-radius:5px; }}
        """)
        left_body.addWidget(self.slider)
        
        body.addLayout(left_body, stretch=1)
        
        self.stats = StatsPanel()
        body.addWidget(self.stats)
        v.addLayout(body, stretch=1)

    def _build_ctrl(self):
        # ── 공통 버튼 스타일 헬퍼 ─────────────────────────────────────
        def _btn(text, color=None, checkable=False, checked=False):
            b = QPushButton(text)
            b.setMinimumWidth(72)
            b.setFixedHeight(28)
            b.setCheckable(checkable)
            b.setChecked(checked)
            if checkable:
                b.setStyleSheet(
                    f"QPushButton{{background:{C_BG};color:{C_DARK};"
                    f"border:1px solid {C_BORDER};border-radius:5px;"
                    f"font-size:12px;font-weight:500;padding:0 8px;}}"
                    f"QPushButton:checked{{background:{color if color else C_BLUE};"
                    f"color:white;border:none;border-radius:5px;"
                    f"font-size:12px;font-weight:bold;padding:0 8px;}}"
                )
            else:
                bg = color if color else C_BG
                fg = 'white' if color else C_DARK
                bd = 'none' if color else f'1px solid {C_BORDER}'
                b.setStyleSheet(
                    f"QPushButton{{background:{bg};color:{fg};"
                    f"border:{bd};border-radius:5px;"
                    f"font-size:12px;font-weight:bold;padding:0 8px;}}"
                )
            return b

        def _sep():
            f = QFrame(); f.setFrameShape(QFrame.VLine)
            f.setStyleSheet(f"color:{C_BORDER}; max-width:1px; margin:2px 2px;")
            return f

        # ── 외부 컨테이너 (2줄) ──────────────────────────────────────
        bar = QWidget()
        bar.setFixedHeight(86)
        bar.setStyleSheet(
            f"background:{C_WHITE}; border:1px solid {C_BORDER}; border-radius:8px;"
        )
        vbox = QVBoxLayout(bar)
        vbox.setContentsMargins(10, 5, 10, 5)
        vbox.setSpacing(4)

        # ══ 상단 줄: 제목 | 소스 선택 | 재생 제어 | 상태 ══
        top = QHBoxLayout(); top.setSpacing(6)

        lbl_title = QLabel("실시간 관제")
        lbl_title.setStyleSheet(
            f"font-size:13px; font-weight:bold; color:{C_DARK}; padding-right:2px;"
        )
        top.addWidget(lbl_title)
        top.addWidget(_sep())

        btn_file = _btn("파일 열기", color=C_BLUE)
        btn_file.clicked.connect(self._open_file)
        btn_cam  = _btn("카메라 (0번)", color=C_BLUE)
        btn_cam.clicked.connect(self._open_cam)
        top.addWidget(btn_file)
        top.addWidget(btn_cam)
        top.addWidget(_sep())

        self.btn_pause = _btn("일시 정지", color=C_ORANGE)
        self.btn_pause.clicked.connect(self._toggle_pause)
        self.btn_pause.setEnabled(False)
        btn_stop = _btn("종료", color=C_RED)
        btn_stop.clicked.connect(self._stop)
        top.addWidget(self.btn_pause)
        top.addWidget(btn_stop)
        top.addStretch()

        self.lbl_st = QLabel("● 대기")
        self.lbl_st.setStyleSheet(
            f"font-size:12px; font-weight:bold; color:{C_SUB};"
        )
        top.addWidget(self.lbl_st)

        # ══ 하단 줄: 옵션 토글 | 데이터 액션 ══
        bot = QHBoxLayout(); bot.setSpacing(6)

        self.btn_skip = _btn("스킵 ON", color=C_BLUE, checkable=True, checked=True)
        self.btn_skip.clicked.connect(self._toggle_skip)

        self.btn_clahe = _btn("CLAHE", color=C_YELLOW, checkable=True, checked=True)
        self.btn_clahe.clicked.connect(self._toggle_clahe)

        self.btn_crop_view = _btn("크롭 뷰", color="#E67E22", checkable=True, checked=False)
        self.btn_crop_view.clicked.connect(self._toggle_crop_view)

        self.btn_diag = _btn("진단", color="#8E44AD", checkable=True, checked=False)
        self.btn_diag.clicked.connect(self._toggle_diag)

        bot.addWidget(self.btn_skip)
        bot.addWidget(self.btn_clahe)
        bot.addWidget(self.btn_crop_view)
        bot.addWidget(self.btn_diag)
        bot.addWidget(_sep())

        btn_db_clr = _btn("DB 초기화")
        btn_db_clr.clicked.connect(self._clear_diag_db)

        btn_gt_cap = _btn("GT 캡처", color="#9B59B6")
        btn_gt_cap.clicked.connect(self._capture_for_gt)

        btn_save_target = _btn("타겟 저장", color=C_GREEN)
        btn_save_target.clicked.connect(self._save_as_target)

        bot.addWidget(btn_db_clr)
        bot.addWidget(btn_gt_cap)
        bot.addWidget(btn_save_target)
        bot.addStretch()

        vbox.addLayout(top)
        vbox.addLayout(bot)


        return bar




    def _toggle_skip(self):
        checked = self.btn_skip.isChecked()
        if self._thread: self._thread.set_skip_enabled(checked)
        self.btn_skip.setText("스킵 ON" if checked else "스킵 OFF")

    def _toggle_diag(self):
        checked = self.btn_diag.isChecked()
        self.btn_diag.setText("진단 ON" if checked else "진단")
        if self._thread:
            self._thread.set_diag(checked)

    def _toggle_clahe(self):
        checked = self.btn_clahe.isChecked()
        self.btn_clahe.setText("CLAHE ON" if checked else "CLAHE OFF")
        if self._thread:
            self._thread.set_clahe(checked)

    def _toggle_crop_view(self):
        """✂ 크롭 뷰 토글 — YOLO 크롭 분석 화면과 원본 풀프레임 화면을 전환"""
        checked = self.btn_crop_view.isChecked()
        self.btn_crop_view.setText("크롭 ON" if checked else "크롭 뷰")
        if self._thread:
            self._thread.set_show_crop(checked)

    def _clear_diag_db(self):
        if self._thread and self._thread._diag_logger:
            self._thread._diag_logger.clear()
        else:
            try:
                from engine.diagnostic_logger import DiagnosticLogger
                DiagnosticLogger().clear()
            except Exception as ex:
                print(f"[LiveMonitor] DB 초기화 실패: {ex}")

    def _capture_for_gt(self):
        """현재 YOLO 크롭 프레임을 data/capture/ 에 저장 (정답 라벨링 탭에서 사용)"""
        if not self._thread or self._thread._last_crop is None:
            QMessageBox.warning(self, "캡처 실패",
                                "영상을 재생 중이어야 하며 YOLO가 베젤을 감지해야 합니다.")
            return
        from datetime import datetime
        ts = datetime.now().strftime("%Y%m%d_%H%M%S_%f")[:18]
        capture_dir = os.path.join(_ROOT, "data", "capture")
        os.makedirs(capture_dir, exist_ok=True)
        fname = f"capture_{ts}.png"
        save_path = os.path.join(capture_dir, fname)
        try:
            ret, buf = cv2.imencode(".png", self._thread._last_crop)
            if ret:
                buf.tofile(save_path)
                self.lbl_st.setText(f"● 캡처: {fname}")
            else:
                QMessageBox.critical(self, "캡처 실패", "이미지 인코딩 실패")
        except Exception as ex:
            QMessageBox.critical(self, "캡처 실패", str(ex))

    def _save_as_target(self):
        """현재 YOLO 크롭 프레임을 타겟 이미지(1~4.png)로 저장하고 즉시 재로드"""
        if not self._thread or self._thread._last_crop is None:
            QMessageBox.warning(self, "저장 실패",
                                "영상을 재생 중이어야 하며\nYOLO가 베젤을 감지해야 저장할 수 있습니다.")
            return

        nums = ["1", "2", "3", "4"]
        n, ok = QInputDialog.getItem(
            self, "타겟 번호 선택",
            "이 프레임을 어떤 타겟으로 저장할까요?\n(기존 파일이 덮어쓰기됩니다)",
            nums, 0, False)
        if not ok:
            return

        td = os.path.join(_ROOT, "data", "targets")
        os.makedirs(td, exist_ok=True)
        save_path = os.path.join(td, f"{n}.png")

        crop = self._thread._last_crop  # BGR 640×360
        ret, buf = cv2.imencode(".png", crop)
        if not ret:
            QMessageBox.critical(self, "저장 실패", "이미지 인코딩에 실패했습니다.")
            return

        buf.tofile(save_path)
        self._thread.reload_targets()
        QMessageBox.information(
            self, "저장 완료",
            f"타겟 {n}.png 저장 완료!\n\n"
            f"경로: {save_path}\n\n"
            f"저장된 이미지는 YOLO 크롭 기준(640×360)이므로\n"
            f"ROI 편집기에서 ROI를 새로 그려주세요.")

    def _toggle_pause(self):
        if self._thread:
            self._thread.pause_resume()
            if self._thread._paused:
                self.btn_pause.setText("▶ 계속 재생")
                self.btn_pause.setStyleSheet(f"background:{C_GREEN};color:white;border:none;border-radius:6px;font-weight:bold;")
                self.lbl_st.setText("● 일시 정지됨")
                self.lbl_st.setStyleSheet(f"font-size:12px; font-weight:bold; color:{C_ORANGE}; padding-left:6px;")
            else:
                self.btn_pause.setText("⏸ 일시 정지")
                self.btn_pause.setStyleSheet(f"background:{C_ORANGE};color:white;border:none;border-radius:6px;font-weight:bold;")
                self.lbl_st.setText("● 분석 중...")
                self.lbl_st.setStyleSheet(f"font-size:12px; font-weight:bold; color:{C_BLUE}; padding-left:6px;")

    def _open_file(self):
        p,_ = QFileDialog.getOpenFileName(self,"영상 파일",_ROOT,"Video (*.mp4 *.avi *.mov *.mkv)")
        if p: self._start(p)

    def _open_cam(self): self._start(0)

    def _on_seek(self, value):
        if self._thread:
            self._thread.set_frame(value)

    def _start(self, src):
        self._stop()
        self.stats.pie.reset_cumulative()
        self._thread = VideoThread(src)
        self._thread.use_clahe    = self.btn_clahe.isChecked()
        self._thread.diag_enabled = self.btn_diag.isChecked()
        if self._thread.diag_enabled:
            try:
                from engine.diagnostic_logger import DiagnosticLogger
                self._thread._diag_logger = DiagnosticLogger()
            except Exception as ex:
                print(f"[LiveMonitor] DiagnosticLogger 초기화 실패: {ex}")
        self._thread.frame_signal.connect(lambda qi: self.video.set_frame(QPixmap.fromImage(qi)))
        self._thread.status_signal.connect(self._on_status)
        self._thread.progress_signal.connect(self._on_progress)
        self._thread.capture_signal.connect(lambda n: self.stats.update_capture(n))
        self._thread.start()
        
        self.btn_pause.setEnabled(True)
        self.btn_pause.setText("⏸ 일시 정지")
        self.btn_pause.setStyleSheet(f"background:{C_ORANGE};color:white;border:none;border-radius:6px;font-weight:bold;")
        self.slider.setEnabled(True)
        self.lbl_st.setText("● 분석 중...")
        self.lbl_st.setStyleSheet(f"font-size:12px; font-weight:bold; color:{C_BLUE}; padding-left:6px;")

    def _stop(self):
        if self._thread and self._thread.isRunning(): self._thread.stop()
        self._thread = None
        self.btn_pause.setEnabled(False)
        self.slider.setEnabled(False)
        self.slider.setValue(0)
        self.lbl_st.setText("● 정지")
        self.lbl_st.setStyleSheet(f"font-size:12px; font-weight:bold; color:{C_SUB}; padding-left:6px;")

    def _on_progress(self, cur, tot):
        if not self.slider.isSliderDown():
            if self.slider.maximum() != tot:
                self.slider.setRange(0, tot)
            self.slider.blockSignals(True)
            self.slider.setValue(cur)
            self.slider.blockSignals(False)

    def _on_status(self, fps, yolo_ms, pre_ms, orb_ext_ms, orb_cmp_ms,
                   score, is_ok, roi_passed, roi_total, target_id=''):
        self.stats.update_stats(fps, yolo_ms, pre_ms, orb_ext_ms, orb_cmp_ms,
                                score, is_ok, roi_passed, roi_total, target_id)
        if not (self._thread and getattr(self._thread, '_paused', False)):
            col = C_GREEN if is_ok else C_RED
            if is_ok and target_id:
                st_text = f"● 타겟 {target_id} ✅"
            elif is_ok:
                st_text = "● 정상 ✅"
            else:
                st_text = "● 에러! ❌"
            self.lbl_st.setText(st_text)
            self.lbl_st.setStyleSheet(f"font-size:12px; font-weight:bold; color:{col}; padding-left:6px;")

    def closeEvent(self, e): self._stop(); super().closeEvent(e)



# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  샴 네트워크 VideoThread
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
class SiameseVideoThread(QThread):
    """
    샴 네트워크(ResNet18 임베딩 + 코사인 유사도)로 화면을 분류하는 스레드.

    use_yolo=True  → YOLO로 모니터 영역 크롭 후 임베딩 (정확도 ↑)
    use_yolo=False → 풀프레임을 그대로 임베딩 (YOLO 없이도 작동 확인)
    """
    # QImage 프레임
    frame_signal  = pyqtSignal(QImage)
    # fps, yolo_ms, embed_ms, sim_ms, similarity(0~100), is_ok, target_id
    status_signal = pyqtSignal(float, float, float, float, float, bool, str)
    progress_signal = pyqtSignal(int, int)

    def __init__(self, source, use_yolo: bool = True):
        super().__init__()
        self.source   = source
        self.use_yolo = use_yolo
        self.running  = True
        self._paused  = False
        self._seek_frame = -1
        self._fps_ts  = deque(maxlen=7)

        # 합격 임계값: siamese_classifier.py의 cosine_threshold(기본 0.75)로 위임됨.
        # 이 값은 GUI에서 슬라이더로 변경할 경우 classifier.cosine_threshold에 반영.
        self.sim_threshold = 0.75

        self.detector   = None
        self.classifier = None
        self._last_crop = None

    # ── 외부 제어 ──────────────────────────────────────────
    def pause_resume(self):
        self._paused = not self._paused

    def set_frame(self, frame_idx: int):
        self._seek_frame = frame_idx

    def stop(self):
        self.running = False
        self.wait()

    def set_use_yolo(self, enabled: bool):
        self.use_yolo = enabled

    def set_sim_threshold(self, val: float):
        self.sim_threshold = val
        # classifier가 초기화된 상태라면 cosine_threshold도 동기화
        if self.classifier is not None:
            self.classifier.cosine_threshold = val

    # ── 메인 루프 ──────────────────────────────────────────
    def run(self):
        # 1. 샴 분류기 초기화
        try:
            from offline.siamese_classifier import SiameseClassifier
            self.classifier = SiameseClassifier()
            print("[SiameseThread] 샴 분류기 초기화 완료")
        except Exception as e:
            print(f"[SiameseThread] 샴 분류기 초기화 실패: {e}")
            return

        # 2. YOLO 탐지기 초기화 (use_yolo=True 일 때만)
        if self.use_yolo:
            try:
                from engine.detector import BezelDetector
                active_file = os.path.join(_ROOT, "data", "active_model.json")
                model_path  = None
                if os.path.exists(active_file):
                    try:
                        import json as _j
                        with open(active_file) as af:
                            model_path = os.path.join(_ROOT, _j.load(af).get("path", ""))
                    except Exception:
                        pass
                if not model_path or not os.path.exists(model_path):
                    seg_best  = os.path.join(_ROOT, "models", "canon_fast_yolo", "weights", "best.pt")
                    det_model = os.path.join(_ROOT, "yolov8n.pt")
                    model_path = seg_best if os.path.exists(seg_best) else (
                        det_model if os.path.exists(det_model) else None)
                if model_path:
                    self.detector = BezelDetector(model_path=model_path)
                    print(f"[SiameseThread] YOLO 로드 완료: {os.path.basename(model_path)}")
                else:
                    print("[SiameseThread] ⚠️  YOLO 모델 파일 없음 → 폴백: 풀프레임")
            except Exception as e:
                print(f"[SiameseThread] YOLO 초기화 실패 → 풀프레임 폴백: {e}")

        # 3. 영상 열기
        from PIL import Image as PILImage
        cap = cv2.VideoCapture(self.source)
        if not cap.isOpened():
            return

        tot_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
        if tot_frames <= 0:
            tot_frames = 1
        idx = 0

        while self.running:
            if self._seek_frame >= 0:
                cap.set(cv2.CAP_PROP_POS_FRAMES, self._seek_frame)
                self._seek_frame = -1

            if self._paused:
                self.msleep(50)
                continue

            ret, frame = cap.read()
            if not ret:
                cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
                continue

            cur_frame = int(cap.get(cv2.CAP_PROP_POS_FRAMES))
            self.progress_signal.emit(cur_frame, tot_frames)

            yolo_ms = embed_ms = sim_ms = 0.0
            similarity = 0.0
            is_ok = False
            target_id = ""
            display_frame = frame.copy()
            active_bbox = None

            # ① YOLO 크롭 (use_yolo=True이고 detector 있을 때만)
            analysis_frame = frame.copy()
            if self.use_yolo and self.detector:
                t0 = time.perf_counter()
                cropped, bbox = self.detector.detect_and_crop(frame)
                yolo_ms = (time.perf_counter() - t0) * 1000
                if cropped is not None and cropped.size > 0:
                    analysis_frame = cropped
                    active_bbox = bbox
            
            # 저장용 크롭 기억
            self._last_crop = analysis_frame.copy()

            # ② & ③ 임베딩 추출 및 분류 (FC 우선, 코사인 폴백)
            t1 = time.perf_counter()
            try:
                # BGR → RGB → PIL
                rgb_img = cv2.cvtColor(analysis_frame, cv2.COLOR_BGR2RGB)
                pil_img = PILImage.fromarray(rgb_img)
                best_name, confidence, ok_flag = self.classifier.classify_frame(pil_img)
                
                if best_name:
                    similarity = confidence
                    target_id  = os.path.splitext(best_name)[0]
                    is_ok      = ok_flag
            except Exception as e:
                print(f"[SiameseThread] 분류 실패: {e}")
            
            # 이전 구조와의 UI 호환성을 위해 연산 시간을 임의 반분 (통합되었으므로)
            total_t  = (time.perf_counter() - t1) * 1000
            embed_ms = total_t * 0.7
            sim_ms   = total_t * 0.3

            # ④ 오버레이 — analysis_frame에 결과 표시
            ov_color = (0, 200, 0) if is_ok else (0, 60, 220)
            sim_pct  = f"{similarity:.1f}%"
            cv2.rectangle(analysis_frame, (0, 0), (340, 26), (0, 0, 0), -1)
            cv2.putText(analysis_frame,
                        f"Siamese | Target:{target_id}  Sim:{sim_pct}",
                        (4, 18), cv2.FONT_HERSHEY_SIMPLEX, 0.55, ov_color, 1, cv2.LINE_AA)

            # ⑤ 원본 프레임에 YOLO 박스 오버레이 (use_yolo 모드일 때)
            if active_bbox:
                x1, y1, x2, y2 = active_bbox
                corners = self.detector.last_corners if self.detector else None
                if corners is not None:
                    pts = corners.astype(np.int32).reshape((-1, 1, 2))
                    cv2.polylines(display_frame, [pts], True, (0, 255, 0), 3)
                else:
                    cv2.rectangle(display_frame, (x1, y1), (x2, y2), (0, 200, 255), 3)

            # ⑥ 화면 미리보기: analysis 결과를 display_frame 좌상단 인셋으로 합성
            ih, iw = analysis_frame.shape[:2]
            th = min(200, display_frame.shape[0] // 3)
            tw = int(th * iw / ih)
            inset = cv2.resize(analysis_frame, (tw, th))
            dh, dw = display_frame.shape[:2]
            if dh >= th and dw >= tw:
                display_frame[0:th, 0:tw] = inset
                cv2.rectangle(display_frame, (0, 0), (tw, th), (0, 180, 255), 2)

            # ⑦ 프레임 emit
            rgb_disp = cv2.cvtColor(display_frame, cv2.COLOR_BGR2RGB)
            h, w, _ = rgb_disp.shape
            self.frame_signal.emit(
                QImage(rgb_disp.data, w, h, 3 * w, QImage.Format_RGB888).copy())

            # ⑧ FPS 계산 & status emit
            curr_t = time.perf_counter()
            self._fps_ts.append(curr_t)
            fps_real = ((len(self._fps_ts) - 1) /
                        max(self._fps_ts[-1] - self._fps_ts[0], 1e-6)
                        ) if len(self._fps_ts) >= 2 else 0.0

            self.status_signal.emit(
                fps_real, yolo_ms, embed_ms, sim_ms, similarity, is_ok, target_id)
            idx += 1

        cap.release()


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  샴 네트워크 우측 통계 패널
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
class SiameseStatsPanel(QWidget):
    """YOLO+ORB StatsPanel과 동일한 구성, 샴 전용 지표 표시"""

    def __init__(self):
        super().__init__()
        self.setMinimumWidth(300)
        self.setMaximumWidth(380)
        self.setStyleSheet(f"background:{C_WHITE}; border-left:1px solid {C_BORDER};")

        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(0, 0, 0, 0)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setStyleSheet("QScrollArea { border: none; }")

        content = QWidget()
        v = QVBoxLayout(content)
        v.setContentsMargins(14, 14, 14, 14)
        v.setSpacing(12)

        def sep():
            f = QFrame()
            f.setFrameShape(QFrame.HLine)
            f.setStyleSheet(f"color:{C_BORDER}; max-height:1px;")
            return f

        # ── KPI 카드 (1행) ────────────────────────────────────────
        # 🧠 핵심 지표: FPS / 유사도 % / 판정 / YOLO
        row = QHBoxLayout()
        self.fps_card   = self._card("FPS",       "--")
        self.sim_card   = self._card("유사도 %",  "--")
        self.verdict    = self._card("판정",      "대기")
        self.yolo_card  = self._card("YOLO",      "미연결")
        row.addWidget(self.fps_card[0])
        row.addWidget(self.sim_card[0])
        row.addWidget(self.verdict[0])
        row.addWidget(self.yolo_card[0])
        v.addLayout(row)

        # ── KPI 카드 (2행) — 처리 시간 합계 ─────────────────────
        self.lbl_total = QLabel("Total: 0 ms")
        self.lbl_total.setStyleSheet(
            f"font-size:15px; font-weight:bold; color:{C_DARK}; text-align:center;")
        self.lbl_total.setAlignment(Qt.AlignCenter)
        v.addWidget(self.lbl_total)
        v.addWidget(sep())

        # ── Latency 막대 (YOLO / 임베딩 추출 / 유사도 비교) ──────
        self.latency = self._build_latency_bars(v)
        v.addWidget(sep())

        # ── 파이 차트 ─────────────────────────────────────────────
        self.pie = PieChartWidget()
        self.pie._names = ["YOLO", "임베딩", "유사도비교", ""]
        v.addWidget(self.pie)
        v.addWidget(sep())

        # ── 전체 소요 시간 캔들스틱 ──────────────────────────────
        self.candle_chart = CandlestickWidget()
        v.addWidget(self.candle_chart)
        v.addWidget(sep())

        # ── 유사도 분포 박스플롯 (합격/불합격) ───────────────────
        self.boxplot = DualBoxPlotWidget(
            title="유사도 분포 비교 (합격/불합격)", max_val=100)
        v.addWidget(self.boxplot)
        v.addStretch()

        scroll.setWidget(content)
        main_layout.addWidget(scroll)

    # ── Latency 막대 3개 구성 ─────────────────────────────────────
    def _build_latency_bars(self, v):
        """YOLO / 임베딩 / 유사도비교 막대를 만들어 v에 추가, 참조 dict 반환"""
        bars = {}
        container = QWidget()
        container.setFixedHeight(120)
        cv = QVBoxLayout(container)
        cv.setContentsMargins(0, 0, 0, 0)
        cv.setSpacing(6)

        lbl = QLabel("⏱️  구간별 분석 시간 (Latency)")
        lbl.setStyleSheet(f"font-size:12px; font-weight:bold; color:{C_DARK};")
        cv.addWidget(lbl)

        specs = [
            ("YOLO 추론",    C_BLUE,    "yolo"),
            ("임베딩 추출",  "#9B59B6", "embed"),
            ("유사도 비교",  C_ORANGE,  "sim"),
        ]
        for name, color, key in specs:
            h = QHBoxLayout()
            lbl_n = QLabel(name)
            lbl_n.setMinimumWidth(78)
            lbl_n.setStyleSheet(f"font-size:12px; color:{C_DARK}; font-weight:bold;")
            bar = QProgressBar()
            bar.setMaximum(100)
            bar.setValue(0)
            bar.setTextVisible(False)
            bar.setFixedHeight(12)
            bar.setStyleSheet(
                f"QProgressBar{{background:{C_BORDER};border-radius:4px;}}"
                f"QProgressBar::chunk{{background:{color};border-radius:4px;}}")
            val_lbl = QLabel("0 ms")
            val_lbl.setFixedWidth(52)
            val_lbl.setStyleSheet(
                f"font-size:11px; color:{C_DARK}; font-weight:bold;")
            h.addWidget(lbl_n)
            h.addWidget(bar)
            h.addWidget(val_lbl)
            cv.addLayout(h)
            bars[key] = (bar, val_lbl)

        v.addWidget(container)
        return bars

    def _card(self, title, val):
        c = QWidget()
        c.setStyleSheet(f"background:{C_BG}; border-radius:8px;")
        cv = QVBoxLayout(c)
        cv.setContentsMargins(8, 8, 8, 8)
        t = QLabel(title)
        t.setAlignment(Qt.AlignCenter)
        t.setStyleSheet(f"font-size:11px; color:{C_SUB}; font-weight:bold;")
        lv = QLabel(val)
        lv.setAlignment(Qt.AlignCenter)
        lv.setStyleSheet(
            f"font-size:17px; font-weight:bold; color:{C_DARK};")
        cv.addWidget(t)
        cv.addWidget(lv)
        return c, lv

    def update_stats(self, fps, yolo_ms, embed_ms, sim_ms,
                     similarity, is_ok, target_id, use_yolo):
        total = yolo_ms + embed_ms + sim_ms
        col   = C_GREEN if is_ok else C_RED

        # FPS
        self.fps_card[1].setText(f"{fps:.0f}")

        # 유사도 카드
        self.sim_card[1].setText(f"{similarity:.1f}%")
        self.sim_card[1].setStyleSheet(
            f"font-size:16px; font-weight:bold; color:{col};")

        # 판정 카드
        if is_ok and target_id:
            verdict_text = f"✅ 타겟 {target_id}"
        elif is_ok:
            verdict_text = "✅ 정상"
        else:
            verdict_text = "❌ 불일치"
        self.verdict[1].setText(verdict_text)
        self.verdict[1].setStyleSheet(
            f"font-size:13px; font-weight:bold; color:{col};")

        # YOLO 카드
        if use_yolo:
            yolo_state = f"{yolo_ms:.0f}ms" if yolo_ms > 0 else "탐지 실패"
            yolo_color = C_BLUE if yolo_ms > 0 else C_ORANGE
        else:
            yolo_state  = "OFF (풀프레임)"
            yolo_color  = C_SUB
        self.yolo_card[1].setText(yolo_state)
        self.yolo_card[1].setStyleSheet(
            f"font-size:11px; font-weight:bold; color:{yolo_color};")

        # Total
        self.lbl_total.setText(f"Total: {total:.1f} ms")

        # Latency 막대 업데이트
        total_safe = max(total, 1.0)
        for key, ms in [("yolo", yolo_ms), ("embed", embed_ms), ("sim", sim_ms)]:
            bar, lbl_v = self.latency[key]
            bar.setValue(int(ms / total_safe * 100))
            lbl_v.setText(f"{ms:.1f} ms")

        # 파이 차트 (4번째 슬롯은 0 으로 채움)
        self.pie.update_pie(yolo_ms, embed_ms, sim_ms, 0.01)

        # 캔들스틱
        self.candle_chart.add_value(total)

        # 박스플롯
        self.boxplot.add_score(similarity, is_ok)


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  샴 네트워크 실시간 관제 서브탭
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
class SiameseMonitorSubTab(QWidget):
    """
    YOLO+ORB LiveMonitorSubTab과 동일한 UX,
    YOLO 사용 여부를 실시간으로 토글할 수 있습니다.

    ◎ YOLO ON  → 모니터 영역 크롭 후 임베딩 (정확도 ↑)
    ◎ YOLO OFF → 풀프레임 그대로 임베딩   (지연 ↓, 크롭 없음)
    """

    def __init__(self):
        super().__init__()
        self.setStyleSheet(f"background:{C_BG};")
        self._thread   = None
        self._use_yolo = True    # 기본값: YOLO 활성

        v = QVBoxLayout(self)
        v.setContentsMargins(8, 8, 8, 8)
        v.setSpacing(6)
        v.addWidget(self._build_ctrl())

        body = QHBoxLayout()
        body.setSpacing(0)

        # 좌측: 영상 + 슬라이더
        left_body = QVBoxLayout()
        left_body.setContentsMargins(0, 0, 0, 0)
        left_body.setSpacing(8)

        self.video = VideoDisplayLabel()
        left_body.addWidget(self.video, stretch=1)

        self.slider = QSlider(Qt.Horizontal)
        self.slider.setRange(0, 100)
        self.slider.setEnabled(False)
        self.slider.sliderMoved.connect(self._on_seek)
        self.slider.setStyleSheet(f"""
            QSlider::groove:horizontal {{ border:1px solid {C_BORDER}; background:{C_WHITE}; height:6px; border-radius:3px; }}
            QSlider::sub-page:horizontal {{ background:{C_BLUE}; border:1px solid #2980B9; height:6px; border-radius:3px; }}
            QSlider::handle:horizontal {{ background:{C_DARK}; border:1px solid #1A252F; width:14px; margin-top:-4px; margin-bottom:-4px; border-radius:5px; }}
        """)
        left_body.addWidget(self.slider)
        body.addLayout(left_body, stretch=1)

        # 우측: 샴 전용 통계 패널
        self.stats = SiameseStatsPanel()
        body.addWidget(self.stats)

        v.addLayout(body, stretch=1)

    # ── 컨트롤 바 ──────────────────────────────────────────────
    def _build_ctrl(self):
        bar = QWidget()
        bar.setFixedHeight(46)
        bar.setStyleSheet(
            f"background:{C_WHITE}; border:1px solid {C_BORDER}; border-radius:8px;")
        h = QHBoxLayout(bar)
        h.setContentsMargins(10, 0, 10, 0)
        h.setSpacing(8)

        h.addWidget(QLabel("🧬  샴 네트워크 실시간 관제"))
        h.addStretch()

        # 파일 열기 / 카메라
        for text, slot in [("📂  파일 열기", self._open_file),
                           ("📷  카메라 (0번)", self._open_cam)]:
            b = QPushButton(text)
            b.clicked.connect(slot)
            h.addWidget(b)

        # ── 일시 정지 ─────────────────────────────────────
        self.btn_pause = QPushButton("⏸ 일시 정지")
        self.btn_pause.setStyleSheet(
            f"background:{C_ORANGE};color:white;border:none;border-radius:6px;font-weight:bold;")
        self.btn_pause.clicked.connect(self._toggle_pause)
        self.btn_pause.setEnabled(False)
        h.addWidget(self.btn_pause)

        btn_stop = QPushButton("⏹ 종료")
        btn_stop.setStyleSheet(
            f"background:{C_RED};color:white;border:none;border-radius:6px;font-weight:bold;")
        btn_stop.clicked.connect(self._stop)
        h.addWidget(btn_stop)

        # ── 샴큐 캡처 (샴수동학습용 큐) ───────────────────
        btn_siam_cap = QPushButton("📸 샴큐 캡처")
        btn_siam_cap.setFixedWidth(85)
        btn_siam_cap.setStyleSheet(
            f"background:#34495e;color:white;border:none;border-radius:6px;font-size:11px;font-weight:bold;")
        btn_siam_cap.clicked.connect(self._capture_for_siamese)
        h.addWidget(btn_siam_cap)

        # ── 핵심 토글: YOLO ON / OFF ──────────────────────
        self.btn_yolo = QPushButton("🔍 YOLO ON")
        self.btn_yolo.setCheckable(True)
        self.btn_yolo.setChecked(True)   # 기본 ON
        self.btn_yolo.setFixedWidth(100)
        self.btn_yolo.setStyleSheet(
            f"QPushButton{{background:{C_BLUE};color:white;border:none;"
            f"border-radius:6px;font-weight:bold;font-size:11px;}}"
            f"QPushButton:!checked{{background:{C_BG};color:{C_DARK};"
            f"border:1px solid {C_BORDER};border-radius:6px;font-size:11px;}}")
        self.btn_yolo.setToolTip(
            "ON:  YOLO로 모니터 영역을 잘라낸 뒤 샴 임베딩\n"
            "OFF: 풀프레임 그대로 샴 임베딩 (YOLO 없이도 동작 가능 여부 확인)")
        self.btn_yolo.clicked.connect(self._toggle_yolo)
        h.addWidget(self.btn_yolo)

        # ── 상태 표시 레이블 ──────────────────────────────
        self.lbl_st = QLabel("● 대기")
        self.lbl_st.setStyleSheet(
            f"font-size:12px; font-weight:bold; color:{C_SUB}; padding-left:6px;")
        h.addWidget(self.lbl_st)
        return bar

    # ── 토글 핸들러 ────────────────────────────────────────────
    
    def _capture_for_siamese(self):
        """현재 YOLO 크롭(또는 풀프레임) 프레임을 data/siamese_train/_queue/ 에 저장"""
        if not self._thread or self._thread._last_crop is None:
            QMessageBox.warning(self, "캡처 실패", "영상을 재생 중이어야 합니다.")
            return
            
        queue_dir = os.path.join(_ROOT, "data", "siamese_train", "_queue")
        os.makedirs(queue_dir, exist_ok=True)
        
        from datetime import datetime
        ts = datetime.now().strftime("%Y%m%d_%H%M%S_%f")[:18]
        fname = f"sq_{ts}.png"
        save_path = os.path.join(queue_dir, fname)
        
        try:
            ret, buf = cv2.imencode(".png", self._thread._last_crop)
            if ret:
                buf.tofile(save_path)
                cnt = len([f for f in os.listdir(queue_dir) if f.endswith(('.png','.jpg'))])
                self.lbl_st.setText(f"● 샴큐 누적: {cnt}장")
            else:
                QMessageBox.critical(self, "캡처 실패", "이미지 인코딩 실패")
        except Exception as ex:
            QMessageBox.critical(self, "오류", str(ex))

    def _toggle_pause(self):
        if self._thread:
            self._thread.pause_resume()
            if self._thread._paused:
                self.btn_pause.setText("▶ 계속 재생")
                self.btn_pause.setStyleSheet(
                    f"background:{C_GREEN};color:white;border:none;"
                    f"border-radius:6px;font-weight:bold;")
                self.lbl_st.setText("● 일시 정지됨")
                self.lbl_st.setStyleSheet(
                    f"font-size:12px; font-weight:bold; color:{C_ORANGE}; padding-left:6px;")
            else:
                self.btn_pause.setText("⏸ 일시 정지")
                self.btn_pause.setStyleSheet(
                    f"background:{C_ORANGE};color:white;border:none;"
                    f"border-radius:6px;font-weight:bold;")
                self.lbl_st.setText("● 분석 중...")
                self.lbl_st.setStyleSheet(
                    f"font-size:12px; font-weight:bold; color:{C_BLUE}; padding-left:6px;")

    def _toggle_yolo(self):
        """YOLO ON/OFF 실시간 전환 — 스레드 재시작으로 반영"""
        self._use_yolo = self.btn_yolo.isChecked()
        label = "🔍 YOLO ON" if self._use_yolo else "🔍 YOLO OFF"
        self.btn_yolo.setText(label)
        # 실행 중이면 현재 소스 기억 후 재시작
        if self._thread and self._thread.isRunning():
            src = self._thread.source
            self._stop()
            self._start(src)

    # ── 소스 열기 ──────────────────────────────────────────────
    def _open_file(self):
        p, _ = QFileDialog.getOpenFileName(
            self, "영상 파일", _ROOT,
            "Video (*.mp4 *.avi *.mov *.mkv)")
        if p:
            self._start(p)

    def _open_cam(self):
        self._start(0)

    def _on_seek(self, value):
        if self._thread:
            self._thread.set_frame(value)

    # ── 스레드 생명주기 ────────────────────────────────────────
    def _start(self, src):
        self._stop()
        self.stats.pie.reset_cumulative()
        self._thread = SiameseVideoThread(src, use_yolo=self._use_yolo)
        self._thread.frame_signal.connect(
            lambda qi: self.video.set_frame(QPixmap.fromImage(qi)))
        self._thread.status_signal.connect(self._on_status)
        self._thread.progress_signal.connect(self._on_progress)
        self._thread.start()

        self.btn_pause.setEnabled(True)
        self.btn_pause.setText("⏸ 일시 정지")
        self.btn_pause.setStyleSheet(
            f"background:{C_ORANGE};color:white;border:none;"
            f"border-radius:6px;font-weight:bold;")
        self.slider.setEnabled(True)
        self.lbl_st.setText("● 분석 중...")
        self.lbl_st.setStyleSheet(
            f"font-size:12px; font-weight:bold; color:{C_BLUE}; padding-left:6px;")

    def _stop(self):
        if self._thread and self._thread.isRunning():
            self._thread.stop()
        self._thread = None
        self.btn_pause.setEnabled(False)
        self.slider.setEnabled(False)
        self.slider.setValue(0)
        self.lbl_st.setText("● 정지")
        self.lbl_st.setStyleSheet(
            f"font-size:12px; font-weight:bold; color:{C_SUB}; padding-left:6px;")

    # ── 시그널 수신 ────────────────────────────────────────────
    def _on_progress(self, cur, tot):
        if not self.slider.isSliderDown():
            if self.slider.maximum() != tot:
                self.slider.setRange(0, tot)
            self.slider.blockSignals(True)
            self.slider.setValue(cur)
            self.slider.blockSignals(False)

    def _on_status(self, fps, yolo_ms, embed_ms, sim_ms,
                   similarity, is_ok, target_id):
        self.stats.update_stats(
            fps, yolo_ms, embed_ms, sim_ms,
            similarity, is_ok, target_id, self._use_yolo)

        col = C_GREEN if is_ok else C_RED
        if is_ok and target_id:
            st_text = f"● 타겟 {target_id} ✅  ({similarity:.1f}%)"
        elif is_ok:
            st_text = "● 정상 ✅"
        else:
            st_text = f"● 불일치 ❌  ({similarity:.1f}%)"
        self.lbl_st.setText(st_text)
        self.lbl_st.setStyleSheet(
            f"font-size:12px; font-weight:bold; color:{col}; padding-left:6px;")

    def closeEvent(self, e):
        self._stop()
        super().closeEvent(e)


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  최종 MonitorTab (서브탭 A + 서브탭 B + 서브탭 C)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
class MonitorTab(QWidget):
    def __init__(self):
        super().__init__()
        self.setStyleSheet(f"background:{C_BG};")
        v = QVBoxLayout(self); v.setContentsMargins(0,0,0,0)

        sub = QTabWidget()
        sub.setStyleSheet(f"""
            QTabWidget::pane{{border:none; background:{C_BG};}}
            QTabBar::tab{{background:{C_BG};color:{C_SUB};padding:8px 20px;
                border:1px solid {C_BORDER};border-bottom:none;border-radius:4px 4px 0 0; margin-right:2px;}}
            QTabBar::tab:selected{{background:{C_WHITE};color:{C_BLUE};border-bottom:2px solid {C_BLUE};}}
        """)
        sub.addTab(LiveMonitorSubTab(),      "  🎥  실시간 Live 관제  ")
        sub.addTab(TargetROITab(),           "  🎯  타겟 뷰어 & ROI 설정  ")
        sub.addTab(SiameseMonitorSubTab(),   "  🧬  샴 네트워크 관제  ")
        v.addWidget(sub)
