"""
offline/auto_tuner.py — 야간 ORB 파라미터 자동 최적화

실시간 파이프라인(engine/preprocessor.py + engine/matcher.py)과 동일한 전처리를
사용하여 Optuna Bayesian 탐색으로 최적 파라미터 조합을 찾습니다.

탐색 공간 (실시간 params_config.json 키와 1:1 대응):
  clahe_clip_limit  : CLAHE 증폭 한계
  clahe_tile_grid   : CLAHE 타일 크기 (N×N)
  blur_ksize        : 가우시안 블러 커널 (0=꺼짐)
  gamma             : 감마 보정
  sharpen_amount    : 언샤프 마스킹 강도
  nfeatures         : ORB 추출 특징점 수
  lowe_ratio        : Lowe's ratio test 임계값

최적화 목표 (합성 점수):
  composite = acc_ratio × disc_norm + spd_ratio × speed_norm
  · disc_norm  : 판별력(정답매칭 − 최고오답) 을 [-30, +30] 기준으로 0~1 정규화
  · speed_norm : 실측 처리시간을 [8ms, 40ms] 기준으로 0~1 정규화 (빠를수록 1)
  · acc_ratio / spd_ratio : GUI에서 설정한 정확도/속도 가중치 비율

결과를 data/params_config.json 에 저장하면 실시간 파이프라인에 즉시 반영됨.
"""
import os
import sys
import json
import time
import cv2
import numpy as np

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

try:
    import optuna
    optuna.logging.set_verbosity(optuna.logging.WARNING)
    _HAS_OPTUNA = True
except ImportError:
    _HAS_OPTUNA = False

_PARAMS_CONFIG = os.path.join(_ROOT, "data", "params_config.json")
_IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".bmp"}

# ── 탐색 공간 정의 (실시간 파이프라인 파라미터와 동일) ──────────────────────────
SEARCH_SPACE = {
    "clahe_clip_limit": [0.5, 1.0, 1.5, 2.0, 2.5, 3.0, 3.5, 4.0, 5.0, 6.0],
    "clahe_tile_grid":  [2, 4, 6, 8, 10, 12, 16, 20],
    "blur_ksize":       [0, 1, 3, 5, 7, 9, 11, 13, 15],
    "gamma":            [0.5, 0.7, 0.8, 0.9, 1.0, 1.1, 1.2, 1.4, 1.6, 2.0],
    "sharpen_amount":   [0.0, 0.2, 0.5, 0.8, 1.0, 1.3, 1.6, 2.0, 2.5, 3.0],
    "nfeatures":        [300, 500, 700, 900, 1100, 1500],
    "lowe_ratio":       [0.60, 0.65, 0.70, 0.75, 0.80, 0.85],
}


def _preprocess(gray, clahe_clip, clahe_tile, blur_ksize, gamma, sharpen):
    """실시간 ImagePreprocessor.preprocess_for_orb() 와 동일한 5단계 파이프라인."""
    img = gray.copy()
    # [2] Blur
    if blur_ksize > 0 and blur_ksize % 2 == 1:
        img = cv2.GaussianBlur(img, (blur_ksize, blur_ksize), 0)
    # [3] Gamma
    if abs(gamma - 1.0) > 0.01:
        lut = (np.power(np.arange(256) / 255.0, gamma) * 255).astype(np.uint8)
        img = cv2.LUT(img, lut)
    # [4] CLAHE
    clahe = cv2.createCLAHE(clipLimit=float(clahe_clip),
                             tileGridSize=(int(clahe_tile), int(clahe_tile)))
    img = clahe.apply(img)
    # [5] Sharpening
    if sharpen > 0.0:
        blur5 = cv2.GaussianBlur(img, (5, 5), 1.0)
        img = cv2.addWeighted(img, 1.0 + sharpen, blur5, -sharpen, 0)
    return img


def _load_image_gray(path):
    """한글 경로 대응 이미지 로드 후 640×360 gray 반환."""
    buf = np.fromfile(path, dtype=np.uint8)
    img = cv2.imdecode(buf, cv2.IMREAD_COLOR)
    if img is None:
        return None
    return cv2.cvtColor(cv2.resize(img, (640, 360)), cv2.COLOR_BGR2GRAY)


class BayesianAutoTuner:
    """
    실시간 파이프라인과 동일한 전처리로 Optuna Bayesian 최적화를 수행합니다.

    최적화 목표: 타겟 판별력 = avg(정답 매칭 − 최고 오답 매칭)
      · 타겟 이미지(label 있음): disc = correct - best_wrong  (클수록 좋음)
      · 비타겟 이미지(label 없음): 최고 매칭 수 (낮을수록 좋음, 점수 차감)
    """

    # ── 정규화 기준값 (합성 점수 계산에 사용) ──────────────────────────────────────
    DISC_MIN  = -30.0   # 판별력 최솟값 기준 (이 이하 → 0점)
    DISC_MAX  =  30.0   # 판별력 최댓값 기준 (이 이상 → 1점)
    FAST_MS   =   8.0   # "빠름" 기준 ms   (이 이하 → speed 1점)
    SLOW_MS   =  40.0   # "느림" 기준 ms   (이 이상 → speed 0점)

    def __init__(self, target_image_paths, pending_image_paths, taboo_file=None,
                 speed_weight=5, accuracy_weight=5):
        """
        Parameters
        ----------
        speed_weight    : 속도 중요도 (1~9, 기본 5)
        accuracy_weight : 정확도 중요도 (1~9, 기본 5)
        합성점수 = (accuracy_weight / total) × disc_norm
                 + (speed_weight / total) × speed_norm
        """
        if taboo_file is None:
            taboo_file = os.path.join(_ROOT, "models", "taboo_list.json")
        os.makedirs(os.path.dirname(taboo_file), exist_ok=True)

        self.target_paths     = target_image_paths   # [(path, label_id), ...] or [path, ...]
        self.pending_paths    = pending_image_paths   # [(path, label_id), ...] or [path, ...]
        self.taboo_file       = taboo_file
        self.taboo_list       = self._load_taboo_list()
        total = max(speed_weight + accuracy_weight, 1)
        self.speed_ratio    = speed_weight    / total
        self.accuracy_ratio = accuracy_weight / total

    # ── taboo 관리 ──────────────────────────────────────────────────────────────
    def _load_taboo_list(self):
        if os.path.exists(self.taboo_file):
            try:
                with open(self.taboo_file, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception:
                pass
        return []

    def register_taboo_rollback(self, bad_params):
        """성능 하락 시 해당 파라미터를 블랙리스트에 등록."""
        self.taboo_list.append(bad_params)
        with open(self.taboo_file, "w", encoding="utf-8") as f:
            json.dump(self.taboo_list, f, indent=4)
        print("[AutoTuner] 파라미터 블랙리스트 등록 완료")

    # ── 이미지 로드 ─────────────────────────────────────────────────────────────
    def _load_target_grays(self):
        """
        타겟 폴더 또는 path 리스트에서 gray 이미지 로드.
        반환: {label_id: gray_ndarray}
        """
        target_grays = {}
        for item in self.target_paths:
            if isinstance(item, (list, tuple)) and len(item) == 2:
                path, label = item
            else:
                path  = item
                label = os.path.splitext(os.path.basename(path))[0]
            gray = _load_image_gray(path)
            if gray is not None:
                target_grays[label] = gray
        return target_grays

    def _load_test_items(self, target_grays):
        """
        pending 이미지 로드. label이 없으면 비타겟으로 처리.
        반환: [(gray, label_or_None), ...]
        """
        items = []
        for item in self.pending_paths:
            if isinstance(item, (list, tuple)) and len(item) == 2:
                path, label = item
            else:
                path  = item
                label = None
            gray = _load_image_gray(path)
            if gray is not None:
                items.append((gray, label))
        return items

    # ── 채점 함수 ───────────────────────────────────────────────────────────────
    def _score(self, params, target_grays, test_items):
        """
        파라미터 조합의 판별력 점수 + 실측 처리시간 계산.
        반환: (disc_score, avg_ms_per_image)
          · disc_score: 판별력 = avg(correct − best_wrong) − 비타겟 페널티
          · avg_ms    : 이미지 1장당 전처리 + ORB 추출 평균 시간 (ms)
        """
        clip   = params["clahe_clip_limit"]
        tile   = params["clahe_tile_grid"]
        blur   = params["blur_ksize"]
        gamma  = params["gamma"]
        sharp  = params["sharpen_amount"]
        nfeat  = params["nfeatures"]
        lowe   = params["lowe_ratio"]

        orb = cv2.ORB_create(nfeatures=nfeat)
        bf  = cv2.BFMatcher(cv2.NORM_HAMMING, crossCheck=False)

        # 타겟 descriptor 미리 추출
        t_des_map = {}
        for tid, tgray in target_grays.items():
            t_proc = _preprocess(tgray, clip, tile, blur, gamma, sharp)
            _, des = orb.detectAndCompute(t_proc, None)
            t_des_map[tid] = des  # None 가능

        target_margins    = []
        nontarget_matches = []
        timings_ms        = []   # 전처리 + ORB 추출 시간

        for gray, label in test_items:
            t0     = time.perf_counter()
            q_proc = _preprocess(gray, clip, tile, blur, gamma, sharp)
            _, q_des = orb.detectAndCompute(q_proc, None)
            timings_ms.append((time.perf_counter() - t0) * 1000)

            if q_des is None or len(q_des) == 0:
                continue

            sc = {}
            for tid, t_des in t_des_map.items():
                if t_des is None or len(t_des) == 0:
                    sc[tid] = 0; continue
                try:
                    ms  = bf.knnMatch(q_des, t_des, k=2)
                    cnt = sum(1 for m in ms
                              if len(m) == 2 and m[0].distance < lowe * m[1].distance)
                except Exception:
                    cnt = 0
                sc[tid] = cnt

            correct    = sc.get(label, 0) if label else 0
            wrongs     = [s for tid, s in sc.items() if tid != label]
            best_wrong = max(wrongs) if wrongs else 0

            if label and label in target_grays:
                target_margins.append(correct - best_wrong)
            else:
                nontarget_matches.append(max(sc.values()) if sc else 0)

        t_score  = float(np.mean(target_margins))    if target_margins    else 0.0
        nt_pen   = float(np.mean(nontarget_matches)) if nontarget_matches else 0.0
        disc_score = t_score - nt_pen
        avg_ms     = float(np.mean(timings_ms)) if timings_ms else 0.0
        return disc_score, avg_ms

    # ── Optuna objective ────────────────────────────────────────────────────────
    def objective(self, trial, target_grays, test_items):
        params = {
            "clahe_clip_limit": trial.suggest_categorical(
                "clahe_clip_limit", SEARCH_SPACE["clahe_clip_limit"]),
            "clahe_tile_grid":  trial.suggest_categorical(
                "clahe_tile_grid",  SEARCH_SPACE["clahe_tile_grid"]),
            "blur_ksize":       trial.suggest_categorical(
                "blur_ksize",       SEARCH_SPACE["blur_ksize"]),
            "gamma":            trial.suggest_categorical(
                "gamma",            SEARCH_SPACE["gamma"]),
            "sharpen_amount":   trial.suggest_categorical(
                "sharpen_amount",   SEARCH_SPACE["sharpen_amount"]),
            "nfeatures":        trial.suggest_categorical(
                "nfeatures",        SEARCH_SPACE["nfeatures"]),
            "lowe_ratio":       trial.suggest_categorical(
                "lowe_ratio",       SEARCH_SPACE["lowe_ratio"]),
        }

        # taboo 방어
        for taboo in self.taboo_list:
            if all(abs(params.get(k, 0) - taboo.get(k, 0)) < 1e-6
                   for k in taboo if k in params):
                trial.set_user_attr('disc_score', -9999.0)
                trial.set_user_attr('avg_ms', 0.0)
                return -9999.0

        disc_score, avg_ms = self._score(params, target_grays, test_items)

        # trial에 개별 지표 저장 (top-N 조회 시 활용)
        trial.set_user_attr('disc_score', disc_score)
        trial.set_user_attr('avg_ms', avg_ms)

        # 합성 점수: 판별력과 처리속도를 각각 [0,1] 정규화 후 가중합산
        disc_norm  = max(0.0, min(1.0,
            (disc_score - self.DISC_MIN) / (self.DISC_MAX - self.DISC_MIN)))
        speed_norm = max(0.0, min(1.0,
            (self.SLOW_MS - avg_ms) / (self.SLOW_MS - self.FAST_MS)))

        return self.accuracy_ratio * disc_norm + self.speed_ratio * speed_norm

    # ── 메인 탐색 ───────────────────────────────────────────────────────────────
    def run_night_tuning(self, n_trials=200, top_n=10):
        """
        Optuna Bayesian 탐색으로 최적 파라미터를 찾습니다.

        반환: (top_results, best_composite_score)
          top_results: list of dict (최대 top_n개, 합성점수 내림차순)
            {
              'rank'      : 순위 (1~top_n),
              'params'    : 파라미터 dict,
              'composite' : 합성 점수 (0~1),
              'disc_score': 판별력 (raw),
              'avg_ms'    : 처리시간 (ms),
            }
        """
        if not _HAS_OPTUNA:
            print("[AutoTuner] optuna 미설치 — pip install optuna")
            return [], 0.0

        spd_pct = int(round(self.speed_ratio * 100))
        acc_pct = int(round(self.accuracy_ratio * 100))
        print(f"[AutoTuner] 탐색 시작 ({n_trials}회) | 속도 {spd_pct}% / 정확도 {acc_pct}% ...")
        target_grays = self._load_target_grays()
        test_items   = self._load_test_items(target_grays)

        if not target_grays:
            print("[AutoTuner] 타겟 이미지 없음"); return [], 0.0
        if not test_items:
            print("[AutoTuner] 테스트 이미지 없음"); return [], 0.0

        # ── 안전장치: Warm-up 탐색 구간 확보 ────────────────────────────────────
        # TPESampler 기본 n_startup_trials=10은 너무 적음.
        # 전체 시도수의 15% (최소 30회)를 순수 무작위로 먼저 탐색하여
        # 초반 우연한 결과에 속아 좁은 구역만 파는 지역 최적화 함정을 방지.
        n_warmup = max(30, int(n_trials * 0.15))
        study = optuna.create_study(
            direction="maximize",
            sampler=optuna.samplers.TPESampler(seed=42, n_startup_trials=n_warmup),
        )
        study.optimize(
            lambda trial: self.objective(trial, target_grays, test_items),
            n_trials=n_trials,
            show_progress_bar=False,
        )

        # 유효 trial 수집 (taboo 제외, 완료된 것만)
        valid_trials = [
            t for t in study.trials
            if t.value is not None
            and t.value > -9999.0
            and t.state.name == "COMPLETE"
        ]
        valid_trials.sort(key=lambda t: t.value, reverse=True)

        top_results = []
        for i, trial in enumerate(valid_trials[:top_n]):
            top_results.append({
                'rank'      : i + 1,
                'params'    : dict(trial.params),
                'composite' : round(trial.value, 4),
                'disc_score': round(trial.user_attrs.get('disc_score', 0.0), 2),
                'avg_ms'    : round(trial.user_attrs.get('avg_ms', 0.0), 1),
            })

        best_composite = study.best_value
        if top_results:
            print(f"[AutoTuner] 완료 — 합성점수 1위: {top_results[0]['composite']:.4f} "
                  f"(판별력 {top_results[0]['disc_score']:.1f}, "
                  f"속도 {top_results[0]['avg_ms']:.1f}ms)")
        return top_results, best_composite

    def save_best_params(self, best_params):
        """
        탐색 결과를 params_config.json 에 저장 → 실시간 파이프라인 즉시 반영.
        기존 config의 다른 키(orb_compare_threshold 등)는 유지.
        """
        existing = {}
        if os.path.isfile(_PARAMS_CONFIG):
            try:
                with open(_PARAMS_CONFIG, encoding="utf-8") as f:
                    existing = json.load(f)
            except Exception:
                pass
        existing.update(best_params)
        os.makedirs(os.path.dirname(_PARAMS_CONFIG), exist_ok=True)
        with open(_PARAMS_CONFIG, "w", encoding="utf-8") as f:
            json.dump(existing, f, indent=2, ensure_ascii=False)
        print(f"[AutoTuner] params_config.json 저장 완료")

    def generate_morning_report(self, best_params, improvement_pct, llm_judge_api=None):
        lines = [
            f"판별력 향상: {improvement_pct:.1f}%",
            f"CLAHE clip: {best_params.get('clahe_clip_limit', '-')}",
            f"감마: {best_params.get('gamma', '-')}",
            f"샤프닝: {best_params.get('sharpen_amount', '-')}",
            f"ORB 특징점: {best_params.get('nfeatures', '-')}",
        ]
        return "\n".join(lines)


if __name__ == "__main__":
    # 단독 실행 테스트
    import glob
    target_dir  = os.path.join(_ROOT, "data", "targets")
    gt_dir      = os.path.join(_ROOT, "data", "gt_labeled")

    target_paths = [
        (p, os.path.splitext(os.path.basename(p))[0])
        for p in glob.glob(os.path.join(target_dir, "*"))
        if os.path.splitext(p)[1].lower() in _IMAGE_EXTS
    ]
    pending_paths = [
        (p, os.path.splitext(os.path.basename(p))[0].rsplit("_", 1)[0])
        for p in glob.glob(os.path.join(gt_dir, "**", "*"), recursive=True)
        if os.path.splitext(p)[1].lower() in _IMAGE_EXTS
    ]

    tuner = BayesianAutoTuner(target_paths, pending_paths,
                              speed_weight=5, accuracy_weight=5)
    top_results, best_score = tuner.run_night_tuning(n_trials=100, top_n=10)
    if top_results:
        print("\n[Top 10 결과]")
        for r in top_results:
            print(f"  {r['rank']}위 | 합성:{r['composite']:.4f} | "
                  f"판별력:{r['disc_score']:+.1f} | 속도:{r['avg_ms']:.1f}ms | "
                  f"params:{r['params']}")
        # 1위 자동 적용 (단독 실행 시)
        tuner.save_best_params(top_results[0]['params'])
