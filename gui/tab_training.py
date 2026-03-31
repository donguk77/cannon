"""
tab_training.py — TAB 2: 야간 학습 지시 및 데이터 검수실 (Track B)
서브탭 구성:
  [서브탭 A] Pending 검수  : Track A에서 자동 캡처된 이미지 (개별 가중치 기본=3)
  [서브탭 B] 학습 데이터셋 : dataset_target_and_1cycle/data의 실제 라벨드 데이터 그리드 뷰어
격발 버튼: [🎯 YOLO 재학습] / [⚙️ ORB 파라미터 최적화] 분리
"""
import os, sys, shutil
import numpy as np
import cv2

from PyQt5.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QFrame, QScrollArea, QGridLayout, QFileDialog,
    QProgressBar, QMessageBox, QTabWidget, QListWidget,
    QSizePolicy, QAbstractItemView, QSplitter
)
from PyQt5.QtCore import Qt, QThread, pyqtSignal, QTimer, QPoint, QRect
from PyQt5.QtGui import QPixmap, QImage, QFont, QPainter, QPen, QBrush, QColor

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
C_BG     = "#F8F9FA"; C_WHITE  = "#FFFFFF"; C_DARK   = "#2C3E50"
C_SUB    = "#7F8C8D"; C_BLUE   = "#3498DB"; C_GREEN  = "#27AE60"
C_RED    = "#E74C3C"; C_ORANGE = "#E67E22"; C_BORDER = "#E0E4E8"

# ─── 학습 데이터 경로 ──────────────────────────────────────────────────────────
LABELED_DATA_DIR = os.path.join(_ROOT, "dataset_target_and_1cycle", "data")
PENDING_DIR      = os.path.join(_ROOT, "data", "pending")

# ─── importance 설정 저장 파일 ─────────────────────────────────────────────────
import json
IMPORTANCE_FILE = os.path.join(_ROOT, "data", "importance_config.json")


def _load_importance() -> dict:
    if os.path.exists(IMPORTANCE_FILE):
        try:
            with open(IMPORTANCE_FILE) as f: return json.load(f)
        except: pass
    return {}


def _save_importance(data: dict):
    os.makedirs(os.path.dirname(IMPORTANCE_FILE), exist_ok=True)
    with open(IMPORTANCE_FILE, "w") as f: json.dump(data, f, indent=2)


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  백그라운드 워커들
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
class OrbTunerThread(QThread):
    progress = pyqtSignal(int, str)
    finished = pyqtSignal(dict, float)

    def __init__(self, target_paths, pending_paths, n_trials=30):
        super().__init__()
        self.target_paths = target_paths; self.pending_paths = pending_paths
        self.n_trials = n_trials

    def run(self):
        try:
            from offline.auto_tuner import BayesianAutoTuner
            tuner = BayesianAutoTuner(self.target_paths, self.pending_paths)
            self.progress.emit(10, "ORB 베이시안 탐색 시작...")
            bp, bs = tuner.run_night_tuning(n_trials=self.n_trials)
            self.progress.emit(100, f"ORB 완료! 최고 점수: {bs:.2f}")
            self.finished.emit(bp, bs)
        except Exception as e:
            self.progress.emit(-1, f"ORB 튜닝 오류: {e}")


class YoloTrainThread(QThread):
    progress     = pyqtSignal(int, str)
    finished     = pyqtSignal(dict)
    metric_signal = pyqtSignal(dict)   # epoch별 지표: {epoch, total, P, R, mAP50, mAP50-95, box_loss}

    def __init__(self, epochs=50, mode='resume'):
        super().__init__()
        self.epochs = epochs
        self.mode   = mode   # 'scratch' | 'resume'
        self._proc  = None

    def run(self):
        """
        subprocess로 train_yolo.py를 실행하여 Qt/torch DLL 충돌(WinError 1114)을 방지합니다.
        stdout의 [PCT%] 행과 [METRIC] 행을 파싱해 각각 progress/metric_signal로 내보냅니다.
        """
        import subprocess, sys
        python_exe = sys.executable
        _ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        script = os.path.join(_ROOT, "scripts", "train_yolo.py")

        self.progress.emit(3, "YOLO 학습 프로세스 시작 중...")
        try:
            self._proc = subprocess.Popen(
                [python_exe, script, "--epochs", str(self.epochs), "--mode", self.mode],
                cwd=_ROOT,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True, encoding="utf-8", errors="replace"
            )
            for line in iter(self._proc.stdout.readline, ""):
                line = line.strip()
                if not line: continue

                # ── [PCT%] 진행률 파싱 ──────────────────────────────────────
                if line.startswith("[") and "%]" in line:
                    try:
                        pct = int(float(line[1: line.index("%]")]))
                        msg = line[line.index("%]")+2:].strip()
                        self.progress.emit(pct, msg)
                    except Exception:
                        self.progress.emit(50, line[:100])

                # ── [METRIC] epoch별 지표 파싱 ──────────────────────────────
                elif line.startswith("[METRIC]"):
                    try:
                        parts = line[8:].split()
                        m = {k: v for kv in parts for k, v in [kv.split("=")]}
                        self.metric_signal.emit({
                            "epoch":    int(m.get("epoch", 0)),
                            "total":    int(m.get("total", self.epochs)),
                            "P":        float(m.get("P", 0)),
                            "R":        float(m.get("R", 0)),
                            "mAP50":    float(m.get("mAP50", 0)),
                            "mAP50_95": float(m.get("mAP50-95", 0)),
                            "box_loss": float(m.get("box_loss", 0)),
                        })
                    except Exception:
                        pass

            self._proc.wait()
            ret = self._proc.returncode
            if ret == 0:
                self.progress.emit(100, "YOLO 학습 완료!")
                self.finished.emit({"map50": 0.0, "model_path": "", "error": ""})
            else:
                self.progress.emit(-1, f"학습 비정상 종료 (코드: {ret})")
        except Exception as e:
            self.progress.emit(-1, f"YOLO 실행 오류: {e}")




# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  공용 이미지 카드 (가중치 기본=3, ± 버튼)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
STATUS_COLORS = {
    "ok":        (C_GREEN,  "✅ 정상"),
    "ambiguous": (C_ORANGE, "⚠️ 애매"),
    "labeled":   (C_BLUE,   "🏷️ 라벨"),
}

class ImageCard(QWidget):
    def __init__(self, img_path: str, status: str = "ambiguous", default_weight: int = 3):
        super().__init__()
        self.img_path = img_path
        self.weight   = default_weight
        self.is_hard_negative = False
        color, badge_text = STATUS_COLORS.get(status, (C_BORDER, "?"))
        self.setFixedSize(158, 215)
        self.setStyleSheet(f"background:{C_WHITE}; border-radius:8px; border:1px solid {color};")

        v = QVBoxLayout(self); v.setContentsMargins(5,5,5,5); v.setSpacing(3)

        # 썸네일 (한글 경로 지원: np.fromfile + imdecode 우회)
        self.thumb = QLabel(); self.thumb.setFixedHeight(98)
        self.thumb.setAlignment(Qt.AlignCenter)
        self.thumb.setStyleSheet(f"background:#1a1a2e; border-radius:4px; border:2px solid {color};")
        if os.path.exists(img_path):
            try:
                buf = np.fromfile(img_path, dtype=np.uint8)
                img = cv2.imdecode(buf, cv2.IMREAD_COLOR)
                if img is not None:
                    img_rgb = cv2.cvtColor(img, cv2.COLOR_BGR2RGB)
                    h2, w2 = img_rgb.shape[:2]
                    qimg = QImage(img_rgb.tobytes(), w2, h2, 3*w2, QImage.Format_RGB888)
                    pix = QPixmap.fromImage(qimg).scaled(146, 94, Qt.KeepAspectRatio, Qt.SmoothTransformation)
                    self.thumb.setPixmap(pix)
            except Exception as ex:
                print(f"[ImageCard] 썸네일 로드 실패: {img_path} ({ex})")
        v.addWidget(self.thumb)


        # 배지
        badge = QLabel(badge_text); badge.setAlignment(Qt.AlignCenter)
        badge.setStyleSheet(f"background:{color}22;color:{color};font-size:9px;font-weight:bold;border-radius:3px;")
        v.addWidget(badge)

        # 가중치 행
        w_row = QHBoxLayout(); w_row.setSpacing(2)
        btn_m = QPushButton("−"); btn_m.setFixedSize(24,22)
        btn_m.setStyleSheet(f"background:{C_ORANGE};color:white;border:none;border-radius:4px;font-weight:bold;font-size:14px;")
        self.lbl_w = QLabel(f"중요도: {self.weight}")
        self.lbl_w.setAlignment(Qt.AlignCenter)
        self.lbl_w.setStyleSheet(f"font-size:10px;color:{C_DARK};font-weight:bold;")
        btn_p = QPushButton("+"); btn_p.setFixedSize(24,22)
        btn_p.setStyleSheet(f"background:{C_BLUE};color:white;border:none;border-radius:4px;font-weight:bold;font-size:14px;")
        w_row.addWidget(btn_m); w_row.addWidget(self.lbl_w,1); w_row.addWidget(btn_p)
        v.addLayout(w_row)
        btn_m.clicked.connect(lambda: self._chg(-1))
        btn_p.clicked.connect(lambda: self._chg(+1))

        # Hard Neg 버튼
        self.btn_hn = QPushButton("🚫 Hard Neg")
        self.btn_hn.setFixedHeight(22)
        self.btn_hn.setStyleSheet(f"font-size:9px;background:{C_BG};color:{C_RED};border:1px solid {C_RED};border-radius:4px;")
        self.btn_hn.clicked.connect(self._toggle_hn)
        v.addWidget(self.btn_hn)

        # 파일명
        fname = os.path.basename(img_path)
        if len(fname) > 17: fname = fname[:14]+"..."
        fn_l = QLabel(fname); fn_l.setAlignment(Qt.AlignCenter)
        fn_l.setStyleSheet(f"font-size:8px;color:{C_SUB};")
        v.addWidget(fn_l)

    def _chg(self, d):
        self.weight = max(1, min(10, self.weight+d))
        self.lbl_w.setText(f"중요도: {self.weight}")

    def _toggle_hn(self):
        self.is_hard_negative = not self.is_hard_negative
        if self.is_hard_negative:
            self.setStyleSheet(f"background:#fff5f5;border-radius:8px;border:2px solid {C_RED};")
            self.btn_hn.setText("🚫 Hard Neg ✓")
        else:
            self.setStyleSheet(f"background:{C_WHITE};border-radius:8px;border:1px solid {C_ORANGE};")
            self.btn_hn.setText("🚫 Hard Neg")


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  서브탭 A: Pending 검수실
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
class PendingReviewTab(QWidget):
    def __init__(self):
        super().__init__()
        self.setStyleSheet(f"background:{C_BG};")
        self._cards = []
        v = QVBoxLayout(self); v.setContentsMargins(8,8,8,8); v.setSpacing(6)
        v.addWidget(self._build_ctrl())

        body = QHBoxLayout(); body.setSpacing(8)
        body.addWidget(self._build_grid(), stretch=1)
        body.addWidget(self._build_guide_panel())
        v.addLayout(body, stretch=1)

        QTimer.singleShot(400, self._load)

    def _build_ctrl(self):
        bar = QWidget(); bar.setFixedHeight(44)
        bar.setStyleSheet(f"background:{C_WHITE};border:1px solid {C_BORDER};border-radius:8px;")
        h = QHBoxLayout(bar); h.setContentsMargins(10,0,10,0); h.setSpacing(8)
        h.addWidget(self._lbl("📋  Pending 검수 (Track A 자동 캡처)", bold=True))
        h.addStretch()
        for txt, fn in [("📁 파일 투입", self._upload),
                         ("🤖 AI 자동 분류", self._ai_classify),
                         ("🔄 새로고침", self._load)]:
            b = QPushButton(txt); b.clicked.connect(fn); h.addWidget(b)
        self.lbl_cnt = QLabel("0개"); self.lbl_cnt.setStyleSheet(f"font-size:11px;color:{C_SUB};padding-left:4px;")
        h.addWidget(self.lbl_cnt)
        return bar

    def _build_grid(self):
        box = QWidget()
        box.setStyleSheet(f"background:{C_WHITE};border:1px solid {C_BORDER};border-radius:8px;")
        v = QVBoxLayout(box); v.setContentsMargins(8,8,8,8); v.setSpacing(4)
        scroll = QScrollArea(); scroll.setWidgetResizable(True); scroll.setStyleSheet("border:none;")
        self._gc = QWidget()
        self._grid = QGridLayout(self._gc); self._grid.setSpacing(8)
        self._grid.setAlignment(Qt.AlignTop|Qt.AlignLeft)
        scroll.setWidget(self._gc)
        v.addWidget(scroll)
        return box

    def _build_guide_panel(self):
        p = QWidget(); p.setFixedWidth(220)
        p.setStyleSheet(f"background:{C_WHITE};border:1px solid {C_BORDER};border-radius:8px;")
        v = QVBoxLayout(p); v.setContentsMargins(12,12,12,12); v.setSpacing(8)
        v.addWidget(self._lbl("ℹ️  가이드", bold=True))
        guide = QLabel(
            "• [−][+]로 중요도 조정\n  기본값: 3 / 최대: 10\n\n"
            "• 🚫 Hard Neg: 절대 오답\n  케이스로 강제 학습\n\n"
            "• 높은 중요도 이미지는\n  학습 시 더 많이 반복\n  노출됩니다."
        )
        guide.setWordWrap(True)
        guide.setStyleSheet(f"font-size:11px;color:{C_SUB};")
        v.addWidget(guide)
        v.addStretch()
        btn_save = QPushButton("💾 중요도 설정 저장")
        btn_save.setStyleSheet(f"background:{C_GREEN};color:white;border:none;border-radius:6px;font-weight:bold;")
        btn_save.clicked.connect(self._save_importance)
        v.addWidget(btn_save)
        return p

    def _lbl(self, t, bold=False):
        l = QLabel(t); l.setStyleSheet(f"font-size:12px;{'font-weight:bold;' if bold else ''}color:{C_DARK};")
        return l

    def _load(self):
        os.makedirs(PENDING_DIR, exist_ok=True)
        for c in self._cards: c.deleteLater()
        self._cards.clear()
        imp = _load_importance()
        files = [f for f in os.listdir(PENDING_DIR) if f.lower().endswith((".jpg",".png"))][:60]
        for i, f in enumerate(files):
            path = os.path.join(PENDING_DIR, f)
            w_default = imp.get(f, 3)
            card = ImageCard(path, "ambiguous", default_weight=w_default)
            self._grid.addWidget(card, i//5, i%5)
            self._cards.append(card)
        self.lbl_cnt.setText(f"{len(files)}개")

    def _upload(self):
        paths, _ = QFileDialog.getOpenFileNames(self,"파일 선택",_ROOT,"이미지 (*.jpg *.png)")
        os.makedirs(PENDING_DIR, exist_ok=True)
        for p in paths: shutil.copy2(p, PENDING_DIR)
        self._load()

    def _ai_classify(self):
        QMessageBox.information(self,"AI 분류","Siamese + Gemini 1차 분류를 시작합니다.")

    def _save_importance(self):
        data = {os.path.basename(c.img_path): c.weight for c in self._cards}
        _save_importance(data)
        QMessageBox.information(self, "저장 완료", f"{len(data)}개 이미지의 중요도가 저장되었습니다.")

    def get_pending_paths(self):
        return [c.img_path for c in self._cards]


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  학습 데이터 이미지 뷰어 (타겟 뷰어와 동일한 구조)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
class DatasetImageViewer(QLabel):
    """
    학습 데이터 이미지를 크게 표시하고 YOLO bbox + ORB 특징점 + 드래그 ROI를 그립니다.
    """
    roi_added  = pyqtSignal(object)   # QRectF 시그널 (정규화 좌표) — ROI 모드
    bbox_added = pyqtSignal(object)   # QRectF 시그널 — BBOX 모드일 때 발행

    def __init__(self):
        super().__init__()
        self.setAlignment(Qt.AlignCenter)
        self.setMinimumSize(400, 300)
        self.setStyleSheet("background:#1a1a2e; border-radius:6px;")
        self._base_pixmap = None
        self._kp_list  = []   # 정규화 ORB 키포인트
        self._bboxes   = []   # 정규화 YOLO bbox [(cx,cy,w,h), ...]
        self._rois     = []   # 정규화 QRectF ROI 목록
        self._origin   = QPoint()
        self._bbox_mode = False   # True → 드래그 시 BBOX 추가 (False이면 ROI 모드)
        from PyQt5.QtWidgets import QRubberBand
        self._rubber = QRubberBand(QRubberBand.Rectangle, self)

    def load_image(self, img_path: str, txt_path: str = None):
        """이미지 + YOLO label 불러오기 및 실제 파이프라인(Crop -> Preprocess -> ORB) 시뮬레이션"""
        try:
            buf = np.fromfile(img_path, dtype=np.uint8)
            img_color = cv2.imdecode(buf, cv2.IMREAD_COLOR)
        except Exception as ex:
            print(f"[DatasetImageViewer] 로드 실패: {img_path} ({ex})")
            return
        if img_color is None:
            return
            
        h, w = img_color.shape[:2]

        # 1. YOLO bbox 파싱
        self._bboxes = []
        if txt_path and os.path.exists(txt_path):
            try:
                with open(txt_path) as f:
                    for line in f:
                        parts = line.strip().split()
                        if len(parts) == 5:
                            _, cx, cy, bw, bh = map(float, parts)
                            self._bboxes.append((cx, cy, bw, bh))
            except Exception:
                pass

        # 2. 실제와 동일하게 작동: BBOX가 있으면 해당 영역을 자른 뒤 전처리, 없으면 전체 이미지 전처리
        try:
            from engine.preprocessor import ImagePreprocessor
            preprocessor = ImagePreprocessor()
        except Exception:
            preprocessor = None

        self._kp_list = []
        orb = cv2.ORB_create(nfeatures=700)
        
        if self._bboxes:
            # 첫 번째 bbox 기준
            cx, cy, bw, bh = self._bboxes[0]
            x_min = max(0, int((cx - bw/2) * w))
            y_min = max(0, int((cy - bh/2) * h))
            x_max = min(w, int((cx + bw/2) * w))
            y_max = min(h, int((cy + bh/2) * h))
            
            if x_max > x_min and y_max > y_min:
                cropped = img_color[y_min:y_max, x_min:x_max]
                if preprocessor:
                    pre_ready = preprocessor.preprocess_for_orb(cropped)
                else:
                    pre_ready = cv2.cvtColor(cropped, cv2.COLOR_BGR2GRAY)
                    
                kp, _ = orb.detectAndCompute(pre_ready, None)
                # 좌표를 다시 원본 이미지 기준으로 복원 후 정규화
                self._kp_list = [((p.pt[0]+x_min)/w, (p.pt[1]+y_min)/h) for p in kp]
        else:
            # BBOX가 없을 때
            if preprocessor:
                pre_ready = preprocessor.preprocess_for_orb(img_color)
            else:
                pre_ready = cv2.cvtColor(img_color, cv2.COLOR_BGR2GRAY)
                
            kp, _ = orb.detectAndCompute(pre_ready, None)
            self._kp_list = [(p.pt[0]/w, p.pt[1]/h) for p in kp]

        img_rgb = cv2.cvtColor(img_color, cv2.COLOR_BGR2RGB)
        qimg = QImage(img_rgb.tobytes(), w, h, 3*w, QImage.Format_RGB888)
        self._base_pixmap = QPixmap.fromImage(qimg)
        self.repaint()

    def set_rois(self, rois):
        self._rois = rois; self.update()

    def paintEvent(self, e):
        super().paintEvent(e)
        if self._base_pixmap is None or self._base_pixmap.isNull():
            return
        p = QPainter(self); p.setRenderHint(QPainter.Antialiasing)
        scaled = self._base_pixmap.scaled(self.size(), Qt.KeepAspectRatio, Qt.SmoothTransformation)
        ox = (self.width()  - scaled.width())  // 2
        oy = (self.height() - scaled.height()) // 2
        p.drawPixmap(ox, oy, scaled)
        sw, sh = scaled.width(), scaled.height()

        # YOLO bbox (초록 실선)
        p.setPen(QPen(QColor(C_GREEN), 2, Qt.SolidLine))
        p.setBrush(QBrush(QColor(C_GREEN + "22")))
        for (cx, cy, bw, bh) in self._bboxes:
            rx = int(ox + (cx - bw/2) * sw)
            ry = int(oy + (cy - bh/2) * sh)
            rw = int(bw * sw)
            rh = int(bh * sh)
            p.drawRect(rx, ry, rw, rh)
            p.setPen(QPen(QColor(C_GREEN)))
            p.setFont(QFont("Malgun Gothic", 9, QFont.Bold))
            p.drawText(rx + 4, ry + 13, "BBOX")
            p.setPen(QPen(QColor(C_GREEN), 2, Qt.SolidLine))

        # ORB 특징점 (빨간 점)
        p.setPen(Qt.NoPen)
        p.setBrush(QBrush(QColor(C_RED)))
        for nx, ny in self._kp_list:
            p.drawEllipse(int(ox + nx*sw) - 3, int(oy + ny*sh) - 3, 6, 6)

        # ROI 박스 (파란 점선)
        p.setPen(QPen(QColor(C_BLUE), 2, Qt.DashLine))
        p.setBrush(QBrush(QColor(C_BLUE + "33")))
        for i, r in enumerate(self._rois):
            rx = int(ox + r.x() * sw); ry = int(oy + r.y() * sh)
            rw = int(r.width() * sw);  rh = int(r.height() * sh)
            p.drawRect(rx, ry, rw, rh)
            p.setPen(QPen(QColor(C_BLUE)))
            p.setFont(QFont("Malgun Gothic", 10, QFont.Bold))
            p.drawText(rx + 4, ry + 14, f"ROI-{i+1}")
            p.setPen(QPen(QColor(C_BLUE), 2, Qt.DashLine))

    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton:
            self._origin = e.pos()
            from PyQt5.QtCore import QSize
            self._rubber.setGeometry(QRect(self._origin, QSize()))
            self._rubber.show()

    def mouseMoveEvent(self, e):
        if not self._rubber.isHidden():
            self._rubber.setGeometry(QRect(self._origin, e.pos()).normalized())

    def mouseReleaseEvent(self, e):
        if e.button() == Qt.LeftButton:
            self._rubber.hide()
            rect = QRect(self._origin, e.pos()).normalized()
            if rect.width() > 10 and rect.height() > 10 and self._base_pixmap:
                scaled = self._base_pixmap.scaled(
                    self.size(), Qt.KeepAspectRatio, Qt.SmoothTransformation)
                ox = (self.width()  - scaled.width())  // 2
                oy = (self.height() - scaled.height()) // 2
                sw, sh = scaled.width(), scaled.height()
                from PyQt5.QtCore import QRectF
                nx = max(0.0, (rect.x()-ox)/sw)
                ny = max(0.0, (rect.y()-oy)/sh)
                nw = min(rect.width()/sw,  1.0-nx)
                nh = min(rect.height()/sh, 1.0-ny)
                nr = QRectF(nx, ny, nw, nh)
                if self._bbox_mode:
                    self.bbox_added.emit(nr)   # BBOX 모드
                else:
                    self.roi_added.emit(nr)    # ROI 모드 (기존)


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  서브탭 B: 학습 데이터셋 뷰어 (타겟 뷰어 방식)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
class DatasetViewerTab(QWidget):
    """
    dataset_target_and_1cycle/data 에 있는 라벨드 이미지를
    타겟 뷰어처럼 선택 → 큰 이미지(YOLO bbox + ORB + ROI) 방식으로 표시합니다.
    """
    def __init__(self):
        super().__init__()
        self.setStyleSheet(f"background:{C_BG};")
        self._cur_fname   = ""
        self._roi_dict    = self._load_saved_rois() # { "fname": [QRectF], ... }
        self._rois        = []
        self._weight      = 3
        self._importance  = _load_importance()

        main = QHBoxLayout(self)
        main.setContentsMargins(12, 12, 12, 12); main.setSpacing(12)

        # ── 좌측: 이미지 목록 ───────────────────────────────
        left = QWidget(); left.setFixedWidth(200)
        left.setStyleSheet(f"background:{C_WHITE}; border:1px solid {C_BORDER}; border-radius:8px;")
        lv = QVBoxLayout(left); lv.setContentsMargins(10, 10, 10, 10); lv.setSpacing(8)
        lv.addWidget(self._lbl("🏷️  학습 데이터 목록", bold=True))
        self.img_list = QListWidget()
        self.img_list.setStyleSheet(
            f"QListWidget{{background:{C_BG};border:1px solid {C_BORDER};border-radius:4px;}}"
            f"QListWidget::item:selected{{background:#EBF5FB;color:{C_BLUE};}}")
        lv.addWidget(self.img_list)
        self.lbl_cnt = QLabel("0개 (라벨:0)")
        self.lbl_cnt.setStyleSheet(f"font-size:10px;color:{C_SUB};")
        lv.addWidget(self.lbl_cnt)
        btn_r = QPushButton("🔄 새로고침"); btn_r.clicked.connect(self._load_dataset)
        btn_add = QPushButton("📁 이미지 추가"); btn_add.clicked.connect(self._add_images)
        btn_add.setStyleSheet(f"background:{C_BLUE};color:white;border:none;border-radius:6px;font-weight:bold;")
        lv.addWidget(btn_r); lv.addWidget(btn_add)
        main.addWidget(left)

        # ── 중앙: 큰 이미지 뷰어 ─────────────────────────────
        center = QWidget()
        center.setStyleSheet(f"background:{C_WHITE}; border:1px solid {C_BORDER}; border-radius:8px;")
        cv_layout = QVBoxLayout(center); cv_layout.setContentsMargins(8, 8, 8, 8); cv_layout.setSpacing(6)
        cv_layout.addWidget(self._lbl(
            "🟢 초록 박스 = YOLO bbox  |  🔴 빨간 점 = ORB 특징점  |  🔵 파란 점선 = ROI", bold=False))
        self.viewer = DatasetImageViewer()
        self.viewer.roi_added.connect(self._on_roi_drawn)
        self.viewer.bbox_added.connect(self._on_bbox_drawn)   # BBOX 모드 시그널 연결
        cv_layout.addWidget(self.viewer, stretch=1)
        main.addWidget(center, stretch=1)

        # ── 우측: 중요도 + ROI 관리 패널 ──────────────────────
        right = QWidget(); right.setFixedWidth(240)
        right.setStyleSheet(f"background:{C_WHITE}; border:1px solid {C_BORDER}; border-radius:8px;")
        rv = QVBoxLayout(right); rv.setContentsMargins(10, 10, 10, 10); rv.setSpacing(8)

        # 중요도 설정
        rv.addWidget(self._lbl("⭐  중요도 설정", bold=True))
        w_row = QHBoxLayout(); w_row.setSpacing(6)
        btn_m = QPushButton("−"); btn_m.setFixedSize(32, 32)
        btn_m.setStyleSheet(f"background:{C_ORANGE};color:white;border:none;border-radius:6px;font-size:18px;font-weight:bold;")
        self.lbl_weight = QLabel("중요도: 3"); self.lbl_weight.setAlignment(Qt.AlignCenter)
        self.lbl_weight.setStyleSheet(f"font-size:14px;font-weight:bold;color:{C_DARK};")
        btn_p = QPushButton("+"); btn_p.setFixedSize(32, 32)
        btn_p.setStyleSheet(f"background:{C_BLUE};color:white;border:none;border-radius:6px;font-size:18px;font-weight:bold;")
        w_row.addWidget(btn_m); w_row.addWidget(self.lbl_weight, 1); w_row.addWidget(btn_p)
        rv.addLayout(w_row)
        btn_m.clicked.connect(lambda: self._change_weight(-1))
        btn_p.clicked.connect(lambda: self._change_weight(+1))

        btn_save_imp = QPushButton("💾 중요도 저장")
        btn_save_imp.setStyleSheet(f"background:{C_GREEN};color:white;border:none;border-radius:6px;font-weight:bold;")
        btn_save_imp.clicked.connect(self._save_importance)
        rv.addWidget(btn_save_imp)

        # 구분선
        sep = QFrame(); sep.setFrameShape(QFrame.HLine)
        sep.setStyleSheet(f"color:{C_BORDER};max-height:1px;"); rv.addWidget(sep)

        # ROI 관리
        rv.addWidget(self._lbl("🎯  ROI 설정 관리", bold=True))
        self.roi_list = QListWidget()
        self.roi_list.setStyleSheet(
            f"QListWidget{{background:{C_BG};border:1px solid {C_BORDER};border-radius:4px;font-size:11px;}}"
            f"QListWidget::item:selected{{background:#EBF5FB;color:{C_BLUE};}}")
        rv.addWidget(self.roi_list)
        btn_del = QPushButton("🗑  선택 ROI 삭제"); btn_del.clicked.connect(self._del_roi)
        btn_clr = QPushButton("🗑  전체 초기화");   btn_clr.clicked.connect(self._clear_roi)
        btn_sav = QPushButton("💾  ROI 저장 (핫리로드)")
        btn_sav.setStyleSheet(f"background:{C_GREEN};color:white;border:none;border-radius:6px;font-weight:bold;")
        btn_sav.clicked.connect(self._save_roi)
        rv.addWidget(btn_del); rv.addWidget(btn_clr); rv.addWidget(btn_sav)
        rv.addWidget(self._lbl("ℹ️ 이미지 위에서\n마우스를 드래그하여\nROI를 추가하세요.", bold=False))

        # ── BBOX 직접 편집 섹션 ────────────────────────
        sep2 = QFrame(); sep2.setFrameShape(QFrame.HLine)
        sep2.setStyleSheet(f"color:{C_BORDER};max-height:1px;"); rv.addWidget(sep2)
        rv.addWidget(self._lbl("📦  BBOX 직접 편집", bold=True))

        self.btn_bbox_mode = QPushButton("🎯 BBOX 추가 모드 OFF")
        self.btn_bbox_mode.setCheckable(True)
        self.btn_bbox_mode.setStyleSheet(
            f"QPushButton{{background:{C_BG};color:{C_DARK};border:1px solid {C_BORDER};border-radius:6px;"
            f"font-weight:bold;font-size:11px;}}"
            f"QPushButton:checked{{background:{C_RED};color:white;border:none;}}")
        self.btn_bbox_mode.clicked.connect(self._toggle_bbox_mode)
        rv.addWidget(self.btn_bbox_mode)

        self.bbox_list = QListWidget()
        self.bbox_list.setMaximumHeight(70)
        self.bbox_list.setStyleSheet(
            f"QListWidget{{background:{C_BG};border:1px solid {C_BORDER};border-radius:4px;font-size:10px;}}"
            f"QListWidget::item:selected{{background:#FEF9E7;color:{C_ORANGE};}}")
        rv.addWidget(self.bbox_list)
        btn_del_bbox = QPushButton("🗑 선택 BBOX 삭제")
        btn_del_bbox.clicked.connect(self._del_bbox)
        btn_clr_bbox = QPushButton("🗑 전체 BBOX 초기화")
        btn_clr_bbox.clicked.connect(self._clear_bbox)
        btn_sav_bbox = QPushButton("💾 BBOX TXT 저장")
        btn_sav_bbox.setStyleSheet(
            f"background:{C_ORANGE};color:white;border:none;border-radius:6px;font-weight:bold;")
        btn_sav_bbox.clicked.connect(self._save_bbox_to_txt)
        rv.addWidget(btn_del_bbox); rv.addWidget(btn_clr_bbox); rv.addWidget(btn_sav_bbox)

        rv.addStretch()
        main.addWidget(right)

        # 목록 클릭 연결
        self.img_list.currentItemChanged.connect(
            lambda cur, _: self._load_image(cur.text() if cur else ""))
        QTimer.singleShot(400, self._load_dataset)

    # ── 헬퍼 ─────────────────────────────────────────────
    def _lbl(self, t, bold=False):
        l = QLabel(t); l.setWordWrap(True)
        l.setStyleSheet(f"font-size:12px;{'font-weight:bold;' if bold else ''}color:{C_DARK};")
        return l

    # ── 데이터 로딩 ──────────────────────────────────────
    def _load_dataset(self):
        self.img_list.clear()
        if not os.path.isdir(LABELED_DATA_DIR):
            self.lbl_cnt.setText("폴더 없음"); return
        imgs = sorted([f for f in os.listdir(LABELED_DATA_DIR)
                       if f.lower().endswith((".jpg", ".png"))])
        labeled = [f for f in imgs if os.path.exists(
            os.path.join(LABELED_DATA_DIR, os.path.splitext(f)[0] + ".txt"))]
        for f in imgs:
            self.img_list.addItem(f)
        self.lbl_cnt.setText(f"{len(imgs)}개 (라벨:{len(labeled)})")
        if self.img_list.count() > 0:
            self.img_list.setCurrentRow(0)
            first = self.img_list.item(0)
            if first:
                self._load_image(first.text())

    def _load_image(self, fname):
        if not fname: return
        self._cur_fname = fname
        img_path = os.path.join(LABELED_DATA_DIR, fname)
        txt_path = os.path.join(LABELED_DATA_DIR, os.path.splitext(fname)[0] + ".txt")
        
        # UI 리스트 및 데이터 동기화
        self._rois = self._roi_dict.get(fname, []).copy()
        
        self.roi_list.clear()
        for i, r in enumerate(self._rois):
            self.roi_list.addItem(f"ROI-{i+1}: ({r.x():.2f},{r.y():.2f}) {r.width():.2f}×{r.height():.2f}")

        # 중요도 복원
        self._weight = self._importance.get(fname, 3)
        self.lbl_weight.setText(f"중요도: {self._weight}")
        # 이미지 + bbox 로드
        self.viewer.load_image(img_path, txt_path if os.path.exists(txt_path) else None)
        self.viewer.set_rois(self._rois)
        # BBOX 리스트 동기화 (이미지 전환 시 자동 반영)
        self.bbox_list.clear()
        for i, (cx, cy, bw, bh) in enumerate(self.viewer._bboxes):
            self.bbox_list.addItem(f"BBOX-{i+1}: cx={cx:.3f} cy={cy:.3f} w={bw:.3f} h={bh:.3f}")

    def _add_images(self):
        from PyQt5.QtWidgets import QFileDialog
        paths, _ = QFileDialog.getOpenFileNames(self, "이미지 선택", _ROOT, "이미지 (*.jpg *.png)")
        os.makedirs(LABELED_DATA_DIR, exist_ok=True)
        for p in paths:
            import shutil; shutil.copy2(p, LABELED_DATA_DIR)
        self._load_dataset()

    # ── 중요도 ────────────────────────────────────────────
    def _change_weight(self, d):
        self._weight = max(1, min(10, self._weight + d))
        self.lbl_weight.setText(f"중요도: {self._weight}")
        # 현재 파일명을 키로 즉시 저장
        if self._cur_fname:
            self._importance[self._cur_fname] = self._weight

    def _save_importance(self):
        _save_importance(self._importance)
        from PyQt5.QtWidgets import QMessageBox
        QMessageBox.information(self, "저장 완료", f"중요도 설정 {len(self._importance)}개가 저장되었습니다.")

    # ── ROI 관리 ──────────────────────────────────────────
    def _on_roi_drawn(self, nr):
        self._rois.append(nr)
        if self._cur_fname: self._roi_dict[self._cur_fname] = self._rois
        self.roi_list.addItem(f"ROI-{len(self._rois)}: ({nr.x():.2f},{nr.y():.2f}) {nr.width():.2f}×{nr.height():.2f}")
        self.viewer.set_rois(self._rois)

    def _del_roi(self):
        row = self.roi_list.currentRow()
        if row >= 0:
            self._rois.pop(row); self.roi_list.takeItem(row)
            if self._cur_fname: self._roi_dict[self._cur_fname] = self._rois
            self.viewer.set_rois(self._rois)

    def _clear_roi(self):
        self._rois.clear(); self.roi_list.clear()
        if self._cur_fname: self._roi_dict[self._cur_fname] = self._rois
        self.viewer.set_rois(self._rois)

    def _save_roi(self):
        roi_file = os.path.join(_ROOT, "data", "dataset_roi_config.json")
        os.makedirs(os.path.dirname(roi_file), exist_ok=True)
        # 딕셔너리 형태로 모두 저장
        saved_dict = {}
        for fname, rois in self._roi_dict.items():
            saved_dict[fname] = [{"x": r.x(), "y": r.y(), "w": r.width(), "h": r.height()} for r in rois]
            
        with open(roi_file, "w") as f:
            import json; json.dump(saved_dict, f, indent=2)
        from PyQt5.QtWidgets import QMessageBox
        QMessageBox.information(self, "저장 완료", f"모든 데이터셋 이미지의 다중 ROI가 성공적으로 저장되었습니다.\n{roi_file}")

    def _load_saved_rois(self):
        roi_file = os.path.join(_ROOT, "data", "dataset_roi_config.json")
        roi_dict_loaded = {}
        if not os.path.exists(roi_file): return roi_dict_loaded
        try:
            import PyQt5.QtCore as _qc
            import json
            with open(roi_file) as f: data = json.load(f)
            if isinstance(data, list):
                pass
            elif isinstance(data, dict):
                for fname, rlist in data.items():
                    roi_dict_loaded[fname] = [_qc.QRectF(d["x"],d["y"],d["w"],d["h"]) for d in rlist]
        except: pass
        return roi_dict_loaded

    # ── BBOX 직접 편집 메서드 ───────────────────────────────────
    def _toggle_bbox_mode(self, checked: bool):
        """토글 ON → BBOX 모드, OFF → ROI 모드"""
        self.viewer._bbox_mode = checked
        self.btn_bbox_mode.setText(
            "🎯 BBOX 추가 모드 ON" if checked else "🎯 BBOX 추가 모드 OFF")

    def _on_bbox_drawn(self, nr):
        """YOLO cx,cy,w,h 형식으로 변환하여 viewer에 추가"""
        cx = nr.x() + nr.width()  / 2.0
        cy = nr.y() + nr.height() / 2.0
        bw, bh = nr.width(), nr.height()
        self.viewer._bboxes.append((cx, cy, bw, bh))
        self.bbox_list.addItem(
            f"BBOX-{len(self.viewer._bboxes)}: cx={cx:.3f} cy={cy:.3f} w={bw:.3f} h={bh:.3f}")
        self.viewer.repaint()

    def _del_bbox(self):
        row = self.bbox_list.currentRow()
        if row >= 0:
            self.viewer._bboxes.pop(row)
            self.bbox_list.takeItem(row)
            self.viewer.repaint()

    def _clear_bbox(self):
        self.viewer._bboxes.clear(); self.bbox_list.clear(); self.viewer.repaint()

    def _save_bbox_to_txt(self):
        """현재 BBOX 목록을 YOLO 형식 .txt로 덮어쓰기"""
        if not self._cur_fname:
            QMessageBox.warning(self, "충고", "먼저 이미지를 선택해주세요."); return
        if not self.viewer._bboxes:
            QMessageBox.warning(
                self, "충고", "BBOX가 없습니다.\nBBOX 모드 켜고 드래그하세요."); return
        txt_path = os.path.join(
            LABELED_DATA_DIR, os.path.splitext(self._cur_fname)[0] + ".txt")
        try:
            with open(txt_path, "w", encoding="utf-8") as f:
                for (cx, cy, bw, bh) in self.viewer._bboxes:
                    f.write(f"0 {cx:.6f} {cy:.6f} {bw:.6f} {bh:.6f}\n")
            QMessageBox.information(
                self, "저장 완료",
                f"BBOX {len(self.viewer._bboxes)}개 저장 완료\n{txt_path}")
            self._load_dataset()   # 라벨 카운트 수 갱신
        except Exception as ex:
            QMessageBox.critical(self, "저장 실패", str(ex))




# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  하단 격발 바 (YOLO / ORB 분리)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
class FireBar(QWidget):
    def __init__(self, pending_tab: PendingReviewTab):
        super().__init__()
        self._pending_tab = pending_tab
        self._yolo_thread = None
        self._orb_thread  = None
        self.setFixedHeight(185)   # 모드/모델 선택 UI 추가로 높이 확장
        self.setStyleSheet(f"background:{C_WHITE};border:1px solid {C_BORDER};border-radius:8px;")

        main_h = QHBoxLayout(self)
        main_h.setContentsMargins(14, 8, 14, 8)
        main_h.setSpacing(12)

        # ── 왼쪽: 진행률 마 + 상태레이블 ─────────────────────
        left = QVBoxLayout()
        self.lbl = QLabel("✅  학습 버튼을 눌러 시작하세요.")
        self.lbl.setStyleSheet(f"font-size:11px;color:{C_SUB};")
        self.prog = QProgressBar()
        self.prog.setRange(0, 100); self.prog.setValue(0)
        self.prog.setFixedHeight(10); self.prog.setTextVisible(True)
        self.prog.setStyleSheet(
            f"QProgressBar{{background:{C_BORDER};border-radius:5px;text-align:center;font-size:10px;}}"
            f"QProgressBar::chunk{{background:{C_GREEN};border-radius:5px;}}")
        left.addWidget(self.lbl)
        left.addWidget(self.prog)
        main_h.addLayout(left, stretch=1)

        # ── 중간: 실시간 지표 패널 ───────────────────────
        metric_frame = QFrame()
        metric_frame.setFixedWidth(480)
        metric_frame.setStyleSheet(f"background:{C_BG};border-radius:6px;border:1px solid {C_BORDER};")
        mg = QGridLayout(metric_frame)
        mg.setContentsMargins(10, 6, 10, 6); mg.setSpacing(4)

        def _metric_label(title, key):
            t = QLabel(title)
            t.setStyleSheet(f"font-size:10px;color:{C_SUB};font-weight:bold;")
            v = QLabel("-")
            v.setStyleSheet(f"font-size:12px;color:{C_DARK};font-weight:bold;")
            return t, v

        t1,self.m_P   = _metric_label("Precision",  "P")
        t2,self.m_R   = _metric_label("Recall",     "R")
        t3,self.m_50  = _metric_label("mAP50",      "mAP50")
        t4,self.m_595 = _metric_label("mAP50-95",   "mAP50_95")
        t5,self.m_box = _metric_label("Box Loss",   "box_loss")
        t6,self.m_ep  = _metric_label("Epoch",      "epoch")

        mg.addWidget(t1, 0,0); mg.addWidget(self.m_P,   1,0)
        mg.addWidget(t2, 0,1); mg.addWidget(self.m_R,   1,1)
        mg.addWidget(t3, 0,2); mg.addWidget(self.m_50,  1,2)
        mg.addWidget(t4, 0,3); mg.addWidget(self.m_595, 1,3)
        mg.addWidget(t5, 0,4); mg.addWidget(self.m_box, 1,4)
        mg.addWidget(t6, 0,5); mg.addWidget(self.m_ep,  1,5)
        main_h.addWidget(metric_frame)

        # ── 오른쪽: 학습 모드 + 모델 선택 + 실행 버튼 ──────────────
        from PyQt5.QtWidgets import QRadioButton, QButtonGroup, QComboBox
        btn_col = QVBoxLayout(); btn_col.setSpacing(5)

        # ① 학습 모드 (Radio)
        mode_lbl = QLabel("📚 학습 모드")
        mode_lbl.setStyleSheet(f"font-size:10px;font-weight:bold;color:{C_SUB};")
        btn_col.addWidget(mode_lbl)
        mode_row = QHBoxLayout(); mode_row.setSpacing(6)
        self._radio_scratch = QRadioButton("⭕ 처음부터")
        self._radio_resume  = QRadioButton("🔄 이어서")
        self._radio_resume.setChecked(True)   # 기본: 이어서
        for rb in [self._radio_scratch, self._radio_resume]:
            rb.setStyleSheet(f"font-size:11px;color:{C_DARK};")
        self._mode_group = QButtonGroup(self)
        self._mode_group.addButton(self._radio_scratch, 0)
        self._mode_group.addButton(self._radio_resume,  1)
        mode_row.addWidget(self._radio_scratch)
        mode_row.addWidget(self._radio_resume)
        btn_col.addLayout(mode_row)

        # ② YOLO 모델 선택
        model_lbl = QLabel("🧠 YOLO 모델 선택")
        model_lbl.setStyleSheet(f"font-size:10px;font-weight:bold;color:{C_SUB};")
        btn_col.addWidget(model_lbl)
        model_row = QHBoxLayout(); model_row.setSpacing(4)
        self.combo_model = QComboBox()
        self.combo_model.setStyleSheet(
            f"QComboBox{{background:{C_BG};border:1px solid {C_BORDER};border-radius:4px;"
            f"font-size:10px;padding:2px;}}")
        self.combo_model.setMinimumWidth(140)
        btn_refresh_m = QPushButton("🔄")
        btn_refresh_m.setFixedSize(26, 24)
        btn_refresh_m.setToolTip("모델 목록 새로고침")
        btn_refresh_m.setStyleSheet(
            f"background:{C_BG};border:1px solid {C_BORDER};border-radius:4px;font-size:11px;")
        btn_refresh_m.clicked.connect(self._scan_models)
        model_row.addWidget(self.combo_model, 1)
        model_row.addWidget(btn_refresh_m)
        btn_col.addLayout(model_row)
        btn_apply = QPushButton("✅ 이 모델을 추론에 적용")
        btn_apply.setFixedHeight(24)
        btn_apply.setStyleSheet(
            f"background:{C_GREEN};color:white;border:none;border-radius:4px;"
            f"font-size:10px;font-weight:bold;")
        btn_apply.clicked.connect(self._apply_model)
        btn_col.addWidget(btn_apply)

        # ③ 실행 버튼
        fire_row = QHBoxLayout(); fire_row.setSpacing(6)
        btn_yolo = QPushButton("🎯 YOLO 50에폭")
        btn_yolo.setFixedHeight(36)
        btn_yolo.setStyleSheet(
            f"QPushButton{{background:{C_BLUE};color:white;font-size:11px;font-weight:bold;"
            f"border:none;border-radius:6px;}}QPushButton:hover{{background:#2874A6;}}")
        btn_yolo.clicked.connect(self._fire_yolo)
        btn_rollback = QPushButton("📂 롤백")
        btn_rollback.setFixedHeight(36)
        btn_rollback.setToolTip(".pt 파일을 선택해 추론 모델로 롤백")
        btn_rollback.setStyleSheet(
            f"QPushButton{{background:{C_ORANGE};color:white;font-size:11px;font-weight:bold;"
            f"border:none;border-radius:6px;}}QPushButton:hover{{background:#D35400;}}")
        btn_rollback.clicked.connect(self._rollback_model)
        btn_orb = QPushButton("⚙️ ORB")
        btn_orb.setFixedHeight(36)
        btn_orb.setStyleSheet(
            f"QPushButton{{background:{C_GREEN};color:white;font-size:11px;font-weight:bold;"
            f"border:none;border-radius:6px;}}QPushButton:hover{{background:#1E8449;}}")
        btn_orb.clicked.connect(self._fire_orb)
        fire_row.addWidget(btn_yolo)
        fire_row.addWidget(btn_rollback)
        fire_row.addWidget(btn_orb)
        btn_col.addLayout(fire_row)
        main_h.addLayout(btn_col)

        # 실행 시 모델 목록 자동 스캔
        QTimer.singleShot(200, self._scan_models)

    def _update_metrics(self, m: dict):
        """metric_signal 수신 시 지표 패널 업데이트"""
        self.m_P  .setText(f"{m.get('P',0):.3f}")
        self.m_R  .setText(f"{m.get('R',0):.3f}")
        self.m_50 .setText(f"{m.get('mAP50',0):.3f}")
        self.m_595.setText(f"{m.get('mAP50_95',0):.3f}")
        self.m_box.setText(f"{m.get('box_loss',0):.4f}")
        self.m_ep .setText(f"{m.get('epoch',0)}/{m.get('total',50)}")
        # mAP50 수준에 따라 색상 피드백
        c = C_GREEN if m.get('mAP50',0) >= 0.5 else (C_ORANGE if m.get('mAP50',0) >= 0.2 else C_RED)
        self.m_50.setStyleSheet(f"font-size:12px;color:{c};font-weight:bold;")

    def _fire_yolo(self):
        """선택된 모드로 YOLO 학습 시작"""
        mode = 'scratch' if self._radio_scratch.isChecked() else 'resume'
        mode_label = '처음부터 (yolov8n.pt)' if mode == 'scratch' else '이어서 (best.pt)'
        self.prog.setValue(0)
        self.lbl.setText(f"🎯  YOLO 재학습 시작... [{mode_label}]")
        for w in [self.m_P, self.m_R, self.m_50, self.m_595, self.m_box, self.m_ep]:
            w.setText("-")
        self._yolo_thread = YoloTrainThread(epochs=50, mode=mode)
        self._yolo_thread.progress.connect(self._on_prog)
        self._yolo_thread.metric_signal.connect(self._update_metrics)
        self._yolo_thread.finished.connect(self._on_yolo_done)
        self._yolo_thread.start()

    def _on_yolo_done(self, r):
        m = r.get("metrics", {})
        QMessageBox.information(
            self, "YOLO 학습 완료",
            f"재학습 완료!\n"
            f"mAP50: {m.get('map50',r.get('map50',0)):.3f}\n"
            f"mAP50-95: {m.get('map50_95',0):.3f}\n"
            f"Precision: {m.get('precision',0):.3f}\n"
            f"Recall: {m.get('recall',0):.3f}\n"
            f"Box Loss: {m.get('box_loss',0):.4f}"
        )

    def _fire_orb(self):
        td = os.path.join(_ROOT,"dataset_target_and_1cycle","target_image")
        t_paths = [os.path.join(td,f) for f in os.listdir(td)
                   if f.lower().endswith((".jpg",".png"))] if os.path.isdir(td) else []
        p_paths = self._pending_tab.get_pending_paths()
        if not t_paths:
            QMessageBox.warning(self,"경고","타겟 폴더가 비어 있습니다!"); return
        if not p_paths:
            QMessageBox.warning(self,"경고","Pending 이미지가 없습니다!\nTrack A를 먼저 실행하세요."); return
        self.prog.setValue(0); self.lbl.setText("⚙️  ORB 파라미터 탐색 시작...")
        self._orb_thread = OrbTunerThread(t_paths, p_paths, n_trials=30)
        self._orb_thread.progress.connect(self._on_prog)
        self._orb_thread.finished.connect(lambda bp,bs: QMessageBox.information(
            self,"ORB 완료",f"ORB 최적화 완료!\n점수: {bs:.2f}\n[결재 탭]에서 승인하세요."))
        self._orb_thread.start()

    def _on_prog(self, pct, msg):
        if pct >= 0:
            self.prog.setValue(min(pct, 100))
            self.lbl.setText(msg)
        else:
            self.lbl.setText(f"❌  {msg}")
            self.prog.setStyleSheet(
                f"QProgressBar{{background:{C_BORDER};border-radius:5px;}}"
                f"QProgressBar::chunk{{background:{C_RED};border-radius:5px;}}")

    # ── 모델 관리 메서드 ───────────────────────────────────
    def _scan_models(self):
        """모델/ 폴더를 순회하여 .pt 파일 목록을 ComboBox에 채움"""
        models_dir = os.path.join(_ROOT, "models")
        self.combo_model.clear()
        pt_files = []
        if os.path.isdir(models_dir):
            for root, dirs, files in os.walk(models_dir):
                for f in files:
                    if f.endswith(".pt"):
                        rel = os.path.relpath(os.path.join(root, f), _ROOT)
                        pt_files.append(rel)
        # 루트의 yolov8n.pt도 추가
        base = os.path.join(_ROOT, "yolov8n.pt")
        if os.path.exists(base):
            pt_files.insert(0, "yolov8n.pt")
        for p in pt_files:
            self.combo_model.addItem(p)
        # 현재 적용중인 데이터를 선택
        active_file = os.path.join(_ROOT, "data", "active_model.json")
        if os.path.exists(active_file):
            try:
                import json
                with open(active_file) as af:
                    active = json.load(af).get("path", "")
                idx = self.combo_model.findText(active)
                if idx >= 0:
                    self.combo_model.setCurrentIndex(idx)
            except Exception:
                pass

    def _apply_model(self):
        """선택한 모델을 추론용 활성 모델로 저장 (다음 영상 시작 시 적용)"""
        sel = self.combo_model.currentText()
        if not sel:
            QMessageBox.warning(self, "충고", "선택된 모델이 없습니다."); return
        active_file = os.path.join(_ROOT, "data", "active_model.json")
        os.makedirs(os.path.dirname(active_file), exist_ok=True)
        import json
        with open(active_file, "w") as f:
            json.dump({"path": sel}, f, indent=2)
        QMessageBox.information(
            self, "적용 완료",
            f"추론 모델이 \'{sel}'로 설정되었습니다.\n"
            f"다음 업 시작 시 자동으로 적용됩니다.")

    def _rollback_model(self):
        """임의 .pt 파일을 선택하여 추론 모델로 롤백"""
        from PyQt5.QtWidgets import QFileDialog
        fpath, _ = QFileDialog.getOpenFileName(
            self, "롤백할 YOLO 모델 선택",
            os.path.join(_ROOT, "models"),
            "YOLO 모델 (*.pt)")
        if not fpath:
            return
        rel = os.path.relpath(fpath, _ROOT)
        active_file = os.path.join(_ROOT, "data", "active_model.json")
        os.makedirs(os.path.dirname(active_file), exist_ok=True)
        import json
        with open(active_file, "w") as f:
            json.dump({"path": rel}, f, indent=2)
        # ComboBox에 없으면 추가
        if self.combo_model.findText(rel) < 0:
            self.combo_model.insertItem(0, rel)
        self.combo_model.setCurrentText(rel)
        QMessageBox.information(
            self, "롤백 완료",
            f"추론 모델이 \'{rel}'로 롤백되었습니다.")
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
class TrainingTab(QWidget):
    def __init__(self):
        super().__init__()
        self.setStyleSheet(f"background:{C_BG};")
        v = QVBoxLayout(self); v.setContentsMargins(0,0,0,0); v.setSpacing(0)

        sub = QTabWidget()
        sub.setStyleSheet(f"""
            QTabWidget::pane{{border:none;background:{C_BG};}}
            QTabBar::tab{{background:{C_BG};color:{C_SUB};padding:8px 18px;
                border:1px solid {C_BORDER};border-bottom:none;border-radius:4px 4px 0 0;margin-right:2px;}}
            QTabBar::tab:selected{{background:{C_WHITE};color:{C_BLUE};border-bottom:2px solid {C_BLUE};}}
        """)

        self._pending_tab  = PendingReviewTab()
        self._dataset_tab  = DatasetViewerTab()
        sub.addTab(self._pending_tab,  "  📋  Pending 검수실  ")
        sub.addTab(self._dataset_tab,  "  🏷️  학습 데이터셋 뷰어  ")
        v.addWidget(sub, stretch=1)

        # 격발 바 (Pending 탭 참조)
        fire = FireBar(self._pending_tab)
        outer = QWidget()
        outer.setStyleSheet(f"background:{C_BG};")
        ov = QVBoxLayout(outer); ov.setContentsMargins(12,6,12,8)
        ov.addWidget(fire)
        v.addWidget(outer)
