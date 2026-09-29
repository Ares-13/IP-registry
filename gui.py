"""IP-реестр — графическое приложение для учёта рабочих станций вуза."""

from __future__ import annotations

import sys
import tkinter as tk
from datetime import datetime
from tkinter import filedialog, messagebox, ttk

import psycopg2
import psycopg2.errors

import db
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
    def __init__(self) -> None:
        super().__init__()
        self.title(APP_TITLE)
        self.geometry("1280x720")
        self.minsize(960, 560)

        self.operator_var = tk.StringVar(value=get_default_operator())
        self.name_var = tk.StringVar()
        self.room_var = tk.StringVar()

        self._ws_by_iid: dict[str, dict] = {}
        self._hist_by_iid: dict[str, dict] = {}
        self._selected_ws: dict | None = None
        self._selected_hist: dict | None = None
        self._editing = False

        self._build()
        self.refresh_all()

    def _build(self) -> None:
        outer = ttk.Frame(self, padding=10)
        outer.pack(fill=tk.BOTH, expand=True)

        title = ttk.Label(outer, text=APP_TITLE, font=("Segoe UI", 16, "bold"))
        title.pack(anchor=tk.W)

        hint = ttk.Label(
            outer,
            text="Актуальные адреса рабочих станций и архив изменений. "
            "Поиск — по фамилии и аудитории.",
        )
        hint.pack(anchor=tk.W, pady=(0, 8))

        toolbar = ttk.Frame(outer)
        toolbar.pack(fill=tk.X, pady=(0, 8))

        ttk.Label(toolbar, text="Фамилия / ФИО:").pack(side=tk.LEFT)
        name_entry = ttk.Entry(toolbar, textvariable=self.name_var, width=28)
        name_entry.pack(side=tk.LEFT, padx=(4, 12))
        name_entry.bind("<Return>", lambda _e: self.refresh_all())

        ttk.Label(toolbar, text="Аудитория:").pack(side=tk.LEFT)
        room_entry = ttk.Entry(toolbar, textvariable=self.room_var, width=14)
        room_entry.pack(side=tk.LEFT, padx=(4, 12))
        room_entry.bind("<Return>", lambda _e: self.refresh_all())

        ttk.Button(toolbar, text="Найти", command=self.refresh_all).pack(side=tk.LEFT, padx=2)
        ttk.Button(toolbar, text="Сбросить", command=self._reset_search).pack(side=tk.LEFT, padx=2)
        ttk.Button(toolbar, text="Сохранить .txt…", command=self.save_txt).pack(
            side=tk.LEFT, padx=(16, 2)
        )
        ttk.Button(toolbar, text="Добавить станцию", command=self.add_workstation).pack(
            side=tk.LEFT, padx=2
        )
        ttk.Button(toolbar, text="Обновить", command=self.refresh_all).pack(side=tk.LEFT, padx=2)

        op_box = ttk.Frame(toolbar)
        op_box.pack(side=tk.RIGHT)
        ttk.Label(op_box, text="Оператор:").pack(side=tk.LEFT)
        ttk.Entry(op_box, textvariable=self.operator_var, width=18).pack(side=tk.LEFT, padx=4)

        self.notebook = ttk.Notebook(outer)
        self.notebook.pack(fill=tk.BOTH, expand=True)
        self.notebook.bind("<<NotebookTabChanged>>", lambda _e: self._sync_status())

        self.live_tab = ttk.Frame(self.notebook, padding=6)
        self.archive_tab = ttk.Frame(self.notebook, padding=6)
        self.notebook.add(self.live_tab, text="Актуальные")
        self.notebook.add(self.archive_tab, text="Архив")

        self._build_live_tab()
        self._build_archive_tab()

        self.status = tk.StringVar(value="")
        ttk.Label(outer, textvariable=self.status).pack(anchor=tk.W, pady=(6, 0))

    def _build_live_tab(self) -> None:
        pane = ttk.Panedwindow(self.live_tab, orient=tk.HORIZONTAL)
        pane.pack(fill=tk.BOTH, expand=True)

        left = ttk.Frame(pane)
        right = ttk.LabelFrame(pane, text="Карточка станции", padding=8)
        pane.add(left, weight=4)
        pane.add(right, weight=2)

        columns = ("ip", "full_name", "room", "ticket", "updated_at", "updated_by")
        self.ws_tree = ttk.Treeview(left, columns=columns, show="headings", selectmode="browse")
        headings = {
            "ip": "IP-адрес",
            "full_name": "ФИО",
            "room": "Аудитория",
            "ticket": "Заявка",
            "updated_at": "Обновлено",
            "updated_by": "Кем",
        }
        widths = {"ip": 130, "full_name": 240, "room": 90, "ticket": 110, "updated_at": 130, "updated_by": 90}
        for col in columns:
            self.ws_tree.heading(col, text=headings[col])
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
            "created_at": tk.StringVar(),
            "updated_at": tk.StringVar(),
            "updated_by": tk.StringVar(),
        }
        labels = [
            ("ip_address", "IP-адрес"),
            ("full_name", "ФИО"),
            ("room_number", "Аудитория"),
            ("ticket_id", "Номер заявки"),
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
        self.btn_delete = ttk.Button(buttons, text="Удалить в архив", command=self._delete_ws, width=16)
        self.btn_edit.pack(pady=(0, 6))
        self.btn_delete.pack(pady=(0, 6))
        self._set_edit_mode(False)

    def _build_archive_tab(self) -> None:
        pane = ttk.Panedwindow(self.archive_tab, orient=tk.HORIZONTAL)
        pane.pack(fill=tk.BOTH, expand=True)

        left = ttk.Frame(pane)
        right = ttk.LabelFrame(pane, text="Запись архива", padding=8)
        pane.add(left, weight=4)
        pane.add(right, weight=2)

        columns = ("operation", "ip", "full_name", "room", "ticket", "valid_to", "changed_by")
        self.hist_tree = ttk.Treeview(left, columns=columns, show="headings", selectmode="browse")
        headings = {
            "operation": "Операция",
            "ip": "IP-адрес",
            "full_name": "ФИО",
            "room": "Аудитория",
            "ticket": "Заявка",
            "valid_to": "Когда",
            "changed_by": "Кем",
        }
        widths = {
            "operation": 110,
            "ip": 130,
            "full_name": 220,
            "room": 90,
            "ticket": 110,
            "valid_to": 130,
            "changed_by": 90,
        }
        for col in columns:
            self.hist_tree.heading(col, text=headings[col])
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
            "ip_address": tk.StringVar(),
            "full_name": tk.StringVar(),
            "room_number": tk.StringVar(),
            "ticket_id": tk.StringVar(),
            "valid_from": tk.StringVar(),
            "valid_to": tk.StringVar(),
            "changed_by": tk.StringVar(),
            "id_original": tk.StringVar(),
        }
        labels = [
            ("operation", "Операция"),
            ("ip_address", "IP-адрес"),
            ("full_name", "ФИО"),
            ("room_number", "Аудитория"),
            ("ticket_id", "Номер заявки"),
            ("valid_from", "Действовало с"),
            ("valid_to", "Действовало до"),
            ("changed_by", "Кто изменил"),
            ("id_original", "Исходный id"),
        ]
        for row, (key, caption) in enumerate(labels):
            ttk.Label(fields, text=caption).grid(row=row, column=0, sticky=tk.W, pady=3, padx=(0, 8))
            ttk.Entry(fields, textvariable=self.hist_fields[key], width=36, state="readonly").grid(
                row=row, column=1, sticky=tk.EW, pady=3
            )
        fields.columnconfigure(1, weight=1)

        buttons = ttk.Frame(right)
        buttons.pack(side=tk.RIGHT, fill=tk.Y, padx=(12, 0))
        ttk.Button(
            buttons, text="Вернуть в актуальные", command=self._restore_hist, width=22
        ).pack(pady=(0, 6))

    def operator(self) -> str:
        return self.operator_var.get().strip() or "admin"

    def _reset_search(self) -> None:
        self.name_var.set("")
        self.room_var.set("")
        self.refresh_all()

    def refresh_all(self) -> None:
        name = self.name_var.get()
        room = self.room_var.get()
        try:
            workstations = db.fetch_workstations(full_name=name, room_number=room)
            history = db.fetch_history(full_name=name, room_number=room)
        except Exception as exc:
            messagebox.showerror(APP_TITLE, explain_db_error(exc), parent=self)
            return
        self._fill_ws(workstations)
        self._fill_hist(history)
        self._sync_status()

    def _fill_ws(self, rows: list[dict]) -> None:
        self._cancel_edit(silent=True)
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
                    fmt_dt(row["updated_at"]),
                    row["updated_by"],
                ),
            )

    def _fill_hist(self, rows: list[dict]) -> None:
        self.hist_tree.delete(*self.hist_tree.get_children())
        self._hist_by_iid.clear()
        self._selected_hist = None
        self._clear_hist_fields()
        for row in rows:
            iid = str(row["history_id"])
            self._hist_by_iid[iid] = row
            self.hist_tree.insert(
                "",
                tk.END,
                iid=iid,
                values=(
                    OPERATION_LABELS.get(row["operation"], row["operation"]),
                    row["ip_address"],
                    row["full_name"],
                    row["room_number"],
                    row["ticket_id"],
                    fmt_dt(row["valid_to"]),
                    row["changed_by"],
                ),
            )

    def _sync_status(self) -> None:
        live = len(self._ws_by_iid)
        hist = len(self._hist_by_iid)
        tab = self.notebook.index(self.notebook.select()) if str(self.notebook.select()) else 0
        if tab == 0:
            self.status.set(f"Актуальных записей: {live}. Архив: {hist}.")
        else:
            self.status.set(f"Записей в архиве: {hist}. Актуальных: {live}.")

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
        self.hist_fields["operation"].set(OPERATION_LABELS.get(row["operation"], row["operation"]))
        self.hist_fields["ip_address"].set(row["ip_address"])
        self.hist_fields["full_name"].set(row["full_name"])
        self.hist_fields["room_number"].set(row["room_number"])
        self.hist_fields["ticket_id"].set(row["ticket_id"])
        self.hist_fields["valid_from"].set(fmt_dt(row["valid_from"]))
        self.hist_fields["valid_to"].set(fmt_dt(row["valid_to"]))
        self.hist_fields["changed_by"].set(row["changed_by"])
        self.hist_fields["id_original"].set(str(row["id_original"]))

    def _show_ws(self, row: dict) -> None:
        self.ws_fields["ip_address"].set(row["ip_address"])
        self.ws_fields["full_name"].set(row["full_name"])
        self.ws_fields["room_number"].set(row["room_number"])
        self.ws_fields["ticket_id"].set(row["ticket_id"])
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
        for key in ("ip_address", "full_name", "room_number", "ticket_id"):
            self.ws_entries[key].configure(state="normal" if enabled else "readonly")
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
                self.operator(),
            )
        except Exception as exc:
            messagebox.showerror(APP_TITLE, explain_db_error(exc), parent=self)
            return
        self._set_edit_mode(False)
        self.refresh_all()
        self._reselect_ws_by_ip(ip)

    def _delete_ws(self) -> None:
        if self._editing:
            messagebox.showinfo(APP_TITLE, "Сначала сохраните или отмените правку.", parent=self)
            return
        if not self._selected_ws:
            messagebox.showinfo(APP_TITLE, "Выберите запись в списке.", parent=self)
            return
        row = self._selected_ws
        if not messagebox.askyesno(
            APP_TITLE,
            f"Перенести в архив станцию {row['ip_address']} ({row['full_name']}, ауд. {row['room_number']})?\n"
            "Из актуального списка она исчезнет, запись останется в архиве.",
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
            messagebox.showinfo(APP_TITLE, "Выберите запись архива.", parent=self)
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
        self.resizable(False, False)
        self.transient(master)
        self.grab_set()

        self.ip = tk.StringVar()
        self.full_name = tk.StringVar()
        self.room = tk.StringVar()
        self.ticket = tk.StringVar()

        body = ttk.Frame(self, padding=12)
        body.pack(fill=tk.BOTH, expand=True)
        rows = [
            ("IP-адрес", self.ip),
            ("ФИО", self.full_name),
            ("Аудитория", self.room),
            ("Номер заявки", self.ticket),
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
        if not ip or not full_name or not room:
            messagebox.showwarning(
                APP_TITLE, "Заполните IP-адрес, ФИО и аудиторию.", parent=self
            )
            return
        try:
            db.add_workstation(ip, full_name, room, ticket, self.operator)
        except Exception as exc:
            messagebox.showerror(APP_TITLE, explain_db_error(exc), parent=self)
            return
        self.result = ip
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

    app = App()
    app.mainloop()


if __name__ == "__main__":
    main()
