"""ACI 318M-19 beam shear / flexure capacity calculations. Stdlib only.

Formulas copied from BeamValidation repo
(https://github.com/Siboi420/BeamValidation, commit
668be3670dc8ba065f215a0ca1b59eb9e3bd8ca5, scripts/RCBeam_moment_capacity.py),
with numpy dropped for math. The source repo targets ACI 318-14; the reused
formulas are unchanged in ACI 318M-19 (see module-level notes per function).

Constants (matching source):
  EPSILON_CU = 0.003   ultimate concrete strain
  Es          = 200000 MPa
  lambda      = 1.0    normal-weight concrete
"""

import math

EPSILON_CU = 0.003
ES = 2e5  # MPa

# phi_v = 0.75 per ACI 318-19 §21.2.1 (shear)


def _beta_1(f_c):
    """Stress block factor, ACI 318-19 §22.2.2.4.3 (same as 318-14)."""
    if f_c >= 55:
        return 0.65
    elif f_c > 28:
        return round(0.85 - 0.05 * (f_c - 28) / 7, 2)
    else:
        return 0.85


def _phi(epsilon_t, epsilon_y):
    """Strength reduction factor for flexure, ACI 318-19 Table 21.2.2."""
    if epsilon_t >= 0.005:
        return 0.90  # tension-controlled
    elif epsilon_t > epsilon_y:
        # Transition zone (spiral)
        return 0.65 + 0.25 * (epsilon_t - epsilon_y) / (0.005 - epsilon_y)
    else:
        return 0.65  # compression-controlled


def _resolve_d(d, h, cover_cg):
    """Effective depth d (mm): pass d, or h with cover_cg (d = h − cover_cg).

    XOR — d plus h/cover_cg is a loud error, never a silent precedence;
    cover_cg >= h is rejected (d would be ≤ 0).
    """
    if d is not None and (h is not None or cover_cg is not None):
        raise ValueError("give d OR (h with cover_cg), not both (d = h − cover_cg)")
    if d is not None:
        return d
    if h is not None and cover_cg is not None:
        if cover_cg >= h:
            raise ValueError(f"cover_cg ({cover_cg} mm) must be < h ({h} mm)")
        return h - cover_cg
    raise ValueError("give d, or h with cover_cg (d = h − cover_cg)")


def min_shear_reinf(b_w, f_c, f_yt):
    """Minimum shear reinforcement Av,min per metre of beam, mm²/m.

    ACI 318M-19 §9.6.3.3 (identical to 318-14):
      Av,min/m = max(0.062·sqrt(f'c)·b_w/f_yt, 0.35·b_w/f_yt) · 1000
    """
    term1 = 0.062 * math.sqrt(f_c) * b_w / f_yt
    term2 = 0.35 * b_w / f_yt
    return max(term1, term2) * 1000  # mm²/m


def shear_capacity(b, d=None, f_c=None, A_v=0.0, s=0.0, f_yw=0.0,
                   A_s=None, V_u=None, M_u=None, h=None, cover_cg=None):
    """Shear capacity, ACI 318-19 §22.5.5.1(a)/(b)/(c) + §22.5.8.5.3.

    Effective depth d, or h with cover_cg (d = h − cover_cg) — never both.
    Vc row: (a) simplified; (b) detailed when A_s+V_u+M_u all given AND
    stirrups ≥ Av,min; (c) size-effect when stirrups < Av,min (or absent) and
    A_s is given: λ_s = min(√(2/(1+d/250)), 1), V_c = 0.66·λ_s·λ·(ρ_w)^⅓·√f'c·b·d.
    Stirrups adequate ⇔ A_v ≥ min_shear_reinf(b, f_c, f_yw)·s/1000.
    λ = 1.0. Returns {V_c_kN, V_s_kN, V_n_kN, phiV_n_kN, phi_v,
    Vc_criterion, lambda_s}.
    """
    if f_c is None:
        raise ValueError("shear_capacity: f_c is required")
    d_eff = _resolve_d(d, h, cover_cg)

    lambda_factor = 1.0  # normal weight concrete
    phi_v = 0.75

    has_stirrups = A_v > 0 and s > 0 and f_yw > 0
    stirrups_adequate = (
        has_stirrups and A_v >= min_shear_reinf(b, f_c, f_yw) * s / 1000
    )

    if stirrups_adequate and A_s is not None and V_u is not None and M_u is not None and M_u > 0:
        # Detailed, ACI 318-19 Eq. 22.5.5.1(b)
        rho_w = A_s / (b * d_eff)
        vu_d_over_mu = min(abs(V_u) * d_eff / (abs(M_u) * 1000), 1.0)
        V_c = (0.16 * lambda_factor * math.sqrt(f_c) + 17 * rho_w * vu_d_over_mu) * b * d_eff / 1000
        V_c_max = 0.29 * lambda_factor * math.sqrt(f_c) * b * d_eff / 1000
        V_c = min(V_c, V_c_max)
        lambda_s = 1.0
        Vc_criterion = "row (b)"
    elif not stirrups_adequate and A_s is not None:
        # Size-effect, ACI 318-19 Eq. 22.5.5.1(c)
        lambda_s = min(math.sqrt(2 / (1 + d_eff / 250)), 1.0)
        rho_w = A_s / (b * d_eff)
        V_c = 0.66 * lambda_s * lambda_factor * rho_w ** (1 / 3) * math.sqrt(f_c) * b * d_eff / 1000
        Vc_criterion = "row (c)"
    else:
        # Simplified, ACI 318-19 Eq. 22.5.5.1(a)
        V_c = 0.17 * lambda_factor * math.sqrt(f_c) * b * d_eff / 1000  # kN
        lambda_s = 1.0
        Vc_criterion = "row (a)"

    # Steel contribution, ACI 318-19 Eq. 22.5.8.5.3 (partial stirrups still resist)
    if has_stirrups:
        V_s = A_v * f_yw * d_eff / s / 1000  # kN
    else:
        V_s = 0.0

    V_n = V_c + V_s  # kN
    phiV_n = phi_v * V_n  # kN

    return {
        "V_c_kN": V_c,
        "V_s_kN": V_s,
        "V_n_kN": V_n,
        "phiV_n_kN": phiV_n,
        "phi_v": phi_v,
        "Vc_criterion": Vc_criterion,
        "lambda_s": lambda_s,
    }


def flex_capacity(b, d=None, A_s=None, f_c=None, f_yl=None, h=None, cover_cg=None):
    """Flexural capacity, singly reinforced rectangular section.

    Same effective-depth path as shear_capacity: d, or h with cover_cg
    (d = h − cover_cg), never both. a = A_s·f_y/(0.85·f'c·b); c = a/β₁;
    ε_t = 0.003·(d−c)/c; M_n = A_s·f_y·(d−a/2); φ = _phi(ε_t, ε_y). Assumes
    tension steel yields. ACI 318-19 §22.2.2.1 (stress block), §22.2.2.4.3 (β₁),
    Table 21.2.2 (φ). Returns {M_n_kNm, phiM_n_kNm, a_mm, c_mm, beta_1,
    epsilon_t, phi, A_s_mm2}. All units SI (MPa, mm, mm², kN·m).
    """
    if A_s is None or f_c is None or f_yl is None:
        raise ValueError("flex_capacity: A_s, f_c and f_yl are required")
    d_eff = _resolve_d(d, h, cover_cg)

    epsilon_y = f_yl / ES
    beta_1 = _beta_1(f_c)

    a = A_s * f_yl / (0.85 * f_c * b)
    c = a / beta_1
    epsilon_t = EPSILON_CU * (d_eff - c) / c
    M_n = A_s * f_yl * (d_eff - a / 2)  # N-mm
    phi = _phi(epsilon_t, epsilon_y)
    phiM_n = phi * M_n

    return {
        "M_n_kNm": M_n / 1e6,
        "phiM_n_kNm": phiM_n / 1e6,
        "a_mm": a,
        "c_mm": c,
        "beta_1": beta_1,
        "epsilon_t": epsilon_t,
        "phi": phi,
        "A_s_mm2": A_s,
    }


# --- design_beam: cheapest-feasible full-grid search -----------------------

DEFAULT_F_C_LIST = [20, 25, 30, 35, 40]  # MPa, fixed grades
# Bar name -> (one-bar area mm², diameter mm) — nominal metric bars
LONG_BARS = {"D16": (201, 16), "D19": (284, 19), "D22": (380, 22), "D25": (491, 25)}
STIRRUP_BARS = {"D10": 157, "D12": 226, "D13": 265}  # 2 legs
STEP = 50  # mm, section grid step
B_MIN = 250  # mm
H_MIN = 350  # mm
RHO_STEEL = 7850.0  # kg/m³
# Placeholder unit rates ($/m³ by grade, $/kg steel) — NOT real market prices.
DEFAULT_RATE_CONC = {20: 120, 25: 125, 30: 130, 35: 135, 40: 140}
DEFAULT_RATE_STEEL = 1.2


def _as_min(b, d_eff, f_c, f_y):
    """Minimum longitudinal tension steel, ACI 318M-19 §9.6.1.2 (mm²)."""
    return max(0.25 * math.sqrt(f_c) / f_y, 1.4 / f_y) * b * d_eff


def design_beam(V_u, M_u, max_b, max_h, cover=40.0, f_yt=420.0, f_y=420.0,
                f_c_list=None, rate_conc=None, rate_steel=DEFAULT_RATE_STEEL):
    """Cheapest feasible beam design for factored V_u (kN) + M_u (kN·m).

    Deterministic full-grid search: b in 250..max_b step 50, h in 350..max_h
    step 50, f'c in the fixed grade list, longitudinal D16-D25 × 1-6 bars,
    stirrups D10/D12/D13 (2 legs). d = h − cover − φ_long/2. Shear uses the
    simplified Vc row (a) 0.17·λ·√f'c·b·d with λ=1 (design intent; the
    row (b)/(c) variants in shear_capacity evaluate existing sections);
    flexure gates through flex_capacity (φM_n ≥ M_u, plus A_s ≥ As,min
    §9.6.1.2 checked here — not inside flex_capacity).

    Stirrup design: V_s,req = V_u/0.75 − V_c. When ≤ 0 -> minimum stirrups at
    s_max; else s = A_v·f_yt·d/V_s,req; every bar must also satisfy
    A_v ≥ Av,min·s/1000, V_s ≤ 0.66·√f'c·b·d (§22.5.1.2), 100 ≤ s ≤ s_max
    (§9.7.6.2.2: min(d/2,600) when V_s ≤ 0.33·√f'c·b·d else min(d/4,300)).

    Cost per metre = concrete (rate_conc[f'c]·b·h/1e6) + longitudinal steel
    (rate_steel·ρ·A_s/1e6) + stirrup steel (rate_steel·ρ·A_v·perim/(s·1e6),
    perimeter ≈ 2(b−2·cover)+2(h−2·cover)). Unit rates are configurable
    placeholders, not asserted as real prices. M_u is required (shear-only
    sizing is out of scope; pass M_u=0 for a flexurally trivial demand).

    ponytail: longitudinal-bar placement geometry within width b (bar-fit /
    min spacing check) is NOT verified — add only if a real design case needs it.
    ponytail: the §9.6.3.1 exemption (stirrups may be omitted when
    V_u ≤ 0.5·φ·V_c) is folded into "min stirrups" — conservative (more steel),
    never unsafe; add the exemption if cost fidelity ever matters.

    Returns {"feasible": bool, "reason": str|None, "optimum": dict|None,
    "ranked": [design dict × ≤5]}. Each design dict: b, h, d, f_c, long_bar
    ("D19 × 3"), phi_long, n_bars, A_s_mm2, stirrup_bar, A_v_mm2, s, s_max,
    V_c_kN, V_s_kN, phiV_n_kN, phiM_n_kNm, phi, utilization, cost,
    cost_concrete, cost_long_steel, cost_stirrup_steel.
    """
    if V_u <= 0 or M_u < 0:
        raise ValueError("design_beam: V_u > 0 and M_u >= 0 required")
    if max_b < B_MIN or max_h < H_MIN:
        return {
            "feasible": False,
            "reason": (
                f"no section in range: max_b ({max_b}) ≥ {B_MIN} and "
                f"max_h ({max_h}) ≥ {H_MIN} required (grid step {STEP} mm)"
            ),
            "optimum": None,
            "ranked": [],
        }

    f_cs = sorted(set(f_c_list or DEFAULT_F_C_LIST))
    rates = dict(DEFAULT_RATE_CONC)
    if rate_conc:
        # JSON/the model send grade keys as strings ("20": 100) — normalize
        # to int grades so the customization actually applies, never silently
        # falls back to the default rate (verified bug 2026-10-08). Non-numeric
        # grade keys are a loud ValueError, not a raw crash.
        try:
            rates.update({int(k): v for k, v in rate_conc.items()})
        except (TypeError, ValueError, OverflowError) as exc:
            raise ValueError(
                f"design_beam: rate_conc keys must be numeric f'c grades, "
                f"got {sorted(map(str, rate_conc))}"
            ) from exc

    # inputs reach here schema-validated (finite numbers), so int() cannot
    # throw; the guard keeps direct callers honest too
    try:
        b_max = int(max_b)
        h_max = int(max_h)
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError(
            f"design_beam: max_b/max_h must be finite numbers, got "
            f"{max_b!r}/{max_h!r}"
        ) from exc

    designs = []
    for b in range(B_MIN, b_max + 1, STEP):
        for h in range(H_MIN, h_max + 1, STEP):
            perimeter = 2 * (b - 2 * cover) + 2 * (h - 2 * cover)
            if perimeter <= 0:
                continue  # cover too big for the section
            for f_c in f_cs:
                av_min = min_shear_reinf(b, f_c, f_yt)  # mm²/m
                for bar_name, (bar_area_1, phi_long) in LONG_BARS.items():
                    d_eff = h - cover - phi_long / 2
                    if d_eff <= 0:
                        continue
                    for n_bars in range(1, 7):
                        a_s = bar_area_1 * n_bars
                        # --- flexure gate ---
                        if a_s < _as_min(b, d_eff, f_c, f_y):
                            continue
                        flex = flex_capacity(b, d=d_eff, A_s=a_s,
                                             f_c=f_c, f_yl=f_y)
                        if flex["phiM_n_kNm"] < M_u:
                            continue
                        # --- shear ---
                        v_c = 0.17 * math.sqrt(f_c) * b * d_eff / 1000  # kN
                        v_s_req = V_u / 0.75 - v_c
                        limit_66 = 0.66 * math.sqrt(f_c) * b * d_eff / 1000
                        if v_s_req > limit_66:
                            continue  # §22.5.1.2 cap

                        stirrup = _pick_stirrups(
                            b, d_eff, f_c, f_yt, av_min, v_s_req,
                        )
                        if stirrup is None:
                            continue

                        v_s = stirrup["v_s_kN"]
                        phiV_n = 0.75 * (v_c + v_s)
                        cost_conc = rates[f_c] * b * h / 1e6
                        cost_long = rate_steel * RHO_STEEL * a_s / 1e6
                        cost_stir = rate_steel * RHO_STEEL * stirrup["av"] * perimeter \
                            / (stirrup["s"] * 1e6)
                        cost = cost_conc + cost_long + cost_stir

                        designs.append({
                            "b": b, "h": h, "d": d_eff, "f_c": f_c,
                            "long_bar": f"{bar_name} × {n_bars}",
                            "phi_long": phi_long, "n_bars": n_bars,
                            "A_s_mm2": a_s,
                            "stirrup_bar": stirrup["name"],
                            "A_v_mm2": stirrup["av"],
                            "s": stirrup["s"], "s_max": stirrup["s_max"],
                            "V_c_kN": v_c, "V_s_kN": v_s, "phiV_n_kN": phiV_n,
                            "phiM_n_kNm": flex["phiM_n_kNm"], "phi": flex["phi"],
                            "utilization": max(M_u / flex["phiM_n_kNm"],
                                               V_u / phiV_n),
                            "cost": cost, "cost_concrete": cost_conc,
                            "cost_long_steel": cost_long,
                            "cost_stirrup_steel": cost_stir,
                        })

    if not designs:
        return {
            "feasible": False,
            "reason": (
                f"no feasible design for V_u={V_u} kN, M_u={M_u} kN·m within "
                f"max_b={max_b} × max_h={max_h}, f'c in {f_cs} "
                f"(V_s caps at 0.66·√f'c·b·d; increase the section bounds, "
                f"f'c, or add longitudinal bars)"
            ),
            "optimum": None,
            "ranked": [],
        }

    designs.sort(key=lambda row: row["cost"])
    return {"feasible": True, "reason": None, "optimum": designs[0],
            "ranked": designs[:5]}


def _pick_stirrups(b, d_eff, f_c, f_yt, av_min, v_s_req):
    """Pick the cheapest-consistent stirrup bar/spacing for one combo.

    Returns {"name", "av", "s", "s_max", "v_s_kN"} or None when no bar
    fits. The A_v ≥ Av,min·s/1000 requirement is folded in as a maximum s
    (s ≤ A_v·1000/Av,min), so the two constraints never disagree.
    """
    v_c_cap = 0.33 * math.sqrt(f_c) * b * d_eff / 1000
    s_max = min(d_eff / 2, 600) if v_s_req <= v_c_cap else min(d_eff / 4, 300)

    for name, av in STIRRUP_BARS.items():
        s_av_lim = av * 1000 / av_min  # max s that still meets A_v ≥ Av,min·s/1000
        if v_s_req <= 0:
            s = min(s_max, s_av_lim)  # minimum stirrups: widest legal spacing
        else:
            s = av * f_yt * d_eff / (v_s_req * 1000)  # exact demand spacing
            if s > s_max:
                # demand spacing exceeds s_max: min stirrups at s_max already
                # give more Vs than needed (s_demand > s_max ⇔ Vs@max ≥ V_s,req) —
                # clamp, don't reject (verified bug 2026-10-08: combos with
                # small positive V_s,req were silently dropped, so the search
                # missed cheaper designs)
                s = s_max
        if s < 100 or s > s_max:
            continue
        if s > s_av_lim:
            continue
        v_s = av * f_yt * d_eff / s / 1000  # kN with the chosen (av, s)
        return {"name": name, "av": av, "s": s, "s_max": s_max, "v_s_kN": v_s}
    return None