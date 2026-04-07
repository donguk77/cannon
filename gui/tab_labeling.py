"""
tab_labeling.py — 정답 라벨링 탭
캡처된 이미지에 정답 타겟 ID를 지정하여 GT(Ground Truth) 데이터셋을 만듭니다.
만들어진 GT 폴더는 파라미터 가이드 탭의 [정답 데이터 기반 분석]에서 사용합니다.

워크플로우:
  1. 실시간 관제 탭에서 [🖼 GT 캡처] 버튼 → data/capture/ 에 이미지 저장
  2. 이 탭에서 각 이미지의 정답 타겟 ID를 지정
  3. [레이블 저장] → data/gt_labeled/{타겟ID}_{파일명}.png 으로 복사
  4. 파라미터 가이드 → [정답 데이터 기반 분석] → [폴더 선택] 에서 gt_labeled 폴더 사용
"""
import os, json, cv2
import numpy as np
from PyQt5.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel,
    QListWidget, QListWidgetItem, QPushButton,
    QComboBox, QFrame, QMessageBox, QSizePolicy, QTabWidget
)
from PyQt5.QtCore import Qt
from PyQt5.QtGui import QImage, QPixmap

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

CAPTURE_DIR    = os.path.join(_ROOT, "data", "capture")
GT_LABELED_DIR = os.path.join(_ROOT, "data", "gt_labeled")
LABELS_FILE    = os.path.join(_ROOT, "data", "gt_labels.json")
TARGET_DIR     = os.path.join(_ROOT, "data", "targets")

C_BG     = "#F8F9FA"
C_WHITE  = "#FFFFFF"
C_DARK   = "#2C3E50"
C_SUB    = "#7F8C8D"
C_BLUE   = "#3498DB"
C_GREEN  = "#27AE60"
C_RED    = "#E74C3C"
C_ORANGE = "#E67E22"
C_BORDER = "#E0E4E8"
C_PURPLE = "#9B59B6"


def _lbl(text, size=12, bold=False, color=C_DARK, wrap=True):
    l = QLabel(text)
    style = f"font-size:{size}px; color:{color};"
    if bold:
        style += " font-weight:bold;"
    l.setStyleSheet(style)
    l.setWordWrap(wrap)
    return l


class _ORBLabelingWidget(QWidget):
    """ORB 정답 라벨링 내부 위젯 — 캡처 이미지에 타겟 ID를 지정해 GT 데이터셋 구성"""

    def __init__(self):
        super().__init__()
        self.setStyleSheet(f"background:{C_BG};")
        self._labels   = self._load_labels()   # {filename: target_id}
        self._cur_file = ""

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # ── 상단 헤더 바 ────────────────────────────────────────
        hdr = QWidget()
        hdr.setFixedHeight(54)
        hdr.setStyleSheet(f"background:{C_WHITE}; border-bottom:1px solid {C_BORDER};")
        hh = QHBoxLayout(hdr)
        hh.setContentsMargins(24, 0, 24, 0)
        hh.addWidget(_lbl("정답 라벨링", size=17, bold=True, wrap=False))
        hh.addWidget(_lbl(
            "  [GT 캡처] 버튼으로 수집한 이미지에 정답 타겟을 지정합니다.",
            size=11, color=C_SUB, wrap=False))
        hh.addStretch()

        self._stats_lbl = _lbl("", size=12, color=C_SUB, wrap=False)
        hh.addWidget(self._stats_lbl)

        btn_refresh = QPushButton("🔄 새로고침")
        btn_refresh.setFixedSize(95, 32)
        btn_refresh.clicked.connect(self._load_list)
        hh.addWidget(btn_refresh)

        root.addWidget(hdr)

        # ── 본문 ────────────────────────────────────────────────
        body_w = QWidget()
        body   = QHBoxLayout(body_w)
        body.setContentsMargins(12, 12, 12, 12)
        body.setSpacing(12)

        # ── 좌측: 캡처 이미지 목록 ─────────────────────────────
        left = QWidget()
        left.setFixedWidth(270)
        left.setStyleSheet(
            f"background:{C_WHITE}; border:1px solid {C_BORDER}; border-radius:8px;")
        lv = QVBoxLayout(left)
        lv.setContentsMargins(10, 10, 10, 10)
        lv.setSpacing(6)
        lv.addWidget(_lbl("캡처 이미지 목록", bold=True))
        lv.addWidget(_lbl(f"폴더: ...data/capture/", size=10, color=C_SUB))

        self.file_list = QListWidget()
        self.file_list.setStyleSheet(
            f"QListWidget{{background:{C_BG};border:1px solid {C_BORDER};"
            f"border-radius:4px;font-size:11px;}}"
            f"QListWidget::item{{padding:4px;}}"
            f"QListWidget::item:selected{{background:#EBF5FB;color:{C_BLUE};}}")
        self.file_list.currentItemChanged.connect(self._on_select)
        lv.addWidget(self.file_list, stretch=1)

        btn_del = QPushButton("🗑  선택 이미지 삭제")
        btn_del.clicked.connect(self._delete_selected)
        lv.addWidget(btn_del)
        body.addWidget(left)

        # ── 중앙: 이미지 미리보기 ───────────────────────────────
        center = QWidget()
        center.setStyleSheet(
            f"background:{C_WHITE}; border:1px solid {C_BORDER}; border-radius:8px;")
        cv_layout = QVBoxLayout(center)
        cv_layout.setContentsMargins(10, 10, 10, 10)
        cv_layout.setSpacing(6)
        cv_layout.addWidget(_lbl("이미지 미리보기", bold=True))

        self.preview = QLabel()
        self.preview.setAlignment(Qt.AlignCenter)
        self.preview.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Ignored)
        self.preview.setStyleSheet(
            "background:#1a1a2e; border-radius:6px;"
            f"color:{C_SUB}; font-size:13px;")
        self.preview.setText("← 왼쪽에서 이미지를 선택하세요")
        cv_layout.addWidget(self.preview, stretch=1)

        self._fname_lbl = _lbl("", size=11, color=C_SUB, wrap=False)
        cv_layout.addWidget(self._fname_lbl)
        body.addWidget(center, stretch=1)

        # ── 우측: 레이블 지정 패널 ─────────────────────────────
        right = QWidget()
        right.setFixedWidth(230)
        right.setStyleSheet(
            f"background:{C_WHITE}; border:1px solid {C_BORDER}; border-radius:8px;")
        rv = QVBoxLayout(right)
        rv.setContentsMargins(14, 14, 14, 14)
        rv.setSpacing(10)

        rv.addWidget(_lbl("정답 타겟 지정", size=14, bold=True))

        self._cur_label_lbl = _lbl("이미지를 선택하세요", size=12, color=C_SUB)
        rv.addWidget(self._cur_label_lbl)

        sep = QFrame(); sep.setFrameShape(QFrame.HLine)
        sep.setStyleSheet(f"color:{C_BORDER};")
        rv.addWidget(sep)

        rv.addWidget(_lbl("정답 타겟 ID:", bold=True))
        self.target_combo = QComboBox()
        self.target_combo.setStyleSheet(
            f"QComboBox{{border:1px solid {C_BORDER};border-radius:4px;"
            f"padding:5px;font-size:13px;font-weight:bold;background:{C_WHITE};}}")
        self._populate_targets()
        rv.addWidget(self.target_combo)

        self._save_btn = QPushButton("💾  레이블 저장")
        self._save_btn.setEnabled(False)
        self._save_btn.setStyleSheet(
            f"QPushButton{{background:{C_GREEN};color:white;border:none;"
            f"border-radius:6px;font-size:13px;font-weight:bold;padding:8px;}}"
            f"QPushButton:hover{{background:#219a52;}}"
            f"QPushButton:disabled{{background:{C_BORDER};color:{C_SUB};}}")
        self._save_btn.clicked.connect(self._save_label)
        rv.addWidget(self._save_btn)

        sep2 = QFrame(); sep2.setFrameShape(QFrame.HLine)
        sep2.setStyleSheet(f"color:{C_BORDER};")
        rv.addWidget(sep2)

        rv.addWidget(_lbl("저장 경로:", size=11, bold=True))
        rv.addWidget(_lbl(
            "data/gt_labeled/\n"
            "{타겟ID}_{파일명}.png",
            size=10, color=C_BLUE))

        sep3 = QFrame(); sep3.setFrameShape(QFrame.HLine)
        sep3.setStyleSheet(f"color:{C_BORDER};")
        rv.addWidget(sep3)

        rv.addWidget(_lbl(
            "파라미터 가이드 탭 →\n[정답 데이터 기반 분석] →\n[폴더 선택] 에서\ngt_labeled 폴더를 사용하세요.",
            size=11, color=C_DARK))

        btn_open = QPushButton("📂  GT 폴더 열기")
        btn_open.clicked.connect(self._open_gt_folder)
        rv.addWidget(btn_open)

        rv.addStretch()
        body.addWidget(right)

        root.addWidget(body_w, stretch=1)

        self._load_list()

    def showEvent(self, event):
        """탭이 활성화될 때마다 목록 자동 새로고침"""
        super().showEvent(event)
        self._load_list()

    # ── 데이터 로드/저장 ────────────────────────────────────────────────────

    def _load_labels(self):
        if os.path.isfile(LABELS_FILE):
            try:
                with open(LABELS_FILE, encoding="utf-8") as f:
                    return json.load(f)
            except Exception:
                pass
        return {}

    def _save_labels(self):
        os.makedirs(os.path.dirname(LABELS_FILE), exist_ok=True)
        with open(LABELS_FILE, "w", encoding="utf-8") as f:
            json.dump(self._labels, f, indent=2, ensure_ascii=False)

    def _populate_targets(self):
        """사용 가능한 타겟 ID를 target_image 폴더에서 읽어옴. 'none(부정답)' 항목 포함."""
        self.target_combo.clear()
        # 부정답 옵션 — 어떤 타겟과도 매칭되면 안 되는 이미지
        self.target_combo.addItem("none  (정답 없음 — 부정답 이미지)", "none")
        ids = []
        if os.path.isdir(TARGET_DIR):
            for fname in sorted(os.listdir(TARGET_DIR)):
                if fname.lower().endswith((".png", ".jpg", ".jpeg")):
                    ids.append(os.path.splitext(fname)[0])
        if not ids:
            ids = ["1", "2", "3", "4"]
        for tid in ids:
            self.target_combo.addItem(f"타겟  {tid}", tid)

    def _load_list(self):
        self.file_list.clear()
        os.makedirs(CAPTURE_DIR, exist_ok=True)
        files = sorted([
            f for f in os.listdir(CAPTURE_DIR)
            if f.lower().endswith((".png", ".jpg", ".jpeg"))
        ])
        labeled = 0
        for fname in files:
            item = QListWidgetItem()
            if fname in self._labels:
                tid = self._labels[fname]
                if tid == "none":
                    item.setText(f"✖  [부정답]  {fname}")
                else:
                    item.setText(f"✅  [{tid}]  {fname}")
                labeled += 1
            else:
                item.setText(f"❓  {fname}")
            item.setData(Qt.UserRole, fname)
            self.file_list.addItem(item)
        total = len(files)
        self._stats_lbl.setText(f"레이블 완료: {labeled} / {total}  |  미완료: {total - labeled}  |")

    # ── 이벤트 핸들러 ────────────────────────────────────────────────────────

    def _on_select(self, cur, _prev):
        if cur is None:
            return
        fname = cur.data(Qt.UserRole)
        self._cur_file  = fname
        self._fname_lbl.setText(f"파일명: {fname}")
        self._save_btn.setEnabled(True)

        # 미리보기 렌더링
        path = os.path.join(CAPTURE_DIR, fname)
        try:
            buf = np.fromfile(path, dtype=np.uint8)
            img = cv2.imdecode(buf, cv2.IMREAD_COLOR)
            if img is not None:
                rgb  = cv2.cvtColor(img, cv2.COLOR_BGR2RGB)
                h, w = rgb.shape[:2]
                qimg = QImage(rgb.tobytes(), w, h, 3 * w, QImage.Format_RGB888)
                px   = QPixmap.fromImage(qimg).scaled(
                    self.preview.size(), Qt.KeepAspectRatio, Qt.SmoothTransformation)
                self.preview.setPixmap(px)
            else:
                self.preview.setText("이미지 로드 실패")
        except Exception as ex:
            self.preview.setText(f"로드 오류:\n{ex}")

        # 현재 레이블 표시 및 콤보박스 동기화
        if fname in self._labels:
            tid = self._labels[fname]
            if tid == "none":
                self._cur_label_lbl.setText("현재 레이블: ✖ 부정답 (정답 없음)")
                self._cur_label_lbl.setStyleSheet(
                    f"font-size:12px; color:{C_RED}; font-weight:bold;")
            else:
                self._cur_label_lbl.setText(f"현재 레이블: ✅ 타겟 {tid}")
                self._cur_label_lbl.setStyleSheet(
                    f"font-size:12px; color:{C_GREEN}; font-weight:bold;")
            # 콤보박스를 저장된 data 값으로 맞추기
            for i in range(self.target_combo.count()):
                if self.target_combo.itemData(i) == tid:
                    self.target_combo.setCurrentIndex(i)
                    break
        else:
            self._cur_label_lbl.setText("미레이블 — 정답 미지정")
            self._cur_label_lbl.setStyleSheet(
                f"font-size:12px; color:{C_ORANGE};")

    def _save_label(self):
        if not self._cur_file:
            return
        # currentData() 로 실제 레이블 값 가져오기 ("none" 또는 "1"/"2"/...)
        tid = self.target_combo.currentData()
        if not tid:
            return

        # JSON 레이블 저장
        self._labels[self._cur_file] = tid
        self._save_labels()

        # GT 폴더에 이미지 복사: {레이블}_{원본파일명}.png
        os.makedirs(GT_LABELED_DIR, exist_ok=True)
        src = os.path.join(CAPTURE_DIR, self._cur_file)
        dst = os.path.join(GT_LABELED_DIR, f"{tid}_{self._cur_file}")
        try:
            buf = np.fromfile(src, dtype=np.uint8)
            img = cv2.imdecode(buf, cv2.IMREAD_COLOR)
            if img is not None:
                _, enc = cv2.imencode(".png", img)
                enc.tofile(dst)
        except Exception as ex:
            QMessageBox.critical(self, "복사 실패", str(ex))
            return

        if tid == "none":
            self._cur_label_lbl.setText("저장됨: ✖ 부정답")
            self._cur_label_lbl.setStyleSheet(
                f"font-size:12px; color:{C_RED}; font-weight:bold;")
        else:
            self._cur_label_lbl.setText(f"저장됨: ✅ 타겟 {tid}")
            self._cur_label_lbl.setStyleSheet(
                f"font-size:12px; color:{C_GREEN}; font-weight:bold;")
        self._load_list()

        # 다음 미레이블 항목으로 자동 이동
        for i in range(self.file_list.count()):
            item = self.file_list.item(i)
            if item and item.data(Qt.UserRole) not in self._labels:
                self.file_list.setCurrentItem(item)
                break

    def _delete_selected(self):
        item = self.file_list.currentItem()
        if not item:
            return
        fname = item.data(Qt.UserRole)
        ret = QMessageBox.question(
            self, "삭제 확인",
            f"'{fname}' 을 삭제하시겠습니까?\n(data/capture/ 에서 삭제됩니다)",
            QMessageBox.Yes | QMessageBox.No)
        if ret != QMessageBox.Yes:
            return
        try:
            os.remove(os.path.join(CAPTURE_DIR, fname))
        except Exception:
            pass
        if fname in self._labels:
            del self._labels[fname]
            self._save_labels()
        self.preview.clear()
        self.preview.setText("← 왼쪽에서 이미지를 선택하세요")
        self._save_btn.setEnabled(False)
        self._cur_file = ""
        self._load_list()

    def _open_gt_folder(self):
        os.makedirs(GT_LABELED_DIR, exist_ok=True)
        import subprocess
        subprocess.Popen(f'explorer "{GT_LABELED_DIR}"')


# ─────────────────────────────────────────────────────────────────────────────
class LabelingTab(QWidget):
    """
    정답 라벨링 통합 탭.
    서브탭:
      - ORB 정답 라벨링     : ORB GT 데이터셋 구성 (기존 기능)
      - 세그멘테이션 학습 데이터 : YOLO-Seg 훈련 데이터 폴리곤 라벨링
    """

    def __init__(self):
        super().__init__()
        self.setStyleSheet(f"background:{C_BG};")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        self._inner_tabs = QTabWidget()
        # 전역 스타일시트의 큰 탭 스타일이 내부 탭에도 상속되지 않도록 별도 스타일 지정
        self._inner_tabs.setStyleSheet(f"""
            QTabWidget::pane {{
                border: none;
                background: {C_BG};
                border-radius: 0;
            }}
            QTabBar::tab {{
                background: {C_BG};
                color: {C_SUB};
                padding: 6px 18px;
                font-size: 12px;
                font-weight: bold;
                border: 1px solid {C_BORDER};
                border-bottom: none;
                border-top-left-radius: 5px;
                border-top-right-radius: 5px;
                margin-right: 2px;
            }}
            QTabBar::tab:selected {{
                background: {C_WHITE};
                color: {C_BLUE};
                border-bottom: 2px solid {C_BLUE};
            }}
            QTabBar::tab:hover {{ color: {C_DARK}; }}
        """)

        self._orb_widget = _ORBLabelingWidget()

        try:
            from gui.tab_seg_labeling import SegLabelingWidget
            self._seg_widget = SegLabelingWidget()
        except Exception as e:
            print(f"[LabelingTab] 세그멘테이션 탭 로드 실패: {e}")
            placeholder = QWidget()
            pl = QVBoxLayout(placeholder)
            lb = QLabel("세그멘테이션 탭 로드 실패")
            lb.setAlignment(Qt.AlignCenter)
            lb.setStyleSheet(f"font-size:16px; color:{C_SUB};")
            pl.addWidget(lb)
            self._seg_widget = placeholder

        self._inner_tabs.addTab(self._orb_widget, "  🏷  ORB 정답 라벨링  ")
        self._inner_tabs.addTab(self._seg_widget, "  🔷  세그멘테이션 학습 데이터  ")
        layout.addWidget(self._inner_tabs)

    def showEvent(self, event):
        """외부 탭 전환 시 현재 활성 서브탭에도 showEvent 전달 (목록 자동 새로고침)"""
        super().showEvent(event)
        current = self._inner_tabs.currentWidget()
        if current is not None and hasattr(current, "showEvent"):
            current.showEvent(event)
