"""
tab_seg_labeling.py — YOLO-Seg 훈련 데이터 생성 위젯
모니터 외곽선 폴리곤을 직접 그려 YOLO-Seg 훈련 데이터셋을 만듭니다.

저장 구조:
  data/seg_labels.json          ← 폴리곤 좌표 JSON 백업 (재편집용)
  data/seg_dataset/
    images/train/               ← 훈련 이미지 (80%)
    images/val/                 ← 검증 이미지 (20%)
    labels/train/               ← YOLO seg 라벨 (0 x1 y1 x2 y2 ...)
    labels/val/
    monitor.yaml                ← 훈련 설정 파일
"""
import os
import json
import cv2
import numpy as np
from PyQt5.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel,
    QListWidget, QListWidgetItem, QPushButton,
    QFrame, QMessageBox, QSizePolicy, QFileDialog,
    QDialog, QTextEdit, QDialogButtonBox, QApplication,
)
from PyQt5.QtCore import Qt, pyqtSignal
from PyQt5.QtGui import QImage, QPixmap, QCursor

_ROOT       = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CAPTURE_DIR = os.path.join(_ROOT, "data", "capture")
SEG_LABELS  = os.path.join(_ROOT, "data", "seg_labels.json")
SEG_DATASET = os.path.join(_ROOT, "data", "seg_dataset")

C_BG     = "#F8F9FA"
C_WHITE  = "#FFFFFF"
C_DARK   = "#2C3E50"
C_SUB    = "#7F8C8D"
C_BLUE   = "#3498DB"
C_GREEN  = "#27AE60"
C_RED    = "#E74C3C"
C_ORANGE = "#E67E22"
C_BORDER = "#E0E4E8"


def _lbl(text, size=12, bold=False, color=C_DARK, wrap=True):
    lb = QLabel(text)
    style = f"font-size:{size}px; color:{color};"
    if bold:
        style += " font-weight:bold;"
    lb.setStyleSheet(style)
    lb.setWordWrap(wrap)
    return lb


# ─────────────────────────────────────────────────────────────────────────────
class PolyCanvas(QLabel):
    """
    이미지 위에 폴리곤을 클릭으로 그리는 캔버스.
      - 좌클릭 : 점 추가
      - 우클릭 : 마지막 점 취소
      - 3점 이상이면 자동으로 닫힌 폴리곤 표시 (초록)
    """
    points_changed = pyqtSignal(int)   # 현재 점 개수 전달

    def __init__(self):
        super().__init__()
        self.setAlignment(Qt.AlignCenter)
        self.setMinimumSize(400, 280)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.setStyleSheet(
            "background:#1a1a2e; border-radius:6px;"
            f"color:{C_SUB}; font-size:13px;"
        )
        self.setText("← 왼쪽에서 이미지를 선택하세요")
        self.setCursor(QCursor(Qt.CrossCursor))

        self._img_cv = None   # BGR numpy array (원본 해상도)
        self._points = []     # [(nx, ny), ...]  정규화 좌표 목록

    # ── 공개 API ─────────────────────────────────────────────────────────────

    def load_image(self, img_cv, saved_poly=None):
        """
        이미지 로드.
        saved_poly: 기존 저장 폴리곤 [(nx,ny), ...] 이면 점으로 복원해 바로 편집 가능.
        """
        self._img_cv = img_cv
        if img_cv is None:
            self._points = []
            self.clear()
            self.setText("이미지 로드 실패")
            self.points_changed.emit(0)
            return
        self._points = list(saved_poly) if saved_poly else []
        self.points_changed.emit(len(self._points))
        self._repaint()

    def undo(self):
        if self._points:
            self._points.pop()
            self._repaint()
            self.points_changed.emit(len(self._points))

    def reset(self):
        self._points = []
        self._repaint()
        self.points_changed.emit(0)

    def get_points(self):
        return list(self._points)

    def is_ready(self):
        return len(self._points) >= 3

    # ── Qt 이벤트 ─────────────────────────────────────────────────────────────

    def mousePressEvent(self, event):
        if self._img_cv is None:
            return
        if event.button() == Qt.LeftButton:
            norm = self._to_norm(event.x(), event.y())
            if norm:
                self._points.append(norm)
                self._repaint()
                self.points_changed.emit(len(self._points))
        elif event.button() == Qt.RightButton:
            self.undo()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._repaint()

    # ── 내부 유틸 ─────────────────────────────────────────────────────────────

    def _transform(self):
        """(scale, offset_x, offset_y, img_w, img_h) 반환"""
        if self._img_cv is None:
            return None
        ih, iw = self._img_cv.shape[:2]
        lw = max(1, self.width())
        lh = max(1, self.height())
        scale = min(lw / iw, lh / ih)
        ox = (lw - iw * scale) / 2
        oy = (lh - ih * scale) / 2
        return scale, ox, oy, iw, ih

    def _to_norm(self, cx, cy):
        """위젯 픽셀 좌표 → 정규화 이미지 좌표 (0~1). 이미지 밖이면 None."""
        t = self._transform()
        if t is None:
            return None
        scale, ox, oy, iw, ih = t
        px = (cx - ox) / scale
        py = (cy - oy) / scale
        if 0 <= px <= iw and 0 <= py <= ih:
            return px / iw, py / ih
        return None

    def _repaint(self):
        if self._img_cv is None:
            return
        vis = self._img_cv.copy()
        ih, iw = vis.shape[:2]

        if self._points:
            pts = np.array(
                [(int(nx * iw), int(ny * ih)) for nx, ny in self._points],
                dtype=np.int32,
            )
            closed = len(pts) >= 3
            # 완성(3점↑) = 초록, 진행 중 = 밝은 청황색
            line_c = (0, 210, 100) if closed else (0, 190, 255)
            dot_c  = (0, 240, 120) if closed else (0, 220, 255)
            cv2.polylines(vis, [pts], closed, line_c, 2, cv2.LINE_AA)
            for i, p in enumerate(pts):
                cv2.circle(vis, tuple(p), 6, dot_c, -1)
                cv2.putText(
                    vis, str(i + 1),
                    (p[0] + 8, p[1] - 6),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.4, dot_c, 1, cv2.LINE_AA,
                )

        rgb  = cv2.cvtColor(vis, cv2.COLOR_BGR2RGB)
        h, w = rgb.shape[:2]
        qimg = QImage(rgb.tobytes(), w, h, 3 * w, QImage.Format_RGB888)
        px   = QPixmap.fromImage(qimg).scaled(
            self.size(), Qt.KeepAspectRatio, Qt.SmoothTransformation
        )
        self.setPixmap(px)


# ─────────────────────────────────────────────────────────────────────────────
class SegLabelingWidget(QWidget):
    """YOLO-Seg 훈련 데이터 생성 위젯 (세그멘테이션 라벨링 서브탭)"""

    def __init__(self):
        super().__init__()
        self.setStyleSheet(f"background:{C_BG};")
        self._labels   = self._load_labels()   # {fname: [[nx,ny],...] | null}
        self._cur_file = ""
        self._src_dir  = CAPTURE_DIR

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # ── 헤더 ─────────────────────────────────────────────────────────────
        hdr = QWidget()
        hdr.setFixedHeight(54)
        hdr.setStyleSheet(f"background:{C_WHITE}; border-bottom:1px solid {C_BORDER};")
        hh = QHBoxLayout(hdr)
        hh.setContentsMargins(24, 0, 24, 0)
        hh.addWidget(_lbl("세그멘테이션 학습 데이터 생성", size=17, bold=True, wrap=False))
        hh.addWidget(_lbl(
            "  모니터 꼭짓점을 좌클릭으로 표시해 YOLO-Seg 훈련 데이터를 만듭니다.",
            size=11, color=C_SUB, wrap=False,
        ))
        hh.addStretch()
        self._stats_lbl = _lbl("", size=12, color=C_SUB, wrap=False)
        hh.addWidget(self._stats_lbl)
        btn_rf = QPushButton("🔄 새로고침")
        btn_rf.setFixedSize(90, 30)
        btn_rf.clicked.connect(self._load_list)
        hh.addWidget(btn_rf)
        root.addWidget(hdr)

        # ── 본문 ─────────────────────────────────────────────────────────────
        body_w = QWidget()
        body   = QHBoxLayout(body_w)
        body.setContentsMargins(12, 12, 12, 12)
        body.setSpacing(10)

        # 좌측: 이미지 목록 ──────────────────────────────────────────────────
        left = QWidget()
        left.setFixedWidth(250)
        left.setStyleSheet(
            f"background:{C_WHITE}; border:1px solid {C_BORDER}; border-radius:8px;"
        )
        lv = QVBoxLayout(left)
        lv.setContentsMargins(10, 10, 10, 10)
        lv.setSpacing(6)
        lv.addWidget(_lbl("이미지 목록", bold=True))
        self._src_lbl = _lbl("data/capture/", size=10, color=C_SUB)
        lv.addWidget(self._src_lbl)

        btn_folder = QPushButton("📁  폴더 변경")
        btn_folder.setFixedHeight(28)
        btn_folder.clicked.connect(self._pick_folder)
        lv.addWidget(btn_folder)

        self.file_list = QListWidget()
        self.file_list.setStyleSheet(
            f"QListWidget{{background:{C_BG};border:1px solid {C_BORDER};"
            f"border-radius:4px;font-size:11px;}}"
            f"QListWidget::item{{padding:4px;}}"
            f"QListWidget::item:selected{{background:#EBF5FB;color:{C_BLUE};}}"
        )
        self.file_list.currentItemChanged.connect(self._on_select)
        lv.addWidget(self.file_list, stretch=1)
        body.addWidget(left)

        # 중앙: 폴리곤 캔버스 ────────────────────────────────────────────────
        self.canvas = PolyCanvas()
        self.canvas.points_changed.connect(self._on_points_changed)
        body.addWidget(self.canvas, stretch=1)

        # 우측: 컨트롤 패널 ──────────────────────────────────────────────────
        right = QWidget()
        right.setFixedWidth(215)
        right.setStyleSheet(
            f"background:{C_WHITE}; border:1px solid {C_BORDER}; border-radius:8px;"
        )
        rv = QVBoxLayout(right)
        rv.setContentsMargins(12, 14, 12, 14)
        rv.setSpacing(8)

        rv.addWidget(_lbl("사용 방법", size=13, bold=True))
        rv.addWidget(_lbl(
            "① 이미지 선택\n"
            "② 모니터 꼭짓점을 순서대로\n"
            "   좌클릭 (최소 4점 권장)\n"
            "③ 우클릭 = 마지막 점 취소\n"
            "④ [💾 라벨 저장] 클릭",
            size=11, color=C_DARK,
        ))
        self._hsep(rv)

        self._pts_lbl = _lbl("점: 0개  (최소 3개 필요)", size=12, color=C_ORANGE)
        rv.addWidget(self._pts_lbl)

        btn_undo = QPushButton("↩  마지막 점 취소")
        btn_undo.clicked.connect(self.canvas.undo)
        rv.addWidget(btn_undo)

        btn_reset = QPushButton("🗑  폴리곤 초기화")
        btn_reset.clicked.connect(self.canvas.reset)
        rv.addWidget(btn_reset)

        self._hsep(rv)

        self._save_btn = QPushButton("💾  라벨 저장")
        self._save_btn.setEnabled(False)
        self._save_btn.setStyleSheet(
            f"QPushButton{{background:{C_GREEN};color:white;border:none;"
            f"border-radius:6px;font-size:13px;font-weight:bold;padding:8px;}}"
            f"QPushButton:hover{{background:#219a52;}}"
            f"QPushButton:disabled{{background:{C_BORDER};color:{C_SUB};}}"
        )
        self._save_btn.clicked.connect(self._save_label)
        rv.addWidget(self._save_btn)

        self._hsep(rv)
        rv.addWidget(_lbl("데이터셋 내보내기", size=12, bold=True))

        btn_export = QPushButton("📦  YOLO 형식으로 내보내기")
        btn_export.setStyleSheet(
            f"QPushButton{{background:{C_BLUE};color:white;border:none;"
            f"border-radius:6px;font-size:12px;font-weight:bold;padding:6px;}}"
            f"QPushButton:hover{{background:#2980b9;}}"
        )
        btn_export.clicked.connect(self._export_dataset)
        rv.addWidget(btn_export)

        btn_yaml = QPushButton("⚙  monitor.yaml 생성")
        btn_yaml.clicked.connect(lambda: self._gen_yaml(silent=False))
        rv.addWidget(btn_yaml)

        btn_cmd = QPushButton("💻  훈련 명령 보기")
        btn_cmd.clicked.connect(self._show_train_cmd)
        rv.addWidget(btn_cmd)

        btn_open = QPushButton("📂  데이터셋 폴더 열기")
        btn_open.clicked.connect(self._open_dataset_folder)
        rv.addWidget(btn_open)

        rv.addStretch()
        body.addWidget(right)
        root.addWidget(body_w, stretch=1)

        self._load_list()

    def showEvent(self, event):
        super().showEvent(event)
        self._load_list()

    # ── 레이아웃 유틸 ────────────────────────────────────────────────────────

    @staticmethod
    def _hsep(layout):
        f = QFrame()
        f.setFrameShape(QFrame.HLine)
        f.setStyleSheet(f"color:{C_BORDER};")
        layout.addWidget(f)

    # ── 데이터 로드/저장 ─────────────────────────────────────────────────────

    def _load_labels(self):
        if os.path.isfile(SEG_LABELS):
            try:
                with open(SEG_LABELS, encoding="utf-8") as f:
                    return json.load(f)
            except Exception:
                pass
        return {}

    def _save_labels_json(self):
        os.makedirs(os.path.dirname(SEG_LABELS), exist_ok=True)
        with open(SEG_LABELS, "w", encoding="utf-8") as f:
            json.dump(self._labels, f, indent=2)

    def _load_list(self):
        self.file_list.clear()
        os.makedirs(self._src_dir, exist_ok=True)
        files = sorted(
            fn for fn in os.listdir(self._src_dir)
            if fn.lower().endswith((".png", ".jpg", ".jpeg"))
        )
        labeled = 0
        for fname in files:
            poly = self._labels.get(fname)
            item = QListWidgetItem()
            if poly:
                item.setText(f"✅  [{len(poly)}pts]  {fname}")
                labeled += 1
            else:
                item.setText(f"❓  {fname}")
            item.setData(Qt.UserRole, fname)
            self.file_list.addItem(item)
        total = len(files)
        self._stats_lbl.setText(
            f"라벨 완료: {labeled} / {total}  |  미완료: {total - labeled}  |"
        )

    # ── 이벤트 핸들러 ────────────────────────────────────────────────────────

    def _pick_folder(self):
        folder = QFileDialog.getExistingDirectory(
            self, "이미지 폴더 선택", self._src_dir
        )
        if folder:
            self._src_dir = folder
            rel = os.path.relpath(folder, _ROOT)
            self._src_lbl.setText(rel if len(rel) < 34 else "…" + rel[-32:])
            self._load_list()

    def _on_select(self, cur, _prev):
        if cur is None:
            return
        fname = cur.data(Qt.UserRole)
        self._cur_file = fname
        path = os.path.join(self._src_dir, fname)
        try:
            buf = np.fromfile(path, dtype=np.uint8)
            img = cv2.imdecode(buf, cv2.IMREAD_COLOR)
        except Exception:
            img = None
        saved = self._labels.get(fname)
        self.canvas.load_image(img, saved)

    def _on_points_changed(self, n):
        if n < 3:
            self._pts_lbl.setText(
                f"점: {n}개" + ("  (최소 3개 필요)" if n < 3 else "")
            )
            self._pts_lbl.setStyleSheet(f"font-size:12px; color:{C_ORANGE};")
        else:
            self._pts_lbl.setText(f"점: {n}개  ✅ 저장 가능")
            self._pts_lbl.setStyleSheet(f"font-size:12px; color:{C_GREEN};")
        self._save_btn.setEnabled(bool(self._cur_file) and n >= 3)

    def _save_label(self):
        if not self._cur_file:
            return
        pts = self.canvas.get_points()
        if len(pts) < 3:
            QMessageBox.warning(self, "점 부족", "최소 3개 이상의 점이 필요합니다.")
            return

        self._labels[self._cur_file] = pts
        self._save_labels_json()
        self._write_yolo_label(self._cur_file, pts)
        self._load_list()

        # 다음 미라벨 이미지로 자동 이동
        for i in range(self.file_list.count()):
            item = self.file_list.item(i)
            if item and not self._labels.get(item.data(Qt.UserRole)):
                self.file_list.setCurrentItem(item)
                return

        # 모두 완료 → 현재 이미지 재선택 (저장된 폴리곤 반영)
        for i in range(self.file_list.count()):
            item = self.file_list.item(i)
            if item and item.data(Qt.UserRole) == self._cur_file:
                self.file_list.setCurrentItem(item)
                break

    def _write_yolo_label(self, fname, pts):
        """YOLO seg 포맷 (0 x1 y1 x2 y2 ...) 으로 즉시 파일 저장"""
        labels_dir = os.path.join(SEG_DATASET, "labels", "train")
        os.makedirs(labels_dir, exist_ok=True)
        stem   = os.path.splitext(fname)[0]
        coords = " ".join(f"{nx:.6f} {ny:.6f}" for nx, ny in pts)
        with open(os.path.join(labels_dir, stem + ".txt"), "w") as f:
            f.write(f"0 {coords}\n")

    # ── 내보내기 ─────────────────────────────────────────────────────────────

    def _export_dataset(self):
        labeled = {fn: pts for fn, pts in self._labels.items() if pts}
        if not labeled:
            QMessageBox.warning(self, "라벨 없음",
                "저장된 라벨이 없습니다.\n라벨 저장 후 내보내기를 진행하세요.")
            return

        files = list(labeled.keys())
        rng   = np.random.default_rng(42)
        rng.shuffle(files)
        split       = max(1, int(len(files) * 0.8))
        train_files = files[:split]
        val_files   = files[split:]

        for split_name, flist in [("train", train_files), ("val", val_files)]:
            img_dir = os.path.join(SEG_DATASET, "images", split_name)
            lbl_dir = os.path.join(SEG_DATASET, "labels", split_name)
            os.makedirs(img_dir, exist_ok=True)
            os.makedirs(lbl_dir, exist_ok=True)
            for fname in flist:
                # 한글 경로 안전 복사
                src = os.path.join(self._src_dir, fname)
                try:
                    buf = np.fromfile(src, dtype=np.uint8)
                    img = cv2.imdecode(buf, cv2.IMREAD_COLOR)
                    if img is not None:
                        _, enc = cv2.imencode(".png", img)
                        enc.tofile(os.path.join(img_dir, fname))
                except Exception:
                    pass
                # 라벨 파일
                pts    = labeled[fname]
                stem   = os.path.splitext(fname)[0]
                coords = " ".join(f"{nx:.6f} {ny:.6f}" for nx, ny in pts)
                with open(os.path.join(lbl_dir, stem + ".txt"), "w") as f:
                    f.write(f"0 {coords}\n")

        self._gen_yaml(silent=True)
        QMessageBox.information(
            self, "내보내기 완료",
            f"총 {len(files)}장 내보내기 완료\n"
            f"  train : {len(train_files)}장\n"
            f"  val   : {len(val_files)}장\n\n"
            f"저장 위치:\n{SEG_DATASET}",
        )

    def _gen_yaml(self, silent=False):
        os.makedirs(SEG_DATASET, exist_ok=True)
        yaml_path = os.path.join(SEG_DATASET, "monitor.yaml")
        content = (
            "# YOLO Segmentation — Monitor dataset\n"
            f"path: {SEG_DATASET.replace(chr(92), '/')}\n"
            "train: images/train\n"
            "val:   images/val\n"
            "nc: 1\n"
            "names: ['monitor']\n"
        )
        with open(yaml_path, "w", encoding="utf-8") as f:
            f.write(content)
        if not silent:
            QMessageBox.information(
                self, "monitor.yaml 생성 완료",
                f"저장 위치:\n{yaml_path}\n\n내용:\n{content}",
            )

    def _show_train_cmd(self):
        yaml_path = os.path.join(SEG_DATASET, "monitor.yaml").replace("\\", "/")
        out_dir   = os.path.join(_ROOT, "models", "seg_monitor").replace("\\", "/")
        active    = os.path.join(_ROOT, "data", "active_model.json").replace("\\", "/")
        cmd = (
            "# ─── 1단계: YOLO-Seg 모델 훈련 ─────────────────────────────────\n"
            "yolo task=segment mode=train \\\n"
            "     model=yolov8n-seg.pt \\\n"
            f'     data="{yaml_path}" \\\n'
            "     epochs=100 imgsz=640 \\\n"
            f'     project="{out_dir}" name=run\n\n'
            "# ─── 2단계: 훈련 완료 후 최적 가중치 위치 ──────────────────────\n"
            f"{out_dir}/run/weights/best.pt\n\n"
            "# ─── 3단계: active_model.json 에 경로 등록 ─────────────────────\n"
            f"# {active} 파일을 아래 내용으로 저장하세요:\n"
            "{\n"
            '  "path": "models/seg_monitor/run/weights/best.pt"\n'
            "}\n"
        )
        dlg = QDialog(self)
        dlg.setWindowTitle("YOLO-Seg 훈련 명령")
        dlg.resize(620, 360)
        lay = QVBoxLayout(dlg)
        lay.addWidget(_lbl("터미널에서 아래 명령을 실행하세요:", bold=True))

        te = QTextEdit()
        te.setReadOnly(True)
        te.setPlainText(cmd)
        te.setStyleSheet(
            "font-family: Consolas, 'Courier New', monospace; font-size: 12px;"
            f"background:{C_BG}; border:1px solid {C_BORDER}; border-radius:4px;"
        )
        lay.addWidget(te)

        btn_copy = QPushButton("📋  명령 전체 클립보드에 복사")
        btn_copy.clicked.connect(lambda: QApplication.clipboard().setText(cmd))
        lay.addWidget(btn_copy)

        bb = QDialogButtonBox(QDialogButtonBox.Close)
        bb.rejected.connect(dlg.reject)
        lay.addWidget(bb)
        dlg.exec_()

    def _open_dataset_folder(self):
        os.makedirs(SEG_DATASET, exist_ok=True)
        import subprocess
        subprocess.Popen(f'explorer "{SEG_DATASET}"')
