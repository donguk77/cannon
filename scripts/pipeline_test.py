"""
pipeline_test.py — 실제 데이터로 전체 파이프라인을 검증하는 통합 테스트 스크립트

[연결 데이터 구성]
  - YOLO 모델  : yolov8n.pt (사전학습 가중치, 베젤 탐지용)
  - ORB 타겟   : data/targets/1.png ~ 4.png
  - 테스트 영상: dataset_video/1-1.mp4 (또는 1-2.mp4, 4.mp4)

[실행 방법]
  py scripts/pipeline_test.py
  py scripts/pipeline_test.py dataset_video/4.mp4  (원하는 영상 지정)
"""

import sys
import os
import time
import cv2

# 프로젝트 루트를 경로에 등록 (engine/ 등을 import할 수 있게)
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)

from engine.preprocessor  import ImagePreprocessor
from engine.detector      import BezelDetector
from engine.matcher       import ScreenMatcher
from engine.frame_skipper import FrameSkipper
from engine.ocr_fallback  import OCRFallback

# ─── 경로 상수 (프로젝트 루트 기준 절대경로) ────────────────────
TARGET_IMAGE_DIR = os.path.join(_ROOT, "data", "targets")
DEFAULT_VIDEO    = os.path.join(_ROOT, "dataset_video", "1-1.mp4")
YOLO_MODEL_PATH  = os.path.join(_ROOT, "yolov8n.pt")   # 사전학습 모델

def run_pipeline(video_path: str):
    print(f"\n{'='*60}")
    print(f" Canon AI Vision 2.1 — 파이프라인 통합 테스트")
    print(f"{'='*60}")
    print(f" 테스트 영상  : {os.path.basename(video_path)}")
    print(f" 타겟 이미지  : {TARGET_IMAGE_DIR}")
    print(f" YOLO 모델   : {YOLO_MODEL_PATH}")
    print(f"{'='*60}\n")

    # ─── 1. 모듈 초기화 ──────────────────────────────────────
    preprocessor  = ImagePreprocessor()
    print("[1/5] 전처리기(Preprocessor) 초기화 완료")

    # YOLO 모델이 없으면 베젤 탐지를 우회하고 원본 프레임 전체를 사용
    detector = None
    if os.path.exists(YOLO_MODEL_PATH):
        detector = BezelDetector(model_path=YOLO_MODEL_PATH, conf_threshold=0.4)
        print(f"[2/5] YOLO 베젤 탐지기 초기화 완료 ({YOLO_MODEL_PATH})")
    else:
        print(f"[2/5] ⚠️  YOLO 모델 파일 없음 → 베젤 탐지 생략, 원본 프레임 전체 사용")
        print(f"      (학습 후 {YOLO_MODEL_PATH} 에 모델 파일을 넣어주세요)")

    matcher = ScreenMatcher(orb_nfeatures=700, lowe_ratio=0.75, match_threshold=25)
    print("[3/5] ORB 특징점 매처(Matcher) 초기화 완료")

    # 타겟 이미지 로드
    targets = matcher.load_targets_from_dir(TARGET_IMAGE_DIR)
    if not targets:
        print(f"      ❌  target_image 폴더에서 이미지를 불러오지 못했습니다.")
        print(f"      경로 확인: {TARGET_IMAGE_DIR}")
        return
    print(f"      → 총 {len(targets)}개 타겟 화면 로드 완료: {list(targets.keys())}")

    skipper = FrameSkipper(skip_frames=2)
    print("[4/5] 프레임 스킵퍼 초기화 완료 (1 처리 : 2 생략)")

    ocr = OCRFallback(expected_keywords=["start", "next", "ok", "cancel", "error"])
    print("[5/5] OCR 폴백 모듈 초기화 완료\n")

    # ─── 2. 영상 읽기 ────────────────────────────────────────
    cap = cv2.VideoCapture(video_path)
    if not cap.isOpened():
        print(f"❌  영상을 열 수 없습니다: {video_path}")
        return

    total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    fps_orig     = cap.get(cv2.CAP_PROP_FPS) or 30.0
    print(f"영상 정보: 총 {total_frames}프레임 | 원본 {fps_orig:.1f} FPS")
    print(f"{'─'*60}")

    # ─── 3. 메인 루프 ────────────────────────────────────────
    frame_idx  = 0
    results    = []  # (frame_idx, screen_id, score, method, passed) 기록
    t_start    = time.time()

    while True:
        ret, frame = cap.read()
        if not ret:
            break
        frame_idx += 1

        # 3-A. 프레임 스킵 판단
        if not skipper.should_process():
            # 이전 결과를 재사용(좀비 모드)
            prev = skipper.get_zombie_result()
            if prev and frame_idx % 30 == 0:
                print(f"  [{frame_idx:5d}] [좀비 유지] {prev}")
            continue

        t0 = time.time()

        # 3-B. YOLO 베젤 탐지 → 모델 없으면 원본 사용
        if detector:
            roi, bbox = detector.detect_and_crop(frame)
            if roi is None:
                roi = frame  # 탐지 실패 시 원본 전체 사용
        else:
            roi = frame

        # 3-C. 전처리 (ORB용)
        orb_ready = preprocessor.preprocess_for_orb(roi)

        # 3-D. 각 타겟과 ORB 비교 → 최고 점수 선택
        best_id, best_score, best_passed = None, 0, False
        for screen_id, target_des in targets.items():
            score, passed = matcher.compare_screens(orb_ready, target_des)
            if score > best_score:
                best_id, best_score, best_passed = screen_id, score, passed

        method = "ORB"

        # 3-E. ORB 실패 → OCR 폴백
        if not best_passed:
            rescued, text = ocr.rescue_judge(orb_ready)
            if rescued:
                best_passed = True
                method = "OCR 폴백"
            else:
                method = "탐지 실패"

        elapsed_ms = (time.time() - t0) * 1000

        # 좀비 메모리 업데이트
        result_summary = f"화면 {best_id}번 | 점수 {best_score} | {method}"
        skipper.update_zombie_memory(result_summary)
        results.append((frame_idx, best_id, best_score, method, best_passed))

        # 30프레임마다 콘솔 출력
        if frame_idx % 30 == 0:
            status = "✅ 통과" if best_passed else "❌ 실패"
            print(f"  [{frame_idx:5d}] {status} | 화면:{best_id}번 | 점수:{best_score:3d} | {method:<10} | {elapsed_ms:.1f}ms")

        # 결과를 영상 프레임 위에 시각화 후 화면 출력
        color = (0, 200, 0) if best_passed else (0, 0, 255)
        label = f"Screen:{best_id} Score:{best_score} [{method}]"
        cv2.putText(frame, label, (20, 50), cv2.FONT_HERSHEY_SIMPLEX, 1.2, color, 2)
        cv2.imshow("Canon AI Vision 2.1 — Pipeline Test (Q: 종료)", frame)
        if cv2.waitKey(1) & 0xFF == ord('q'):
            print("\n[사용자 종료 요청]")
            break

    cap.release()
    cv2.destroyAllWindows()

    # ─── 4. 최종 통계 ────────────────────────────────────────
    elapsed_total = time.time() - t_start
    processed     = len(results)
    passed        = sum(1 for r in results if r[4])
    avg_fps       = processed / elapsed_total if elapsed_total > 0 else 0

    print(f"\n{'='*60}")
    print(f" 파이프라인 테스트 결과 요약")
    print(f"{'='*60}")
    print(f" 처리 프레임    : {processed} / {frame_idx}")
    print(f" 통과율         : {passed}/{processed} ({100*passed/processed:.1f}%)" if processed else " 처리된 프레임 없음")
    print(f" 평균 처리 FPS  : {avg_fps:.1f} FPS")
    print(f" 총 소요 시간   : {elapsed_total:.1f}초")
    print(f"{'='*60}\n")


if __name__ == "__main__":
    video = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_VIDEO
    if not os.path.exists(video):
        print(f"❌  영상 파일을 찾을 수 없습니다: {video}")
        print(f"   사용법: py scripts/pipeline_test.py [영상경로]")
        print(f"   기본값: {DEFAULT_VIDEO}")
    else:
        run_pipeline(video)
