---
name: powder-characterization
description: "배터리 전극 파우더 물성 분석 실험 설계를 위한 ASTM 표준, DEM 시뮬레이션, Beverloo 방정식, Jenike 설계 등 핵심 도메인 지식. Use when designing powder characterization experiments or interpreting powder flow test results."
---

# Powder Characterization

배터리 전극 파우더(NMC811 등)의 물성 분석 실험 설계에 필요한 핵심 도메인 지식을 제공한다.

## When to Use This Skill

- 배터리 전극 파우더의 유동성, 밀도, 전단 특성을 측정할 때
- ASTM 표준 기반 실험 프로토콜을 작성할 때
- DEM 시뮬레이션 파라미터를 캘리브레이션할 때
- 호퍼/깔때기 장치를 설계할 때
- Jenike 전단 셀 하중 계산 또는 YL 구성이 필요할 때
- custom(간이) 장비 vs standard(ASTM 규격) 장비 비교가 필요할 때

## Equipment Mode

| 모드 | 설명 |
|------|------|
| **standard** | ASTM 규격 상용 장비 (기본값) |
| **custom** | 자체 제작 간이 장치 → `references/custom-apparatus.md` 참조 |

## Core ASTM Standards (8종)

### 밀도 측정

| 표준 | 장비 | 오리피스 | 적용 입도 |
|------|------|----------|----------|
| **B329** | Scott Volumeter | N/A | 전 범위 (미세분말 ◎) |
| **B417** | Carney Funnel | 5.08 mm | D50 > 3 μm |

### 유동성 측정

| 표준 | 장비 | 오리피스 | 적용 입도 |
|------|------|----------|----------|
| **B213** | Hall Funnel | 2.54 mm, 60° | D50 > 50 μm (미세분말은 No Flow) |
| **B964** | Carney Funnel | 5.08 mm | B213 대체 (유동 불량 분말) |

### 전단/동적 측정

| 표준 | 장비 | 측정값 |
|------|------|--------|
| **D6128** | Jenike Shear Tester | 항복궤적, fc, σ₁, ff, φ_w |
| **D7891** | FT4 Shear Cell | 자동화 전단 (D6128 교차검증) |
| **D8327** | FT4 Permeability | 공기 투과도 |
| **D8328** | FT4 Dynamic | BFE, SE, SI, FRI, CI |

## 파라미터 3-분류 체계

모든 물성 파라미터를 **측정 → 유도 → DEM 입력**으로 분류한다.

| 분류 | 기호 | 파라미터 예시 | 취득 방법 |
|------|------|-------------|-----------|
| 🔴 직접 측정 | ρ_b, ρ_tap, t_50, AoR, m(t), (σ,τ), (σ_w,τ_w) | 겉보기밀도, 탭밀도, 유동 시간, 안식각, 호퍼 유량, 전단/벽면 데이터 | ASTM 장비로 직접 측정 |
| 🟣 유도 계산 | HR, CI, ff, φ_i, φ_w, c, f_c, W, C, k | Hausner Ratio, Carr Index, 내부/벽면 마찰각, 점착력, Beverloo 계수 | 측정 데이터에서 수식으로 산출 |
| 🔵 DEM 입력 | μ_pp, μ_pw, μ_r, e, E*, γ_JKR | 입자-입자/벽 마찰, 구름마찰, 반발계수, 유효 영률, 표면에너지 | 역해석 또는 문헌값 |

**핵심 매핑:**
- μ_pp = tan(φ_i) ← Jenike 전단(D6128 §8)
- μ_pw = tan(φ_w) ← 벽면 마찰(D6128 §7.6)
- μ_r ← 안식각(AoR) 역해석
- γ_JKR ← 점착력(c) 역해석

상세 매핑표: `references/dem-parameters.md`

---

## Jenike 전단 셀 정밀 치수 (D6128)

### Fig.4 치수

| 부품 | 치수 | 비고 |
|------|------|------|
| Base 높이 | 12.7 mm | 0.5 in |
| Ring 높이 | 15.875 mm | 0.625 in |
| Mold ring 높이 | 9.525 mm | 0.375 in |
| 셀 내경 | 95.25 mm | 3.75 in |
| 벽 두께 | ≥ 3 mm | 강성 확보 |
| Lid 직경 | 93.3 mm | 셀 ID - ~2mm |
| **셀 단면적 A** | **7,126 mm²** | π/4 × 95.25² |

### 전단 조건

- 전단 속도: **2.72 mm/min** (허용 2.54–3.00)
- 전단면: Ring↔Base 경계 (분말↔분말)
- 힘 전달: ①로드셀 ← ②스템 ← ③브래킷 ← ④핀 ← Ring
- 수식: **σ = W/A**, **τ = F/A**

### 벽면 마찰 vs 전단 시험 차이

| 항목 | 전단 (§8) | 벽면 마찰 (§7.6) |
|------|-----------|-----------------|
| 바닥면 | Ring + Base | 쿠폰 (벽면 재질) |
| 전단면 | 분말↔분말 | 분말↔쿠폰 |
| 측정값 | φ_i, c, f_c, ff | φ_w |
| 하중 | σ 고정 (포인트별) | σ_w 감소 (6→1) |

### 하중 계산 (Annex A2)

```
W = m_lid × g + m_W × g     [N]
σ = W / A                    [Pa]
A = 7,126 mm² = 7.126 × 10⁻³ m²
```

### YL(Yield Locus) 7단계 구성법

1. σ_pre(예비 전단 응력) 선택
2. Pre-shear: σ_pre 하에서 정상상태(τ 일정)까지 전단
3. Shear: σ₁ ~ σ₅ (σ < σ_pre) 각각에서 최대 τ 측정
4. (σ, τ) 점들을 직선 피팅 → YL(Yield Locus)
5. YL에 접하는 대·소 Mohr 원 작도
6. 소 Mohr 원 → f_c(일축압축강도), 대 Mohr 원 → σ₁(주응력)
7. ff = σ₁ / f_c (Flow Function)

**Mohr-Coulomb:**
```
τ = c + σ · tan(φ_i)
f_c = 2c · cos(φ_i) / (1 - sin(φ_i))
```

---

## 아칭(Arching) 개념

호퍼 오리피스 직경 D가 임계값 D_c보다 작으면, 분말이 자체 지지 아치(arch/bridge)를 형성하여 유동이 차단된다.

- D < D_c → 아칭 발생 (유동 불가)
- D ≥ D_c → 정상 유동
- D_c는 Beverloo 실험으로 결정

---

## Key Equations

### Beverloo 방정식 (오리피스 유량)

```
W = C · ρ_b · √g · (D_o - k · d_p)^2.5
```

**주의: C(대문자) vs c(소문자) 구분 필수**

| 기호 | 의미 | 일반값 | 혼동 주의 |
|------|------|--------|-----------|
| W | 질량 유량 (kg/s) | 측정값 | |
| **C** | **방출 계수** | **0.55–0.65** | **대문자 C ≠ 점착력 c** |
| ρ_b | 벌크 밀도 (kg/m³) | 측정값 | |
| g | 중력가속도 | 9.81 m/s² | |
| D_o | 오리피스 직경 (m) | 변수 | |
| k | 형상 계수 | 1.0–2.0 (구형 ≈ 1.4) | |
| d_p | 입자 직경 (m) | D50 | |
| **c** | **점착력 (cohesion)** | **0.5–3.0 kPa** | **소문자 c ≠ 방출 계수 C** |

### Flow Function

```
ff = σ₁ / fc
```

| ff 값 | 유동성 분류 |
|--------|-----------|
| < 1 | 유동 불가 |
| 1–2 | 매우 점착성 |
| 2–4 | 점착성 |
| 4–10 | 자유 유동 |
| > 10 | 우수 유동 |

### Hausner Ratio & Carr Index

```
HR = ρ_tap / ρ_apparent
CI = (1 - ρ_apparent / ρ_tap) × 100
```

| 유동성 | HR | CI (%) |
|--------|-----|--------|
| 우수 | 1.00–1.11 | 1–10 |
| 양호 | 1.12–1.18 | 11–15 |
| 불량 | 1.26–1.34 | 21–25 |
| 극히불량 | > 1.46 | > 32 |

## DEM Contact Model: Hertz-Mindlin + JKR

### 필수 파라미터

| 파라미터 | 기호 | 측정 방법 |
|----------|------|-----------|
| 입자-입자 정마찰계수 | μ_s,pp | 안식각 역해석 |
| 입자-벽 정마찰계수 | μ_s,pw | 벽면 마찰 시험 |
| 입자-입자 구름마찰계수 | μ_r,pp | 안식각 역해석 |
| 반발 계수 | e | 낙하 시험/문헌값 |
| 영률 | E | 나노인덴테이션/문헌값 |
| 포아송비 | ν | 문헌값 (≈ 0.3) |
| JKR 표면에너지 | γ | 역해석 |

### 역해석 워크플로우

```
1. Latin Hypercube Sampling으로 파라미터 공간 탐색
2. 각 샘플에 대해 DEM 시뮬레이션 실행
3. Response Surface Method로 대리 모델 구축
4. 최적화 (실험값 - 시뮬레이션 오차 최소화)
5. 검증: 캘리브레이션에 사용하지 않은 실험으로 확인
```

## NMC811 Specific Data

| 항목 | 단결정 (SC) | 다결정 (PC) |
|------|------------|------------|
| 이론 밀도 | 4.78 g/cm³ | 4.78 g/cm³ |
| 탭 밀도 | 2.5–2.9 | 2.2–2.6 |
| 형상 | Faceted 구형 | 이차 구형 (다공) |
| D50 | 3–5 μm | 10–15 μm |
| Hall Funnel | No Flow (예상) | 가능/불량 |

### NMC811 SC 예상 물성 범위

| 파라미터 | 예상 범위 | 구분 |
|----------|-----------|------|
| HR | 1.25–1.60 | [가정] |
| CI | 20–38% | [가정] |
| AoR | 40°–55° | [가정] |
| φ_i | 30°–45° | [가정] |
| φ_w (STS304) | 15°–30° | [가정] |
| c (cohesion) | 0.5–3.0 kPa | [가정] |
| ff | 1–4 (점착성) | [가정] |

### 안전 정보

- MSDS: 산화성 고체, 니켈 피부 감작
- PPE: 니트릴 장갑, 보안경, N95 마스크
- 정전기: 접지 용기, 습도 45%+ RH
- 환경: 23 ± 2°C, 50 ± 5% RH

상세: `references/custom-apparatus.md` NMC811 섹션

## 5-Step Experiment Workflow

```
Step 1: 기본 물성 (밀도, 입도, 형상)
   ↓
Step 2: 유동성 스크리닝 (Hall/Carney/FT4)
   ↓
Step 3: 임계 오리피스 직경 (다공 호퍼 + Beverloo)
   ↓
Step 4: 전단 시험 (Jenike/FT4 → Flow Function)
   ↓
Step 5: DEM 캘리브레이션 (역해석 → 최적 파라미터)
```

## Resources

- **references/astm-summary.md**: ASTM 8종 표준 요약 비교표 (D6128 정밀 치수, B213/B964 오리피스 공차 포함)
- **references/dem-parameters.md**: DEM 파라미터 문헌값 + 3-분류 체계 + NMC811 예상 범위
- **references/custom-apparatus.md**: Custom(간이) 장치 사양서 + NMC811 특화 정보 + 안전
- **assets/protocol-template.md**: 실험 프로토콜 출력 템플릿 (equipment_mode 지원)
