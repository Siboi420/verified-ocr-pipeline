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


def min_shear_reinf(b_w, f_c, f_yt):
    """Minimum shear reinforcement Av,min per metre of beam, mm²/m.

    ACI 318M-19 §9.6.3.3 (identical to 318-14):
      Av,min/m = max(0.062·sqrt(f'c)·b_w/f_yt, 0.35·b_w/f_yt) · 1000
    """
    term1 = 0.062 * math.sqrt(f_c) * b_w / f_yt
    term2 = 0.35 * b_w / f_yt
    return max(term1, term2) * 1000  # mm²/m


def shear_capacity(b, d, f_c, A_v=0.0, s=0.0, f_yw=0.0,
                   A_s=None, V_u=None, M_u=None):
    """Shear capacity, ACI 318-19 §22.5.5.1(a)/(b) + §22.5.8.5.3.

    Only if A_s AND V_u AND M_u are all given (M_u > 0) does it use the
    detailed V_c (Eq. 22.5.5.1(b)), capped at 0.29·λ·√f'c·b·d; otherwise the
    simplified V_c (Eq. 22.5.5.1(a)). The 318-19 size-effect term Eq (c) is
    NOT implemented. λ = 1.0. Returns {V_c_kN, V_s_kN, V_n_kN, phiV_n_kN,
    phi_v}.
    """
    lambda_factor = 1.0  # normal weight concrete
    phi_v = 0.75

    if A_s is not None and V_u is not None and M_u is not None and M_u > 0:
        # Detailed, ACI 318-19 Eq. 22.5.5.1(b)
        rho_w = A_s / (b * d)
        vu_d_over_mu = min(abs(V_u) * d / (abs(M_u) * 1000), 1.0)
        V_c = (0.16 * lambda_factor * math.sqrt(f_c) + 17 * rho_w * vu_d_over_mu) * b * d / 1000
        V_c_max = 0.29 * lambda_factor * math.sqrt(f_c) * b * d / 1000
        V_c = min(V_c, V_c_max)
    else:
        # Simplified, ACI 318-19 Eq. 22.5.5.1(a)
        V_c = 0.17 * lambda_factor * math.sqrt(f_c) * b * d / 1000  # kN

    # Steel contribution, ACI 318-19 Eq. 22.5.8.5.3
    if A_v > 0 and s > 0 and f_yw > 0:
        V_s = A_v * f_yw * d / s / 1000  # kN
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
    }


def flex_capacity(b, d, A_s, f_c, f_yl):
    """Flexural capacity, singly reinforced rectangular section.

    a = A_s·f_y/(0.85·f'c·b); c = a/β₁; ε_t = 0.003·(d−c)/c;
    M_n = A_s·f_y·(d−a/2); φ = _phi(ε_t, ε_y). Assumes tension steel yields.
    ACI 318-19 §22.2.2.1 (stress block), §22.2.2.4.3 (β₁), Table 21.2.2 (φ).
    Returns {M_n_kNm, phiM_n_kNm, a_mm, c_mm, beta_1, epsilon_t, phi,
    A_s_mm2}. All units SI (MPa, mm, mm², kN·m).
    """
    epsilon_y = f_yl / ES
    beta_1 = _beta_1(f_c)

    a = A_s * f_yl / (0.85 * f_c * b)
    c = a / beta_1
    epsilon_t = EPSILON_CU * (d - c) / c
    M_n = A_s * f_yl * (d - a / 2)  # N-mm
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