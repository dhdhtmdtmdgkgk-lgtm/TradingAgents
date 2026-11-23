# 로컬 웹 대시보드

기존 TradingAgents 분석 엔진을 브라우저에서 직접 실행하고 원문 보고서와
정상 완료 기록을 읽는 개인용 화면이다. 클라우드·별도 DB·사용자 계정·자동 주문은 없다.

## 설치 및 실행

저장소 루트에서 기존 가상환경을 활성화한 뒤:

```powershell
python -m pip install -e ".[web]"
python -m tradingagents.web.app
```

설치된 명령 `tradingagents-web`도 동일하다. PC에서 `http://127.0.0.1:8000`을 연다.
키는 기존 Git 제외 로컬 `.env` 또는 서버 환경변수에만 둔다. 서버는 실행한
폴더 기준으로 기존 dotenv 로더와 DEFAULT_CONFIG를 사용한다. 브라우저에는 키,
엔드포인트 URL, Memory 경로 등 전체 설정을 전달하지 않는다.

같은 네트워크의 스마트폰에서 접속하려면:

```powershell
python -m tradingagents.web.app --host 0.0.0.0 --port 8000
```

서버가 출력한 PC의 LAN 주소(예: `http://192.168.0.10:8000`)를 사용한다.
Windows 방화벽이 묻는 경우 신뢰하는 개인 네트워크에 필요한 접근만 허용한다.
서버는 방화벽·라우터·포트 포워딩을 변경하지 않는다. 외부 인터넷에 공개하지 않는다.
여러 네트워크 어댑터 때문에 IP가 자동으로 잡히지 않으면
`--allow-host 192.168.0.10`으로 실제 PC 주소를 추가한다. 기본 바인딩은 루프백이다.
계정 인증이 없는 개인용 서버이므로 LAN에 접속할 수 있는 사람이 보고서를 읽고
분석을 시작할 수 있다. 사용을 마치면 터미널에서 Ctrl+C로 종료한다.

## 화면과 실행

- 분석 데스크: TradingView 차트 / 에이전트 현황 / 입력·설정 / PM 최종 보고서의 2열 구성.
- 분석 기록: 최근 완료된 기록 자동 선택, 티커 검색, 실행별 UUID 목록, 원문 보고서 모달.
- 보고서는 서버에서 안전한 Markdown으로 렌더링한다. AI 재작성·요약·번역은 없다.
  HTML 태그는 실행하지 않고 외부 Markdown 이미지를 자동 요청하지 않는다.
  모달의 ‘Markdown 원문’으로 원래 텍스트도 확인할 수 있다.
- TradingView는 별도 origin의 iframe에 격리한다. 폼·로컬 API 접근 권한은 주지 않는다.
  티커만 연동하며 TradingView 시세의 지연·거래소·시간 범위는 분석 엔진과 다를 수 있다.
  위젯은 인터넷 연결이 필요하고 지원하지 않는 심볼은 표시되지 않을 수 있다.
- 출력 언어는 실행별 config에서 `Korean`으로 고정한다. 기존 기본 설정 파일은 바꾸지 않는다.
  과거 기록은 원래 언어 그대로 표시한다.
- Quick/Deep 모델은 기존 provider별 모델 목록과 현재 기본값을 사용한다.
  직접 모델 ID를 입력할 수도 있으며 모델 계정 접근 권한은 실제 공급자가 결정한다.
  provider·API 키·endpoint는 UI에서 변경할 수 없다.
- 연구·위험 라운드는 각각 1–10. 기본값은 DEFAULT_CONFIG를 따른다.
  Bull/Bear와 세 Risk 발언자의 누적 history 및 전체 토론을 모두 열람할 수 있다.

브라우저의 ‘분석 시작’을 누르기 전 API 비용 동의 체크가 필요하다. 시작을 누르기
전에는 모델을 생성하거나 분석을 실행하지 않는다. 기존 Memory 정산 기능도 유지되므로
미평가 과거 기록이 있으면 기존 Reflector 호출이 실행 비용에 포함될 수 있다.
브라우저 읽기·모달 열람·기록 조회는 LLM을 호출하지 않는다. 기존 사전 점검은 별도
`python -m tradingagents.dataflows.preflight` 명령을 그대로 사용할 수 있다.

## 선택 보유 정보

수량·평균 매수가·현금의 빈 값은 `None`, 직접 입력한 0은 숫자 0이다. 전부 비우면
기존 엔진에 `portfolio=None`을 전달한다. 일부만 제공하면 웹 전용 SingleHolding
어댑터가 입력한 값과 미제공 항목을 구분해 기존 `render/fingerprint/model_dump`
인터페이스로 전달한다. 예를 들어 현금만 입력해도 보유 수량을 0으로 가정하지 않는다.

알려진 수량의 렌더링은 기존 PortfolioContext/Position을 재사용한다. 다른 종목의
보유 정보는 미제공이며 전체 포트폴리오가 아니라는 문맥을 덧붙인다. 기록의 portfolio에
`scope=analyzed_instrument_only`와 누락된 수량 `null`을 보존한다. 원본 전체 포트폴리오
스키마와 CLI `--portfolio`는 변경하지 않았다. 계좌·보유 정보는 브라우저 영구 저장소에
자동 저장하지 않는다.

## 기존 엔진과 저장 연결

백그라운드 스레드 한 개가 기존 `checkpoint_scope`, `create_run_state`,
`checkpoint_input`, `stream_run`을 사용한다. 브라우저는 약 1초 간격으로 메모리 안의
상태를 읽으며 분석 실행과 분리된다. LangGraph 콜백은 실행 시작·실패를 표시하고,
스트림이 반환한 원문 보고서는 전체 분석 종료 전에도 읽을 수 있다.

PM 완료 후 기존 `record_decision`이 UUID JSON과 Memory를 저장하고 기존 성공 시
체크포인트 정리를 수행한다. 원본 가격 처리·프롬프트·판단·Memory 정책은 그대로다.
실패 시 자동 전체 재실행이나 실패 JSON 생성은 하지 않으며 완료된 보고서는 서버가
켜져 있는 동안 열람할 수 있다. 서버 종료 후 미완료 웹 상태는 소실된다. 기존 설정이
체크포인트 사용이면 엔진이 기존 체크포인트를 남길 수 있다. 자동 재개는 하지 않는다.

같은 서버 프로세스에서는 한 번에 한 분석만 허용한다. 데스크톱과 스마트폰이 함께
접속해도 중복 실행 요청은 거절한다. 별도 서버 프로세스를 여러 개 실행하지 않는다.
상태 객체는 최근 최대 20개만 메모리에 유지하며 정상 완료 JSON은 삭제하지 않는다.

기록은 DEFAULT_CONFIG의 `results_dir/runs/<uuid>.json`을 읽는다. 필요하면:

```powershell
python -m tradingagents.web.app --results-dir "D:\TradingAgentsResults"
```

이 경로는 새 분석의 저장 경로이기도 하다. 기존 NVDA 검증 기록을 열려면 해당
검증 결과 폴더를 지정하면 된다. 원래 기록을 이동·변환하지 않는다. UUID 형식·정상
완료 상태·버전을 검사하고 잘못된 파일은 목록에서 제외한다. UUID 기록 이전의
구형 날짜별 JSON은 자동 변환하지 않는다. 서버는 repo나 results 폴더 전체를 정적
파일로 공개하지 않는다. Host 제한·동일 origin·요청 토큰으로 타 사이트의 실행 요청을
막으며, 이는 LAN 사용자별 인증을 대체하는 기능은 아니다.

## API 비용 없는 모의 실행

```powershell
python -m tradingagents.web.app --demo --results-dir "D:\TradingAgentsDemo" --port 8001
```

별도 모의 폴더를 사용한다. `--results-dir`을 생략하면 `~/.tradingagents/dashboard-demo`를
사용한다. DEMO 표식과 모든 샘플 보고서에 모의 자료임을 표시한다. 이 모드는 엔진·
외부 데이터·LLM·Memory를 호출하지 않으며, 같은 기존 UUID writer로 UI 검증 기록만
저장한다. TradingView 차트 자체는 별도 인터넷 위젯이다. 실제 모드의 서버에서
`--demo` 없는 분석 버튼은 API 비용을 발생시킬 수 있다.

## 검증 범위

`tests/test_web_dashboard.py`는 입력·빈 값/0·진행 중 보고서·누적 토론·기존 writer·
동시 실행 거절·실패 무재시도·기록 검색·접근 제한·키 비노출·Markdown 안전성을
모의 그래프로 검증한다. `test_graph_end_to_end.py`의 웹 연결 테스트는 원래
TradingAgentsGraph 전체를 scripted LLM과 테스트 데이터로 실행하고 2라운드의 토론,
한국어 실행 설정, 원문/UUID 저장과 기존 Memory 연결을 확인한다. 유료 모델로
한국어 보고서 품질이나 실데이터 공급자의 가용성을 다시 검증하는 테스트는 아니다.
