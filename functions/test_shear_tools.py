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
    assert "row (c)" in out["basis"], "basis must cover the size-effect row (c)"


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


# --- h / cover_cg path: d-equivalence + resolution errors -------------------
def t_d_equivalence_shear():
    by_d = beam_calc.shear_capacity(b=350, d=500, f_c=28)
    by_h = beam_calc.shear_capacity(b=350, h=560, cover_cg=60, f_c=28)
    assert by_d == by_h, f"d-path {by_d} != h-path {by_h}"


# --- flex_capacity via h/cover_cg must match d ------------------------------
def t_d_equivalence_flex():
    by_d = beam_calc.flex_capacity(b=400, d=500, A_s=1600, f_c=30, f_yl=420)
    by_h = beam_calc.flex_capacity(b=400, h=560, cover_cg=60, A_s=1600, f_c=30, f_yl=420)
    assert by_d == by_h, f"d-path {by_d} != h-path {by_h}"


# --- effective-depth resolution errors (XOR / neither / cover >= h) ---------
def t_d_resolution_errors():
    # XOR: d plus any of h/cover_cg is a hard error, never silent precedence
    for kwargs in [
        {"b": 350, "d": 500, "f_c": 28, "h": 560},
        {"b": 350, "d": 500, "f_c": 28, "cover_cg": 60},
        {"b": 350, "d": 500, "f_c": 28, "h": 560, "cover_cg": 60},
    ]:
        try:
            beam_calc.shear_capacity(**kwargs)
        except ValueError as exc:
            assert "not both" in str(exc), exc
        else:
            raise AssertionError(f"shear_capacity({kwargs}): expected ValueError")

    # neither d nor h/cover_cg
    try:
        beam_calc.shear_capacity(b=350, f_c=28)
    except ValueError as exc:
        assert "give d, or h with cover_cg" in str(exc), exc
    else:
        raise AssertionError("expected ValueError when neither d nor h/cover_cg")

    # cover_cg >= h would give d <= 0
    try:
        beam_calc.shear_capacity(b=350, h=500, cover_cg=500, f_c=28)
    except ValueError as exc:
        assert "cover_cg" in str(exc) and "< h" in str(exc), exc
    else:
        raise AssertionError("expected ValueError when cover_cg >= h")

    # same resolution rules on flex
    try:
        beam_calc.flex_capacity(b=400, h=560, A_s=1600, f_c=30, f_yl=420)
    except ValueError as exc:
        assert "give d, or h with cover_cg" in str(exc), exc
    else:
        raise AssertionError("flex_capacity: expected ValueError with h but no cover_cg")


# --- row (c) size-effect Vc, ACI 22.5.5.1(c) --------------------------------
def t_row_c_size_effect():
    # b=350, d=500, f'c=28, A_s=1500, no stirrups:
    # rho_w = 1500/(350·500) = 0.008571, lambda_s = sqrt(2/(1+500/250)) = 0.8165
    # V_c = 0.66·0.8165·0.008571^(1/3)·sqrt(28)·350·500/1000 ≈ 102.2 kN
    out = beam_calc.shear_capacity(b=350, d=500, f_c=28, A_s=1500)
    lambda_s = min(math.sqrt(2 / (1 + 500 / 250)), 1.0)
    rho_w = 1500 / (350 * 500)
    expected = 0.66 * lambda_s * math.sqrt(28) * rho_w ** (1 / 3) * 350 * 500 / 1000
    near(out["lambda_s"], lambda_s, 1e-4, "row (c) lambda_s")
    near(out["V_c_kN"], expected, 1e-2, "row (c) V_c")
    near(out["V_c_kN"], 102.2, 0.2, "row (c) V_c ~ 102.2 kN")
    assert out["Vc_criterion"] == "row (c)", out["Vc_criterion"]
    assert out["V_s_kN"] == 0.0, "no stirrups -> V_s = 0"


# --- adequate stirrups -> rows (a)/(b), lambda_s = 1 ------------------------
def t_stirrups_adequate_rows_ab():
    # Av,min = 291.67 mm²/m (b_w=350, f'c=28, f_yw=420); per spacing at s=150:
    # 291.67·150/1000 = 43.75 mm²; A_v=100 is adequate
    out_a = beam_calc.shear_capacity(b=350, d=500, f_c=28, A_v=100, s=150, f_yw=420)
    assert out_a["Vc_criterion"] == "row (a)", out_a["Vc_criterion"]
    near(out_a["lambda_s"], 1.0, 0, "row (a) lambda_s")
    expected_a = 0.17 * math.sqrt(28) * 350 * 500 / 1000
    near(out_a["V_c_kN"], expected_a, 1e-3, "row (a) V_c")

    # detailed (b) still reachable when adequate AND A_s+V_u+M_u given
    out_b = beam_calc.shear_capacity(
        b=350, d=500, f_c=28, A_v=100, s=150, f_yw=420,
        A_s=1500, V_u=100, M_u=50,
    )
    assert out_b["Vc_criterion"] == "row (b)", out_b["Vc_criterion"]
    rho_w = 1500 / (350 * 500)
    vu_d_over_mu = min(100 * 500 / (50 * 1000), 1.0)
    expected_b = min((0.16 * math.sqrt(28) + 17 * rho_w * vu_d_over_mu) * 350 * 500 / 1000,
                     0.29 * math.sqrt(28) * 350 * 500 / 1000)
    near(out_b["V_c_kN"], expected_b, 1e-2, "row (b) V_c")


# --- partial stirrups -> row (c) plus V_s -----------------------------------
def t_row_c_partial_stirrups():
    # A_v=30 < 43.75 mm²/spacing -> row (c), but stirrups still resist
    out = beam_calc.shear_capacity(b=350, d=500, f_c=28, A_v=30, s=150, f_yw=420, A_s=1500)
    assert out["Vc_criterion"] == "row (c)", out["Vc_criterion"]
    near(out["V_s_kN"], 30 * 420 * 500 / 150 / 1000, 1e-9, "partial stirrups V_s")
    lambda_s = min(math.sqrt(2 / (1 + 500 / 250)), 1.0)
    near(out["lambda_s"], lambda_s, 1e-9, "partial stirrups lambda_s")


# --- no stirrups + no A_s -> row (a) fallback -------------------------------
def t_no_stirrups_no_as_row_a():
    out = beam_calc.shear_capacity(b=350, d=500, f_c=28)
    assert out["Vc_criterion"] == "row (a)", out["Vc_criterion"]
    near(out["lambda_s"], 1.0, 0, "fallback lambda_s")


# --- wrapper accepts the h/cover_cg path ------------------------------------
def t_wrapper_h_path():
    by_h = wrapper.call_tool("shear_capacity", b=350, h=560, cover_cg=60, f_c=28)
    by_d = wrapper.call_tool("shear_capacity", b=350, d=500, f_c=28)
    assert by_h["value"] == by_d["value"], (by_h, by_d)
    assert by_h["value"]["Vc_criterion"] == "row (a)", by_h["value"]["Vc_criterion"]
    flex = wrapper.call_tool("flex_capacity", b=400, h=560, cover_cg=60, A_s=1600, f_c=30, f_yl=420)
    near(flex["value"]["phiM_n_kNm"], 282.471, 1e-2, "wrapper h-path flex phiM_n")


# --- 5. s_max switches at the 0.33·√f'c·b·d threshold; Vs cap (§22.5.1.2) --
def _row_limits(row, v_u):
    """Recompute the design rules a row must satisfy, for hand cross-checks."""
    v_c = row["V_c_kN"]
    v_s_req = v_u / 0.75 - v_c
    cap33 = 0.33 * math.sqrt(row["f_c"]) * row["b"] * row["d"] / 1000
    cap66 = 0.66 * math.sqrt(row["f_c"]) * row["b"] * row["d"] / 1000
    s_max = min(row["d"] / 2, 600) if v_s_req <= cap33 else min(row["d"] / 4, 300)
    return v_s_req, cap66, s_max


def t_design_pure_shear():
    """V_u=50, M_u=0, max 400×700: cheapest feasible is 250×350×20, D19×1.

    Hand-verified: at f'c=20, V_c = 57.1 kN, so V_s,req = 66.7−57.1 = 9.6 kN
    > 0 — the demand spacing exceeds s_max, so the stirrup picker clamps to
    s_max = d/2 = 150.25 (D10 @ 150.25 gives Vs = 131.9 ≫ 9.6). That clamp
    (2026-10-08 bug fix) is exactly what makes the cheaper f'c=20 grade
    feasible; f'c=30 also works (cost 22.69) but 20 wins at 21.84 < the
    previously-verified b=300,d=500,f'c=30 class (~37.6).
    """
    out = beam_calc.design_beam(V_u=50, M_u=0, max_b=400, max_h=700)
    assert out["feasible"], out
    assert out["reason"] is None
    opt = out["optimum"]
    assert opt["b"] == 250 and opt["h"] == 350, opt
    assert opt["f_c"] == 20, opt
    assert opt["long_bar"] == "D19 × 1", opt
    assert opt["phiV_n_kN"] >= 50 - 1e-6, opt  # φVn ≥ Vu
    assert opt["phiM_n_kNm"] >= 0, opt
    assert opt["utilization"] <= 1.0 + 1e-6, opt
    near(opt["cost"], 21.84, 0.5, "pure-shear optimum cost")
    # stirrup-clamp regression guard: V_s,req > 0 but small, s == s_max
    assert opt["s"] == opt["s_max"] == opt["d"] / 2, opt
    assert opt["V_s_kN"] > 0, "clamped min stirrups still contribute Vs"
    # strictly cheaper than the previously-verified b=300,d=500,f'c=30
    # class design (concrete 300×550 + D19×3 + D10@250 ≈ 37.6 $/m)
    assert opt["cost"] < 30, opt
    assert len(out["ranked"]) <= 5


def t_design_min_stirrup():
    """Min-stirrup path obeys Av ≥ Av,min·s/1000 (Av,min per §9.6.3.3)."""
    out = beam_calc.design_beam(V_u=50, M_u=0, max_b=400, max_h=700)
    opt = out["optimum"]
    av_min = beam_calc.min_shear_reinf(opt["b"], opt["f_c"], 420)
    assert opt["A_v_mm2"] >= av_min * opt["s"] / 1000 - 1e-6, opt
    assert opt["V_s_kN"] > 0, "min stirrups still contribute Vs"


def t_design_flex_gate():
    """φMn ≥ Mu and A_s ≥ As,min bind; 250×350 rejected for M_u=200.

    Hand-verified: at 250×350 the strongest combo (f'c=40, D25×6) reaches
    only φMn ≈ 186 kN·m < 200, so no row may carry that section. The search
    chooses 250×550×20 D16×6: d=502, a=119.15, ε_t=0.0077→φ=0.9,
    M_n=224.1 → φMn=201.7 ≥ 200; deep + f'c=20 (cheapest concrete) beats
    shallower high-grade options.
    """
    out = beam_calc.design_beam(V_u=50, M_u=200, max_b=400, max_h=700)
    assert out["feasible"], out
    for row in out["ranked"]:
        assert row["phiM_n_kNm"] >= 200 - 1e-6, row
        assert row["phiV_n_kN"] >= 50 - 1e-6, row
        assert not (row["b"] == 250 and row["h"] == 350), row
        as_min = max(0.25 * math.sqrt(row["f_c"]) / 420, 1.4 / 420) * row["b"] * row["d"]
        assert row["A_s_mm2"] >= as_min - 1e-6, row
    opt = out["optimum"]
    assert opt["b"] == 250 and opt["h"] == 550, opt
    assert opt["f_c"] == 20 and opt["long_bar"] == "D16 × 6", opt
    near(opt["phiM_n_kNm"], 201.7, 0.5, "M_u=200 optimum φMn")
    near(opt["cost"], 35.4, 0.5, "M_u=200 optimum cost")
    cheap = beam_calc.design_beam(V_u=50, M_u=0, max_b=400, max_h=700)
    assert opt["cost"] > cheap["optimum"]["cost"], \
        "flexure demand must cost more than pure shear"


def t_design_d_formula():
    """d = h − cover − φ_long/2 (cover 40 default; D19 h=300 → 250.5)."""
    out = beam_calc.design_beam(V_u=50, M_u=200, max_b=400, max_h=700)
    for row in out["ranked"]:
        assert row["d"] == row["h"] - 40 - row["phi_long"] / 2, row
    # custom cover threaded through
    out2 = beam_calc.design_beam(V_u=50, M_u=0, max_b=300, max_h=500, cover=50)
    for row in out2["ranked"]:
        assert row["d"] == row["h"] - 50 - row["phi_long"] / 2, row
    # exact plan example
    assert 350 - 40 - 19 / 2 == 300.5


def t_design_shear_limits():
    """Vs ≤ 0.66·√f'c·b·d rejected; s in [100, s_max]; s_max switches."""
    for kwargs in [
        {"V_u": 50, "M_u": 0, "max_b": 400, "max_h": 700},
        {"V_u": 50, "M_u": 200, "max_b": 400, "max_h": 700},
    ]:
        out = beam_calc.design_beam(**kwargs)
        for row in out["ranked"]:
            v_s_req, cap66, expect_s_max = _row_limits(row, kwargs["V_u"])
            assert row["V_s_kN"] <= cap66 + 1e-6, row
            assert row["s_max"] == expect_s_max, row
            assert row["s"] >= 100 - 1e-6, row
            assert row["s"] <= row["s_max"] + 1e-6, row
            av_min = beam_calc.min_shear_reinf(row["b"], row["f_c"], 420)
            assert row["A_v_mm2"] >= av_min * row["s"] / 1000 - 1e-6, row
    # V_s,req > 0.66·√f'c·b·d everywhere inside 250×350 -> no feasible design
    out = beam_calc.design_beam(V_u=300, M_u=50, max_b=250, max_h=350)
    assert not out["feasible"], out
    assert out["ranked"] == []


def t_design_bounds():
    """Grid respects max_b/max_h; step 50; b never exceeds max_b."""
    out = beam_calc.design_beam(V_u=50, M_u=0, max_b=300, max_h=500)
    assert out["feasible"], out
    for row in out["ranked"]:
        assert row["b"] in (250, 300), row
        assert row["h"] in (350, 400, 450, 500), row
    out2 = beam_calc.design_beam(V_u=50, M_u=0, max_b=385, max_h=380)
    assert out2["feasible"], out2
    for row in out2["ranked"]:
        assert row["b"] in (250, 300, 350) and row["b"] <= 385, row
        assert row["h"] == 350 and row["h"] <= 380, row


def t_design_infeasible():
    """Huge demands -> feasible False + reason, never a crash."""
    out = beam_calc.design_beam(V_u=50, M_u=900, max_b=400, max_h=700)
    assert not out["feasible"], out
    assert out["ranked"] == [] and out["optimum"] is None
    assert "no feasible" in out["reason"].lower()
    out2 = beam_calc.design_beam(V_u=50, M_u=0, max_b=200, max_h=500)
    assert not out2["feasible"] and "no section" in out2["reason"], out2


def t_design_ranking():
    """ranked sorted by min total cost; optimum == ranked[0]; deterministic."""
    out = beam_calc.design_beam(V_u=50, M_u=200, max_b=400, max_h=700)
    costs = [row["cost"] for row in out["ranked"]]
    assert costs == sorted(costs), costs
    assert out["optimum"] == out["ranked"][0]
    again = beam_calc.design_beam(V_u=50, M_u=200, max_b=400, max_h=700)
    assert again == out, "deterministic"
    for row in out["ranked"]:
        assert row["cost"] > 0
        near(row["cost"],
             row["cost_concrete"] + row["cost_long_steel"]
             + row["cost_stirrup_steel"], 1e-9, "cost breakdown sum")


def t_design_wrapper():
    """Wrapper: design_beam registered, arrays/objects validated, errors."""
    out = wrapper.call_tool("design_beam", V_u=50, M_u=0, max_b=400, max_h=700)
    assert isinstance(out["value"], dict) and out["value"]["feasible"], out
    assert 0 < len(out["value"]["ranked"]) <= 5
    assert "design" in out["unit"]
    assert "9.6.3.3" in out["basis"] and "22.5.5.1" in out["basis"]

    custom = wrapper.call_tool(
        "design_beam", V_u=50, M_u=0, max_b=300, max_h=500,
        f_c_list=[25, 40], rate_conc={"25": 99, "40": 199}, rate_steel=3.0,
    )
    assert custom["value"]["feasible"], custom
    opt = custom["value"]["optimum"]
    assert opt["f_c"] in (25, 40), opt  # custom grade list honored
    # string-key rate_conc must ACTUALLY apply (regression: keys were silently
    # ignored and the default rate used; 2026-10-08). cost_concrete =
    # rate[grade]·b·h/1e6 exactly.
    expected = {25: 99, 40: 199}[opt["f_c"]] * opt["b"] * opt["h"] / 1e6
    near(opt["cost_concrete"], expected, 1e-9, "custom rate_conc applied")
    # non-numeric grade key -> loud ValueError, not a crash
    try:
        wrapper.call_tool("design_beam", V_u=50, M_u=0, max_b=300, max_h=500,
                          rate_conc={"high": 99})
    except ValueError as exc:
        assert "numeric" in str(exc), exc
    else:
        raise AssertionError("design_beam rate_conc non-numeric key: expected ValueError")

    # preset="idr" substitutes the Indonesian price table (Rp/m³, Rp/kg);
    # explicit rates always beat a preset; unknown preset -> ValueError
    idr = wrapper.call_tool("design_beam", V_u=50, M_u=0, max_b=300, max_h=500,
                            preset="idr")
    assert idr["value"]["feasible"], idr
    o = idr["value"]["optimum"]
    expected_idr = beam_calc.PRICE_PRESETS["idr"]["rate_conc"][o["f_c"]] * o["b"] * o["h"] / 1e6
    near(o["cost_concrete"], expected_idr, 1e-9, "idr preset concrete rate applied")
    assert o["cost"] > 1000, "idr costs are in Rp (thousands), not USD"
    # explicit rate_conc beats the preset
    explicit = wrapper.call_tool("design_beam", V_u=50, M_u=0, max_b=300, max_h=500,
                                 preset="idr", rate_conc={"20": 99, "25": 99, "30": 99, "35": 99, "40": 99})
    o2 = explicit["value"]["optimum"]
    near(o2["cost_concrete"], 99 * o2["b"] * o2["h"] / 1e6, 1e-9, "explicit rate beats preset")
    expect_value_error("design_beam", {"V_u": 50, "M_u": 0, "max_b": 400,
                                       "max_h": 700, "preset": "eur"},
                       "one of")

    expect_value_error("design_beam", {"V_u": 50, "M_u": 0, "max_b": 400,
                                       "max_h": 700, "f_c_list": "nope"},
                       "array")
    expect_value_error("design_beam", {"V_u": 50, "M_u": 0, "max_b": 400,
                                       "max_h": 700, "rate_conc": [1, 2]},
                       "object")
    expect_value_error("design_beam", {"V_u": 50, "M_u": 0, "max_b": 400,
                                       "max_h": 700, "f_c_list": [1, "x"]},
                       "finite")
    expect_value_error("design_beam", {"V_u": 50, "M_u": 0, "max_b": 400,
                                       "max_h": 700, "bogus": 1},
                       "unknown")
    # direct call guards: non-positive Vu
    try:
        beam_calc.design_beam(V_u=0, M_u=0, max_b=400, max_h=700)
    except ValueError as exc:
        assert "V_u" in str(exc), exc
    else:
        raise AssertionError("design_beam(V_u=0): expected ValueError")


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
    t_d_equivalence_shear,
    t_d_equivalence_flex,
    t_d_resolution_errors,
    t_row_c_size_effect,
    t_stirrups_adequate_rows_ab,
    t_row_c_partial_stirrups,
    t_no_stirrups_no_as_row_a,
    t_wrapper_h_path,
    t_design_pure_shear,
    t_design_min_stirrup,
    t_design_flex_gate,
    t_design_d_formula,
    t_design_shear_limits,
    t_design_bounds,
    t_design_infeasible,
    t_design_ranking,
    t_design_wrapper,
]

if __name__ == "__main__":
    for test in TESTS:
        check(test.__name__, test)

    print(f"\n{len(CHECKS) - len(FAILURES)}/{len(CHECKS)} checks passed")
    if FAILURES:
        sys.exit(1)