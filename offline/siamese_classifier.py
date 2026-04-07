import os
import torch
import torch.nn as nn
import torchvision.models as models
import torchvision.transforms as transforms
from PIL import Image

# 파인튜닝 가중치 저장 경로 (train_siamese.py 실행 결과물)
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_FINETUNED_PATH = os.path.join(_ROOT, "models", "siamese_finetuned.pt")

class SiameseClassifier:
    """
    [Track B 오프라인 정밀 분석 파이프라인의 메인 두뇌]
    낮 실시간 레이스에서 '애매함' 판정을 받았던 에러 화면(Pending)들을 야간에 돌려,
    단 1~5장의 깨끗한 타겟 정답 사진(Anchor)과 "수학적/의미론적 DNA 거리"를 계산해 냅니다.
    이 과정을 통해 비전문가 작업자가 수백 장을 일일이 라벨링하지 않아도 
    기계가 스스로 정답 폴더로 사진을 분류수거(Self-Labeling)해버립니다.
    """
    def __init__(self, anchor_dir=None):
        if anchor_dir is None:
            anchor_dir = os.path.join(_ROOT, "data", "targets")
        
        # 산업용 똥컴(엣지 로컬 PC)에서도 돌아가야 하므로 GPU가 없으면 CPU 모드로 유연하게 돌아가게 설계
        self.device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
        
        # 1. 뼈대: 아주 가벼운 ResNet18을 베이스로 가져옵니다. (강력한 샴 네트워크의 근간)
        # 최신 모델(비전 LLM 등)은 공장 PC에서 안 돌아가므로 철저히 트레이드오프를 고려한 가성비 선택입니다.
        # [버그 수정] pretrained=True는 PyTorch >= 1.13에서 deprecated 됨. 최신 방식으로 교체.
        self.model = models.resnet18(weights=models.ResNet18_Weights.DEFAULT)
        # 단순히 멍멍이/소분류를 뱉는 마지막 대뇌 피질(fc 계층)을 뭉텅 잘라내고,
        # 사진을 보여주면 '512개의 DNA 숫자 배열(Embedding)'만 길게 뱉도록 개조해줍니다.
        self.model.fc = nn.Identity()

        # ── 파인튜닝 가중치 자동 로드 ──────────────────────────────
        # scripts/train_siamese.py 실행 결과물(siamese_finetuned.pt)이 있으면 자동 적용.
        # 없으면 ImageNet 사전학습 가중치 그대로 사용 (하위 호환 보장).
        self.fc_head       = None   # 직접 분류 헤드 (코사인 유사도보다 정확)
        self.class_files   = []     # 학습 시 클래스 순서 (fc_head argmax 해석용)
        self.num_classes   = 0

        if os.path.isfile(_FINETUNED_PATH):
            ckpt = torch.load(_FINETUNED_PATH, map_location="cpu")
            self.model.load_state_dict(ckpt['state_dict'])
            best_acc = ckpt.get('best_acc', 0.0)
            print(f"[SiameseClassifier] ✅ 파인튜닝 가중치 로드 "
                  f"(최고 훈련 정확도: {best_acc:.1f}%)")
            print(f"                   경로: {_FINETUNED_PATH}")

            # ── FC 분류 헤드 복원 (새 체크포인트에만 존재) ──────────
            # fc_state 키가 있으면 Linear(512, N)을 복원해 직접 분류에 사용.
            # 코사인 유사도는 앵커 이미지가 전처리 도메인과 다를 때 혼동이 발생하지만,
            # FC 헤드는 학습 때 이미 분류 경계를 학습했으므로 훨씬 정확하다.
            fc_state = ckpt.get('fc_state', None)
            self.class_files = ckpt.get('class_files', [])
            self.num_classes = ckpt.get('num_classes', 0)
            if fc_state and self.num_classes > 0:
                self.fc_head = nn.Linear(512, self.num_classes)
                self.fc_head.load_state_dict(fc_state)
                self.fc_head.eval()
                self.fc_head.to(self.device)
                print(f"[SiameseClassifier] ✅ FC 분류 헤드 복원 완료 "
                      f"({self.num_classes}클래스 직접 분류 모드 활성)")
                print(f"                   클래스 순서: {self.class_files}")
            else:
                print("[SiameseClassifier] ⚠️  fc_state 없음 → 코사인 유사도 폴백 모드")
                print("                   (scripts/train_siamese.py 를 다시 실행하면 개선됩니다)")
        else:
            print("[SiameseClassifier] ⚠️  파인튜닝 가중치 없음 → ImageNet 사전학습 사용")
            print("                   (scripts/train_siamese.py 를 실행하면 정확도가 높아집니다)")
        # ────────────────────────────────────────────────────────────

        self.model.eval()
        self.model.to(self.device)

        # 2. 이미지 스탠다드 포맷(전처리)
        self.transform = transforms.Compose([
            transforms.Resize((224, 224)),
            transforms.ToTensor(),
            transforms.Normalize(mean=[0.485, 0.456, 0.406], # 조명, 화이트밸런스 불균형을 잠재우는 마법의 숫자
                                 std=[0.229, 0.224, 0.225])
        ])

        self.anchor_dir = anchor_dir
        self.anchor_features = self._load_and_embed_anchors()

        # [Why-Monster 방어: 폭포수 판별 아키텍처 커트라인]
        # "왜 사람 손이 거의 안 가는가?" 에 대한 설계적 증명
        self.auto_label_threshold_high = 0.90  # 90점 이상: 묻고 더블로 가 (완벽한 정답이므로 사람 몰래 라벨 부착, 유지보수 0원)
        self.need_vlm_threshold_low = 0.50     # 50~90점: 샴(Siamese)의 머리론 좀 헷갈림. 더 똑똑한 LLM 판사에게 넘김

        # [핵심 임계값] 코사인 유사도 기반 합격 기준 (0.0 ~ 1.0)
        # FC Softmax는 과잉확신 경향이 심해 타겟 구분 불가 문제 발생.
        # 따라서 실제 합격 판정은 "DNA 거리(코사인 유사도)"로 수행.
        # 0.75 = 75% 일치 이상이면 합격. 카메라 환경에 따라 조절 가능.
        self.cosine_threshold = 0.75

    def _load_and_embed_anchors(self):
        """
        서버가 켜질 때, 폴더에 들어박혀있는 진짜 정답 타겟 1~5장(Few-Shot 원본)의 DNA를 미리 다 뽑아 둡니다.
        """
        features = {}
        if not os.path.exists(self.anchor_dir):
            return features
            
        for file_name in os.listdir(self.anchor_dir):
            path = os.path.join(self.anchor_dir, file_name)
            if path.endswith(tuple(['.jpg', '.png', '.jpeg'])):
                img = Image.open(path).convert("RGB")
                vec = self.get_embedding(img)
                features[file_name] = vec
        return features

    def get_embedding(self, pil_image):
        """사진을 받아 512자리 DNA 숫자 배열(바코드)로 추출합니다."""
        tensor = self.transform(pil_image).unsqueeze(0).to(self.device)
        with torch.no_grad():
            feature = self.model(tensor)
            
        # 얼굴인식(FaceID) 할 때 처럼 코사인 유사도 연산을 위해 크기를 1로 눌러(Normalize) 줍니다.
        feature = torch.nn.functional.normalize(feature, p=2, dim=1)
        return feature

    def classify_frame(self, pil_image):
        """
        PIL 이미지를 받아 타겟 클래스를 판별합니다.

        [판별 전략 — 코사인 유사도 우선 + FC 보조]

        FC(Softmax)는 타겟 간 유사도가 높을 때 특정 클래스에 확률을 과도하게
        몰아주는 '과잉 확신(Overconfidence)' 경향이 있습니다.
        예) 타겟1:0.88, 타겟2:0.84 → Softmax 후 타겟1:92%, 타겟2:6%

        때문에 실제 합격/불합격 판정은 '코사인 유사도(DNA 거리 측정)'를
        최우선 기준으로 삼습니다. FC 헤드는 타겟 이름 추정에만 보조 사용.

        [흐름]
          1. 코사인 스코어 → 어떤 타겟이 가장 가까운지 (이름 결정)
          2. FC 헤드 있으면 → neg 클래스 우세 여부만 교차 확인
          3. 합격 기준: 최고 코사인 유사도 >= cosine_threshold (기본 0.75)
        """
        import torch.nn.functional as F

        if not self.anchor_features:
            return None, 0.0, False

        tensor = self.transform(pil_image).unsqueeze(0).to(self.device)
        with torch.no_grad():
            raw_feature = self.model(tensor)   # (1, 512) 정규화 전 원본 벡터

        emb = F.normalize(raw_feature, p=2, dim=1)  # (1, 512) 코사인 전용 정규화 벡터

        # ── Step 1: 모든 앵커와 코사인 유사도 계산 ──────────────────
        # 코사인 유사도 = 두 벡터 내적 (normalize 후이므로 범위 -1~1, 실제론 0~1)
        cosine_scores = {
            anchor_name: torch.sum(emb * anchor_vec).item()
            for anchor_name, anchor_vec in self.anchor_features.items()
        }

        # 코사인 기준으로 가장 가까운 앵커 선정 (타겟 이름 결정)
        best_cosine_name = max(cosine_scores, key=cosine_scores.get)
        best_cosine_sim  = cosine_scores[best_cosine_name]

        # ── Step 2: FC neg 교차 확인 [현재 비활성] ─────────────────────
        # [진단 결과 2026-04-06] neg 훈련 데이터가 타겟 3·4를 잠식:
        #   앵커 3.png → neg 67.8% 1위 / 앵커 4.png → neg 79.6% 1위
        # → 정상 타겟 화면도 불합격 처리되는 치명적 버그 확인.
        # neg 데이터를 정리하고 재학습할 때까지 FC neg 체크는 건너뜁니다.
        # (재활성화 방법: 아래 주석 해제 후 코사인 조건에 "and (not is_neg_dominant)" 추가)
        #
        # is_neg_dominant = False
        # if self.fc_head is not None and self.class_files and "neg" in self.class_files:
        #     with torch.no_grad():
        #         logits = self.fc_head(raw_feature)
        #         probs  = F.softmax(logits, dim=1)[0]
        #     overall_best_idx = int(probs.argmax().item())
        #     is_neg_dominant  = (self.class_files[overall_best_idx] == "neg")

        # ── Step 3: 합격 판정 (순수 코사인 유사도 단독 기준) ──────────
        # 코사인 유사도 >= cosine_threshold(기본 0.75) 이면 합격.
        # neg FC 안전장치 재활성 시 위 주석 해제 + 아래 조건에 추가할 것.
        confidence = best_cosine_sim * 100.0  # 0~100% 로 정규화해 표시
        is_ok      = (best_cosine_sim >= self.cosine_threshold)

        return best_cosine_name, round(confidence, 2), is_ok

    def classify_image(self, pending_img_path):
        """
        에러가 나서 `/pending/` 폴더에 굴러다니던 불쌍한 사진을 꺼내어 정밀 타격 채점을 시작합니다.
        반환: (제일 비슷한 원본 사진 이름, DNA 일치율 %, 다음 단계 파이프라인 지시서)
        """
        if not self.anchor_features:
            return None, 0.0, "NO_ANCHOR_FOUND"

        try:
            img = Image.open(pending_img_path).convert("RGB")
        except:
            return None, 0.0, "INVALID_IMAGE"

        # classify_frame 으로 통합 (FC 우선, 코사인 폴백)
        best_name, confidence, _ = self.classify_frame(img)

        # 3단 파이프라인 상태 판정 (신뢰도 기준)
        if confidence >= self.auto_label_threshold_high * 100:
            status = "AUTO_LABELED"
        elif confidence >= self.need_vlm_threshold_low * 100:
            status = "NEED_LLM_JUDGE"
        else:
            status = "REJECTED_MANUAL_REVIEW"

        return best_name, round(confidence, 2), status
