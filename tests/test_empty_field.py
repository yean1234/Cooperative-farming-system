"""빈 필드 시뮬레이터용 실측 단지 데이터(server/realdata/empty_field.py) 검사."""
from server.realdata.empty_field import FARM_HA, build


def test_build_real_complex():
    d = build(6)
    ps = d["parcels"]
    members = [p for p in ps if p["kind"] == "member"]
    # 단지 6은 지원 기준(20ha)을 넘는 논 단지다
    assert sum(p["area"] for p in members) >= 20
    assert all(p["use"] == "논" for p in members)
    # 참여 필지와 편입 후보에는 추정 소유자가 있고, 배경 필지에는 없다
    assert all(p["owner"] for p in ps if p["kind"] in ("member", "cand"))
    assert all(p["owner"] is None for p in ps if p["kind"] == "bg")
    # 참여 농가와 이웃 농가 이름이 섞이지 않는다
    assert all(p["owner"].startswith("참여") for p in members)
    assert all(p["owner"].startswith("이웃") for p in ps if p["kind"] == "cand")
    # 추정 농가 규모는 목표(1.8ha) 근처다
    farm_area = {}
    for p in members:
        farm_area[p["owner"]] = farm_area.get(p["owner"], 0) + p["area"]
    mean = sum(farm_area.values()) / len(farm_area)
    assert 0.6 * FARM_HA <= mean <= 1.4 * FARM_HA
    # 이웃 관계는 대칭이고, 좌표는 화면 영역 안에 있다
    nb = {p["id"]: set(p["nb"]) for p in ps}
    assert all(i in nb[j] for i, js in nb.items() for j in js)
    W, H = d["meta"]["width"], d["meta"]["height"]
    assert all(0 <= x <= W and 0 <= y <= H for p in ps for x, y in p["poly"])
