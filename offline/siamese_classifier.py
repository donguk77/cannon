import os
import torch
import torch.nn as nn
import torchvision.models as models
import torchvision.transforms as transforms
from PIL import Image

class SiameseClassifier:
    """
    [Track B 오프라인 정밀 분석 파이프라인의 메인 두뇌]
    낮 실시간 레이스에서 '애매함' 판정을 받았던 에러 화면(Pending)들을 야간에 돌려,
    단 1~5장의 깨끗한 타겟 정답 사진(Anchor)과 "수학적/의미론적 DNA 거리"를 계산해 냅니다.
    이 과정을 통해 비전문가 작업자가 수백 장을 일일이 라벨링하지 않아도 
    기계가 스스로 정답 폴더로 사진을 분류수거(Self-Labeling)해버립니다.
    """
    def __init__(self, anchor_dir="../models/siamese_anchor"):
        # 산업용 똥컴(엣지 로컬 PC)에서도 돌아가야 하므로 GPU가 없으면 CPU 모드로 유연하게 돌아가게 설계
        self.device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
        
        # 1. 뼈대: 아주 가벼운 ResNet18을 베이스로 가져옵니다. (강력한 샴 네트워크의 근간)
        # 최신 모델(비전 LLM 등)은 공장 PC에서 안 돌아가므로 철저히 트레이드오프를 고려한 가성비 선택입니다.
        # [버그 수정] pretrained=True는 PyTorch >= 1.13에서 deprecated 됨. 최신 방식으로 교체.
        self.model = models.resnet18(weights=models.ResNet18_Weights.DEFAULT)
        # 단순히 멍멍이/소분류를 뱉는 마지막 대뇌 피질(fc 계층)을 뭉텅 잘라내고,
        # 사진을 보여주면 '512개의 DNA 숫자 배열(Embedding)'만 길게 뱉도록 개조해줍니다.
        self.model.fc = nn.Identity() 
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

        # 에러 화면의 DNA 
        target_vec = self.get_embedding(img)

        # 누가누가 더 정답에 가깝나(DNA 거리가 짧은가) 대결 구도
        best_match_name = None
        best_similarity = -1.0
        
        for anchor_name, anchor_vec in self.anchor_features.items():
            # 두 벡터의 코사인 내적 = 얼마나 똑같이 생겨먹었는지 점수 (1.0 만점)
            sim = torch.sum(target_vec * anchor_vec).item()
            if sim > best_similarity:
                best_similarity = sim
                best_match_name = anchor_name

        # [최종 재판 3단 로직 (아키텍처 문서 반영)]
        if best_similarity >= self.auto_label_threshold_high:
            status = "AUTO_LABELED"             # 사람 몰래 자동 라벨 달고 창고 (Labeled)행
        elif best_similarity >= self.need_vlm_threshold_low:
            status = "NEED_LLM_JUDGE"           # 헷갈리니까 LLM보고 한번 구제해달라고 토스
        else:
            status = "REJECTED_MANUAL_REVIEW"   # 완전 개박살나서 못알아봄. 사람이 확인하라고 알람 토스

        return best_match_name, round(best_similarity * 100, 2), status
