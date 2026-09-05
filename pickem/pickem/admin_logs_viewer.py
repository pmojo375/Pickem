"""Hardened admin log viewer (fixes IndexError on unmatched rows when filtering)."""

import os
import tempfile
import shutil
import logging
from datetime import datetime

from django.contrib.admin.views.decorators import staff_member_required
from django.core.paginator import Paginator
from django.http import FileResponse
from django.shortcuts import redirect, render

from django_admin_logs_viewer.conf import app_settings
from django_admin_logs_viewer.defaults import DEFAULTS
from django_admin_logs_viewer.views.parser import _parse_logs
from django_admin_logs_viewer.views.utils import (
    _auto_drill_down,
    _build_breadcrumbs,
    _count_errors_in_dir,
    _is_inside_logs_dirs,
    _validate_settings,
)


def _safe_column_index(column_types, name: str):
    if not column_types:
        return None
    lowered = [str(t).lower() for t in column_types]
    try:
        return lowered.index(name.lower())
    except ValueError:
        return None


def _row_cell(row, index):
    if index is None or index >= len(row):
        return None
    return row[index]


@staff_member_required
def logs_view(request):
    errors = _validate_settings()
    if errors:
        for e in errors:
            logging.error(e)
        return render(
            request,
            "admin/errors.html",
            {
                "errors": errors,
                "breadcrumbs": [{"name": "Logs error", "url": ""}],
            },
        )

    log_dirs = app_settings.LOGS_DIRS
    current_path = request.GET.get("path", "")

    if current_path:
        current_path = os.path.abspath(current_path)
        if not _is_inside_logs_dirs(current_path):
            return render(
                request,
                "admin/errors.html",
                {
                    "errors": ["Path does not exist or is outside of LOGS_DIRS."],
                    "breadcrumbs": [{"name": "Logs error", "url": ""}],
                },
            )

    if request.GET.get("download"):
        if not current_path:
            tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".zip")
            with tempfile.TemporaryDirectory() as tmpdir:
                for i, log_dir in enumerate(log_dirs):
                    path = log_dir["path"]
                    if os.path.exists(path):
                        dst = os.path.join(tmpdir, f"{os.path.basename(path)}_{i}")
                        shutil.copytree(path, dst)
                shutil.make_archive(tmp.name, "zip", tmpdir)
            return FileResponse(
                open(tmp.name + ".zip", "rb"),
                as_attachment=True,
                filename="all_logs.zip",
            )
        if os.path.isdir(current_path):
            tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".zip")
            shutil.make_archive(tmp.name, "zip", current_path)
            return FileResponse(
                open(tmp.name + ".zip", "rb"),
                as_attachment=True,
                filename=os.path.basename(current_path) + ".zip",
            )
        if os.path.isfile(current_path):
            return FileResponse(
                open(current_path, "rb"),
                as_attachment=True,
                filename=os.path.basename(current_path),
            )

    if not current_path:
        items = []
        for log_dir in log_dirs:
            path = log_dir["path"]
            is_dir = os.path.isdir(path)
            errors_count = _count_errors_in_dir(path, request)
            items.append(
                {
                    "name": os.path.basename(path),
                    "path": path,
                    "is_dir": is_dir,
                    "errors_since_last_login": errors_count,
                }
            )

        if len(items) == 1:
            drilled = _auto_drill_down(items[0]["path"])
            return redirect(f"{request.path}?path={drilled}")

        return render(
            request,
            "admin/logs_dir.html",
            {
                "items": items,
                "current_path": current_path,
                "breadcrumbs": _build_breadcrumbs(current_path, log_dirs),
            },
        )

    current_path = os.path.abspath(current_path)
    current_path = _auto_drill_down(current_path)

    if os.path.isdir(current_path):
        items = []
        for name in sorted(os.listdir(current_path)):
            item_path = os.path.join(current_path, name)
            is_dir = os.path.isdir(item_path)
            errors_count = _count_errors_in_dir(item_path, request)
            items.append(
                {
                    "name": name,
                    "path": item_path,
                    "is_dir": is_dir,
                    "errors_since_last_login": errors_count,
                }
            )

        return render(
            request,
            "admin/logs_dir.html",
            {
                "items": items,
                "current_path": current_path,
                "breadcrumbs": _build_breadcrumbs(current_path, log_dirs),
            },
        )

    rows_per_page = app_settings.LOGS_ROWS_PER_PAGE
    page_number = int(request.GET.get("page", 1))
    search_query = request.GET.get("search_query", "").strip()
    level_filter = request.GET.get("level_filter", "").strip().lower()
    time_from = request.GET.get("time_from", "").strip()
    time_to = request.GET.get("time_to", "").strip()

    with open(current_path, "r", encoding="utf-8", errors="ignore") as f:
        content = f.read()

    parser_name = None
    for entry in log_dirs:
        if current_path.startswith(os.path.abspath(entry["path"])):
            parser_name = entry.get("parser")
            break

    mode, column_names, column_types, all_rows, datetime_format = _parse_logs(
        content, parser_name
    )

    if all_rows:
        # Normalize cells (optional regex groups become None) and skip junk short rows later.
        normalized = []
        for row in all_rows:
            normalized.append([("" if v is None else v) for v in row])
        all_rows = list(reversed(normalized))

    if search_query:
        all_rows = [
            row
            for row in all_rows
            if any(search_query.lower() in str(value).lower() for value in row)
        ]

    level_column_index = _safe_column_index(column_types, "level")
    if level_filter and level_column_index is not None:
        filtered_rows = []
        for row in all_rows:
            cell = _row_cell(row, level_column_index)
            if cell is None:
                continue
            if str(cell).lower() == level_filter:
                filtered_rows.append(row)
        all_rows = filtered_rows

    time_column_index = _safe_column_index(column_types, "time")
    if (time_from or time_to) and time_column_index is not None:
        filtered_rows = []
        for row in all_rows:
            row_time_str = _row_cell(row, time_column_index)
            if row_time_str is None:
                continue
            try:
                row_time = datetime.strptime(
                    str(row_time_str),
                    datetime_format or DEFAULTS["datetime_format"],
                )
            except ValueError:
                continue

            include = True
            if time_from:
                from_dt = datetime.fromisoformat(time_from)
                if row_time < from_dt:
                    include = False
            if time_to:
                to_dt = datetime.fromisoformat(time_to)
                if row_time > to_dt:
                    include = False
            if include:
                filtered_rows.append(row)
        all_rows = filtered_rows

    if all_rows:
        paginator = Paginator(all_rows, rows_per_page)
        page_obj = paginator.get_page(page_number)
        rows = page_obj.object_list
    else:
        page_obj = None
        rows = None

    return render(
        request,
        "admin/logs_file.html",
        {
            "mode": mode,
            "content": None if parser_name else content,
            "rows": rows,
            "column_names": column_names,
            "column_types": column_types,
            "current_path": current_path,
            "page_obj": page_obj,
            "search_query": search_query,
            "level_filter": level_filter,
            "breadcrumbs": _build_breadcrumbs(current_path, log_dirs),
        },
    )
