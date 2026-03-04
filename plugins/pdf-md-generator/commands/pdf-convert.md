Orchestrate PDF to Markdown conversion from user request in `$ARGUMENTS`.

## Configuration Options

- `ARGUMENTS`: 사용자 요청 원문. PDF 경로, 변환 옵션, 출력 경로를 포함한다.
- `input_dir` (required): PDF 파일이 있는 폴더 경로.
- `only` (optional): 특정 PDF 파일명만 변환 (복수 지정 가능).
- `overwrite` (optional): 기존 .md 파일 덮어쓰기 여부. 기본값 `false`.
- `backend` (optional): MinerU 백엔드 선택 (`pipeline`|`vlm`|`hybrid-auto-engine`). 기본값 `hybrid-auto-engine`.
- `language` (optional): OCR 언어. 기본값 `en`.

## Phase 1: 요구사항 파악

1. Parse `$ARGUMENTS` and extract conversion parameters.
   - PDF 폴더 경로 확인 (필수).
   - 특정 파일 지정 여부 확인 (`--only`).
   - 덮어쓰기 옵션 확인.
2. Validate input directory exists and contains PDF files.
   - 폴더가 없거나 PDF가 없으면 사용자에게 안내 후 중단.

## Phase 2: 변환 실행 (delegate to pdf-converter via Task tool)

1. Use Task tool with subagent_type="pdf-md-generator::pdf-converter"
   - Prompt: "Convert PDFs in `{input_dir}` to Markdown. Options: {parsed_options}. Report results including success/failure/skip counts and output file paths."
   - Expected output: 변환 결과 요약, 생성된 파일 경로 목록, 에러 정보.

## Phase 3: 결과 전달

1. Return final delivery summary.
   - 변환 성공/실패/스킵 건수.
   - 생성된 .md 파일 경로 목록.
   - images/ 폴더 경로 (이미지가 추출된 경우).
   - 실패한 파일이 있으면 에러 원인 안내.
2. Provide next actions.
   - `--overwrite` 옵션으로 재변환 방법 안내 (스킵된 파일이 있을 경우).
   - 추가 PDF 변환 방법 안내.

## MUST DO

- [ ] `$ARGUMENTS`에서 PDF 경로를 정확히 파악한다.
- [ ] Task tool 기반 위임으로만 변환을 수행한다.
- [ ] 변환 결과(성공/실패/스킵)를 사용자에게 명확히 보고한다.

## MUST NOT DO

- [ ] 오케스트레이터가 직접 Python 스크립트를 실행하지 않는다.
- [ ] 스크립트를 찾지 못했을 때 자체 코드를 작성하지 않는다.
- [ ] 사용자 요청 없이 `--overwrite` 옵션을 사용하지 않는다.

## Usage Example

```
@pdf-md-generator PDF를 마크다운으로 변환해줘.

ARGUMENTS:
- input_dir: ./papers/
- only: "paper1.pdf"
- overwrite: true
```
