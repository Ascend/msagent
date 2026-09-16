import importlib.util
from pathlib import Path


SCRIPT = (
    Path(__file__).resolve().parents[3] / "skills/quantizer/tune-practice-cfg/scripts/check_anti_outlier_support.py"
)
SPEC = importlib.util.spec_from_file_location("check_anti_outlier_support", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(MODULE)


class FlexInterface:
    pass


class Adapter(FlexInterface):
    pass


def test_shared_interface_lists_both_algorithms():
    result = MODULE.list_supported_algorithms(Adapter(), {"FlexSmoothQuantInterface": FlexInterface})
    assert result["ok"] is True
    assert result["supported_algorithms"] == ["flex_smooth_quant", "flex_awq_ssz"]


def test_missing_interface_is_not_selectable():
    result = MODULE.list_supported_algorithms(Adapter(), {"IterSmoothInterface": type("Iter", (), {})})
    assert "iter_smooth" in result["unsupported_algorithms"]
    assert "iter_smooth" not in result["supported_algorithms"]


def test_no_interfaces_means_no_anti_outlier_choices():
    result = MODULE.list_supported_algorithms(object(), {})
    assert result["supported_algorithms"] == []
    assert set(result["unsupported_algorithms"]) == set(MODULE.ALGORITHM_INTERFACES)
