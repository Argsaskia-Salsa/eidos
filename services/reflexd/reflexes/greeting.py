from reflexd import call, on_event


@on_event("user_message")
async def on_user_message(_event):
    await call("set_avatar", face="happy")
    await call("move_head", yaw=10, pitch=60)


@on_event("user_response")
async def on_user_response(_event):
    await call("set_avatar", face="neutral")
    await call("move_head", yaw=0, pitch=60)
