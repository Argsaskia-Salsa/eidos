GATEWAY_TOOLS = {
    "move_head": {
        "type": "object",
        "properties": {
            "yaw": {"type": "integer", "minimum": -90, "maximum": 90},
            "pitch": {"type": "integer", "minimum": 5, "maximum": 85},
            "speed": {"type": "integer", "minimum": 10, "maximum": 200, "default": 100},
        },
        "required": ["yaw", "pitch"],
        "additionalProperties": False,
    },
    "set_avatar": {
        "type": "object",
        "properties": {
            "face": {"type": "string"},
        },
        "required": ["face"],
        "additionalProperties": False,
    },
    "set_mouth": {
        "type": "object",
        "properties": {
            "mouth": {"type": "string"},
        },
        "required": ["mouth"],
        "additionalProperties": False,
    },
    "set_blink": {
        "type": "object",
        "properties": {
            "enabled": {"type": "boolean"},
        },
        "required": ["enabled"],
        "additionalProperties": False,
    },
    "set_all_leds": {
        "type": "object",
        "properties": {
            "r": {"type": "integer", "minimum": 0, "maximum": 255},
            "g": {"type": "integer", "minimum": 0, "maximum": 255},
            "b": {"type": "integer", "minimum": 0, "maximum": 255},
        },
        "required": ["r", "g", "b"],
        "additionalProperties": False,
    },
    "set_led": {
        "type": "object",
        "properties": {
            "index": {"type": "integer", "minimum": 0},
            "r": {"type": "integer", "minimum": 0, "maximum": 255},
            "g": {"type": "integer", "minimum": 0, "maximum": 255},
            "b": {"type": "integer", "minimum": 0, "maximum": 255},
        },
        "required": ["index", "r", "g", "b"],
        "additionalProperties": False,
    },
    "set_leds": {
        "type": "object",
        "properties": {
            "colors": {"type": "array"},
        },
        "required": ["colors"],
        "additionalProperties": False,
    },
    "clear_leds": {
        "type": "object",
        "properties": {},
        "additionalProperties": False,
    },
    "take_photo": {
        "type": "object",
        "properties": {
            "question": {"type": "string"},
        },
        "required": ["question"],
        "additionalProperties": False,
    },
    "get_status": {
        "type": "object",
        "properties": {},
        "additionalProperties": False,
    },
    "get_device_info": {
        "type": "object",
        "properties": {},
        "additionalProperties": False,
    },
    "cancel_reflex": {
        "type": "object",
        "properties": {"name": {"type": "string"}},
        "required": ["name"],
        "additionalProperties": False,
    },
    "list_running_reflexes": {
        "type": "object",
        "properties": {},
        "additionalProperties": False,
    },
}
