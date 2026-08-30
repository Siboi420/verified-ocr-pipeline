# OCR: testOCR7page.pdf


--- Page 1 ---

CHAPTER 21—STRENGTH REDUCTION FACTORS CODE

COMMENTARY

21.1—Scope
21.1.1 This chapter shall apply to the selection of strength reduction factors used in design, except as permitted by Chapter 27.

21.2—Strength reduction factors for structural concrete members and connections
21.2.1 Strength reduction factors $\phi$ shall be in accordance with Table 21.2.1, except as modified by 21.2.2, 21.2.3, and 21.2.4.

Table 21.2.1—Strength reduction factors $\phi$

| Action or structural element | $\phi$ | Exceptions |
| :--- | :--- | :--- |
| (a) Moment, axial force, or combined moment and axial force | 0.65 to 0.90 in accordance with 21.2.2 | Near ends of pretensioned members where strands are not fully developed, $\phi$ shall be in accordance with 21.2.3. |
| (b) Shear | 0.75 | Additional requirements are given in 21.2.4 for structures designed to resist earthquake effects. |
| (c) Torsion | 0.75 | — |
| (d) Bearing | 0.65 | — |
| (e) Post-tensioned anchorage zones | 0.85 | — |
| (f) Brackets and corbels | 0.75 | — |
| (g) Struts, ties, nodal zones, and bearing areas designed in accordance with strut-and-tie method in Chapter 23 | 0.75 | — |
| (h) Components of connections of precast members controlled by yielding of steel elements in tension | 0.90 | — |
| (i) Plain concrete elements | 0.60 | — |
| (j) Anchors in concrete elements | 0.45 to 0.75 in accordance with Chapter 17 | — |

21.2.2 Strength reduction factor for moment, axial force, or combined moment and axial force shall be in accordance with Table 21.2.2.

R21.1—Scope
R21.1.1 The purposes of strength reduction factors $\phi$ are:
(1) to account for the probability of under-strength members due to variations in material strengths and dimensions; (2) to account for inaccuracies in the design equations; (3) to reflect the available ductility and required reliability of the member under the load effects being considered; and (4) to reflect the importance of the member in the structure (MacGregor 1976; Winter 1979).

R21.2—Strength reduction factors for structural concrete members and connections
R21.2.1 The strength reduction factors in this Code are compatible with the ASCE/SEI 7 load combinations, which are the basis for the required factored load combinations in Chapter 5:

(e) Laboratory tests of post-tensioned anchorage zones (Breen et al. 1994) indicate a wide range of scatter in the results. This observation is addressed with a $\phi$-factor of 0.85 and by limiting the nominal compressive strength of unconfined concrete in the general zone to $0.7\lambda f_{ci}'$ in 25.9.4.5.2, where $\lambda$ is defined in 19.2.4. Thus, the effective design strength of unconfined concrete is $0.85 \times 0.7\lambda f_{ci}' = 0.6\lambda f_{ci}'$ in the general zone.

(f) Bracket and corbel behavior is predominantly controlled by shear; therefore, a single value of $\phi = 0.75$ is used for all potential modes of failure.

(i) The strength reduction factor $\phi$ for plain concrete members is the same for all potential modes of failure. Because both the flexural tension strength and shear strength for plain concrete depend on the tensile strength of the concrete, without the reserve strength or ductility that might otherwise be provided by reinforcement, equal strength reduction factors for moment and shear are considered to be appropriate.

R21.2.2 The nominal strength of a member that is subjected to moment or combined moment and axial force is determined for the condition where the strain in the extreme compression fiber is equal to the assumed strain limit of 0.003. The net tensile strain $\varepsilon_t$ is the tensile strain calculated in the extreme tension reinforcement at nominal strength,

--- Page 2 ---

CODE

21.2.2.1 For deformed reinforcement, $\varepsilon_{ty}$ shall be $f_y/E_s$. For Grade 420 deformed reinforcement, it shall be permitted to take $\varepsilon_{ty}$ equal to 0.002.

21.2.2.2 For all prestressed reinforcement, $\varepsilon_{ty}$ shall be taken as 0.002.

COMMENTARY

The net tensile strain in the extreme tension reinforcement is determined from a linear strain distribution at nominal strength, shown in Fig. R21.2.2a for a nonprestressed member.

Members subjected to only axial compression are considered to be compression-controlled and members subjected to only axial tension are considered to be tension-controlled.

If the net tensile strain in the extreme tension reinforcement is sufficiently large ($\geq \varepsilon_{ty} + 0.003$), the section is defined as tension-controlled, for which warning of failure by excessive deflection and cracking may be expected. The limit $\geq \varepsilon_{ty} + 0.003$ provides sufficient ductility for most applications. Before the 2019 Code, the tension-controlled limit on $\varepsilon_t$ was defined as 0.005 established primarily on the basis of Grade 420 nonprestressed reinforcement and prestressed reinforcement, with some consideration given to higher grades of nonprestressed reinforcement (Mast 1992). Beginning with the 2019 Code, to accommodate nonprestressed reinforcement of higher grades, the tension-controlled limit on $\varepsilon_t$ in Table 21.2.2 is defined as $\varepsilon_{ty} + 0.003$. This expression is consistent with the recommendations of Mast (1992) for the general case of reinforcement other than Grade 420 and test data show that the expression leads to elements with adequate ductility.

One condition where greater ductile behavior is required is in design for redistribution of moments in continuous members and frames, which is addressed in 6.6.5. Because redistribution of moment depends on the ductility available in the hinge regions, redistribution of moment is limited to sections that have a net tensile strain of at least 0.0075.

If the net tensile strain in the extreme tension reinforcement is small ($\leq \varepsilon_{ty}$), a brittle compression failure condition is expected, with little warning of impending failure. Before ACI 318M-14, the compression-controlled strain limit was defined as 0.002 for Grade 420 reinforcement and all prestressed reinforcement, but it was not explicitly defined for other types of reinforcement. The compression-controlled strain limit $\varepsilon_{ty}$ is defined in 21.2.2.1 and 21.2.2.2 for deformed and prestressed reinforcement, respectively.

Beams and slabs are usually tension-controlled, whereas columns may be compression-controlled. Some members, such as those with small axial forces and large bending moments, experience net tensile strain in the extreme tension reinforcement between the limits of $\varepsilon_{ty}$ and ($\varepsilon_{ty} + 0.003$). These sections are in a transition region between compression-controlled and tension-controlled.

This section specifies the appropriate strength reduction factors for tension-controlled and compression-controlled sections, and for intermediate cases in the transition region. Beginning with the 2019 Code, the expression ($\varepsilon_{ty} + 0.003$) defines the limit on $\varepsilon_t$ for tension-controlled behavior in Table 21.2.2.2. For sections subjected to combined axial force and moment, design strengths are determined by multiplying both $P_n$ and $M_n$ by the appropriate single value of $\phi$.

--- Page 3 ---

CODE

COMMENTARY

A lower $\phi$-factor is used for compression-controlled sections than for tension-controlled sections because compression-controlled sections have less ductility, are more sensitive to variations in concrete strength, and generally occur in members that support larger loaded areas than members with tension-controlled sections. Columns with spiral reinforcement are assigned a higher $\phi$-factor than columns with other types of transverse reinforcement because spiral columns have greater ductility and toughness. For sections within the transition region, the value of $\phi$ may be determined by linear interpolation, as shown in Fig. R21.2.2b.

Table 21.2.2—Strength reduction factor $\phi$ for moment, axial force, or combined moment and axial force

| Net tensile stain $\varepsilon_t$ | Classification | $\phi$ |
| :--- | :--- | :--- |
| $\varepsilon_t \leq \varepsilon_{ty}$ | Compression-controlled | 0.75 (a) |
| $\varepsilon_{ty} < \varepsilon_t < \varepsilon_{ty} + 0.003$ | Transition$^{[1]}$ | $0.75 + 0.15 \frac{(\varepsilon_t - \varepsilon_{ty})}{(0.003)}$ (c) |
| $\varepsilon_t \geq \varepsilon_{ty} + 0.003$ | Tension-controlled | 0.90 (e) |

$[1]$For sections classified as transition, it shall be permitted to use $\phi$ corresponding to compression-controlled sections.

Fig. R21.2.2a—Strain distribution and net tensile strain in a nonprestressed member.

--- Page 4 ---

21.2.3 For sections in pretensioned flexural members where all strands are not fully developed, $\phi$ for moment shall be calculated in accordance with Table 21.2.3, where $\ell_{tr}$ is calculated using Eq. (21.2.3), $\phi_p$ is the value of $\phi$ determined in accordance with Table 21.2.2 at the cross section closest to the end of member where all strands are developed, and $\ell_d$ is given in 25.4.8.1.

$$\ell_{tr} = \left( \frac{f_{se}}{21} \right) d_b$$

(21.2.3)

Table 21.2.3—Strength reduction factor $\phi$ for sections near the end of pretensioned members

| Condition near end of member | Stress in concrete under service load[1] | Distance from end of member to section under consideration | $\phi$ |
| :--- | :--- | :--- | :--- |
| All strands bonded | Not applicable | $\leq \ell_{tr}$ | 0.75 |
| All strands bonded | Not applicable | $\ell_{tr}$ to $\ell_d$ | Linear interpolation from 0.75 to $\phi_p$[2] |
| One or more strands debonded | No tension calculated | $\leq (\ell_{db} + \ell_{tr})$ | 0.75 |
| One or more strands debonded | No tension calculated | $(\ell_{db} + \ell_{tr})$ to $(\ell_{db} + \ell_d)$ | Linear interpolation from 0.75 to $\phi_p$[2] |
| One or more strands debonded | Tension calculated | $\leq (\ell_{db} + \ell_{tr})$ | 0.75 |
| One or more strands debonded | Tension calculated | $(\ell_{db} + \ell_{tr})$ to $(\ell_{db} + 2\ell_d)$ | Linear interpolation from 0.75 to $\phi_p$[2] |

[1]Stress calculated using gross cross-sectional properties in extreme concrete fiber of precompressed tension zone under service loads after allowance for all prestress losses at section under consideration.

[2]It shall be permitted to use a strength reduction factor of 0.75.

R21.2.3 If a critical section along a pretensioned member occurs in a region where not all the strands are fully developed, bond slip failure may occur. This mode of failure resembles a brittle shear failure; hence, $\phi$ values for flexure are reduced relative to the value of $\phi$ at the cross section where all strands are fully developed. For sections between the end of the transfer length and the end of the development length, the value of $\phi$ may be determined by linear interpolation, as shown in Fig. R21.2.3a, where $\phi_p$ corresponds to the value of $\phi$ at the cross section closest to the end of the member where all strands are fully developed.

Where bonding of one or more strands does not extend to the end of the member, instead of more rigorous analysis, $\phi$ should be taken as 0.75 from the end of the member to the end of the transfer length of the strand with the longest debonded length. Beyond this point, $\phi$ may be varied linearly to $\phi_p$ at the cross section where all strands are developed, as shown in Fig. R21.2.3b. Alternatively, the value of $\phi$ may be taken as 0.75 until all strands are fully developed. Embedment of debonded strand is considered to begin at the termination of the debonding sleeves. Beyond this point, the provisions of 25.4.8.1 are used to determine whether the strands develop over a length of $\ell_d$ or $2\ell_d$, depending on the calculated stress in the precompressed tension zone under service loads (Fig. R21.2.3b).

--- Page 5 ---

21.2.4 For structures that rely on elements in (a), (b), or (c) to resist earthquake effects $E$, the value of $\phi$ for shear shall be modified in accordance with 21.2.4.1 through 21.2.4.4:

(a) Special moment frames
(b) Special structural walls
(c) Intermediate precast structural walls in structures assigned to Seismic Design Category D, E, or F

**Commentary**

Fig. R21.2.3a—Variation of $\phi$ with distance from the free end of strand in pretensioned member with fully bonded strands.

Note: The location of the end of development length depends on the calculated stresses in the extreme concrete fiber of the precompressed tension zone under service loads.

Fig. R21.2.3b—Variation of $\phi$ with distance from the free end of strand in pretensioned member with debonded strands.

--- Page 6 ---

## CODE

21.2.4.1 For any member designed to resist $E$, $\phi$ for shear shall be 0.60 if the nominal shear strength of the member is less than the shear corresponding to the development of the nominal moment strength of the member. The nominal moment strength shall be the maximum value calculated considering factored axial loads from load combinations that include $E$.

21.2.4.2 For diaphragms, $\phi$ for shear shall not exceed the least value of $\phi$ for shear used for the vertical components of the primary seismic-force-resisting system.

21.2.4.3 For foundation elements supporting the primary seismic-force-resisting system, $\phi$ for shear shall not exceed the least value of $\phi$ for shear used for the vertical components of the primary seismic-force-resisting system.

21.2.4.4 For beam-column joints of special moment frames and diagonally reinforced coupling beams, $\phi$ for shear shall be 0.85.

## COMMENTARY

R21.2.4.1 This provision addresses shear-controlled members, such as low-rise walls, portions of walls between openings, or diaphragms, for which nominal shear strength is less than the shear corresponding to development of nominal flexural strength for the pertinent loading conditions.

R21.2.4.2 Short structural walls were the primary vertical elements of the lateral-force-resisting system in many of the parking structures that sustained damage during the 1994 Northridge earthquake. In some cases, walls remained essentially linear elastic, while diaphragms responded inelastically. This provision is intended to increase strength of the diaphragm and its connections in buildings for which the shear strength reduction factor for walls is 0.60, as those structures tend to have relatively high overstrength.

R21.2.4.3 This provision is intended to provide consistent reliability for shear in foundation elements that support shear-controlled walls designed with a strength reduction factor of 0.6.

--- Page 7 ---

CHAPTER 22—SECTIONAL STRENGTH CODE COMMENTARY

22.1—Scope
22.1.1 This chapter shall apply to calculating nominal strength at sections of members, including (a) through (g):

(a) Flexural strength
(b) Axial strength or combined flexural and axial strength
(c) One-way shear strength
(d) Two-way shear strength
(e) Torsional strength
(f) Bearing
(g) Shear friction

22.1.2 Sectional strength requirements of this chapter shall be satisfied unless the member or region of the member is designed in accordance with Chapter 23.

22.1.3 Design strength at a section shall be taken as the nominal strength multiplied by the applicable strength reduction factor $\phi$ given in Chapter 21.

22.2—Design assumptions for moment and axial strength
22.2.1 Equilibrium and strain compatibility
22.2.1.1 Equilibrium shall be satisfied at each section.

22.2.1.2 Strain in concrete and nonprestressed reinforcement shall be assumed proportional to the distance from neutral axis.

22.2.1.3 Strain in prestressed concrete and in bonded and unbonded prestressed reinforcement shall include the strain due to effective prestress.

22.2.1.4 Changes in strain for bonded prestressed reinforcement shall be assumed proportional to the distance from neutral axis.

R22.1—Scope
22.2.1.1 The provisions in this chapter apply where the strength of the member is evaluated at critical sections.

R22.1.2 Chapter 23 provides methods for designing discontinuity regions where section-based methods do not apply.

R22.2—Design assumptions for moment and axial strength
22.2.2.1 Equilibrium and strain compatibility
The flexural and axial strength of a member calculated by the strength design method of the Code requires that two basic conditions be satisfied: 1) equilibrium; and 2) compatibility of strains. Equilibrium refers to the balancing of forces acting on the cross section at nominal strength. The relationship between the stress and strain for the concrete and the reinforcement at nominal strength is established within the design assumptions allowed by 22.2.

R22.2.1.2 It is reasonable to assume a linear distribution of strain across a reinforced concrete cross section (plane sections remain plane), even near nominal strength except in cases as described in Chapter 23.

The strain in both nonprestressed reinforcement and in concrete is assumed to be directly proportional to the distance from the neutral axis. This assumption is of primary importance in design for determining the strain and corresponding stress in the reinforcement.

R22.2.1.4 The change in strain for bonded prestressed reinforcement is influenced by the change in strain at the section under consideration. For unbonded prestressed reinforcement, the change in strain is influenced by external load, reinforcement location, and boundary conditions along the length of the reinforcement. Current Code equations for calculating $f_{ps}$ for unbonded tendons, as provided in 20.3.2.4, have been correlated with test results.