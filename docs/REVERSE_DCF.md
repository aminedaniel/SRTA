# Reverse DCF expectations

The offline module projects annual revenue by applying the supplied growth path, then calculates free cash flow as projected revenue times the supplied FCF-margin path. Each annual FCF is discounted at `(1 + discount rate)^year`. Terminal value is `final-year FCF × (1 + terminal growth) / (discount rate - terminal growth)` and is discounted back to present value. Enterprise value is explicit FCF PV plus terminal-value PV; equity value adds cash and subtracts debt; per-share value divides equity by forecast diluted shares.

Negative current FCF is accepted: a scenario may linearly converge from its current negative margin to a positive terminal margin. Dilution compounds each forecast year before the per-share calculation. Inputs carry provenance and availability timestamps and any future-dated input is rejected.

Reverse solvers use deterministic bisection with explicit bounds; an unbracketed or nonconvergent target reports diagnostics rather than an extreme answer. Terminal values can dominate DCFs, so the signal reduces confidence when their share is high. These results are scenarios and expectations comparisons, not precise price targets.
