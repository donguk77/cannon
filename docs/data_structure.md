# 데이터 구조 문서 (Data Structure)

> 최종 업데이트: 2026-04-07

---

## 전체 구조 개요

모든 데이터는 `data/` 단일 폴더 아래에 통합되어 있습니다.

```
data/
├── targets/          ORB·Siamese 기준 화면 (타겟 이미지 원본)
├── yolo/             YOLO 학습 데이터셋
├── yolo_source/      YOLO 보조 데이터 원본
├── capture/          수동 스크린샷
├── gt_labeled/       GT 레이블링 완료 이미지
├── matched/          ORB 합격 자동 캡처
├── pending/          Hard Mining 대기 이미지
├── siamese_train/    Siamese 학습 데이터 (수동 라벨)
├── gt_labels.json
├── mask_config.json
├── params_config.json
└── roi_config.json
```

---

## 폴더별 상세 설명

### `data/targets/` — ORB·Siamese 기준 화면

ORB 매칭의 정답 기준 화면이자 Siamese 네트워크의 Few-Shot 앵커 이미지.
**타겟 화면을 교체할 때 이 폴더만 수정하면 양쪽에 동시 반영됩니다.**

```
targets/
├── 1.png   타겟 1번 화면
├── 2.png   타겟 2번 화면
├── 3.png   타겟 3번 화면
└── 4.png   타겟 4번 화면
```

| 참조 위치 | 용도 |
|-----------|------|
| `engine/matcher.py` | ORB 특징점 추출 기준 |
| `offline/siamese_classifier.py` | Siamese Few-Shot 앵커 |
| `scripts/train_siamese.py` | 앵커 증강 학습 소스 |
| `gui/tab_monitor.py` | 타겟 뷰어, ROI 설정 |
| `gui/tab_labeling.py` | 라벨링 탭 타겟 표시 |

---

### `data/yolo/` — YOLO 학습 데이터셋

`scripts/train_yolo.py`가 직접 사용하는 YOLO 표준 구조.

```
yolo/
├── images/
│   ├── train/   (80개 jpg)
│   └── val/     (20개 jpg)
├── labels/
│   ├── train/   (80개 txt — YOLO 형식)
│   └── val/     (20개 txt)
├── canon_data.yaml   학습 설정 (nc:1, names:[canon_monitor])
└── yolov8n-seg.pt    베이스 모델 (학습 시 사전학습 가중치)
```

> `train_yolo.py`가 `data/yolo_source/`의 파일을 여기로 복사해 학습에 사용합니다.

---

### `data/yolo_source/` — YOLO 보조 데이터 원본

수동으로 촬영·라벨링한 YOLO 학습용 원본 데이터 (100쌍).
`train_yolo.py` 실행 시 `data/yolo/images+labels/train/`으로 복사됩니다.

```
yolo_source/
├── YYYYMMDD_HHMMSS.jpg   원본 이미지
└── YYYYMMDD_HHMMSS.txt   YOLO 라벨 (class cx cy w h)
```

---

### `data/capture/` — 수동 스크린샷

실시간 관제 탭에서 [GT 캡처] 버튼으로 저장되는 이미지.
GT 라벨링의 소스 데이터.

```
파일명: capture_YYYYMMDD_HHMMSS_MS.png
```

---

### `data/gt_labeled/` — GT 레이블링 완료 이미지

`data/capture/`의 이미지에 타겟 ID를 지정해 복사한 정답 데이터셋.
`tab_guide.py`의 정답 기반 파라미터 최적화에 사용.

```
파일명: {타겟ID}_capture_YYYYMMDD_HHMMSS_MS.png
예시:   1_capture_20260403_104343_38.png
        none_capture_20260403_103729_48.png
```

---

### `data/matched/` — ORB 합격 자동 캡처

실시간 관제 중 ORB 매칭이 불합격→합격으로 전환되는 순간 자동 저장.
운영 중 계속 누적되므로 주기적으로 정리 필요 (`tab_training.py`의 "matched 폴더 비우기" 사용).

```
파일명: matched_YYYYMMDD_HHMMSS_MS_{N}of{M}.jpg
예시:   matched_20260403_103729_48_2of3.jpg   (3개 ROI 중 2개 합격)
```

---

### `data/pending/` — Hard Mining 대기 이미지

ORB 점수가 임계값 ±margin(2~3점) 구간에 속하는 "애매한" 프레임을 자동 저장.
`tab_training.py`의 Pending 검수실에서 확인 후 `data/siamese_train/`으로 분류.

```
파일명: pending_YYYYMMDD_HHMMSS_MS_s{score}.jpg
예시:   pending_20260403_103729_48_s09.jpg   (ORB 점수 9)
```

---

### `data/siamese_train/` — Siamese 학습 수동 라벨 데이터

pending 이미지를 수동 분류한 결과. `scripts/train_siamese.py`의 학습 소스.

```
siamese_train/
├── 1/        타겟 1 이미지
├── 2/        타겟 2 이미지
├── 3/        타겟 3 이미지
├── 4/        타겟 4 이미지
├── neg/      오답(Negative) 이미지  ← 현재 비어있음, 재학습 시 추가 필요
└── _queue/   Siamese 라벨링 자동 캡처 대기열
```

---

### 설정 파일

| 파일 | 내용 |
|------|------|
| `gt_labels.json` | capture 파일명 → 타겟 ID 매핑 `{"파일명": "1"\|"2"\|"3"\|"4"\|"none"}` |
| `mask_config.json` | ORB 마스크 설정 (동적 영역 제거, 타겟별 좌표) |
| `params_config.json` | ORB·전처리·임계값 파라미터 (UI에서 저장) |
| `roi_config.json` | ORB ROI 설정 (타겟별 관심 영역 좌표) |

---

## 전체 데이터 흐름

```
카메라 영상
    │
    ├─[수동 촬영]──→ capture/ ──→ gt_labeled/ ──→ 파라미터 최적화
    │
    ├─[ORB 합격 전환]──→ matched/
    │
    └─[Hard Mining]──→ pending/
                           │
                     [수동 분류 — tab_training]
                           │
                    siamese_train/1~4/neg/
                           │
                   scripts/train_siamese.py
                           │
                  models/siamese_finetuned.pt


yolo_source/ ──[train_yolo.py 복사]──→ yolo/images+labels/train/
                                              │
                                     scripts/train_yolo.py
                                              │
                                  models/canon_fast_yolo/weights/best.pt


targets/ ──→ engine/matcher.py        (ORB 기준 화면)
         └──→ offline/siamese_classifier.py  (Siamese 앵커)
```
