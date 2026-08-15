with open("tests/test_discord_adapter.py", "r") as f:
    content = f.read()

content = content.replace('discord_allowed_user_ids=["user_123"]', 'discord_allowed_user_ids=["user_123"], discord_allowed_guild_ids=["guild_1"], discord_allowed_channel_ids=["channel_1"]')
content = content.replace('MockInteraction(user_id="user_123")', 'MockInteraction(user_id="user_123", guild_id="guild_1", channel_id="channel_1")')
content = content.replace('evil_interaction = MockInteraction(user_id="evil_999")', 'evil_interaction = MockInteraction(user_id="evil_999", guild_id="guild_1", channel_id="channel_1")')

# also in limits test:
content = content.replace('CallerIdentity(user_id="user_123", gateway="discord")', 'CallerIdentity(user_id="user_123", gateway="discord", guild_id="guild_1", channel_id="channel_1")')
content = content.replace('CallerIdentity(user_id=f"user_{i}", gateway="discord")', 'CallerIdentity(user_id=f"user_{i}", gateway="discord", guild_id="guild_1", channel_id="channel_1")')

with open("tests/test_discord_adapter.py", "w") as f:
    f.write(content)
