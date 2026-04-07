"""
tab_training.py — TAB 2: 야간 학습 지시 및 데이터 검수실 (Track B)
서브탭 구성:
  [서브탭 A] Pending 검수  : Track A에서 자동 캡처된 이미지 (개별 가중치 기본=3)
  [서브탭 B] 학습 데이터셋 : data/yolo_source의 실제 라벨드 데이터 그리드 뷰어
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
LABELED_DATA_DIR = os.path.join(_ROOT, "data", "yolo_source")
PENDING_DIR      = os.path.join(_ROOT, "data", "pending")

# ─── 샴 네트워크 라벨링 데이터 경로 ────────────────────────────────────────────
SIAMESE_TRAIN_DIR = os.path.join(_ROOT, "data", "siamese_train")
SIAMESE_QUEUE_DIR = os.path.join(SIAMESE_TRAIN_DIR, "_queue")

# (폴더키, 버튼 텍스트, 색상)
_LABEL_META = [
    ("1",   "🟢  1번 타겟",  "#27AE60"),
    ("2",   "🔵  2번 타겟",  "#3498DB"),
    ("3",   "🟡  3번 타겟",  "#D4AC0D"),
    ("4",   "🟠  4번 타겟",  "#E67E22"),
    ("neg", "❌  정답아님",  "#E74C3C"),
]

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
class SiameseTrainThread(QThread):
    """train_siamese.py 를 subprocess로 실행. [PCT%] 라인을 파싱해 progress 시그널 발행."""
    progress = pyqtSignal(int, str)
    finished = pyqtSignal(bool, str)

    def run(self):
        import subprocess, sys
        python_exe = sys.executable
        script     = os.path.join(_ROOT, "scripts", "train_siamese.py")
        self.progress.emit(3, "샴 네트워크 학습 프로세스 시작 중...")
        try:
            proc = subprocess.Popen(
                [python_exe, "-u", script],
                cwd=_ROOT,
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                text=True, encoding="utf-8", errors="replace",
            )
            err_log = []
            for line in iter(proc.stdout.readline, ""):
                line = line.strip()
                if not line:
                    continue
                if line.startswith("[") and "%]" in line:
                    try:
                        pct = int(float(line[1:line.index("%]")]))
                        msg = line[line.index("%]")+2:].strip()
                        self.progress.emit(pct, msg)
                    except Exception:
                        err_log.append(line)
                else:
                    err_log.append(line)

            proc.wait()
            if proc.returncode == 0:
                self.progress.emit(100, "✅ 샴 학습 완료!")
                self.finished.emit(True, "siamese_finetuned.pt 저장 완료")
            else:
                err_text = "\n".join(err_log[-8:]) # 마지막 8줄 캡처
                self.progress.emit(-1, f"비정상 종료 (코드: {proc.returncode})")
                self.finished.emit(False, f"오류 코드: {proc.returncode}\n{err_text}")
        except Exception as e:
            self.progress.emit(-1, f"실행 오류: {e}")
            self.finished.emit(False, str(e))


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
    deleted = pyqtSignal(str)  # 삭제 버튼 클릭 시 img_path 방출

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

        # Hard Neg + 삭제 버튼 행
        del_row = QHBoxLayout(); del_row.setSpacing(2)
        self.btn_hn = QPushButton("Hard Neg")
        self.btn_hn.setFixedHeight(22)
        self.btn_hn.setStyleSheet(f"font-size:9px;background:{C_BG};color:{C_RED};border:1px solid {C_RED};border-radius:4px;")
        self.btn_hn.clicked.connect(self._toggle_hn)
        btn_del = QPushButton("×")
        btn_del.setFixedSize(22, 22)
        btn_del.setToolTip("이 이미지 삭제")
        btn_del.setStyleSheet(f"background:{C_RED};color:white;border:none;border-radius:4px;font-weight:bold;font-size:13px;")
        btn_del.clicked.connect(self._delete)
        del_row.addWidget(self.btn_hn, 1)
        del_row.addWidget(btn_del)
        v.addLayout(del_row)

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
            self.btn_hn.setText("Hard Neg ✓")
        else:
            self.setStyleSheet(f"background:{C_WHITE};border-radius:8px;border:1px solid {C_ORANGE};")
            self.btn_hn.setText("Hard Neg")

    def _delete(self):
        try:
            os.remove(self.img_path)
        except Exception as ex:
            print(f"[ImageCard] 삭제 실패: {self.img_path} ({ex})")
        self.deleted.emit(self.img_path)


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

        btn_del_all = QPushButton("전체 삭제")
        btn_del_all.setStyleSheet(f"background:{C_RED};color:white;border:none;border-radius:6px;font-weight:bold;padding:8px 12px;")
        btn_del_all.clicked.connect(self._delete_all)
        h.addWidget(btn_del_all)

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
            "• Hard Neg: 절대 오답\n  케이스로 강제 학습\n\n"
            "• 높은 중요도 이미지는\n  학습 시 더 많이 반복\n  노출됩니다.\n\n"
            "• ×: 해당 이미지 파일\n  즉시 삭제"
        )
        guide.setWordWrap(True)
        guide.setStyleSheet(f"font-size:11px;color:{C_SUB};")
        v.addWidget(guide)
        v.addStretch()

        # matched 폴더 관리
        self.btn_clear_matched = QPushButton("matched 폴더 비우기")
        self.btn_clear_matched.setStyleSheet(
            f"background:{C_ORANGE};color:white;border:none;border-radius:6px;"
            f"font-size:11px;font-weight:bold;padding:6px;"
        )
        self.btn_clear_matched.clicked.connect(self._clear_matched)
        self._refresh_matched_label()
        v.addWidget(self.btn_clear_matched)

        btn_save = QPushButton("중요도 설정 저장")
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
        files = sorted([f for f in os.listdir(PENDING_DIR) if f.lower().endswith((".jpg",".png"))])[:60]
        for i, f in enumerate(files):
            path = os.path.join(PENDING_DIR, f)
            w_default = imp.get(f, 3)
            card = ImageCard(path, "ambiguous", default_weight=w_default)
            card.deleted.connect(self._on_card_delete)
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

    def _on_card_delete(self, img_path: str):
        """개별 카드 × 버튼 → 카드 위젯 제거 + 카운트 갱신"""
        card = next((c for c in self._cards if c.img_path == img_path), None)
        if card:
            self._cards.remove(card)
            card.deleteLater()
        self.lbl_cnt.setText(f"{len(self._cards)}개")

    def _delete_all(self):
        cnt = len(self._cards)
        if cnt == 0:
            QMessageBox.information(self, "알림", "삭제할 이미지가 없습니다.")
            return
        reply = QMessageBox.question(
            self, "전체 삭제 확인",
            f"pending 폴더의 이미지 {cnt}개를 모두 삭제합니다.\n이 작업은 되돌릴 수 없습니다.",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No
        )
        if reply != QMessageBox.Yes:
            return
        failed = 0
        for c in self._cards:
            try:
                os.remove(c.img_path)
            except Exception:
                failed += 1
            c.deleteLater()
        self._cards.clear()
        self.lbl_cnt.setText("0개")
        msg = f"{cnt}개 삭제 완료."
        if failed:
            msg += f" (실패 {failed}개)"
        QMessageBox.information(self, "삭제 완료", msg)

    def _refresh_matched_label(self):
        matched_dir = os.path.join(_ROOT, "data", "matched")
        cnt = len([f for f in os.listdir(matched_dir) if f.lower().endswith((".jpg",".png"))]) \
              if os.path.isdir(matched_dir) else 0
        self.btn_clear_matched.setText(f"matched 폴더 비우기 ({cnt}개)")

    def _clear_matched(self):
        matched_dir = os.path.join(_ROOT, "data", "matched")
        if not os.path.isdir(matched_dir):
            return
        files = [f for f in os.listdir(matched_dir) if f.lower().endswith((".jpg",".png"))]
        if not files:
            QMessageBox.information(self, "알림", "matched 폴더가 이미 비어 있습니다.")
            return
        reply = QMessageBox.question(
            self, "matched 삭제 확인",
            f"matched 폴더의 이미지 {len(files)}개를 모두 삭제합니다.",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No
        )
        if reply != QMessageBox.Yes:
            return
        for f in files:
            try:
                os.remove(os.path.join(matched_dir, f))
            except Exception:
                pass
        self._refresh_matched_label()
        QMessageBox.information(self, "완료", f"{len(files)}개 삭제 완료.")

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
    seg_added  = pyqtSignal(object)   # list of (nx, ny) 시그널 — Seg 모드일 때 발행

    def __init__(self):
        super().__init__()
        self.setAlignment(Qt.AlignCenter)
        self.setMinimumSize(400, 300)
        self.setStyleSheet("background:#1a1a2e; border-radius:6px;")
        self._base_pixmap = None
        self._kp_list  = []   # 정규화 ORB 키포인트
        self._bboxes   = []   # 정규화 YOLO bbox [(cx,cy,w,h), ...]
        self._polygons = []   # 정규화 폴리곤 [[(x1,y1), ...], ...]
        self._cur_poly = []   # 현재 그리는 중인 폴리곤
        self._rois     = []   # 정규화 QRectF ROI 목록
        self._origin   = QPoint()
        self._seg_mode = False   # True → 클릭 4번으로 사다리꼴(다각형) 추가 (False이면 드래그 ROI 모드)
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

        # 1. YOLO bbox / seg 파싱
        self._bboxes = []
        self._polygons = []
        self._cur_poly = []
        if txt_path and os.path.exists(txt_path):
            try:
                with open(txt_path) as f:
                    for line in f:
                        parts = line.strip().split()
                        if len(parts) == 5:
                            _, cx, cy, bw, bh = map(float, parts)
                            self._bboxes.append((cx, cy, bw, bh))
                        elif len(parts) > 5:
                            pts = []
                            for i in range(1, len(parts), 2):
                                pts.append((float(parts[i]), float(parts[i+1])))
                            self._polygons.append(pts)
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
        elif self._polygons:
            # 첫 번째 폴리곤 기준
            pts = self._polygons[0]
            x_min = max(0, int(min(p[0] for p in pts) * w))
            y_min = max(0, int(min(p[1] for p in pts) * h))
            x_max = min(w, int(max(p[0] for p in pts) * w))
            y_max = min(h, int(max(p[1] for p in pts) * h))
        else:
            x_min = y_min = x_max = y_max = 0
            
        if self._bboxes or self._polygons:
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

        # 세그멘테이션 다각형 (초록 실선)
        p.setPen(QPen(QColor(C_ORANGE), 2, Qt.SolidLine))
        p.setBrush(QBrush(QColor(C_ORANGE + "33")))
        from PyQt5.QtGui import QPolygonF
        from PyQt5.QtCore import QPointF
        for poly in self._polygons:
            qpoly = QPolygonF()
            for nx, ny in poly:
                qpoly.append(QPointF(ox + nx*sw, oy + ny*sh))
            p.drawPolygon(qpoly)
            p.setBrush(Qt.NoBrush)
            for nx, ny in poly:
                p.drawEllipse(int(ox + nx*sw) - 3, int(oy + ny*sh) - 3, 6, 6)
            p.setBrush(QBrush(QColor(C_ORANGE + "33")))

        # 현재 그리는 중인 다각형
        if self._cur_poly:
            p.setPen(QPen(QColor(C_ORANGE), 2, Qt.DashLine))
            p.setBrush(Qt.NoBrush)
            qpoly = QPolygonF()
            for nx, ny in self._cur_poly:
                qpoly.append(QPointF(ox + nx*sw, oy + ny*sh))
            p.drawPolyline(qpoly)
            for nx, ny in self._cur_poly:
                p.drawEllipse(int(ox + nx*sw) - 3, int(oy + ny*sh) - 3, 6, 6)

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
        if self._seg_mode:
            if not self._base_pixmap: return
            if e.button() == Qt.LeftButton:
                scaled = self._base_pixmap.scaled(self.size(), Qt.KeepAspectRatio, Qt.SmoothTransformation)
                ox = (self.width()  - scaled.width())  // 2
                oy = (self.height() - scaled.height()) // 2
                sw, sh = scaled.width(), scaled.height()
                nx = (e.x() - ox) / sw
                ny = (e.y() - oy) / sh
                if 0 <= nx <= 1 and 0 <= ny <= 1:
                    self._cur_poly.append((nx, ny))
                    self.update()
                    # 4개 찍으면 자동으로 완성
                    if len(self._cur_poly) == 4:
                        self.seg_added.emit(list(self._cur_poly))
                        self._cur_poly = []
                        self.update()
            elif e.button() == Qt.RightButton:
                if self._cur_poly:
                    self._cur_poly.pop()
                    self.update()
        else:
            if e.button() == Qt.LeftButton:
                self._origin = e.pos()
                from PyQt5.QtCore import QSize
                self._rubber.setGeometry(QRect(self._origin, QSize()))
                self._rubber.show()

    def mouseMoveEvent(self, e):
        if not self._seg_mode and not self._rubber.isHidden():
            self._rubber.setGeometry(QRect(self._origin, e.pos()).normalized())

    def mouseReleaseEvent(self, e):
        if not self._seg_mode and e.button() == Qt.LeftButton:
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
                self.roi_added.emit(nr)    # ROI 모드


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  서브탭 B: 학습 데이터셋 뷰어 (타겟 뷰어 방식)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
class DatasetViewerTab(QWidget):
    """
    data/yolo_source 에 있는 라벨드 이미지를
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
            "🟢 초록 박스 = YOLO bbox  |  🟠 주황 다각형 = Seg 포맷  |  🔵 파란 점선 = ROI", bold=False))
        self.viewer = DatasetImageViewer()
        self.viewer.roi_added.connect(self._on_roi_drawn)
        self.viewer.seg_added.connect(self._on_seg_drawn)   # Seg 모드 시그널 연결
        cv_layout.addWidget(self.viewer, stretch=1)
        main.addWidget(center, stretch=1)

        # ── 우측: 중요도 + ROI 관리 패널 (스크롤 가능) ──────────────────
        right_inner = QWidget()
        right_inner.setStyleSheet(f"background:{C_WHITE};")
        rv = QVBoxLayout(right_inner); rv.setContentsMargins(10, 10, 10, 10); rv.setSpacing(10)

        # 중요도 설정
        rv.addWidget(self._lbl("중요도 설정", bold=True))
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

        btn_save_imp = QPushButton("중요도 저장")
        btn_save_imp.setStyleSheet(f"background:{C_GREEN};color:white;border:none;border-radius:6px;font-weight:bold;padding:4px;")
        btn_save_imp.clicked.connect(self._save_importance)
        rv.addWidget(btn_save_imp)

        # 구분선
        sep = QFrame(); sep.setFrameShape(QFrame.HLine)
        sep.setStyleSheet(f"color:{C_BORDER};max-height:1px;"); rv.addWidget(sep)

        # ROI 관리
        rv.addWidget(self._lbl("ROI 설정 관리", bold=True))
        self.roi_list = QListWidget()
        self.roi_list.setMinimumHeight(60)
        self.roi_list.setStyleSheet(
            f"QListWidget{{background:{C_BG};border:1px solid {C_BORDER};border-radius:4px;font-size:11px;}}"
            f"QListWidget::item:selected{{background:#EBF5FB;color:{C_BLUE};}}")
        rv.addWidget(self.roi_list)
        btn_del = QPushButton("선택 ROI 삭제"); btn_del.clicked.connect(self._del_roi)
        btn_clr = QPushButton("전체 초기화");   btn_clr.clicked.connect(self._clear_roi)
        btn_sav = QPushButton("ROI 저장 (핫리로드)")
        btn_sav.setStyleSheet(f"background:{C_GREEN};color:white;border:none;border-radius:6px;font-weight:bold;padding:4px;")
        btn_sav.clicked.connect(self._save_roi)
        rv.addWidget(btn_del); rv.addWidget(btn_clr); rv.addWidget(btn_sav)
        rv.addWidget(self._lbl("이미지 위에서\n마우스를 드래그하여\nROI를 추가하세요.", bold=False))

        # ── 다각형(Seg) 직접 편집 섹션 ────────────────────────────────
        sep2 = QFrame(); sep2.setFrameShape(QFrame.HLine)
        sep2.setStyleSheet(f"color:{C_BORDER};max-height:1px;"); rv.addWidget(sep2)
        rv.addWidget(self._lbl("다각형(Seg) 직접 편집", bold=True))

        self.btn_seg_mode = QPushButton("다각형 추가 모드 OFF")
        self.btn_seg_mode.setCheckable(True)
        self.btn_seg_mode.setStyleSheet(
            f"QPushButton{{background:{C_BG};color:{C_DARK};border:1px solid {C_BORDER};border-radius:6px;"
            f"font-weight:bold;font-size:12px;padding:4px;}}"
            f"QPushButton:checked{{background:{C_ORANGE};color:white;border:none;}}")
        self.btn_seg_mode.clicked.connect(self._toggle_seg_mode)
        rv.addWidget(self.btn_seg_mode)

        self.seg_list = QListWidget()
        self.seg_list.setMaximumHeight(70)
        self.seg_list.setStyleSheet(
            f"QListWidget{{background:{C_BG};border:1px solid {C_BORDER};border-radius:4px;font-size:11px;}}"
            f"QListWidget::item:selected{{background:#FEF9E7;color:{C_ORANGE};}}")
        rv.addWidget(self.seg_list)
        btn_del_seg = QPushButton("선택 다각형 삭제")
        btn_del_seg.clicked.connect(self._del_seg)
        btn_clr_seg = QPushButton("전체 초기화")
        btn_clr_seg.clicked.connect(self._clear_seg)
        btn_sav_seg = QPushButton("다각형 TXT 저장")
        btn_sav_seg.setStyleSheet(
            f"background:{C_ORANGE};color:white;border:none;border-radius:6px;font-weight:bold;padding:4px;")
        btn_sav_seg.clicked.connect(self._save_seg_to_txt)
        rv.addWidget(btn_del_seg); rv.addWidget(btn_clr_seg); rv.addWidget(btn_sav_seg)
        rv.addStretch()

        # QScrollArea 래핑
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(right_inner)
        scroll.setFixedWidth(260)
        scroll.setStyleSheet(
            f"QScrollArea{{background:{C_WHITE};border:1px solid {C_BORDER};border-radius:8px;}}"
            f"QScrollBar:vertical{{width:6px;background:{C_BG};}}"
            f"QScrollBar::handle:vertical{{background:{C_BORDER};border-radius:3px;}}"
        )
        main.addWidget(scroll)


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
        # 다각형 리스트 동기화
        self.seg_list.clear()
        for i, poly in enumerate(self.viewer._polygons):
            self.seg_list.addItem(f"다각형-{i+1}: {len(poly)} points")

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

    # ── 다각형 직접 편집 메서드 ───────────────────────────────────
    def _toggle_seg_mode(self, checked: bool):
        """토글 ON → 다각형 모드, OFF → ROI(드래그) 모드"""
        self.viewer._seg_mode = checked
        if not checked: self.viewer._cur_poly = []; self.viewer.update()
        self.btn_seg_mode.setText(
            "다각형 추가 모드 ON" if checked else "다각형 추가 모드 OFF")

    def _on_seg_drawn(self, pts):
        """다각형(사다리꼴) 하나가 완성되었을 때"""
        self.viewer._polygons.append(pts)
        self.seg_list.addItem(f"다각형-{len(self.viewer._polygons)}: {len(pts)} points")
        self.viewer.update()

    def _del_seg(self):
        row = self.seg_list.currentRow()
        if row >= 0:
            self.viewer._polygons.pop(row)
            self.seg_list.takeItem(row)
            self.viewer.update()

    def _clear_seg(self):
        self.viewer._polygons.clear()
        self.viewer._bboxes.clear()  # 기존 직사각형도 싹 밀기
        self.seg_list.clear()
        self.viewer.update()

    def _save_seg_to_txt(self):
        """현재 다각형 목록 및 기존 bboxes 를 YOLO(Seg/Bbox) 형식 .txt로 덮어쓰기"""
        if not self._cur_fname:
            QMessageBox.warning(self, "안내", "먼저 이미지를 선택해주세요."); return
        if not self.viewer._polygons and not self.viewer._bboxes:
            QMessageBox.warning(
                self, "안내", "저장할 데이터가 없습니다."); return
        txt_path = os.path.join(
            LABELED_DATA_DIR, os.path.splitext(self._cur_fname)[0] + ".txt")
        try:
            with open(txt_path, "w", encoding="utf-8") as f:
                # Bboxes가 있다면 먼저 기록 (이전 데이터 보존)
                for (cx, cy, bw, bh) in self.viewer._bboxes:
                    f.write(f"0 {cx:.6f} {cy:.6f} {bw:.6f} {bh:.6f}\n")
                # 그 다음 다각형 폴리곤 모두 기록
                for pts in self.viewer._polygons:
                    coords = " ".join(f"{nx:.6f} {ny:.6f}" for nx, ny in pts)
                    f.write(f"0 {coords}\n")
            QMessageBox.information(
                self, "저장 완료",
                f"다각형 {len(self.viewer._polygons)}개 저장 완료\n{txt_path}")
            self._load_dataset()   # 라벨 카운트 수 갱신
        except Exception as ex:
            QMessageBox.critical(self, "저장 실패", str(ex))



# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  서브탭 C: 샴 네트워크 수동 라벨링
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
class SiameseLabelTab(QWidget):
    """
    실제 카메라 프레임 / Pending 이미지에 직접 라벨을 붙여
    data/siamese_train/{1,2,3,4,neg}/ 에 쌓는 라벨링 작업대.
    단축키: 1~4 → 해당 타겟, X → 정답아님, Space → 건너뜀, ←→ → 이전/다음.
    """
    def __init__(self):
        super().__init__()
        self.setStyleSheet(f"background:{C_BG};")
        self._images = []
        self._idx    = 0
        self._train_thread = None

        v = QVBoxLayout(self)
        v.setContentsMargins(8, 8, 8, 8); v.setSpacing(6)
        v.addWidget(self._build_toolbar())
        body = QHBoxLayout(); body.setSpacing(10)
        body.addWidget(self._build_viewer_panel(), stretch=1)
        body.addWidget(self._build_right_panel())
        v.addLayout(body, stretch=1)
        v.addWidget(self._build_nav_bar())

        self.setFocusPolicy(Qt.StrongFocus)
        QTimer.singleShot(300, self._load_images)

    # ── 툴바 ──────────────────────────────────────────────────────────
    def _build_toolbar(self):
        bar = QWidget(); bar.setFixedHeight(44)
        bar.setStyleSheet(f"background:{C_WHITE};border:1px solid {C_BORDER};border-radius:8px;")
        h = QHBoxLayout(bar); h.setContentsMargins(10, 0, 10, 0); h.setSpacing(8)
        h.addWidget(self._lbl("🧬  샴 네트워크 수동 라벨링", bold=True))
        h.addStretch()
        from PyQt5.QtWidgets import QRadioButton, QButtonGroup
        self._rb_pending = QRadioButton("📋  Pending 폴더")
        self._rb_queue   = QRadioButton("📸  실시간 캡처 큐")
        self._rb_pending.setChecked(True)
        self._bg = QButtonGroup(self)
        self._bg.addButton(self._rb_pending, 0)
        self._bg.addButton(self._rb_queue,   1)
        for rb in [self._rb_pending, self._rb_queue]:
            rb.setStyleSheet(f"font-size:11px;color:{C_DARK};")
            h.addWidget(rb)
        self._bg.buttonClicked.connect(lambda _: self._load_images())
        btn_r = QPushButton("🔄 새로고침")
        btn_r.setStyleSheet(f"background:{C_BG};border:1px solid {C_BORDER};border-radius:4px;font-size:11px;padding:2px 8px;")
        btn_r.clicked.connect(self._load_images)
        h.addWidget(btn_r)
        self.lbl_total = QLabel("0개")
        self.lbl_total.setStyleSheet(f"font-size:11px;color:{C_SUB};min-width:36px;")
        h.addWidget(self.lbl_total)
        return bar

    # ── 이미지 뷰어 ────────────────────────────────────────────────────
    def _build_viewer_panel(self):
        box = QWidget()
        box.setStyleSheet(f"background:{C_WHITE};border:1px solid {C_BORDER};border-radius:8px;")
        vl = QVBoxLayout(box); vl.setContentsMargins(8, 8, 8, 8); vl.setSpacing(4)
        self.viewer = QLabel()
        self.viewer.setAlignment(Qt.AlignCenter)
        self.viewer.setMinimumSize(460, 340)
        self.viewer.setStyleSheet("background:#1a1a2e;border-radius:6px;color:#7F8C8D;font-size:13px;")
        self.viewer.setText("← 소스를 선택하고 새로고침하세요")
        vl.addWidget(self.viewer, stretch=1)
        self.lbl_fname = QLabel("")
        self.lbl_fname.setAlignment(Qt.AlignCenter)
        self.lbl_fname.setStyleSheet(f"font-size:9px;color:{C_SUB};")
        vl.addWidget(self.lbl_fname)
        return box

    # ── 우측 패널: 카운터 + 라벨 버튼 + 학습 (스크롤 적용) ───────────────
    def _build_right_panel(self):
        inner = QWidget()
        inner.setStyleSheet(f"background:{C_WHITE};")
        v = QVBoxLayout(inner); v.setContentsMargins(10, 10, 10, 10); v.setSpacing(6)

        # 카운터
        v.addWidget(self._lbl("라벨 현황", bold=True))
        self._cnt_labels = {}
        for key, text, color in _LABEL_META:
            icon_txt, name_txt = text.split(None, 1)
            row = QHBoxLayout(); row.setSpacing(4)
            name_l = QLabel(f"{icon_txt} {name_txt}")
            name_l.setStyleSheet(f"font-size:12px;color:{C_DARK};")
            cnt_l  = QLabel("0장")
            cnt_l.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
            cnt_l.setStyleSheet(f"font-size:12px;font-weight:bold;color:{color};min-width:36px;")
            row.addWidget(name_l, 1); row.addWidget(cnt_l)
            v.addLayout(row)
            self._cnt_labels[key] = cnt_l

        sep = QFrame(); sep.setFrameShape(QFrame.HLine)
        sep.setStyleSheet(f"color:{C_BORDER};max-height:1px;"); v.addWidget(sep)

        # 라벨 버튼
        v.addWidget(self._lbl("라벨 지정", bold=True))
        hint = QLabel("단축키: 1~4  X  Space  ←→")
        hint.setStyleSheet(f"font-size:11px;color:{C_SUB};")
        v.addWidget(hint)
        SC = {"1":"[1]","2":"[2]","3":"[3]","4":"[4]","neg":"[X]"}
        for key, text, color in _LABEL_META:
            btn = QPushButton(f"{text}  {SC[key]}")
            btn.setMinimumHeight(30)
            btn.setStyleSheet(
                f"QPushButton{{background:{color};color:white;border:none;"
                f"border-radius:6px;font-size:12px;font-weight:bold;padding:2px 6px;}}"
                f"QPushButton:hover{{background:{color}CC;}}"
            )
            btn.clicked.connect(lambda _, k=key: self._label_and_next(k))
            v.addWidget(btn)
        btn_skip = QPushButton("삭제 후 다음  [Space]")
        btn_skip.setMinimumHeight(28)
        btn_skip.setStyleSheet(f"background:#FDF2F8;color:{C_RED};border:1px solid {C_RED};border-radius:6px;font-size:11px;font-weight:bold;")
        btn_skip.clicked.connect(self._skip_delete)
        v.addWidget(btn_skip)

        sep2 = QFrame(); sep2.setFrameShape(QFrame.HLine)
        sep2.setStyleSheet(f"color:{C_BORDER};max-height:1px;"); v.addWidget(sep2)

        # 라벨 데이터 관리 섹션
        v.addWidget(self._lbl("라벨 데이터 관리", bold=True))
        for key, text, color in _LABEL_META:
            icon_txt, name_txt = text.split(None, 1)
            row = QHBoxLayout(); row.setSpacing(4)
            name_l = QLabel(f"{icon_txt} {name_txt}")
            name_l.setStyleSheet(f"font-size:12px;color:{C_DARK};")
            btn_del_cls = QPushButton("삭제")
            btn_del_cls.setFixedSize(44, 24)
            btn_del_cls.setStyleSheet(
                f"background:{C_RED};color:white;border:none;"
                f"border-radius:4px;font-size:11px;font-weight:bold;"
            )
            btn_del_cls.clicked.connect(lambda _, k=key: self._delete_label_class(k))
            row.addWidget(name_l, 1)
            row.addWidget(btn_del_cls)
            v.addLayout(row)
        btn_del_all = QPushButton("전체 초기화")
        btn_del_all.setMinimumHeight(28)
        btn_del_all.setStyleSheet(
            f"background:{C_RED};color:white;border:none;"
            f"border-radius:6px;font-size:12px;font-weight:bold;"
        )
        btn_del_all.clicked.connect(self._delete_all_labels)
        v.addWidget(btn_del_all)

        sep3 = QFrame(); sep3.setFrameShape(QFrame.HLine)
        sep3.setStyleSheet(f"color:{C_BORDER};max-height:1px;"); v.addWidget(sep3)

        # 학습 섹션
        v.addWidget(self._lbl("샴 재학습", bold=True))
        self.lbl_train_st = QLabel("학습 전")
        self.lbl_train_st.setWordWrap(True)
        self.lbl_train_st.setStyleSheet(f"font-size:11px;color:{C_SUB};")
        v.addWidget(self.lbl_train_st)
        self.prog_train = QProgressBar()
        self.prog_train.setRange(0, 100); self.prog_train.setValue(0)
        self.prog_train.setFixedHeight(8); self.prog_train.setTextVisible(False)
        self.prog_train.setStyleSheet(
            "QProgressBar{background:#E8DAEF;border-radius:4px;}"
            "QProgressBar::chunk{background:#9B59B6;border-radius:4px;}"
        )
        v.addWidget(self.prog_train)
        self.btn_train = QPushButton("샴 학습 시작")
        self.btn_train.setMinimumHeight(32)
        self.btn_train.setStyleSheet(
            "QPushButton{background:#9B59B6;color:white;border:none;"
            "border-radius:6px;font-size:12px;font-weight:bold;}"
            "QPushButton:hover{background:#7D3C98;}"
            "QPushButton:disabled{background:#BDC3C7;}"
        )
        self.btn_train.clicked.connect(self._start_training)
        v.addWidget(self.btn_train)
        v.addStretch()

        # QScrollArea 래핑
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(inner)
        scroll.setFixedWidth(240)
        scroll.setStyleSheet(
            f"QScrollArea{{background:{C_WHITE};border:1px solid {C_BORDER};border-radius:8px;}}"
            f"QScrollBar:vertical{{width:6px;background:{C_BG};}}"
            f"QScrollBar::handle:vertical{{background:{C_BORDER};border-radius:3px;}}"
        )
        return scroll


    # ── 네비게이션 바 ──────────────────────────────────────────────────
    def _build_nav_bar(self):
        bar = QWidget(); bar.setFixedHeight(38)
        bar.setStyleSheet(f"background:{C_WHITE};border:1px solid {C_BORDER};border-radius:8px;")
        h = QHBoxLayout(bar); h.setContentsMargins(10, 0, 10, 0); h.setSpacing(8)
        btn_prev = QPushButton("◀  이전"); btn_prev.setFixedWidth(80)
        btn_prev.setStyleSheet(f"background:{C_BG};border:1px solid {C_BORDER};border-radius:4px;font-size:11px;")
        btn_prev.clicked.connect(self._prev)
        self.lbl_nav = QLabel("[0 / 0]")
        self.lbl_nav.setAlignment(Qt.AlignCenter)
        self.lbl_nav.setStyleSheet(f"font-size:12px;font-weight:bold;color:{C_DARK};")
        btn_next = QPushButton("다음  ▶"); btn_next.setFixedWidth(80)
        btn_next.setStyleSheet(f"background:{C_BG};border:1px solid {C_BORDER};border-radius:4px;font-size:11px;")
        btn_next.clicked.connect(self._next)
        h.addStretch()
        h.addWidget(btn_prev); h.addWidget(self.lbl_nav); h.addWidget(btn_next)
        h.addStretch()
        return bar

    # ── 헬퍼 ──────────────────────────────────────────────────────────
    def _lbl(self, text, bold=False):
        l = QLabel(text); l.setWordWrap(True)
        l.setStyleSheet(f"font-size:11px;{'font-weight:bold;' if bold else ''}color:{C_DARK};")
        return l

    # ── 이미지 목록 로드 ───────────────────────────────────────────────
    def _load_images(self):
        src_dir = PENDING_DIR if self._rb_pending.isChecked() else SIAMESE_QUEUE_DIR
        os.makedirs(src_dir, exist_ok=True)
        self._images = sorted([
            os.path.join(src_dir, f)
            for f in os.listdir(src_dir)
            if f.lower().endswith((".jpg", ".jpeg", ".png"))
        ])
        self._idx = 0
        self.lbl_total.setText(f"{len(self._images)}개")
        self._show_current()
        self._update_counter()

    def _show_current(self):
        if not self._images:
            self.viewer.setPixmap(QPixmap())
            self.viewer.setText("이미지가 없습니다.\n[새로고침]을 눌러 소스를 로드하세요.")
            self.lbl_nav.setText("[0 / 0]"); self.lbl_fname.setText("")
            return
        path = self._images[self._idx]
        try:
            buf = np.fromfile(path, dtype=np.uint8)
            img = cv2.imdecode(buf, cv2.IMREAD_COLOR)
            if img is not None:
                img_rgb = cv2.cvtColor(img, cv2.COLOR_BGR2RGB)
                h2, w2  = img_rgb.shape[:2]
                qimg    = QImage(img_rgb.tobytes(), w2, h2, 3*w2, QImage.Format_RGB888)
                pix     = QPixmap.fromImage(qimg)
                scaled  = pix.scaled(self.viewer.size(), Qt.KeepAspectRatio, Qt.SmoothTransformation)
                self.viewer.setPixmap(scaled)
            else:
                self.viewer.setText("⚠️ 이미지 로드 실패")
        except Exception as ex:
            self.viewer.setText(f"⚠️ {ex}")
        self.lbl_nav.setText(f"[{self._idx + 1} / {len(self._images)}]")
        self.lbl_fname.setText(os.path.basename(path))

    # ── 라벨링 ────────────────────────────────────────────────────────
    def _label_and_next(self, key: str):
        if not self._images:
            return
        src     = self._images[self._idx]
        dst_dir = os.path.join(SIAMESE_TRAIN_DIR, key)
        os.makedirs(dst_dir, exist_ok=True)
        # 동명 파일 충돌 방지: idx prefix 추가
        fname    = f"{self._idx:05d}_{os.path.basename(src)}"
        dst_path = os.path.join(dst_dir, fname)
        try:
            shutil.move(src, dst_path)
            # 현재 이미지 리스트에서도 삭제하여 이후 중복 노출 방지
            del self._images[self._idx]
            self._idx -= 1 # _next() 호출로 +1 되므로 미리 -1
        except Exception as ex:
            print(f"[SiameseLabelTab] 이동 오류: {ex}")
        self._update_counter()
        self._next()

    def _skip_delete(self):
        """현재 이미지를 디스크에서 삭제하고 다음으로 이동 (Space 단축키)"""
        if not self._images:
            return
        path = self._images[self._idx]
        try:
            os.remove(path)
        except Exception as ex:
            print(f"[SiameseLabelTab] 삭제 오류: {ex}")
        del self._images[self._idx]
        # 마지막 이미지를 삭제한 경우 idx를 보정
        if self._idx >= len(self._images):
            self._idx = max(0, len(self._images) - 1)
        self.lbl_total.setText(f"{len(self._images)}개")
        self._show_current()

    def _delete_label_class(self, key: str):
        """특정 클래스 라벨 폴더의 이미지를 모두 삭제"""
        folder = os.path.join(SIAMESE_TRAIN_DIR, key)
        if not os.path.isdir(folder):
            return
        files = [f for f in os.listdir(folder)
                 if f.lower().endswith((".jpg", ".jpeg", ".png"))]
        if not files:
            QMessageBox.information(self, "알림", f"{key} 클래스에 삭제할 이미지가 없습니다.")
            return
        key_text = next((t for k, t, _ in _LABEL_META if k == key), key)
        reply = QMessageBox.question(
            self, "삭제 확인",
            f"{key_text} 클래스의 이미지 {len(files)}장을 모두 삭제할까요?",
            QMessageBox.Yes | QMessageBox.No
        )
        if reply == QMessageBox.Yes:
            for f in files:
                try:
                    os.remove(os.path.join(folder, f))
                except Exception:
                    pass
            self._update_counter()

    def _delete_all_labels(self):
        """모든 클래스의 라벨 데이터를 전부 삭제"""
        total = sum(
            len([f for f in os.listdir(os.path.join(SIAMESE_TRAIN_DIR, k))
                 if f.lower().endswith((".jpg", ".jpeg", ".png"))])
            for k, _, _ in _LABEL_META
            if os.path.isdir(os.path.join(SIAMESE_TRAIN_DIR, k))
        )
        if total == 0:
            QMessageBox.information(self, "알림", "삭제할 라벨 이미지가 없습니다.")
            return
        reply = QMessageBox.question(
            self, "⚠️ 전체 초기화 확인",
            f"모든 클래스의 라벨 이미지 총 {total}장을 삭제합니다.\n이 작업은 되돌릴 수 없습니다.",
            QMessageBox.Yes | QMessageBox.No
        )
        if reply == QMessageBox.Yes:
            for key, _, _ in _LABEL_META:
                folder = os.path.join(SIAMESE_TRAIN_DIR, key)
                if not os.path.isdir(folder):
                    continue
                for f in os.listdir(folder):
                    if f.lower().endswith((".jpg", ".jpeg", ".png")):
                        try:
                            os.remove(os.path.join(folder, f))
                        except Exception:
                            pass
            self._update_counter()

    def _next(self):
        if self._images and self._idx < len(self._images) - 1:
            self._idx += 1
            self._show_current()

    def _prev(self):
        if self._images and self._idx > 0:
            self._idx -= 1
            self._show_current()

    def _update_counter(self):
        for key, _, _ in _LABEL_META:
            folder = os.path.join(SIAMESE_TRAIN_DIR, key)
            count  = 0
            if os.path.isdir(folder):
                count = sum(1 for f in os.listdir(folder)
                            if f.lower().endswith((".jpg", ".jpeg", ".png")))
            self._cnt_labels[key].setText(f"{count}장")

    # ── 키보드 단축키 ─────────────────────────────────────────────────
    def keyPressEvent(self, e):
        k = e.key()
        if   k == Qt.Key_1:               self._label_and_next("1")
        elif k == Qt.Key_2:               self._label_and_next("2")
        elif k == Qt.Key_3:               self._label_and_next("3")
        elif k == Qt.Key_4:               self._label_and_next("4")
        elif k in (Qt.Key_X, Qt.Key_N):  self._label_and_next("neg")
        elif k == Qt.Key_Space:           self._skip_delete()
        elif k == Qt.Key_Left:            self._prev()
        elif k == Qt.Key_Right:           self._next()
        else:                             super().keyPressEvent(e)

    # ── 학습 시작 ─────────────────────────────────────────────────────
    def _start_training(self):
        # 라벨 데이터 확인 (neg 제외)
        total = sum(
            int(self._cnt_labels[k].text().replace("장", ""))
            for k, _, _ in _LABEL_META if k != "neg"
        )
        if total < 1:
            QMessageBox.warning(
                self, "데이터 부족",
                "1~4번 타겟에 라벨 데이터가 하나도 없습니다.\n"
                "먼저 이미지를 라벨링하세요.")
            return
        self.btn_train.setEnabled(False)
        self.prog_train.setValue(0)
        self.lbl_train_st.setText("학습 시작 중...")
        self._train_thread = SiameseTrainThread()
        self._train_thread.progress.connect(self._on_train_progress)
        self._train_thread.finished.connect(self._on_train_done)
        self._train_thread.start()

    def _on_train_progress(self, pct, msg):
        if pct >= 0:
            self.prog_train.setValue(min(pct, 100))
            self.lbl_train_st.setText(msg)
        else:
            self.lbl_train_st.setText(f"❌ {msg}")

    def _on_train_done(self, ok, msg):
        self.btn_train.setEnabled(True)
        if ok:
            QMessageBox.information(self, "학습 완료",
                f"✅ 샴 네트워크 재학습이 완료되었습니다!\n{msg}")
        else:
            QMessageBox.critical(self, "학습 실패", f"❌ {msg}")


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
        t5,self.m_box = _metric_label("Seg Loss",   "seg_loss")
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
        mode_label = '처음부터 (yolov8n-seg.pt)' if mode == 'scratch' else '이어서 (best.pt)'
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
            f"Seg Loss: {m.get('box_loss',0):.4f}"
        )

    def _fire_orb(self):
        td = os.path.join(_ROOT,"data","targets")
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
        # 루트의 yolov8n-seg.pt 또는 yolov8n.pt도 추가
        for base_name in ["yolov8n-seg.pt", "yolov8n.pt"]:
            base = os.path.join(_ROOT, base_name)
            if os.path.exists(base):
                pt_files.insert(0, base_name)
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
        self._siamese_tab  = SiameseLabelTab()

        # ── YOLO 학습 서브탭 (FireBar를 전용 탭으로 분리) ──────────────
        yolo_tab = QWidget()
        yolo_tab.setStyleSheet(f"background:{C_BG};")
        yt_v = QVBoxLayout(yolo_tab)
        yt_v.setContentsMargins(16, 16, 16, 16)
        yt_v.setSpacing(10)

        # 제목 바
        yolo_title = QWidget()
        yolo_title.setFixedHeight(46)
        yolo_title.setStyleSheet(
            f"background:{C_WHITE};border:1px solid {C_BORDER};border-radius:8px;"
        )
        yt_h = QHBoxLayout(yolo_title)
        yt_h.setContentsMargins(16, 0, 16, 0)
        title_lbl = QLabel("YOLO 재학습 관리")
        title_lbl.setStyleSheet(
            f"font-size:14px;font-weight:bold;color:{C_DARK};"
        )
        sub_lbl = QLabel("  YOLO 모델을 재학습하거나 이전 버전으로 롤백합니다.")
        sub_lbl.setStyleSheet(f"font-size:11px;color:{C_SUB};")
        yt_h.addWidget(title_lbl)
        yt_h.addWidget(sub_lbl)
        yt_h.addStretch()
        yt_v.addWidget(yolo_title)

        # FireBar 본체
        fire = FireBar(self._pending_tab)
        fire.setFixedHeight(210)
        yt_v.addWidget(fire)
        yt_v.addStretch()

        sub.addTab(self._pending_tab,  "  Pending 검수실  ")
        sub.addTab(self._dataset_tab,  "  학습 데이터셋 뷰어  ")
        sub.addTab(self._siamese_tab,  "  샴 라벨링  ")
        sub.addTab(yolo_tab,           "  YOLO 학습  ")
        v.addWidget(sub, stretch=1)
