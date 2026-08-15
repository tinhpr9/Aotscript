import re

with open("tests/test_discord_adapter.py", "r") as f:
    content = f.read()

content = content.replace('"stop", ["m123"]', '"batch", ["marmot"]')
content = content.replace('"stop"', '"batch"')

# Fix mock patch
content = content.replace('"omnicontrol.gateways.discord_adapter.is_authorized"', '"omnicontrol.gateways.discord_adapter.is_authorized"')
# Wait, let's just patch omnicontrol.auth.is_authorized instead
content = content.replace('mock.patch("omnicontrol.gateways.discord_adapter.is_authorized"', 'mock.patch("omnicontrol.auth.is_authorized"')

with open("tests/test_discord_adapter.py", "w") as f:
    f.write(content)
