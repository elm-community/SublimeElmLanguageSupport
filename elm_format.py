from __future__ import print_function

import subprocess
import os, os.path
import re
import sublime, sublime_plugin

from .elm_plugin import *
from .elm_project import ElmProject

USER_SETTING_PREFIX = 'elm_language_support_'

class ElmFormatCommand(sublime_plugin.TextCommand):
    def run(self, edit):
        path_separator = ':'
        if os.name == "nt":
            path_separator = ';'

        binary = self.get_setting('elm_format_binary') or 'elm-format'
        options = self.get_setting('elm_format_options') or ''
        path = self.get_setting('elm_paths') or ''
        if path:
            old_path = os.environ['PATH']
            os.environ['PATH'] = os.path.expandvars(path + path_separator + '$PATH')

        working_dir = self.working_dir()
        command = binary.split() + [self.view.file_name(), '--yes'] + options.split()
        p = subprocess.Popen(
            command,
            cwd=working_dir,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            startupinfo=get_popen_startupinfo()
        )

        if path:
            os.environ['PATH'] = old_path

        output, errors = p.communicate()

        if errors.strip():
            # If there is a error while running it. It should show the syntax errors
            panel_view = self.view.window().create_output_panel("elm_format")
            panel_view.set_read_only(False)
            panel_view.run_command('erase_view')

            # elm-format should have a --no-color option https://github.com/avh4/elm-format/issues/372
            errors = re.sub('\x1b\[\d{1,2}m', '', errors.strip().decode())

            panel_view.run_command('append', {'characters': errors})
            panel_view.set_read_only(True)
            self.view.window().run_command("show_panel", {"panel": "output.elm_format"})
        else:
            self.view.window().run_command("hide_panel", {"panel": "output.elm_format"})

        if self.get_setting('debug'):
            string_settings = sublime.load_settings('Elm User Strings.sublime-settings')
            print(string_settings.get('logging.prefix', '') + output.strip().decode(),
                  '\ncommand: ' + str(command),
                  '\nworking directory: ' + working_dir,
                  '\nerrors: ' + errors.strip().decode()
            )
            if errors.strip():
                print('Your PATH is: ', os.environ['PATH'])

    def working_dir(self):
        vars = self.view.window().extract_variables()
        project = ElmProject(vars['file'])
        log_string('project.logging.settings', repr(project))
        return project.working_dir or vars['project_path'] or vars['file_path']

    def get_setting(self, key, user_key=None):
        package_settings = sublime.load_settings('Elm Language Support.sublime-settings')
        user_settings = self.view.settings()

        return user_settings.get(user_key or (USER_SETTING_PREFIX + key), package_settings.get(key))


class ElmFormatOnSave(sublime_plugin.EventListener):
    def on_post_save(self, view):
        sel = view.sel()[0]
        region = view.word(sel)
        scope = view.scope_name(region.b)
        if scope.find('source.elm') != -1:
            package_settings = sublime.load_settings('Elm Language Support.sublime-settings')
            on_save = 'elm_format_on_save'
            if view.settings().get(USER_SETTING_PREFIX + on_save, package_settings.get(on_save, True)):
                filename_filter = 'elm_format_filename_filter'
                regex = view.settings().get(USER_SETTING_PREFIX + filename_filter, package_settings.get(filename_filter, ''))
                if not (len(regex) > 0 and re.search(regex, view.file_name()) is not None):
                    view.run_command('elm_format')
