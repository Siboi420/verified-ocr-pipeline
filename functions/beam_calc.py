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