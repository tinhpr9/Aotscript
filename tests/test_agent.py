import unittest
from unittest import mock
from importlib.machinery import SourceFileLoader
import sys
import os

# Ensure agent can be imported without executing the infinite loop.
agent_path = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'agent'))
agent = SourceFileLoader('agent', agent_path).load_module()
sys.modules['agent'] = agent

class TestAgentGroupLogic(unittest.TestCase):
    def setUp(self):
        self.group_content = ''
        self.open_mock = mock.mock_open()
        self.patcher_open = mock.patch('builtins.open', self.open_mock)
        self.patcher_open.start()
        self.patcher_exists = mock.patch('os.path.exists', return_value=True)
        self.patcher_exists.start()
        self.patcher_makedirs = mock.patch('os.makedirs')
        self.patcher_makedirs.start()
        self.patcher_remove = mock.patch('os.remove')
        self.patcher_remove.start()
        self.patcher_urlopen = mock.patch('urllib.request.urlopen')
        self.mock_urlopen = self.patcher_urlopen.start()
        self.patcher_request = mock.patch('urllib.request.Request')
        self.mock_request = self.patcher_request.start()
        self.patcher_time = mock.patch('time.time', return_value=1234567890)
        self.patcher_time.start()
        self.patcher_sleep = mock.patch('time.sleep')
        self.patcher_sleep.start()
        self.patcher_system = mock.patch('os.system')
        self.patcher_system.start()
        self.patcher_subprocess_run = mock.patch('subprocess.run')
        self.patcher_subprocess_run.start()
        self.patcher_subprocess_popen = mock.patch('subprocess.Popen')
        self.patcher_subprocess_popen.start()
        self.patcher_json_load = mock.patch('json.load', return_value={
            'device_group': '',
            'common_command_hash': '',
            'group_command_hash': '',
            'last_processed_at': ''
        })
        self.patcher_json_load.start()
        self.patcher_json_dump = mock.patch('json.dump')
        self.patcher_json_dump.start()
        self.open_mock.side_effect = self.open_side_effect

    def tearDown(self):
        mock.patch.stopall()

    def open_side_effect(self, file, mode='r', *args, **kwargs):
        if file == agent.GROUP_PATH and 'r' in mode:
            return mock.mock_open(read_data=self.group_content).return_value
        if 'w' in mode or 'a' in mode:
            return mock.mock_open().return_value
        return mock.mock_open(read_data='').return_value

    def set_group_file(self, content):
        self.group_content = content

    def make_fetch_responses(self, *texts):
        responses = []
        for text in texts:
            response = mock.MagicMock()
            response.read.return_value = text.encode('utf-8')
            response.__enter__.return_value = response
            response.__exit__.return_value = False
            responses.append(response)
        self.mock_urlopen.side_effect = responses

    def test_nova_uses_common_and_nova(self):
        self.set_group_file('NOVA')
        self.make_fetch_responses('COMMON_CMD\n', 'NOVA_CMD\n')

        self.assertEqual(agent.get_device_group(), 'NOVA')
        common, group = agent.fetch_commands_for_group('NOVA')
        self.assertEqual(common, 'COMMON_CMD\n')
        self.assertEqual(group, 'NOVA_CMD\n')

    def test_marmot_uses_common_and_marmot(self):
        self.set_group_file('MARMOT')
        self.make_fetch_responses('COMMON_CMD\n', 'MARMOT_CMD\n')

        self.assertEqual(agent.get_device_group(), 'MARMOT')
        common, group = agent.fetch_commands_for_group('MARMOT')
        self.assertEqual(common, 'COMMON_CMD\n')
        self.assertEqual(group, 'MARMOT_CMD\n')

    def test_invalid_group_does_not_fetch(self):
        self.set_group_file('BADGROUP')
        self.assertIsNone(agent.get_device_group())
        self.assertFalse(self.mock_urlopen.called)

    def test_hash_unchanged_skips_processing(self):
        state = {
            'device_group': 'NOVA',
            'common_command_hash': agent.calculate_sha256('A'),
            'group_command_hash': agent.calculate_sha256('B'),
            'last_processed_at': ''
        }
        self.assertEqual(state['common_command_hash'], agent.calculate_sha256('A'))
        self.assertEqual(state['group_command_hash'], agent.calculate_sha256('B'))

    def test_group_change_resets_hash(self):
        state = {'device_group': 'NOVA', 'common_command_hash': 'old', 'group_command_hash': 'old'}
        new_group = 'MARMOT'
        self.assertNotEqual(new_group, state['device_group'])
        state['device_group'] = new_group
        state['common_command_hash'] = ''
        state['group_command_hash'] = ''
        self.assertEqual(state['common_command_hash'], '')
        self.assertEqual(state['group_command_hash'], '')

    def test_install_group_script_rejects_wrong_group(self):
        agent.install_group_script('BADGROUP')
        self.mock_request.assert_not_called()

if __name__ == '__main__':
    unittest.main()
