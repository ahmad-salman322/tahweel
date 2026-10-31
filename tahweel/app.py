"""Tahweel window — drop PDF, Word or Markdown files and convert them to one of the others (GTK4 + libadwaita).

The interface is Arabic or English. The first launch asks which; the switch at the bottom of the sidebar
changes it at any time (the window is rebuilt in place, the file list is kept).
"""

import json
import math
import os
import threading

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gdk, Gio, GLib, GObject, Gtk, Pango  # noqa: E402

from . import __version__, convert, detect, output_path  # noqa: E402
from .cli import explain  # noqa: E402

APP_ID = "io.github.ahmad.Tahweel"
CONFIG = os.path.join(GLib.get_user_config_dir(), "tahweel", "settings.json")
LANG_OVERRIDE = os.environ.get("TAHWEEL_LANG")  # for testing; not saved
LANG = "ar"

TARGETS = ("docx", "pdf", "md")
FORMAT = {  # name, file extension shown on badges, colour
    "pdf": ("PDF", "PDF", "#C43D33"),
    "docx": ("Word", "DOCX", "#2F5BD3"),
    "md": ("Markdown", "MD", "#1E7F6A"),
}

AR = {
    "Tahweel": "تحويل",
    "PDF, Word and Markdown converter": "محوّل بين PDF ووورد وماركداون",
    "Convert to": "حوّل إلى",
    "Word": "وورد",
    "Markdown": "ماركداون",
    "Convert to {fmt}": "حوّل إلى {fmt}",
    "Convert again": "حوّل من جديد",
    "Drag files here": "اسحب الملفات إلى هنا",
    "PDF, Word and Markdown files, in Arabic, Hebrew or English":
        "ملفات PDF ووورد وماركداون، بالعربية أو العبرية أو الإنجليزية",
    "Choose files": "اختيار ملفات",
    "Add files": "إضافة ملفات",
    "Clear list": "مسح القائمة",
    "About Tahweel": "عن تحويل",
    "Options": "خيارات",
    "Keep page background": "الإبقاء على خلفية الصفحة",
    "Letterhead behind PDF pages": "الترويسة خلف صفحات PDF",
    "Open when done": "فتح الملف بعد التحويل",
    "Save to": "مكان الحفظ",
    "Next to each file": "بجانب كل ملف",
    "Choose folder": "اختيار مجلد",
    "Save next to each file": "الحفظ بجانب كل ملف",
    "Language": "اللغة",
    "Show sidebar": "إظهار الشريط الجانبي",
    "Waiting": "في الانتظار",
    "Converting…": "جارٍ التحويل…",
    "Page {a} of {b}": "صفحة {a} من {b}",
    "Open": "فتح",
    "Show in folder": "إظهار في المجلد",
    "Remove": "إزالة",
    "Failed: {msg}": "فشل التحويل: {msg}",
    "Already {fmt}. Pick another format in the sidebar.": "الملف {fmt} أصلاً. اختر صيغة أخرى من الشريط الجانبي.",
    "PDF output needs LibreOffice": "التحويل إلى PDF يحتاج برنامج ليبر أوفيس",
    "Password-protected PDF": "ملف PDF محمي بكلمة مرور",
    "No text found. Is it a scanned PDF? The result is empty.":
        "لم يُعثر على نص. هل الملف ممسوح ضوئياً؟ الناتج فارغ.",
    "Only PDF, Word (.docx) and Markdown files can be added": "يمكن إضافة ملفات PDF ووورد (docx) وماركداون فقط",
    "Documents": "المستندات",
}


def _(s):
    return AR.get(s, s) if LANG == "ar" else s


def fmt_name(fmt):
    return _(FORMAT[fmt][0])


def count_files(n, after_noun=False):
    """'3 files'; in Arabic the dual changes after a noun ("تحويل ملفين", not "ملفان")."""
    if LANG != "ar":
        return "1 file" if n == 1 else f"{n} files"
    if n == 1:
        return "ملف واحد"
    if n == 2:
        return "ملفين" if after_noun else "ملفان"
    return f"{n} ملفات" if 3 <= n <= 10 else f"{n} ملفاً"


def converted_msg(n):
    return f"تم تحويل {count_files(n, True)}" if LANG == "ar" else f"Converted {count_files(n)}"


def failed_msg(n):
    return f"تعذّر تحويل {count_files(n, True)}" if LANG == "ar" else f"{count_files(n).capitalize()} failed"


def error_text(raw):
    """Errors are kept raw on the row and translated when shown, so a language switch re-translates them."""
    return {"encrypted": _("Password-protected PDF"), "no-libreoffice": _("PDF output needs LibreOffice")}.get(raw, raw)


# ---------------------------------------------------------------- settings / helpers

def load_settings():
    data = {"keep_background": True, "open_after": False, "out_dir": None, "target": "docx", "lang": None}
    try:
        with open(CONFIG, encoding="utf-8") as f:
            data.update(json.load(f))
    except (OSError, ValueError):
        pass
    return data


def save_settings(data):
    try:
        os.makedirs(os.path.dirname(CONFIG), exist_ok=True)
        with open(CONFIG, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
    except OSError:
        pass


def short_path(folder):
    home = os.path.expanduser("~")
    return "~" + folder[len(home):] if folder.startswith(home) else folder


def open_uri(path):
    Gio.AppInfo.launch_default_for_uri(Gio.File.new_for_path(path).get_uri(), None)


def show_in_folder(path):
    uri = Gio.File.new_for_path(path).get_uri()
    try:
        bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
        bus.call_sync("org.freedesktop.FileManager1", "/org/freedesktop/FileManager1",
                      "org.freedesktop.FileManager1", "ShowItems", GLib.Variant("(ass)", ([uri], "")),
                      None, Gio.DBusCallFlags.NONE, 2000, None)
    except GLib.Error:
        open_uri(os.path.dirname(path))


def badge(fmt):
    lbl = Gtk.Label(label=FORMAT[fmt][1], valign=Gtk.Align.CENTER, css_classes=["badge", f"fmt-{fmt}"])
    lbl.set_direction(Gtk.TextDirection.LTR)
    return lbl


def _hex(color):
    return tuple(int(color[i:i + 2], 16) / 255 for i in (1, 3, 5))


# ---------------------------------------------------------------- empty-state drawing

class Sheets(Gtk.DrawingArea):
    """Three sheets of paper, one per format; the current target lies on top."""

    ORDER = ("pdf", "docx", "md")

    def __init__(self, target):
        super().__init__(content_width=260, content_height=170, halign=Gtk.Align.CENTER)
        self.target = target
        self.set_draw_func(self._draw)

    def set_target(self, target):
        self.target = target
        self.queue_draw()

    def _sheet(self, cr, fmt, fg, dark, rtl, lifted):
        w, h, ear = 92.0, 118.0, 18.0
        cr.save()
        cr.translate(-w / 2, -h / 2 - (8 if lifted else 0))
        # page with a folded corner
        cr.move_to(0, 0)
        cr.line_to(w - ear, 0)
        cr.line_to(w, ear)
        cr.line_to(w, h)
        cr.line_to(0, h)
        cr.close_path()
        cr.set_source_rgb(*((0.20, 0.20, 0.22) if dark else (1, 1, 1)))
        cr.fill_preserve()
        cr.set_source_rgba(*fg, 0.30 if lifted else 0.18)
        cr.set_line_width(1.2)
        cr.stroke()
        cr.move_to(w - ear, 0)
        cr.line_to(w - ear, ear)
        cr.line_to(w, ear)
        cr.stroke()
        # text lines, aligned to the reading start
        cr.set_source_rgba(*fg, 0.16)
        for i, length in enumerate((0.72, 0.58, 0.66, 0.40)):
            lw = (w - 24) * length
            x = (w - 12 - lw) if rtl else 12
            cr.rectangle(x, 30 + i * 12, lw, 4)
            cr.fill()
        # coloured band with the format name
        cr.set_source_rgb(*_hex(FORMAT[fmt][2]))
        cr.rectangle(0, h - 30, w, 30)
        cr.fill()
        cr.set_source_rgb(1, 1, 1)
        cr.select_font_face("Adwaita Sans", 0, 1)
        cr.set_font_size(13)
        label = FORMAT[fmt][1]
        ext = cr.text_extents(label)
        cr.move_to((w - ext.width) / 2 - ext.x_bearing, h - 15 - ext.height / 2 - ext.y_bearing)
        cr.show_text(label)
        cr.restore()

    def _draw(self, _area, cr, width, height):
        rgba = self.get_color()
        fg = (rgba.red, rgba.green, rgba.blue)
        dark = Adw.StyleManager.get_default().get_dark()
        rtl = self.get_direction() == Gtk.TextDirection.RTL
        slots = {"pdf": (-74, -9), "docx": (0, 0), "md": (74, 9)}  # x offset, rotation (degrees)
        order = [f for f in self.ORDER if f != self.target] + [self.target]
        for fmt in order:
            dx, angle = slots[fmt]
            cr.save()
            cr.translate(width / 2 + dx, height / 2 + 6)
            cr.rotate(math.radians(angle))
            self._sheet(cr, fmt, fg, dark, rtl, fmt == self.target)
            cr.restore()


# ---------------------------------------------------------------- file rows

class FileRow(Gtk.ListBoxRow):
    """One file: source badge, name, status, thin progress line, target badge and actions."""

    def __init__(self, path, target, on_remove):
        super().__init__(activatable=False, selectable=False, css_classes=["file-row"])
        self.path = path
        self.result = None
        self.state = "waiting"
        self.message = ""
        self.warnings = []
        self._pulsing = False

        box = Gtk.Box(spacing=14)
        box.append(badge(detect(path)))

        text = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2, hexpand=True, valign=Gtk.Align.CENTER)
        self.title = Gtk.Label(label=os.path.basename(path), xalign=0, ellipsize=Pango.EllipsizeMode.END, css_classes=["file-name"])
        self.subtitle = Gtk.Label(xalign=0, ellipsize=Pango.EllipsizeMode.END, css_classes=["file-status"])
        self.progress = Gtk.ProgressBar(visible=False, css_classes=["thin"], margin_top=6)
        text.append(self.title)
        text.append(self.subtitle)
        text.append(self.progress)
        box.append(text)

        self.arrow = Gtk.Image(icon_name="go-next-symbolic", css_classes=["dim-label"])
        self.target_badge = Gtk.Box()
        box.append(self.arrow)
        box.append(self.target_badge)
        self.set_target(target)

        self.status_icon = Gtk.Image(visible=False)
        self.open_btn = Gtk.Button(icon_name="document-open-symbolic", tooltip_text=_("Open"),
                                   valign=Gtk.Align.CENTER, visible=False, css_classes=["flat"])
        self.folder_btn = Gtk.Button(icon_name="folder-open-symbolic", tooltip_text=_("Show in folder"),
                                     valign=Gtk.Align.CENTER, visible=False, css_classes=["flat"])
        self.remove_btn = Gtk.Button(icon_name="window-close-symbolic", tooltip_text=_("Remove"),
                                     valign=Gtk.Align.CENTER, css_classes=["flat", "circular"])
        self.open_btn.connect("clicked", lambda *_a: open_uri(self.result))
        self.folder_btn.connect("clicked", lambda *_a: show_in_folder(self.result))
        self.remove_btn.connect("clicked", lambda *_a: on_remove(self))
        for w in (self.status_icon, self.open_btn, self.folder_btn, self.remove_btn):
            box.append(w)
        self.set_child(box)
        self.set_waiting()

    def set_target(self, target):
        same = detect(self.path) == target
        child = self.target_badge.get_first_child()
        if child is not None:
            self.target_badge.remove(child)
        self.target_badge.append(badge(target))
        self.arrow.set_opacity(0.35 if same else 1)
        self.target_badge.set_opacity(0.35 if same else 1)

    def set_waiting(self):
        self.state = "waiting"
        self._pulsing = False
        self.result = None
        self.subtitle.set_label(short_path(os.path.dirname(self.path)))
        self.subtitle.remove_css_class("error")
        for w in (self.progress, self.status_icon, self.open_btn, self.folder_btn):
            w.set_visible(False)
        self.remove_btn.set_visible(True)

    def set_running(self):
        self.state = "running"
        self.remove_btn.set_visible(False)
        self.status_icon.set_visible(False)
        self.progress.set_fraction(0)
        self.progress.set_visible(True)
        self.subtitle.set_label(_("Converting…"))

    def set_progress(self, done, total):
        if total <= 1:  # Word/Markdown input or the LibreOffice step: nothing to count, show activity
            if done < total and not self._pulsing:
                self._pulsing = True
                GLib.timeout_add(110, self._pulse)
            return
        self._pulsing = False
        self.progress.set_fraction(done / max(1, total))
        if done < total:
            self.subtitle.set_label(_("Page {a} of {b}").format(a=min(done + 1, total - 1), b=total - 1))

    def _pulse(self):
        if self._pulsing and self.state == "running":
            self.progress.pulse()
            return True
        self._pulsing = False
        return False

    def _finish(self, icon, css):
        self._pulsing = False
        self.progress.set_visible(False)
        self.status_icon.set_from_icon_name(icon)
        self.status_icon.set_css_classes([css])
        self.status_icon.set_visible(True)

    def set_done(self, dest, warnings):
        self.state = "done"
        self.result = dest
        self.warnings = list(warnings)
        warn = "no-text" in warnings
        self._finish("dialog-warning-symbolic" if warn else "object-select-symbolic", "warning" if warn else "success")
        self.open_btn.set_visible(True)
        self.folder_btn.set_visible(True)
        self.remove_btn.set_visible(False)
        self.subtitle.set_label(_("No text found. Is it a scanned PDF? The result is empty.") if warn
                                else os.path.basename(dest))
        self.set_tooltip_text(dest)

    def set_skipped(self, fmt):
        self.state = "skipped"
        self.message = fmt
        self._finish("action-unavailable-symbolic", "dim-label")
        self.remove_btn.set_visible(True)
        self.subtitle.set_label(_("Already {fmt}. Pick another format in the sidebar.").format(fmt=fmt_name(fmt)))

    def set_failed(self, message):
        self.state = "failed"
        self.message = message
        self._finish("dialog-error-symbolic", "error")
        self.remove_btn.set_visible(True)
        self.subtitle.set_label(_("Failed: {msg}").format(msg=error_text(message)))
        self.subtitle.add_css_class("error")


# ---------------------------------------------------------------- window

class Window(Adw.ApplicationWindow):
    def __init__(self, app):
        super().__init__(application=app, default_width=900, default_height=620)
        self.settings = load_settings()
        self.rows = []
        self.busy = False
        self.dropping = False

        drop = Gtk.DropTarget.new(Gdk.FileList, Gdk.DragAction.COPY)
        drop.connect("drop", self._on_drop)
        drop.connect("enter", self._drop_enter)
        drop.connect("leave", lambda *_a: self.remove_css_class("dropping"))
        self.add_controller(drop)
        for name, cb in (("clear", self.clear), ("add", self.choose_files), ("convert", self.start)):
            action = Gio.SimpleAction.new(name, None)
            action.connect("activate", lambda *_a, cb=cb: cb())
            self.add_action(action)

        lang = LANG_OVERRIDE or self.settings.get("lang")
        if lang in ("ar", "en"):
            self.apply_language(lang, save=False)
        else:
            self.show_welcome()

    # ---------- language ----------
    def show_welcome(self):
        set_language("ar")
        self.set_title("Tahweel")
        page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6, valign=Gtk.Align.CENTER,
                       halign=Gtk.Align.CENTER, css_classes=["welcome"])
        mark = Gtk.Label(label="تحويل", css_classes=["wordmark", "big"])
        latin = Gtk.Label(label="Tahweel", css_classes=["wordmark-latin"])
        page.append(mark)
        page.append(latin)
        ask = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2, margin_top=36, margin_bottom=18)
        ask.append(Gtk.Label(label="اختر لغة الواجهة", css_classes=["welcome-ask"]))
        ask.append(Gtk.Label(label="Choose the interface language", css_classes=["welcome-ask", "dim-label"]))
        page.append(ask)
        choices = Gtk.Box(spacing=12, homogeneous=True, halign=Gtk.Align.CENTER)
        choices.set_direction(Gtk.TextDirection.LTR)
        for code, name, css in (("en", "English", "lang-en"), ("ar", "العربية", "lang-ar")):
            btn = Gtk.Button(css_classes=["lang-choice", css])
            btn.set_child(Gtk.Label(label=name))
            btn.connect("clicked", lambda _b, c=code: self.apply_language(c, save=True))
            choices.append(btn)
        page.append(choices)
        header = Adw.HeaderBar(show_title=False, css_classes=["flat"])
        view = Adw.ToolbarView(content=page)
        view.add_top_bar(header)
        self.set_content(view)

    def apply_language(self, lang, save=True):
        state = [(r.path, r.state, r.result, r.message, r.warnings) for r in self.rows]
        set_language(lang)
        if save:
            self.settings["lang"] = lang
            save_settings(self.settings)
        self.set_title(_("Tahweel"))
        for cls in ("lang-ar", "lang-en"):
            self.remove_css_class(cls)
        self.add_css_class(f"lang-{lang}")
        self.rows = []
        self.set_content(self._build())
        for path, st, result, message, warnings in state:
            row = self._add_row(path)
            if st == "done" and result:
                row.set_done(result, warnings)
            elif st == "failed":
                row.set_failed(message)
            elif st == "skipped":
                row.set_skipped(message)
        self._refresh()
        pending, self._pending = getattr(self, "_pending", None), None
        if pending:
            self.add_paths(pending)

    # ---------- layout ----------
    def _build(self):
        self._ready = False
        self.split = Adw.OverlaySplitView(min_sidebar_width=250, max_sidebar_width=290, sidebar_width_fraction=0.3)
        self.split.set_sidebar(self._sidebar())
        self.split.set_content(self._content())

        bin_ = Adw.BreakpointBin(child=self.split, width_request=360, height_request=440)
        bp = Adw.Breakpoint.new(Adw.BreakpointCondition.parse("max-width: 640sp"))
        bp.add_setter(self.split, "collapsed", True)
        bp.add_setter(self.sidebar_btn, "visible", True)
        bin_.add_breakpoint(bp)
        self.toasts = Adw.ToastOverlay(child=bin_)
        self._ready = True
        return self.toasts

    def _sidebar(self):
        side = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, css_classes=["side"])
        side.append(Gtk.Label(label=_("Tahweel"), xalign=0, css_classes=["wordmark"]))

        side.append(Gtk.Label(label=_("Convert to"), xalign=0, css_classes=["side-heading"]))
        tiles = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        self.tiles = {}
        group = None
        for fmt in TARGETS:
            tile = Gtk.ToggleButton(css_classes=["format-tile", f"fmt-{fmt}"])
            if group is not None:
                tile.set_group(group)
            group = group or tile
            inner = Gtk.Box(spacing=12)
            inner.append(Gtk.Box(css_classes=["spine"]))
            labels = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, valign=Gtk.Align.CENTER)
            labels.append(Gtk.Label(label=fmt_name(fmt), xalign=0, css_classes=["tile-name"]))
            ext = Gtk.Label(label="\u200e." + fmt, xalign=0, css_classes=["tile-ext"])
            labels.append(ext)
            inner.append(labels)
            tile.set_child(inner)
            tile.connect("toggled", self._tile_toggled, fmt)
            tiles.append(tile)
            self.tiles[fmt] = tile
        side.append(tiles)
        target = self.settings.get("target") if self.settings.get("target") in TARGETS else "docx"
        self.tiles[target].set_active(True)

        side.append(Gtk.Label(label=_("Options"), xalign=0, css_classes=["side-heading"]))
        side.append(self._switch_line(_("Keep page background"), _("Letterhead behind PDF pages"), "keep_background"))
        side.append(self._switch_line(_("Open when done"), None, "open_after"))

        dest = Gtk.Box(spacing=8, css_classes=["opt-line"])
        dest_text = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, hexpand=True, valign=Gtk.Align.CENTER)
        dest_text.append(Gtk.Label(label=_("Save to"), xalign=0, css_classes=["opt-title"]))
        self.dest_label = Gtk.Label(xalign=0, ellipsize=Pango.EllipsizeMode.START, css_classes=["opt-sub"])
        dest_text.append(self.dest_label)
        dest.append(dest_text)
        self.reset_dest = Gtk.Button(icon_name="edit-undo-symbolic", tooltip_text=_("Save next to each file"),
                                     valign=Gtk.Align.CENTER, css_classes=["flat", "circular"])
        self.reset_dest.connect("clicked", lambda *_a: self._set_dest(None))
        pick = Gtk.Button(icon_name="folder-symbolic", tooltip_text=_("Choose folder"), valign=Gtk.Align.CENTER,
                          css_classes=["flat", "circular"])
        pick.connect("clicked", lambda *_a: self.choose_folder())
        dest.append(self.reset_dest)
        dest.append(pick)
        side.append(dest)
        self._set_dest(self.settings.get("out_dir"))

        side.append(Gtk.Box(vexpand=True))
        lang = Adw.ToggleGroup(css_classes=["round", "lang-switch"], tooltip_text=_("Language"))
        lang.set_direction(Gtk.TextDirection.LTR)  # same order in both languages, so it never jumps
        lang.add(Adw.Toggle(name="ar", label="عربي"))
        lang.add(Adw.Toggle(name="en", label="English"))
        lang.set_active_name(LANG)
        lang.connect("notify::active-name", self._lang_toggled)
        self.lang_switch = lang
        side.append(lang)

        view = Adw.ToolbarView(content=Gtk.ScrolledWindow(child=side, hscrollbar_policy=Gtk.PolicyType.NEVER))
        view.add_top_bar(Adw.HeaderBar(show_title=False, show_end_title_buttons=False, css_classes=["flat"]))
        return view

    def _switch_line(self, title, sub, key):
        line = Gtk.Box(spacing=8, css_classes=["opt-line"])
        text = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, hexpand=True, valign=Gtk.Align.CENTER)
        text.append(Gtk.Label(label=title, xalign=0, wrap=True, css_classes=["opt-title"]))
        if sub:
            text.append(Gtk.Label(label=sub, xalign=0, wrap=True, css_classes=["opt-sub"]))
        line.append(text)
        sw = Gtk.Switch(active=self.settings[key], valign=Gtk.Align.CENTER)
        sw.connect("notify::active", lambda s, *_a: self._set(key, s.get_active()))
        line.append(sw)
        return line

    def _content(self):
        header = Adw.HeaderBar(show_title=False, css_classes=["flat"])
        self.sidebar_btn = Gtk.ToggleButton(icon_name="sidebar-show-symbolic", tooltip_text=_("Show sidebar"),
                                            visible=False)
        self.sidebar_btn.bind_property("active", self.split, "show-sidebar",
                                       GObject.BindingFlags.BIDIRECTIONAL)
        header.pack_start(self.sidebar_btn)
        self.add_btn = Gtk.Button(icon_name="list-add-symbolic", tooltip_text=_("Add files"), visible=False)
        self.add_btn.connect("clicked", lambda *_a: self.choose_files())
        header.pack_start(self.add_btn)
        menu = Gio.Menu()
        menu.append(_("Clear list"), "win.clear")
        menu.append(_("About Tahweel"), "app.about")
        header.pack_end(Gtk.MenuButton(icon_name="open-menu-symbolic", menu_model=menu, primary=True))

        self.stack = Gtk.Stack(transition_type=Gtk.StackTransitionType.CROSSFADE, vexpand=True)
        self.stack.add_named(self._empty_page(), "empty")
        self.stack.add_named(self._list_page(), "list")

        self.bottom = Gtk.Box(spacing=12, css_classes=["bottom"], visible=False)
        self.count_label = Gtk.Label(xalign=0, hexpand=True, css_classes=["dim-label"])
        self.convert_btn = Gtk.Button(css_classes=["pill", "go"])
        self.convert_btn.connect("clicked", lambda *_a: self.start())
        self.bottom.append(self.count_label)
        self.bottom.append(self.convert_btn)

        view = Adw.ToolbarView(content=self.stack)
        view.add_top_bar(header)
        view.add_bottom_bar(self.bottom)
        return view

    def _empty_page(self):
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8, valign=Gtk.Align.CENTER,
                      halign=Gtk.Align.FILL, css_classes=["drop-area"], vexpand=True)
        self.sheets = Sheets(self.settings.get("target", "docx"))
        self.sheets.set_margin_bottom(18)
        box.append(self.sheets)
        box.append(Gtk.Label(label=_("Drag files here"), css_classes=["drop-title"]))
        box.append(Gtk.Label(label=_("PDF, Word and Markdown files, in Arabic, Hebrew or English"),
                             wrap=True, justify=Gtk.Justification.CENTER, css_classes=["dim-label"]))
        btn = Gtk.Button(label=_("Choose files"), halign=Gtk.Align.CENTER, margin_top=14, css_classes=["pill"])
        btn.connect("clicked", lambda *_a: self.choose_files())
        box.append(btn)
        return box

    def _list_page(self):
        self.listbox = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE, css_classes=["files"])
        clamp = Adw.Clamp(maximum_size=760, child=self.listbox, margin_top=8, margin_bottom=16,
                          margin_start=12, margin_end=12)
        return Gtk.ScrolledWindow(child=clamp, hscrollbar_policy=Gtk.PolicyType.NEVER, vexpand=True)

    # ---------- settings ----------
    def _set(self, key, value):
        self.settings[key] = value
        save_settings(self.settings)

    @property
    def target(self):
        t = self.settings.get("target")
        return t if t in TARGETS else "docx"

    def _tile_toggled(self, tile, fmt):
        if not tile.get_active():
            return
        self._set("target", fmt)
        if hasattr(self, "sheets"):
            self.sheets.set_target(fmt)
        if not self.busy:  # finished rows were for the old format: queue them again
            for r in self.rows:
                r.set_target(fmt)
                if r.state in ("done", "failed", "skipped"):
                    r.set_waiting()
        self._refresh()

    def _lang_toggled(self, group, *_a):
        lang = group.get_active_name()
        if lang and lang != LANG:
            GLib.idle_add(self.apply_language, lang)  # rebuild after the click finishes

    def _set_dest(self, folder):
        if folder and not os.path.isdir(folder):
            folder = None
        self._set("out_dir", folder)
        self.dest_label.set_label(short_path(folder) if folder else _("Next to each file"))
        self.reset_dest.set_visible(bool(folder))

    # ---------- adding files ----------
    def choose_files(self):
        dlg = Gtk.FileDialog(title=_("Choose files"))
        flt = Gtk.FileFilter(name=_("Documents"))
        for suffix in ("pdf", "docx", "md", "markdown"):
            flt.add_suffix(suffix)
        store = Gio.ListStore.new(Gtk.FileFilter)
        store.append(flt)
        dlg.set_filters(store)
        dlg.set_default_filter(flt)
        dlg.open_multiple(self, None, self._on_files_chosen)

    def _on_files_chosen(self, dlg, res):
        try:
            files = dlg.open_multiple_finish(res)
        except GLib.Error:
            return
        self.add_paths([f.get_path() for f in files if f.get_path()])

    def choose_folder(self):
        Gtk.FileDialog(title=_("Choose folder")).select_folder(self, None, self._on_folder_chosen)

    def _on_folder_chosen(self, dlg, res):
        try:
            f = dlg.select_folder_finish(res)
        except GLib.Error:
            return
        if f and f.get_path():
            self._set_dest(f.get_path())

    def _drop_enter(self, *_a):
        self.add_css_class("dropping")
        return Gdk.DragAction.COPY

    def _on_drop(self, _target, value, *_a):
        self.remove_css_class("dropping")
        if not getattr(self, "_ready", False):  # still on the language screen
            return False
        self.add_paths([f.get_path() for f in value.get_files() if f.get_path()])
        return True

    def _add_row(self, path):
        row = FileRow(path, self.target, self.remove_row)
        self.rows.append(row)
        self.listbox.append(row)
        return row

    def add_paths(self, paths):
        if not getattr(self, "_ready", False):  # language screen still showing
            self._pending = (getattr(self, "_pending", None) or []) + list(paths)
            return
        known = {r.path for r in self.rows}
        skipped = 0
        for p in paths:
            if os.path.isdir(p):
                self.add_paths([os.path.join(p, n) for n in sorted(os.listdir(p)) if detect(n)])
                known = {r.path for r in self.rows}
                continue
            if not detect(p):
                skipped += 1
                continue
            p = os.path.abspath(p)
            if p not in known:
                self._add_row(p)
                known.add(p)
        if skipped:
            self.toasts.add_toast(Adw.Toast(title=_("Only PDF, Word (.docx) and Markdown files can be added")))
        self._refresh()

    def remove_row(self, row):
        if self.busy:
            return
        self.rows.remove(row)
        self.listbox.remove(row)
        self._refresh()

    def clear(self):
        if self.busy or not getattr(self, "_ready", False):
            return
        for r in self.rows:
            self.listbox.remove(r)
        self.rows.clear()
        self._refresh()

    def _refresh(self):
        if not getattr(self, "_ready", False):
            return
        has = bool(self.rows)
        self.stack.set_visible_child_name("list" if has else "empty")
        self.bottom.set_visible(has)
        self.add_btn.set_visible(has)
        self.count_label.set_label(count_files(len(self.rows)) if has else "")
        pending = any(r.state in ("waiting", "failed") for r in self.rows)
        target = self.target
        self.convert_btn.set_label(_("Convert to {fmt}").format(fmt=fmt_name(target)) if pending or not has
                                   else _("Convert again"))
        for cls in ("go-docx", "go-pdf", "go-md"):
            self.convert_btn.remove_css_class(cls)
        self.convert_btn.add_css_class(f"go-{target}")
        for fmt in TARGETS:
            self.remove_css_class(f"target-{fmt}")
        self.add_css_class(f"target-{target}")
        self.convert_btn.set_sensitive(not self.busy and has)
        self.lang_switch.set_sensitive(not self.busy)
        for tile in self.tiles.values():
            tile.set_sensitive(not self.busy)

    # ---------- conversion ----------
    def start(self):
        if self.busy or not self.rows:
            return
        todo = [r for r in self.rows if r.state in ("waiting", "failed")]
        if not todo:  # "convert again": redo everything
            for r in self.rows:
                r.set_waiting()
            todo = list(self.rows)
        self.busy = True
        self._refresh()
        opts = dict(self.settings)
        for r in todo:
            r.set_running()
        threading.Thread(target=self._worker, args=(todo, opts), daemon=True).start()

    def _worker(self, rows, opts):
        ok = failed = 0
        target = opts.get("target", "docx")
        for row in rows:
            if detect(row.path) == target:
                GLib.idle_add(row.set_skipped, target)
                continue
            dest = output_path(row.path, opts.get("out_dir"), to=target)

            def progress(done, total, row=row):
                GLib.idle_add(row.set_progress, done, total)

            try:
                doc = convert(row.path, dest, progress=progress, keep_background=opts["keep_background"], to=target)
            except Exception as e:  # noqa: BLE001 - shown on the row
                raw = str(e) if str(e) in ("encrypted", "no-libreoffice") else explain(e)
                GLib.idle_add(row.set_failed, raw)
                failed += 1
                continue
            GLib.idle_add(row.set_done, dest, list(doc.warnings))
            if opts["open_after"]:
                GLib.idle_add(open_uri, dest)
            ok += 1
        GLib.idle_add(self._finished, ok, failed)

    def _finished(self, ok, failed):
        self.busy = False
        self._refresh()
        if failed:
            self.toasts.add_toast(Adw.Toast(title=failed_msg(failed)))
        elif ok:
            toast = Adw.Toast(title=converted_msg(ok), timeout=4)
            last = next((r for r in reversed(self.rows) if r.state == "done"), None)
            if last and ok == 1:
                toast.set_button_label(_("Open"))
                toast.connect("button-clicked", lambda *_a: open_uri(last.result))
            self.toasts.add_toast(toast)


def set_language(lang):
    global LANG
    LANG = lang
    Gtk.Widget.set_default_direction(Gtk.TextDirection.RTL if lang == "ar" else Gtk.TextDirection.LTR)


# ---------------------------------------------------------------- style

def _format_css():
    out = []
    for fmt, (_n, _e, color) in FORMAT.items():
        out.append(f"""
.badge.fmt-{fmt} {{ background-color: {color}; }}
.format-tile.fmt-{fmt} .spine {{ background-color: {color}; }}
.format-tile.fmt-{fmt}:checked {{ background-color: alpha({color}, 0.16); }}
.format-tile.fmt-{fmt}:checked .tile-ext {{ color: {color}; opacity: 1; }}
button.go.go-{fmt} {{ background-color: {color}; color: white; }}
button.go.go-{fmt}:hover {{ background-color: shade({color}, 1.08); }}
button.go.go-{fmt}:active {{ background-color: shade({color}, 0.92); }}
window.dropping.target-{fmt} .drop-area {{ border-color: {color}; }}
""")
    return "".join(out)


CSS = """
window.lang-ar { font-family: "Noto Sans Arabic UI", "Noto Sans Arabic", "Adwaita Sans", sans-serif; }

.wordmark { font-family: "Noto Kufi Arabic", "Adwaita Sans", sans-serif; font-weight: 900; font-size: 22pt;
            margin: 0 4px 22px 4px; }
.wordmark.big { font-size: 46pt; margin: 0; }
.wordmark-latin { font-weight: 300; font-size: 15pt; opacity: 0.55; }
.welcome-ask { font-size: 12pt; }

button.lang-choice { min-width: 150px; min-height: 64px; border-radius: 14px; font-size: 15pt; font-weight: 600; }
button.lang-choice.lang-ar { font-family: "Noto Kufi Arabic", sans-serif; }

.side { padding: 4px 14px 16px 14px; }
.side-heading { font-weight: 700; font-size: 10pt; opacity: 0.6; margin: 20px 6px 8px 6px; }

button.format-tile { padding: 6px 10px 6px 8px; border-radius: 10px; background: none; min-height: 46px; }
button.format-tile:hover { background-color: alpha(currentColor, 0.06); }
.format-tile .spine { min-width: 5px; border-radius: 3px; margin: 2px 0; }
.tile-name { font-weight: 600; }
.tile-ext { font-size: 9pt; opacity: 0.55; }

.opt-line { padding: 8px 6px; }
.opt-title { font-size: 10.5pt; }
.opt-sub { font-size: 9pt; opacity: 0.55; }

.lang-switch { margin: 18px 4px 0 4px; }

.drop-area { border: 2px dashed alpha(currentColor, 0.14); border-radius: 22px; margin: 6px 24px 24px 24px;
             padding: 32px 24px; }
window.dropping .drop-area { background-color: alpha(currentColor, 0.04); }
.drop-title { font-size: 17pt; font-weight: 700; }

.files { background: none; }
.files row.file-row { padding: 12px 6px; border-bottom: 1px solid alpha(currentColor, 0.08); }
.files row.file-row:last-child { border-bottom: none; }
.file-name { font-weight: 600; }
.file-status { font-size: 9.5pt; opacity: 0.6; }
.file-status.error { color: @error_color; opacity: 1; }

.badge { color: white; font-weight: 800; font-size: 8pt; border-radius: 5px; padding: 3px 7px;
         font-family: "Adwaita Sans", sans-serif; }

progressbar.thin trough, progressbar.thin progress { min-height: 3px; border-radius: 2px; }

.bottom { padding: 10px 18px 14px 18px; }
button.go { padding: 8px 26px; font-weight: 700; }
button.go:disabled { opacity: 0.5; }

image.success { color: @success_color; }
image.warning { color: @warning_color; }
image.error { color: @error_color; }
"""


class App(Adw.Application):
    def __init__(self, application_id=APP_ID, flags=Gio.ApplicationFlags.HANDLES_OPEN):
        super().__init__(application_id=application_id, flags=flags)
        GLib.set_application_name("Tahweel")
        about = Gio.SimpleAction.new("about", None)
        about.connect("activate", self._about)
        self.add_action(about)
        quit_ = Gio.SimpleAction.new("quit", None)
        quit_.connect("activate", lambda *_a: self.quit())
        self.add_action(quit_)
        self.set_accels_for_action("app.quit", ["<Control>q"])
        self.set_accels_for_action("win.add", ["<Control>o"])
        self.set_accels_for_action("win.convert", ["<Control>Return"])
        self.set_accels_for_action("win.clear", ["<Control>l"])

    def do_startup(self):
        Adw.Application.do_startup(self)
        css = Gtk.CssProvider()
        css.load_from_string(CSS + _format_css())
        Gtk.StyleContext.add_provider_for_display(Gdk.Display.get_default(), css,
                                                  Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)

    def _window(self):
        win = self.get_active_window()
        if win is None:
            win = Window(self)
        win.present()
        return win

    def do_activate(self):
        self._window()

    def do_open(self, files, _n, _hint):
        self._window().add_paths([f.get_path() for f in files if f.get_path()])

    def _about(self, *_a):
        Adw.AboutDialog(application_name=_("Tahweel"), application_icon=APP_ID, version=__version__,
                        developer_name="Ahmad", comments=_("PDF, Word and Markdown converter"),
                        license_type=Gtk.License.MIT_X11).present(self.get_active_window())


def run(argv):
    import sys
    return App().run([sys.argv[0], *argv])
