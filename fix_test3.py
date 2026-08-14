with open("tests/test_backup_restore_data.py", "r") as f:
    src = f.read()

old_mock = """        def mock_is_green(opt, nodes):
            if opt in ["APKs", "Data", "Cloud"]:
                return True, getattr(CONTROLLER, "_smart_find")(opt, nodes)
            return False, getattr(CONTROLLER, "_smart_find")(opt, nodes)"""

new_mock = """        def mock_is_green(opt, nodes):
            card = getattr(CONTROLLER, "_smart_find")(opt, nodes)
            if not card:
                return False, None
            if opt in ["APKs", "Data", "Cloud"]:
                return True, card
            return False, card"""

src = src.replace(old_mock, new_mock)

with open("tests/test_backup_restore_data.py", "w") as f:
    f.write(src)
print("tests fixed 3")
