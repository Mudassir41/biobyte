class_name BioSegLink
extends Node
## BioSeg Twin 3D GDScript WebSocket client.
## Connects to the BioSeg Twin server at ws://127.0.0.1:8000/ws and consumes
## section 3 event schemas to drive 3D flap, chute, and bin animations.

signal event_received(event: Dictionary)
signal classified(item_id: String, item_class: String, route: String, flap: String)
signal routed(item_id: String, flap: String)
signal held(item_id: String, reason: String)
signal autoclave_tick(temp_c: float, f0: float, phase: String)
signal autoclave_result(f0: float, result: String)
signal state_change(from_state: String, to_state: String)

@export var websocket_url: String = "ws://127.0.0.1:8000/ws"
@export var replay_file_path: String = "res://../data/sessions/demo_synthetic.jsonl"
@export var auto_connect: bool = true

var _socket: WebSocketPeer = WebSocketPeer.new()
var _connected: bool = false
var _reconnect_timer: float = 0.0
var _is_replaying: bool = false
var _replay_events: Array = []
var _replay_index: int = 0
var _replay_timer: float = 0.0

# Rig State Tracking
var flap_position: String = "centre" # "left", "right", "centre"
var flap_angle_deg: float = 0.0 # -35.0 (yellow/left), 0.0 (hold/centre), +35.0 (red/right)
var yellow_bin_count: int = 0
var red_bin_count: int = 0
var hold_tray_count: int = 0
var release_blocked: bool = false


func _ready() -> void:
	var env_url := OS.get_environment("BIOSEG_WS_URL")
	if env_url != "":
		websocket_url = env_url
	if auto_connect:
		connect_to_server()


func connect_to_server() -> void:
	var err := _socket.connect_to_url(websocket_url)
	if err != OK:
		push_warning("BioSegLink: Could not initiate connection to " + websocket_url)
		_connected = false
	else:
		_connected = true


func _process(delta: float) -> void:
	if _is_replaying:
		_process_replay(delta)
		return

	_socket.poll()
	var state := _socket.get_ready_state()

	if state == WebSocketPeer.STATE_OPEN:
		while _socket.get_available_packet_count() > 0:
			var packet := _socket.get_packet()
			var text := packet.get_string_from_utf8()
			_handle_json_message(text)
	elif state == WebSocketPeer.STATE_CLOSED:
		_connected = false
		_reconnect_timer += delta
		if _reconnect_timer >= 3.0:
			_reconnect_timer = 0.0
			connect_to_server()


func _handle_json_message(json_str: String) -> void:
	var parsed = JSON.parse_string(json_str)
	if typeof(parsed) != TYPE_DICTIONARY:
		return
	dispatch_event(parsed)


func dispatch_event(data: Dictionary) -> void:
	event_received.emit(data)
	var evt: String = data.get("event", "")

	match evt:
		"classified":
			var item_id: String = data.get("item_id", "")
			var item_class: String = data.get("item_class", "")
			var route: String = data.get("route", "hold")
			var flap: String = data.get("flap", "centre")
			_set_flap_target(flap)
			classified.emit(item_id, item_class, route, flap)

		"routed":
			var item_id: String = data.get("item_id", "")
			var flap: String = data.get("flap", "centre")
			_set_flap_target(flap)
			if flap == "left":
				yellow_bin_count += 1
			elif flap == "right":
				red_bin_count += 1
			else:
				hold_tray_count += 1
			routed.emit(item_id, flap)

		"held":
			var item_id: String = data.get("item_id", "")
			var reason: String = data.get("reason", "")
			_set_flap_target("centre")
			hold_tray_count += 1
			held.emit(item_id, reason)

		"autoclave_tick":
			var t_c: float = data.get("t_c", 0.0)
			var f0: float = data.get("f0", 0.0)
			var phase: String = data.get("phase", "")
			autoclave_tick.emit(t_c, f0, phase)

		"autoclave_result":
			var f0: float = data.get("f0", 0.0)
			var result: String = data.get("result", "PASS")
			if result == "FAIL":
				release_blocked = true
			autoclave_result.emit(f0, result)

		"state_change":
			var from_st: String = data.get("from", "")
			var to_st: String = data.get("to", "")
			state_change.emit(from_st, to_st)


func _set_flap_target(pos: String) -> void:
	flap_position = pos
	match pos:
		"left":
			flap_angle_deg = -35.0
		"right":
			flap_angle_deg = 35.0
		_:
			flap_angle_deg = 0.0


## Start standalone offline replay from a JSONL session file.
func load_and_play_replay(path: String = "") -> bool:
	var target_path := path if path != "" else replay_file_path
	if not FileAccess.file_exists(target_path):
		push_warning("BioSegLink: Replay file not found: " + target_path)
		return false

	var f := FileAccess.open(target_path, FileAccess.READ)
	if f == null:
		return false

	_replay_events.clear()
	while not f.eof_reached():
		var line := f.get_line().strip_edges()
		if line != "":
			var parsed = JSON.parse_string(line)
			if typeof(parsed) == TYPE_DICTIONARY:
				_replay_events.append(parsed)
	f.close()

	_is_replaying = true
	_replay_index = 0
	_replay_timer = 0.0
	return true


func _process_replay(delta: float) -> void:
	if _replay_index >= _replay_events.size():
		# Loop replay
		_replay_index = 0
		_replay_timer = 0.0
		return

	_replay_timer += delta
	# Pace replay at approx 1 event per 1.5 seconds for visual pacing
	if _replay_timer >= 1.5:
		_replay_timer = 0.0
		var evt: Dictionary = _replay_events[_replay_index]
		_replay_index += 1
		dispatch_event(evt)
