All critical claims now verified against the actual Snowywood source. The findings confirm and sharpen the agent data in several important ways:

- **`show_character_previews(mutable_appearance/MA)`** is hardwired to `"character_preview_map"` (line 1166/1183), NOT a parameterized map key, and uses `screen` + `char_render_holders` (HUD screen objects), not a TGUI `ByondUi` map. This is a bigger preview gap than the agent stated.
- **`get_pref_data` / `cached_accessory_names` confirmed ABSENT** from SW's `customizer_choice.dm` (zero output).
- **Origin confirmed `/datum/origin` model**, zero `virtue_origin`/`uniquefaith` — confirming the highest-risk divergence.
- **`base_name`/`sub_name`/`is_subrace` confirmed absent** from SW species — subspecies UI would be broken.
- **`build_class_explain_html` absent**; SW has `class_setup_examine` flag instead.
- **`fam_process_link`/`gnoll_process_link` confirmed to lack the `from_tgui` param** (signature is `(mob/user, list/href_list)`).
- **`ShowChoices` confirmed still routes to legacy HTML** (`?_src_=prefs` hrefs); `tgui_pref` is just a stored toggle with a `tgui_ui_prefs` Topic, NOT wired to a TGUI window.

I now have everything needed. Producing the assessment.

---

# Snowywood ← Emerald-Summit TGUI Character-Creation Port: Engineering Assessment

## 1. What is what, and why the port direction is backward

There are two forks of the same Roguetown-lineage SS13/BYOND game.

- **Snowywood (`/home/blob/Snowywood`)** is the **target**. It is ~6 months **ahead on chargen mechanics** but its character-creation UI is still the **old `browse()`/HTML** system: a monolithic `code/modules/client/vices_menu.dm` (~1890 lines) renders one giant HTML page covering vices/virtues, languages, and loadout; `language_menu.dm` and `loadout_menu.dm` are thin redirects into it. Entry is `client.prefs.ShowChoices(user, tab)` which **always builds HTML** (verified: line 455+, emits `?_src_=prefs;...` hrefs). `tgui_pref` exists (line 369, default TRUE) but is only a stored toggle exposed via a `tgui_ui_prefs` Topic — it is **not wired to any TGUI window** (no `/datum/preferences_menu`, no `open_preferences_menu` — both confirmed absent).
- **Emerald-Summit (`/tmp/Emerald-Summit`)** is the **source**. It is ~6 months **behind on mechanics** but has the **finished modernization we want**: a unified TGUI React `PreferencesMenu` (`code/modules/client/preferences_menu/preferences_menu.dm`, ~4287 lines + 17 `.tsx` files) with tabs (Identity/Features/Jobs/Loadout/Flavor/GamePrefs/Keybinds/Familiar/Gnoll). In ES, `ShowChoices` does `if(tgui_pref) open_preferences_menu(user)` and only falls back to HTML otherwise.

**Why port backward (ES → SW):** the *UI rewrite* is the expensive, finished asset and it lives only on the behind fork; the *newer mechanics* are the cheap-to-keep asset and live only on the ahead fork. So we lift ES's TGUI scaffold onto Snowywood and **re-wire / extend** it to drive Snowywood's richer mechanics — rather than re-implementing the whole React menu from scratch on the ahead fork.

**The load-bearing architectural fact** (confirmed across the customizer/jobs/familiar agents): ES did **not** rewrite the chargen *backend datums*. It added a parallel TGUI **read layer** (`build_*_static` / `build_*_dynamic` procs) and a TGUI **write layer** (`ui_act` Topic handlers) that *call the same classic procs* (`handle_customizer_topic`, `SetJobPreferenceLevel`, `validate_*_preferences`, `fam_process_link`, …). Most of those classic procs already exist in Snowywood, often **identical**. So the port is mostly: bring over ES's `preferences_menu.dm` + `.tsx`, **keep Snowywood's richer catalogs/datums**, and add the handful of TGUI-serialization hooks ES added that SW lacks — plus build new UI for the mechanics ES never had.

The hard part is **not** plumbing. It is the **mechanics delta** and a few **structural schema mismatches** where ES's builders read fields that don't exist on Snowywood's datums.

---

## 2. File / surface inventory of the port

### 2a. COPY into Snowywood (net-new, mostly drop-in)

**Frontend — `tgui/packages/tgui/interfaces/` (ALL absent in SW):**

| File | Notes |
|---|---|
| `common/SearchableDropdown.tsx` | **PORT FIRST.** Every tab imports it as `Dropdown`. SW `common/` confirmed has only `InputButtons/Loader/LoadingScreen`. Zero new CSS/assets (reuses already-bundled `Dropdown.scss` via `@use '~tgui-core/styles'`). |
| `PreferencesMenu.tsx` | Entry component; auto-registered by `routes.tsx` `require.context('./interfaces')` — exported name `PreferencesMenu` must match `ui_interact` interface name. Rebrand `"Emerald Summit"` title → Snowywood. |
| `PreferencesMenu/` (13 files) | `IdentityTab, BodySection, MarkingsSection, FeaturesTab, CustomizerCard, JobsTab, LoadoutTab, FlavorTab, GamePrefsTab, OocPrefsTab, KeybindsTab, FamiliarTab, GnollTab` |
| `LateJoinChoices.tsx` | TGUI late-join job picker (ES-only). |

> Registration is **automatic** — no `routes.tsx`/index edits. SW already ships `tgui/packages/tgui/styles/interfaces/PreferencesMenu.scss` (stylesheet pre-staged even though the tsx are absent). tgui-core versions match (ES `^4.2.3` / SW resolved `4.3.3`); every component used (`Box/Button/ByondUi/Slider/Table/Tabs/LabeledList/Section/Stack/Popper/Icon/Input`) + `Window canClose` prop already exist in SW. **Smoke-test `Slider/Popper/Dropdown` after `yarn build`** for the minor version drift.

**Backend — `code/modules/client/`:**

| File | Notes |
|---|---|
| `preferences_menu/preferences_menu.dm` (~4287 lines) | **The heart.** `/datum/preferences_menu` + `ui_interact/ui_static_data/ui_data/ui_act` + all `build_*` + `/datum/preferences/proc/open_preferences_menu`. **Cannot be copied verbatim** — heavy DM-side rewrites required (Sections 3–4). |
| `code/modules/mob/dead/new_player/late_join_choices.dm` | `/datum/late_join_choices` backing `LateJoinChoices.tsx`. |

**`.dme` additions:** `#include` both new DM files (ES `.dme` lines 1443 and 1884).

### 2b. EDIT in Snowywood (hand-merge, NOT clean apply)

| File | Change |
|---|---|
| `code/modules/client/preferences.dm` | Add `var/datum/preferences_menu/preferences_menu`; route `ShowChoices` to `open_preferences_menu(user)` when `tgui_pref` (currently always HTML, line 455+); add the `preference=preferences_menu;task=open` Topic branch. **~1920/3419 lines differ from ES — graft intent only; do NOT clobber SW's point procs (145–186), vice vars (239–243), `extra_language_1/_2` (102–103).** |
| `code/modules/client/preferences_savefile.dm` | **Touch only to add new keys** (gnoll flavor/OOC if adopting ES gnoll). **KEEP SW `MIN18/MAX38` (verified lines 2/10) — never import ES `MIN33/MAX36`.** |
| `code/modules/mob/dead/new_player/new_player.dm` | Add `var/datum/late_join_choices` + the two `SStgui.close_uis(...)` calls in `close_spawn_windows()`. |
| `code/modules/client/customizer/customizer_choice.dm` | **Add `get_pref_data()` + `cached_accessory_names` + `picked_name` handling in `handle_topic`** (confirmed absent in SW). |
| `code/modules/client/familiar_prefs.dm` / `gnoll_prefs.dm` | Add `from_tgui` param + `picked_name` handling + guard `*_show_ui()` (signatures confirmed `(mob/user, list/href_list)` — no `from_tgui`). |
| `client_procs.dm` `show_character_previews` | **See §4 — must be reworked for the TGUI preview map.** |

**No work needed** (effectively identical across repos): `preferences_tgui.dm` (only theme-name string differs), `preferences_culinary.dm` (byte-identical), `preferences_body_markings.dm` (identical), `descriptor_entry.dm`/`custom_descriptor_entry.dm` (identical).

### 2c. DELETE in Snowywood — **⚠ DANGEROUS, do NOT delete blind**

ES deleted these 6 files + their `.dme` includes. **A naive port that deletes them strips player-facing controls for Snowywood-only mechanics that have NO home in ES's TGUI yet:**

| File | Danger |
|---|---|
| `code/modules/client/vices_menu.dm` (66 KB) | **HIGHEST DANGER.** Sole editor for **vice1–vice5** (confirmed present), the **virtue/vice conflict engine**, **presets/undo**, **paid triumph languages**, and the **loadout point economy**. Deleting it with no TGUI replacement removes those controls entirely. |
| `code/modules/client/loadoutmenu/loadout_menu.dm` | SW's intermediate TGUI loadout using `get_total_points()` — encodes the **triumph-cost economy**. |
| `code/modules/client/loadout_menu.dm` | Legacy HTML loadout (rename/describe/color, 10 slots). |
| `code/modules/client/language_menu.dm` | Client-side HTML language picker (the **2 paid triumph-language slots**). *(Distinct from `code/modules/language/language_menu.dm` — keep that one.)* |
| `code/modules/client/client_topic_lang.dm` | Lang tooltip renderer. |
| `code/modules/client/manual_donator.dm` | **Donator allowlist** (`is_donator`, `data/donators.db`, admin verbs) — **Snowywood-only, ES has zero references.** Gates donator-only chargen options. **Keep it** — no TGUI surface to port, but its gate must be replicated in the new builders. |

**Rule: re-host each file's mechanics inside the new `preferences_menu.dm` `ui_act`/`build_*` handlers FIRST, then delete.** Until then the port "compiles but silently drops mechanics."

---

## 3. The GAP TABLE — Snowywood-only / newer mechanics ES's TGUI does NOT cover

Grouped by subsystem. `es_handling`: **absent** = no scaffolding at all; **partial** = covers some of it; **different** = incompatible implementation; **covered** = data-driven, ports for free.

### A. Vices / charflaws / point economy — *the largest gap*

| Mechanic (SW file) | es_handling | Port action | Risk | Effort |
|---|---|---|---|---|
| **Multi-vice slots `vice1..vice5`** (`preferences.dm:239-243`✓, savefile `499-523`, `copy_to:3206-3222`) | **different** (ES = single `charflaw`) | Add 5 slot vars + `S["vice1..5"]` r/w (keep legacy `charflaw` as vice1 fallback for old saves); port `copy_to` vices-list build; replace ES's single `set_charflaw` act with per-slot acts (slot-1-required, dup-prevention); emit 5-slot array in `ui_data`; build a React vice panel. | high | high |
| **Vice-points budget** `get_vice_points()` (+1/vice → `get_total_points`) (`preferences.dm:148-154`✓) | **absent** | Depends on multi-vice landing. Copy `get_vice_points`; wire `get_total_points` into loadout act. | high | high |
| **Base points** `get_base_points()`=10 + `get_total/remaining_points` (`preferences.dm:143-185`✓) | **absent** (ES has zero `get_*_points`) | Copy procs verbatim (pure logic); surface total/spent/remaining in loadout `ui_data`; enforce in loadout act (ES has **no** budget check). | medium | medium |
| **Virtue↔vice / vice↔vice conflict engine** `check_*_conflict` (`vices_menu.dm:1-262`) | **absent** | Port `check_*` procs onto `/datum/preferences` (framework-agnostic); filter candidate list in per-slot act. Depends on multi-vice + dual-virtue. | medium | medium |
| **Vice catalog** `GLOB.character_flaws` (~46 vs ES ~28; `(+1 TRI)`/`(-3 TRI)` label pricing) | **partial** (same plumbing, smaller list) | **Keep SW catalog** — ES `build_charflaw_options()` reads `GLOB.character_flaws` generically, renders larger list unchanged. Pricing is label-suffix + per-flaw `adjust_triumphs()`, no numeric field needed. | low | low |
| **`lawless` flaw** | **different** (ES lists it but the typepath is **undefined/dangling**) | **Keep SW's `lawless.dm`** (only working impl). | low | low |
| **Presets (3 slots) / undo-history / per-loadout custom name** (`vices_menu.dm:265-630,1476-1517`) | **absent** | **Optional/adjacent scope.** Port `save_preset/load_preset/clear_preset` + `save_to_history/undo_last_change` if desired; degrades gracefully if descoped (savefile keys already round-trip). | medium | high |

> `limbloss`, `addiction`, `noflaw/randflaw` are **shared datums** — keep SW's (larger, multi-vice `sate_addiction` logic). Watch path divergence (ES `/datum/charflaw/masochist` vs SW `/datum/charflaw/addiction/masochist`) for savefile string round-trips.

### B. Languages

| Mechanic | es_handling | Port action | Risk | Effort |
|---|---|---|---|---|
| **Free/origin `extra_language`** | **partial** (ES already in IdentityTab) | Reuse ES's IdentityTab dropdown but **rewire gating predicate** from ES `virtue_origin?.extra_language` → SW `origin?.origin_language`; replace ES's 7-entry list with SW's full roster (`vices_menu.dm:1781-1798`). | medium | medium |
| **2 paid triumph-language slots `extra_language_1/_2`** (slot1=2 TRI, slot2=4 TRI, hardcoded) | **absent** | **Build from scratch:** new Language section/tab, per-slot triumph cost + live remaining-triumph counter (`user.get_triumphs()`), select/change/clear acts with affordability guard. Savefile keys exist. | high | high |

### C. Loadout

| Mechanic | es_handling | Port action | Risk | Effort |
|---|---|---|---|---|
| **Item picker (10 slots vs ES 6)** | **different** (ES `LoadoutTab` cleaner; SW standalone `LoadoutMenu.tsx` half-built w/ `triumph_cost=item.desc` bug) | **Adopt ES `LoadoutTab` base; DISCARD SW standalone `LoadoutMenu.tsx`/`datum/loadout_menu`.** Extend 6→10 slots; port item-icon spritesheet. | medium | medium |
| **Loadout point economy** (`triumph_cost`, affordability + duplicate guards, `keep_loadout_stats` nerf-exempt) | **absent** (ES item datum has only name/desc/path/donoritem) | **Keep SW's `modular_azurepeak/code/datums/loadout.dm`** (do NOT overwrite with ES's leaner datum). Add affordability layer ES lacks into `set_loadout_slot_direct`; surface total/spent/remaining; per-item cost labels; dup guard. | high | high |
| **Rename/describe/recolor** (`loadout_N_name/_desc/_hex`, 10 slots) | **partial** (ES = color only, 6 slots, named presets) | Adopt ES's named-color-preset UX (upgrade over raw hex); **re-add rename + describe** controls ES dropped; extend 6→10. | medium | medium |
| **Donator + Nobility gating** | **partial** (ES has donator, **lacks nobility**) | Keep ES's cached owner-ckey donator filter; **add `nobility_check(prefs.parent)` filter** ES lacks (travels with SW's loadout datum). | medium | medium |

### D. Identity / species / origin / virtues / patron

| Mechanic | es_handling | Port action | Risk | Effort |
|---|---|---|---|---|
| **Origin model** SW `/datum/origin` (`GLOB.origins`, 11 origins, `origin_language`, stored as name) vs ES `prefs.virtue_origin` (`/datum/virtue/origin` w/ `uniquefaith`+`extra_language`) — confirmed SW has `var/datum/origin/origin` only, zero `virtue_origin`/`uniquefaith` | **different** (incompatible) | **HIGHEST-FRICTION BRIDGE.** Recommend **option B**: keep SW `/datum/origin`, **rewrite** ES `build_origin_options`/`build_faith_options`/`set_origin`/`set_extra_language` + IdentityTab to read `GLOB.origins`+`prefs.origin`; **stub/strip every `virtue_origin?.uniquefaith` and `virtue_origin?.extra_language` reference.** Bridge touches faith + language too. | high | high |
| **Species / subspecies** — ES uses `base_name/sub_name/is_subrace` grouping; **confirmed ABSENT on all SW species** | **different** | Either backport `base_name/sub_name/is_subrace` onto SW's ~48 species, OR **flatten** ES `build_species_options/build_subspecies_options` + IdentityTab to a flat `GLOB.roundstart_races` list (drop subspecies dropdown). Verify/stub `species_psydonic`/`species_use_titles`. SW-only species (arachnid, gnome, construct family) appear free if flat. | high | high |
| **Virtues** (`virtue`+`virtuetwo`; 21 SW-only virtues) | **partial** (data-driven SET ports free; but ES picker reads `v.restricted`/`v.races` **absent on SW base**) | **Backport `restricted=FALSE`, `races=list()`** (and optionally `extra_language`/`required_virtues`) onto SW `_virtue.dm` so ES `build_virtue_picker_list` doesn't error; reconcile SW species-side `restricted_virtues` with ES virtue-side restriction. `virtuetwo` gate (`statpack=='Virtuous'`) matches. | medium | medium |
| **Patron / faith** (SW-only "Undivided" patron) | **covered** (data-driven over `GLOB.patronlist/faithlist`) | Free SET port; **must neutralize ES `build_faith_options`'s `prefs.virtue_origin?.uniquefaith` branch** (dead/erroring on SW). Confirm "Undivided" has `preference_accessible=TRUE`. | low | low |
| **Statpacks** (SW-only "Enduring") | **covered** | Ports for free (data-driven over `GLOB.statpacks`). Verify `generate_modifier_string` format + Virtuous→virtuetwo gate. | low | low |
| **Age/sex/family/spouse/title** | **partial** (parity except the two language gaps) | Wire acts to existing SW prefs vars; add `extra_language_1/_2` dropdowns. Verify `FAMILY_*`/`gender_choice` constants match. | medium | medium |

### E. Customizers / descriptors / markings / accessories

| Mechanic | es_handling | Port action | Risk | Effort |
|---|---|---|---|---|
| **Customizer core + serialization** (`get_pref_data` confirmed ABSENT on SW) | **covered** (generic router) | **Add ES's `customizer_choice.dm` `get_pref_data` + `cached_accessory_names` + `picked_name`**; keep SW's `preferences_customizers.dm` null-guards. Generic `customizer_action → handle_customizer_topic` works (SW has the proc). | medium | medium |
| **Catalogs** (eyes/genitals/hair etc., SW richer) | **partial** | **Keep SW catalogs** (supersets); merge ES `get_pref_data` overrides into SW's eyes/genitals/hair files. | medium | medium |
| **Wings color/gradient** (SW-only `/datum/customizer_entry/organ/wings` w/ `wings_color`/`natural_gradient`/`dye_gradient`) | **absent** (ES never TGUI-ported wings; the **only** custom-picker customizer with no `get_pref_data`) | **Hand-author a new `get_pref_data` override** mirroring SW's `generate_pref_choices` (color picker + gradient choosers via `hair_gradient_name_to_type_list`); add `picked_name` to gradient choosers. | high | high |
| **Body markings** (SW-only construct_plating/gradient/wolf) | **covered** (`preferences_body_markings.dm` identical) | Adopt ES `MarkingsSection` + builders as-is. **Ensure `construct_plating.dm` is in `.dme`** or markings silently vanish. | low | low |
| **Descriptors** (SW-only `trait.dm` ~70 entries + expanded statures) | **covered** (generic) | Keep SW `descriptor_choice.dm` + catalogs; **ensure `trait.dm` is in `.dme`**; verify species `descriptor_choices` include the `trait` `DESCRIPTOR_CHOICE`. | medium | low |
| **Sprite accessories** (SW supersets; base has `gradient_icon` + `NECK_LAYER`) | **covered** | Keep all SW `sprite_accessory/*` + **keep SW `_sprite_accessory.dm` base** (`gradient_icon` is load-bearing for wings gradient). | low | low |

### F. Jobs / familiar / gnoll / donator

| Mechanic | es_handling | Port action | Risk | Effort |
|---|---|---|---|---|
| **Jobs prefs** (`SetJobPreferenceLevel`/`ResetJobs` — signatures match) | **covered** | Adopt ES `build_jobs_static/dynamic` + `JobsTab`. **`build_jobs_static` must iterate live `SSjob`** so SW-newer jobs appear; reconcile `SetChoices` splitJobs names; verify SW-only job gates. | medium | medium |
| **`build_class_explain_html`** — confirmed ABSENT on SW (`_job.dm` has `class_setup_examine` flag instead) | **different** | Either port the proc or **re-target `show_class_explain` at SW's `class_setup_examine` HTML**. `JobsTab` renders it via `dangerouslySetInnerHTML` (sanitize). | high | medium |
| **Familiar prefs** | **covered** (model identical) | Apply ES `fam_process_link` TGUI variant (add `from_tgui`, `tgui_input_*`, `picked_name`, guard `fam_show_ui`) — **confirmed SW signature lacks `from_tgui`.** | low | low |
| **Gnoll prefs** (ES is the **superset** — adds `gnoll_flavortext`/`gnoll_ooc_notes` + `_display`) | **covered** | Adopt ES `gnoll_prefs.dm` wholesale; **add 4 new savefile keys** or they won't persist. Add `from_tgui` (confirmed absent). | medium | medium |
| **Donator allowlist** (`manual_donator.dm`, `is_donator`) | **absent** (zero ES refs) | **Keep file untouched.** **Audit every `is_donator`/`client.donator` gate** in species/loadout/customizer/job option emission and replicate in the new `build_*_static`. | medium | medium |
| **Culinary** | **covered** (`preferences_culinary.dm` byte-identical) | Adopt ES builders + IdentityTab section verbatim. | low | low |

---

## 4. The hardest problems / conflicts

1. **Origin model mismatch (highest risk).** ES's `set_origin`/`build_origin_options`/`build_faith_options`/`set_extra_language` and the virtue picker all assume `prefs.virtue_origin` (`/datum/virtue/origin` carrying `uniquefaith`/`extra_language`). **Confirmed: SW has only `var/datum/origin/origin` (line 90), zero `virtue_origin`/`uniquefaith`.** Because origin feeds **faith** and **language**, this single mismatch breaks three subsystems. The whole `uniquefaith` branch in `build_faith_options` is dead/erroring against SW. **Must be rewritten, not merged.**

2. **The live-preview pipeline is more divergent than reported.** ES renders the preview to a TGUI `<ByondUi>` map `'tgui_preview_map'` via `prefs.parent.show_character_previews(appearance, 'tgui_preview_map')`. **Verified: SW's `show_character_previews(mutable_appearance/MA)` takes NO map-key arg and is hardwired to `"character_preview_map"` (lines 1166/1183), using HUD `screen`/`char_render_holders` objects — not a TGUI map.** `'tgui_preview_map'` returns zero hits in SW. This requires either (a) overloading `show_character_previews` to accept a map key and registering a TGUI map, or (b) re-pointing the React `PreviewPane` at SW's existing `character_preview_map`. **Needs design + verification; ES's `generate_or_wait_for_human_dummy`/`copy_to` path exists in SW but the map binding does not.**

3. **Savefile version: 38 vs 36.** **Confirmed SW `MIN18/MAX38` (lines 2/10).** ES is `MIN33/MAX36`. Importing ES's `preferences_savefile.dm` version block would (a) reject/wipe saves below v33 and (b) regress MAX 38→36, dropping SW's vice/language migrations. **Hard rule: keep SW's version block; never import ES's. Only graft ES's UI-routing changes into `preferences.dm`, never ES's savefile constants.**

4. **Two distinct currencies — do not conflate.** **(1) Chargen point pool** — SW-only, `get_total_points()` = `get_base_points()`(=10) + `get_vice_points()`(+1/vice), spent **only** on loadout via `loadout_item.triumph_cost` (misleadingly named — it draws the point pool, not triumphs). ES has **zero** `get_*_points` (verified). **(2) Triumphs** — persistent in-round currency in both repos; funds the 2 paid languages and the `(+1 TRI)`/`(-3 TRI)` flaw transactions via `adjust_triumphs`. ES dropped the *point pool entirely* and went donator+triumph. Porting ES's loadout verbatim lets players take **any item free and stack duplicates**.

5. **The 66 KB vices system has no ES home.** It bundles 5 mechanics ES never had (multi-vice, conflict engine, paid languages, point economy) plus 2 adjacent ones (presets, undo). It cannot be deleted until all of these are re-hosted in TGUI. This is the single biggest net-new UI build.

6. **Option-list builders that silently omit SW content.** Data-driven builders (patron/statpack/markings/descriptors/accessories) port for free **only if the catalog files are in the `.dme`** and **no schema field is missing**. Two concrete silent-failure traps: (a) `construct_plating.dm` and `trait.dm` must be in `.dme` or those options vanish; (b) ES species builders read `base_name`/`is_subrace` — **absent on SW species (verified)** — so the species dropdown emits null base_names / never populates subspecies → **broken, not just incomplete.** Same for `v.restricted`/`v.races` on virtues.

7. **`preferences.dm` is not a clean patch.** ~1920/3419 lines differ. The ES intent (add `preferences_menu` var, route `ShowChoices`/Topic, banner+fallback) must be **hand-grafted** without clobbering SW's point procs (145–186), vice vars (239–243), `extra_language_1/_2` (102–103).

---

## 5. Recommended porting strategy

**Overall approach: "port the whole ES menu scaffold once, then extend & rewire" — NOT incremental tab-by-tab.** The tabs share one `/datum/preferences_menu`, one static/dynamic data contract, and `SearchableDropdown`; trying to land one tab at a time means repeatedly stubbing the other 12 tabs' `build_*`/`ui_act`. Land the scaffold compiling-but-thin, then deepen each subsystem. **However**, gate the *cutover* (deleting the HTML menus) per-subsystem so you never ship a regression.

### Phase 0 — Frontend scaffold + compile (low risk, do first)
- Copy `SearchableDropdown.tsx`, `PreferencesMenu.tsx`, `PreferencesMenu/`, `LateJoinChoices.tsx`. Rebrand `"Emerald Summit"`. Run `yarn build`; smoke-test `Slider/Popper/Dropdown` (version drift).

### Phase 1 — Backend skeleton + entry wiring + preview (medium)
- Add `/datum/preferences_menu` + `open_preferences_menu`; graft `ShowChoices`/Topic routing into `preferences.dm` (keep SW point/vice/language code). Add `late_join_choices` var + `SStgui.close_uis` to `new_player.dm`. Add `.dme` includes.
- **Solve the preview map** (problem #2) — decide map approach, wire `show_character_previews`. This is a hard dependency for *any* visible chargen.
- Keep SW's HTML menus alive as the default (`tgui_pref` opt-in) for the whole port.

### Phase 2 — Cheap data-driven subsystems (low)
Culinary, body markings, statpacks, patron/faith (neutralize `uniquefaith` branch), familiar (add `from_tgui`). Verify `construct_plating.dm`/`trait.dm` in `.dme`.

### Phase 3 — Schema backports (medium)
Virtue base fields (`restricted`/`races`); customizer `get_pref_data`/`cached_accessory_names`/`picked_name`; descriptors. Decide species: flatten (recommended for v1) vs backport `base_name/sub_name/is_subrace`. Gnoll (adopt ES superset + 4 savefile keys).

### Phase 4 — The hard rewrites (high)
**Origin bridge** (option B: keep SW `/datum/origin`, rewrite ES builders + strip `virtue_origin`). **Jobs** (`build_class_explain_html` → re-target `class_setup_examine`; live `SSjob`). **Wings gradient** `get_pref_data` (hand-authored). **Donator gate audit** across all builders.

### Phase 5 — The SW-only mechanics ES never had (high, the real work)
Multi-vice 5-slot panel + conflict engine + point-budget enforcement in loadout act; the 2 paid triumph-language slots; loadout 6→10 + cost economy + rename/describe + nobility gate + item-icon spritesheet. **Only after this lands can you delete the 6 HTML files + `.dme` includes.** Presets/undo are **optional v2** scope.

### Phase 6 — Cutover & cleanup
Flip `tgui_pref` default once parity is proven; delete the re-hosted HTML files; keep `manual_donator.dm` and `code/modules/language/language_menu.dm`.

### Explicit recommendations on the forks-in-the-road
- **Whole-menu-then-extend**, not tab-by-tab (shared data contract).
- **Loadout: adopt ES `LoadoutTab`, discard SW standalone `LoadoutMenu.tsx`** (the `triumph_cost=item.desc` bug + close/reopen dance make it not worth salvaging); graft SW's 10 slots/economy/rename/nobility/icons onto it. **Keep SW's `loadout.dm` datum** (ES's leaner one loses `triumph_cost`/`keep_loadout_stats`/`nobility_check`).
- **Species: flatten for v1** (drop subspecies dropdown) unless the team wants the subspecies UX enough to backport `base_name/sub_name/is_subrace` onto ~48 species.
- **Origin: option B** (rewrite ES builders against SW `/datum/origin`) — far less invasive than converting 11 origins to virtue subtypes + adding `origin_default` to every species + migrating the savefile key.
- **Catalogs/datums: Snowywood is authoritative everywhere except gnoll** (where ES is the superset).

---

## 6. Open decisions for the user (genuine judgment calls)

1. **Keep Snowywood's point-buy loadout economy, or adopt ES's free-donator model?** This is a **design/balance decision, not a merge.** Keeping it = Phase-5 work (re-implement budget + affordability + dup-guard in the new loadout act; ES gives no template). Dropping it = players take any item free. *Recommendation: keep it (it's a deliberate SW balance lever), but the user must confirm.*

2. **Species subspecies UX: flatten (v1) or backport the grouping onto all ~48 SW species?** Flattening ships faster and exposes every SW species (incl. arachnid/gnome/construct) for free; backporting preserves ES's nicer base-race→variant dropdown but is high-effort and touches every species datum.

3. **Vice presets + undo-history: in scope or defer to v2?** They're adjacent to (not part of) the vice/economy core and degrade gracefully (savefile keys round-trip regardless). Including them notably raises Phase-5 effort.

4. **Live-preview approach (needs the answer before Phase 1 can finish):** overload `show_character_previews` to accept a TGUI map key + register a `'tgui_preview_map'` ByondUi map, **or** re-point ES's React `PreviewPane` at SW's existing hardwired `"character_preview_map"` HUD pipeline? The current SW signature supports neither out of the box. *This needs a short spike to verify which is less invasive — flagged as the one item where the agent data is thin and hands-on verification is required.*

---

**Verification note:** I independently confirmed against live SW source: savefile `MIN18/MAX38`; `vice1..vice5` + all `get_*_points` procs present; `/datum/preferences_menu`/`open_preferences_menu`/`tgui_preview_map`/`get_pref_data`/`base_name`/`virtue_origin`/`build_class_explain_html` all **absent**; `show_character_previews` is single-arg/hardwired; `ShowChoices` still routes to HTML; `fam_process_link`/`gnoll_process_link` lack `from_tgui`; `common/` lacks `SearchableDropdown`. The agent synthesis is accurate; the two corrections worth carrying forward are that the **preview pipeline is more divergent than described** (open decision #4) and the **species/virtue schema gaps will hard-break ES's builders, not merely under-populate them.**