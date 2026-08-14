with open("tests/test_backup_restore_data.py", "r") as f:
    src = f.read()

mock_patch = """        self.mock_dump = mock.patch.object(CONTROLLER, "dump_ui_xml").start()
        
        def mock_is_green(opt, nodes):
            if opt in ["APKs", "Data", "Cloud"]:
                return True, getattr(CONTROLLER, "_smart_find")(opt, nodes)
            return False, getattr(CONTROLLER, "_smart_find")(opt, nodes)
        self.mock_green = mock.patch.object(CONTROLLER, "_is_green_selected", side_effect=mock_is_green).start()
"""

src = src.replace('        self.mock_dump = mock.patch.object(CONTROLLER, "dump_ui_xml").start()', mock_patch)

with open("tests/test_backup_restore_data.py", "w") as f:
    f.write(src)
print("tests fixed 2")
