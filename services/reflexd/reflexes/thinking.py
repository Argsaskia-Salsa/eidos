from reflexd import call, on_event


@on_event("thinking_start")
async def on_thinking_start(_event):
    await call("set_avatar", face="thinking")


@on_event("thinking_end")
async def on_thinking_end(_event):
    await call("set_avatar", face="neutral")
