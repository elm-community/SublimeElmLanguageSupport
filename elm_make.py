import sublime
import sublime_plugin

import html
import json
import os
import string
import subprocess
import threading

from .elm_plugin import *
from .elm_project import ElmProject

USER_SETTING_PREFIX = 'elm_language_support_'

# We need a custom build command so that we can take the JSON output from the
# Elm compiler and render it in a format that works with Sublime Text’s build
# output panel syntax highlighting and regexp-based error navigation.
#
# Based on Advanced Example: https://www.sublimetext.com/docs/3/build_systems.html#advanced_example
class ElmMakeCommand(sublime_plugin.WindowCommand):

    encoding = 'utf-8'
    killed = False
    proc = None
    panel = None
    panel_lock = threading.Lock()

    errs_by_file = {}
    phantom_sets_by_buffer = {}
    show_errors_inline = True

    def is_enabled(self, kill=False):
        # Cancel only available when the process is still running
        if kill:
            return self.proc is not None and self.proc.poll() is None
        return True

    def run(self, cmd=[], kill=False):
        if kill:
            if self.proc:
                self.killed = True
                self.proc.terminate()
            return

        working_dir = self.working_dir()
        self.create_panel(working_dir)

        if self.proc is not None:
            self.proc.terminate()
            self.proc = None

        self.proc = subprocess.Popen(
            self.format_cmd(cmd),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            cwd=working_dir,
            startupinfo=get_popen_startupinfo()
        )
        self.killed = False

        threading.Thread(
            target=self.read_handle,
            args=(self.proc.stdout,)
        ).start()

    def working_dir(self):
        vars = self.window.extract_variables()
        project = ElmProject(vars['file'])
        log_string('project.logging.settings', repr(project))
        return project.working_dir or vars['project_path'] or vars['file_path']

    def create_panel(self, working_dir):
        # Only allow one thread to touch output panel at a time
        with self.panel_lock:
            # implicitly clears previous contents
            self.panel = self.window.create_output_panel('exec')

            settings = self.panel.settings()

            self.panel.assign_syntax('Packages/Elm Language Support/Syntaxes/Elm Compile Messages.sublime-syntax')
            settings.set('gutter', False)
            settings.set('scroll_past_end', False)
            settings.set('word_wrap', False)
            settings.set('color_scheme', self.get_setting('build_output_color_scheme', 'color_scheme'))

            # Enable result navigation
            settings.set(
                'result_file_regex',
                r'^\-\- \w+: (?=.+ \- (.+?):(\d+)(?=:(\d+))?)(.+) \- .*$'
            )
            settings.set('result_base_dir', working_dir)

        preferences = sublime.load_settings('Preferences.sublime-settings')

        self.hide_phantoms()
        self.show_errors_inline = preferences.get('show_errors_inline', True)

        show_panel_on_build = preferences.get('show_panel_on_build', True)
        if show_panel_on_build:
            self.window.run_command('show_panel', {'panel': 'output.exec'})

    def format_cmd(self, cmd):
        binary, command, file, output = cmd[0:4]

        binary = binary.format(elm_binary=self.get_setting('elm_binary'))

        return [binary, command, file, output] + cmd[4:]

    def read_handle(self, handle):
        chunk_size = 2 ** 13
        output = b''
        while True:
            try:
                chunk = os.read(handle.fileno(), chunk_size)
                output += chunk

                if chunk == b'':
                    if output != b'':
                        self.queue_write(self.format_output(output.decode(self.encoding)))
                    raise IOError('EOF')

            except UnicodeDecodeError as e:
                msg = 'Error decoding output using %s - %s'
                self.queue_write(msg % (self.encoding, str(e)))
                break

            except IOError:
                if self.killed:
                    msg = 'Cancelled'
                else:
                    msg = 'Finished'
                    sublime.set_timeout(lambda: self.finish(), 0)
                self.queue_write('[%s]' % msg)
                break

    def queue_write(self, text):
        # Calling set_timeout inside this function rather than inline ensures
        # that the value of text is captured for the lambda to use, and not
        # mutated before it can run.
        sublime.set_timeout(lambda: self.do_write(text), 1)

    def do_write(self, text):
        with self.panel_lock:
            self.panel.set_read_only(False)
            self.panel.run_command('append', {'characters': text})
            self.panel.set_read_only(True)

            if self.show_errors_inline and text.find('\n') >= 0:
                errs = self.panel.find_all_results_with_text()
                errs_by_file = {}
                for file, line, column, text in errs:
                    if file not in errs_by_file:
                        errs_by_file[file] = []
                    errs_by_file[file].append((line, column, text))
                self.errs_by_file = errs_by_file

                self.update_phantoms()

    def format_output(self, output):
        try:
            data = json.loads(output)
            log_string('make.logging.json', output)
            if data['type'] == 'compile-errors':
                return self.format_errors(data['errors'])
            elif data['type'] == 'error':
                return self.format_compiler_error(data)
            else:
                return 'Unrecognized compiler output:\n' + str(output) + '\n\nPlease report this bug in Elm Language Support.\n\n'
        except ValueError as e:
            log_string('make.logging.invalid_json', output)
            return ''

    def format_errors(self, errors):
        return '\n'.join(map(self.format_error, errors)) + '\n'

    def format_error(self, error):
        file = error['path']
        return '\n'.join(map(lambda problem: self.format_problem(file, problem), error['problems']))

    def format_problem(self, file, problem):
        error_format = string.Template('-- $type: $title - $file:$line:$column\n\n$message\n')

        type = 'error'
        title = problem['title']
        line = problem['region']['start']['line']
        column = problem['region']['start']['column']
        message = self.format_message(problem['message'])

        vars = locals()
        vars.pop('self') # https://bugs.python.org/issue23671
        return error_format.substitute(**vars)

    def format_compiler_error(self, error):
        error_format = string.Template('-- $type: $title - $file:1\n\n$message\n')

        type = 'error'
        title = error['title']
        file = error['path']
        message = self.format_message(error['message'])

        vars = locals()
        vars.pop('self') # https://bugs.python.org/issue23671
        return error_format.substitute(**vars)

    def format_message(self, message):
        format = lambda msg: msg if isinstance(msg, str) else msg['string']

        return ''.join(map(format, message))

    def finish(self):
        errs = self.panel.find_all_results()
        if len(errs) == 0:
            sublime.status_message('Build finished')
        else:
            sublime.status_message('Build finished with %d errors' % len(errs))

    # Borrowed from Sublime’s ExecCommand: https://github.com/twolfson/sublime-files/blob/master/Packages/Default/exec.py
    def update_phantoms(self):
        stylesheet = '''
            <style>
                div.error-arrow {
                    border-top: 0.4rem solid transparent;
                    border-left: 0.5rem solid color(var(--redish) blend(var(--background) 30%));
                    width: 0;
                    height: 0;
                }
                div.error {
                    padding: 0.4rem 0 0.4rem 0.7rem;
                    margin: 0 0 0.2rem;
                    border-radius: 0 0.2rem 0.2rem 0.2rem;
                }
                div.error span.message {
                    padding-right: 0.7rem;
                }
                div.error a {
                    text-decoration: inherit;
                    padding: 0.35rem 0.7rem 0.45rem 0.8rem;
                    position: relative;
                    bottom: 0.05rem;
                    border-radius: 0 0.2rem 0.2rem 0;
                    font-weight: bold;
                }
                html.dark div.error a {
                    background-color: #00000018;
                }
                html.light div.error a {
                    background-color: #ffffff18;
                }
            </style>
        '''

        for file, errs in self.errs_by_file.items():
            view = self.window.find_open_file(file)
            if view:

                buffer_id = view.buffer_id()
                if buffer_id not in self.phantom_sets_by_buffer:
                    phantom_set = sublime.PhantomSet(view, "exec")
                    self.phantom_sets_by_buffer[buffer_id] = phantom_set
                else:
                    phantom_set = self.phantom_sets_by_buffer[buffer_id]

                phantoms = []

                for line, column, text in errs:
                    pt = view.text_point(line - 1, column - 1)
                    phantoms.append(sublime.Phantom(
                        sublime.Region(pt, view.line(pt).b),
                        ('<body id=inline-error>' + stylesheet +
                            '<div class="error-arrow"></div><div class="error">' +
                            '<span class="message">' + html.escape(text, quote=False) + '</span>' +
                            '<a href=hide>' + chr(0x00D7) + '</a></div>' +
                            '</body>'),
                        sublime.LAYOUT_BELOW,
                        on_navigate=self.on_phantom_navigate))

                phantom_set.update(phantoms)

    def hide_phantoms(self):
        for file, errs in self.errs_by_file.items():
            view = self.window.find_open_file(file)
            if view:
                view.erase_phantoms('elm_make')

        self.errs_by_file = {}
        self.phantom_sets_by_buffer = {}
        self.show_errors_inline = False

    def on_phantom_navigate(self, url):
        self.hide_phantoms()

    def get_setting(self, key, user_key=None):
        package_settings = sublime.load_settings('Elm Language Support.sublime-settings')
        user_settings = self.window.active_view().settings()

        return user_settings.get(user_key or (USER_SETTING_PREFIX + key), package_settings.get(key))
