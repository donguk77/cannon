import os
import sys
import json
import cv2
import numpy as np
import optuna

# 프로젝트 루트 참조 (엔진 모듈 임포트용)
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from engine.matcher import ScreenMatcher

class BayesianAutoTuner:
    """
    [자동 파라미터 최적화(Hyperparameter Tuning) 통제실 - 고도화 버전]
    가짜 점수(Dummy)를 모두 제거하고 실제 `engine.matcher.ScreenMatcher`를 생성하여
    물리적으로 전처리를 가하고 실제 ORB 매칭 점수를 테스트하는 실전(Real) 최적화 코어입니다.
    """
    def __init__(self, target_image_paths, pending_image_paths, taboo_file=None):
        if taboo_file is None:
            taboo_file = os.path.join(_ROOT, "models", "taboo_list.json")
            
        # 1. 안전망: 폴더가 없으면 프로그램이 뻗는 현상(FileNotFoundError) 극복
        os.makedirs(os.path.dirname(taboo_file), exist_ok=True)
        
        self.target_paths = target_image_paths
        self.pending_paths = pending_image_paths
        self.taboo_file = taboo_file
        self.taboo_list = self._load_taboo_list()
        
    def _load_taboo_list(self):
        """사용자가 롤백 버튼을 눌러 박제한 '독극물(망한 파라미터)' 리스트를 불러옵니다."""
        if os.path.exists(self.taboo_file):
            try:
                with open(self.taboo_file, 'r', encoding='utf-8') as f:
                    return json.load(f)
            except:
                pass
        return []

    def _apply_preprocessing(self, img, params):
        """Optuna가 제안한 파라미터로 5단계 컨베이어 벨트를 통과시킵니다."""
        # 방어벽: 채널이 3개(컬러)면 먼저 흑백(Grayscale)으로 변환
        if len(img.shape) == 3:
            img = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)

        # 1. Blur (Gaussian or Median) - 노이즈 제거
        blur_type = params['blur_type']
        ksize = params['blur_ksize']
        if blur_type == 'gaussian':
            img = cv2.GaussianBlur(img, (ksize, ksize), 0)
        else:
            img = cv2.medianBlur(img, ksize)
            
        # 2. CLAHE - 조명 불균형 해소
        clahe = cv2.createCLAHE(clipLimit=params['clahe_clip'], tileGridSize=(8, 8))
        img = clahe.apply(img)
        
        # 3. Top-hat Transform - 배경 조명 노이즈 제거 및 UI 도드라짐
        kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (params['tophat_ksize'], params['tophat_ksize']))
        tophat = cv2.morphologyEx(img, cv2.MORPH_TOPHAT, kernel)
        img = cv2.add(img, tophat)
        
        # 4. Laplacian / Unsharp Masking - 특징점 에지 강조
        if params['use_laplacian']:
            laplacian = cv2.Laplacian(img, cv2.CV_64F)
            img = cv2.convertScaleAbs(img - (params['laplacian_alpha'] * laplacian))
            
        # 5. Min-Max Normalization - 0~255 정규화 강건성
        img = cv2.normalize(img, None, 0, 255, cv2.NORM_MINMAX)
        
        return img

    def objective(self, trial):
        """Optuna가 밤새 수백 번 반복 테스트할 [실전] 채점 함수"""
        # ─── 파라미터 조합 (검색 공간) ───
        params = {
            'blur_type': trial.suggest_categorical("blur_type", ["gaussian", "median"]),
            'blur_ksize': trial.suggest_categorical("blur_ksize", [3, 5, 7]),
            'clahe_clip': trial.suggest_float("clahe_clip", 1.0, 4.0),
            'tophat_ksize': trial.suggest_categorical("tophat_ksize", [9, 15, 21]),
            'use_laplacian': trial.suggest_categorical("use_laplacian", [True, False]),
            'laplacian_alpha': trial.suggest_float("laplacian_alpha", 0.1, 1.0),
            'orb_nfeatures': trial.suggest_int("orb_nfeatures", 500, 2000, step=100)
        }
        
        # ─── 롤백 오답노트 방어 (Local Minima 회피) ───
        for taboo in self.taboo_list:
            if abs(params['clahe_clip'] - taboo.get('clahe_clip', 0)) < 0.5 and \
               params['blur_ksize'] == taboo.get('blur_ksize', 0):
                return -9999.0  # 치명적 독극물 판정 (탐색 즉시 종료)
        
        # ─── [핵심 연결] 실제 엔진(Matcher) 가동 ───
        matcher = ScreenMatcher(orb_nfeatures=params['orb_nfeatures'])
        
        targets_des = []
        for t_path in self.target_paths:
            t_img = cv2.imread(t_path, cv2.IMREAD_GRAYSCALE)
            if t_img is not None:
                # 타겟 이미지도 동일한 베이시안 파라미터로 전처리
                t_img_p = self._apply_preprocessing(t_img, params)
                _, des = matcher.get_features(t_img_p)
                if des is not None:
                    targets_des.append(des)
        
        if not targets_des:
            return 0.0 # 타겟이 정상이 아니면 무조건 0점 처리
            
        total_good_matches = 0
        valid_count = 0
        
        # 보류/오류 판정난 이미지(pending)들을 테스트하여 일치도를 구함
        for p_path in self.pending_paths:
            p_img = cv2.imread(p_path, cv2.IMREAD_GRAYSCALE)
            if p_img is None: 
                continue
            
            # 여기서 5단계 파이프라인 컨베이어 벨트를 탑니다.
            p_img_p = self._apply_preprocessing(p_img, params)
            
            best_match_points = 0
            for t_des in targets_des:
                # engine.matcher 의 실제 KNN 매칭 로직 호출
                # (Lowe's ratio 테스트를 통과한 짱짱한 특징점 쌍의 개수 반환)
                score_pts, is_passed = matcher.compare_screens(p_img_p, t_des)
                if score_pts > best_match_points:
                    best_match_points = score_pts
            
            total_good_matches += best_match_points
            valid_count += 1
            
        if valid_count == 0:
            return 0.0
            
        # 평균 매칭 포인트(개수)를 반환 (이 점수를 100점 만점으로 끌어올리려 노력함)
        average_match_score = total_good_matches / valid_count
        return average_match_score

    def run_night_tuning(self, n_trials=50):
        """공장 불 꺼진 후 기계가 스스로 최적 조합을 찾음"""
        print("🌙 [AutoTuner] 야간 실전 매칭 시뮬레이션 및 5단계 최적화 가동...")
        # 로깅 과부하 방지
        optuna.logging.set_verbosity(optuna.logging.WARNING) 
        
        study = optuna.create_study(direction="maximize")
        study.optimize(self.objective, n_trials=n_trials)
        
        best_params = study.best_params
        best_score = study.best_value
        print(f"🎯 새벽 탐색 종료 - 최고 평균 매칭 특징점 수: {best_score:.2f}개")
        return best_params, best_score

    def generate_morning_report(self, best_params, improvement_pct, llm_judge_api):
        """제미나이 API에 결과 수치를 던져 짧고 이해하기 쉬운 브리핑을 만들어냅니다."""
        system_prompt = (
            f"당신은 공장 AI 시스템입니다. 오늘 밤 최적화 결과로 조명 대비(CLAHE)를 {best_params['clahe_clip']:.2f}로 높이고, "
            f"특징점을 {best_params['orb_nfeatures']}개로 잡았더니 기존 대비 일치도가 {improvement_pct}% 상승했습니다. "
            "이 수치들을 포함하여 공장 반장님이 [업데이트 승인] 버튼을 누르도록 3줄 이내로 매우 친절하고 확신에 찬 요약 보고서를 작성하세요."
        )
        return f"[AI 실제 처리 요약 대기 중...]\n(Gemini API 통신 자리)"

    def register_taboo_rollback(self, bad_params):
        """관리자가 업데이트 후 [성능 하락/롤백] 버튼 클릭 시, 해당 파라미터를 영구 박제함"""
        self.taboo_list.append(bad_params)
        with open(self.taboo_file, 'w', encoding='utf-8') as f:
            json.dump(self.taboo_list, f, indent=4)
        print("🚨 [AutoTuner] 해당 파라미터를 블랙리스트에 등록했습니다. 앞으로 이 근처는 탐색하지 않습니다.")

if __name__ == "__main__":
    tuner = BayesianAutoTuner([], [])
