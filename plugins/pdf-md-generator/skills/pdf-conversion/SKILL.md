---
name: pdf-conversion
description: "PDF to Markdown conversion toolkit with PyMuPDF layout analysis, table detection, equation rendering, and MinerU fallback. Use when converting PDF documents to structured Markdown."
---

# PDF Conversion

PDF를 고품질 Markdown으로 변환하는 도구. ASTM 기술 표준 등 복잡한 레이아웃의 PDF를 정확하게 변환합니다.

## When to Use This Skill

- PDF 문서를 Markdown으로 변환할 때
- 2단 레이아웃, 표, 수식이 포함된 기술 문서를 처리할 때
- 배치 PDF 변환이 필요할 때

## Core Features

| 기능 | 설명 |
|------|------|
| 2단 레이아웃 감지 | 좌측→우측 컬럼 순서로 텍스트 추출 |
| 표 감지 | heading-anchored, whitespace-aligned 복수 전략 |
| 수식 렌더링 | 수학 폰트 영역을 250 DPI PNG로 캡처 |
| 심볼 목록 | LIST OF SYMBOLS를 Markdown 표로 변환 |
| 위첨자/각주 | 작은 폰트 숫자를 `<sup>` 태그로 변환 |
| 워터마크 제거 | 2페이지 이상 반복 이미지를 자동 감지/제거 |
| 이미지 추출 | `./images/{pdf-stem}/` 폴더로 이미지 저장 |

## Dependencies

**필수:**
- PyMuPDF (fitz): PDF 읽기, 텍스트/이미지 추출
- pdfplumber: 대체 표 감지

**선택:**
- MinerU CLI: 더 나은 OCR 및 구조 감지 (없으면 PyMuPDF로 폴백)

## 스크립트 참조 및 실행 (CRITICAL)

스크립트는 이 스킬의 상대경로에 위치합니다:

`scripts/pdf_to_md.py`

**실행 순서:**

**Step 1. 상대경로로 실행** (최우선)
```bash
TMP=D:/AI/temp TEMP=D:/AI/temp python scripts/pdf_to_md.py -i "{input_dir}" [--only "{filename}"] [--overwrite]
```

**Step 2. 상대경로 실패 시 Glob 폴백**
```
Glob: **/pdf-md-generator/skills/pdf-conversion/scripts/pdf_to_md.py
```

**Step 3. Glob도 실패 시 확장 탐색**
```
Glob: **/pdf_to_md.py
```

**절대 금지**: 스크립트를 찾지 못했을 때 자체적으로 Python 코드를 작성하지 마세요.
반드시 에러를 보고하고 사용자에게 경로 확인을 요청하세요.

## CLI Options

| 옵션 | 설명 | 기본값 |
|------|------|--------|
| `--input-dir`, `-i` | PDF 폴더 경로 (필수) | - |
| `--backend`, `-b` | MinerU 백엔드 | `hybrid-auto-engine` |
| `--language`, `-l` | OCR 언어 | `en` |
| `--overwrite` | 기존 .md 덮어쓰기 | `false` |
| `--only` | 특정 파일만 변환 (복수 가능) | - |

## Environment Setup (Windows)

```bash
TMP=D:/AI/temp TEMP=D:/AI/temp python scripts/pdf_to_md.py -i "{input_dir}"
```

Windows에서 임시 폴더 경로에 한글이 포함되면 오류가 발생할 수 있으므로 반드시 `TMP`, `TEMP` 환경변수를 설정합니다.

## Exit Codes

| Code | Meaning |
|------|---------|
| 0 | 성공 |
| 1 | 입력 디렉토리 없음 또는 PDF 없음 |
| 2 | 하나 이상의 PDF 변환 실패 |
| 3 | 하나 이상의 PDF 스킵 (이미 존재) |

## Output Structure

변환 결과는 입력 PDF와 같은 폴더에 생성됩니다:

```
{input_dir}/
├── document.pdf          # 원본
├── document.md           # 변환 결과
└── images/
    └── document/         # 추출된 이미지
        ├── img_001.png
        └── ...
```
