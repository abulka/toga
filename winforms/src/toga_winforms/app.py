import asyncio
import re
import sys
import threading
from ctypes import c_void_p, windll, wintypes

import System.Windows.Forms as WinForms
from Microsoft.Win32 import SystemEvents
from System import Environment, Threading
from System.ComponentModel import InvalidEnumArgumentException
from System.Drawing import Font as WinFont
from System.Media import SystemSounds
from System.Net import SecurityProtocolType, ServicePointManager
from System.Windows.Threading import Dispatcher

import toga
from toga import Key
from toga.command import Separator

from .keys import toga_to_winforms_key, toga_to_winforms_shortcut
from .libs.proactor import WinformsProactorEventLoop
from .libs.wrapper import WeakrefCallable
from .screens import Screen as ScreenImpl
from .widgets.base import Scalable
from .window import Window


class MainWindow(Window):
    def update_menubar_font_scale(self):
        # Directly using self.native.MainMenuStrip.Font instead of
        # original_menubar_font makes the menubar font to not scale down.
        self.native.MainMenuStrip.Font = WinFont(
            self.original_menubar_font.FontFamily,
            self.scale_font(self.original_menubar_font.Size),
            self.original_menubar_font.Style,
        )

    def winforms_FormClosing(self, sender, event):
        # Differentiate between the handling that occurs when the user
        # requests the app to exit, and the actual application exiting.
        if not self.interface.app._impl._is_exiting:  # pragma: no branch
            # If there's an event handler, process it. The decision to
            # actually exit the app will be processed in the on_exit handler.
            # If there's no exit handler, assume the close/exit can proceed.
            self.interface.app.on_exit()
            event.Cancel = True


def winforms_thread_exception(sender, winforms_exc):  # pragma: no cover
    # The PythonException returned by Winforms doesn't give us
    # easy access to the underlying Python stacktrace; so we
    # reconstruct it from the string message.
    # The Python message is helpfully included in square brackets,
    # as the context for the first line in the .net stack trace.
    # So, look for the closing bracket and the start of the Python.net
    # stack trace. Then, reconstruct the line breaks internal to the
    # remaining string.
    print("Traceback (most recent call last):")
    py_exc = winforms_exc.get_Exception()
    full_stack_trace = py_exc.StackTrace
    regex = re.compile(
        r"^\[(?:'(.*?)', )*(?:'(.*?)')\]   (?:.*?) Python\.Runtime",
        re.DOTALL | re.UNICODE,
    )

    def print_stack_trace(stack_trace_line):  # pragma: no cover
        for level in stack_trace_line.split("', '"):
            for line in level.split("\\n"):
                if line:
                    print(line)

    stacktrace_relevant_lines = regex.findall(full_stack_trace)
    if len(stacktrace_relevant_lines) == 0:
        print_stack_trace(full_stack_trace)
    else:
        for lines in stacktrace_relevant_lines:
            for line in lines:
                print_stack_trace(line)

    print(py_exc.Message)


class App(Scalable):
    _MAIN_WINDOW_CLASS = MainWindow

    # These are required for properly setting up DPI mode
    WinForms.Application.EnableVisualStyles()
    WinForms.Application.SetCompatibleTextRenderingDefault(False)

    # ------------------- Set the DPI Awareness mode for the process -------------------
    # This needs to be done at the earliest and doing this in __init__() or
    # in create() doesn't work
    #
    # Check the version of windows and make sure we are setting the DPI mode
    # with the most up to date API
    # Windows Versioning Check Sources : https://www.lifewire.com/windows-version-numbers-2625171
    # and https://docs.microsoft.com/en-us/windows/release-information/
    win_version = Environment.OSVersion.Version
    # Represents Windows 10 Build 1703 and beyond which should use
    # SetProcessDpiAwarenessContext(-4) for DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2
    # Valid values: https://learn.microsoft.com/en-us/windows/win32/hidpi/dpi-awareness-context
    if (win_version.Major > 10) or (
        win_version.Major == 10 and win_version.Build >= 15063
    ):
        windll.user32.SetProcessDpiAwarenessContext.restype = wintypes.BOOL
        windll.user32.SetProcessDpiAwarenessContext.argtypes = [c_void_p]
        # SetProcessDpiAwarenessContext returns False(0) on Failure
        if windll.user32.SetProcessDpiAwarenessContext(-4) == 0:  # pragma: no cover
            print("WARNING: Failed to set the DPI Awareness mode for the app.")
    else:  # pragma: no cover
        print(
            "WARNING: Your Windows version doesn't support DPI Awareness setting.  "
            "We recommend you upgrade to at least Windows 10 Build 1703."
        )
    # ----------------------------------------------------------------------------------

    def __init__(self, interface):
        self.interface = interface
        self.interface._impl = self

        # Winforms app exit is tightly bound to the close of the MainWindow.
        # The FormClosing message on MainWindow triggers the "on_exit" handler
        # (which might abort the exit). However, on success, it will request the
        # app (and thus the Main Window) to close, causing another close event.
        # So - we have a flag that is only ever sent once a request has been
        # made to exit the native app. This flag can be used to shortcut any
        # window-level close handling.
        self._is_exiting = False

        # Winforms cursor visibility is a stack; If you call hide N times, you
        # need to call Show N times to make the cursor re-appear. Store a local
        # boolean to allow us to avoid building a deep stack.
        self._cursor_visible = True

        self.loop = WinformsProactorEventLoop()
        asyncio.set_event_loop(self.loop)

    def create(self):
        self.native = WinForms.Application
        self.app_context = WinForms.ApplicationContext()
        self.app_dispatcher = Dispatcher.CurrentDispatcher

        # Register the DisplaySettingsChanged event handler
        SystemEvents.DisplaySettingsChanged += WeakrefCallable(
            self.winforms_DisplaySettingsChanged
        )

        # Ensure that TLS1.2 and TLS1.3 are enabled for HTTPS connections.
        # For some reason, some Windows installs have these protocols
        # turned off by default. SSL3, TLS1.0 and TLS1.1 are *not* enabled
        # as they are deprecated protocols and their use should *not* be
        # encouraged.
        try:
            ServicePointManager.SecurityProtocol |= SecurityProtocolType.Tls12
        except AttributeError:  # pragma: no cover
            print(
                "WARNING: Your Windows .NET install does not support TLS1.2. "
                "You may experience difficulties accessing some web server content."
            )
        try:
            ServicePointManager.SecurityProtocol |= SecurityProtocolType.Tls13
        except AttributeError:  # pragma: no cover
            print(
                "WARNING: Your Windows .NET install does not support TLS1.3. "
                "You may experience difficulties accessing some web server content."
            )

        # Call user code to populate the main window
        self.interface._startup()
        self.create_app_commands()
        self.create_menus()
        self.interface.main_window._impl.set_app(self)

    ######################################################################
    # Commands and menus
    ######################################################################

    def create_app_commands(self):
        self.interface.commands.add(
            # About should be the last item in the Help menu, in a section on its own.
            toga.Command(
                lambda _: self.interface.about(),
                f"About {self.interface.formal_name}",
                group=toga.Group.HELP,
                section=sys.maxsize,
            ),
            #
            toga.Command(None, "Preferences", group=toga.Group.FILE),
            #
            # On Windows, the Exit command doesn't usually contain the app name. It
            # should be the last item in the File menu, in a section on its own.
            toga.Command(
                lambda _: self.interface.on_exit(),
                "Exit",
                shortcut=Key.MOD_1 + "q",
                group=toga.Group.FILE,
                section=sys.maxsize,
            ),
            #
            toga.Command(
                lambda _: self.interface.visit_homepage(),
                "Visit homepage",
                enabled=self.interface.home_page is not None,
                group=toga.Group.HELP,
            ),
        )

    def _submenu(self, group, menubar):
        try:
            return self._menu_groups[group]
        except KeyError:
            if group is None:
                submenu = menubar
            else:
                parent_menu = self._submenu(group.parent, menubar)

                submenu = WinForms.ToolStripMenuItem(group.text)

                # Top level menus are added in a different way to submenus
                if group.parent is None:
                    parent_menu.Items.Add(submenu)
                else:
                    parent_menu.DropDownItems.Add(submenu)

            self._menu_groups[group] = submenu
        return submenu

    def create_menus(self):
        if self.interface.main_window is None:  # pragma: no branch
            # The startup method may create commands before creating the window, so
            # we'll call create_menus again after it returns.
            return

        window = self.interface.main_window._impl
        menubar = window.native.MainMenuStrip
        if menubar:
            menubar.Items.Clear()
        else:
            # The menu bar doesn't need to be positioned, because its `Dock` property
            # defaults to `Top`.
            menubar = WinForms.MenuStrip()
            window.native.Controls.Add(menubar)
            window.native.MainMenuStrip = menubar
            menubar.SendToBack()  # In a dock, "back" means "top".

        # The File menu should come before all user-created menus.
        self._menu_groups = {}
        toga.Group.FILE.order = -1

        submenu = None
        for cmd in self.interface.commands:
            submenu = self._submenu(cmd.group, menubar)
            if isinstance(cmd, Separator):
                submenu.DropDownItems.Add("-")
            else:
                submenu = self._submenu(cmd.group, menubar)
                item = WinForms.ToolStripMenuItem(cmd.text)
                item.Click += WeakrefCallable(cmd._impl.winforms_Click)
                if cmd.shortcut is not None:
                    try:
                        item.ShortcutKeys = toga_to_winforms_key(cmd.shortcut)
                        # The Winforms key enum is... daft. The "oem" key
                        # values render as "Oem" or "Oemcomma", so we need to
                        # *manually* set the display text for the key shortcut.
                        item.ShortcutKeyDisplayString = toga_to_winforms_shortcut(
                            cmd.shortcut
                        )
                    except (
                        ValueError,
                        InvalidEnumArgumentException,
                    ) as e:  # pragma: no cover
                        # Make this a non-fatal warning, because different backends may
                        # accept different shortcuts.
                        print(f"WARNING: invalid shortcut {cmd.shortcut!r}: {e}")

                item.Enabled = cmd.enabled

                cmd._impl.native.append(item)
                submenu.DropDownItems.Add(item)

        # Required for font scaling on DPI changes
        window.original_menubar_font = menubar.Font
        window.resize_content()

    ######################################################################
    # App lifecycle
    ######################################################################

    def exit(self):  # pragma: no cover
        self._is_exiting = True
        self.native.Exit()

    def _run_app(self):  # pragma: no cover
        # Enable coverage tracing on this non-Python-created thread
        # (https://github.com/nedbat/coveragepy/issues/686).
        if threading._trace_hook:
            sys.settrace(threading._trace_hook)

        try:
            self.create()

            # This catches errors in handlers, and prints them
            # in a usable form.
            self.native.ThreadException += WeakrefCallable(winforms_thread_exception)

            self.loop.run_forever(self)
        except Exception as e:
            # In case of an unhandled error at the level of the app,
            # preserve the Python stacktrace
            self._exception = e
        else:
            self._exception = None

    def main_loop(self):
        thread = Threading.Thread(Threading.ThreadStart(self._run_app))
        thread.SetApartmentState(Threading.ApartmentState.STA)
        thread.Start()
        thread.Join()

        # If the thread has exited, the _exception attribute will exist.
        # If it's non-None, raise it, as it indicates the underlying
        # app thread had a problem; this is effectibely a re-raise over
        # a thread boundary.
        if self._exception:  # pragma: no cover
            raise self._exception

    def set_main_window(self, window):
        self.app_context.MainForm = window._impl.native

    ######################################################################
    # App resources
    ######################################################################

    def get_screens(self):
        primary_screen = ScreenImpl(WinForms.Screen.PrimaryScreen)
        screen_list = [primary_screen] + [
            ScreenImpl(native=screen)
            for screen in WinForms.Screen.AllScreens
            if screen != primary_screen.native
        ]
        return screen_list

    ######################################################################
    # App capabilities
    ######################################################################

    def beep(self):
        SystemSounds.Beep.Play()

    def show_about_dialog(self):
        message_parts = []
        if self.interface.version is not None:
            message_parts.append(
                f"{self.interface.formal_name} v{self.interface.version}"
            )
        else:
            message_parts.append(self.interface.formal_name)

        if self.interface.author is not None:
            message_parts.append(f"Author: {self.interface.author}")
        if self.interface.description is not None:
            message_parts.append(f"\n{self.interface.description}")
        self.interface.main_window.info_dialog(
            f"About {self.interface.formal_name}", "\n".join(message_parts)
        )

    ######################################################################
    # Cursor control
    ######################################################################

    def hide_cursor(self):
        if self._cursor_visible:
            WinForms.Cursor.Hide()
        self._cursor_visible = False

    def show_cursor(self):
        if not self._cursor_visible:
            WinForms.Cursor.Show()
        self._cursor_visible = True

    ######################################################################
    # Window control
    ######################################################################

    def get_current_window(self):
        for window in self.interface.windows:
            if WinForms.Form.ActiveForm == window._impl.native:
                return window._impl
        return None

    def set_current_window(self, window):
        window._impl.native.Activate()

    ######################################################################
    # Full screen control
    ######################################################################

    def enter_full_screen(self, windows):
        for window in windows:
            window._impl.set_full_screen(True)

    def exit_full_screen(self, windows):
        for window in windows:
            window._impl.set_full_screen(False)


class DocumentApp(App):  # pragma: no cover
    def create_app_commands(self):
        super().create_app_commands()
        self.interface.commands.add(
            toga.Command(
                lambda w: self.open_file,
                text="Open...",
                shortcut=Key.MOD_1 + "o",
                group=toga.Group.FILE,
                section=0,
            ),
        )
