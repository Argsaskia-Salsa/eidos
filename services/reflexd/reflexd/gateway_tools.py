GATEWAY_TOOLS = {
    "move_head": {"yaw": "int", "pitch": "int", "speed": "int?"},
    "set_avatar": {"face": "str"},
    "set_mouth": {"mouth": "str"},
    "set_blink": {"enabled": "bool"},
    "set_all_leds": {"r": "int", "g": "int", "b": "int"},
    "set_led": {"index": "int", "r": "int", "g": "int", "b": "int"},
    "set_leds": {"colors": "list"},
    "clear_leds": {},
    "take_photo": {"question": "str"},
    "get_status": {},
    "get_device_info": {},
}