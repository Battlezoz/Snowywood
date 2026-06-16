// ============================================================================
// PHASE-1 PORT SCAFFOLDING — Emerald-Summit TGUI PreferencesMenu port
// ============================================================================
// These are deliberate STUBS that let Emerald-Summit's preferences_menu.dm
// compile against Snowywood, which diverged ~6 months ahead. Each block is
// replaced by the real Snowywood-native implementation in a later phase
// (see docs/chargen-tgui-port-plan.md):
//   * /datum/virtue origin fields  -> Phase 4 origin bridge (SW uses /datum/origin)
//   * /datum/species subspecies    -> Phase 3 backport across ~48 species
//   * OOC/flavor/ERP pref vars      -> Phase 2 (OOC/Flavor prefs wiring)
//   * lamian_tail stub              -> when lamia tail bodyparts are ported
//   * gnoll superset fields          -> gnoll adoption + savefile keys
// While these stubs are in place the TGUI menu is OPT-IN only (tgui_pref off by
// default); do NOT flip the default on until the real implementations land.
// ============================================================================

// Job-department flag ES's late_join_choices uses; SW lacks it.
#define MERCENARIES (1<<7)

// --- Extend SW's /datum/virtue (modular_azurepeak/_virtue.dm) ----------------
// ES models origins as virtues carrying uniquefaith/extra_language, and its
// virtue picker reads restricted/races. SW's virtue has none of these. Phase 4
// rewrites the origin/faith/language builders against /datum/origin.
/datum/virtue
	var/uniquefaith
	var/extra_language
	var/restricted = FALSE
	var/list/races

// Origin subtypes ES references that SW lacks (/datum/virtue + /none already exist).
/datum/virtue/racial
/datum/virtue/origin
/datum/virtue/origin/racial

// --- Stub lamian tail bodypart (+ default subtype) not in the SW build -------
/obj/item/bodypart/lamian_tail
	name = "Tail"

/obj/item/bodypart/lamian_tail/lamian_tail

// --- /datum/species subspecies fields ES's IdentityTab reads ------------------
// Real values get backported onto each species in Phase 3 (subspecies grouping).
/datum/species
	var/base_name
	var/sub_name
	var/is_subrace = FALSE
	var/psydonic = FALSE

/datum/species/proc/get_tail_list()
	return list()

// --- /datum/preferences fields ES's menu reads that SW lacks -----------------
/datum/preferences
	/// Handle to the TGUI menu controller (permanent; relocates to preferences.dm later)
	var/datum/preferences_menu/preferences_menu
	/// Origin stub — Phase 4 bridges this to SW's /datum/origin
	var/datum/virtue/virtue_origin = new /datum/virtue/none
	var/stat_simple = FALSE
	var/is_legacy = FALSE
	var/nsfw_headshot_link
	var/nsfw_ooc_extra
	var/nsfw_ooc_extra_link
	var/nsfwflavortext_display
	var/erpprefs_display
	var/ooc_extra_link
	var/ooc_notes_display
	var/song_url
	var/rumour_display
	var/gossip
	var/gossip_display
	var/tail_type
	var/tail_color = "#ffffff"
	var/tail_markings_color = "#ffffff"

// --- Gnoll superset fields ES adds (Phase: gnoll adoption + savefile keys) ----
/datum/gnoll_prefs
	var/gnoll_flavortext
	var/gnoll_flavortext_display
	var/gnoll_ooc_notes
	var/gnoll_ooc_notes_display

// --- Stub NSFW headshot URL validator (Phase: OOC prefs) ---------------------
// ES's real validator lives in preferences.dm; ported permissively for now.
/proc/valid_nsfw_headshot_link(mob/user, value, silent = FALSE)
	return TRUE

// --- Customizer TGUI serialization (Phase 3: customizer port) ----------------
// ES adds get_pref_data() to feed the React CustomizerCard. Empty for now →
// customizer options simply don't populate until the real proc is ported.
/datum/customizer_choice/proc/get_pref_data(prefs, entry)
	return list()

// --- Size virtue scale (Phase 4: virtue/origin bridge) -----------------------
// ES's body-size sync reads /datum/virtue/size.scale; SW's size virtue lacks it.
/datum/virtue/size
	var/scale = 1

// --- Admin "toggle dead chat" (admin-only; Phase: GamePrefs/admin wiring) -----
/client/proc/deadchat()
	return

// --- Job class-explain HTML (Phase 4: re-target to class_setup_examine) -------
/datum/job/proc/build_class_explain_html()
	return ""
