**Model** (pk model definition)
- `canonical_name`: string [canonical] — unique model identifier → **`{drug}_{descriptor}_{type}` format, lowercase** (e.g., `"adalimumab_popPK_two_compartment"`, `"warfarin_elderly_one_compartment"`)
- `mathematical_equations`: string[] — core PK equation(s) used in the model (e.g., `["dC/dt = -(CL/V) * C", "C(t) = (Dose/V) * exp(-(CL/V)*t)"]`)
- `parameter_means`: object — typical/population parameter values → **serialize as JSON string** (e.g., `'{"CL": 12.5, "V": 45.0, "Ka": 1.2}'`)
- `parameter_iiv_std_dev`: object — inter-individual variability (IIV) → **serialize as JSON string** (e.g., `'{"CL_iiv_cv%": 28, "V_iiv_cv%": 22}'`)

**Type** (model structure category)
- `canonical_name`: string [canonical] — model type → **normalize to snake_case** (e.g., `"one_compartment"`, `"two_compartment"`, `"nonlinear_michaelis_menten"`)
- `model_type`: string — same as canonical_name (e.g., `"one_compartment"`, `"two_compartment"`)

**Drug** (drug identity and class)
- `canonical_name`: string [canonical] — INN/generic drug name → **normalize to INN/generic, lowercase** (e.g., `"warfarin"`, `"adalimumab"`)
- `drug_name`: string — same as canonical_name (e.g., `"warfarin"`, `"vancomycin"`, `"adalimumab"`)
- `drug_type`: string — drug modality → **normalize to snake_case** (e.g., `"small_molecule"`, `"monoclonal_antibody"`, `"antibiotic"`)
- `aliases`: string[] — alternative names, brand names, abbreviations (e.g., `["humira", "coumadin"]`)

**Organism** (species context)
- `canonical_name`: string [canonical] — species → **normalize to lowercase common name** (e.g., `"human"`, `"rat"`, `"mouse"`)
- `organism`: string — same as canonical_name (e.g., `"human"`, `"rat"`, `"mouse"`)

**Disease** (disease or indication)
- `canonical_name`: string [canonical] — disease name → **normalize to snake_case** (e.g., `"hypercholesterolemia"`, `"rheumatoid_arthritis"`)
- `name`: string — human-readable disease name (e.g., `"hypercholesterolemia"`, `"rheumatoid arthritis"`)
- `aliases`: string[] — abbreviations and alternative names (e.g., `["RA", "ra"]`)

---

### Relationships

- `(Model)-[:IS_TYPE]->(Type)` — links a PK model to its structural category — **one-to-one**
- `(Model)-[:CHARACTERIZES]->(Drug)` — the model describes the PK of this drug — **many-to-one**
- `(Model)-[:STUDIED_IN]->(Organism)` — the species used in the PK study — **many-to-many**
- `(Model)-[:TREATS]->(Disease)` — the therapeutic indication studied — **many-to-many**

