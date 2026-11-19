"""
Custom patterns directory.
Place your pattern modules here.
Each module should export a PATTERN variable of type PatternSpec.

Example:
    from led_stream_server import PatternSpec
    
    def my_pattern(num_leds=12, color=[255,0,0]):
        # return frames
        return frames
    
    PATTERN = PatternSpec(
        name="my_pattern",
        func=my_pattern,
        args={"color": [255, 0, 0]},
        description="My custom pattern"
    )
"""