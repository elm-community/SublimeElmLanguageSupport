import collections
import json

from .elm_plugin import *

class ElmProjectCommand(sublime_plugin.TextCommand):

    def is_enabled(self):
        self.project = ElmProject(self.view.file_name())
        return self.project.exists

    def run(self, edit, prop_name=None, choices=None, caption=None):
        self.window = self.view.window()
        self.window.open_file(self.project.json_path, sublime.TRANSIENT)

class ElmProject(object):

    @classmethod
    def find_json(cls, dir_path):
        if not fs.isdir(fs.abspath(dir_path)):
            return None
        file_path = fs.abspath(fs.join(dir_path, 'elm.json'))
        if fs.isfile(file_path):
            return file_path
        parent_path = fs.join(dir_path, fs.pardir)
        if fs.abspath(parent_path) == fs.abspath(dir_path):
            return None
        return cls.find_json(parent_path)

    def __init__(self, file_path):
        self.file_path = file_path
        self.json_path = self.find_json(fs.dirname(file_path or ''))
        self.data_dict = self.load_json()

    def __getitem__(self, keys):
        if not self.exists:
            return None
        item = self.data_dict
        for key in keys:
            item = item.get(key)
            if not item:
                break
        return item

    def __setitem__(self, keys, value):
        self._last_updated_key_path = None
        if not self.exists:
            sublime.error_message(get_string('project.not_found'))
            return
        item = self.data_dict
        for key in keys[0:-1]:
            item = item.setdefault(key, {})
        item[keys[-1]] = value
        self.save_json()
        self._last_updated_key_path = keys

    def __repr__(self):
        members = [(name, getattr(self, name), ' ' * 4)
            for name in dir(self) if name[0] != '_']
        properties = ["{indent}{name}={value},".format(**locals())
            for name, value, indent in members if not callable(value)]
        return "{0}(\n{1}\n)".format(self.__class__.__name__, '\n'.join(properties))

    def load_json(self):
        try:
            with open(self.json_path) as json_file:
                if is_ST2(): # AttributeError: 'module' object has no attribute 'OrderedDict'
                    return json.load(json_file)
                else:
                    return json.load(json_file, object_pairs_hook=collections.OrderedDict)
        except TypeError: # self.json_path == None
            pass
        except ValueError:
            log_string('project.logging.invalid_json', self.json_path)
        return None

    def save_json(self):
        with open(self.json_path, 'w') as json_file:
            json.dump(self.data_dict, json_file,
                indent=4,
                separators=(',', ': '),
                sort_keys=is_ST2())

    @property
    def exists(self):
        return bool(self.data_dict)

    @property
    def working_dir(self):
        return fs.dirname(self.json_path) if self.json_path else None
