import os
import sys
import json
import copy
import threading
import subprocess
import tkinter as tk
from tkinter import messagebox, ttk, filedialog, simpledialog, font as tkfont
import customtkinter as ctk
from tkinterdnd2 import TkinterDnD, DND_FILES

# Detect platform
IS_MAC = sys.platform == "darwin"
IS_WINDOWS = sys.platform == "win32"

if getattr(sys, 'frozen', False):  # if running as .exe
    BASE_DIR = os.path.expanduser(os.path.join("~", "Documents", "ShortcutsApp"))
else:
    BASE_DIR = os.path.dirname(os.path.abspath(__file__))

DATA_FILE = os.path.join(BASE_DIR, "shortcuts_data.json")

# Ensure data folder exists
os.makedirs(BASE_DIR, exist_ok=True)


# ============================================================================
# FIX #1 — FILE TRACKING BY INODE
# Files are now stored as dicts: {"path": ..., "inode": ..., "parent": ...}
# If a file/folder is renamed in the same directory, the inode lookup finds it.
# ============================================================================

def get_file_info(path):
    """Create a tracked file entry with inode so renames can be followed."""
    path = os.path.normpath(path)
    try:
        inode = os.stat(path).st_ino
    except Exception:
        inode = None
    return {"path": path, "inode": inode, "parent": os.path.dirname(path)}


def resolve_file_path(file_entry):
    """Return the current path for a file entry, searching by inode if needed."""
    if isinstance(file_entry, str):
        return file_entry  # backward-compat with old string-only saves

    path = file_entry.get("path", "")
    inode = file_entry.get("inode")
    parent = file_entry.get("parent", "")

    if os.path.exists(path):
        return path

    # Path no longer valid — scan the parent directory for matching inode
    if inode and parent and os.path.isdir(parent):
        try:
            for entry in os.scandir(parent):
                try:
                    if os.stat(entry.path).st_ino == inode:
                        file_entry["path"] = entry.path  # update cached path
                        return entry.path
                except Exception:
                    pass
        except Exception:
            pass

    return path  # return stale path if nothing found


def normalize_file_list(files):
    """Convert a list of strings or dicts into a list of tracked file dicts."""
    result = []
    for f in files:
        if isinstance(f, str):
            result.append(get_file_info(f))
        elif isinstance(f, dict):
            result.append(f)
    return result


# ============================================================================
# UTILITY FUNCTIONS
# ============================================================================

def open_file(path):
    """Cross-platform file opener with better Windows support"""
    try:
        path = os.path.normpath(path)

        if sys.platform.startswith("darwin"):
            subprocess.run(['open', path], check=True)
        elif sys.platform.startswith("linux"):
            subprocess.run(['xdg-open', path], check=True)
        else:
            os.startfile(path)

    except subprocess.CalledProcessError as e:
        messagebox.showerror("Error", f"Failed to open file:\n{path}\n\nError: {str(e)}")
    except OSError as e:
        messagebox.showerror("Error", f"Failed to open file:\n{path}\n\nError: {str(e)}")
    except Exception as e:
        messagebox.showerror("Error", f"Unexpected error opening file:\n{path}\n\nError: {str(e)}")


# ============================================================================
# CUSTOM DIALOGS
# ============================================================================

class CustomInputDialog(ctk.CTkToplevel):
    def __init__(self, parent, title, prompt, default=""):
        super().__init__(parent)
        self.title(title)
        self.geometry("300x120")
        self.resizable(False, False)
        self.user_input = None
        self.transient(parent)
        self.lift()
        self.update_idletasks()

        parent_x = parent.winfo_rootx()
        parent_y = parent.winfo_rooty()
        parent_width = parent.winfo_width()
        parent_height = parent.winfo_height()
        win_width = self.winfo_width()
        win_height = self.winfo_height()
        x = parent_x + (parent_width - win_width) // 2
        y = parent_y + (parent_height - win_height) // 2
        self.geometry(f"+{x}+{y}")

        ctk.CTkLabel(self, text=prompt).pack(pady=5)
        self.entry = ctk.CTkEntry(self)
        self.entry.pack(fill="x", padx=10)
        self.entry.insert(0, default)
        ctk.CTkButton(self, text="OK", command=self.submit).pack(pady=10)
        self.entry.bind("<Return>", lambda e: self.submit())
        self.bind("<Escape>", lambda e: self.destroy())   # Fix #3: ESC cancels
        self.entry.focus()
        self.grab_set()
        self.wait_window()

    def submit(self):
        self.user_input = self.entry.get()
        self.destroy()


# ============================================================================
# EDITOR WINDOW
# ============================================================================

class EditorWindow(ctk.CTkToplevel):
    def __init__(self, master, button, app):
        super().__init__(master)

        master.update_idletasks()
        root_x = master.winfo_x()
        root_y = master.winfo_y()
        root_w = master.winfo_width()
        root_h = master.winfo_height()

        w = 400
        h = 320
        x = root_x + (root_w // 2) - (w // 2)
        y = root_y + (root_h // 2) - (h // 2)

        self.btn = button
        self.app = app
        self.title(f"Edit: {button.data['name']}")
        self.geometry(f'{w}x{h}+{x}+{y}')
        # Work on a copy; only commit on Save & Close
        self.file_list = list(button.data["files"])
        self._original_file_list = list(button.data["files"])  # Fix #3: for ESC restore

        self.transient(master)
        self.grab_set()

        # Outer frame with tight padding
        frame = ctk.CTkFrame(self, fg_color="transparent")
        frame.pack(fill="both", expand=True, padx=8, pady=8)

        # Style the treeview to look more minimal
        style = ttk.Style()
        style.configure("Minimal.Treeview",
                        rowheight=22,
                        font=("Arial", 11),
                        borderwidth=0)
        style.configure("Minimal.Treeview.Heading", font=("Arial", 11))
        style.layout("Minimal.Treeview", [
            ("Minimal.Treeview.treearea", {"sticky": "nswe"})
        ])

        # Tree with a thin border frame around it
        tree_border = ctk.CTkFrame(frame, corner_radius=6, border_width=1,
                                   border_color="#d0d0d0", fg_color="#ffffff")
        tree_border.pack(fill="both", expand=True)

        self.tree = ttk.Treeview(tree_border, columns=("path",), show="tree",
                                 style="Minimal.Treeview")
        self.tree.pack(fill="both", expand=True, padx=2, pady=2)

        # Bind delete keys - cross-platform
        self.tree.bind("<Delete>", self.delete_selected)
        if IS_MAC:
            self.tree.bind("<Command-BackSpace>", self.delete_selected)
        else:
            self.tree.bind("<Control-BackSpace>", self.delete_selected)
        self.tree.bind("<BackSpace>", self.delete_selected)
        self.tree.bind("<Double-1>", lambda e: self.open_selected())

        # Fix #3: ESC cancels without saving
        self.bind("<Escape>", self.cancel)

        self.tree.focus_set()

        # Drag-and-drop (Fix #2: handled in _process_new — folders kept as-is)
        self.tree.drop_target_register(DND_FILES)
        self.tree.dnd_bind("<<Drop>>", self.drop_files)

        self.refresh_list()

        # Bottom button row — compact, fixed-width, not stretched
        btnf = ctk.CTkFrame(frame, fg_color="transparent")
        btnf.pack(fill="x", pady=(6, 0))

        _btn = dict(height=26, corner_radius=5, width=100)
        ctk.CTkButton(btnf, text="Add Files", command=self.add_files, **_btn).pack(side="left")
        ctk.CTkButton(btnf, text="Cancel", command=self.cancel,
                      fg_color="#888888", hover_color="#666666", **_btn).pack(side="right")
        ctk.CTkButton(btnf, text="Save & Close", command=self.on_close, **_btn).pack(side="right", padx=(0, 6))

        self.protocol("WM_DELETE_WINDOW", self.on_close)

        # Multiple selections
        self.drag_selecting = False
        self.last_selected_items = set()

        self.tree.bind("<ButtonPress-1>", self.on_drag_select_start)
        self.tree.bind("<B1-Motion>", self.on_drag_select_move)
        self.tree.bind("<ButtonRelease-1>", self.on_drag_select_end)

    def cancel(self, event=None):
        """Close without saving — restores the original file list."""
        self.btn.data["files"] = self._original_file_list
        self.destroy()

    def on_drag_select_start(self, event):
        self.drag_selecting = True
        if not (event.state & 0x0004):
            self.tree.selection_remove(self.tree.selection())
        self.last_selected_items.clear()
        self.select_item_at(event)

    def on_drag_select_move(self, event):
        if self.drag_selecting:
            self.select_item_at(event)

    def on_drag_select_end(self, event):
        self.drag_selecting = False

    def select_item_at(self, event):
        x, y = event.x, event.y
        item = self.tree.identify_row(y)
        if item and item not in self.last_selected_items:
            self.tree.selection_add(item)
            self.last_selected_items.add(item)

    def refresh_list(self):
        if hasattr(self, "tree") and self.tree.winfo_exists():
            self.tree.delete(*self.tree.get_children())
        for f in self.file_list:
            resolved = resolve_file_path(f)
            name = os.path.basename(resolved)
            exists = os.path.exists(resolved)
            display = name if exists else f"⚠ {name}  (missing — may have been renamed/moved)"
            self.tree.insert("", "end", text=display, values=(resolved,))

    def add_files(self):
        paths = filedialog.askopenfilenames()
        self._process_new(paths)

    def drop_files(self, event):
        # Fix #2: pass raw paths — folders are kept as folders, not expanded
        paths = []
        for item in self.tk.splitlist(event.data):
            item = item.strip('{}')
            item = os.path.normpath(item)
            paths.append(item)
        self._process_new(paths)

    def _process_new(self, paths):
        # Fix #1: store as tracked dicts; Fix #2: no folder expansion
        existing_paths = {resolve_file_path(f) for f in self.file_list}
        for p in paths:
            p = os.path.normpath(p)
            if p in existing_paths:
                if not messagebox.askyesno("Duplicate?", f"{os.path.basename(p)} exists. Add anyway?"):
                    continue
            self.file_list.append(get_file_info(p))
            existing_paths.add(p)
        self.refresh_list()

    def delete_selected(self, event=None):
        sel = self.tree.selection()
        if not sel:
            return

        if len(sel) == 1:
            filename = os.path.basename(self.tree.item(sel[0])["values"][0])
            message = f"Remove '{filename}' from this shortcut?"
        else:
            message = f"Remove {len(sel)} files from this shortcut?"

        if messagebox.askyesno("Remove Files", message):
            to_remove = {self.tree.item(iid)["values"][0] for iid in sel}
            self.file_list = [f for f in self.file_list if resolve_file_path(f) not in to_remove]
            self.refresh_list()

    def open_selected(self):
        sel = self.tree.selection()
        if sel:
            open_file(self.tree.item(sel[0])["values"][0])

    def on_close(self):
        self.btn.data["files"] = self.file_list
        self.app.save_data()
        self.destroy()


# ============================================================================
# SHORTCUT BUTTON
# ============================================================================

class ShortcutButton(ctk.CTkButton):
    def __init__(self, master, data, app, **kwargs):
        super().__init__(master, text=data["name"], **kwargs)
        self.app = app
        self.data = data
        self.place(x=data.get("x", 10), y=data.get("y", 10))

        if "color" not in self.data:
            self.data["color"] = self.app.config.get("btn_bg", "#1f6aa5")

        self.configure(fg_color=self.data["color"])

        # Enable drag and drop
        self.drop_target_register(DND_FILES)
        self.dnd_bind("<<Drop>>", self.drop_files_on_button)

        self.bind("<Button-1>", self.click_press)
        self.bind("<B1-Motion>", self.do_drag)
        self.bind("<ButtonRelease-1>", self.click_release)
        self.bind("<Double-Button-1>", self.dclick_event)
        self.bind("<Button-3>", self.show_context)

        # On Mac, Control+Click is the standard right-click equivalent
        if IS_MAC:
            self.bind("<Control-Button-1>", self.show_context)

        if IS_MAC:
            self.bind("<Command-Button-1>", self.ctrl_click)
        else:
            self.bind("<Control-Button-1>", self.ctrl_click)

        self.offset = (0, 0)
        self._dragging = False
        self._drag_moved = False
        self._doubleclicked = False
        self._waitingaction = False
        self.selected = False
        self.multi_drag_start = None

    def drop_files_on_button(self, event):
        files = []
        for item in self.app.root.tk.splitlist(event.data):
            item = item.strip('{}')
            item = os.path.normpath(item)
            files.append(item)

        existing_paths = {resolve_file_path(f) for f in self.data["files"]}
        new_files = []
        duplicates = []

        for file_path in files:
            # Fix #2: add folders as-is — do NOT walk their contents
            if os.path.isfile(file_path) or os.path.isdir(file_path):
                if file_path in existing_paths:
                    duplicates.append(os.path.basename(file_path))
                else:
                    new_files.append(file_path)

        if duplicates:
            duplicate_list = "\n".join(duplicates)
            if not messagebox.askyesno(
                    "Duplicate Files",
                    f"These items already exist in '{self.data['name']}':\n\n{duplicate_list}\n\nAdd them anyway?"
            ):
                new_files = [f for f in new_files if os.path.basename(f) not in duplicates]

        if new_files:
            # Fix #1: store as tracked dicts with inode info
            self.data["files"].extend([get_file_info(f) for f in new_files])
            self.app.save_data()
            print(f"Added {len(new_files)} item(s) to '{self.data['name']}'")

            original_color = self.cget("fg_color")
            self.configure(fg_color="#2cc940")
            self.after(200, lambda: self.configure(fg_color=original_color))

    def set_selected(self, selected):
        self.selected = selected
        if selected:
            original_color = self.data.get("color", self.app.get_color("btn_bg"))
            darker_color = self.darken_color(original_color)
            self.configure(fg_color=darker_color)
        else:
            self.configure(fg_color=self.data.get("color", self.app.get_color("btn_bg")))

    def darken_color(self, color):
        if color.startswith("#"):
            hex_color = color[1:]
            rgb = tuple(int(hex_color[i:i + 2], 16) for i in (0, 2, 4))
            darkened = tuple(max(0, int(c * 0.7)) for c in rgb)
            return f"#{darkened[0]:02x}{darkened[1]:02x}{darkened[2]:02x}"
        return color

    def ctrl_click(self, event):
        self.set_selected(not self.selected)
        self.offset = (event.x, event.y)
        return "break"

    def click_press(self, event):
        self.offset = (event.x, event.y)
        self._drag_moved = False

        ctrl_pressed = event.state & 0x4
        cmd_pressed = event.state & 0x8

        if ctrl_pressed or cmd_pressed:
            return

        was_selected = self.selected

        if not was_selected:
            self.app.clear_selection()
            self.set_selected(True)

    def do_drag(self, event):
        # *** FREEZE FEATURE: Check if buttons are locked ***
        if self.app.buttons_locked:
            return  # Don't allow dragging when locked

        if not self._dragging:
            self._dragging = True
            self._drag_moved = False

            if not self.selected:
                self.app.clear_selection()
                self.set_selected(True)

            selected_buttons = [btn for btn in self.app.buttons if btn.selected]
            for btn in selected_buttons:
                btn.multi_drag_start = (btn.winfo_x(), btn.winfo_y())

        new_x = self.winfo_x() + event.x - self.offset[0]
        new_y = self.winfo_y() + event.y - self.offset[1]

        if abs(new_x - self.winfo_x()) > 2 or abs(new_y - self.winfo_y()) > 2:
            self._drag_moved = True

        new_x, new_y = self.app.snap_to_grid(new_x, new_y)

        new_x = max(0, min(new_x, self.master.winfo_width() - self.winfo_width()))
        new_y = max(0, min(new_y, self.master.winfo_height() - self.winfo_height()))

        if hasattr(self, 'multi_drag_start') and self.multi_drag_start is not None:
            delta_x = new_x - self.multi_drag_start[0]
            delta_y = new_y - self.multi_drag_start[1]

            selected_buttons = [btn for btn in self.app.buttons if btn.selected]
            for btn in selected_buttons:
                if hasattr(btn, 'multi_drag_start') and btn.multi_drag_start is not None:
                    btn_new_x = btn.multi_drag_start[0] + delta_x
                    btn_new_y = btn.multi_drag_start[1] + delta_y

                    btn_new_x, btn_new_y = self.app.snap_to_grid(btn_new_x, btn_new_y)

                    btn_new_x = max(0, min(btn_new_x, self.master.winfo_width() - btn.winfo_width()))
                    btn_new_y = max(0, min(btn_new_y, self.master.winfo_height() - btn.winfo_height()))

                    btn.place(x=btn_new_x, y=btn_new_y)

    def click_release(self, event):
        if self._dragging:
            was_drag = self._drag_moved
            self._dragging = False
            self._drag_moved = False

            if was_drag:
                selected_buttons = [btn for btn in self.app.buttons if btn.selected]

                for btn in selected_buttons:
                    btn.data["x"] = btn.winfo_x()
                    btn.data["y"] = btn.winfo_y()

                self.app.save_data()
                self.app.save_state_to_history()

                for btn in selected_buttons:
                    if hasattr(btn, 'multi_drag_start'):
                        del btn.multi_drag_start
            else:
                self.app.clear_selection()
                self.set_selected(True)

                for btn in self.app.buttons:
                    if hasattr(btn, 'multi_drag_start'):
                        del btn.multi_drag_start
        else:
            if not self._waitingaction:
                self._waitingaction = True
                self.after(250, self.mouse_action, event)

    def dclick_event(self, event):
        self._doubleclicked = True

    def mouse_action(self, event):
        if self._doubleclicked:
            self._doubleclicked = False
            self.open_all()
        else:
            self.open_editor()

        self._waitingaction = False

    def show_context(self, event):
        menu = tk.Menu(self, tearoff=0)

        selected_buttons = [btn for btn in self.app.buttons if btn.selected]

        if len(selected_buttons) > 1:
            menu.add_command(label=f"Batch Rename ({len(selected_buttons)})", command=self.app.batch_rename)
            menu.add_command(label=f"Batch Color ({len(selected_buttons)})", command=self.app.batch_change_color)
            menu.add_command(label=f"Delete Selected ({len(selected_buttons)})",
                             command=self.app.delete_selected_buttons)
            menu.add_separator()
            menu.add_command(label="Auto-arrange All", command=self.app.auto_arrange_buttons)
            menu.add_separator()
            menu.add_command(label="Clear Selection", command=self.app.clear_selection)
        else:
            menu.add_command(label="Rename", command=self.rename)
            menu.add_separator()

            color_menu = tk.Menu(menu, tearoff=0)
            colors = [
                ("Blue", "#1f6aa5"),
                ("Red", "#d42c2c"),
                ("Green", "#2cc940"),
                ("Orange", "#ff8c00"),
                ("Purple", "#8e44ad"),
                ("Pink", "#e91e63"),
                ("Teal", "#009688"),
                ("Gray", "#607d8b")
            ]

            for color_name, color_code in colors:
                color_menu.add_command(
                    label=color_name,
                    command=lambda c=color_code: self.change_color(c)
                )

            menu.add_cascade(label="Change Color", menu=color_menu)
            menu.add_separator()
            menu.add_command(label="List Files", command=self.show_files_list)
            menu.add_command(label="Edit Files", command=self.open_editor)
            menu.add_command(label="Open All", command=self.open_all)
            menu.add_separator()
            menu.add_command(label="🔗 Create Symlinks…", command=self.create_symlinks)
            menu.add_separator()
            menu.add_command(label="Delete", command=self.confirm_delete)

        try:
            menu.tk_popup(event.x_root, event.y_root)
        finally:
            menu.grab_release()

    def change_color(self, color):
        old_color = self.data.get("color", "#1f6aa5")
        self.data["color"] = color
        self.configure(fg_color=color)
        self.app.save_data()
        self.app.save_state_to_history()

    def show_files_list(self):
        if not self.data["files"]:
            messagebox.showinfo("Files List", f"'{self.data['name']}' has no files added yet.")
            return

        files_text = "\n".join([f"• {os.path.basename(resolve_file_path(f))}" for f in self.data["files"]])
        messagebox.showinfo("Files List", f"Files in '{self.data['name']}':\n\n{files_text}")

    def confirm_delete(self):
        file_count = len(self.data.get("files", []))
        message = f"Delete '{self.data['name']}'?\n\nThis shortcut contains {file_count} file(s)."

        if messagebox.askyesno("Delete Shortcut", message):
            self.app.remove_shortcut(self)
            self.app.save_state_to_history()

    def rename(self):
        dlg = CustomInputDialog(self.app.root, "Rename Shortcut", "New name:", self.data["name"])
        if dlg.user_input:
            old_name = self.data["name"]
            self.data["name"] = dlg.user_input
            self.configure(text=dlg.user_input)
            self.app.save_data()
            self.app.save_state_to_history()

    def open_all(self):
        if not self.data["files"]:
            messagebox.showinfo("No Files", f"'{self.data['name']}' has no files to open.")
            return

        opened_count = 0
        for f in self.data["files"]:
            try:
                open_file(resolve_file_path(f))
                opened_count += 1
            except Exception as e:
                print(f"Error opening {f}: {e}")

    def open_editor(self):
        EditorWindow(self.app.root, self, self.app)

    def create_symlinks(self):
        """Create real filesystem symlinks for every item in this shortcut."""
        if not self.data["files"]:
            messagebox.showinfo("No Files", f"'{self.data['name']}' has no files to symlink.")
            return

        # Warn Windows users about the privilege requirement
        if IS_WINDOWS:
            if not messagebox.askyesno(
                "Windows Symlink Notice",
                "Creating symlinks on Windows requires either:\n\n"
                "• Developer Mode enabled  (Settings → System → Developer Mode)\n"
                "• Running this app as Administrator\n\n"
                "Continue?"
            ):
                return

        # Ask where to put the symlinks
        dest_dir = filedialog.askdirectory(title="Choose folder to place symlinks in")
        if not dest_dir:
            return

        created, skipped, failed = [], [], []

        for f in self.data["files"]:
            src = resolve_file_path(f)
            name = os.path.basename(src)
            link_path = os.path.join(dest_dir, name)

            # If a symlink/file with this name already exists, ask to overwrite
            if os.path.exists(link_path) or os.path.islink(link_path):
                if messagebox.askyesno(
                    "Already Exists",
                    f"'{name}' already exists in the destination.\nOverwrite the symlink?"
                ):
                    try:
                        os.remove(link_path)
                    except Exception as e:
                        failed.append(f"{name}  (could not remove existing: {e})")
                        continue
                else:
                    skipped.append(name)
                    continue

            try:
                os.symlink(src, link_path)
                created.append(name)
            except OSError as e:
                # Friendly message for the most common Windows error
                err_str = str(e)
                if "1314" in err_str or "privilege" in err_str.lower():
                    failed.append(f"{name}  (insufficient privilege — enable Developer Mode or run as Admin)")
                else:
                    failed.append(f"{name}  ({e})")

        # Build summary
        parts = []
        if created:
            parts.append(f"✅ Created ({len(created)}):\n" + "\n".join(f"  • {n}" for n in created))
        if skipped:
            parts.append(f"⏭ Skipped ({len(skipped)}):\n" + "\n".join(f"  • {n}" for n in skipped))
        if failed:
            parts.append(f"❌ Failed ({len(failed)}):\n" + "\n".join(f"  • {n}" for n in failed))

        title = "Symlinks Created" if not failed else "Symlinks — Some Errors"
        messagebox.showinfo(title, "\n\n".join(parts) if parts else "Nothing to do.")

        # Flash green if at least one was created
        if created:
            original_color = self.cget("fg_color")
            self.configure(fg_color="#2cc940")
            self.after(400, lambda: self.configure(fg_color=original_color))

    def to_dict(self):
        return {
            "name": self.data["name"],
            "files": self.data["files"],
            "x": self.winfo_x(),
            "y": self.winfo_y(),
            "color": self.data.get("color", "#1f6aa5")
        }


# ============================================================================
# SELECTION MANAGER
# ============================================================================

class SelectionManager:
    def __init__(self, app, canvas):
        self.app = app
        self.canvas = canvas
        self.start_x = None
        self.start_y = None
        self.rect = None
        self.is_dragging = False

        self.canvas.bind("<ButtonPress-1>", self.on_start)
        self.canvas.bind("<B1-Motion>", self.on_drag)
        self.canvas.bind("<ButtonRelease-1>", self.on_release)
        self.canvas.bind("<Escape>", self.cancel_selection)

    def cancel_selection(self, event=None):
        """Cancel the current selection rectangle"""
        if self.rect:
            self.canvas.delete("selection_rect")
            self.rect = None

        self.is_dragging = False
        self.start_x = None
        self.start_y = None

        for btn in self.app.buttons:
            if not btn.selected:
                btn.configure(border_width=0)

    def on_start(self, event):
        # Crucial check: only start if the click is not on a button itself.
        # This is handled by the buttons' own bindings, but an extra check
        # on the canvas is safer if the button binding fails or returns "break".
        clicked_widget = self.canvas.winfo_containing(event.x_root, event.y_root)

        # Check if the click is on the canvas_overlay or the buttons_frame itself
        if clicked_widget not in [self.canvas, self.app.buttons_frame]:
            return

        self.start_x = event.x
        self.start_y = event.y
        self.is_dragging = True

    def on_drag(self, event):
        if self.is_dragging:
            if not self.rect:
                self.rect = self.canvas.create_rectangle(
                    self.start_x, self.start_y, self.start_x, self.start_y,
                    outline="#1f6aa5", dash=(3, 3), width=2, fill="", tags="selection_rect"
                )

            self.canvas.coords(self.rect, self.start_x, self.start_y, event.x, event.y)
            self.update_selection_preview(event.x, event.y)

    def update_selection_preview(self, current_x, current_y):
        if not self.start_x or not self.start_y:
            return

        if not self.rect:
            return

        x1, x2 = sorted([self.start_x, current_x])
        y1, y2 = sorted([self.start_y, current_y])

        if abs(x2 - x1) > 10 and abs(y2 - y1) > 10:
            for btn in self.app.buttons:
                btn_x = btn.winfo_x()
                btn_y = btn.winfo_y()
                btn_width = btn.winfo_width()
                btn_height = btn.winfo_height()

                btn_x2 = btn_x + btn_width
                btn_y2 = btn_y + btn_height

                overlaps = (x1 < btn_x2 and x2 > btn_x and y1 < btn_y2 and y2 > btn_y)

                if overlaps and not btn.selected:
                    btn.configure(border_width=2, border_color="#1f6aa5")
                elif not overlaps and not btn.selected:
                    btn.configure(border_width=0)

    def on_release(self, event):
        if not self.is_dragging:
            return

        ctrl_pressed = event.state & 0x4
        cmd_pressed = event.state & 0x8

        # --- FIX 1: CLEAR SELECTION ON EMPTY SPACE CLICK ---
        if not self.rect:
            # It was a click (no drag rect created)
            if not (ctrl_pressed or cmd_pressed):
                self.app.clear_selection()
        # --- END FIX 1 ---
        else:
            # It was a drag - perform selection
            coords = self.canvas.coords(self.rect)

            if len(coords) == 4:
                x1, y1, x2, y2 = coords
                x1, x2 = sorted([x1, x2])
                y1, y2 = sorted([y1, y2])

                if abs(x2 - x1) > 10 and abs(y2 - y1) > 10:
                    for btn in self.app.buttons:
                        btn_x = btn.winfo_x()
                        btn_y = btn.winfo_y()
                        btn_width = btn.winfo_width()
                        btn_height = btn.winfo_height()

                        btn_x2 = btn_x + btn_width
                        btn_y2 = btn_y + btn_height

                        if (x1 < btn_x2 and x2 > btn_x and y1 < btn_y2 and y2 > btn_y):
                            btn.set_selected(True)

        # Cleanup
        if self.rect:
            self.canvas.delete("selection_rect")

        self.rect = None
        self.is_dragging = False
        self.start_x = None
        self.start_y = None

        for btn in self.app.buttons:
            if not btn.selected:
                btn.configure(border_width=0)

## ============================================================================
# MAIN APPLICATION
# ============================================================================

class ShortcutsApp:
    def __init__(self):
        # Initialize the app with TkinterDnD capabilities
        self.root = TkinterDnD.Tk()
        self.root.title("Shortcuts")

        # Set window icon — works in taskbar, dock, and title bar
        self._set_icon()

        # Load data, set defaults, and initialize state
        self.load_data()
        self.grid_size = 5
        self.history = []
        self.history_index = -1
        self.max_history = 50
        self.last_mouse_x = 100
        self.last_mouse_y = 100

        default_config = {
            "bg": "#f5f5f5",
            "btn_bg": "#1f6aa5",
            "btn_fg": "#ffffff",
            "font": "Arial",
            "font_size": 11,          # smaller default for a more minimal look
            "window_geometry": "800x600+100+100",
            "buttons_locked": False
        }
        self.config = default_config.copy()
        saved_config = self.data.get("config", {})
        self.config.update(saved_config)
        
        # *** FREEZE FEATURE: Initialize locked state ***
        self.buttons_locked = self.config.get("buttons_locked", False)

        # Toolbar — minimal, compact
        toolbar = ctk.CTkFrame(self.root, height=30, corner_radius=0)
        toolbar.pack(side="top", fill="x")

        _tb_btn = dict(height=24, corner_radius=4, border_width=0)

        # Left side buttons
        ctk.CTkButton(toolbar, text="＋ New", command=self.new_button_at_fixed_pos,
                      width=64, **_tb_btn).pack(side="left", padx=(6, 2), pady=3)

        self.lock_button = ctk.CTkButton(
            toolbar,
            text="🔓" if not self.buttons_locked else "🔒",
            command=self.toggle_lock,
            width=32,
            fg_color="#2cc940" if not self.buttons_locked else "#d42c2c",
            **_tb_btn
        )
        self.lock_button.pack(side="left", padx=2, pady=3)

        ctk.CTkButton(toolbar, text="🔤", command=self.show_font_settings,
                      width=32, **_tb_btn).pack(side="left", padx=2, pady=3)

        # Right side buttons
        ctk.CTkButton(toolbar, text="↩", command=self.undo,
                      width=30, **_tb_btn).pack(side="right", padx=(2, 6), pady=3)
        ctk.CTkButton(toolbar, text="↪", command=self.redo,
                      width=30, **_tb_btn).pack(side="right", padx=2, pady=3)
        ctk.CTkButton(toolbar, text="⌨", command=self.show_hotkeys,
                      width=30, **_tb_btn).pack(side="right", padx=2, pady=3)

        # Buttons frame — tighter padding for minimal feel
        self.buttons_frame = ctk.CTkFrame(self.root, corner_radius=0)
        self.buttons_frame.pack(fill="both", expand=True, padx=0, pady=0)

        self.buttons_frame.drop_target_register(DND_FILES)
        self.buttons_frame.dnd_bind("<<Drop>>", self.drop_files_on_window)

        # Load buttons
        self.buttons = []

        # Window geometry setup
        geometry = self.config.get("window_geometry", "800x600+100+100")
        width, height, x, y = self.parse_geometry(geometry)
        screen_width = self.root.winfo_screenwidth()
        screen_height = self.root.winfo_screenheight()
        x = max(0, min(x, screen_width - width))
        y = max(0, min(y, screen_height - height))
        self.root.geometry(f"{width}x{height}+{x}+{y}")

        self.root.after(100, self.load_buttons_from_data)

        self.update_styles()

        # Keyboard shortcuts - cross-platform
        self.root.bind("<Delete>", self.delete_selected_buttons)

        if IS_MAC:
            self.root.bind("<Command-BackSpace>", self.delete_selected_buttons)
            self.root.bind("<Command-z>", lambda e: self.undo())
            self.root.bind("<Command-Shift-z>", lambda e: self.redo())
            self.root.bind("<Command-l>", lambda e: self.toggle_lock())  # *** NEW: Cmd+L to toggle lock ***
        else:
            self.root.bind("<Control-BackSpace>", self.delete_selected_buttons)
            self.root.bind("<Control-z>", lambda e: self.undo())
            self.root.bind("<Control-y>", lambda e: self.redo())
            self.root.bind("<Control-Shift-z>", lambda e: self.redo())
            self.root.bind("<Control-l>", lambda e: self.toggle_lock())  # *** NEW: Ctrl+L to toggle lock ***

        self.root.bind("<BackSpace>", self.delete_selected_buttons)
        self.root.bind("<space>", self.create_button_at_cursor)
        self.root.bind("<F2>", self.rename_selected_buttons)
        self.root.bind("<Escape>", self.clear_selection_key)

        self.root.bind("<Motion>", self.track_mouse_position)
        self.buttons_frame.bind("<Motion>", self.track_mouse_position)

        self.root.focus_set()

        self.root.protocol("WM_DELETE_WINDOW", self.on_close)

        self.root.after(100, self.setup_overlay)

    def _set_icon(self):
        """Set the window/taskbar/dock icon from shortcut.ico, cross-platform."""
        # Look for the icon next to the script, or next to the frozen .exe
        if getattr(sys, 'frozen', False):
            icon_dir = os.path.dirname(sys.executable)
        else:
            icon_dir = BASE_DIR

        ico_path = os.path.join(icon_dir, "shortcut.ico")

        if not os.path.exists(ico_path):
            return  # Icon file not found — skip silently

        try:
            if IS_WINDOWS:
                # Windows: iconbitmap works natively with .ico
                self.root.iconbitmap(ico_path)
            else:
                # Mac / Linux: tkinter can't read .ico directly —
                # use Pillow to convert to a format tkinter understands
                from PIL import Image, ImageTk
                img = Image.open(ico_path)
                # Use the largest size available in the .ico for best quality
                sizes = getattr(img, 'n_frames', 1)
                best = img
                if hasattr(img, 'seek'):
                    best_size = 0
                    for i in range(sizes):
                        try:
                            img.seek(i)
                            w, h = img.size
                            if w * h > best_size:
                                best_size = w * h
                                best = img.copy()
                        except EOFError:
                            break
                photo = ImageTk.PhotoImage(best)
                self.root.iconphoto(True, photo)
                # Keep a reference so it isn't garbage collected
                self._icon_photo = photo
        except ImportError:
            # Pillow not installed — try a direct tk call as last resort
            try:
                self.root.iconbitmap(ico_path)
            except Exception:
                pass
        except Exception as e:
            print(f"Could not set icon: {e}")

    # *** FREEZE FEATURE: Toggle lock method ***
    def toggle_lock(self):
        """Toggle the lock state of buttons"""
        self.buttons_locked = not self.buttons_locked
        self.config["buttons_locked"] = self.buttons_locked
        
        if self.buttons_locked:
            self.lock_button.configure(text="🔒", fg_color="#d42c2c")
            self.root.configure(cursor="arrow")
        else:
            self.lock_button.configure(text="🔓", fg_color="#2cc940")
            self.root.configure(cursor="arrow")
        
        # Save the state
        self.save_data()
        
        # Show feedback
        status = "locked" if self.buttons_locked else "unlocked"
        print(f"Buttons {status}")

    def snap_to_grid(self, x, y):
        snapped_x = round(x / self.grid_size) * self.grid_size
        snapped_y = round(y / self.grid_size) * self.grid_size
        return snapped_x, snapped_y

    def save_state_to_history(self):
        current_state = {
            "buttons": [btn.to_dict() for btn in self.buttons],
            "config": self.config.copy()
        }

        if self.history_index < len(self.history) - 1:
            self.history = self.history[:self.history_index + 1]

        self.history.append(copy.deepcopy(current_state))
        self.history_index += 1

        if len(self.history) > self.max_history:
            self.history.pop(0)
            self.history_index -= 1

    def undo(self):
        if self.history_index > 0:
            self.history_index -= 1
            self.restore_state_from_history()
            print("Undo performed")

    def redo(self):
        if self.history_index < len(self.history) - 1:
            self.history_index += 1
            self.restore_state_from_history()
            print("Redo performed")

    def restore_state_from_history(self):
        if 0 <= self.history_index < len(self.history):
            state = self.history[self.history_index]

            for btn in self.buttons[:]:
                btn.destroy()
            self.buttons.clear()

            for btn_data in state["buttons"]:
                self.create_shortcut_button(btn_data)

            self.config = state["config"].copy()
            self.update_styles()

    def track_mouse_position(self, event):
        if event.widget == self.buttons_frame:
            self.last_mouse_x = event.x
            self.last_mouse_y = event.y
        else:
            self.last_mouse_x = event.x - self.buttons_frame.winfo_x()
            self.last_mouse_y = event.y - self.buttons_frame.winfo_y()

    def create_button_at_cursor(self, event=None):
        # Create at mouse cursor position (used by spacebar)
        x, y = self.snap_to_grid(self.last_mouse_x, self.last_mouse_y)
        self._create_button_with_defaults(x, y)

    def new_button_at_fixed_pos(self):
        # Used by the "New" button in the toolbar
        self._create_button_with_defaults(10, 10)

    def _create_button_with_defaults(self, x, y):
        btn_width, btn_height = self.get_button_size()
        x = max(8, min(x, self.buttons_frame.winfo_width() - btn_width - 8))
        y = max(8, min(y, self.buttons_frame.winfo_height() - btn_height - 8))

        data = {
            "name": f"Shortcut {len(self.buttons) + 1}",
            "files": [],
            "x": x,
            "y": y,
            "color": self.get_color("btn_bg")
        }

        new_btn = self.create_shortcut_button(data)
        self.save_state_to_history()
        self.save_data()

        # Select and open editor for newly created button
        self.clear_selection()
        new_btn.set_selected(True)
        new_btn.open_editor()


    def rename_selected_buttons(self, event=None):
        selected_buttons = [btn for btn in self.buttons if btn.selected]

        if not selected_buttons:
            return

        if len(selected_buttons) == 1:
            selected_buttons[0].rename()
        else:
            self.batch_rename()

    def clear_selection_key(self, event=None):
        self.clear_selection()

    def auto_arrange_buttons(self):
        if not self.buttons:
            return

        try:
            window_width = self.buttons_frame.winfo_width()
            cols = max(1, window_width // 120)
        except:
            cols = 5

        for i, btn in enumerate(self.buttons):
            row = i // cols
            col = i % cols

            x = col * 120
            y = row * 50
            x, y = self.snap_to_grid(x, y)

            btn.place(x=x, y=y)
            btn.data["x"] = x
            btn.data["y"] = y

        self.save_state_to_history()
        self.save_data()

    def batch_change_color(self):
        selected_buttons = [btn for btn in self.buttons if btn.selected]

        if not selected_buttons:
            messagebox.showinfo("Batch Color", "Please select buttons first.")
            return

        color_window = ctk.CTkToplevel(self.root)
        color_window.title("Choose Color")
        color_window.geometry("300x200")
        color_window.transient(self.root)
        color_window.grab_set()
        color_window.bind("<Escape>", lambda e: color_window.destroy())  # Fix #3: ESC cancels

        colors = [
            ("Blue", "#1f6aa5"),
            ("Red", "#d42c2c"),
            ("Green", "#2cc940"),
            ("Orange", "#ff8c00"),
            ("Purple", "#8e44ad"),
            ("Pink", "#e91e63"),
            ("Teal", "#009688"),
            ("Gray", "#607d8b")
        ]

        def apply_color(color):
            for btn in selected_buttons:
                btn.change_color(color)
            self.save_state_to_history()
            color_window.destroy()

        ctk.CTkLabel(color_window, text=f"Change color for {len(selected_buttons)} buttons:").pack(pady=10)

        for color_name, color_code in colors:
            ctk.CTkButton(
                color_window,
                text=color_name,
                fg_color=color_code,
                command=lambda c=color_code: apply_color(c)
            ).pack(pady=2, padx=20, fill="x")

    def batch_rename(self):
        selected_buttons = [btn for btn in self.buttons if btn.selected]

        if not selected_buttons:
            messagebox.showinfo("Batch Rename", "Please select buttons first.")
            return

        dlg = CustomInputDialog(
            self.root,
            "Batch Rename",
            f"Base name for {len(selected_buttons)} buttons:",
            "Shortcut"
        )

        if dlg.user_input:
            base_name = dlg.user_input
            for i, btn in enumerate(selected_buttons, 1):
                if len(selected_buttons) == 1:
                    btn.data["name"] = base_name
                else:
                    btn.data["name"] = f"{base_name} {i}"
                btn.configure(text=btn.data["name"])

            self.save_state_to_history()
            self.save_data()

    def show_loading_dialog(self, title="Processing...", message="Please wait..."):
        loading_dialog = ctk.CTkToplevel(self.root)
        loading_dialog.title(title)
        loading_dialog.geometry("300x150")
        loading_dialog.transient(self.root)
        loading_dialog.grab_set()

        self.root.update_idletasks()
        main_x = self.root.winfo_x()
        main_y = self.root.winfo_y()
        main_width = self.root.winfo_width()
        main_height = self.root.winfo_height()

        dialog_width = 300
        dialog_height = 150
        x = main_x + (main_width - dialog_width) // 2
        y = main_y + (main_height - dialog_height) // 2
        loading_dialog.geometry(f"{dialog_width}x{dialog_height}+{x}+{y}")

        ctk.CTkLabel(loading_dialog, text=message, font=("Arial", 14)).pack(pady=20)

        progress = ctk.CTkProgressBar(loading_dialog, mode="indeterminate")
        progress.pack(pady=20, padx=40, fill="x")
        progress.start()

        loading_dialog.update()
        return loading_dialog

    def process_dropped_files(self, files):
        all_files = []
        folder_count = 0
        file_count = 0

        for file_path in files:
            file_path = os.path.normpath(file_path)

            if os.path.isfile(file_path):
                all_files.append(file_path)
                file_count += 1
            elif os.path.isdir(file_path):
                # Fix #2: add the folder itself, not its contents
                all_files.append(file_path)
                folder_count += 1

        return {
            'all_files': all_files,
            'folder_count': folder_count,
            'file_count': file_count,
            'has_folders': folder_count > 0
        }

    def suggest_shortcut_name(self, files, file_info):
        if not files:
            return "New Shortcut"

        if len(files) == 1:
            file_path = files[0]
            name = os.path.basename(file_path)
            if os.path.isfile(file_path):
                name = os.path.splitext(name)[0]
            return name

        folder_count = file_info['folder_count']
        file_count = file_info['file_count']

        if folder_count > 0 and file_count == 0:
            return f"{folder_count} Folders"
        elif folder_count == 0 and file_count > 0:
            return f"{file_count} Files"
        else:
            return f"{folder_count} Folders + {file_count} Files"

    def drop_files_on_window(self, event):
        try:
            files = []
            for item in self.root.tk.splitlist(event.data):
                item = item.strip('{}')
                item = os.path.normpath(item)
                files.append(item)

            if not files:
                return

            loading_dialog = self.show_loading_dialog(
                "Processing Files...",
                f"Analyzing {len(files)} item(s)..."
            )

            def process_in_background():
                try:
                    file_info = self.process_dropped_files(files)

                    self.root.after(0, lambda: self.create_shortcut_after_processing(
                        files, file_info, loading_dialog
                    ))

                except Exception as e:
                    print(f"Error processing files: {e}")
                    self.root.after(0, lambda: self.handle_processing_error(loading_dialog, str(e)))

            thread = threading.Thread(target=process_in_background, daemon=True)
            thread.start()

        except Exception as e:
            print(f"Error in drop_files_on_window: {e}")

    def create_shortcut_after_processing(self, original_files, file_info, loading_dialog):
        try:
            loading_dialog.destroy()

            suggested_name = self.suggest_shortcut_name(original_files, file_info)

            folder_count = file_info['folder_count']
            total_files = file_info['file_count']

            info_parts = []
            if folder_count > 0:
                info_parts.append(f"{folder_count} folder(s)")
            if total_files > 0:
                info_parts.append(f"{total_files} file(s)")

            info_message = "Found: " + ", ".join(info_parts)

            shortcut_name = simpledialog.askstring(
                "Create Shortcut",
                f"{info_message}\n\nName for this shortcut:",
                initialvalue=suggested_name
            )

            if not shortcut_name:
                return

            try:
                window_width = self.buttons_frame.winfo_width()
                cols = max(1, window_width // 120)
            except:
                cols = 5

            existing_count = len(self.buttons)
            row = existing_count // cols
            col = existing_count % cols

            x = col * 120
            y = row * 50
            x, y = self.snap_to_grid(x, y)

            button_color = "#2cc940" if file_info['has_folders'] else self.get_color("btn_bg")

            data = {
                "name": shortcut_name,
                # Fix #1: store as tracked dicts with inode info
                "files": [get_file_info(f) for f in file_info['all_files']],
                "x": x,
                "y": y,
                "color": button_color
            }

            self.create_shortcut_button(data)
            self.save_state_to_history()
            self.save_data()

        except Exception as e:
            print(f"Error in create_shortcut_after_processing: {e}")
            messagebox.showerror("Error", f"Failed to create shortcut: {str(e)}")

    def handle_processing_error(self, loading_dialog, error_message):
        try:
            loading_dialog.destroy()
        except:
            pass

        messagebox.showerror("Processing Error", f"Failed to process files:\n{error_message}")

    def setup_overlay(self):
        self.canvas_overlay = tk.Canvas(
            self.buttons_frame,
            highlightthickness=0,
            bg=self.config.get("bg", "#ffffff")
        )
        self.canvas_overlay.place(relx=0, rely=0, relwidth=1, relheight=1)

        self.canvas_overlay.bind("<Button-3>", self.show_empty_space_context)
        # Note: We bind to canvas_overlay for left-click/drag selection,
        # but the main logic for selection/clear is in SelectionManager.

        # Initialize SelectionManager
        self.selection_manager = SelectionManager(self, self.canvas_overlay)

        self.root.update_idletasks()
        self.root.update()

        # Ensure buttons are on top of the canvas
        for btn in self.buttons:
            btn.tkraise()

    def show_empty_space_context(self, event):
        clicked_widget = event.widget.winfo_containing(event.x_root, event.y_root)

        # Do not show empty space menu if a button was clicked
        for btn in self.buttons:
            if clicked_widget == btn:
                return

        menu = tk.Menu(self.root, tearoff=0)
        # Use the right-click position for the new button
        menu.add_command(label="New Shortcut", command=lambda: self.create_button_at_position(event))
        menu.add_separator()

        selected_count = sum(1 for btn in self.buttons if btn.selected)
        if selected_count > 0:
            menu.add_command(label=f"Delete Selected ({selected_count})", command=self.delete_selected_buttons)
            menu.add_command(label="Auto-arrange", command=self.auto_arrange_buttons)
            menu.add_command(label="Batch Color", command=self.batch_change_color)
            menu.add_command(label="Batch Rename", command=self.batch_rename)
            menu.add_separator()

        menu.add_command(label="Clear Selection", command=self.clear_selection)
        menu.add_separator()
        
        # *** FREEZE FEATURE: Add to context menu ***
        lock_text = "🔓 Unlock Buttons" if self.buttons_locked else "🔒 Lock Buttons"
        menu.add_command(label=lock_text, command=self.toggle_lock)
        menu.add_separator()
        
        menu.add_command(label="Settings", command=self.show_settings)

        try:
            menu.tk_popup(event.x_root, event.y_root)
        finally:
            menu.grab_release()

    def create_button_at_position(self, event):
        # Create at right-click position
        x = event.x if hasattr(event, 'x') else self.last_mouse_x
        y = event.y if hasattr(event, 'y') else self.last_mouse_y
        self._create_button_with_defaults(x, y)

    def show_font_settings(self):
        """Fix #5: Dialog to configure global font family and size."""
        win = ctk.CTkToplevel(self.root)
        win.title("Font Settings")
        win.geometry("360x280")
        win.resizable(False, False)
        win.transient(self.root)
        win.grab_set()
        win.bind("<Escape>", lambda e: win.destroy())

        self.root.update_idletasks()
        px = self.root.winfo_x() + (self.root.winfo_width() - 360) // 2
        py = self.root.winfo_y() + (self.root.winfo_height() - 280) // 2
        win.geometry(f"+{px}+{py}")

        ctk.CTkLabel(win, text="Font Settings", font=("Arial", 14, "bold")).pack(pady=(12, 4))

        # Font family
        ctk.CTkLabel(win, text="Font Family:", anchor="w").pack(pady=(4, 0), padx=20, fill="x")
        font_var = tk.StringVar(value=self.config.get("font", "Arial"))
        common_fonts = ["Arial", "Helvetica", "Verdana", "Tahoma", "Calibri",
                        "Times New Roman", "Georgia", "Courier New", "Trebuchet MS"]
        font_menu = ctk.CTkOptionMenu(win, values=common_fonts, variable=font_var)
        font_menu.pack(pady=(2, 6), padx=20, fill="x")

        # Font size
        ctk.CTkLabel(win, text="Font Size  (buttons scale with size):", anchor="w").pack(padx=20, fill="x")
        size_var = tk.IntVar(value=self.config.get("font_size", 11))
        size_row = ctk.CTkFrame(win, fg_color="transparent")
        size_row.pack(padx=20, fill="x")

        size_display = ctk.CTkLabel(size_row, text=str(size_var.get()), width=28)

        def on_change(v=None):
            fs = int(float(size_var.get()))
            size_display.configure(text=str(fs))
            w = max(60, fs * 8)
            h = max(20, fs + 10)
            preview.configure(font=(font_var.get(), fs), width=w, height=h,
                              text=f"Preview  ({w}×{h}px)")

        size_slider = ctk.CTkSlider(size_row, from_=8, to=24, number_of_steps=16,
                                    variable=size_var, command=on_change)
        size_slider.pack(side="left", fill="x", expand=True)
        size_display.pack(side="right")
        font_menu.configure(command=lambda v: on_change())

        # Live preview button
        fs0 = self.config.get("font_size", 11)
        w0 = max(60, fs0 * 8)
        h0 = max(20, fs0 + 10)
        preview = ctk.CTkButton(win,
                                text=f"Preview  ({w0}×{h0}px)",
                                font=(font_var.get(), fs0),
                                width=w0, height=h0,
                                fg_color=self.get_color("btn_bg"),
                                text_color=self.get_color("btn_fg"),
                                state="disabled")
        preview.pack(pady=10)

        def apply():
            self.config["font"] = font_var.get()
            self.config["font_size"] = int(size_var.get())
            self.update_styles()
            self.save_data()
            win.destroy()

        btn_row = ctk.CTkFrame(win, fg_color="transparent")
        btn_row.pack(pady=8, padx=20, fill="x")
        ctk.CTkButton(btn_row, text="Cancel", command=win.destroy, width=80).pack(side="left")
        ctk.CTkButton(btn_row, text="Apply", command=apply, width=80).pack(side="right")

    def show_hotkeys(self):
        """Fix #4: Show a reference window listing all keyboard shortcuts."""
        win = ctk.CTkToplevel(self.root)
        win.title("Keyboard Shortcuts")
        win.geometry("430x460")
        win.resizable(False, True)
        win.transient(self.root)
        win.grab_set()
        win.bind("<Escape>", lambda e: win.destroy())

        self.root.update_idletasks()
        px = self.root.winfo_x() + (self.root.winfo_width() - 430) // 2
        py = self.root.winfo_y() + (self.root.winfo_height() - 460) // 2
        win.geometry(f"+{px}+{py}")

        mod = "Cmd" if IS_MAC else "Ctrl"

        hotkeys = [
            ("General", [
                ("Space", "Create shortcut at mouse position"),
                ("F2", "Rename selected shortcut(s)"),
                ("Delete / Backspace", "Delete selected shortcut(s)"),
                (f"{mod}+Z", "Undo"),
                (f"{mod}+Y  /  {mod}+Shift+Z", "Redo"),
                (f"{mod}+L", "Toggle Lock / Unlock buttons"),
                ("Escape", "Clear selection  /  cancel dialog"),
            ]),
            ("Mouse", [
                (f"{mod}+Click", "Add to / remove from selection"),
                ("Drag shortcut button", "Move shortcut(s)"),
                ("Drag selection box", "Select multiple shortcuts"),
                ("Single-click shortcut", "Open editor for that shortcut"),
                ("Double-click shortcut", "Open all files in shortcut"),
                ("Right-click  /  Ctrl+Click (Mac)", "Context menu (rename, color, symlink, delete…)"),
                ("Right-click empty area", "New shortcut  /  batch actions"),
            ]),
            ("Drag & Drop", [
                ("Drop file/folder → shortcut", "Add item to that shortcut"),
                ("Drop file/folder → empty area", "Create new shortcut"),
            ]),
        ]

        ctk.CTkLabel(win, text="Keyboard & Mouse Shortcuts",
                     font=("Arial", 15, "bold")).pack(pady=(15, 5))

        scroll = ctk.CTkScrollableFrame(win)
        scroll.pack(fill="both", expand=True, padx=12, pady=5)

        for section, entries in hotkeys:
            ctk.CTkLabel(scroll, text=section,
                         font=("Arial", 12, "bold"),
                         anchor="w").pack(fill="x", pady=(10, 2))
            for key, desc in entries:
                row = ctk.CTkFrame(scroll, fg_color="transparent")
                row.pack(fill="x", pady=1)
                ctk.CTkLabel(row, text=key,
                             font=("Courier", 11),
                             width=190, anchor="w").pack(side="left")
                ctk.CTkLabel(row, text=desc,
                             anchor="w", wraplength=210).pack(side="left", fill="x", expand=True)

        ctk.CTkButton(win, text="Close", command=win.destroy).pack(pady=10)

    def show_settings(self):
        # Kept for backward compat with context menu — opens font settings
        self.show_font_settings()

    def parse_geometry(self, geometry_str):
        parts = geometry_str.replace("x", "+").split("+")
        width = int(parts[0])
        height = int(parts[1])
        x = int(parts[2])
        y = int(parts[3])
        return width, height, x, y

    def load_data(self):
        if os.path.exists(DATA_FILE):
            try:
                with open(DATA_FILE, "r", encoding='utf-8') as f:
                    self.data = json.load(f)
            except json.JSONDecodeError:
                print("Error decoding JSON. Creating new data file.")
                self.data = {"buttons": [], "config": {}}
        else:
            self.data = {"buttons": [], "config": {}}

    def load_buttons_from_data(self):
        for d in self.data.get("buttons", []):
            self.create_shortcut_button(d)
        self.save_state_to_history()

    def clear_selection(self):
        for btn in self.buttons:
            btn.set_selected(False)

    def delete_selected_buttons(self, event=None):
        selected_buttons = [btn for btn in self.buttons if btn.selected]

        if not selected_buttons:
            return

        if len(selected_buttons) == 1:
            btn = selected_buttons[0]
            file_count = len(btn.data.get("files", []))
            message = f"Delete '{btn.data['name']}'?\n\nThis shortcut contains {file_count} file(s)."
        else:
            total_files = sum(len(btn.data.get("files", [])) for btn in selected_buttons)
            message = f"Delete {len(selected_buttons)} shortcuts?\n\nThese contain a total of {total_files} file(s)."

        if messagebox.askyesno("Delete Shortcuts", message):
            for btn in selected_buttons:
                self.remove_shortcut(btn)
            self.save_state_to_history()

    def save_data(self):
        self.data["buttons"] = [btn.to_dict() for btn in self.buttons]
        self.data["config"] = self.config

        try:
            os.makedirs(os.path.dirname(DATA_FILE), exist_ok=True)
            with open(DATA_FILE, "w", encoding='utf-8') as f:
                json.dump(self.data, f, indent=4)
        except Exception as e:
            print(f"ERROR saving data: {e}")

    def create_shortcut_button(self, data):
        if "color" not in data:
            data["color"] = self.get_color("btn_bg")

        # Fix #1: normalize file list — convert legacy string paths to tracked dicts
        if "files" not in data:
            data["files"] = []
        data["files"] = normalize_file_list(data["files"])

        x = data.get("x", 10)
        y = data.get("y", 10)

        btn_width, btn_height = self.get_button_size()

        # Boundary checks for initial placement
        max_x = self.buttons_frame.winfo_width() - btn_width - 10 if self.buttons_frame.winfo_width() > 0 else 700
        max_y = self.buttons_frame.winfo_height() - btn_height - 10 if self.buttons_frame.winfo_height() > 0 else 500
        x = max(8, min(x, max_x))
        y = max(8, min(y, max_y))

        btn = ShortcutButton(
            self.buttons_frame, data, self,
            fg_color=data["color"],
            text_color=self.get_color("btn_fg"),
            font=(self.config.get("font", "Arial"), self.config.get("font_size", 11)),
            corner_radius=5,
            width=btn_width,
            height=btn_height
        )

        btn.place(x=x, y=y)
        self.buttons.append(btn)
        data["x"] = x
        data["y"] = y
        return btn

    def remove_shortcut(self, btn):
        btn.destroy()
        if btn in self.buttons:
            self.buttons.remove(btn)
        self.save_data()

    def get_button_size(self):
        """Derive button width/height from the current font size so they scale together."""
        fs = self.config.get("font_size", 11)
        width = max(60, fs * 8)    # e.g. fs=11→88, fs=14→112, fs=18→144
        height = max(20, fs + 10)  # e.g. fs=11→21, fs=14→24, fs=18→28
        return width, height

    def get_color(self, key):
        val = self.config.get(key)
        if isinstance(val, str) and val.startswith("#"):
            return val
        else:
            defaults = {
                "bg": "#ffffff",
                "btn_bg": "#1f6aa5",
                "btn_fg": "#ffffff"
            }
            return defaults.get(key, "#000000")

    def update_styles(self):
        bg = self.get_color("bg")
        self.root.configure(bg=bg)
        self.buttons_frame.configure(fg_color=bg)

        if hasattr(self, 'canvas_overlay'):
            self.canvas_overlay.configure(bg=bg)

        btn_width, btn_height = self.get_button_size()
        for btn in self.buttons:
            btn.configure(
                fg_color=btn.data.get("color", self.get_color("btn_bg")),
                text_color=self.get_color("btn_fg"),
                font=(self.config.get("font", "Arial"), self.config.get("font_size", 11)),
                width=btn_width,
                height=btn_height,
            )

    def on_close(self):
        width = self.root.winfo_width()
        height = self.root.winfo_height()
        x = self.root.winfo_x()
        y = self.root.winfo_y()
        self.config["window_geometry"] = f"{width}x{height}+{x}+{y}"
        self.data["config"] = self.config
        self.save_data()
        self.root.destroy()

    def run(self):
        self.root.mainloop()


# ============================================================================
# MAIN ENTRY POINT
# ============================================================================

if __name__ == "__main__":
    ctk.set_appearance_mode("System")
    ctk.set_default_color_theme("blue")
    app = ShortcutsApp()
    app.run()
