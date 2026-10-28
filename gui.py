"""IP-реестр — графическое приложение для учёта рабочих станций вуза."""

from __future__ import annotations

import ipaddress
import sys
import tkinter as tk
from datetime import datetime
from tkinter import filedialog, messagebox, ttk

import psycopg2
import psycopg2.errors
from PIL import Image, ImageTk

import db
import hashlib
from config import get_default_operator, load_env

APP_TITLE = "IP-реестр"
OPERATION_LABELS = {
    "INSERT": "Добавление",
    "UPDATE": "Изменение",
    "DELETE": "Удаление",
}


def fmt_dt(value) -> str:
    if value is None:
        return ""
    if isinstance(value, datetime):
        return value.strftime("%d.%m.%Y %H:%M")
    return str(value)


def explain_db_error(exc: BaseException) -> str:
    if isinstance(exc, psycopg2.errors.UniqueViolation):
        return "Такой IP-адрес уже есть в актуальном списке."
    if isinstance(exc, psycopg2.Error):
        return f"Ошибка базы данных: {exc}"
    return str(exc)


class App(tk.Tk):
    def __init__(self, current_user: dict) -> None:
        super().__init__()
        self.current_user = current_user 
        self.title(f"{APP_TITLE} — {self.current_user['username']} ({self.current_user['role']})")
        self.geometry("1360x740")
        self.minsize(1024, 580)

        try:
            self.iconbitmap("logo.ico")
        except Exception as e:
            print(f"Ошибка загрузки иконки: {e}")

        self.name_var = tk.StringVar()
        self.room_var = tk.StringVar()
        self.ip_adr = tk.StringVar()
        self.limit_var = tk.StringVar(value="500")

        self._ws_by_iid: dict[str, dict] = {}
        self._hist_by_iid: dict[str, dict] = {}
        self._selected_ws: dict | None = None
        self._selected_hist: dict | None = None
        self._editing = False

        self._tree_headings: dict[ttk.Treeview, dict[str, str]] = {}
        self._sort_state: dict[tuple[ttk.Treeview, str], bool] = {}

        self._build()
        self.refresh_all()
        self._apply_role_restrictions() 

    def _build(self) -> None:
        bg_color = "#e8f5e9"
        self.configure(bg=bg_color)

        style = ttk.Style()
        style.configure("TFrame", background=bg_color)
        style.configure("TLabel", background=bg_color)
        style.configure("TLabelframe", background=bg_color)
        style.configure("TLabelframe.Label", background=bg_color)

        outer = ttk.Frame(self, padding=10)
        outer.pack(fill=tk.BOTH, expand=True)

        header_frame = ttk.Frame(outer)
        header_frame.pack(fill=tk.X, pady=(0, 8))

        text_frame = ttk.Frame(header_frame)
        text_frame.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        title = ttk.Label(text_frame, text=APP_TITLE, font=("Segoe UI", 16, "bold"))
        title.pack(anchor=tk.W)

        hint = ttk.Label(
            text_frame,
            text="Актуальные адреса рабочих станций и журнал событий. Клик по заголовку столбца — сортировка.",
        )
        hint.pack(anchor=tk.W, pady=(0, 8))

        try:
            img = Image.open("logo_pgups.png")
            img.thumbnail((150, 80), Image.Resampling.LANCZOS)
            self.logo_image = ImageTk.PhotoImage(img)

            logo_label = ttk.Label(header_frame, image=self.logo_image)
            logo_label.pack(side=tk.RIGHT, anchor=tk.NE, padx=(0, 10))
        except Exception as e:
            print(f"Ошибка загрузки логотипа: {e}")

        toolbar = ttk.Frame(outer)
        toolbar.pack(fill=tk.X, pady=(0, 8))

        ttk.Label(toolbar, text="Фамилия / ФИО:").pack(side=tk.LEFT)
        name_entry = ttk.Entry(toolbar, textvariable=self.name_var, width=22)
        name_entry.pack(side=tk.LEFT, padx=(4, 10))
        name_entry.bind("<Return>", lambda _e: self.refresh_all())

        ttk.Label(toolbar, text="Аудитория:").pack(side=tk.LEFT)
        room_entry = ttk.Entry(toolbar, textvariable=self.room_var, width=10)
        room_entry.pack(side=tk.LEFT, padx=(4, 10))
        room_entry.bind("<Return>", lambda _e: self.refresh_all())

        ttk.Label(toolbar, text="IP-адрес:").pack(side=tk.LEFT)
        ip_entry = ttk.Entry(toolbar, textvariable=self.ip_adr, width=14)
        ip_entry.pack(side=tk.LEFT, padx=(4, 10))
        ip_entry.bind("<Return>", lambda _e: self.refresh_all())

        ttk.Label(toolbar, text="Лимит логов:").pack(side=tk.LEFT)
        limit_combo = ttk.Combobox(
            toolbar,
            textvariable=self.limit_var,
            values=("100", "500", "1000", "Все"),
            width=6,
            state="readonly",
        )
        limit_combo.pack(side=tk.LEFT, padx=(4, 10))
        limit_combo.bind("<<ComboboxSelected>>", lambda _e: self.refresh_all())

        ttk.Button(toolbar, text="Найти", command=self.refresh_all).pack(side=tk.LEFT, padx=2)
        ttk.Button(toolbar, text="Сбросить", command=self._reset_search).pack(side=tk.LEFT, padx=2)
        ttk.Button(toolbar, text="Сохранить .txt…", command=self.save_txt).pack(
            side=tk.LEFT, padx=(12, 2)
        )
        
        self.btn_add_ws = ttk.Button(toolbar, text="Добавить станцию", command=self.add_workstation)
        self.btn_add_ws.pack(side=tk.LEFT, padx=2)
        
        ttk.Button(toolbar, text="Обновить", command=self.refresh_all).pack(side=tk.LEFT, padx=2)

        self.notebook = ttk.Notebook(outer)
        self.notebook.pack(fill=tk.BOTH, expand=True)
        self.notebook.bind("<<NotebookTabChanged>>", lambda _e: self._sync_status())

        self.live_tab = ttk.Frame(self.notebook, padding=6)
        self.archive_tab = ttk.Frame(self.notebook, padding=6)
        self.notebook.add(self.live_tab, text="Актуальные")
        self.notebook.add(self.archive_tab, text="Журнал событий")

        self._build_live_tab()
        self._build_archive_tab()

        self.status = tk.StringVar(value="")
        ttk.Label(outer, textvariable=self.status).pack(anchor=tk.W, pady=(6, 0))


    def _sort_tree(self, tree: ttk.Treeview, col: str) -> None:
        """Сортирует таблицу Treeview по выбранному столбцу (как в Excel)."""
        reverse = self._sort_state.get((tree, col), False)
        items = [(tree.set(iid, col), iid) for iid in tree.get_children("")]

        def sort_key(item: tuple[str, str]):
            val = item[0].strip()
            if not val:
                return (1, "")  # Пустые ячейки всегда в конце
            if col == "ip":
                try:
                    return (0, int(ipaddress.ip_address(val)))
                except ValueError:
                    return (0, val.lower())
            if col in ("updated_at", "valid_to"):
                try:
                    return (0, datetime.strptime(val, "%d.%m.%Y %H:%M"))
                except ValueError:
                    return (0, val.lower())
            return (0, val.lower())

        items.sort(key=sort_key, reverse=reverse)

        for index, (_, iid) in enumerate(items):
            tree.move(iid, "", index)

        # Обновляем стрелочки в заголовках
        headings = self._tree_headings[tree]
        for c, text in headings.items():
            if c == col:
                arrow = " ▼" if reverse else " ▲"
                tree.heading(c, text=text + arrow)
            else:
                tree.heading(c, text=text)

        self._sort_state[(tree, col)] = not reverse


    def _reset_tree_Sort_headers(self, tree: ttk.Treeview) -> None:
        """Сбрасывает значки стрелочек при полном обновлении данных из БД."""
        if tree in self._tree_headings:
            for c, text in self._tree_headings[tree].items():
                tree.heading(c, text=text)


    def _build_live_tab(self) -> None:
        pane = ttk.Panedwindow(self.live_tab, orient=tk.HORIZONTAL)
        pane.pack(fill=tk.BOTH, expand=True)

        left = ttk.Frame(pane)
        right = ttk.LabelFrame(pane, text="Карточка станции", padding=8)
        pane.add(left, weight=4)
        pane.add(right, weight=2)

        columns = ("ip", "full_name", "room", "ticket", "note", "updated_at", "updated_by")
        self.ws_tree = ttk.Treeview(left, columns=columns, show="headings", selectmode="browse")
        headings = {
            "ip": "IP-адрес",
            "full_name": "ФИО",
            "room": "Аудитория",
            "ticket": "Заявка",
            "note": "Примечание",
            "updated_at": "Обновлено",
            "updated_by": "Кем",
        }
        self._tree_headings[self.ws_tree] = headings.copy()
        widths = {
            "ip": 120,
            "full_name": 200,
            "room": 85,
            "ticket": 95,
            "note": 160,
            "updated_at": 120,
            "updated_by": 85,
        }
        for col in columns:
            self.ws_tree.heading(
                col, text=headings[col], command=lambda c=col: self._sort_tree(self.ws_tree, c)
            )
            self.ws_tree.column(col, width=widths[col], stretch=True)

        yscroll = ttk.Scrollbar(left, orient=tk.VERTICAL, command=self.ws_tree.yview)
        self.ws_tree.configure(yscrollcommand=yscroll.set)
        self.ws_tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        yscroll.pack(side=tk.RIGHT, fill=tk.Y)
        self.ws_tree.bind("<<TreeviewSelect>>", self._on_ws_select)
        self.ws_tree.bind("<Double-1>", lambda _e: self._start_edit())

        fields = ttk.Frame(right)
        fields.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        self.ws_fields = {
            "ip_address": tk.StringVar(),
            "full_name": tk.StringVar(),
            "room_number": tk.StringVar(),
            "ticket_id": tk.StringVar(),
            "note": tk.StringVar(),
            "created_at": tk.StringVar(),
            "updated_at": tk.StringVar(),
            "updated_by": tk.StringVar(),
        }
        labels = [
            ("ip_address", "IP-адрес"),
            ("full_name", "ФИО"),
            ("room_number", "Аудитория"),
            ("ticket_id", "Номер заявки"),
            ("note", "Примечание"),
            ("created_at", "Создано"),
            ("updated_at", "Обновлено"),
            ("updated_by", "Кем изменено"),
        ]
        self.ws_entries: dict[str, ttk.Entry] = {}
        for row, (key, caption) in enumerate(labels):
            ttk.Label(fields, text=caption).grid(row=row, column=0, sticky=tk.W, pady=3, padx=(0, 8))
            entry = ttk.Entry(fields, textvariable=self.ws_fields[key], width=36, state="readonly")
            entry.grid(row=row, column=1, sticky=tk.EW, pady=3)
            self.ws_entries[key] = entry
        fields.columnconfigure(1, weight=1)

        buttons = ttk.Frame(right)
        buttons.pack(side=tk.RIGHT, fill=tk.Y, padx=(12, 0))
        self.btn_edit = ttk.Button(buttons, text="Изменить", command=self._start_edit, width=16)
        self.btn_save = ttk.Button(buttons, text="Сохранить", command=self._save_edit, width=16)
        self.btn_cancel = ttk.Button(buttons, text="Отмена", command=self._cancel_edit, width=16)
        self.btn_delete = ttk.Button(buttons, text="Удалить запись", command=self._delete_ws, width=16)
        self.btn_edit.pack(pady=(0, 6))
        self.btn_delete.pack(pady=(0, 6))
        self._set_edit_mode(False)


    def _build_archive_tab(self) -> None:
        pane = ttk.Panedwindow(self.archive_tab, orient=tk.HORIZONTAL)
        pane.pack(fill=tk.BOTH, expand=True)

        left = ttk.Frame(pane)
        right = ttk.LabelFrame(pane, text="Детали события", padding=8)
        pane.add(left, weight=4)
        pane.add(right, weight=2)

        columns = (
            "operation",
            "change_details",
            "ip",
            "full_name",
            "room",
            "ticket",
            "note",
            "valid_to",
            "changed_by",
        )
        self.hist_tree = ttk.Treeview(left, columns=columns, show="headings", selectmode="browse")
        headings = {
            "operation": "Операция",
            "change_details": "Что изменилось",
            "ip": "IP-адрес",
            "full_name": "ФИО",
            "room": "Аудитория",
            "ticket": "Заявка",
            "note": "Примечание",
            "valid_to": "Когда",
            "changed_by": "Кем",
        }
        self._tree_headings[self.hist_tree] = headings.copy()
        widths = {
            "operation": 95,
            "change_details": 230,
            "ip": 115,
            "full_name": 180,
            "room": 80,
            "ticket": 90,
            "note": 130,
            "valid_to": 120,
            "changed_by": 80,
        }
        for col in columns:
            self.hist_tree.heading(
                col, text=headings[col], command=lambda c=col: self._sort_tree(self.hist_tree, c)
            )
            self.hist_tree.column(col, width=widths[col], stretch=True)

        yscroll = ttk.Scrollbar(left, orient=tk.VERTICAL, command=self.hist_tree.yview)
        self.hist_tree.configure(yscrollcommand=yscroll.set)
        self.hist_tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        yscroll.pack(side=tk.RIGHT, fill=tk.Y)
        self.hist_tree.bind("<<TreeviewSelect>>", self._on_hist_select)

        fields = ttk.Frame(right)
        fields.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        self.hist_fields = {
            "operation": tk.StringVar(),
            "change_details": tk.StringVar(),
            "ip_address": tk.StringVar(),
            "full_name": tk.StringVar(),
            "room_number": tk.StringVar(),
            "ticket_id": tk.StringVar(),
            "note": tk.StringVar(),
            "valid_from": tk.StringVar(),
            "valid_to": tk.StringVar(),
            "changed_by": tk.StringVar(),
            "id_original": tk.StringVar(),
        }
        labels = [
            ("operation", "Операция"),
            ("change_details", "Что изменилось"),
            ("ip_address", "IP-адрес"),
            ("full_name", "ФИО"),
            ("room_number", "Аудитория"),
            ("ticket_id", "Номер заявки"),
            ("note", "Примечание"),
            ("valid_from", "Действовало с"),
            ("valid_to", "Дата события"),
            ("changed_by", "Оператор"),
            ("id_original", "Исходный ID"),
        ]
        for row, (key, caption) in enumerate(labels):
            ttk.Label(fields, text=caption).grid(row=row, column=0, sticky=tk.W, pady=3, padx=(0, 8))
            ttk.Entry(fields, textvariable=self.hist_fields[key], width=36, state="readonly").grid(
                row=row, column=1, sticky=tk.EW, pady=3
            )
        fields.columnconfigure(1, weight=1)

        buttons = ttk.Frame(right)
        buttons.pack(side=tk.RIGHT, fill=tk.Y, padx=(12, 0))
        
        # Сохраняем кнопку восстановления в переменную self.btn_restore, чтобы скрыть ее от user
        self.btn_restore = ttk.Button(
            buttons, text="Вернуть в актуальные", command=self._restore_hist, width=22
        )
        self.btn_restore.pack(pady=(0, 6))


    def operator(self) -> str:
        return self.current_user["username"]


    def _reset_search(self) -> None:
        self.name_var.set("")
        self.room_var.set("")
        self.ip_adr.set("")
        self.refresh_all()


    def refresh_all(self) -> None:
        name = self.name_var.get()
        room = self.room_var.get()
        ip_adr = self.ip_adr.get()
        
        raw_limit = self.limit_var.get()
        limit_val = int(raw_limit) if raw_limit.isdigit() else None

        try:
            workstations = db.fetch_workstations(
                full_name=name, room_number=room, ip_address=ip_adr
            )
            history = db.fetch_history(
                full_name=name, room_number=room, ip_address=ip_adr, limit=limit_val
            )
        except Exception as exc:
            messagebox.showerror(APP_TITLE, explain_db_error(exc), parent=self)
            return
        self._fill_ws(workstations)
        self._fill_hist(history)
        self._sync_status()


    def _fill_ws(self, rows: list[dict]) -> None:
        self._cancel_edit(silent=True)
        self._reset_tree_Sort_headers(self.ws_tree)
        self.ws_tree.delete(*self.ws_tree.get_children())
        self._ws_by_iid.clear()
        self._selected_ws = None
        self._clear_ws_fields()
        for row in rows:
            iid = str(row["id"])
            self._ws_by_iid[iid] = row
            self.ws_tree.insert(
                "",
                tk.END,
                iid=iid,
                values=(
                    row["ip_address"],
                    row["full_name"],
                    row["room_number"],
                    row["ticket_id"],
                    row["note"],
                    fmt_dt(row["updated_at"]),
                    row["updated_by"],
                ),
            )


    def _fill_hist(self, rows: list[dict]) -> None:
        self._reset_tree_Sort_headers(self.hist_tree)
        self.hist_tree.delete(*self.hist_tree.get_children())
        self._hist_by_iid.clear()
        self._selected_hist = None
        self._clear_hist_fields()
        for row in rows:
            iid = str(row["history_id"])
            self._hist_by_iid[iid] = row
            
            # Если в старых записях БД нет деталей, ставим прочерк или базовое описание
            details = row.get("change_details") or ""
            if not details:
                if row["operation"] == "INSERT":
                    details = "Создание новой записи"
                elif row["operation"] == "DELETE":
                    details = "Удаление записи"

            self.hist_tree.insert(
                "",
                tk.END,
                iid=iid,
                values=(
                    OPERATION_LABELS.get(row["operation"], row["operation"]),
                    details,
                    row["ip_address"],
                    row["full_name"],
                    row["room_number"],
                    row["ticket_id"],
                    row["note"],
                    fmt_dt(row["valid_to"]),
                    row["changed_by"],
                ),
            )


    def _sync_status(self) -> None:
        live = len(self._ws_by_iid)
        hist = len(self._hist_by_iid)
        tab = self.notebook.index(self.notebook.select()) if str(self.notebook.select()) else 0
        if tab == 0:
            self.status.set(f"Актуальных записей: {live}. Событий в журнале: {hist}.")
        else:
            self.status.set(f"Событий в журнале: {hist}. Актуальных записей: {live}.")


    def _on_ws_select(self, _event=None) -> None:
        if self._editing:
            if not messagebox.askyesno(
                APP_TITLE,
                "Есть несохранённые изменения. Отменить редактирование и перейти к другой записи?",
                parent=self,
            ):
                return
            self._cancel_edit(silent=True)
        sel = self.ws_tree.selection()
        if not sel:
            return
        row = self._ws_by_iid.get(sel[0])
        if not row:
            return
        self._selected_ws = row
        self._show_ws(row)


    def _on_hist_select(self, _event=None) -> None:
        sel = self.hist_tree.selection()
        if not sel:
            return
        row = self._hist_by_iid.get(sel[0])
        if not row:
            return
        self._selected_hist = row
        
        details = row.get("change_details") or ""
        if not details:
            if row["operation"] == "INSERT":
                details = "Создание новой записи"
            elif row["operation"] == "DELETE":
                details = "Удаление записи"

        self.hist_fields["operation"].set(OPERATION_LABELS.get(row["operation"], row["operation"]))
        self.hist_fields["change_details"].set(details)
        self.hist_fields["ip_address"].set(row["ip_address"])
        self.hist_fields["full_name"].set(row["full_name"])
        self.hist_fields["room_number"].set(row["room_number"])
        self.hist_fields["ticket_id"].set(row["ticket_id"])
        self.hist_fields["note"].set(row["note"])
        self.hist_fields["valid_from"].set(fmt_dt(row["valid_from"]))
        self.hist_fields["valid_to"].set(fmt_dt(row["valid_to"]))
        self.hist_fields["changed_by"].set(row["changed_by"])
        self.hist_fields["id_original"].set(str(row["id_original"]))


    def _show_ws(self, row: dict) -> None:
        self.ws_fields["ip_address"].set(row["ip_address"])
        self.ws_fields["full_name"].set(row["full_name"])
        self.ws_fields["room_number"].set(row["room_number"])
        self.ws_fields["ticket_id"].set(row["ticket_id"])
        self.ws_fields["note"].set(row["note"])
        self.ws_fields["created_at"].set(fmt_dt(row["created_at"]))
        self.ws_fields["updated_at"].set(fmt_dt(row["updated_at"]))
        self.ws_fields["updated_by"].set(row["updated_by"])


    def _clear_ws_fields(self) -> None:
        for var in self.ws_fields.values():
            var.set("")


    def _clear_hist_fields(self) -> None:
        for var in self.hist_fields.values():
            var.set("")


    def _set_edit_mode(self, enabled: bool) -> None:
        self._editing = enabled
        for key in ("ip_address", "full_name", "room_number", "ticket_id", "note"):
            self.ws_entries[key].configure(state="normal" if enabled else "readonly")
            
        # БРОНЯ: Если это не админ, принудительно прячем все кнопки управления карточкой и выходим
        if self.current_user["role"] != "admin":
            self.btn_edit.pack_forget()
            self.btn_delete.pack_forget()
            self.btn_save.pack_forget()
            self.btn_cancel.pack_forget()
            return

        # Логика отрисовки только для админа
        if enabled:
            self.btn_edit.pack_forget()
            self.btn_delete.pack_forget()
            self.btn_save.pack(pady=(0, 6))
            self.btn_cancel.pack(pady=(0, 6))
        else:
            self.btn_save.pack_forget()
            self.btn_cancel.pack_forget()
            self.btn_edit.pack(pady=(0, 6))
            self.btn_delete.pack(pady=(0, 6))


    def _start_edit(self) -> None:
        if not self._selected_ws:
            messagebox.showinfo(APP_TITLE, "Выберите запись в списке.", parent=self)
            return
        self._set_edit_mode(True)
        self.ws_entries["ip_address"].focus_set()


    def _cancel_edit(self, silent: bool = False) -> None:
        self._set_edit_mode(False)
        if self._selected_ws:
            self._show_ws(self._selected_ws)


    def _save_edit(self) -> None:
        if not self._selected_ws:
            return
        ip = self.ws_fields["ip_address"].get().strip()
        full_name = self.ws_fields["full_name"].get().strip()
        room = self.ws_fields["room_number"].get().strip()
        ticket = self.ws_fields["ticket_id"].get().strip()
        note = self.ws_fields["note"].get().strip()
        if not ip or not full_name or not room:
            messagebox.showwarning(
                APP_TITLE, "Заполните IP-адрес, ФИО и аудиторию.", parent=self
            )
            return
        try:
            db.update_workstation(
                self._selected_ws["id"],
                ip,
                full_name,
                room,
                ticket,
                note,
                self.operator(),
            )
        except Exception as exc:
            messagebox.showerror(APP_TITLE, explain_db_error(exc), parent=self)
            return
        self._set_edit_mode(False)
        self.refresh_all()
        self._reselect_ws_by_ip(ip)


    def _delete_ws(self) -> None:
        # Защита на уровне функции (backend-ограничение на случай сбоев интерфейса)
        if self.current_user["role"] != "admin":
            messagebox.showerror(APP_TITLE, "У вас нет прав на удаление записей.", parent=self)
            return
            
        if self._editing:
            messagebox.showinfo(APP_TITLE, "Сначала сохраните или отмените правку.", parent=self)
            return
        if not self._selected_ws:
            messagebox.showinfo(APP_TITLE, "Выберите запись в списке.", parent=self)
            return
        row = self._selected_ws
        if not messagebox.askyesno(
            APP_TITLE,
            f"Удалить станцию {row['ip_address']} ({row['full_name']}, ауд. {row['room_number']})?\n"
            "Из актуального списка она исчезнет, событие сохранится в журнале.",
            parent=self,
        ):
            return
        try:
            db.archive_workstation(row["id"], self.operator())
        except Exception as exc:
            messagebox.showerror(APP_TITLE, explain_db_error(exc), parent=self)
            return
        self.refresh_all()


    def _restore_hist(self) -> None:
        if not self._selected_hist:
            messagebox.showinfo(APP_TITLE, "Выберите запись в журнале событий.", parent=self)
            return
        row = self._selected_hist
        if not messagebox.askyesno(
            APP_TITLE,
            f"Вернуть в актуальный список {row['ip_address']} ({row['full_name']})?",
            parent=self,
        ):
            return
        try:
            db.restore_from_history(row["history_id"], self.operator())
        except Exception as exc:
            messagebox.showerror(APP_TITLE, explain_db_error(exc), parent=self)
            return
        self.refresh_all()
        self.notebook.select(self.live_tab)
        self._reselect_ws_by_ip(row["ip_address"])


    def _reselect_ws_by_ip(self, ip: str) -> None:
        for iid, row in self._ws_by_iid.items():
            if row["ip_address"] == ip:
                self.ws_tree.selection_set(iid)
                self.ws_tree.see(iid)
                self._on_ws_select()
                break


    def _apply_role_restrictions(self) -> None:
        if self.current_user["role"] != "admin":
            self.btn_edit.pack_forget()
            self.btn_delete.pack_forget()
            self.btn_save.pack_forget()
            self.btn_cancel.pack_forget()
            self.btn_add_ws.pack_forget()
            self.btn_restore.pack_forget() 
            
            self.ws_tree.unbind("<Double-1>")

        if self.current_user["role"] == "viewer":
            self.notebook.hide(self.archive_tab)


    def _start_edit(self) -> None:
        if self.current_user["role"] != "admin":
            return
        if not self._selected_ws:
            messagebox.showinfo(APP_TITLE, "Выберите запись в списке.", parent=self)
            return
        self._set_edit_mode(True)
        self.ws_entries["ip_address"].focus_set()

    
    def add_workstation(self) -> None:
        dialog = WorkstationDialog(self, operator=self.operator())
        self.wait_window(dialog)
        if dialog.result:
            self.refresh_all()
            self._reselect_ws_by_ip(dialog.result)


    def save_txt(self) -> None:
        stamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        path = filedialog.asksaveasfilename(
            parent=self,
            title="Сохранить отчёт",
            defaultextension=".txt",
            filetypes=[("Текстовые файлы", "*.txt"), ("Все файлы", "*.*")],
            initialfile=f"workstations_report_{stamp}.txt",
        )
        if not path:
            return
        try:
            count = db.export_workstations_txt(path)
        except Exception as exc:
            messagebox.showerror(APP_TITLE, explain_db_error(exc), parent=self)
            return
        messagebox.showinfo(
            APP_TITLE,
            f"Сохранено записей: {count}\n{path}",
            parent=self,
        )


class WorkstationDialog(tk.Toplevel):
    def __init__(self, master: App, operator: str) -> None:
        super().__init__(master)
        self.operator = operator
        self.result: str | None = None
        self.title("Новая рабочая станция")
        
        # Задаем базовые размеры окна
        window_width = 380
        window_height = 240
        
        # Получаем разрешение экрана
        screen_width = self.winfo_screenwidth()
        screen_height = self.winfo_screenheight()
        
        # Вычисляем координаты X и Y для центра
        x = int((screen_width / 2) - (window_width / 2))
        y = int((screen_height / 2) - (window_height / 2))
        
        # Размещаем окно по центру
        self.geometry(f"{window_width}x{window_height}+{x}+{y}")
        self.resizable(False, False)
        self.transient(master)
        self.grab_set()

        self.ip = tk.StringVar()
        self.full_name = tk.StringVar()
        self.room = tk.StringVar()
        self.ticket = tk.StringVar()
        self.note = tk.StringVar()

        body = ttk.Frame(self, padding=12)
        body.pack(fill=tk.BOTH, expand=True)
        rows = [
            ("IP-адрес", self.ip),
            ("ФИО", self.full_name),
            ("Аудитория", self.room),
            ("Номер заявки", self.ticket),
            ("Примечание", self.note),
        ]
        for i, (caption, var) in enumerate(rows):
            ttk.Label(body, text=caption).grid(row=i, column=0, sticky=tk.W, pady=4, padx=(0, 8))
            ttk.Entry(body, textvariable=var, width=36).grid(row=i, column=1, pady=4)

        btns = ttk.Frame(body)
        btns.grid(row=len(rows), column=0, columnspan=2, pady=(10, 0), sticky=tk.E)
        ttk.Button(btns, text="Отмена", command=self.destroy).pack(side=tk.RIGHT, padx=4)
        ttk.Button(btns, text="Сохранить", command=self._save).pack(side=tk.RIGHT)

        self.bind("<Return>", lambda _e: self._save())
        self.bind("<Escape>", lambda _e: self.destroy())
        self.after(50, lambda: body.grid_slaves(row=0, column=1)[0].focus_set())

    def _save(self) -> None:
        ip = self.ip.get().strip()
        full_name = self.full_name.get().strip()
        room = self.room.get().strip()
        ticket = self.ticket.get().strip()
        note = self.note.get().strip()
        if not ip or not full_name or not room:
            messagebox.showwarning(
                APP_TITLE, "Заполните IP-адрес, ФИО и аудиторию.", parent=self
            )
            return
        try:
            db.add_workstation(ip, full_name, room, ticket, note, self.operator)
        except Exception as exc:
            messagebox.showerror(APP_TITLE, explain_db_error(exc), parent=self)
            return
        self.result = ip
        self.destroy()


class LoginDialog(tk.Toplevel):
    def __init__(self, master) -> None:
        super().__init__(master)
        self.title("Авторизация")
        
        # Задаем размеры окна
        window_width = 300
        window_height = 180
        
        # Получаем разрешение экрана
        screen_width = self.winfo_screenwidth()
        screen_height = self.winfo_screenheight()
        
        # Вычисляем координаты X и Y для центра
        x = int((screen_width / 2) - (window_width / 2))
        y = int((screen_height / 2) - (window_height / 2))
        
        # Применяем размеры и координаты (формат: "ширинаxвысота+x+y")
        self.geometry(f"{window_width+30}x{window_height+30}+{x}+{y}")
        self.resizable(False, False)
        self.grab_set()
        
        self.authenticated_user = None

        ttk.Label(self, text="Логин:").pack(pady=(15, 2))
        self.username_entry = ttk.Entry(self, width=25)
        self.username_entry.pack(pady=2)
        
        ttk.Label(self, text="Пароль:").pack(pady=(10, 2))
        self.password_entry = ttk.Entry(self, show="*", width=25)
        self.password_entry.pack(pady=2)

        ttk.Button(self, text="Войти", command=self.login).pack(pady=(15, 0))
        
        self.bind("<Return>", lambda _e: self.login())
        self.protocol("WM_DELETE_WINDOW", self.on_close)
        self.username_entry.focus_set()


    def login(self):
        username = self.username_entry.get().strip()
        password = self.password_entry.get().strip()
        
        if not username or not password:
            messagebox.showwarning("Ошибка", "Введите логин и пароль.", parent=self)
            return
            
        try:
            user_data = db.get_user_by_username(username)
        except Exception as e:
            messagebox.showerror("Ошибка БД", db.explain_db_error(e), parent=self)
            return
            
        if user_data:
            # Хэшируем введенный пароль и сверяем с базой
            hashed_input = hashlib.sha256(password.encode()).hexdigest()
            if hashed_input == user_data["password_hash"]:
                self.authenticated_user = user_data
                self.destroy()
                return
                
        messagebox.showerror("Ошибка", "Неверный логин или пароль.", parent=self)

    def on_close(self):
        self.destroy()

def main() -> None:
    try:
        load_env()
    except (FileNotFoundError, ValueError) as exc:
        root = tk.Tk()
        root.withdraw()
        messagebox.showerror(APP_TITLE, str(exc))
        root.destroy()
        sys.exit(1)

    root = tk.Tk()
    root.withdraw()
    
    login_window = LoginDialog(root)
    root.wait_window(login_window)
    
    if not login_window.authenticated_user:
        sys.exit(0)
        
    root.destroy()
    app = App(login_window.authenticated_user)
    app.mainloop()

if __name__ == "__main__":
    import ctypes
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except Exception:
        pass
    main()