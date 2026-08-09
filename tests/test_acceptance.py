from okam_native.acceptance import REQUIRED_GATES, evaluate


def test_all_physical_and_architecture_gates_are_required() -> None:
    passed, failures = evaluate({"gates": {name: True for name in REQUIRED_GATES}})
    assert passed
    assert failures == ()


def test_one_missing_gate_blocks_release() -> None:
    gates = {name: True for name in REQUIRED_GATES}
    gates["create_snapshot"] = False
    passed, failures = evaluate({"gates": gates})
    assert not passed
    assert failures == ("create_snapshot",)
