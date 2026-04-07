"""
diagnose_siamese.py — 샴 네트워크 임베딩 품질 진단
==================================================
실행:  py scripts/diagnose_siamese.py

출력:
  1. 앵커 자기 유사도 행렬 (4×4) — 대각선이 최대여야 정상
  2. 앵커 간 최소/최대/평균 유사도 — 값이 너무 가까우면 구분 불가
  3. 판정: 학습 문제인지, 도메인 갭 문제인지 진단
"""
import os, sys, torch, torch.nn.functional as F
from PIL import Image

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)

ANCHOR_DIR     = os.path.join(_ROOT, "data", "targets")
FINETUNED_PATH = os.path.join(_ROOT, "models", "siamese_finetuned.pt")

print("=" * 60)
print(" 샴 네트워크 임베딩 품질 진단")
print("=" * 60)

# ── 모델 로드 ──────────────────────────────────────────────────
from offline.siamese_classifier import SiameseClassifier
clf = SiameseClassifier(anchor_dir=ANCHOR_DIR)

print(f"\n[앵커 폴더] {ANCHOR_DIR}")
print(f"[파인튜닝] {'있음 ✅' if os.path.isfile(FINETUNED_PATH) else '없음 ⚠️ (ImageNet만)'}")
print(f"[디바이스] {clf.device}")
print()

# ── 앵커 임베딩 로드 ───────────────────────────────────────────
anchors = clf.anchor_features
if not anchors:
    print("❌ 앵커 없음. data/targets/ 에 이미지를 확인하세요.")
    sys.exit(1)

names = sorted(anchors.keys())
embs  = [anchors[n] for n in names]

print(f"앵커 목록 ({len(names)}개): {names}")
print()

# ── 4×4 코사인 유사도 행렬 ─────────────────────────────────────
print("━" * 60)
print(" 📊 앵커 간 코사인 유사도 행렬 (대각선 = 자기 자신)")
print("━" * 60)

header = f"{'':>12}" + "".join(f"{n:>10}" for n in names)
print(header)
print("-" * len(header))

matrix = []
for i, (n_q, e_q) in enumerate(zip(names, embs)):
    row = []
    line = f"{n_q:>12}"
    for j, (n_r, e_r) in enumerate(zip(names, embs)):
        sim = torch.sum(e_q * e_r).item()
        row.append(sim)
        mark = " ◀" if i == j else ""
        line += f"  {sim:.4f}{mark if i==j else '  '}"
    matrix.append(row)
    print(line)

print()

# ── 진단 지표 계산 ─────────────────────────────────────────────
diag_sims    = [matrix[i][i] for i in range(len(names))]       # 자기 자신
off_sims     = [matrix[i][j] for i in range(len(names))
                              for j in range(len(names)) if i != j]

print("━" * 60)
print(" 📈 진단 지표")
print("━" * 60)
print(f"  자기 유사도 (대각선)  : 최소 {min(diag_sims):.4f} / 평균 {sum(diag_sims)/len(diag_sims):.4f} / 최대 {max(diag_sims):.4f}")
print(f"  타겟 간 유사도 (비대각): 최소 {min(off_sims):.4f} / 평균 {sum(off_sims)/len(off_sims):.4f} / 최대 {max(off_sims):.4f}")
gap = min(diag_sims) - max(off_sims)
print(f"  마진 (자기-최대혼동)  : {gap:+.4f}  {'✅ 충분' if gap > 0 else '❌ 역전 — 혼동 발생 중!'}")
print()

# ── 자기 매칭 테스트 ───────────────────────────────────────────
print("━" * 60)
print(" 🔍 자기 매칭 테스트 (각 앵커의 최고 유사도 타겟은?)")
print("━" * 60)
all_ok = True
for i, (n_q, e_q) in enumerate(zip(names, embs)):
    scores = {n_r: torch.sum(e_q * e_r).item() for n_r, e_r in zip(names, embs)}
    best   = max(scores, key=scores.get)
    ok     = best == n_q
    all_ok = all_ok and ok
    icon   = "✅" if ok else "❌ 혼동!"
    confused = [f"{n}:{s:.3f}" for n, s in sorted(scores.items(), key=lambda x: -x[1])]
    print(f"  {n_q} → 최고:{best} ({scores[best]:.4f}) {icon}")
    print(f"        전체 순위: {' > '.join(confused)}")
print()

# ── 실제 타겟 이미지로 테스트 (앵커≠타겟 이미지인 경우) ─────────
print("━" * 60)
print(" 🎯 실제 타겟 이미지 폴더와 교차 테스트")
print("━" * 60)
target_files = sorted([f for f in os.listdir(ANCHOR_DIR)
                       if f.lower().endswith(('.png', '.jpg', '.jpeg'))])
if not target_files:
    print("  타겟 이미지 없음 — 건너뜀")
else:
    for tfile in target_files:
        img_path = os.path.join(ANCHOR_DIR, tfile)
        img = Image.open(img_path).convert("RGB")
        emb = clf.get_embedding(img)
        scores = {n: torch.sum(emb * e).item() for n, e in clf.anchor_features.items()}
        best = max(scores, key=scores.get)
        sim  = scores[best]
        ok   = os.path.splitext(best)[0] == os.path.splitext(tfile)[0]
        icon = "✅" if ok else "❌ 혼동!"
        print(f"  타겟 {tfile} → 매칭 앵커: {best} ({sim:.4f}) {icon}")

print()

# ── 종합 진단 ──────────────────────────────────────────────────
print("━" * 60)
print(" 📋 종합 진단")
print("━" * 60)

if gap > 0.10:
    diagnosis = "✅ 임베딩 품질 양호. 문제는 도메인 갭(카메라 노이즈/각도)일 가능성 높음."
    suggest   = "→ YOLO 크롭이 제대로 되고 있는지 확인하세요. (YOLO OFF면 ON으로 변경)"
elif gap > 0:
    diagnosis = "⚠️  임베딩 마진이 작음. 카메라 환경에서 혼동 발생 가능."
    suggest   = "→ AUG_PER_IMG=500, EPOCHS_UNFROZEN=25로 늘려 재학습을 권장합니다."
else:
    diagnosis = "❌ 임베딩이 타겟을 구분 못 함. 앵커끼리 유사도가 너무 가깝습니다."
    suggest   = "→ (A) 재학습 강도 올리기  또는\n   (B) 분류기 헤드(argmax) 방식으로 전환 권장"

print(f"\n  {diagnosis}")
print(f"  {suggest}\n")
print("=" * 60)
