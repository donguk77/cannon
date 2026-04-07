"""
train_siamese.py — 샴 네트워크 파인튜닝 스크립트
==================================================
4장의 타겟 앵커 이미지를 카메라 현실적 증강으로 대량 생성하여
ResNet18 임베딩 추출기를 공장 모니터 도메인에 최적화합니다.

2단계 학습 전략:
  Phase 1: 백본 동결 → fc 레이어만 빠르게 수렴 (10 에폭)
  Phase 2: 상위 레이어 해동 → 전체 미세조정 (30 에폭)

실행:
  python scripts/train_siamese.py

출력:
  models/siamese_finetuned.pt  ← siamese_classifier.py가 자동으로 로드
"""

import os
import sys
import time

import torch
import torch.nn as nn
import torchvision.models as models
import torchvision.transforms as T
from torch.utils.data import Dataset, DataLoader
from PIL import Image

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)

# ── 설정 값 ───────────────────────────────────────────────────────────────────
ANCHOR_DIR        = os.path.join(_ROOT, "data", "targets")
SIAMESE_TRAIN_DIR = os.path.join(_ROOT, "data", "siamese_train")  # 수동 라벨 데이터
SAVE_PATH         = os.path.join(_ROOT, "models", "siamese_finetuned.pt")
AUG_PER_IMG       = 500   # 앵커만 모드: 이미지당 증강 수 (4 × 500 = 2,000장)
AUG_ANCHOR_MIX    = 200   # 혼합 모드: 앵커 보완 증강 수 (기존 50 → 200, 클래스 균형)
AUG_LABELED_LIGHT = 15    # 혼합 모드: 라벨 이미지당 가벼운 증강 수 (기존 5 → 15)
EPOCHS_FROZEN     = 10    # Phase 1: 백본 고정, FC만 학습 (기존 5 → 10)
EPOCHS_UNFROZEN   = 30    # Phase 2: 상위 레이어 해동 후 미세조정 (기존 15 → 30)
BATCH_SIZE        = 16
LR                = 1e-4
DEVICE            = torch.device("cuda" if torch.cuda.is_available() else "cpu")


# ── 커스텀 증강: 카메라 가우시안 노이즈 ──────────────────────────────────────
class AddGaussianNoise:
    """
    카메라 센서 특성상 발생하는 랜덤 픽셀 노이즈를 시뮬레이션합니다.
    std가 클수록 노이즈가 심함 (0.02 정도가 현실적).
    """
    def __init__(self, std: float = 0.02):
        self.std = std

    def __call__(self, tensor: torch.Tensor) -> torch.Tensor:
        noise = torch.randn_like(tensor) * self.std
        return (tensor + noise).clamp(0.0, 1.0)


# ── 학습용 증강 파이프라인 ────────────────────────────────────────────────────
# 카메라 촬영 환경 시뮬레이션:
#   ① RandomPerspective  : 카메라 각도 (비스듬히 찍히는 효과)
#   ② RandomRotation     : 카메라 기울기 (±8도 이내)
#   ③ ColorJitter        : 조명 밝기 / 대비 / 색온도 변화
#   ④ GaussianBlur       : 카메라 초점 흔들림 (50% 확률)
#   ⑤ AddGaussianNoise   : 카메라 센서 노이즈
#   ❌ Horizontal Flip   : 절대 사용 금지 (화면 텍스트·UI가 좌우 반전됨)
TRAIN_TRANSFORMS = T.Compose([
    T.Resize((256, 256)),
    T.RandomCrop(224),                                      # 화면 프레이밍 차이
    T.RandomPerspective(distortion_scale=0.25, p=0.6),      # 카메라 각도
    T.RandomRotation(degrees=8),                            # 카메라 기울기
    T.ColorJitter(
        brightness=0.4, contrast=0.4,
        saturation=0.3, hue=0.08                            # 조명·색온도
    ),
    T.RandomApply(
        [T.GaussianBlur(kernel_size=5, sigma=(0.5, 2.0))],  # 초점 흔들림
        p=0.5
    ),
    T.ToTensor(),
    T.Normalize(mean=[0.485, 0.456, 0.406],
                std=[0.229, 0.224, 0.225]),
    AddGaussianNoise(std=0.015),                            # 센서 노이즈
])

# 검증·앵커 임베딩용 (증강 없이 정규화만)
EVAL_TRANSFORMS = T.Compose([
    T.Resize((224, 224)),
    T.ToTensor(),
    T.Normalize(mean=[0.485, 0.456, 0.406],
                std=[0.229, 0.224, 0.225]),
])


# ── 데이터셋 ──────────────────────────────────────────────────────────────────
class AugmentedAnchorDataset(Dataset):
    """
    앵커 폴더의 이미지(1.png~4.png)를 aug_per_img배 증강하여
    학습 데이터셋을 구성합니다.

    예: 4장 × 300 = 1,200장
    클래스 번호: 파일 정렬 순서(0~3) → 1.png=0, 2.png=1, 3.png=2, 4.png=3
    """

    def __init__(self, anchor_dir: str, aug_per_img: int, transform=None):
        self.samples   = []
        self.transform = transform

        files = sorted([
            f for f in os.listdir(anchor_dir)
            if f.lower().endswith(('.png', '.jpg', '.jpeg'))
        ])

        if not files:
            raise FileNotFoundError(
                f"앵커 이미지가 없습니다: {anchor_dir}\n"
                f"data/targets/ 에 1.png~4.png 가 있는지 확인하세요."
            )

        for class_idx, fname in enumerate(files):
            img = Image.open(os.path.join(anchor_dir, fname)).convert("RGB")
            for _ in range(aug_per_img):
                self.samples.append((img, class_idx))

        self._num_classes = len(files)
        self._files       = files
        print(f"[Dataset] 클래스 {self._num_classes}개 × {aug_per_img}배 증강 "
              f"= 총 {len(self.samples):,}장")
        for i, f in enumerate(files):
            print(f"         클래스 {i}: {f}")

    def __len__(self) -> int:
        return len(self.samples)

    def __getitem__(self, idx):
        img, label = self.samples[idx]
        if self.transform:
            img = self.transform(img)
        return img, label

    @property
    def num_classes(self) -> int:
        return self._num_classes

    @property
    def class_files(self) -> list:
        return self._files


class MixedDataset(Dataset):
    """
    실제 라벨 데이터(data/siamese_train/) + 앵커 보완 증강 혼합 데이터셋.

    클래스별 전략:
      - 라벨 이미지 있음: 실제 이미지 × AUG_LABELED_LIGHT + 앵커 × AUG_ANCHOR_MIX
      - 라벨 이미지 없음: 앵커만 × AUG_PER_IMG (기존 방식 유지)
      - neg/ 폴더 있으면 배경 클래스(N+1)로 추가
    """
    def __init__(self, anchor_dir: str, train_dir: str, class_files: list, transform=None):
        self.samples   = []
        self.transform = transform

        # ── YOLO 크롭 탐지기 초기화 ──
        try:
            from engine.detector import BezelDetector
            active_file = os.path.join(_ROOT, "data", "active_model.json")
            model_path = None
            if os.path.exists(active_file):
                import json
                with open(active_file) as af:
                    model_path = os.path.join(_ROOT, json.load(af).get("path", ""))
            if not model_path or not os.path.exists(model_path):
                seg_best  = os.path.join(_ROOT, "models", "canon_fast_yolo", "weights", "best.pt")
                det_model = os.path.join(_ROOT, "yolov8n.pt")
                model_path = seg_best if os.path.exists(seg_best) else (
                    det_model if os.path.exists(det_model) else None)
            
            detector = BezelDetector(model_path=model_path) if model_path else None
            print(f"[MixedDataset] YOLO 탐지기 초기화 완료: {os.path.basename(model_path) if model_path else 'None'}")
        except Exception as e:
            print(f"[MixedDataset] YOLO 탐지기 초기화 실패 (크롭 생략): {e}")
            detector = None

        import cv2
        import numpy as np

        def crop_with_yolo(img_pil):
            if detector is None:
                return img_pil
            img_bgr = cv2.cvtColor(np.array(img_pil), cv2.COLOR_RGB2BGR)
            cropped, _ = detector.detect_and_crop(img_bgr)
            if cropped is not None and cropped.size > 0:
                return Image.fromarray(cv2.cvtColor(cropped, cv2.COLOR_BGR2RGB))
            # 베젤을 못 찾으면 원본 그대로 반환
            return img_pil

        base_files = [f for f in class_files if f != "neg"]
        for class_idx, anchor_file in enumerate(base_files):
            anchor_img = Image.open(
                os.path.join(anchor_dir, anchor_file)).convert("RGB")
            key       = os.path.splitext(anchor_file)[0]   # "1","2","3","4"
            label_dir = os.path.join(train_dir, key)
            labeled   = []
            if os.path.isdir(label_dir):
                for fname in os.listdir(label_dir):
                    if fname.lower().endswith((".jpg", ".jpeg", ".png")):
                        try:
                            raw_img = Image.open(
                                os.path.join(label_dir, fname)).convert("RGB")
                            cropped_img = crop_with_yolo(raw_img)
                            labeled.append(cropped_img)
                        except Exception:
                            pass

            if labeled:
                for img in labeled:
                    for _ in range(AUG_LABELED_LIGHT):
                        self.samples.append((img, class_idx))
                for _ in range(AUG_ANCHOR_MIX):
                    self.samples.append((anchor_img, class_idx))
            else:
                for _ in range(AUG_PER_IMG):
                    self.samples.append((anchor_img, class_idx))

        # neg 클래스 처리
        if "neg" in class_files:
            neg_idx = len(base_files)
            neg_dir = os.path.join(train_dir, "neg")
            if os.path.isdir(neg_dir):
                for fname in os.listdir(neg_dir):
                    if fname.lower().endswith((".jpg", ".jpeg", ".png")):
                        try:
                            raw_img = Image.open(
                                os.path.join(neg_dir, fname)).convert("RGB")
                            cropped_img = crop_with_yolo(raw_img)
                            for _ in range(AUG_LABELED_LIGHT):
                                self.samples.append((cropped_img, neg_idx))
                        except Exception:
                            pass

        self._num_classes = len(class_files)
        self._files       = class_files
        print(f"[MixedDataset] 클래스 {self._num_classes}개 | 총 샘플 {len(self.samples):,}장")
        for i, f in enumerate(class_files):
            print(f"               클래스 {i}: {f}")

    def __len__(self) -> int:
        return len(self.samples)

    def __getitem__(self, idx):
        img, label = self.samples[idx]
        if self.transform:
            img = self.transform(img)
        return img, label

    @property
    def num_classes(self) -> int:
        return self._num_classes

    @property
    def class_files(self) -> list:
        return self._files


def _detect_labeled_data():
    """
    data/siamese_train/ 폴더를 확인하여 라벨 데이터 유무와 클래스 목록을 반환.
    반환: (class_files, has_labeled, has_neg)
    """
    anchor_files = sorted([
        f for f in os.listdir(ANCHOR_DIR)
        if f.lower().endswith((".png", ".jpg", ".jpeg"))
    ])
    has_labeled = False
    for af in anchor_files:
        key = os.path.splitext(af)[0]
        label_dir = os.path.join(SIAMESE_TRAIN_DIR, key)
        if os.path.isdir(label_dir) and any(
            f.lower().endswith((".jpg", ".jpeg", ".png"))
            for f in os.listdir(label_dir)
        ):
            has_labeled = True
            break

    neg_dir = os.path.join(SIAMESE_TRAIN_DIR, "neg")
    has_neg = os.path.isdir(neg_dir) and any(
        f.lower().endswith((".jpg", ".jpeg", ".png"))
        for f in os.listdir(neg_dir)
    )
    class_files = list(anchor_files) + (["neg"] if has_neg else [])
    return class_files, has_labeled, has_neg


# ── 에폭 학습 함수 ────────────────────────────────────────────────────────────
def train_epoch(model, loader, criterion, optimizer, device):
    """1 에폭 학습 후 (loss, accuracy%) 반환"""
    model.train()
    total_loss, correct, total = 0.0, 0, 0

    for imgs, labels in loader:
        imgs, labels = imgs.to(device), labels.to(device)
        optimizer.zero_grad()
        outputs = model(imgs)
        loss    = criterion(outputs, labels)
        loss.backward()
        optimizer.step()

        total_loss += loss.item() * len(labels)
        correct    += (outputs.argmax(1) == labels).sum().item()
        total      += len(labels)

    return total_loss / total, correct / total * 100.0


# ── 메인 학습 루프 ────────────────────────────────────────────────────────────
def run_training():
    print("=" * 60)
    print(" 샴 네트워크 파인튜닝 시작")
    print("=" * 60)
    print(f"  디바이스        : {DEVICE}")
    print(f"  앵커 폴더       : {ANCHOR_DIR}")
    print(f"  라벨 데이터     : {SIAMESE_TRAIN_DIR}")
    print(f"  저장 경로       : {SAVE_PATH}")
    print()

    # ── 1. 데이터셋 & 로더 준비 ──────────────────────────────────
    print("[5%] 라벨 데이터 확인 중...")
    class_files, has_labeled, has_neg = _detect_labeled_data()

    if has_labeled:
        mode_str = "혼합 모드 (실제 라벨 + 앵커 보완)"
        if has_neg:
            mode_str += f" + neg 클래스 포함 → 총 {len(class_files)}클래스"
        print(f"[8%] {mode_str}")
        dataset = MixedDataset(ANCHOR_DIR, SIAMESE_TRAIN_DIR, class_files, TRAIN_TRANSFORMS)
    else:
        mode_str = "앵커 단독 모드 (라벨 데이터 없음, anchor-only 폴백)"
        print(f"[8%] {mode_str}")
        dataset = AugmentedAnchorDataset(ANCHOR_DIR, AUG_PER_IMG, TRAIN_TRANSFORMS)
        class_files = dataset.class_files

    num_classes = dataset.num_classes
    loader      = DataLoader(
        dataset, batch_size=BATCH_SIZE, shuffle=True, num_workers=0
    )

    # ── 2. 모델: ResNet18 + N클래스 분류 헤드 ────────────────────
    print(f"[12%] ResNet18 모델 로드 중... ({num_classes}클래스)")
    model    = models.resnet18(weights=models.ResNet18_Weights.DEFAULT)
    model.fc = nn.Linear(512, num_classes)
    model    = model.to(DEVICE)

    # ── 클래스 불균형 자동 보정 (neg가 많을 때 가중치 조정) ───────
    # 각 클래스의 샘플 수를 세어 역수로 가중치를 계산합니다.
    # 예: neg=175샘플, 클래스1=60샘플 → neg 가중치↓, 클래스1 가중치↑
    class_counts = [0] * num_classes
    for _, lbl in dataset:
        class_counts[lbl] += 1
    total = sum(class_counts)
    # 가중치 = 전체평균 / 클래스샘플수 (샘플 적을수록 높은 가중치)
    weights = torch.tensor(
        [total / (num_classes * max(c, 1)) for c in class_counts],
        dtype=torch.float
    ).to(DEVICE)
    print(f"[13%] 클래스 가중치 적용:")
    for i, (cf, cnt, w) in enumerate(zip(dataset.class_files, class_counts, weights.tolist())):
        print(f"       클래스{i}({cf}): {cnt}샘플 → weight={w:.3f}")
    criterion = nn.CrossEntropyLoss(weight=weights)

    # ── Phase 1: 백본 완전 동결, FC만 ────────────────────────────
    print(f"[15%] [Phase 1] 백본 동결 → FC만 학습 ({EPOCHS_FROZEN} epochs)")
    print("-" * 40)

    for param in model.parameters():
        param.requires_grad = False
    for param in model.fc.parameters():
        param.requires_grad = True

    optimizer = torch.optim.Adam(model.fc.parameters(), lr=LR)

    for ep in range(EPOCHS_FROZEN):
        t0 = time.time()
        loss, acc = train_epoch(model, loader, criterion, optimizer, DEVICE)
        pct = 15 + int((ep + 1) / EPOCHS_FROZEN * 30)   # 15% ~ 45%
        print(f"[{pct}%] Phase1 Epoch {ep+1:2d}/{EPOCHS_FROZEN} | "
              f"Loss: {loss:.4f} | Acc: {acc:5.1f}% | {time.time()-t0:.0f}s")

    # ── Phase 2: layer3, layer4, fc 해동 → 미세조정 ──────────────
    print()
    print(f"[45%] [Phase 2] 상위 레이어 해동 → 미세조정 ({EPOCHS_UNFROZEN} epochs)")
    print("-" * 40)

    for name, param in model.named_parameters():
        if any(k in name for k in ['layer3', 'layer4', 'fc']):
            param.requires_grad = True

    optimizer = torch.optim.Adam(
        filter(lambda p: p.requires_grad, model.parameters()),
        lr=LR * 0.1
    )
    scheduler = torch.optim.lr_scheduler.CosineAnnealingLR(
        optimizer, T_max=EPOCHS_UNFROZEN, eta_min=1e-6
    )

    best_acc   = 0.0
    best_state = None

    for ep in range(EPOCHS_UNFROZEN):
        t0 = time.time()
        loss, acc = train_epoch(model, loader, criterion, optimizer, DEVICE)
        scheduler.step()

        flag = ""
        if acc > best_acc:
            best_acc   = acc
            best_state = {k: v.clone() for k, v in model.state_dict().items()}
            flag = " ★ best"

        pct = 45 + int((ep + 1) / EPOCHS_UNFROZEN * 45)  # 45% ~ 90%
        print(f"[{pct}%] Phase2 Epoch {ep+1:2d}/{EPOCHS_UNFROZEN} | "
              f"Loss: {loss:.4f} | Acc: {acc:5.1f}%"
              f" | lr={scheduler.get_last_lr()[0]:.2e}"
              f" | {time.time()-t0:.0f}s{flag}")

    # 최고 성능 가중치 복원
    if best_state:
        model.load_state_dict(best_state)

    # ── 3. FC 가중치 보관 후 → 임베딩 추출기로 변환하여 저장 ──────
    print()
    print("[92%] FC 헤드 보관 → 임베딩 추출기(512-dim)로 변환 중...")

    fc_state = {k: v.clone().cpu() for k, v in model.fc.state_dict().items()}
    model.fc = nn.Identity()
    model    = model.cpu().eval()

    os.makedirs(os.path.dirname(SAVE_PATH), exist_ok=True)
    torch.save({
        'state_dict' : model.state_dict(),
        'fc_state'   : fc_state,
        'num_classes': num_classes,
        'class_files': dataset.class_files,
        'aug_per_img': AUG_PER_IMG,
        'best_acc'   : best_acc,
    }, SAVE_PATH)
    print(f"[95%] 저장 완료 → {SAVE_PATH}")
    print(f"      최고 훈련 정확도: {best_acc:.1f}%  |  클래스: {class_files}")

    # ── 4. 빠른 검증: 앵커 자기매칭 ─────────────────────────────
    print()
    print("[97%] 앵커 자기매칭 테스트...")
    print("-" * 40)

    anchor_files_only = [f for f in class_files if f != "neg"]
    embeddings = {}
    for fname in anchor_files_only:
        img = Image.open(os.path.join(ANCHOR_DIR, fname)).convert("RGB")
        t   = EVAL_TRANSFORMS(img).unsqueeze(0)
        with torch.no_grad():
            emb = nn.functional.normalize(model(t), p=2, dim=1)
        embeddings[fname] = emb

    all_ok = True
    for q_name, q_emb in embeddings.items():
        scores = {n: torch.sum(q_emb * e).item() for n, e in embeddings.items()}
        best   = max(scores, key=scores.get)
        ok     = best == q_name
        all_ok = all_ok and ok
        icon   = "✅" if ok else "❌"
        print(f"  {q_name} → 최고 유사도: {best} ({scores[best]:.4f}) {icon}")

    print()
    if all_ok:
        print("🎉 파인튜닝 완료! 모든 앵커가 자기 자신을 1등으로 인식합니다.")
    else:
        print("⚠️  일부 앵커가 자기 자신을 못 찾았습니다.")
    print("[100%] 완료! siamese_classifier.py가 자동으로 새 가중치를 사용합니다.")


if __name__ == "__main__":
    run_training()

