import re

with open("tests/test_discord_adapter.py", "r") as f:
    lines = f.readlines()

new_lines = []
skip = False
for line in lines:
    if "with mock.patch" in line:
        continue
    # Unindent next line if previous was mock
    if len(new_lines) > 0 and "def " not in new_lines[-1] and line.startswith("            res ="):
        line = line.replace("            ", "        ", 1)
    if len(new_lines) > 0 and line.startswith("            self.gateway.handle_slash_command"):
        line = line.replace("            ", "        ", 1)
    new_lines.append(line)

with open("tests/test_discord_adapter.py", "w") as f:
    f.writelines(new_lines)

