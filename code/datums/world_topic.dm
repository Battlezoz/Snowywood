// SETUP

/proc/TopicHandlers()
	. = list()
	var/list/all_handlers = subtypesof(/datum/world_topic)
	for(var/I in all_handlers)
		var/datum/world_topic/WT = I
		var/keyword = initial(WT.keyword)
		if(!keyword)
			warning("[WT] has no keyword! Ignoring...")
			continue
		var/existing_path = .[keyword]
		if(existing_path)
			warning("[existing_path] and [WT] have the same keyword! Ignoring [WT]...")
		else if(keyword == "key")
			warning("[WT] has keyword 'key'! Ignoring...")
		else
			.[keyword] = WT

// DATUM

/datum/world_topic
	var/keyword
	var/log = TRUE
	var/key_valid
	var/require_comms_key = FALSE

/datum/world_topic/proc/TryRun(list/input)
	if(!config)
		return "Configuration has not initialised yet"
	var/comms_key = CONFIG_GET(string/comms_key)
	if(!comms_key) // key was not set
		return "Commskey disabled"
	key_valid = comms_key == input["key"]
	if(require_comms_key && !key_valid)
		return "Bad Key"
	input -= "key"
	. = Run(input)
	if(islist(.))
		. = list2params(.)

/datum/world_topic/proc/Run(list/input)
	CRASH("Run() not implemented for [type]!")

// TOPICS

/datum/world_topic/ping
	keyword = "ping"
	log = FALSE

/datum/world_topic/ping/Run(list/input)
	. = 0
	for (var/client/C in GLOB.clients)
		++.

/datum/world_topic/playing
	keyword = "playing"
	log = FALSE

/datum/world_topic/playing/Run(list/input)
	return GLOB.player_list.len

/datum/world_topic/pr_announce
	keyword = "announce"
	require_comms_key = TRUE
	var/static/list/PRcounts = list()	//PR id -> number of times announced this round

/datum/world_topic/pr_announce/Run(list/input)
	var/list/payload = json_decode(input["payload"])
	var/id = "[payload["pull_request"]["id"]]"
	if(!PRcounts[id])
		PRcounts[id] = 1
	else
		++PRcounts[id]
		if(PRcounts[id] > PR_ANNOUNCEMENTS_PER_ROUND)
			return

	var/final_composed = span_announce("PR: [input[keyword]]")
	for(var/client/C in GLOB.clients)
		C.AnnouncePR(final_composed)

/datum/world_topic/ahelp_relay
	keyword = "Ahelp"
	require_comms_key = TRUE

/datum/world_topic/ahelp_relay/Run(list/input)
	relay_msg_admins(span_adminnotice("<b><font color=red>HELP: </font> [input["source"]] [input["message_sender"]]: [input["message"]]</b>"))

/datum/world_topic/news_report
	keyword = "News_Report"
	require_comms_key = TRUE

/datum/world_topic/news_report/Run(list/input)
	minor_announce(input["message"], "Breaking Update From [input["message_sender"]]")

/datum/world_topic/server_hop
	keyword = "server_hop"

/datum/world_topic/server_hop/Run(list/input)
	var/expected_key = input[keyword]
	for(var/mob/dead/observer/O in GLOB.player_list)
		if(O.key == expected_key)
			new /atom/movable/screen/splash(O.client, TRUE)
			break

/datum/world_topic/adminmsg
	keyword = "adminmsg"
	require_comms_key = TRUE

/datum/world_topic/adminmsg/Run(list/input)
	return IrcPm(input[keyword], input["msg"], input["sender"])

/datum/world_topic/namecheck
	keyword = "namecheck"
	require_comms_key = TRUE

/datum/world_topic/namecheck/Run(list/input)
	//Oh this is a hack, someone refactor the functionality out of the chat command PLS
	var/datum/tgs_chat_command/namecheck/NC = new
	var/datum/tgs_chat_user/user = new
	user.friendly_name = input["sender"]
	user.mention = user.friendly_name
	return NC.Run(user, input["namecheck"])

/datum/world_topic/adminwho
	keyword = "adminwho"
	require_comms_key = TRUE

/datum/world_topic/adminwho/Run(list/input)
	return ircadminwho()

/datum/world_topic/status
	keyword = "status"

/datum/world_topic/status/Run(list/input)
	. = list()
	.["version"] = GLOB.game_version
	.["mode"] = GLOB.master_mode
	.["respawn"] = config ? !CONFIG_GET(flag/norespawn) : FALSE
	.["enter"] = GLOB.enter_allowed
	.["vote"] = CONFIG_GET(flag/allow_vote_mode)
	.["ai"] = CONFIG_GET(flag/allow_ai)
	.["host"] = world.host ? world.host : null
	.["round_id"] = GLOB.round_id
	.["players"] = GLOB.clients.len
	.["revision"] = GLOB.revdata.commit
	.["revision_date"] = GLOB.revdata.date

	var/list/adm = get_admin_counts()
	var/list/presentmins = adm["present"]
	var/list/afkmins = adm["afk"]
	.["admins"] = presentmins.len + afkmins.len //equivalent to the info gotten from adminwho
	.["gamestate"] = SSticker.current_state

	.["map_name"] = SSmapping.config?.map_name || "Loading..."

	if(key_valid)
		.["active_players"] = get_active_player_count()
		if(SSticker.HasRoundStarted())
			.["real_mode"] = "Storytellers"
			// Key-authed callers may know the truth behind the "secret"

	.["round_duration"] = SSticker ? round((world.time-SSticker.round_start_time)/10) : 0
	// Amount of world's ticks in seconds, useful for calculating round duration
	
	//Time dilation stats.
	.["time_dilation_current"] = SStime_track.time_dilation_current
	.["time_dilation_avg"] = SStime_track.time_dilation_avg
	.["time_dilation_avg_slow"] = SStime_track.time_dilation_avg_slow
	.["time_dilation_avg_fast"] = SStime_track.time_dilation_avg_fast
	
	//pop cap stats
	.["soft_popcap"] = CONFIG_GET(number/soft_popcap) || 0
	.["hard_popcap"] = CONFIG_GET(number/hard_popcap) || 0
	.["extreme_popcap"] = CONFIG_GET(number/extreme_popcap) || 0
	.["popcap"] = max(CONFIG_GET(number/soft_popcap), CONFIG_GET(number/hard_popcap), CONFIG_GET(number/extreme_popcap)) //generalized field for this concept for use across ss13 codebases

/// Connected player keys for the Discord bot's /ask live-status answers. Comms-key gated.
/datum/world_topic/playerlist
	keyword = "playerlist"
	require_comms_key = TRUE

/datum/world_topic/playerlist/Run(list/input)
	var/list/keys = list()
	for(var/client/C in GLOB.clients)
		var/key = C.ckey
		if(key in GLOB.anonymize)
			key = get_fake_key(key)
		keys += key
	return jointext(keys, ", ")

/// Inbound OOC from the Discord bot bridge. Comms-key gated; broadcasts using the same OOC visibility rules as /client/verb/ooc.
/datum/world_topic/discord_ooc
	keyword = "discord_ooc"
	require_comms_key = TRUE
	/// world.time of recently injected messages, for rate limiting
	var/static/list/recent_times = list()

/datum/world_topic/discord_ooc/Run(list/input)
	// The in-game "mute OOC" admin toggle also silences the Discord bridge.
	if(!GLOB.ooc_allowed)
		return "OOC disabled"
	// Rate limit: at most 5 Discord->game OOC messages per 5 seconds.
	var/now = world.time
	var/list/kept = list()
	for(var/t in recent_times)
		if(now - t <= 5 SECONDS)
			kept += t
	recent_times = kept
	if(length(recent_times) >= 5)
		return "Rate limited"
	recent_times += now

	var/sender = input["sender"]
	var/message = input["message"]
	if(!message)
		return "No message"
	sender = copytext(sanitize(sender), 1, 64)
	if(!sender)
		sender = "Discord"
	message = copytext(sanitize(message), 1, MAX_MESSAGE_LEN)
	if(!message)
		return "Empty message"

	var/msg_to_send = "<font color='#7289DA'><EM>\[Discord] [sender]:</EM></font> <span class='message linkify'>[message]</span>"
	var/post_round = (SSticker.current_state >= GAME_STATE_FINISHED)
	for(var/client/C in GLOB.clients)
		if(!post_round)
			if(!C.holder && !istype(C.mob, /mob/dead/new_player))
				continue
			if(C.holder && !C.show_lobby_ooc && !istype(C.mob, /mob/dead/new_player))
				continue
		if(!(C.prefs.chat_toggles & CHAT_OOC))
			continue
		to_chat(C, type = MESSAGE_TYPE_OOC, html = msg_to_send)

	log_ooc("DISCORD: [sender]: [message]")
	return "OK"

/// In-game replies from the RAG bot ("Quill") to OOC mentions of it. The bot
/// (bot.py _answer_ooc_quill) detects "quill" in mirrored OOC, runs the RAG, and
/// calls this back. Comms-key gated; broadcasts as an OOC line under the same
/// visibility + mute rules as the discord_ooc bridge above.
/datum/world_topic/quill_say
	keyword = "quill_say"
	require_comms_key = TRUE
	/// world.time of recent Quill lines, for rate limiting
	var/static/list/recent_times = list()

/datum/world_topic/quill_say/Run(list/input)
	if(!GLOB.ooc_allowed)
		return "OOC disabled"
	var/now = world.time
	var/list/kept = list()
	for(var/t in recent_times)
		if(now - t <= 5 SECONDS)
			kept += t
	recent_times = kept
	if(length(recent_times) >= 3)
		return "Rate limited"
	recent_times += now

	var/message = input["message"]
	if(!message)
		return "No message"
	message = copytext(sanitize(message), 1, MAX_MESSAGE_LEN)
	if(!message)
		return "Empty message"

	var/msg_to_send = "<font color='#1f9ec9'><EM>Quill:</EM></font> <font color='#1f9ec9'><span class='message linkify'>[message]</span></font>"
	var/post_round = (SSticker.current_state >= GAME_STATE_FINISHED)
	for(var/client/C in GLOB.clients)
		if(!post_round)
			if(!C.holder && !istype(C.mob, /mob/dead/new_player))
				continue
			if(C.holder && !C.show_lobby_ooc && !istype(C.mob, /mob/dead/new_player))
				continue
		if(!(C.prefs.chat_toggles & CHAT_OOC))
			continue
		to_chat(C, type = MESSAGE_TYPE_OOC, html = msg_to_send)

	log_ooc("QUILL: [message]")
	return "OK"

	
