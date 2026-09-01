"""Hand-calc verification for functions/beam_calc.py + wrapper.py. Plain asserts.

Run: python3 functions/test_shear_tools.py
Exits non-zero on any failure.
"""

import sys
import os
import math
import importlib

# Load sibling modules explicitly — works from any cwd; pyright stays silent
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
beam_calc = importlib.import_module("beam_calc")
wrapper = importlib.import_module("wrapper")


CHECKS = []
FAILURES = []


def check(name, fn):
    CHECKS.append(name)
    try:
        fn()
        print(f"PASS  {name}")
    except AssertionError as exc:
        FAILURES.append((name, exc))
        print(f"FAIL  {name}: {exc}")
    except Exception as exc:  # unexpected — tool raised something else
        FAILURES.append((name, exc))
        print(f"FAIL  {name} (unexpected {type(exc).__name__}): {exc}")


def near(actual, expected, tol, label):
    assert abs(actual - expected) <= tol, (
        f"{label}: got {actual}, expected {expected} ± {tol}"
    )


# --- 1. min_shear_reinf : Av,min per metre, ACI 318M-19 §9.6.3.3 -----------
def t_min_shear():
    result = beam_calc.min_shear_reinf(350, 28, 420)
    term1 = 0.062 * math.sqrt(28) * 350 / 420 * 1000  # 273.39
    term2 = 0.35 * 350 / 420 * 1000  # 291.6667 — governing
    near(result, term2, 1e-6, "min_shear_reinf(350, 28, 420)")
    assert result > term1, "term2 (0.35·b_w/f_yt) must govern for f'c=28"


# --- 2. shear_capacity : simplified Vc, ACI 22.5.5.1(a) ---------------------
def t_shear_simplified():
    out = beam_calc.shear_capacity(350, 500, 28)
    expected = 0.17 * math.sqrt(28) * 350 * 500 / 1000  # 157.42 kN
    near(out["V_c_kN"], expected, 1e-3, "shear_capacity simplified V_c")
    assert out["phi_v"] == 0.75
    near(out["phiV_n_kN"], 0.75 * expected, 1e-3, "shear_capacity phiV_n")


# --- 3. flex_capacity : ACI 22.2.2.1 stress block + Table 21.2.2 φ ----------
def t_flex():
    out = beam_calc.flex_capacity(400, 500, 1600, 30, 420)
    near(out["a_mm"], 65.8824, 1e-2, "a_mm")
    near(out["beta_1"], 0.84, 1e-6, "beta_1")
    near(out["c_mm"], 78.431, 1e-2, "c_mm")
    near(out["epsilon_t"], 0.01613, 1e-4, "epsilon_t")
    near(out["phi"], 0.90, 1e-6, "phi")
    near(out["M_n_kNm"], 313.857, 1e-2, "M_n_kNm")
    near(out["phiM_n_kNm"], 282.471, 1e-2, "phiM_n_kNm")
    assert out["A_s_mm2"] == 1600


# --- 4. wrapper : shape + unit + basis --------------------------------------
def t_wrapper_shape_min_shear():
    out = wrapper.call_tool("min_shear_reinf", b_w=350, f_c=28, f_yt=420)
    assert isinstance(out["value"], float)
    near(out["value"], 291.6667, 1e-4, "wrapper min_shear_reinf value")
    assert out["unit"] == "mm²/m", out["unit"]
    assert "9.6.3.3" in out["basis"], out["basis"]


def t_wrapper_shape_flex():
    out = wrapper.call_tool("flex_capacity", b=400, d=500, A_s=1600, f_c=30, f_yl=420)
    assert isinstance(out["value"], dict)
    near(out["value"]["phiM_n_kNm"], 282.471, 1e-2, "wrapper flex phiM_n")
    assert out["unit"] == "kN·m", out["unit"]
    assert "21.2.2" in out["basis"]


def t_wrapper_shear_shape():
    out = wrapper.call_tool("shear_capacity", b=350, d=500, f_c=28)
    assert out["unit"] == "kN", out["unit"]
    assert "22.5.5.1" in out["basis"]
    assert "size-effect" in out["basis"], "basis must note Eq(c) not implemented"


# --- validation error paths ------------------------------------------------
def expect_value_error(name, kwargs, fragment):
    try:
        wrapper.call_tool(name, **kwargs)
    except ValueError as exc:
        assert fragment in str(exc), f"missing '{fragment}' in: {exc}"
        return
    raise AssertionError(f"{name}{kwargs}: expected ValueError")


def t_missing_required():
    expect_value_error("min_shear_reinf", {"b_w": 350}, "missing required")
    expect_value_error("flex_capacity", {"b": 400, "d": 500, "A_s": 1600}, "f_yl")


def t_negative_bound():
    expect_value_error("min_shear_reinf", {"b_w": -350, "f_c": 28, "f_yt": 420}, "b_w")
    expect_value_error("flex_capacity", {"b": 400, "d": 500, "A_s": 1600, "f_c": 30, "f_yl": -420}, "f_yl")


def t_unknown_key():
    expect_value_error("shear_capacity", {"b": 350, "d": 500, "f_c": 28, "bogus": 1}, "unknown")

def t_non_numeric():
    expect_value_error("min_shear_reinf", {"b_w": "wide", "f_c": 28, "f_yt": 420}, "number")


def t_unknown_tool():
    expect_value_error("no_such_tool", {}, "unknown tool")


TESTS = [
    t_min_shear,
    t_shear_simplified,
    t_flex,
    t_wrapper_shape_min_shear,
    t_wrapper_shape_flex,
    t_wrapper_shear_shape,
    t_missing_required,
    t_negative_bound,
    t_unknown_key,
    t_non_numeric,
    t_unknown_tool,
]

if __name__ == "__main__":
    for test in TESTS:
        check(test.__name__, test)

    print(f"\n{len(CHECKS) - len(FAILURES)}/{len(CHECKS)} checks passed")
    if FAILURES:
        sys.exit(1)