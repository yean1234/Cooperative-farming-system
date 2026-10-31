"""실측 읍면 지도 만들기(후보단지 시뮬레이터 입력).

가상 읍면(region_demo) 대신 공개 실측 자료를 묶어 같은 형식의 지도 JSON을 만든다.

  필지 경계·논밭 구분·지목·경지정리  팜맵 SHP(농림수산식품교육문화정보원, 공공데이터포털 파일)   키 불필요
  도로                               OpenStreetMap(Overpass API)                              키 불필요
  경사(대체값)                       Copernicus DEM GLO-90(Open-Meteo 표고 API)               키 불필요
  경사·배수등급·논밭 적성등급        흙토람 토양특성 API(농촌진흥청, 공공데이터포털)          DATA_GO_KR_KEY
  지목 '도' 필지·공시지가            연속지적도(브이월드 2D 데이터 API)                       VWORLD_KEY

키는 환경변수나 저장소 루트의 .env에서 읽는다(.env.example 참고). 키가 없는 단계는 건너뛰고 meta.sources에 남긴다.
"""
