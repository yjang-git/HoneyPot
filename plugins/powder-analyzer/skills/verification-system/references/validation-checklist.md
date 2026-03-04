# 검증 체크리스트 (6개 카테고리)

## 1. 단위 일관성 (Unit Consistency)

- [ ] 모든 길이: m (또는 μm, mm 명시)
- [ ] 모든 질량: kg (또는 g 명시)
- [ ] 모든 시간: s
- [ ] 밀도: g/cm³ 또는 kg/m³ (혼용 금지)
- [ ] 응력: Pa 또는 kPa (혼용 금지)
- [ ] 에너지: J 또는 mJ (혼용 금지)
- [ ] 방정식 양변 차원 일치
- [ ] 단위 변환 과정 명시

## 2. 물리적 타당성 (Physical Plausibility)

### 밀도
- [ ] 겉보기 밀도 < 탭 밀도 < 이론 밀도
- [ ] Hausner Ratio: 1.0–2.0 범위
- [ ] Carr Index: 0–60% 범위

### 마찰 계수
- [ ] μ_s: 0–1.0 범위 (일반적)
- [ ] μ_r: 0–0.5 범위
- [ ] μ_pw < μ_pp (일반적 경향, 필수는 아님)
- [ ] PTFE μ_pw < SUS304 μ_pw

### 유동
- [ ] Beverloo C: 0.4–0.7 범위
- [ ] Beverloo k: 0.5–3.0 범위
- [ ] 유량 > 0 (유동 시)
- [ ] 임계 직경 > 입자 직경의 5배 이상

### 전단
- [ ] fc ≥ 0
- [ ] σ₁ > fc (정의에 의해)
- [ ] φ_i: 20–50° 범위 (분말 일반)
- [ ] φ_w < φ_i (벽면 마찰 < 내부 마찰)

### DEM
- [ ] 반발 계수 e: 0–1 범위
- [ ] JKR γ > 0
- [ ] 시간 스텝 < Rayleigh 시간의 40%

## 3. ASTM 표준 준수 (Standard Compliance)

### B213 (Hall Flow)
- [ ] 오리피스 직경 2.54 ± 0.02 mm
- [ ] 시료량 50.0 ± 0.1 g
- [ ] 건조 시료 사용
- [ ] 반복 3회 이상

### B329 (Scott Density)
- [ ] Scott Volumeter 사용
- [ ] 25 cm³ ± 0.05 cm³ 컵
- [ ] 과충전 후 스크레이핑

### D6128 (Jenike Shear)
- [ ] 예비 전단: 정상 상태 확인
- [ ] 전단: 3+ 수직 응력 수준
- [ ] 항복 궤적 선형 피팅 R² > 0.95
- [ ] Mohr 원 접선 조건 확인

### D8328 (FT4 Dynamic)
- [ ] 컨디셔닝 사이클 수행
- [ ] Test 1–7: BFE 측정 (Test 4–7 안정성)
- [ ] SI = BFE_7 / BFE_4 (± 10% → 안정)

## 4. 수학적 정확성 (Mathematical Accuracy)

- [ ] Beverloo 피팅: R² > 0.95
- [ ] Mohr 원: σ₁, fc 독립 재계산
- [ ] Flow Function: ff = σ₁/fc 계산 확인
- [ ] Hausner Ratio 계산 확인
- [ ] Carr Index 계산 확인
- [ ] 평균/표준편차/CV% 계산 확인
- [ ] 95% 신뢰구간 계산 확인

## 5. 교차 일관성 (Cross-Consistency)

- [ ] Hausner Ratio ↔ Hall Funnel 결과 일관
  - HR > 1.35 → Hall No Flow 예상
- [ ] FT4 BFE ↔ Jenike ff 일관
  - 높은 BFE → 낮은 ff (점착성)
- [ ] Scott 밀도 ↔ DEM 벌크 밀도 일관 (±15%)
- [ ] 벽면 마찰각 ↔ DEM μ_pw 일관
- [ ] Beverloo 임계 직경 ↔ DEM 임계 직경 일관 (±20%)

## 6. 문헌 비교 (Literature Comparison)

### NMC811 참조값

| 항목 | 문헌 범위 | FLAG 기준 |
|------|----------|-----------|
| 탭 밀도 (SC) | 2.5–2.9 g/cm³ | ±50% |
| BFE | 측정 필요 | 유사 물질 대비 |
| Jenike fc | 측정 필요 | 유사 물질 대비 |

### 일반 미세 분말 참조값

| 항목 | 문헌 범위 | FLAG 기준 (10배 차이) |
|------|----------|---------------------|
| μ_s,pp | 0.1–0.5 | < 0.01 또는 > 5.0 |
| JKR γ | 0.01–0.5 J/m² | < 0.001 또는 > 5.0 |
| Beverloo C | 0.45–0.65 | < 0.04 또는 > 6.5 |
