"""
tab_guide.py — TAB 4: 파라미터 가이드
각 ORB / 전처리 / 임계값 파라미터가 무엇을 의미하고
올리면/내리면 어떤 일이 생기는지를 설명하는 레퍼런스 탭입니다.
파라미터 값을 직접 수정하고 저장할 수 있습니다.
"""
import os, json, itertools
import numpy as np
from PyQt5.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel,
    QScrollArea, QFrame, QSizePolicy,
    QPushButton, QSpinBox, QDoubleSpinBox, QMessageBox,
    QProgressBar, QTextEdit, QTabWidget, QFileDialog
)
from PyQt5.QtCore import QThread, pyqtSignal

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PARAMS_CONFIG_FILE = os.path.join(_ROOT, "data", "params_config.json")

C_BG     = "#F8F9FA"
C_WHITE  = "#FFFFFF"
C_DARK   = "#2C3E50"
C_SUB    = "#7F8C8D"
C_BLUE   = "#3498DB"
C_GREEN  = "#27AE60"
C_RED    = "#E74C3C"
C_ORANGE = "#E67E22"
C_BORDER = "#E0E4E8"
C_YELLOW = "#F39C12"
C_PURPLE = "#9B59B6"

DEFAULT_CONFIG = {
    "nfeatures": 700,
    "lowe_ratio": 0.75,
    "match_threshold": 25,
    "roi_match_threshold": 7,
    "clahe_clip_limit": 2.0,
    "clahe_tile_grid": 8,
    "MATCH_THRESHOLD": 60,
    "yolo_imgsz": 640,
    "blur_ksize": 0,
    "gamma": 1.0,
    "sharpen_amount": 1.0,
}


def load_params_config() -> dict:
    """params_config.json 로드. 없으면 기본값 반환."""
    if os.path.isfile(PARAMS_CONFIG_FILE):
        try:
            with open(PARAMS_CONFIG_FILE, "r", encoding="utf-8") as f:
                cfg = json.load(f)
            return {**DEFAULT_CONFIG, **cfg}
        except Exception:
            pass
    return dict(DEFAULT_CONFIG)


def save_params_config(values: dict):
    """params_config.json 저장."""
    os.makedirs(os.path.dirname(PARAMS_CONFIG_FILE), exist_ok=True)
    with open(PARAMS_CONFIG_FILE, "w", encoding="utf-8") as f:
        json.dump(values, f, indent=4, ensure_ascii=False)


TARGET_DIR = os.path.join(_ROOT, "data", "targets")

# ─── 정답 데이터 기반 파라미터 최적화 스레드 ────────────────────────────────
class GroundTruthOptimizerThread(QThread):
    """
    실제 정답 레이블이 있는 테스트 이미지로 파라미터를 최적화합니다.
    판별력 점수 = avg(정답 타겟 점수 - 최고 오답 타겟 점수)
    이 값이 클수록 정답과 오답을 명확하게 구분할 수 있습니다.

    파일명 규칙: <타겟ID>_<설명>.png  (예: 1_capture01.png → 타겟 "1")
    """
    progress = pyqtSignal(int, int, str)
    finished = pyqtSignal(dict, str)

    _NF = [300, 500, 700, 1000, 1500]
    _LR = [0.65, 0.70, 0.75, 0.80]
    _CL = [1.0, 2.0, 3.0, 4.0]
    _CT = [4, 6, 8, 12]

    def __init__(self, test_dir: str):
        super().__init__()
        self.test_dir = test_dir

    def run(self):
        import cv2
        np.random.seed(42)

        # ① 타겟 이미지 로드
        target_imgs = {}
        if not os.path.isdir(TARGET_DIR):
            self.finished.emit({}, f"타겟 폴더를 찾을 수 없습니다:\n{TARGET_DIR}")
            return
        for fname in sorted(os.listdir(TARGET_DIR)):
            if not fname.lower().endswith((".png", ".jpg", ".jpeg")):
                continue
            buf = np.fromfile(os.path.join(TARGET_DIR, fname), dtype=np.uint8)
            img = cv2.imdecode(buf, cv2.IMREAD_COLOR)
            if img is not None:
                target_imgs[os.path.splitext(fname)[0]] = cv2.resize(img, (640, 360))

        if len(target_imgs) < 2:
            self.finished.emit({}, "타겟 이미지가 2개 이상 필요합니다.")
            return

        # ② 테스트 이미지 로드 + 레이블 파싱 (파일명 앞 '_' 앞 부분이 타겟 ID)
        test_data = []  # [(img_640x360, label_str), ...]  — 긍정 샘플
        neg_data  = []  # [img_640x360, ...]               — 부정답(none) 샘플
        for fname in sorted(os.listdir(self.test_dir)):
            if not fname.lower().endswith((".png", ".jpg", ".jpeg")):
                continue
            buf = np.fromfile(os.path.join(self.test_dir, fname), dtype=np.uint8)
            img = cv2.imdecode(buf, cv2.IMREAD_COLOR)
            if img is None:
                continue
            label = os.path.splitext(fname)[0].split('_')[0]
            if label in target_imgs:
                test_data.append((cv2.resize(img, (640, 360)), label))
            elif label == "none":
                neg_data.append(cv2.resize(img, (640, 360)))

        if not test_data and not neg_data:
            self.finished.emit({},
                "테스트 이미지를 찾을 수 없습니다.\n\n"
                "파일명 앞에 타겟 ID를 붙여주세요.\n"
                "예) 1_test01.png     →  타겟 '1' 긍정 샘플\n"
                "    none_test01.png  →  부정답(none) 샘플")
            return

        # ③ 그리드 탐색
        # 현재 설정에서 blur/gamma/sharpen 값 로드 (탐색 대상 아님 — 환경 보정 파라미터)
        _cur_cfg     = load_params_config()
        _blur_ksize  = int(_cur_cfg.get("blur_ksize", 0))
        _gamma_val   = float(_cur_cfg.get("gamma", 1.0))
        _sharpen_amt = float(_cur_cfg.get("sharpen_amount", 1.0))
        if abs(_gamma_val - 1.0) > 0.01:
            _gamma_lut = (np.power(np.arange(256) / 255.0, _gamma_val) * 255).astype(np.uint8)
        else:
            _gamma_lut = None

        combos  = list(itertools.product(self._NF, self._LR, self._CL, self._CT))
        total   = len(combos)

        best_margin = -9999.0
        best_params = {}
        best_text   = ""

        for idx, (nf, lr, cl, ct) in enumerate(combos):
            self.progress.emit(idx + 1, total,
                               f"nfeatures={nf}  lowe={lr}  clahe={cl}  tile={ct}×{ct}")

            clahe = cv2.createCLAHE(clipLimit=cl, tileGridSize=(ct, ct))
            orb   = cv2.ORB_create(nfeatures=nf)
            bf    = cv2.BFMatcher(cv2.NORM_HAMMING, crossCheck=False)

            def _pre(img, _bk=_blur_ksize, _gl=_gamma_lut, _sa=_sharpen_amt):
                gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
                if _bk > 0:
                    gray = cv2.GaussianBlur(gray, (_bk, _bk), 0)
                if _gl is not None:
                    gray = cv2.LUT(gray, _gl)
                enhanced = clahe.apply(gray)
                if _sa > 0.0:
                    blur5 = cv2.GaussianBlur(enhanced, (5, 5), 1.0)
                    return cv2.addWeighted(enhanced, 1.0 + _sa, blur5, -_sa, 0)
                return enhanced

            def _match(dA, dB):
                if dA is None or dB is None or len(dA) == 0 or len(dB) == 0:
                    return 0
                try:
                    ms = bf.knnMatch(dA, dB, k=2)
                except Exception:
                    return 0
                return sum(1 for m in ms if len(m) == 2 and m[0].distance < lr * m[1].distance)

            # 타겟별 특징점 추출
            tgt_features = {}
            for sid, img in target_imgs.items():
                _, des = orb.detectAndCompute(_pre(img), None)
                tgt_features[sid] = des

            # 긍정 샘플 margin 계산
            margins        = []
            correct_scores = []
            wrong_highs    = []
            ok_count       = 0

            for test_img, label in test_data:
                _, q_des = orb.detectAndCompute(_pre(test_img), None)
                correct  = _match(q_des, tgt_features.get(label))
                wrongs   = [_match(q_des, d) for sid, d in tgt_features.items() if sid != label]
                best_w   = max(wrongs) if wrongs else 0
                margin   = correct - best_w
                margins.append(margin)
                correct_scores.append(correct)
                wrong_highs.append(best_w)
                if margin > 0:
                    ok_count += 1

            # 부정답 샘플 — false positive 페널티
            neg_margins    = []
            neg_max_scores = []
            for neg_img in neg_data:
                _, q_des = orb.detectAndCompute(_pre(neg_img), None)
                all_scores = [_match(q_des, d) for d in tgt_features.values()]
                max_score  = max(all_scores) if all_scores else 0
                neg_max_scores.append(max_score)
                neg_margins.append(-max_score)  # 오탐 점수가 높을수록 페널티

            all_margins = margins + neg_margins

            avg_margin     = float(np.mean(all_margins))    if all_margins    else 0.0
            avg_correct    = float(np.mean(correct_scores)) if correct_scores else 0.0
            avg_wrong      = float(np.mean(wrong_highs))    if wrong_highs    else 0.0
            avg_neg_max    = float(np.mean(neg_max_scores)) if neg_max_scores else 0.0
            fp_count       = sum(1 for s in neg_max_scores if s > 0)

            if avg_margin > best_margin:
                best_margin  = avg_margin
                roi_thr      = max(3, round(avg_wrong * 0.12) + 2)
                match_thr    = max(5, int(avg_wrong) + 2)
                best_params  = {
                    "nfeatures":           nf,
                    "lowe_ratio":          lr,
                    "clahe_clip_limit":    cl,
                    "clahe_tile_grid":     ct,
                    "match_threshold":     match_thr,
                    "roi_match_threshold": roi_thr,
                }
                pos_n   = len(test_data)
                neg_n   = len(neg_data)
                pct     = ok_count / pos_n * 100 if pos_n > 0 else 0.0
                fp_rate = fp_count / neg_n * 100  if neg_n > 0 else 0.0

                pos_block = (
                    f"\n[긍정 샘플 결과 ({pos_n}장)]\n"
                    f"  정답 타겟 평균 점수     : {avg_correct:.1f}쌍\n"
                    f"  오답 타겟 최고 점수     : {avg_wrong:.1f}쌍\n"
                    f"  정답 > 오답 성공률      : {ok_count}/{pos_n} ({pct:.0f}%)\n"
                ) if pos_n > 0 else ""

                neg_block = (
                    f"\n[부정답 샘플 결과 ({neg_n}장)]\n"
                    f"  평균 최고 타겟 점수     : {avg_neg_max:.1f}쌍  (낮을수록 좋음)\n"
                    f"  오탐 발생 수            : {fp_count}/{neg_n} ({fp_rate:.0f}%)\n"
                ) if neg_n > 0 else ""

                best_text = (
                    f"[최적 파라미터 — 정답 데이터 기반]\n"
                    f"  nfeatures        = {nf}\n"
                    f"  lowe_ratio       = {lr}\n"
                    f"  clahe_clip_limit = {cl}\n"
                    f"  clahe_tile_grid  = {ct}  (→ {ct}×{ct} 타일)\n"
                    f"{pos_block}"
                    f"{neg_block}"
                    f"\n[종합 평균 판별 마진 (↑ 좋음): {avg_margin:.1f}]\n"
                    f"\n[자동 권장 임계값]\n"
                    f"  match_threshold     = {match_thr}\n"
                    f"  roi_match_threshold = {roi_thr}"
                )

        self.finished.emit(best_params, best_text)


# ─── 정답 데이터 기반 최적화 패널 ────────────────────────────────────────────
class GroundTruthOptimizerPanel(QWidget):
    """정답 레이블이 있는 테스트 이미지 폴더를 선택해 파라미터를 최적화하는 패널"""
    apply_requested = pyqtSignal(dict)

    def __init__(self):
        super().__init__()
        self._best_params = {}
        self._thread      = None
        self._test_dir    = ""
        self.setStyleSheet(f"background:{C_WHITE};")

        root = QVBoxLayout(self)
        root.setContentsMargins(28, 12, 28, 12)
        root.setSpacing(8)

        # ── 폴더 선택 행 ───────────────────────��────────────────
        dir_row = QHBoxLayout()
        dir_row.addWidget(_lbl("테스트 이미지 폴더:", size=12, bold=True, color=C_DARK, wrap=False))
        self._dir_lbl = _lbl("(폴더를 선택하세요)", size=11, color=C_SUB, wrap=False)
        dir_row.addWidget(self._dir_lbl, stretch=1)
        browse_btn = QPushButton("폴더 선택")
        browse_btn.setFixedSize(90, 28)
        browse_btn.setStyleSheet(
            f"QPushButton{{background:{C_BLUE}22;color:{C_BLUE};border:1px solid {C_BLUE}55;"
            f"border-radius:5px;font-size:11px;}}"
            f"QPushButton:hover{{background:{C_BLUE}44;}}"
        )
        browse_btn.clicked.connect(self._browse)
        dir_row.addWidget(browse_btn)
        root.addLayout(dir_row)

        rule_lbl = _lbl(
            "파일명 규칙:  <타겟ID>_설명.png   예) 1_capture01.png → 타겟 1의 정답 이미지",
            size=11, color=C_SUB
        )
        root.addWidget(rule_lbl)

        # ── 실행 버튼 행 ─────────────────────────────────────────
        hdr = QHBoxLayout()
        hdr.addWidget(_lbl("정답 데이터 기반 파라미터 최적화",
                           size=13, bold=True, color=C_DARK, wrap=False))
        hdr.addStretch()
        self._run_btn = QPushButton("분석 시작")
        self._run_btn.setFixedSize(100, 32)
        self._run_btn.setEnabled(False)
        self._run_btn.setStyleSheet(
            f"QPushButton{{background:{C_RED};color:white;border:none;"
            f"border-radius:6px;font-size:12px;font-weight:bold;}}"
            f"QPushButton:hover{{background:#c0392b;}}"
            f"QPushButton:disabled{{background:{C_BORDER};color:{C_SUB};}}"
        )
        self._run_btn.clicked.connect(self._start)
        hdr.addWidget(self._run_btn)
        root.addLayout(hdr)

        # ── 진행 바 ─────────────────────────────────���────────────
        self._prog = QProgressBar()
        self._prog.setFixedHeight(10)
        self._prog.setTextVisible(False)
        self._prog.setStyleSheet(
            f"QProgressBar{{background:{C_BORDER};border-radius:4px;border:none;}}"
            f"QProgressBar::chunk{{background:{C_RED};border-radius:4px;}}"
        )
        self._prog.hide()
        root.addWidget(self._prog)

        self._status = _lbl("", size=11, color=C_SUB, wrap=False)
        self._status.hide()
        root.addWidget(self._status)

        # ── 결과 영역 ────────────────────────────────────────────
        self._result = QTextEdit()
        self._result.setReadOnly(True)
        self._result.setFixedHeight(130)
        self._result.setStyleSheet(
            f"QTextEdit{{background:#FDF2F2;border:1px solid {C_RED}44;"
            f"border-radius:6px;font-size:12px;font-family:'Malgun Gothic','Consolas';}}"
        )
        self._result.setPlaceholderText(
            "정답 이미지 폴더를 선택한 뒤 [분석 시작]을 눌러주세요.\n\n"
            "결과: 정답 타겟 점수 vs 오답 타겟 점수의 판별 마진이 최대인 파라미터를 찾습니다."
        )
        root.addWidget(self._result)

        # ── 적용 버튼 ────────────────────────────────────────────
        apply_row = QHBoxLayout()
        apply_row.addStretch()
        self._apply_btn = QPushButton("최적값 스핀박스에 적용")
        self._apply_btn.setFixedSize(160, 30)
        self._apply_btn.setEnabled(False)
        self._apply_btn.setStyleSheet(
            f"QPushButton{{background:{C_GREEN};color:white;border:none;"
            f"border-radius:6px;font-size:12px;font-weight:bold;}}"
            f"QPushButton:hover{{background:#219a52;}}"
            f"QPushButton:disabled{{background:{C_BORDER};color:{C_SUB};}}"
        )
        self._apply_btn.clicked.connect(lambda: self.apply_requested.emit(self._best_params))
        apply_row.addWidget(self._apply_btn)
        root.addLayout(apply_row)

    def _browse(self):
        d = QFileDialog.getExistingDirectory(self, "테스트 이미지 폴더 선택")
        if d:
            self._test_dir = d
            self._dir_lbl.setText(d)
            self._run_btn.setEnabled(True)

    def _start(self):
        if not self._test_dir or not os.path.isdir(self._test_dir):
            QMessageBox.warning(self, "폴더 없음", "테스트 이미지 폴더를 먼저 선택하세요.")
            return
        self._run_btn.setEnabled(False)
        self._apply_btn.setEnabled(False)
        self._best_params = {}
        self._prog.setValue(0); self._prog.show()
        self._status.show(); self._result.clear()

        self._thread = GroundTruthOptimizerThread(self._test_dir)
        self._thread.progress.connect(self._on_progress)
        self._thread.finished.connect(self._on_finished)
        self._thread.start()

    def _on_progress(self, cur, total, msg):
        self._prog.setMaximum(total)
        self._prog.setValue(cur)
        self._status.setText(f"[{cur}/{total}]  {msg}")

    def _on_finished(self, best_params, text):
        self._prog.hide(); self._status.hide()
        self._run_btn.setEnabled(True)
        self._result.setPlainText(text)
        if best_params:
            self._best_params = best_params
            self._apply_btn.setEnabled(True)


# ─── 자동 파라미터 최적화 스레드 ────────────────────────────────────────────
class ParamOptimizerThread(QThread):
    """
    타겟 이미지 N장을 대상으로 파라미터 그리드 탐색을 수행합니다.
    판별력 점수 = min(타겟 자기 매칭) - max(타겟 간 교차 매칭)
    이 값이 클수록 각 타겟을 명확하게 구분할 수 있습니다.
    """
    progress = pyqtSignal(int, int, str)          # 현재, 전체, 상태 문자열
    finished = pyqtSignal(dict, str)              # 최적 params, 결과 텍스트

    # 탐색할 파라미터 격자
    _NF   = [300, 500, 700, 1000, 1500]
    _LR   = [0.65, 0.70, 0.75, 0.80]
    _CL   = [1.0, 2.0, 3.0, 4.0]
    _CT   = [4, 6, 8, 12]

    def run(self):
        import cv2
        np.random.seed(42)   # 재현 가능한 노이즈 시뮬레이션

        # ① 타겟 이미지 로드
        imgs = []
        for fname in sorted(os.listdir(TARGET_DIR)):
            if not fname.lower().endswith((".png", ".jpg", ".jpeg")):
                continue
            path = os.path.join(TARGET_DIR, fname)
            buf  = np.fromfile(path, dtype=np.uint8)
            img  = cv2.imdecode(buf, cv2.IMREAD_COLOR)
            if img is not None:
                imgs.append((fname, cv2.resize(img, (640, 360))))

        if len(imgs) < 2:
            self.finished.emit({}, "타겟 이미지가 2개 이상 필요합니다.")
            return

        # 현재 설정에서 blur/gamma/sharpen 값 로드 (탐색 대상 아님 — 환경 보정 파라미터)
        _cur_cfg       = load_params_config()
        _blur_ksize    = int(_cur_cfg.get("blur_ksize", 0))
        _gamma_val     = float(_cur_cfg.get("gamma", 1.0))
        _sharpen_amt   = float(_cur_cfg.get("sharpen_amount", 1.0))
        if abs(_gamma_val - 1.0) > 0.01:
            _gamma_lut = (np.power(np.arange(256) / 255.0, _gamma_val) * 255).astype(np.uint8)
        else:
            _gamma_lut = None

        combos  = list(itertools.product(self._NF, self._LR, self._CL, self._CT))
        total   = len(combos)

        best_gap    = -9999
        best_params = {}
        best_text   = ""

        for idx, (nf, lr, cl, ct) in enumerate(combos):
            self.progress.emit(idx + 1, total,
                               f"nfeatures={nf}  lowe={lr}  clahe={cl}  tile={ct}×{ct}")

            clahe = cv2.createCLAHE(clipLimit=cl, tileGridSize=(ct, ct))
            orb   = cv2.ORB_create(nfeatures=nf)
            bf    = cv2.BFMatcher(cv2.NORM_HAMMING, crossCheck=False)

            # ② 각 타겟 전처리 + 특징점 추출
            features = []   # (fname, des_orig, des_noisy)
            for fname, img in imgs:
                gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
                if _blur_ksize > 0:
                    gray = cv2.GaussianBlur(gray, (_blur_ksize, _blur_ksize), 0)
                if _gamma_lut is not None:
                    gray = cv2.LUT(gray, _gamma_lut)
                enhanced = clahe.apply(gray)
                if _sharpen_amt > 0.0:
                    blur5 = cv2.GaussianBlur(enhanced, (5, 5), 1.0)
                    enh = cv2.addWeighted(enhanced, 1.0 + _sharpen_amt, blur5, -_sharpen_amt, 0)
                else:
                    enh = enhanced
                # 카메라-스크린샷 도메인 갭 시뮬레이션: 약한 노이즈 + 밝기 변화
                noise  = (np.random.randint(-12, 13, enh.shape)).astype(np.int16)
                noisy  = np.clip(enh.astype(np.int16) + noise, 0, 255).astype(np.uint8)
                _, d0  = orb.detectAndCompute(enh,   None)
                _, d1  = orb.detectAndCompute(noisy, None)
                features.append((fname, d0, d1))

            def _match(dA, dB):
                if dA is None or dB is None or len(dA) == 0 or len(dB) == 0:
                    return 0
                try:
                    ms = bf.knnMatch(dA, dB, k=2)
                except Exception:
                    return 0
                return sum(1 for m in ms if len(m) == 2 and m[0].distance < lr * m[1].distance)

            # ③ 자기 매칭 (원본 vs 노이즈 버전)
            self_scores  = [_match(d0, d1) for _, d0, d1 in features]
            # ④ 교차 매칭 (서로 다른 타겟 간)
            cross_scores = [
                _match(features[a][1], features[b][1])
                for a in range(len(features))
                for b in range(len(features)) if a != b
            ]

            min_self  = min(self_scores)  if self_scores  else 0
            max_cross = max(cross_scores) if cross_scores else 0
            gap       = min_self - max_cross   # 판별력 갭 (클수록 좋음)

            if gap > best_gap:
                best_gap    = gap
                # ROI 크롭은 전체 이미지의 약 10~15% 면적 → 교차 점수도 비례 감소
                roi_thr = max(3, round(max_cross * 0.12) + 2)
                best_params = {
                    "nfeatures":          nf,
                    "lowe_ratio":         lr,
                    "clahe_clip_limit":   cl,
                    "clahe_tile_grid":    ct,
                    "match_threshold":    max(5, max_cross + 2),
                    "roi_match_threshold": roi_thr,
                }
                self_str  = "  ".join(f"{f[0]}: {s}쌍"
                                      for f, s in zip(features, self_scores))
                best_text = (
                    f"[최적 파라미터]\n"
                    f"  nfeatures        = {nf}\n"
                    f"  lowe_ratio       = {lr}\n"
                    f"  clahe_clip_limit = {cl}\n"
                    f"  clahe_tile_grid  = {ct}  (→ {ct}×{ct} 타일)\n"
                    f"\n[매칭 점수 — 전체 이미지 기준]\n"
                    f"  자기 매칭 (노이즈 추가)      : {self_str}\n"
                    f"  타겟 간 교차 매칭 최대        : {max_cross}쌍\n"
                    f"  판별력 갭 (↑ 클수록 좋음)    : {gap}\n"
                    f"\n[자동 권장 임계값]\n"
                    f"  match_threshold     = {best_params['match_threshold']}  (전체이미지 폴백)\n"
                    f"  roi_match_threshold = {roi_thr}  (ROI 크롭 기준 추정)"
                )

        self.finished.emit(best_params, best_text)


# ─── 최적화 패널 위젯 ────────────────────────────────────────────────────────
class OptimizerPanel(QWidget):
    """타이틀바 아래에 붙는 자동 최적화 패널"""

    apply_requested = pyqtSignal(dict)  # GuideTab에 최적값 적용 요청

    def __init__(self):
        super().__init__()
        self._best_params = {}
        self._thread = None
        self.setStyleSheet(
            f"background:{C_WHITE}; border-bottom:1px solid {C_BORDER};"
        )

        root = QVBoxLayout(self)
        root.setContentsMargins(28, 12, 28, 12)
        root.setSpacing(8)

        # ── 헤더 행 ───────────────────────────────────────────
        hdr = QHBoxLayout()
        hdr.addWidget(_lbl("타겟 이미지 기반 자동 파라미터 최적화",
                           size=13, bold=True, color=C_DARK, wrap=False))
        hdr.addWidget(_lbl(
            "  타겟 이미지들을 분석해 판별력이 가장 높은 ORB 파라미터 조합을 자동 탐색합니다.",
            size=12, color=C_SUB, wrap=False))
        hdr.addStretch()

        self._run_btn = QPushButton("분석 시작")
        self._run_btn.setFixedSize(100, 32)
        self._run_btn.setStyleSheet(
            f"QPushButton {{ background:{C_BLUE}; color:white; border:none;"
            f"  border-radius:6px; font-size:12px; font-weight:bold; }}"
            f"QPushButton:hover  {{ background:#2980b9; }}"
            f"QPushButton:disabled {{ background:{C_BORDER}; color:{C_SUB}; }}"
        )
        self._run_btn.clicked.connect(self._start)
        hdr.addWidget(self._run_btn)
        root.addLayout(hdr)

        # ── 진행 바 ───────────────────────────────────────────
        self._prog = QProgressBar()
        self._prog.setFixedHeight(10)
        self._prog.setTextVisible(False)
        self._prog.setStyleSheet(
            f"QProgressBar {{ background:{C_BORDER}; border-radius:4px; border:none; }}"
            f"QProgressBar::chunk {{ background:{C_BLUE}; border-radius:4px; }}"
        )
        self._prog.hide()
        root.addWidget(self._prog)

        self._status = _lbl("", size=11, color=C_SUB, wrap=False)
        self._status.hide()
        root.addWidget(self._status)

        # ── 결과 영역 ─────────────────────────────────────────
        self._result = QTextEdit()
        self._result.setReadOnly(True)
        self._result.setFixedHeight(130)
        self._result.setStyleSheet(
            f"QTextEdit {{ background:#F0F4F8; border:1px solid {C_BORDER};"
            f"  border-radius:6px; font-size:12px; font-family:'Malgun Gothic','Consolas'; }}"
        )
        self._result.setPlaceholderText("분석 시작 버튼을 눌러 최적 파라미터를 탐색하세요.")
        root.addWidget(self._result)

        # ── 적용 버튼 ─────────────────────────────────────────
        apply_row = QHBoxLayout()
        apply_row.addStretch()
        self._apply_btn = QPushButton("최적값 스핀박스에 적용")
        self._apply_btn.setFixedSize(160, 30)
        self._apply_btn.setEnabled(False)
        self._apply_btn.setStyleSheet(
            f"QPushButton {{ background:{C_GREEN}; color:white; border:none;"
            f"  border-radius:6px; font-size:12px; font-weight:bold; }}"
            f"QPushButton:hover {{ background:#219a52; }}"
            f"QPushButton:disabled {{ background:{C_BORDER}; color:{C_SUB}; }}"
        )
        self._apply_btn.clicked.connect(lambda: self.apply_requested.emit(self._best_params))
        apply_row.addWidget(self._apply_btn)
        root.addLayout(apply_row)

    def _start(self):
        if not os.path.isdir(TARGET_DIR) or not any(
            f.lower().endswith((".png",".jpg",".jpeg"))
            for f in os.listdir(TARGET_DIR)
        ):
            QMessageBox.warning(self, "타겟 없음",
                                f"타겟 이미지 폴더가 비어 있습니다.\n{TARGET_DIR}")
            return

        self._run_btn.setEnabled(False)
        self._apply_btn.setEnabled(False)
        self._best_params = {}
        self._prog.setValue(0)
        self._prog.show()
        self._status.show()
        self._result.clear()

        self._thread = ParamOptimizerThread()
        self._thread.progress.connect(self._on_progress)
        self._thread.finished.connect(self._on_finished)
        self._thread.start()

    def _on_progress(self, cur, total, msg):
        self._prog.setMaximum(total)
        self._prog.setValue(cur)
        self._status.setText(f"[{cur}/{total}]  {msg}")

    def _on_finished(self, best_params, text):
        self._prog.hide()
        self._status.hide()
        self._run_btn.setEnabled(True)
        self._result.setPlainText(text)
        if best_params:
            self._best_params = best_params
            self._apply_btn.setEnabled(True)


# ─── 파라미터 데이터 정의 ────────────────────────────────────────────────────
# param_key  : config JSON 키. None이면 편집 불가
# input_type : "int" | "float" | None
# val_min/max: 허용 범위
# step       : 스핀박스 한 칸 단위
PARAMS = [
    # ── ORB ──────────────────────────────────────────────────────────────────
    {
        "name": "nfeatures",
        "full_name": "ORB 특징점 개수",
        "category": "ORB 특징점 추출",
        "category_color": C_BLUE,
        "default": "700",
        "unit": "개",
        "range": "200 ~ 2000",
        "short": "이미지에서 뽑아낼 '특징 좌표'의 최대 개수",
        "detail": (
            "ORB 알고리즘은 이미지에서 코너·엣지처럼 '눈에 띄는 점'을 찾아냅니다.\n"
            "이 점들이 타겟과 라이브 영상 사이를 매칭하는 DNA 역할을 합니다.\n"
            "숫자가 클수록 더 많은 점을 찾지만, 그만큼 계산 시간도 늘어납니다."
        ),
        "up":   "매칭 점수가 올라갈 수 있지만 처리 시간(ms)이 늘어납니다.",
        "down": "빠르지만 특징점이 부족해서 점수가 낮아지고 오판이 늘어납니다.",
        "tip":  "타겟 화면이 단순(텍스트 위주)하면 500 이하도 충분합니다.\n복잡한 UI라면 1000~1500도 시도해보세요.",
        "param_key": "nfeatures",
        "input_type": "int",
        "val_min": 200,
        "val_max": 2000,
        "step": 50,
    },
    {
        "name": "lowe_ratio",
        "full_name": "Lowe's Ratio (노이즈 필터)",
        "category": "ORB 특징점 추출",
        "category_color": C_BLUE,
        "default": "0.75",
        "unit": "(비율)",
        "range": "0.50 ~ 0.95",
        "short": "우연히 맞아버린 '가짜 매칭'을 거르는 엄격도",
        "detail": (
            "매칭된 쌍(pair) 중 '1등 거리 ÷ 2등 거리 < 이 값'인 것만 살립니다.\n"
            "값이 낮을수록 기준이 엄격해져서 확실한 쌍만 남깁니다.\n"
            "David Lowe(SIFT 논문 저자)가 제안한 고전적인 노이즈 제거 기법입니다."
        ),
        "up":   "매칭 점수(good_matches)는 올라가지만 가짜 매칭도 같이 늘어납니다.\n→ 점수는 높아 보이지만 오판 위험 증가.",
        "down": "매칭 쌍이 줄어들어 점수가 낮아집니다.\n→ 확실한 것만 남으므로 신뢰도는 오히려 높아집니다.",
        "tip":  "공장 환경처럼 카메라 흔들림이 있다면 0.70 이하로 내려보세요.\n조명이 안정적이면 0.80까지 올려도 됩니다.",
        "param_key": "lowe_ratio",
        "input_type": "float",
        "val_min": 0.50,
        "val_max": 0.95,
        "step": 0.05,
    },
    {
        "name": "match_threshold",
        "full_name": "전체 이미지 매칭 컷오프",
        "category": "ORB 특징점 추출",
        "category_color": C_BLUE,
        "default": "25",
        "unit": "쌍",
        "range": "5 ~ 100",
        "short": "ROI 없을 때 '몇 쌍 이상이면 같은 화면?'의 기준",
        "detail": (
            "ROI 설정이 없는 폴백(Fallback) 모드에서 사용됩니다.\n"
            "good_matches 개수가 이 값 이상이어야 PASS로 처리합니다.\n"
            "ROI 방식을 쓰면 이 값은 사실상 사용되지 않습니다."
        ),
        "up":   "합격 기준이 높아져 오탐(False Positive)은 줄지만\n타겟과 일치해도 FAIL로 빠질 확률이 늘어납니다.",
        "down": "합격이 쉬워지지만 다른 화면도 PASS가 될 수 있습니다.",
        "tip":  "ROI를 제대로 설정했다면 이 값은 건드릴 필요가 거의 없습니다.",
        "param_key": "match_threshold",
        "input_type": "int",
        "val_min": 5,
        "val_max": 100,
        "step": 5,
    },
    {
        "name": "roi_match_threshold",
        "full_name": "ROI 단위 매칭 컷오프",
        "category": "ORB 특징점 추출",
        "category_color": C_BLUE,
        "default": "7",
        "unit": "쌍",
        "range": "3 ~ 30",
        "short": "ROI 크롭 하나당 '몇 쌍 이상이면 합격?'의 기준",
        "detail": (
            "전체 이미지가 아닌 ROI 영역만 잘라서 비교할 때의 컷오프입니다.\n"
            "ROI는 작은 영역이라 전체 이미지보다 특징점 수가 훨씬 적습니다.\n"
            "그래서 match_threshold(25)보다 훨씬 낮은 값을 씁니다."
        ),
        "up":   "각 ROI 합격 기준이 높아지므로 ROI 매칭 통과가 어려워집니다.",
        "down": "ROI 기준이 낮아져 오탐 가능성이 있습니다.",
        "tip":  "ROI 면적이 작을수록 이 값을 낮춰야 합니다. (3~5 권장)\nROI가 크면 10~15도 가능합니다.",
        "param_key": "roi_match_threshold",
        "input_type": "int",
        "val_min": 3,
        "val_max": 30,
        "step": 1,
    },

    # ── YOLO 모델 설정 ──────────────────────────────────────────────────────────
    {
        "name": "yolo_imgsz",
        "full_name": "YOLO 추론 해상도 (imgsz)",
        "category": "YOLO 모델 설정",
        "category_color": C_ORANGE,
        "default": "640",
        "unit": "픽셀",
        "range": "256 ~ 1280",
        "short": "YOLO 인공지능이 사물을 인식할 때 축소해서 바라보는 해상도 크기",
        "detail": (
            "학습된 모델의 환경(예: 640)과 일치해야 최적의 정밀도가 나옵니다.\n"
            "숫자를 내리면(예: 320) 연산이 빨라져 저사양 PC에 매우 유리하지만,\n"
            "마스크 경계가 깨지면서 원본으로 확대 시 화면 테두리가 요동치게 됩니다."
        ),
        "up":   "마스크 경계가 정밀해져 모니터 원근 보정이 자리에 딱 맞게 단단하게 이뤄집니다.\n→ 단, 픽셀 수가 기하급수적으로 늘어 연산 속도가 다소 느려집니다.",
        "down": "픽셀 수가 줄어 추론 속도가 급격히 빨라집니다. (프레임 방어)\n→ 단, 마스크 테두리에 계단 현상이 생기며 보정 화면이 널뛰기할 수 있습니다.",
        "tip":  "현재 모델이 학습한 근본 해상도인 640 유지를 강력 권장합니다.\n속도 확보가 절실하다면 480 정도를 중간 타협점으로 테스트하세요.",
        "param_key": "yolo_imgsz",
        "input_type": "int",
        "val_min": 256,
        "val_max": 1280,
        "step": 32,
    },

    # ── 전처리 ────────────────────────────────────────────────────────────────

    {
        "name": "clahe_clip_limit",
        "full_name": "CLAHE clipLimit (조명 보정 강도)",
        "category": "전처리 (Preprocessing)",
        "category_color": C_YELLOW,
        "default": "2.0",
        "unit": "(배율)",
        "range": "0.5 ~ 8.0",
        "short": "어두운 구석을 얼마나 강하게 밝힐지의 강도",
        "detail": (
            "CLAHE(Contrast Limited Adaptive Histogram Equalization)는\n"
            "이미지를 타일 단위로 나누어 각 구역의 밝기를 균일하게 맞춥니다.\n"
            "clipLimit는 한 타일에서 밝기를 올릴 수 있는 최대 배율입니다.\n"
            "공장의 역광·그림자·반사광에 의한 밝기 불균형을 보정합니다."
        ),
        "up":   "어두운 영역이 더 밝게 보정되어 특징점이 잘 잡힙니다.\n단, 너무 높으면 노이즈까지 증폭되어 가짜 특징점이 늘어납니다.",
        "down": "보정 효과가 약해져서 어두운 영역에 특징점이 안 잡힐 수 있습니다.",
        "tip":  "조명이 균일한 환경: 1.0~2.0\n반사·역광이 심한 환경: 3.0~5.0\n8.0 이상은 노이즈가 심해 권장하지 않습니다.",
        "param_key": "clahe_clip_limit",
        "input_type": "float",
        "val_min": 0.5,
        "val_max": 8.0,
        "step": 0.5,
    },
    {
        "name": "clahe_tile_grid",
        "full_name": "CLAHE tileGridSize (보정 타일 크기)",
        "category": "전처리 (Preprocessing)",
        "category_color": C_YELLOW,
        "default": "8 × 8",
        "unit": "(N×N 타일)",
        "range": "4 ~ 16",
        "short": "조명 보정을 몇 개의 격자(타일)로 나눠서 할지",
        "detail": (
            "이미지를 N×N 개의 사각형 타일로 나누어 각각 독립적으로\n"
            "히스토그램 평탄화를 수행합니다.\n"
            "타일이 작을수록 국소 영역의 조명 차이를 세밀하게 보정합니다.\n"
            "타일이 클수록 전체 이미지를 균일하게 처리합니다."
        ),
        "up":   "타일이 많아져 세밀한 보정이 되지만 처리가 느려집니다.\n패턴이 없는 배경 영역이 과보정될 수 있습니다.",
        "down": "타일이 적어져 넓은 영역을 뭉쳐서 처리합니다.\n국소적인 어두운 구석은 잘 못 잡을 수 있습니다.",
        "tip":  "모니터 화면처럼 UI 요소가 분산되어 있으면 8이 적합합니다.\n전체적으로 어두우면 4도 충분합니다.",
        "param_key": "clahe_tile_grid",
        "input_type": "int",
        "val_min": 4,
        "val_max": 16,
        "step": 2,
    },
    {
        "name": "blur_ksize",
        "full_name": "Blur 커널 크기 (노이즈·모아레 제거)",
        "category": "전처리 (Preprocessing)",
        "category_color": C_YELLOW,
        "default": "0",
        "unit": "(픽셀)",
        "range": "0 / 3 / 5 / 7",
        "short": "CLAHE 전에 적용하는 가우시안 블러 크기. 0=꺼짐",
        "detail": (
            "카메라로 모니터를 촬영하면 픽셀 격자 간섭으로 '모아레(줄무늬) 패턴'이 생깁니다.\n"
            "ORB는 이 패턴을 실제 특징점으로 오인하여 타겟(스크린샷)에 없는\n"
            "가짜 매칭을 만들어 점수를 낮춥니다.\n\n"
            "가우시안 블러를 CLAHE 이전에 적용하면 이 노이즈를 제거할 수 있습니다.\n"
            "단, 너무 강하면 UI 텍스트·버튼 엣지도 뭉개져서 특징점 수가 줄어듭니다.\n"
            "0=꺼짐, 3=가벼운 제거, 5=중간, 7=강한 제거 (홀수만 유효)"
        ),
        "up":   "모아레·카메라 노이즈가 제거되어 가짜 특징점이 줄어듭니다.\n값이 너무 크면 엣지가 뭉개져 ORB 키포인트 수가 감소합니다.",
        "down": "블러가 약해져 노이즈 특징점이 늘어납니다.\n0=꺼짐으로 설정하면 기존 동작 그대로 유지됩니다.",
        "tip":  "먼저 3으로 시작해 매칭 점수를 확인하세요.\n카메라 품질이 좋으면 0(꺼짐)이 더 나을 수 있습니다.\n모아레가 심한 환경이면 5까지 올려보세요.",
        "param_key": "blur_ksize",
        "input_type": "int",
        "val_min": 0,
        "val_max": 7,
        "step": 2,
    },
    {
        "name": "gamma",
        "full_name": "Gamma 보정 (어두운 화면 선보정)",
        "category": "전처리 (Preprocessing)",
        "category_color": C_YELLOW,
        "default": "1.0",
        "unit": "(지수)",
        "range": "0.50 ~ 2.00",
        "short": "CLAHE 전에 적용하는 감마 보정. 1.0=꺼짐, 0.7=밝게",
        "detail": (
            "공장 조명 절약 모드나 야간 촬영 환경에서 모니터 화면이 전체적으로 어두울 때,\n"
            "CLAHE만으로는 협소한 밝기 구간 안에서만 조정하므로 효과가 제한됩니다.\n\n"
            "감마 보정을 먼저 적용해 화면을 밝혀두면 CLAHE가 더 넓은\n"
            "동적 범위에서 작동하여 특징점 추출 품질이 향상됩니다.\n\n"
            "γ < 1.0 → 어두운 화면을 밝게 (0.7~0.9: 약간 밝게)\n"
            "γ = 1.0 → 변화 없음 (꺼짐)\n"
            "γ > 1.0 → 밝은 화면을 어둡게 (역광 보정 시)"
        ),
        "up":   "γ값이 커질수록 화면이 더 어두워집니다.\n역광·과노출 환경에서는 1.2~1.5가 도움이 됩니다.",
        "down": "γ값이 작아질수록 화면이 더 밝아집니다.\n어두운 공장 조명 환경이라면 0.7~0.9를 먼저 시도하세요.",
        "tip":  "일반적인 밝기 환경에서는 1.0(꺼짐)을 유지하세요.\n화면이 전반적으로 어두워 특징점이 잘 안 잡힐 때 0.8로 내려보세요.",
        "param_key": "gamma",
        "input_type": "float",
        "val_min": 0.50,
        "val_max": 2.00,
        "step": 0.05,
    },
    {
        "name": "sharpen_amount",
        "full_name": "Sharpening 강도 (언샤프 마스킹)",
        "category": "전처리 (Preprocessing)",
        "category_color": C_YELLOW,
        "default": "1.0",
        "unit": "(강도)",
        "range": "0.0 ~ 3.0",
        "short": "글자·버튼 테두리를 얼마나 날카롭게 강조할지",
        "detail": (
            "ORB는 코너처럼 '변화가 큰 지점'에 특징점을 찍습니다.\n"
            "언샤프 마스킹(Unsharp Masking)은 원본에서 블러 버전을 빼는 방식으로\n"
            "엣지를 강조하여 ORB가 UI 경계에 정확히 특징점을 찍도록 유도합니다.\n\n"
            "result = (1+amount) × 원본  −  amount × 가우시안블러(5×5)\n\n"
            "0.0: 샤프닝 없음 (CLAHE 결과 그대로)\n"
            "1.0: 중간 강도 (기본값)\n"
            "2.0: 강한 강도\n"
            "3.0: 매우 강함 (노이즈 이미지에서 역효과 주의)"
        ),
        "up":   "특징점이 테두리에 집중되어 매칭 정확도가 높아집니다.\n과하면 이미지 전체가 거칠어져 노이즈 특징점이 늘어납니다.",
        "down": "부드러운 이미지가 되어 코너 감지가 약해집니다.\n0.0으로 설정하면 CLAHE 직후 이미지 그대로 ORB에 전달됩니다.",
        "tip":  "텍스트·아이콘이 뚜렷한 화면이면 1.0이 적당합니다.\n카메라가 흐릿하거나 원거리 촬영이라면 1.5~2.0을 시도하세요.\n블러(blur_ksize)를 올렸다면 샤프닝도 같이 올려 균형을 맞추세요.",
        "param_key": "sharpen_amount",
        "input_type": "float",
        "val_min": 0.0,
        "val_max": 3.0,
        "step": 0.25,
    },

    # ── 마스킹 ────────────────────────────────────────────────────────────────
    {
        "name": "mask_region",
        "full_name": "마스크 영역 (동적 콘텐츠 제외)",
        "category": "마스킹",
        "category_color": C_RED,
        "default": "없음",
        "unit": "(영역)",
        "range": "직접 드로우",
        "short": "매 프레임 바뀌는 영역을 ORB 특징점 추출에서 제외",
        "detail": (
            "모니터 화면에 시계·날짜·카운터처럼 계속 변하는 영역이 있으면\n"
            "그 부분에 ORB 특징점이 잔뜩 몰립니다.\n"
            "타겟 이미지의 시계가 '10:30'이고 라이브 영상은 '10:31'이면\n"
            "분명히 같은 화면인데도 매칭 점수가 뚝 떨어집니다.\n\n"
            "마스크 영역으로 지정하면 그 구역은 완전히 검정으로 칠해져서\n"
            "ORB가 그 부분을 아예 무시하게 됩니다."
        ),
        "up":   "마스크 영역이 넓을수록 비교에 쓰이는 특징점 수가 줄어듭니다.\n지나치게 넓으면 오히려 매칭 근거가 부족해집니다.",
        "down": "(마스크 없음) 동적 콘텐츠 영역의 특징점이 점수를 오염시킵니다.",
        "tip":  "시계·날짜·카운터·LED 숫자 등 변하는 부분만 좁게 지정하세요.\n정적인 UI 요소(로고, 버튼 등)는 마스크하지 마세요.",
        "param_key": None,  # ROI 드로우로 설정 — 편집 불가
        "input_type": None,
    },

    # ── 시스템 임계값 ─────────────────────────────────────────────────────────
    {
        "name": "MATCH_THRESHOLD",
        "full_name": "MATCH_THRESHOLD (최종 합격 기준)",
        "category": "시스템 임계값",
        "category_color": C_GREEN,
        "default": "60",
        "unit": "점 (ORB 점수)",
        "range": "30 ~ 100",
        "short": "이 점수 이상이면 PASS, 미만이면 FAIL",
        "detail": (
            "ROI 매칭이 모두 끝난 뒤 최종 판정에 쓰이는 기준점입니다.\n"
            "best_score(가장 높은 ORB 매칭 점수)가 이 값 이상이어야 합격입니다.\n"
            "이 값은 GUI 상단에 표시되는 '매칭 점수 %'의 기준이 됩니다."
        ),
        "up":   "합격 기준이 높아져 FAIL이 많아집니다.\n→ pending 폴더에 이미지가 쌓이고 야간 학습 부담이 늘어납니다.",
        "down": "합격이 쉬워집니다.\n→ 다른 화면도 PASS될 수 있어 검사 신뢰도가 떨어집니다.",
        "tip":  "초기 운영 중에는 낮게(40~50) 설정하고, ORB 파라미터를 튜닝한 뒤\n점수 분포가 안정되면 60~70으로 올리는 것을 권장합니다.",
        "param_key": "MATCH_THRESHOLD",
        "input_type": "int",
        "val_min": 30,
        "val_max": 100,
        "step": 5,
    },
]
# NOTE: PENDING_THRESHOLD 항목은 제거됨.
# pending 저장 방식이 고정 임계값에서 MATCH_THRESHOLD 기준 ±margin(2~3점) Hard Mining으로
# 변경되어 이 파라미터는 더 이상 동작에 영향을 주지 않습니다.


# ─── 위젯 빌더 헬퍼 ─────────────────────────────────────────────────────────
def _lbl(text, size=13, bold=False, color=C_DARK, wrap=True):
    l = QLabel(text)
    style = f"font-size:{size}px; color:{color};"
    if bold:
        style += " font-weight:bold;"
    l.setStyleSheet(style)
    if wrap:
        l.setWordWrap(True)
    return l


def _divider():
    line = QFrame()
    line.setFrameShape(QFrame.HLine)
    line.setStyleSheet(f"color:{C_BORDER}; margin:4px 0;")
    return line


class ParamCard(QWidget):
    """파라미터 한 개를 시각적으로 설명 + 값 편집이 가능한 카드 위젯"""
    def __init__(self, p: dict, current_value):
        super().__init__()
        self.param_key  = p.get("param_key")
        self.input_type = p.get("input_type")
        self.spinbox    = None

        self.setStyleSheet(
            f"background:{C_WHITE}; border:1px solid {C_BORDER};"
            f"border-radius:10px;"
        )
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Minimum)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(20, 16, 20, 16)
        outer.setSpacing(10)

        # ── 헤더 ──────────────────────────────────────────────
        header = QHBoxLayout()
        color_bar = QFrame()
        color_bar.setFixedWidth(4)
        color_bar.setStyleSheet(
            f"background:{p['category_color']}; border-radius:2px; border:none;"
        )
        header.addWidget(color_bar)

        title_col = QVBoxLayout()
        title_col.setSpacing(2)
        title_row = QHBoxLayout()
        title_row.setSpacing(8)
        name_lbl = _lbl(p["name"], size=15, bold=True, color=C_DARK, wrap=False)
        title_row.addWidget(name_lbl)
        full_lbl = _lbl(f"— {p['full_name']}", size=13, color=C_SUB, wrap=False)
        title_row.addWidget(full_lbl)
        title_row.addStretch()
        title_col.addLayout(title_row)
        short_lbl = _lbl(p["short"], size=12, color=C_SUB)
        title_col.addWidget(short_lbl)

        header.addSpacing(10)
        header.addLayout(title_col, stretch=1)
        outer.addLayout(header)

        # ── 범위 배지 + 현재 설정값 입력 ──────────────────────
        outer.addWidget(self._build_value_row(p, current_value))

        outer.addWidget(_divider())

        # ── 자세한 설명 ────────────────────────────────────────
        outer.addWidget(_lbl(p["detail"], size=13, color=C_DARK))
        outer.addWidget(_divider())

        # ── 올리면 / 내리면 ────────────────────────────────────
        effect_grid = QHBoxLayout()
        effect_grid.setSpacing(10)

        # 올리면: 오렌지 파스텔 배경 (기존 갈색→선명한 주황 계열)
        up_w = QWidget()
        up_w.setStyleSheet(
            "background:#FFF3E0; border-radius:6px; border:1px solid #FFB74D;"
        )
        up_v = QVBoxLayout(up_w)
        up_v.setContentsMargins(12, 8, 12, 8); up_v.setSpacing(4)
        up_v.addWidget(_lbl("값을 올리면 (↑)", size=12, bold=True, color="#E65100", wrap=False))
        up_v.addWidget(_lbl(p["up"], size=12, color=C_DARK))
        effect_grid.addWidget(up_w, stretch=1)

        # 내리면: 스카이블루 파스텔 배경
        down_w = QWidget()
        down_w.setStyleSheet(
            "background:#E3F2FD; border-radius:6px; border:1px solid #64B5F6;"
        )
        down_v = QVBoxLayout(down_w)
        down_v.setContentsMargins(12, 8, 12, 8); down_v.setSpacing(4)
        down_v.addWidget(_lbl("값을 내리면 (↓)", size=12, bold=True, color="#1565C0", wrap=False))
        down_v.addWidget(_lbl(p["down"], size=12, color=C_DARK))
        effect_grid.addWidget(down_w, stretch=1)

        outer.addLayout(effect_grid)

        # ── 팁 ────────────────────────────────────────────────
        tip_w = QWidget()
        tip_w.setStyleSheet(
            "background:#E8F5E9; border-radius:6px; border:1px solid #81C784;"
        )
        tip_v = QVBoxLayout(tip_w)
        tip_v.setContentsMargins(12, 8, 12, 8); tip_v.setSpacing(2)
        tip_v.addWidget(_lbl("실전 팁", size=12, bold=True, color="#2E7D32", wrap=False))
        tip_v.addWidget(_lbl(p["tip"], size=12, color=C_DARK))
        outer.addWidget(tip_w)

    def _build_value_row(self, p: dict, current_value) -> QWidget:
        """기본값/범위 배지 + 현재 설정값 스핀박스 행"""
        color = p["category_color"]
        w = QWidget()
        w.setStyleSheet(f"background:{color}11; border-radius:4px; border:1px solid {color}44;")
        h = QHBoxLayout(w)
        h.setContentsMargins(10, 6, 10, 6)
        h.setSpacing(16)

        # 기본값 표시
        h.addWidget(_lbl(f"기본값  {p['default']} {p['unit']}", size=12, bold=True, color=color, wrap=False))

        sep = QFrame(); sep.setFrameShape(QFrame.VLine)
        sep.setStyleSheet(f"color:{color}44;")
        h.addWidget(sep)

        # 권장 범위
        h.addWidget(_lbl(f"권장 범위  {p['range']}", size=12, color=C_SUB, wrap=False))
        h.addStretch()

        # 편집 가능한 경우 스핀박스 추가
        if self.input_type == "int":
            sb = QSpinBox()
            sb.setMinimum(p["val_min"])
            sb.setMaximum(p["val_max"])
            sb.setSingleStep(p["step"])
            sb.setValue(int(current_value) if current_value is not None else int(p["default"]))
            sb.setFixedWidth(90)
            sb.setStyleSheet(
                f"QSpinBox {{ border:1px solid {color}; border-radius:4px;"
                f"  padding:2px 4px; font-size:13px; font-weight:bold; color:{C_DARK}; background:white; }}"
                f"QSpinBox::up-button, QSpinBox::down-button {{ width:18px; }}"
            )
            h.addWidget(_lbl("현재 설정값", size=12, color=C_SUB, wrap=False))
            h.addWidget(sb)
            h.addWidget(_lbl(p["unit"], size=12, color=C_DARK, wrap=False))
            self.spinbox = sb

        elif self.input_type == "float":
            sb = QDoubleSpinBox()
            sb.setMinimum(p["val_min"])
            sb.setMaximum(p["val_max"])
            sb.setSingleStep(p["step"])
            sb.setDecimals(2)
            sb.setValue(float(current_value) if current_value is not None else float(p["default"]))
            sb.setFixedWidth(90)
            sb.setStyleSheet(
                f"QDoubleSpinBox {{ border:1px solid {color}; border-radius:4px;"
                f"  padding:2px 4px; font-size:13px; font-weight:bold; color:{C_DARK}; background:white; }}"
                f"QDoubleSpinBox::up-button, QDoubleSpinBox::down-button {{ width:18px; }}"
            )
            h.addWidget(_lbl("현재 설정값", size=12, color=C_SUB, wrap=False))
            h.addWidget(sb)
            h.addWidget(_lbl(p["unit"], size=12, color=C_DARK, wrap=False))
            self.spinbox = sb

        else:
            # 편집 불가 파라미터 — "수동 설정" 뱃지
            badge = QLabel("수동 설정")
            badge.setStyleSheet(
                f"background:{C_SUB}22; color:{C_SUB}; border:1px solid {C_SUB}44;"
                f"border-radius:4px; padding:2px 8px; font-size:11px;"
            )
            h.addWidget(badge)

        return w

    def get_value(self):
        """현재 스핀박스 값 반환. 편집 불가이면 None."""
        if self.spinbox is None:
            return None
        return self.spinbox.value()


class CategoryHeader(QWidget):
    """섹션 구분 헤더"""
    def __init__(self, title: str, color: str):
        super().__init__()
        self.setFixedHeight(42)
        self.setStyleSheet(
            f"background: qlineargradient(x1:0,y1:0,x2:1,y2:0,"
            f"stop:0 {color}, stop:1 {color}00); border-radius:6px;"
        )
        h = QHBoxLayout(self)
        h.setContentsMargins(16, 0, 16, 0)
        l = QLabel(title)
        l.setStyleSheet(
            f"font-size:14px; font-weight:bold; color:white;"
            f"background:transparent; border:none;"
        )
        h.addWidget(l)
        h.addStretch()

# ─── 메인 탭 ────────────────────────────────────────────────────────────────
class GuideTab(QWidget):
    """탭 4: 파라미터 가이드 (설정 패널 + 가이드북 분리)"""
    def __init__(self):
        super().__init__()
        self.setStyleSheet(f"background:{C_BG};")
        self._cards          = []   # [(param_key, ParamCard)] — 가이드북 카드
        self._compact_fields = {}   # {param_key: (spinbox, input_type)} — 설정 패널 스핀박스

        cfg = load_params_config()

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # ── 상단 제목 바 ───────────────────────────────────────
        title_bar = QWidget()
        title_bar.setFixedHeight(52)
        title_bar.setStyleSheet(f"background:{C_WHITE}; border-bottom:1px solid {C_BORDER};")
        tb = QHBoxLayout(title_bar)
        tb.setContentsMargins(24, 0, 24, 0)
        tb.addWidget(_lbl("  파라미터 가이드", size=17, bold=True, color=C_DARK, wrap=False))
        tb.addWidget(_lbl("  [설정] 탭에서 값을 조정하고 저장하세요. [가이드북] 탭에서 각 변수의 의미를 확인하세요.",
                          size=11, color=C_SUB, wrap=False))
        tb.addStretch()
        root.addWidget(title_bar)

        # ── 메인 탭: [파라미터 설정] | [가이드북] ──────────────────
        main_tabs = QTabWidget()
        main_tabs.setStyleSheet(
            f"QTabWidget::pane{{background:{C_BG};border:none;}}"
            f"QTabBar::tab{{padding:8px 22px;font-size:12px;color:{C_SUB};}}"
            f"QTabBar::tab:selected{{color:{C_DARK};font-weight:bold;"
            f"border-bottom:2px solid {C_BLUE};}}"
        )

        # ══════════════════════════════════════════════════════
        # 탭 1: 파라미터 설정 (좌: 자동최적화 / 우: 컴팩트 변수 설정)
        # ══════════════════════════════════════════════════════
        settings_tab = QWidget()
        settings_tab.setStyleSheet(f"background:{C_BG};")
        settings_h = QHBoxLayout(settings_tab)
        settings_h.setContentsMargins(12, 12, 12, 12)
        settings_h.setSpacing(12)

        # ── 좌측: 자동 최적화 패널 (55%) ─────────────────────────
        left_box = QWidget()
        left_box.setStyleSheet(f"background:{C_WHITE};border:1px solid {C_BORDER};border-radius:8px;")
        left_v = QVBoxLayout(left_box)
        left_v.setContentsMargins(0, 0, 0, 0)
        left_v.setSpacing(0)

        opt_tabs = QTabWidget()
        opt_tabs.setStyleSheet(
            f"QTabWidget::pane{{background:{C_WHITE};border:none;}}"
            f"QTabBar::tab{{padding:6px 16px;font-size:11px;color:{C_SUB};}}"
            f"QTabBar::tab:selected{{color:{C_DARK};font-weight:bold;"
            f"border-bottom:2px solid {C_BLUE};}}"
        )
        self._opt_panel = OptimizerPanel()
        self._opt_panel.apply_requested.connect(self._on_apply_optimal)
        opt_tabs.addTab(self._opt_panel, "타겟 간 자동 분석")

        self._gt_panel = GroundTruthOptimizerPanel()
        self._gt_panel.apply_requested.connect(self._on_apply_optimal)
        opt_tabs.addTab(self._gt_panel, "정답 데이터 기반 분석")

        left_v.addWidget(opt_tabs)
        settings_h.addWidget(left_box, stretch=55)

        # ── 우측: 컴팩트 변수 설정 패널 (45%) ─────────────────────
        right_box = QWidget()
        right_box.setStyleSheet(f"background:{C_WHITE};border:1px solid {C_BORDER};border-radius:8px;")
        right_v = QVBoxLayout(right_box)
        right_v.setContentsMargins(14, 12, 14, 14)
        right_v.setSpacing(8)

        # 헤더 (타이틀 + 저장 버튼)
        hdr_row = QHBoxLayout()
        hdr_row.addWidget(_lbl("변수 설정", size=14, bold=True, color=C_DARK, wrap=False))
        hdr_row.addStretch()
        save_btn = QPushButton("설정 저장")
        save_btn.setFixedHeight(32)
        save_btn.setMinimumWidth(90)
        save_btn.setStyleSheet(
            f"QPushButton{{background:{C_GREEN};color:white;border:none;"
            f"border-radius:6px;font-size:12px;font-weight:bold;padding:0 12px;}}"
            f"QPushButton:hover{{background:#219a52;}}"
        )
        save_btn.clicked.connect(self._on_save)
        hdr_row.addWidget(save_btn)
        right_v.addLayout(hdr_row)

        hint_lbl = _lbl("값을 변경하면 노란색으로 강조됩니다.", size=10, color=C_SUB)
        right_v.addWidget(hint_lbl)

        # 스크롤 가능한 변수 폼
        compact_scroll = QScrollArea()
        compact_scroll.setWidgetResizable(True)
        compact_scroll.setFrameShape(QFrame.NoFrame)
        compact_scroll.setStyleSheet(
            f"QScrollArea{{background:{C_WHITE};border:none;}}"
            f"QScrollBar:vertical{{width:5px;background:{C_BG};}}"
            f"QScrollBar::handle:vertical{{background:{C_BORDER};border-radius:2px;}}"
        )

        compact_content = QWidget()
        compact_content.setStyleSheet(f"background:{C_WHITE};")
        cv = QVBoxLayout(compact_content)
        cv.setContentsMargins(0, 4, 4, 4)
        cv.setSpacing(3)

        # 카테고리별 변수 배치 (마스킹은 편집 불가라 제외)
        cat_order = [
            ("YOLO 모델 설정",         C_ORANGE),
            ("ORB 특징점 추출",        C_BLUE),
            ("전처리 (Preprocessing)", C_YELLOW),
            ("시스템 임계값",          C_GREEN),
        ]
        params_by_cat = {}
        for p in PARAMS:
            if p.get("input_type") is not None:
                params_by_cat.setdefault(p["category"], []).append(p)

        for cat_name, cat_color in cat_order:
            items = params_by_cat.get(cat_name, [])
            if not items:
                continue

            # 카테고리 헤더
            cat_hdr = QWidget()
            cat_hdr.setFixedHeight(26)
            cat_hdr.setStyleSheet(
                f"background:qlineargradient(x1:0,y1:0,x2:1,y2:0,"
                f"stop:0 {cat_color},stop:0.7 {cat_color}88,stop:1 transparent);"
                f"border-radius:4px;"
            )
            ch = QHBoxLayout(cat_hdr)
            ch.setContentsMargins(10, 0, 10, 0)
            ch_lbl = QLabel(cat_name)
            ch_lbl.setStyleSheet(
                "font-size:11px;font-weight:bold;color:white;background:transparent;border:none;"
            )
            ch.addWidget(ch_lbl)
            cv.addWidget(cat_hdr)

            for p in items:
                row_key   = p["param_key"]
                row_color = p["category_color"]
                default_str = p["default"]
                # default 값 파싱 (숫자만 추출)
                try:
                    default_v = int(default_str) if p["input_type"] == "int" else float(default_str)
                except (ValueError, TypeError):
                    default_v = 0

                row_w = QWidget()
                row_w.setStyleSheet(f"background:{C_WHITE};border-radius:4px;")
                row_h = QHBoxLayout(row_w)
                row_h.setContentsMargins(6, 4, 6, 4)
                row_h.setSpacing(8)

                # 파라미터 이름 레이블
                name_lbl = QLabel(p["name"])
                name_lbl.setStyleSheet(
                    f"font-size:11px;color:{C_DARK};font-weight:bold;"
                )
                name_lbl.setFixedWidth(155)
                row_h.addWidget(name_lbl)

                # 스핀박스 생성
                current = cfg.get(row_key)
                if p["input_type"] == "int":
                    sb = QSpinBox()
                    sb.setMinimum(p["val_min"])
                    sb.setMaximum(p["val_max"])
                    sb.setSingleStep(p["step"])
                    sb.setValue(int(current) if current is not None else int(default_v))
                else:
                    sb = QDoubleSpinBox()
                    sb.setMinimum(p["val_min"])
                    sb.setMaximum(p["val_max"])
                    sb.setSingleStep(p["step"])
                    sb.setDecimals(2)
                    sb.setValue(float(current) if current is not None else float(default_v))

                sb.setFixedWidth(82)
                sb.setFixedHeight(26)
                sb.setStyleSheet(
                    f"QSpinBox, QDoubleSpinBox {{"
                    f"border:1px solid {row_color};border-radius:4px;"
                    f"padding:1px 3px;font-size:12px;font-weight:bold;color:{C_DARK};background:white;}}"
                    f"QSpinBox::up-button, QDoubleSpinBox::up-button,"
                    f"QSpinBox::down-button, QDoubleSpinBox::down-button{{width:16px;}}"
                )

                # 색상 피드백 (값 변경 시 배경색 노랑으로)
                def _make_updater(rw, dv, color):
                    def _update(val):
                        if abs(float(val) - float(dv)) < 0.001:
                            rw.setStyleSheet(f"background:{C_WHITE};border-radius:4px;")
                        else:
                            rw.setStyleSheet(
                                f"background:#FFFBEA;border-radius:4px;"
                                f"border-left:3px solid {color};"
                            )
                    return _update

                updater = _make_updater(row_w, default_v, row_color)
                sb.valueChanged.connect(updater)
                updater(sb.value())  # 초기 색상 설정

                row_h.addWidget(sb)

                # 단위 레이블
                unit_lbl = QLabel(p["unit"])
                unit_lbl.setStyleSheet(f"font-size:10px;color:{C_SUB};")
                row_h.addWidget(unit_lbl)
                row_h.addStretch()

                cv.addWidget(row_w)
                self._compact_fields[row_key] = (sb, p["input_type"])

            cv.addSpacing(6)

        cv.addStretch()
        compact_scroll.setWidget(compact_content)
        right_v.addWidget(compact_scroll, stretch=1)

        settings_h.addWidget(right_box, stretch=45)
        main_tabs.addTab(settings_tab, "파라미터 설정")

        # ══════════════════════════════════════════════════════
        # 탭 2: 가이드북 (기존 ParamCards — 설명 중심)
        # ══════════════════════════════════════════════════════
        guidebook_tab = QWidget()
        guidebook_tab.setStyleSheet(f"background:{C_BG};")
        gb_v = QVBoxLayout(guidebook_tab)
        gb_v.setContentsMargins(0, 0, 0, 0)
        gb_v.setSpacing(0)

        guide_scroll = QScrollArea()
        guide_scroll.setWidgetResizable(True)
        guide_scroll.setFrameShape(QFrame.NoFrame)
        guide_scroll.setStyleSheet(f"background:{C_BG};border:none;")

        guide_content = QWidget()
        guide_content.setStyleSheet(f"background:{C_BG};")
        gv = QVBoxLayout(guide_content)
        gv.setContentsMargins(24, 20, 24, 28)
        gv.setSpacing(12)

        cat_order_full = [
            ("YOLO 모델 설정",         C_ORANGE),
            ("ORB 특징점 추출",        C_BLUE),
            ("전처리 (Preprocessing)", C_YELLOW),
            ("마스킹",                 C_RED),
            ("시스템 임계값",          C_GREEN),
        ]
        all_params_by_cat = {}
        for p in PARAMS:
            all_params_by_cat.setdefault(p["category"], []).append(p)

        for cat_name, cat_color in cat_order_full:
            items = all_params_by_cat.get(cat_name, [])
            if not items:
                continue
            gv.addWidget(CategoryHeader(cat_name, cat_color))
            # ── 2열 그리드로 카드 배치 (가로 길이 압박 해소) ──
            row_h = None
            for idx, p in enumerate(items):
                current_val = cfg.get(p.get("param_key")) if p.get("param_key") else None
                card = ParamCard(p, current_val)
                card.setMaximumWidth(560)          # 카드 최대 너비 제한
                card.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Minimum)
                if p.get("param_key"):
                    self._cards.append((p["param_key"], card))
                if idx % 2 == 0:
                    row_h = QHBoxLayout()
                    row_h.setSpacing(10)
                    row_h.addWidget(card)
                else:
                    row_h.addWidget(card)
                    gv.addLayout(row_h)
                    row_h = None
            # 홀수 개인 경우 마지막 카드 처리
            if row_h is not None:
                row_h.addStretch(1)
                gv.addLayout(row_h)
            gv.addSpacing(8)

        gv.addStretch()
        guide_scroll.setWidget(guide_content)
        gb_v.addWidget(guide_scroll)

        main_tabs.addTab(guidebook_tab, "가이드북")
        root.addWidget(main_tabs, stretch=1)

    def _on_apply_optimal(self, best_params: dict):
        """최적화 결과를 컴팩트 스핀박스와 가이드북 카드에 모두 반영"""
        applied = []
        # 컴팩트 패널
        for key, (sb, input_type) in self._compact_fields.items():
            if key in best_params:
                val = best_params[key]
                sb.setValue(int(val) if input_type == "int" else float(val))
                applied.append(f"{key} = {val}")
        # 가이드북 카드 (동기화)
        for key, card in self._cards:
            if key in best_params and card.spinbox is not None:
                val = best_params[key]
                card.spinbox.setValue(int(val) if card.input_type == "int" else float(val))
        if applied:
            QMessageBox.information(
                self, "적용 완료",
                "최적값이 설정 패널에 반영되었습니다.\n\n"
                + "\n".join(applied)
                + "\n\n[설정 저장] 버튼을 눌러 파일에 저장하세요."
            )

    def _on_save(self):
        """컴팩트 패널 스핀박스 값을 params_config.json에 저장"""
        cfg = load_params_config()
        for key, (sb, input_type) in self._compact_fields.items():
            cfg[key] = sb.value()
        try:
            save_params_config(cfg)
            QMessageBox.information(
                self, "저장 완료",
                "파라미터 설정이 저장되었습니다.\n\n"
                "변경 사항은 영상 분석을 다시 시작할 때 적용됩니다."
            )
        except Exception as e:
            QMessageBox.critical(self, "저장 실패", f"설정 파일 저장 중 오류가 발생했습니다.\n{e}")

