---
name: verification-system
description: "3-AI 검증 시스템(Executor-Validator-Replicator) 및 Consensus Gate 알고리즘. 실험 설계의 과학적 정합성, 재현성, 환각 방지를 위한 검증 프레임워크. Use when verifying experiment protocols or ensuring reproducibility of scientific analyses."
---

# 3-AI Verification System

실험 설계·실행·결과 해석의 각 단계에서 환각(Hallucination) 방지 및 재현성 확보를 위한 3중 검증 시스템.

## When to Use This Skill

- 실험 프로토콜의 과학적 정합성을 검증할 때
- DEM 파라미터 캘리브레이션 결과를 교차 검증할 때
- AI가 생성한 수치/분석 결과의 환각 여부를 판별할 때
- 독립적 재현을 통해 결과 신뢰성을 확보할 때

## Architecture

```
┌─────────────────────────────────────────────┐
│           3-AI Verification System           │
│                                             │
│  ┌─────────┐  ┌───────────┐  ┌───────────┐ │
│  │Executor │─▶│ Validator │─▶│Replicator │ │
│  │(실행자)  │  │ (검증자)   │  │ (재현자)   │ │
│  └─────────┘  └───────────┘  └───────────┘ │
│       │             │              │        │
│       ▼             ▼              ▼        │
│   결과 생성    교차 검증      독립 재현      │
│                                             │
│       ◄── Consensus Gate (합의 관문) ──►    │
│       3자 합의 시에만 다음 단계로 진행        │
└─────────────────────────────────────────────┘
```

## Role Definitions

### Executor (실행자)
- 실험 계획 수립, 프로토콜 생성, 데이터 분석 수행
- 모든 수치에 출처 명시 (ASTM 번호, 논문 DOI)
- 가정과 사실을 `[가정]`/`[사실]` 태그로 구분

### Validator (검증자)
- 6개 카테고리 검증 수행
- PASS / FLAG / FAIL 판정
- 수정 권고사항 제시

### Replicator (재현자)
- Executor 출력을 보지 않고 독립 수행
- 정량적 일치도 비교
- 불일치 원인 분석

## Validation Categories (6개)

| # | 카테고리 | 판정 기준 | FAIL 조건 |
|---|----------|-----------|-----------|
| 1 | **단위 일관성** | SI 단위 일치, 차원 분석 | 불일치 |
| 2 | **물리적 타당성** | 문헌 범위 ±50% | 물리적 불가능 |
| 3 | **ASTM 표준 준수** | 표준 절차 대조 | 무단 편차 |
| 4 | **수학적 정확성** | 독립 재계산 | 계산 오류 |
| 5 | **교차 일관성** | 실험 간 모순 없음 | - (FLAG만) |
| 6 | **문헌 비교** | 유사 물질 문헌값 대조 | - (FLAG만) |

## Consensus Gate Algorithm

```
입력: R_E (Executor 결과), V (Validator 판정), R_R (Replicator 결과)

Step 1: Validator 검증
  IF V.verdict == REJECT → FAIL (사유 반환)

Step 2: Executor-Replicator 일치도
  agreement = compute_agreement(R_E, R_R, thresholds)
  IF agreement.match_ratio < 0.80 → FAIL (불일치 항목 반환)

Step 3: FLAG 처리
  IF V.verdict == REVIEW:
    IF agreement.match_ratio >= 0.95 → CONDITIONAL_PASS
    ELSE → FAIL

Step 4: 전부 통과 → PASS
```

## Agreement Thresholds

| 결과 유형 | 허용 오차 | 근거 |
|-----------|----------|------|
| 밀도 | ±5% | 실험 반복성 |
| 마찰 계수 | ±10% | DEM 민감도 |
| 유량 | ±15% | Beverloo 한계 |
| Flow Function | ±0.5 등급 | 정성 분류 |
| 호퍼 반각 | ±3° | 설계 안전율 |
| Beverloo C | ±0.05 | 피팅 불확실성 |
| Beverloo k | ±0.3 | 피팅 불확실성 |
| JKR γ | ±20% | 미세 입자 불확실성 |

## Step-by-Step Verification Matrix

| 실험 단계 | Executor 출력 | Validator 검증 | Replicator 재현 |
|-----------|-------------|----------------|----------------|
| Step 1 기본물성 | 밀도, HR, CI | 단위, 범위, 문헌 | 독립 계산 |
| Step 2 유동성 | Flow/No Flow, BFE/SE | ASTM 절차, FT4 정합 | 독립 해석 |
| Step 3 임계직경 | D_crit, Beverloo 계수 | 피팅 R², 방정식 검증 | 독립 피팅 |
| Step 4 전단 | fc, σ₁, ff, φ_w | Mohr 원, Jenike 차트 | 독립 Mohr |
| Step 5 DEM | μ_pp, μ_pw, γ, e | 범위, 수렴성 | 독립 역해석 |

## Failure Resolution Protocol

Consensus Gate에서 FAIL 판정 시:

```
FAIL 원인 분석
     │
     ├── Validator REJECT → 해당 카테고리 수정 후 재검증
     │
     ├── Low Match Ratio → 불일치 항목 분석
     │     ├── 해석 방법론 차이 → 표준 방법 합의 후 재실행
     │     ├── 데이터 해석 차이 → 원인 규명 후 수정
     │     └── 근본적 불일치 → 전문가 리뷰 권고
     │
     └── 최대 재시도: 2회 → 이후 사용자에게 에스컬레이션
```

## Resources

- **references/validation-checklist.md**: 6개 카테고리별 상세 검증 체크리스트
