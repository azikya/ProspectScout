from prospectscout import config


def test_load_returns_azikya_defaults_when_missing():
    cfg = config.load()
    assert cfg["company"]["name"] == "Azikya Software Solutions"
    assert cfg["company"]["website"] == "https://azikya.com"
    assert cfg["max_pages"] == 8 and cfg["max_link_checks"] == 40 and cfg["concurrency"] == 1
    assert cfg["sop"]["size_min"] == 11 and cfg["sop"]["size_max"] == 1000
    assert cfg["sop"]["weights"]["hiring_tech_roles"] == 3


def test_save_then_load_roundtrip():
    cfg = config.load()
    cfg["sender"]["name"] = "Alex"
    config.save(cfg)
    assert config.load()["sender"]["name"] == "Alex"


def test_update_deep_merges():
    config.update({"company": {"pitch": "x"}})
    cfg = config.load()
    assert cfg["company"]["pitch"] == "x" and cfg["company"]["name"] == "Azikya Software Solutions"


def test_load_does_not_mutate_defaults():
    cfg = config.load()
    cfg["company"]["services"].append("mutated")
    assert "mutated" not in config.load()["company"]["services"]


def test_followup_spacing_default():
    assert config.load()["followup_spacing"] == [[2, 3], [3, 5], [4, 7]]


def test_concurrent_updates_do_not_collide():
    import threading
    errors = []

    def work(i):
        try:
            config.update({"model": f"m{i}"})
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)
    threads = [threading.Thread(target=work, args=(i,)) for i in range(20)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert not errors and config.load()["model"].startswith("m")
